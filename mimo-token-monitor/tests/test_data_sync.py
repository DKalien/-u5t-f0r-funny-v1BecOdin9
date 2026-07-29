import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from data_sync import DataSyncService, GitCommandError, SyncConfig, SyncStatus, _sanitize_detail


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

    def test_common_diagnostic_phrases_are_preserved(self):
        detail = "token usage; password expired; auth failed"
        self.assertEqual(_sanitize_detail(detail), detail)


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


def read_cookie(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT value_json FROM settings WHERE key='cookie'").fetchone()[0]
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




class TestPushLocalDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mimo_push_")
        self.fixture = GitRepoFixture(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_push_changes_only_target_and_preserves_other_worktree_changes(self):
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

    def test_unchanged_database_creates_no_commit(self):
        service = DataSyncService(self.fixture.config())
        before = run_git(self.fixture.repo, "rev-parse", "refs/remotes/origin/main").stdout
        result = service.push_local_database()
        after = run_git(self.fixture.repo, "rev-parse", "refs/remotes/origin/main").stdout
        self.assertEqual(result.status, SyncStatus.NO_CHANGE)
        self.assertEqual(after, before)


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
