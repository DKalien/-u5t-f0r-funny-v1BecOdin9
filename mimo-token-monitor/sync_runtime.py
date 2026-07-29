from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QEventLoop, QThread, pyqtSignal

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
