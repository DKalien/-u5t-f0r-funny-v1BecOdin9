import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QApplication, QDialog

from config import load_config, save_config
from code_sync import (
    CODE_SYNC_RESULT_ENV,
    CodeSyncConfig,
    CodeSyncService,
    decode_sync_result,
    run_startup_code_sync,
)
from data_sync import DataSyncService, SyncConfig, SyncResult, SyncStatus
from sync_runtime import ExitSyncController, run_startup_sync
from widget import SettingsDialog, TokenWidget


MUTEX_NAME = "MiMoTokenMonitor_SingleInstance"
ACTIVATE_EVENT_NAME = "MiMoTokenMonitor_ActivateExistingInstance"
ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = (
    wintypes.LPVOID,
    wintypes.BOOL,
    wintypes.BOOL,
    wintypes.LPCWSTR,
)
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.OpenEventW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
kernel32.OpenEventW.restype = wintypes.HANDLE
kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
kernel32.SetEvent.restype = wintypes.BOOL
kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.CloseHandle.restype = wintypes.BOOL


def check_single_instance():
    """Create the mutex, or return None when another process already owns it."""
    ctypes.set_last_error(0)
    mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(mutex)
        return None
    return mutex


def create_activation_event():
    """Create the event used by a later launch to wake this instance."""
    event = kernel32.CreateEventW(None, False, False, ACTIVATE_EVENT_NAME)
    if not event:
        raise ctypes.WinError(ctypes.get_last_error())
    return event


def activate_existing_instance() -> bool:
    """Ask the running instance to restore and focus its floating window."""
    event = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, ACTIVATE_EVENT_NAME)
    if not event:
        return False
    try:
        return bool(kernel32.SetEvent(event))
    finally:
        kernel32.CloseHandle(event)


def activation_requested(event) -> bool:
    return kernel32.WaitForSingleObject(event, 0) == WAIT_OBJECT_0


def build_sync_service() -> tuple[DataSyncService | None, SyncResult | None]:
    try:
        return DataSyncService(SyncConfig.from_environment()), None
    except ValueError as exc:
        return None, SyncResult(SyncStatus.SKIPPED, "config", str(exc))


def build_code_sync_service() -> CodeSyncService | None:
    try:
        service = CodeSyncService(
            CodeSyncConfig.from_project_root(Path(__file__).resolve().parent)
        )
        return service if service.config.enabled else None
    except (OSError, ValueError):
        return None


def _read_startup_code_sync_result() -> SyncResult:
    encoded = os.environ.pop(CODE_SYNC_RESULT_ENV, "")
    if encoded:
        result = decode_sync_result(encoded)
        if result is not None:
            return result
    return run_startup_code_sync(Path(__file__).resolve().parent)


def _combine_startup_results(*results: SyncResult | None) -> SyncResult:
    actual = [result for result in results if result is not None]
    if len(actual) == 1:
        return actual[0]
    if not actual:
        return SyncResult(SyncStatus.NO_CHANGE, "startup", "启动同步已完成")
    if all(result.ok for result in actual):
        return SyncResult(
            SyncStatus.SUCCESS,
            "startup",
            "代码仓库和设置数据库已检查",
        )

    failed = [result for result in actual if result.status == SyncStatus.FAILED]
    not_ok = [result for result in actual if not result.ok]
    status = SyncStatus.FAILED if failed else SyncStatus.SKIPPED
    message = "；".join(result.message for result in not_ok)
    if not message:
        message = "代码仓库和设置数据库已检查"
    detail = " | ".join(result.detail for result in not_ok if result.detail)
    return SyncResult(status, "startup", message, detail)


def initialize_window(
    app,
    service,
    code_service=None,
    code_sync_result=None,
    config_result=None,
    activation_target_callback=None,
):
    data_result = (
        run_startup_sync(service, app)
        if service is not None
        else SyncResult(SyncStatus.SKIPPED, "config", "同步配置无效")
    )
    startup_result = _combine_startup_results(
        config_result or data_result,
        code_sync_result,
    )
    cfg = load_config()

    if not cfg.get("cookie"):
        dlg = SettingsDialog(cfg)
        dlg.setWindowTitle("MiMo Token - 首次配置")
        if activation_target_callback is not None:
            activation_target_callback(dlg)
        # A first-run dialog is the only visible window at this point.  Make
        # it foreground explicitly because pythonw-launched Qt dialogs can be
        # created behind the active application on Windows.
        dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        if dlg.exec() == QDialog.DialogCode.Accepted:
            cfg = dlg.get_config()
            save_config(cfg)
        else:
            return None, startup_result

    widget = TokenWidget(cfg, startup_sync_result=startup_result)
    controller = ExitSyncController(
        service,
        widget.finish_quit,
        widget.show_sync_result,
        parent=widget,
        additional_operations=(
            [code_service.push_local_changes] if code_service is not None else []
        ),
    )
    widget._exit_callback = controller.request_exit
    widget._exit_sync_controller = controller
    if activation_target_callback is not None:
        activation_target_callback(widget)
    widget.show()
    return widget, startup_result


def main() -> int:
    mutex = check_single_instance()
    if mutex is None:
        # Do not leave a second pythonw process blocked by a modal warning.
        # If the first instance is hidden, this makes the app visible again.
        activate_existing_instance()
        return 0

    code_startup_result = _read_startup_code_sync_result()
    code_service = build_code_sync_service()
    activation_event = create_activation_event()
    try:
        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)

        activation_target = [None]

        def register_activation_target(target):
            activation_target[0] = target

        activation_timer = QTimer()
        activation_timer.setInterval(250)

        def restore_existing_window():
            target = activation_target[0]
            if target is None:
                return
            if not activation_requested(activation_event):
                return
            if hasattr(target, "_show_window"):
                target._show_window()
                return
            target.show()
            target.raise_()
            target.activateWindow()

        activation_timer.timeout.connect(restore_existing_window)
        activation_timer.start()

        service, config_result = build_sync_service()
        widget, startup_result = initialize_window(
            app,
            service,
            code_service=code_service,
            code_sync_result=code_startup_result,
            config_result=config_result,
            activation_target_callback=register_activation_target,
        )
        if widget is None:
            return 0
        return app.exec()
    finally:
        if "activation_timer" in locals():
            activation_timer.stop()
        kernel32.CloseHandle(activation_event)
        kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    raise SystemExit(main())
