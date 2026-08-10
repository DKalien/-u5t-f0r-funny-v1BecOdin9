import os
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

_ORIGINAL_QT_QPA_PLATFORM = os.environ.get("QT_QPA_PLATFORM")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEventLoop, QTimer  # noqa: E402
from PyQt6.QtGui import QCloseEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from data_sync import SyncResult, SyncStatus  # noqa: E402
from router_control import RouterResult  # noqa: E402
from sync_runtime import ExitSyncController, run_startup_sync  # noqa: E402
from widget import TokenWidget  # noqa: E402


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

    def test_tray_menu_exposes_router_maintenance(self):
        with managed_widget({"position": [100, 100]}) as widget:
            menu = widget._tray_icon.contextMenu()
            actions = {action.text(): action for action in menu.actions()}
            self.assertIn("更新模型元数据", actions)
            self.assertIn("路由控制", actions)
            router_menu = actions["路由控制"].menu()
            self.assertIsNotNone(router_menu)
            self.assertEqual(
                [action.text() for action in router_menu.actions() if not action.isSeparator()],
                ["开启路由", "关闭路由", "重启路由器"],
            )

    def test_router_completion_reenables_menu_and_notifies(self):
        with managed_widget({"position": [100, 100]}) as widget:
            worker = Mock()
            widget._router_worker = worker
            widget._tray_icon.showMessage = Mock()
            for action in widget._router_actions:
                action.setEnabled(False)

            widget._on_router_operation_done(
                RouterResult(True, "模型元数据已更新，路由器已重启")
            )

            worker.wait.assert_called_once()
            worker.deleteLater.assert_called_once()
            self.assertIsNone(widget._router_worker)
            self.assertTrue(all(action.isEnabled() for action in widget._router_actions))
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
