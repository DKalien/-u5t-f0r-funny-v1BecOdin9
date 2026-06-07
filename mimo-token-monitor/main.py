import sys
import ctypes
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox
from config import load_config, save_config
from widget import TokenWidget, SettingsDialog

MUTEX_NAME = "MiMoTokenMonitor_SingleInstance"


def check_single_instance():
    """检查是否已有实例在运行，使用 Windows Mutex 实现单实例。"""
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return None
    return mutex


def main():
    # 单实例检查
    mutex = check_single_instance()
    if mutex is None:
        app = QApplication(sys.argv)
        QMessageBox.warning(None, "MiMo Token Monitor", "程序已在运行中，请检查系统托盘。")
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    cfg = load_config()

    # First run: open settings if no cookie
    if not cfg.get("cookie"):
        dlg = SettingsDialog(cfg)
        dlg.setWindowTitle("MiMo Token - 首次配置")
        if dlg.exec() == QDialog.DialogCode.Accepted:
            cfg = dlg.get_config()
            save_config(cfg)
        else:
            sys.exit(0)

    widget = TokenWidget(cfg)
    widget.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
