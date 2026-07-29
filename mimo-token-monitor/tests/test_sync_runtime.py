import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest.mock import Mock, patch
from PyQt6.QtWidgets import QApplication

from data_sync import SyncResult, SyncStatus
from sync_runtime import run_startup_sync


class FakeService:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def pull_remote_database(self):
        self.calls.append("pull")
        return self.result


class TestStartupRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_startup_sync_returns_worker_result(self):
        expected = SyncResult(SyncStatus.SUCCESS, "pull", "ok")
        service = FakeService(expected)
        self.assertEqual(run_startup_sync(service, self.app), expected)
        self.assertEqual(service.calls, ["pull"])

    @patch("main.load_config", return_value={"cookie": "x"})
    @patch("main.run_startup_sync")
    def test_initialize_window_syncs_before_loading_config(self, run_sync, load_config):
        calls = []
        run_sync.side_effect = lambda *args: calls.append("sync") or SyncResult(
            SyncStatus.SUCCESS, "pull", "ok")
        load_config.side_effect = lambda: calls.append("load") or {"cookie": "x"}
        from main import initialize_window
        with patch("main.TokenWidget") as widget_type:
            widget_type.return_value.show = Mock(side_effect=lambda: calls.append("show"))
            widget, result = initialize_window(self.app, Mock())
        self.assertIs(widget, widget_type.return_value)
        self.assertEqual(result.status, SyncStatus.SUCCESS)
        self.assertEqual(calls, ["sync", "load", "show"])


if __name__ == "__main__":
    unittest.main()
