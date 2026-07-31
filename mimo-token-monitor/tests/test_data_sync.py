import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from data_sync import (
    DataSyncService,
    GitCommandError,
    SyncConfig,
    SyncStatus,
    _read_auth_fields,
    _sanitize_detail,
)
from process_utils import hidden_subprocess_kwargs


class TestSyncConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mimo_sync_")
        self.repo = Path(self.tmp.name) / "data"
        self.data_dir = self.repo / "mimo-token-monitor"
        self.data_dir.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_environment_defaults(self):
        with patch.dict(os.environ, {"MIMO_TOKEN_MONITOR_DATA_DIR": str(self.data_dir)}, clear=True):
            config = SyncConfig.from_environment()
        self.assertEqual(config.repo_root, self.repo.resolve())
        self.assertEqual(config.db_path, (self.data_dir / "settings.db").resolve())
        self.assertEqual(config.git_path, "mimo-token-monitor/settings.db")
        self.assertEqual(config.remote, "origin")
        self.assertEqual(config.branch, "main")
        self.assertEqual(config.timeout_seconds, 30)
        self.assertEqual(config.push_retries, 3)

    def test_invalid_numeric_environment_is_rejected(self):
        with patch.dict(os.environ, {
            "MIMO_TOKEN_MONITOR_DATA_DIR": str(self.data_dir),
            "MIMO_TOKEN_MONITOR_GIT_TIMEOUT_SECONDS": "zero",
        }, clear=True):
            with self.assertRaisesRegex(ValueError, "MIMO_TOKEN_MONITOR_GIT_TIMEOUT_SECONDS"):
                SyncConfig.from_environment()

    def test_target_outside_data_directory_is_rejected(self):
        config = SyncConfig(
            repo_root=self.repo.resolve(),
            data_dir=self.data_dir.resolve(),
            db_path=(self.repo / "other" / "settings.db").resolve(),
            git_path="mimo-token-monitor/settings.db",
        )
        result = DataSyncService(config).validate_paths()
        self.assertEqual(result.status, SyncStatus.SKIPPED)
        self.assertEqual(result.stage, "validate")

    def test_symlink_database_target_outside_data_directory_is_rejected(self):
        outside = Path(self.tmp.name) / "financial-data-backup"
        outside.mkdir()
        target = outside / "settings.db"
        target.touch()
        link = self.data_dir / "settings.db"
        try:
            link.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"当前平台无法创建 symlink: {exc}")

        config = SyncConfig(
            repo_root=self.repo,
            data_dir=self.data_dir,
            db_path=link,
            git_path="mimo-token-monitor/settings.db",
        )
        result = DataSyncService(config).validate_paths()
        self.assertIsNotNone(result)
        self.assertEqual(result.status, SyncStatus.SKIPPED)
        self.assertEqual(result.stage, "validate")

    def test_wrong_git_path_is_rejected(self):
        config = SyncConfig(
            repo_root=self.repo.resolve(),
            data_dir=self.data_dir.resolve(),
            db_path=(self.data_dir / "settings.db").resolve(),
            git_path="financial-data-backup/settings.db",
        )
        result = DataSyncService(config).validate_paths()
        self.assertEqual(result.status, SyncStatus.SKIPPED)

    def test_other_database_file_in_data_directory_is_rejected(self):
        config = SyncConfig(
            repo_root=self.repo.resolve(),
            data_dir=self.data_dir.resolve(),
            db_path=(self.data_dir / "other.db").resolve(),
        )
        result = DataSyncService(config).validate_paths()
        self.assertEqual(result.status, SyncStatus.SKIPPED)
        self.assertEqual(result.stage, "validate")


class TestGitBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mimo_git_")
        self.repo = Path(self.tmp.name) / "data"
        self.data_dir = self.repo / "mimo-token-monitor"
        self.data_dir.mkdir(parents=True)
        self.config = SyncConfig(
            repo_root=self.repo.resolve(),
            data_dir=self.data_dir.resolve(),
            db_path=(self.data_dir / "settings.db").resolve(),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_repository_root_mismatch_is_skipped(self):
        runner = Mock(return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"C:/wrong/repo\n", stderr=b""
        ))
        result = DataSyncService(self.config, runner=runner).validate_repository()
        self.assertEqual(result.status, SyncStatus.SKIPPED)
        self.assertEqual(result.stage, "validate")

    def test_git_timeout_becomes_command_error(self):
        runner = Mock(side_effect=subprocess.TimeoutExpired(["git"], 30))
        service = DataSyncService(self.config, runner=runner)
        with self.assertRaisesRegex(GitCommandError, "超时"):
            service._git("status")

    def test_sensitive_url_is_redacted_and_truncated(self):
        detail = "x" * 2100 + " https://alice:secret@example.test/path?token=abc"
        clean = _sanitize_detail(detail)
        self.assertLessEqual(len(clean), 2000)
        self.assertNotIn("alice", clean)
        self.assertNotIn("secret", clean)
        self.assertNotIn("abc", clean)

    def test_sensitive_diagnostic_forms_are_redacted(self):
        detail = (
            "https://urluser@example.test/a "
            "https://urlname:urlpass@example.test/b "
            "?token=tokenvalue&access_token=accessvalue#password=passwordvalue "
            "?passwd=passwdvalue&api_key=keyvalue&apikey=apikeyvalue "
            "#secret=secretvalue&credential=credentialvalue&auth=authvalue "
            "Authorization: Bearer headerbearer "
            "Bearer standalonebearer ordinary-diagnostic"
        )
        clean = _sanitize_detail(detail)
        for sensitive in (
            "urluser", "urlname", "urlpass", "tokenvalue", "accessvalue",
            "passwordvalue", "passwdvalue", "keyvalue", "apikeyvalue",
            "secretvalue", "credentialvalue", "authvalue", "headerbearer",
            "standalonebearer",
        ):
            self.assertNotIn(sensitive, clean)
        self.assertIn("ordinary-diagnostic", clean)
        self.assertIn("https://***:***@example.test/b", clean)

    def test_sensitive_naked_credentials_and_any_scheme_urls_are_redacted(self):
        detail = (
            "token=abc access_token: def remote token ghi "
            "password=jkl passwd=mno api_key: pqr apikey=stu "
            "secret=vwx credential: yz auth=tokenvalue "
            "ssh://user:password@host/path "
            "ftp://ftpuser:ftppass@example.test/file "
            "http://httpuser:httppass@example.test/path ordinary-diagnostic"
        )
        clean = _sanitize_detail(detail)
        for sensitive in (
            "abc", "def", "ghi", "jkl", "mno", "pqr", "stu", "vwx", "yz",
            "tokenvalue", "user", "ftpuser", "ftppass",
            "httpuser", "httppass",
        ):
            self.assertNotIn(sensitive, clean)
        self.assertIn("remote token ***", clean)
        self.assertIn("ssh://***:***@host/path", clean)
        self.assertIn("ftp://***:***@example.test/file", clean)
        self.assertIn("ordinary-diagnostic", clean)

    def test_cookie_diagnostics_are_redacted_without_overmatching(self):
        clean = _sanitize_detail("Cookie: session=abc; token=def\nSet-Cookie: sid=secret\nhttps://x.test/?cookie=abc&sessionid=def")
        for value in ("abc", "def", "secret"):
            self.assertNotIn(value, clean)
        self.assertIn("cookie policy", _sanitize_detail("cookie policy"))

    def test_common_diagnostic_phrases_are_preserved(self):
        detail = "token usage; password expired; auth failed"
        self.assertEqual(_sanitize_detail(detail), detail)

    def test_non_fast_forward_detection_requires_competition_signal(self):
        service = DataSyncService(self.config)
        self.assertFalse(service._is_non_fast_forward(GitCommandError("failed", "[rejected] main -> main (protected branch hook declined)")))
        self.assertFalse(service._is_non_fast_forward(GitCommandError("failed", "remote rejected: permission denied")))
        self.assertTrue(service._is_non_fast_forward(GitCommandError("failed", "[rejected] main -> main (non-fast-forward)")))
        self.assertTrue(service._is_non_fast_forward(GitCommandError("failed", "hint: fetch first")))


class TestRelativeRepositoryRoot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mimo_relative_git_", dir=Path.cwd())
        self.repo = Path(self.tmp.name) / "data"
        self.data_dir = self.repo / "mimo-token-monitor"
        self.data_dir.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _config_with_relative_root(self):
        relative_root = Path(os.path.relpath(self.repo, Path.cwd()))
        return SyncConfig(
            repo_root=relative_root,
            data_dir=self.data_dir,
            db_path=self.data_dir / "settings.db",
        )

    def test_relative_repo_root_matches_resolved_git_root(self):
        config = self._config_with_relative_root()
        runner = Mock(return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout=f"{self.repo.resolve()}\n".encode(), stderr=b""
        ))
        self.assertIsNone(DataSyncService(config, runner=runner).validate_repository())

    def test_git_uses_resolved_repository_root(self):
        runner = Mock(return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"ok", stderr=b""
        ))
        service = DataSyncService(self._config_with_relative_root(), runner=runner)
        service._git("status")
        self.assertEqual(runner.call_args.args[0][2], str(service.config.repo_root.resolve()))


def run_git(cwd: Path, *args: str, input_bytes: bytes | None = None):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], input=input_bytes,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )


def write_db(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)")
        conn.execute("INSERT INTO settings VALUES ('cookie', ?)", (f'"{value}"',))
        conn.commit()
    finally:
        conn.close()



def write_malformed_auth_db(path: Path) -> None:
    """Write a settings DB where cookie value is not valid JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)")
        conn.execute("INSERT INTO settings VALUES ('cookie', 'not-valid-json{{')")
        conn.execute("INSERT INTO settings VALUES ('third_party_api_key', '""')")
        conn.execute("INSERT INTO settings VALUES ('refresh_interval', '300')")
        conn.commit()
    finally:
        conn.close()



def write_null_auth_db(path: Path) -> None:
    """Write a settings DB where cookie is JSON null."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)")
        conn.execute("INSERT INTO settings VALUES ('cookie', 'null')")
        conn.execute("INSERT INTO settings VALUES ('third_party_api_key', '""')")
        conn.execute("INSERT INTO settings VALUES ('refresh_interval', '300')")
        conn.commit()
    finally:
        conn.close()


def write_int_auth_db(path: Path) -> None:
    """Write a settings DB where cookie is a JSON integer (not a string)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)")
        conn.execute("INSERT INTO settings VALUES ('cookie', '42')")
        conn.execute("INSERT INTO settings VALUES ('third_party_api_key', '""')")
        conn.execute("INSERT INTO settings VALUES ('refresh_interval', '300')")
        conn.commit()
    finally:
        conn.close()


def read_cookie(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT value_json FROM settings WHERE key='cookie'").fetchone()[0]
    finally:
        conn.close()


def read_cookie_from_git(repo: Path, path: str) -> str:
    blob = run_git(repo, "show", f"refs/heads/main:{path}").stdout
    temp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    try:
        temp.write(blob)
        temp.close()
        return read_cookie(Path(temp.name))
    finally:
        Path(temp.name).unlink(missing_ok=True)


def write_auth_db(path: Path, cookie: str = "", **extra) -> None:
    """Write a settings DB with specific auth fields."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)")
        conn.execute("INSERT INTO settings VALUES ('cookie', ?)", (json.dumps(cookie),))
        for key, value in extra.items():
            conn.execute("INSERT INTO settings VALUES (?, ?)", (key, json.dumps(value)))
        conn.commit()
    finally:
        conn.close()


class GitRepoFixture:
    def __init__(self, root: Path):
        self.remote = root / "remote.git"
        self.repo = root / "data"
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["git", "init", "-b", "main", str(self.repo)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        run_git(self.repo, "config", "user.name", "MiMo Test")
        run_git(self.repo, "config", "user.email", "mimo@example.test")
        run_git(self.repo, "remote", "add", "origin", str(self.remote))
        write_db(self.repo / "mimo-token-monitor" / "settings.db", "remote-v1")
        other = self.repo / "financial-data-backup" / "keep.txt"
        other.parent.mkdir(parents=True)
        other.write_text("keep-v1", encoding="utf-8")
        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-m", "seed")
        run_git(self.repo, "push", "-u", "origin", "main")

    def config(self) -> SyncConfig:
        data_dir = self.repo / "mimo-token-monitor"
        return SyncConfig(
            repo_root=self.repo.resolve(), data_dir=data_dir.resolve(),
            db_path=(data_dir / "settings.db").resolve(), timeout_seconds=10,
        )




class RacingSyncService(DataSyncService):
    def __init__(self, config: SyncConfig, competing_clone: Path):
        super().__init__(config)
        self.competing_clone = competing_clone
        self.injected = False

    def _before_push(self, attempt: int) -> None:
        if self.injected:
            return
        self.injected = True
        other = self.competing_clone / "financial-data-backup" / "remote.txt"
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_text("remote-latest", encoding="utf-8")
        run_git(self.competing_clone, "add", ".")
        run_git(self.competing_clone, "commit", "-m", "concurrent other data")
        run_git(self.competing_clone, "push", "origin", "main")


class NonCompetitiveFailureService(DataSyncService):
    def __init__(self, config: SyncConfig, detail: str = "remote: authentication required"):
        super().__init__(config)
        self.detail = detail
        self.push_attempts = 0

    def _git(self, *args: str, **kwargs):
        if args and args[0] == "push":
            self.push_attempts += 1
            raise GitCommandError("Git 命令执行失败", self.detail)
        return super()._git(*args, **kwargs)


class TestPushLocalDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mimo_push_")
        self.fixture = GitRepoFixture(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_push_rebuilds_on_remote_race_and_preserves_remote_other_path(self):
        clone = Path(self.tmp.name) / "competing"
        subprocess.run(
            ["git", "clone", "-b", "main", str(self.fixture.remote), str(clone)],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        run_git(clone, "config", "user.name", "Competitor")
        run_git(clone, "config", "user.email", "competitor@example.test")
        db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        db.unlink()
        write_db(db, "local-exit")

        result = RacingSyncService(self.fixture.config(), clone).push_local_database()

        self.assertEqual(result.status, SyncStatus.SUCCESS)
        verify = Path(self.tmp.name) / "verify"
        subprocess.run(
            ["git", "clone", "-b", "main", str(self.fixture.remote), str(verify)],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(read_cookie(verify / "mimo-token-monitor" / "settings.db"), '"local-exit"')
        self.assertEqual(
            (verify / "financial-data-backup" / "remote.txt").read_text(encoding="utf-8"),
            "remote-latest",
        )

    def test_push_stops_at_retry_limit(self):
        clone = Path(self.tmp.name) / "competing"
        subprocess.run(
            ["git", "clone", "-b", "main", str(self.fixture.remote), str(clone)],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        run_git(clone, "config", "user.name", "Competitor")
        run_git(clone, "config", "user.email", "competitor@example.test")
        db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        db.unlink()
        write_db(db, "local-exit")
        base = self.fixture.config()
        config = SyncConfig(
            repo_root=base.repo_root, data_dir=base.data_dir, db_path=base.db_path,
            timeout_seconds=base.timeout_seconds, push_retries=1,
        )

        result = RacingSyncService(config, clone).push_local_database()

        self.assertEqual(result.status, SyncStatus.FAILED)
        self.assertIn("重试上限", result.message)
        self.assertEqual(
            read_cookie_from_git(self.fixture.remote, "mimo-token-monitor/settings.db"),
            '"remote-v1"',
        )

    def test_non_competitive_push_failure_is_not_retried(self):
        db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        db.unlink()
        write_db(db, "local-exit")
        for detail in ("remote: authentication required", "protected branch hook declined"):
            service = NonCompetitiveFailureService(self.fixture.config(), detail)

            result = service.push_local_database()

            self.assertEqual(result.status, SyncStatus.FAILED)
            self.assertEqual(service.push_attempts, 1)

        repo = self.fixture.repo
        db = repo / "mimo-token-monitor" / "settings.db"
        db.unlink()
        write_db(db, "local-exit")
        other = repo / "financial-data-backup" / "keep.txt"
        other.write_text("local-uncommitted", encoding="utf-8")
        untracked = repo / "financial-data-backup" / "running.tmp"
        untracked.write_text("busy", encoding="utf-8")
        status_before = run_git(repo, "status", "--porcelain=v1").stdout
        index_before = run_git(repo, "write-tree").stdout

        result = DataSyncService(self.fixture.config()).push_local_database()

        self.assertEqual(result.status, SyncStatus.SUCCESS)
        self.assertEqual(run_git(repo, "status", "--porcelain=v1").stdout, status_before)
        self.assertEqual(run_git(repo, "write-tree").stdout, index_before)
        remote_commit = run_git(
            self.fixture.remote, "rev-parse", "refs/heads/main"
        ).stdout.decode().strip()
        changed = run_git(
            self.fixture.remote, "diff-tree", "--no-commit-id", "--name-only", "-r",
            remote_commit,
        ).stdout.decode().splitlines()
        self.assertEqual(changed, ["mimo-token-monitor/settings.db"])
        remote_db = run_git(
            self.fixture.remote, "show", "refs/heads/main:mimo-token-monitor/settings.db"
        ).stdout
        self.assertEqual(remote_db, db.read_bytes())
        self.assertEqual(other.read_text(encoding="utf-8"), "local-uncommitted")
        self.assertEqual(untracked.read_text(encoding="utf-8"), "busy")

    def test_push_failure_after_rebuild_preserves_local_database(self):
        db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        db.unlink()
        write_db(db, "local-safe")
        before = db.read_bytes()

        result = NonCompetitiveFailureService(
            self.fixture.config(), "remote: authentication required"
        ).push_local_database()

        self.assertEqual(result.status, SyncStatus.FAILED)
        self.assertEqual(result.stage, "push")
        self.assertEqual(db.read_bytes(), before)

    def test_unchanged_database_creates_no_commit(self):
        service = DataSyncService(self.fixture.config())
        before = run_git(self.fixture.repo, "rev-parse", "refs/remotes/origin/main").stdout
        result = service.push_local_database()
        after = run_git(self.fixture.repo, "rev-parse", "refs/remotes/origin/main").stdout
        self.assertEqual(result.status, SyncStatus.NO_CHANGE)
        self.assertEqual(after, before)


class TestOperationDeadline(unittest.TestCase):
    def test_pull_shares_budget_and_stops_before_next_command(self):
        config = SyncConfig(Path("/repo"), Path("/repo/mimo-token-monitor"), Path("/repo/mimo-token-monitor/settings.db"), timeout_seconds=30)
        clock = iter([100.0, 100.0, 105.0, 131.0])
        calls = []
        def monotonic(): return next(clock)
        def runner(command, **kwargs):
            calls.append(kwargs["timeout"])
            return subprocess.CompletedProcess(command, 0, b"/repo\n", b"")
        with patch("data_sync.time.monotonic", side_effect=monotonic):
            result = DataSyncService(config, runner=runner).pull_remote_database()
        self.assertEqual(result.status, SyncStatus.FAILED)
        self.assertEqual(len(calls), 2)
        self.assertAlmostEqual(calls[1], 25.0)

    def test_pull_quick_check_budget_expiry_preserves_local_database(self):
        tmp = tempfile.TemporaryDirectory(prefix="mimo_deadline_pull_")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        data_dir = root / "mimo-token-monitor"
        data_dir.mkdir()
        db = data_dir / "settings.db"
        db.write_bytes(b"local")
        config = SyncConfig(root, data_dir, db, timeout_seconds=30)
        now = [0.0]
        def clock(): return now[0]
        def validate(_path):
            now[0] = 31.0
            return True
        service = DataSyncService(config)
        with patch("data_sync.time.monotonic", side_effect=clock),              patch.object(service, "validate_repository", return_value=None),              patch.object(service, "_git", return_value=b"remote"),              patch.object(service, "_validate_sqlite", side_effect=validate),              patch("data_sync.os.replace") as replace:
            result = service.pull_remote_database()
        self.assertEqual(result.status, SyncStatus.FAILED)
        self.assertEqual(db.read_bytes(), b"local")
        replace.assert_not_called()

    def test_push_quick_check_budget_expiry_skips_git(self):
        tmp = tempfile.TemporaryDirectory(prefix="mimo_deadline_push_")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        data_dir = root / "mimo-token-monitor"
        data_dir.mkdir()
        db = data_dir / "settings.db"
        db.write_bytes(b"local")
        config = SyncConfig(root, data_dir, db, timeout_seconds=30)
        now = [0.0]
        def clock(): return now[0]
        def validate(_path):
            now[0] = 31.0
            return True
        runner = Mock()
        service = DataSyncService(config, runner=runner)
        with patch("data_sync.time.monotonic", side_effect=clock),              patch.object(service, "validate_repository", return_value=None),              patch.object(service, "_validate_sqlite", side_effect=validate):
            result = service.push_local_database()
        self.assertEqual(result.status, SyncStatus.FAILED)
        runner.assert_not_called()

    def test_push_retry_does_not_refresh_budget(self):
        config = SyncConfig(Path("/repo"), Path("/repo/mimo-token-monitor"), Path("/repo/mimo-token-monitor/settings.db"), timeout_seconds=30, push_retries=3)
        service = DataSyncService(config)
        with patch.object(service, "validate_repository", return_value=None), patch.object(service, "_validate_sqlite", return_value=True), patch.object(service, "_git", side_effect=GitCommandError("x", "non-fast-forward")) as git:
            service.config.db_path.parent.mkdir(parents=True, exist_ok=True)
            service.config.db_path.write_bytes(b"db")
            with patch("data_sync.time.monotonic", side_effect=[0.0, 0.0, 31.0]):
                result = service.push_local_database()
        self.assertEqual(result.status, SyncStatus.FAILED)
        self.assertEqual(git.call_count, 0)


class TestPullRemoteDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mimo_pull_")
        self.fixture = GitRepoFixture(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_remote_valid_database_overwrites_local_atomically(self):
        db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        db.unlink()
        write_db(db, "local")
        result = DataSyncService(self.fixture.config()).pull_remote_database()
        self.assertEqual(result.status, SyncStatus.SUCCESS)
        self.assertEqual(read_cookie(db), '"remote-v1"')
        self.assertEqual(list(db.parent.glob(".mimo-settings-*.tmp")), [])

    def test_invalid_remote_database_preserves_local(self):
        db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        db.unlink()
        write_db(db, "local")
        invalid = self.fixture.repo / "invalid.db"
        invalid.write_bytes(b"not sqlite")
        blob = run_git(self.fixture.repo, "hash-object", "-w", str(invalid)).stdout.strip().decode()
        run_git(self.fixture.repo, "update-index", "--cacheinfo", "100644", blob,
                "mimo-token-monitor/settings.db")
        run_git(self.fixture.repo, "commit", "-m", "invalid remote")
        run_git(self.fixture.repo, "push", "origin", "main")
        result = DataSyncService(self.fixture.config()).pull_remote_database()
        self.assertEqual(result.status, SyncStatus.FAILED)
        self.assertEqual(read_cookie(db), '"local"')
        self.assertEqual(list(db.parent.glob(".mimo-settings-*.tmp")), [])

    def test_fdopen_failure_closes_untransferred_descriptor_and_preserves_local(self):
        db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        db.unlink()
        write_db(db, "local")
        real_close = os.close
        with patch("data_sync.os.fdopen", side_effect=OSError("fdopen failed")) as fdopen:
            with patch("data_sync.os.close", side_effect=real_close) as close:
                result = DataSyncService(self.fixture.config()).pull_remote_database()
        self.assertEqual(result.status, SyncStatus.FAILED)
        self.assertEqual(read_cookie(db), '"local"')
        fdopen.assert_called_once()
        close.assert_called_once_with(fdopen.call_args.args[0])

    def test_cleanup_failure_does_not_mask_failed_result(self):
        db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        db.unlink()
        write_db(db, "local")
        with patch.object(DataSyncService, "_validate_sqlite", return_value=False):
            with patch.object(Path, "unlink", side_effect=OSError("cleanup failed")):
                result = DataSyncService(self.fixture.config()).pull_remote_database()
        self.assertEqual(result.status, SyncStatus.FAILED)
        self.assertEqual(result.stage, "validate_db")
        self.assertEqual(read_cookie(db), '"local"')

    def test_missing_remote_target_preserves_local(self):
        db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        run_git(self.fixture.repo, "rm", "mimo-token-monitor/settings.db")
        run_git(self.fixture.repo, "commit", "-m", "remove target")
        run_git(self.fixture.repo, "push", "origin", "main")
        write_db(db, "local-safe")
        result = DataSyncService(self.fixture.config()).pull_remote_database()
        self.assertEqual(result.status, SyncStatus.FAILED)
        self.assertEqual(read_cookie(db), '"local-safe"')

    def test_fetch_failure_preserves_local_database(self):
        db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        db.unlink()
        write_db(db, "local-safe")
        run_git(self.fixture.repo, "remote", "set-url", "origin",
                str(Path(self.tmp.name) / "missing.git"))
        result = DataSyncService(self.fixture.config()).pull_remote_database()
        self.assertEqual(result.status, SyncStatus.FAILED)
        self.assertEqual(read_cookie(db), '"local-safe"')


class TestAuthGuard(unittest.TestCase):
    """Auth guard: prevent empty-auth overwriting valid-auth configs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mimo_auth_")
        self.fixture = GitRepoFixture(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_push_empty_local_blocks_effective_remote(self):
        db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        db.unlink()
        write_auth_db(db, cookie="")
        result = DataSyncService(self.fixture.config()).push_local_database()
        self.assertEqual(result.status, SyncStatus.SKIPPED)
        self.assertEqual(result.stage, "push")
        self.assertEqual(
            read_cookie_from_git(self.fixture.remote, "mimo-token-monitor/settings.db"),
            '"remote-v1"',
        )

    def test_push_local_with_auth_allows_push(self):
        db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        db.unlink()
        write_auth_db(db, cookie="local-valid")
        result = DataSyncService(self.fixture.config()).push_local_database()
        self.assertEqual(result.status, SyncStatus.SUCCESS)

    def test_push_empty_local_allows_when_remote_also_empty(self):
        remote_db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        remote_db.unlink()
        write_auth_db(remote_db, cookie="")
        run_git(self.fixture.repo, "add", ".")
        run_git(self.fixture.repo, "commit", "-m", "empty remote auth")
        run_git(self.fixture.repo, "push", "origin", "main")
        local_db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        local_db.unlink()
        write_auth_db(local_db, cookie="")
        result = DataSyncService(self.fixture.config()).push_local_database()
        # Both empty => fail-closed: no valid auth on either side
        self.assertEqual(result.status, SyncStatus.FAILED)



    def test_pull_empty_remote_protects_local_with_auth(self):
        db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        db.unlink()
        write_auth_db(db, cookie="local-v1")
        empty_db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        empty_db.unlink()
        write_auth_db(empty_db, cookie="")
        blob = run_git(self.fixture.repo, "hash-object", "-w", str(empty_db)).stdout.strip().decode()
        run_git(self.fixture.repo, "update-index", "--cacheinfo", "100644", blob,
                "mimo-token-monitor/settings.db")
        run_git(self.fixture.repo, "commit", "-m", "empty remote auth")
        run_git(self.fixture.repo, "push", "origin", "main")
        db2 = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        db2.unlink()
        write_auth_db(db2, cookie="local-v1")
        result = DataSyncService(self.fixture.config()).pull_remote_database()
        self.assertEqual(result.status, SyncStatus.SKIPPED)
        self.assertEqual(result.stage, "pull")
        local_cfg = read_cookie(self.fixture.repo / "mimo-token-monitor" / "settings.db")
        self.assertEqual(local_cfg, '"local-v1"')


class TestAuthFailureGuard(unittest.TestCase):
    """Guard must block push/pull when auth read returns None (malformed or I/O failure)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mimo_authfail_")
        self.fixture = GitRepoFixture(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_auth_fields_returns_none_for_malformed_json(self):
        db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        db.unlink()
        write_malformed_auth_db(db)
        result = _read_auth_fields(db)
        self.assertIsNone(result)

    def test_push_blocked_when_local_auth_malformed(self):
        db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        db.unlink()
        write_malformed_auth_db(db)
        result = DataSyncService(self.fixture.config()).push_local_database()
        # local_auth=None => immediate FAILED before reading remote
        self.assertEqual(result.status, SyncStatus.FAILED)
        self.assertIn("本地", result.message)
        self.assertIn("认证", result.message)
        self.assertEqual(
            read_cookie_from_git(self.fixture.remote, "mimo-token-monitor/settings.db"),
            '"remote-v1"',
        )

    def test_push_blocked_when_remote_auth_malformed(self):
        db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        db.unlink()
        write_auth_db(db, cookie="")
        # Commit a malformed-auth DB to the remote so _read_auth_fields returns None
        malformed = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        malformed.unlink()
        write_malformed_auth_db(malformed)
        blob = run_git(self.fixture.repo, "hash-object", "-w", str(malformed)).stdout.strip().decode()
        run_git(self.fixture.repo, "update-index", "--cacheinfo", "100644", blob,
                "mimo-token-monitor/settings.db")
        run_git(self.fixture.repo, "commit", "-m", "malformed remote auth")
        run_git(self.fixture.repo, "push", "origin", "main")
        local = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        local.unlink()
        write_auth_db(local, cookie="")
        result = DataSyncService(self.fixture.config()).push_local_database()
        self.assertEqual(result.status, SyncStatus.FAILED)
        self.assertIn("认证", result.message)



    def test_push_local_malformed_remote_empty(self):
        """Malformed local auth => FAILED even when remote has empty auth."""
        # Replace remote with empty-auth DB
        remote_db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        remote_db.unlink()
        write_auth_db(remote_db, cookie="")
        run_git(self.fixture.repo, "add", ".")
        run_git(self.fixture.repo, "commit", "-m", "empty remote auth")
        run_git(self.fixture.repo, "push", "origin", "main")
        # Local with malformed auth
        local_db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        local_db.unlink()
        write_malformed_auth_db(local_db)
        result = DataSyncService(self.fixture.config()).push_local_database()
        # local_auth=None => immediate FAILED regardless of remote state
        self.assertEqual(result.status, SyncStatus.FAILED)
        self.assertIn("本地", result.message)

    def test_pull_missing_local_malformed_remote(self):
        """Missing local DB + malformed remote auth => FAILED (remote_auth None => FAILED)."""
        # Commit malformed-auth DB to remote
        db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        db.unlink()
        write_malformed_auth_db(db)
        blob = run_git(self.fixture.repo, "hash-object", "-w", str(db)).stdout.strip().decode()
        run_git(self.fixture.repo, "update-index", "--cacheinfo", "100644", blob,
                "mimo-token-monitor/settings.db")
        run_git(self.fixture.repo, "commit", "-m", "malformed remote auth")
        run_git(self.fixture.repo, "push", "origin", "main")
        # Remove local DB so pull attempts to create from remote
        db.unlink()
        result = DataSyncService(self.fixture.config()).pull_remote_database()
        # remote_auth=None => FAILED before any local check
        self.assertEqual(result.status, SyncStatus.FAILED)
        self.assertIn("认证", result.message)
        # Local must not have been created by a failed pull
        self.assertFalse(db.exists())

    def test_read_auth_fields_null_returns_none(self):
        """JSON null stored in an auth field must return None (fail closed)."""
        db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        db.unlink()
        write_null_auth_db(db)
        result = _read_auth_fields(db)
        self.assertIsNone(result)

    def test_read_auth_fields_non_string_returns_none(self):
        """Non-string JSON value (int) in auth field must return None."""
        db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        db.unlink()
        write_int_auth_db(db)
        result = _read_auth_fields(db)
        self.assertIsNone(result)

    def test_push_blocked_when_local_auth_null(self):
        """JSON null cookie in local DB => FAILED (local_auth is None)."""
        db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        db.unlink()
        write_null_auth_db(db)
        result = DataSyncService(self.fixture.config()).push_local_database()
        self.assertEqual(result.status, SyncStatus.FAILED)

    def test_pull_blocked_when_local_auth_malformed(self):
        db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        db.unlink()
        write_malformed_auth_db(db)
        result = DataSyncService(self.fixture.config()).pull_remote_database()
        self.assertEqual(result.status, SyncStatus.FAILED)
        self.assertIn("认证", result.message)
        self.assertTrue(db.exists())
        # Verify local DB was NOT overwritten
        conn = sqlite3.connect(db)
        try:
            cookie_val = conn.execute("SELECT value_json FROM settings WHERE key='cookie'").fetchone()[0]
            self.assertEqual(cookie_val, "not-valid-json{{")
        finally:
            conn.close()

    def test_pull_blocked_when_remote_auth_malformed(self):
        """Remote blob with malformed auth field should block pull."""
        db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        db.unlink()
        write_auth_db(db, cookie="local-ok")
        # Commit malformed auth to remote
        malformed = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        malformed.unlink()
        write_malformed_auth_db(malformed)
        blob = run_git(self.fixture.repo, "hash-object", "-w", str(malformed)).stdout.strip().decode()
        run_git(self.fixture.repo, "update-index", "--cacheinfo", "100644", blob,
                "mimo-token-monitor/settings.db")
        run_git(self.fixture.repo, "commit", "-m", "malformed remote auth")
        run_git(self.fixture.repo, "push", "origin", "main")
        # Restore local with valid auth
        db2 = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        db2.unlink()
        write_auth_db(db2, cookie="local-ok")
        result = DataSyncService(self.fixture.config()).pull_remote_database()
        self.assertEqual(result.status, SyncStatus.FAILED)
        self.assertIn("认证", result.message)


class TestHiddenSubprocessKwargs(unittest.TestCase):
    def test_data_sync_git_runner_receives_hidden_window_kwargs(self):
        with tempfile.TemporaryDirectory(prefix="mimo_hidden_") as tmp:
            config = SyncConfig(
                repo_root=Path(tmp).resolve(),
                data_dir=(Path(tmp) / "mimo-token-monitor").resolve(),
                db_path=(Path(tmp) / "mimo-token-monitor" / "settings.db").resolve(),
                timeout_seconds=5,
            )
            completed = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=b"ok", stderr=b"",
            )
            runner = Mock(return_value=completed)
            service = DataSyncService(config, runner=runner)

            service._git("status")

            runner.assert_called_once()
            _, kwargs = runner.call_args
            expected = hidden_subprocess_kwargs()
            if sys.platform == "win32":
                self.assertEqual(
                    kwargs.get("creationflags"), subprocess.CREATE_NO_WINDOW,
                )
                self.assertIsInstance(
                    kwargs.get("startupinfo"), subprocess.STARTUPINFO,
                )
                self.assertTrue(
                    kwargs["startupinfo"].dwFlags
                    & subprocess.STARTF_USESHOWWINDOW,
                )
                self.assertEqual(
                    kwargs["startupinfo"].wShowWindow,
                    subprocess.SW_HIDE,
                )
            else:
                self.assertNotIn("creationflags", kwargs)
                self.assertNotIn("startupinfo", kwargs)
                self.assertEqual(expected, {})


if __name__ == "__main__":
    unittest.main()
