import os
from pathlib import Path
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

    def test_non_sensitive_diagnostic_is_preserved(self):
        detail = "fatal: repository unavailable; hint: check ordinary value"
        self.assertEqual(_sanitize_detail(detail), detail)
