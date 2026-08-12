import os
import sys
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

_ORIGINAL_QT_QPA_PLATFORM = os.environ.get("QT_QPA_PLATFORM")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QCloseEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from router_control import RouterResult  # noqa: E402
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


class TestStartupRuntime(unittest.TestCase):
    @patch("main.load_config", return_value={"cookie": "x"})
    def test_initialize_window_loads_local_config_before_showing(self, load_config):
        calls = []
        load_config.side_effect = lambda: calls.append("load") or {"cookie": "x"}
        from main import initialize_window
        with patch("main.TokenWidget") as widget_type:
            widget_type.return_value.show = Mock(side_effect=lambda: calls.append("show"))
            widget = initialize_window()
        self.assertIs(widget, widget_type.return_value)
        widget_type.assert_called_once_with({"cookie": "x"})
        self.assertEqual(calls, ["load", "show"])


class TestExitRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

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
            self.assertIn("路由控制（状态未知）", actions)
            self.assertIn("重启悬浮窗", actions)
            router_menu = actions["路由控制（状态未知）"].menu()
            self.assertIsNotNone(router_menu)
            self.assertEqual(
                [action.text() for action in router_menu.actions() if not action.isSeparator()],
                ["开启路由", "关闭路由", "重启路由器"],
            )

    def test_router_status_updates_tray_menu(self):
        with managed_widget({"position": [100, 100]}) as widget:
            worker = Mock()
            widget._router_worker = worker

            widget._on_router_status_done(
                RouterResult(True, "路由已开启", route_enabled=True)
            )

            self.assertEqual(widget._router_menu_action.text(), "路由控制（已开启）")

            widget._router_worker = Mock()
            widget._on_router_status_done(
                RouterResult(True, "路由已关闭", route_enabled=False)
            )

            self.assertEqual(widget._router_menu_action.text(), "路由控制（已关闭）")

    def test_router_status_refresh_keeps_visible_menu_stable(self):
        with managed_widget({"position": [100, 100]}) as widget:
            widget._set_router_state(True)
            size_before = widget._tray_menu.sizeHint()
            worker = Mock()
            with patch("widget.RouterWorker", return_value=worker):
                widget._refresh_router_state()
            widget._on_router_status_done(
                RouterResult(True, "路由已开启", route_enabled=True)
            )

            self.assertEqual(widget._router_menu_action.text(), "路由控制（已开启）")
            self.assertEqual(widget._tray_menu.sizeHint(), size_before)
            self.assertTrue(all(action.isEnabled() for action in widget._router_actions))
            worker.start.assert_called_once()

    def test_router_action_starts_before_deferred_menu_refresh(self):
        with managed_widget({"position": [100, 100]}) as widget:
            widget._router_worker = None
            worker = Mock()
            worker.isRunning.return_value = True
            widget._tray_icon.showMessage = Mock()
            router_menu = next(
                action.menu()
                for action in widget._tray_menu.actions()
                if action.text() == "路由控制（状态未知）"
            )
            enable_action = next(
                action
                for action in router_menu.actions()
                if action.text() == "开启路由"
            )

            with patch("widget.RouterWorker", return_value=worker) as worker_type:
                widget._tray_menu.aboutToHide.emit()
                enable_action.trigger()
                QApplication.processEvents()

            worker_type.assert_called_once_with("enable", widget)
            worker.start.assert_called_once()
            self.assertIs(widget._router_worker, worker)

    def test_restart_action_requests_exit(self):
        callback = Mock()
        with managed_widget(
            {"position": [100, 100]}, exit_callback=callback
        ) as widget:
            actions = {
                action.text(): action
                for action in widget._tray_icon.contextMenu().actions()
            }

            actions["重启悬浮窗"].trigger()

            self.assertTrue(widget._restart_requested)
            callback.assert_called_once()

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

    def test_quit_action_requests_exit_once(self):
        callback = Mock()
        cfg = {"cookie": "x", "position": [100, 100], "refresh_interval": 300,
               "opacity": 0.85, "always_on_top": True}
        with managed_widget(cfg, exit_callback=callback) as widget:
            widget._quit_app()
            widget._quit_app()
        callback.assert_called_once()

    @patch("main.QProcess.startDetached", return_value=(True, 123))
    def test_restart_relaunches_source_after_shutdown(self, start_detached):
        from main import restart_application

        with (
            patch.object(sys, "argv", ["D:\\app\\main.py"]),
            patch.object(sys, "executable", "D:\\Python\\pythonw.exe"),
            patch.object(sys, "frozen", False, create=True),
        ):
            self.assertTrue(restart_application())

        start_detached.assert_called_once_with(
            "D:\\Python\\pythonw.exe",
            ["D:\\app\\main.py"],
            os.path.dirname(os.path.dirname(__file__)),
        )

    @patch("main.save_config")
    @patch("main.load_config", return_value={})
    @patch("main.SettingsDialog")
    def test_first_configuration_cancel_returns_safely(
        self, dialog_type, _load, save_config
    ):
        from PyQt6.QtWidgets import QDialog
        from main import initialize_window
        dialog_type.return_value.exec.return_value = QDialog.DialogCode.Rejected
        with patch("main.TokenWidget") as widget_type:
            widget = initialize_window()
        self.assertIsNone(widget)
        widget_type.assert_not_called()
        dialog_type.assert_called_once_with({})
        dialog_type.return_value.setWindowTitle.assert_called_once_with("MiMo Token - 首次配置")
        save_config.assert_not_called()

    @patch("main.activate_existing_instance")
    @patch("main.check_single_instance", return_value=None)
    def test_duplicate_instance_returns_before_initialization(self, _check, activate):
        from main import main
        self.assertEqual(main(), 0)
        activate.assert_called_once()
