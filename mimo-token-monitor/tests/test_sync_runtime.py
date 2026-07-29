import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest.mock import Mock, patch
from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication

from data_sync import SyncResult, SyncStatus
from sync_runtime import ExitSyncController, run_startup_sync
from widget import TokenWidget


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

    def test_startup_sync_sanitizes_worker_exception_detail(self):
        class FailingService:
            def pull_remote_database(self):
                raise RuntimeError(
                    "普通上下文 ssh://user:password@host token=abc "
                    "Bearer secret-token"
                )

        result = run_startup_sync(FailingService(), self.app)

        self.assertEqual(result.status, SyncStatus.FAILED)
        self.assertIn("普通上下文", result.detail)
        self.assertNotIn("password", result.detail)
        self.assertNotIn("abc", result.detail)
        self.assertNotIn("secret-token", result.detail)

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


class FakePushService:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def push_local_database(self):
        self.calls += 1
        return self.result


class TestExitRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_duplicate_exit_request_starts_one_push_and_quits_after_failure(self):
        service = FakePushService(SyncResult(SyncStatus.FAILED, "push", "offline"))
        quit_callback = Mock()
        notify_callback = Mock()
        controller = ExitSyncController(service, quit_callback, notify_callback)
        loop = QEventLoop()
        controller.finished.connect(lambda _result: loop.quit())

        controller.request_exit()
        controller.request_exit()
        QTimer.singleShot(5000, loop.quit)
        loop.exec()

        self.assertEqual(service.calls, 1)
        notify_callback.assert_called_once()
        quit_callback.assert_called_once()

    def test_no_service_quits_immediately(self):
        quit_callback = Mock()
        controller = ExitSyncController(None, quit_callback, Mock())
        controller.request_exit()
        quit_callback.assert_called_once()

    def test_widget_true_quit_calls_callback_once(self):
        from widget import TokenWidget

        callback = Mock()
        with patch("widget.save_config"), patch.object(TokenWidget, "_do_fetch"):
            widget = TokenWidget({"position": [100, 100]})
        widget._exit_callback = callback
        widget._quit_app()
        widget._quit_app()
        callback.assert_called_once()
        widget.deleteLater()

    def test_widget_close_event_only_hides_to_tray(self):
        from widget import TokenWidget

        callback = Mock()
        event = Mock()
        with patch("widget.save_config"), patch.object(TokenWidget, "_do_fetch"):
            widget = TokenWidget({"position": [100, 100]}, exit_callback=callback)
        widget._tray_icon.showMessage = Mock()
        widget.closeEvent(event)
        event.ignore.assert_called_once()
        callback.assert_not_called()
        widget._tray_icon.showMessage.assert_called_once()
        widget.deleteLater()



class TestLifecycleDegradation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @patch("main.load_config", return_value={"cookie": "configured"})
    @patch("main.run_startup_sync", return_value=SyncResult(
        SyncStatus.FAILED, "pull", "网络不可用"
    ))
    def test_failed_startup_sync_still_creates_widget(self, _sync, _load):
        from main import initialize_window
        with patch("main.TokenWidget") as widget_type:
            widget, result = initialize_window(self.app, Mock())
        self.assertIs(widget, widget_type.return_value)
        self.assertEqual(result.status, SyncStatus.FAILED)

    def test_close_event_hides_without_requesting_exit(self):
        callback = Mock()
        cfg = {"cookie": "x", "position": [100, 100], "refresh_interval": 300,
               "opacity": 0.85, "always_on_top": True}
        with patch.object(TokenWidget, "_setup_tray"), patch("widget.save_config"):
            widget = TokenWidget(cfg, exit_callback=callback)
            widget._tray_icon = Mock()
            event = QCloseEvent()
            widget.closeEvent(event)
        self.assertFalse(event.isAccepted())
        callback.assert_not_called()

    def test_quit_action_requests_push_once(self):
        callback = Mock()
        cfg = {"cookie": "x", "position": [100, 100], "refresh_interval": 300,
               "opacity": 0.85, "always_on_top": True}
        with patch.object(TokenWidget, "_setup_tray"), patch("widget.save_config"):
            widget = TokenWidget(cfg, exit_callback=callback)
            widget._quit_app()
            widget._quit_app()
        callback.assert_called_once()

    @patch("main.load_config", return_value={})
    @patch("main.run_startup_sync", return_value=SyncResult(
        SyncStatus.SKIPPED, "config", "同步配置无效"
    ))
    @patch("main.SettingsDialog")
    def test_first_configuration_cancel_returns_safely(self, dialog_type, _sync, _load):
        from PyQt6.QtWidgets import QDialog
        from main import initialize_window
        dialog_type.return_value.exec.return_value = QDialog.DialogCode.Rejected
        with patch("main.TokenWidget") as widget_type:
            widget, result = initialize_window(self.app, Mock())
        self.assertIsNone(widget)
        self.assertEqual(result.status, SyncStatus.SKIPPED)
        widget_type.assert_not_called()

    @patch("main.run_startup_sync")
    @patch("main.build_sync_service")
    @patch("main.activate_existing_instance")
    @patch("main.check_single_instance", return_value=None)
    def test_duplicate_instance_returns_before_sync(
        self, _check, activate, build_service, run_sync
    ):
        from main import main
        self.assertEqual(main(), 0)
        activate.assert_called_once()
        build_service.assert_not_called()
        run_sync.assert_not_called()
