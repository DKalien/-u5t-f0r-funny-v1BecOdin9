from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, QEventLoop, QThread, pyqtSignal

from data_sync import DataSyncService, SyncResult, SyncStatus, sanitize_detail


class SyncWorker(QThread):
    completed = pyqtSignal(object)

    def __init__(self, operation: Callable[[], SyncResult], parent=None):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            result = self.operation()
        except Exception as exc:
            result = SyncResult(
                SyncStatus.FAILED,
                "runtime",
                "同步线程异常",
                sanitize_detail(str(exc)),
            )
        self.completed.emit(result)


class ExitSyncController(QObject):
    """Run the final database push once before quitting the application."""

    finished = pyqtSignal(object)

    def __init__(self, service, quit_callback, notify_callback, parent=None):
        super().__init__(parent if isinstance(parent, QObject) else None)
        self.service = service
        self.quit_callback = quit_callback
        self.notify_callback = notify_callback
        self._worker = None
        self._exiting = False

    def request_exit(self):
        if self._exiting:
            return
        self._exiting = True
        if self.service is None:
            self.quit_callback()
            return

        self._worker = SyncWorker(self.service.push_local_database, self)
        self._worker.completed.connect(self._complete)
        self._worker.start()

    def _complete(self, result: SyncResult):
        try:
            if result.status in {SyncStatus.FAILED, SyncStatus.SKIPPED}:
                try:
                    self.notify_callback(result)
                except Exception:
                    pass
            self.finished.emit(result)
        finally:
            if self._worker is not None:
                self._worker.wait()
            self.quit_callback()


def run_startup_sync(service: DataSyncService, app) -> SyncResult:
    loop = QEventLoop()
    holder: dict[str, SyncResult] = {}
    worker = SyncWorker(service.pull_remote_database, app)

    def complete(result: SyncResult):
        holder["result"] = result
        loop.quit()

    worker.completed.connect(complete)
    worker.start()
    loop.exec()
    worker.wait()
    return holder["result"]
