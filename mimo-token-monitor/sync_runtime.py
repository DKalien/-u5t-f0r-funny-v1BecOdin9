from __future__ import annotations

from collections.abc import Callable, Iterable

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
    """Run final database/code pushes once before quitting the application."""

    finished = pyqtSignal(object)

    def __init__(
        self,
        service,
        quit_callback,
        notify_callback,
        parent=None,
        additional_operations: Iterable[Callable[[], SyncResult]] = (),
    ):
        super().__init__(parent if isinstance(parent, QObject) else None)
        self.quit_callback = quit_callback
        self.notify_callback = notify_callback
        self.operations = []
        if service is not None:
            self.operations.append(service.push_local_database)
        self.operations.extend(additional_operations)
        self._worker = None
        self._exiting = False

    def request_exit(self):
        if self._exiting:
            return
        self._exiting = True
        if not self.operations:
            self.quit_callback()
            return

        self._worker = SyncWorker(self._run_operations, self)
        self._worker.completed.connect(self._complete)
        self._worker.start()

    def _run_operations(self):
        results = []
        for operation in self.operations:
            try:
                results.append(operation())
            except Exception as exc:
                results.append(
                    SyncResult(
                        SyncStatus.FAILED,
                        "runtime",
                        "同步线程异常",
                        sanitize_detail(str(exc)),
                    )
                )
        return results

    def _complete(self, result: SyncResult):
        try:
            results = result if isinstance(result, (list, tuple)) else [result]
            for item in results:
                if item.status not in {SyncStatus.FAILED, SyncStatus.SKIPPED}:
                    continue
                try:
                    self.notify_callback(item)
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
