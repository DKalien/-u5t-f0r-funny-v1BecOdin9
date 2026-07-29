# Task 3 实施报告

## 状态
已完成。启动时从安全 Git 边界 fetch 远端分支，导出固定 settings.db blob，执行 SQLite `PRAGMA quick_check`，通过后使用 `os.replace` 原子覆盖本地数据库；无效远端数据库保留本地文件并清理临时文件。

## 改动
- `mimo-token-monitor/data_sync.py`
  - 新增 `sqlite3`、`tempfile` 依赖。
  - 新增 `remote_ref` 属性。
  - 新增 `_validate_sqlite(path)`，使用只读连接及 `quick_check`，显式关闭连接。
  - 新增 `pull_remote_database()`：复用 `validate_repository()`，执行 fetch/show，使用数据目录内随机临时文件、flush/fsync、SQLite 校验和原子替换；Git/OS 错误脱敏返回 `SyncResult`。
- `mimo-token-monitor/tests/test_data_sync.py`
  - 新增真实临时 bare remote/repository fixture，仅访问临时目录。
  - 覆盖有效远端覆盖本地、无效远端不覆盖本地、临时文件清理。
  - 测试 SQLite helper 显式关闭连接，兼容 Windows 文件锁。

## TDD 红绿
- RED：新增 `TestPullRemoteDatabase` 后运行指定测试，功能尚未实现，测试未通过。
- GREEN：实现最小 fetch、blob 导出、quick_check、原子替换后，拉取测试通过。
- 回归测试随后全部通过。

## Commit
待提交：`feat(data-sync): 启动时原子载入远端数据库`

## 测试命令与结果
- `cd mimo-token-monitor && python -m unittest tests.test_data_sync.TestPullRemoteDatabase -v` — PASS（2 tests）
- `cd mimo-token-monitor && python -m unittest tests.test_data_sync -v` — PASS（16 tests）
- `cd mimo-token-monitor && python -m unittest tests.test_config -v` — PASS（7 tests）
- `git diff --check` — PASS

## 自审 / concerns
- 未实现退出提交、push 或 Qt 集成，严格限定在 Task 3。
- 测试 fixture 使用临时 bare Git remote，不访问真实数据目录或 GitHub。
- Windows 下 SQLite 连接必须显式 close；生产校验与测试 helper 均已处理。
