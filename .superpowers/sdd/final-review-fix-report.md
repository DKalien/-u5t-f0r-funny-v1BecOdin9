# Final Review Fix Report

## Findings 映射

- Important 1：`mimo-token-monitor/data_sync.py` 为每次 pull/push 建立单一 `time.monotonic()` deadline，并将剩余预算显式传入 validate、fetch/show、临时 index、blob/tree/commit、push/update-ref；预算耗尽前不再调用 runner。新增 fake clock/runner 测试覆盖 pull 总预算和 push retry 不刷新预算。
- Important 2：`mimo-token-monitor/widget.py` 的 `_quit_app()` 首次请求立即设置 `_exit_requested`、禁用 widget、停止 timer；刷新、Cookie 导入、设置、显示模式、置顶、拖动保存和 worker 回写入口均在退出期间 guard。新增 offscreen 生命周期测试验证 disabled、timer stopped、入口不写配置。
- Important 3：`sanitize_detail` 增加 Cookie/Set-Cookie header、cookie/session/sessionid 等 key 及 URL query 脱敏；新增测试确认敏感值不出现在 detail，同时保留普通 `cookie policy` 文本。
- Minor 1：`test_fetch_failure_preserves_local_database` 已改为调用 `pull_remote_database()`；新增 pull deadline 测试。
- Minor 2：`AGENTS.md` 改为“Python 模块采用单目录扁平结构”，并补充 operation deadline 语义；README 补充默认 30 秒为每次启动/退出同步总预算而非每条命令。

## TDD 证据

先加入 deadline、Cookie 脱敏和退出冻结回归测试并运行 targeted tests，再完成实现并运行 targeted tests GREEN；随后执行完整回归。

## 验证

- `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v`：49 tests，全部通过。
- `python -m compileall -q .`：通过。
- `git diff --check`：通过。

## Concerns

- 同步线程仍由现有 Qt worker 编排，不使用 `terminate()`；Git 子进程 timeout 由 `subprocess.run` 负责终止并等待。
- 测试仅使用 tempfile/local Git fixture/offscreen Qt，未访问真实数据目录、网络、GitHub 或凭据。
