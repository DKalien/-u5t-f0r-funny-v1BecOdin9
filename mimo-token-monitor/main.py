import ctypes
import sys
from ctypes import wintypes

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QDialog

from config import load_config, save_config
from data_sync import DataSyncService, SyncConfig, SyncResult, SyncStatus
from sync_runtime import run_startup_sync
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


def initialize_window(app, service):
    startup_result = (
        run_startup_sync(service, app)
        if service is not None
        else SyncResult(SyncStatus.SKIPPED, "config", "同步配置无效")
    )
    cfg = load_config()

    if not cfg.get("cookie"):
        dlg = SettingsDialog(cfg)
        dlg.setWindowTitle("MiMo Token - 首次配置")
        if dlg.exec() == QDialog.DialogCode.Accepted:
            cfg = dlg.get_config()
            save_config(cfg)
        else:
            return None, startup_result

    widget = TokenWidget(cfg)
    widget.show()
    return widget, startup_result


def main() -> int:
    mutex = check_single_instance()
    if mutex is None:
        # Do not leave a second pythonw process blocked by a modal warning.
        # If the first instance is hidden, this makes the app visible again.
        activate_existing_instance()
        return 0

    activation_event = create_activation_event()
    try:
        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)

        service, config_result = build_sync_service()
        widget, startup_result = initialize_window(app, service)
        if config_result is not None:
            startup_result = config_result
        if widget is None:
            return 0

        activation_timer = QTimer()
        activation_timer.setInterval(250)

        def restore_existing_window():
            if activation_requested(activation_event):
                widget._show_window()

        activation_timer.timeout.connect(restore_existing_window)
        activation_timer.start()
        return app.exec()
    finally:
        kernel32.CloseHandle(activation_event)
        kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    raise SystemExit(main())
