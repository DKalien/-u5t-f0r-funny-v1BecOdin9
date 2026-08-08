# Task 7 实施报告

## 状态
完成。新增真正退出时的异步数据库推送，并保持托盘关闭行为不变。

## 变更
- `sync_runtime.py` 新增 `ExitSyncController(QObject)`：使用 `SyncWorker(QThread)` 执行 `push_local_database`，重复请求只启动一次；无 service 立即退出；FAILED/SKIPPED 先通知后退出；通知异常不会阻止退出；保留 worker 引用并等待线程结束。
- `main.py` 将退出控制器绑定到 `TokenWidget.finish_quit` 与 `show_sync_result`。
- `widget.py` 支持退出回调、启动同步失败托盘通知和幂等真正退出；`closeEvent` 仍只 `ignore()` 并隐藏窗口。
- `tests/test_sync_runtime.py` 覆盖重复退出、失败仍退出、无 service、真正退出回调一次及 closeEvent 不触发回调。

## TDD
先追加退出行为测试并确认因 `ExitSyncController` 缺失产生 ImportError（RED），再实现控制器与窗口接线并逐步修复测试，最终通过（GREEN）。

## 测试
- `QT_QPA_PLATFORM=offscreen python -m unittest tests.test_sync_runtime -v`：7 tests passed
- `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v`：37 tests passed
- `python -m compileall -q sync_runtime.py main.py widget.py`：通过

## 自审
确认退出控制器以 widget 为 QObject parent（兼容测试替身），worker 由 controller 持有；通知回调异常被隔离；`closeEvent` 未调用退出回调；未触发真实数据目录或远程 Git。

## Commit
待提交：`feat(data-sync): 真正退出时后台推送设置`
