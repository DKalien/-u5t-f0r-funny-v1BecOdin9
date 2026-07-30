import os

_ORIGINAL_QT_QPA_PLATFORM = os.environ.get("QT_QPA_PLATFORM")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch
from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication

from data_sync import SyncResult, SyncStatus
from sync_runtime import ExitSyncController, run_startup_sync
from widget import TokenWidget


def tearDownModule():
    if _ORIGINAL_QT_QPA_PLATFORM is None:
        os.environ.pop("QT_QPA_PLATFORM", None)
    else:
        os.environ["QT_QPA_PLATFORM"] = _ORIGINAL_QT_QPA_PLATFORM


@contextmanager
def managed_widget(cfg, **kwargs):
    with patch("widget.save_config"), patch.object(TokenWidget, "_do_fetch"):
        widget = TokenWidget(cfg, **kwargs)
        try:
            yield widget
        finally:
            widget._timer.stop()
            widget.close()
            widget.deleteLater()
            QApplication.processEvents()


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

    def test_combined_successful_startup_sync_is_not_reported_as_warning(self):
        from main import _combine_startup_results

        result = _combine_startup_results(
            SyncResult(SyncStatus.SUCCESS, "pull", "db pulled"),
            SyncResult(SyncStatus.NO_CHANGE, "code_pull", "code unchanged"),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.status, SyncStatus.SUCCESS)


class FakePushService:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def push_local_database(self):
        self.calls += 1
        return self.result


class FakeCodePush:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def push_local_changes(self):
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

    def test_exit_runs_database_and_code_push_before_quitting(self):
        database = FakePushService(SyncResult(SyncStatus.NO_CHANGE, "push", "db unchanged"))
        code = FakeCodePush(SyncResult(SyncStatus.SUCCESS, "code_push", "code pushed"))
        quit_callback = Mock()
        notify_callback = Mock()
        controller = ExitSyncController(
            database,
            quit_callback,
            notify_callback,
            additional_operations=[code.push_local_changes],
        )
        loop = QEventLoop()
        controller.finished.connect(lambda _result: loop.quit())

        controller.request_exit()
        QTimer.singleShot(5000, loop.quit)
        loop.exec()

        self.assertEqual(database.calls, 1)
        self.assertEqual(code.calls, 1)
        notify_callback.assert_not_called()
        quit_callback.assert_called_once()

    def test_no_service_quits_immediately(self):
        quit_callback = Mock()
        controller = ExitSyncController(None, quit_callback, Mock())
        controller.request_exit()
        quit_callback.assert_called_once()

    def test_widget_true_quit_calls_callback_once(self):
        from widget import TokenWidget

        callback = Mock()
        with managed_widget({"position": [100, 100]}, exit_callback=callback) as widget:
            widget._exit_callback = callback
            widget._quit_app()
            widget._quit_app()
        callback.assert_called_once()

    def test_quit_freezes_widget_and_blocks_write_entries(self):
        callback = Mock()
        with patch("widget.save_config") as save_config, patch.object(TokenWidget, "_do_fetch") as fetch:
            widget = TokenWidget({"position": [100, 100]}, exit_callback=callback)
            fetch.reset_mock()
            widget._quit_app()
            self.assertFalse(widget.isEnabled())
            self.assertFalse(widget._timer.isActive())
            widget._open_settings()
            widget._import_cookie_quick()
            widget._do_fetch()
            save_config.assert_not_called()
            widget.deleteLater()
            QApplication.processEvents()

    def test_widget_close_event_only_hides_to_tray(self):
        callback = Mock()
        event = Mock()
        with managed_widget({"position": [100, 100]}, exit_callback=callback) as widget:
            widget._tray_icon.showMessage = Mock()
            widget.closeEvent(event)
            event.ignore.assert_called_once()
            callback.assert_not_called()
            widget._tray_icon.showMessage.assert_called_once()



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
            widget_type.return_value.show = Mock()
            widget, result = initialize_window(self.app, Mock())
        self.assertIs(widget, widget_type.return_value)
        widget_type.return_value.show.assert_called_once()
        self.assertEqual(result.status, SyncStatus.FAILED)

    def test_close_event_hides_without_requesting_exit(self):
        callback = Mock()
        cfg = {"cookie": "x", "position": [100, 100], "refresh_interval": 300,
               "opacity": 0.85, "always_on_top": True}
        with managed_widget(cfg, exit_callback=callback) as widget:
            widget._tray_icon = Mock()
            event = QCloseEvent()
            widget.closeEvent(event)
        self.assertFalse(event.isAccepted())
        callback.assert_not_called()

    def test_quit_action_requests_push_once(self):
        callback = Mock()
        cfg = {"cookie": "x", "position": [100, 100], "refresh_interval": 300,
               "opacity": 0.85, "always_on_top": True}
        with managed_widget(cfg, exit_callback=callback) as widget:
            widget._quit_app()
            widget._quit_app()
        callback.assert_called_once()

    @patch("main.save_config")
    @patch("main.load_config", return_value={})
    @patch("main.run_startup_sync", return_value=SyncResult(
        SyncStatus.SKIPPED, "config", "同步配置无效"
    ))
    @patch("main.SettingsDialog")
    def test_first_configuration_cancel_returns_safely(self, dialog_type, _sync, _load, save_config):
        from PyQt6.QtWidgets import QDialog
        from main import initialize_window
        dialog_type.return_value.exec.return_value = QDialog.DialogCode.Rejected
        with patch("main.TokenWidget") as widget_type:
            widget, result = initialize_window(self.app, Mock())
        self.assertIsNone(widget)
        self.assertEqual(result.status, SyncStatus.SKIPPED)
        widget_type.assert_not_called()
        dialog_type.assert_called_once_with({})
        dialog_type.return_value.setWindowTitle.assert_called_once_with("MiMo Token - 首次配置")
        save_config.assert_not_called()

    @patch("main.save_config")
    @patch("main.load_config", return_value={})
    @patch("main.run_startup_sync", return_value=SyncResult(
        SyncStatus.SKIPPED, "config", "同步配置无效"
    ))
    @patch("main.SettingsDialog")
    def test_first_configuration_registers_dialog_for_activation(
        self, dialog_type, _sync, _load, save_config
    ):
        from PyQt6.QtWidgets import QDialog
        from main import initialize_window

        dialog = dialog_type.return_value
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        dialog.get_config.return_value = {"cookie": "configured"}
        register_target = Mock()

        with patch("main.TokenWidget") as widget_type:
            widget_type.return_value.show = Mock()
            widget, _result = initialize_window(
                self.app,
                Mock(),
                activation_target_callback=register_target,
            )

        self.assertIs(widget, widget_type.return_value)
        register_target.assert_any_call(dialog)
        register_target.assert_any_call(widget)
        self.assertEqual(register_target.call_count, 2)
        dialog.setWindowFlag.assert_called_once()
        dialog.show.assert_called_once()
        dialog.raise_.assert_called_once()
        dialog.activateWindow.assert_called_once()
        save_config.assert_called_once_with({"cookie": "configured"})

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
