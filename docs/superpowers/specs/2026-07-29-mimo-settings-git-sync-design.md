# 悬浮窗 SQLite 数据 Git 同步设计

日期：2026-07-29

## 1. 背景与目标

MiMo Token Monitor 将悬浮窗配置保存在外置 SQLite 文件：

- 本地数据目录：`D:\python\data\mimo-token-monitor`
- 数据文件：`settings.db`
- Git 仓库根目录：`D:\python\data`
- 仓库相对路径：`mimo-token-monitor/settings.db`

`D:\python\data` 同一 Git 仓库还包含 `financial-data-backup` 等其他程序的数据。新功能必须同步 MiMo Token Monitor 的 `settings.db`，同时不得读取、暂存、覆盖、还原或删除其他程序目录中的文件。

目标行为：

1. 每次程序进程首次启动并显示悬浮窗前，从远端拉取 `settings.db`。
2. SQLite 是二进制文件，不进行内容合并；启动时远端版本优先。
3. 仅在用户执行真正的“退出”操作时提交并推送本地 `settings.db`；关闭窗口到托盘不触发推送。
4. 退出时若远端同时发生更新，本机退出时的数据优先覆盖远端目标文件，但保留远端其他路径的最新内容。
5. 启动同步失败时继续使用本地数据；退出同步失败时保留本地数据并正常退出。

## 2. 方案选择

采用 **Git plumbing 命令与临时 index**，不在共享工作树中执行普通 `git pull`、`git checkout`、`git reset`、`git clean` 或 `git commit`。

未采用的方案：

- 临时 clone + sparse-checkout：隔离性良好，但首次下载和缓存维护成本更高。
- 直接操作共享工作树：实现简单，但 pull、合并或 reset 可能影响其他程序目录，不满足隔离要求。

## 3. 架构

新增独立同步模块 `data_sync.py`，Git 交互、路径校验、SQLite 校验和同步结果封装均在该模块内完成。`main.py` 只负责在正确生命周期调用同步服务；`widget.py` 只负责触发真正退出流程和展示结果，不承载 Git 业务逻辑。

### 3.1 同步配置

同步配置包含：

- 数据仓库根目录
- 数据文件绝对路径
- 唯一允许的仓库相对路径 `mimo-token-monitor/settings.db`
- Git remote，默认 `origin`
- Git branch，默认 `main`
- Git 命令超时
- 推送竞争重试次数

默认数据目录继续由现有 `MIMO_TOKEN_MONITOR_DATA_DIR` 规则确定，仓库根目录固定从该数据目录的父目录推导。以下环境变量提供部署覆盖能力：

- `MIMO_TOKEN_MONITOR_GIT_REMOTE`：默认 `origin`
- `MIMO_TOKEN_MONITOR_GIT_BRANCH`：默认 `main`
- `MIMO_TOKEN_MONITOR_GIT_TIMEOUT_SECONDS`：默认 `30`
- `MIMO_TOKEN_MONITOR_GIT_PUSH_RETRIES`：默认 `3`

环境变量值无效时返回配置错误并跳过本次同步，不回退到可能被误解的值。

### 3.2 路径隔离

任何同步操作前必须完成以下校验：

1. `git rev-parse --show-toplevel` 的规范化结果必须等于配置的仓库根目录。
2. 本地目标文件解析后必须位于配置的数据目录内。
3. Git 相对路径必须严格等于 `mimo-token-monitor/settings.db`，禁止绝对路径、`..` 和其他目标。
4. 退出构造提交时使用独立临时 index，不使用共享工作树的 index。
5. 新提交以最新远端提交为父提交并继承其完整 tree，只替换目标 DB blob。

同步模块不得执行会批量修改共享工作树的命令。`financial-data-backup` 和其他兄弟目录中的本地修改、未跟踪文件及后台写入均不参与同步。

## 4. 启动同步流程

同步只在程序进程首次启动时执行一次，从托盘重新显示悬浮窗不重复同步。

流程：

1. 完成 Windows Mutex 单实例检查。重复启动只唤醒已运行实例并返回，不执行同步。
2. 创建 `QApplication`，但尚未加载配置或显示 `TokenWidget`。
3. 调用 `pull_remote_database()`：
   - 校验仓库和目标路径。
   - 执行 `git fetch <remote> <branch>`，仅更新远端引用，不修改工作树。
   - 使用 `git show <remote>/<branch>:mimo-token-monitor/settings.db` 将远端 blob 导出到同目录临时文件。
   - 确认文件具有 SQLite 格式并通过只读连接执行 `PRAGMA quick_check`。
   - 校验成功后用 `os.replace()` 原子覆盖本地 `settings.db`。
4. 无论同步成功或失败，随后才调用现有 `load_config()`。
5. 创建并显示悬浮窗。
6. 若同步失败，程序继续使用原有本地数据库，并在窗口可用后通过托盘通知或日志显示不含敏感内容的错误信息。

只有成功获取并验证的远端 DB 才能覆盖本地文件。网络失败、Git/SSH 认证失败、远端目标不存在、命令超时或数据库损坏均不得破坏本地 DB。

## 5. 退出同步流程

只有托盘菜单或悬浮窗右键菜单中的“退出”触发同步。窗口关闭事件继续只隐藏到托盘。

流程：

1. `_quit_app()` 发起异步退出同步，并避免重复触发退出流程。
2. 工作线程调用 `push_local_database()`：
   - 校验仓库和目标路径。
   - 执行 `git fetch <remote> <branch>` 获取最新远端提交。
   - 创建临时 index，并从最新远端提交读取完整 tree。
   - 将本地 `settings.db` 写成 Git blob，仅更新 index 中 `mimo-token-monitor/settings.db` 对应条目。
   - 若生成的 tree 与远端 tree 相同，返回 `no_change`，不创建提交。
   - 使用 `git write-tree` 和 `git commit-tree` 创建以最新远端为父提交的新提交。
   - 使用提交信息 `chore(mimo-token-monitor): 同步悬浮窗设置`。
   - 将新提交推送到远端分支。
3. 若 fetch 与 push 之间远端发生更新导致 non-fast-forward，重新 fetch，以新远端提交为父提交重建“仅替换目标 DB”的提交，再有限重试。
4. 重试成功、无变化或达到重试上限后清理临时 index。
5. 应用隐藏托盘并退出。

重建提交时始终继承最新远端完整 tree，因此本机 DB 可以覆盖远端目标文件，但远端其他目录的最新提交内容保持不变。不得使用覆盖整个远端分支的无条件 force push。

## 6. 并发与界面响应

Git 网络操作不得阻塞 Qt 主线程：

- 单实例检查通过后，创建一个无窗口的启动同步工作线程；在线程完成或 30 秒超时前不调用 `load_config()`、不创建 `TokenWidget`。
- 主线程通过 Qt 事件循环等待工作线程信号，因此 Windows 不会将进程判断为无响应；本功能不新增启动等待界面。
- 退出同步在工作线程执行，退出动作等待同步完成或 30 秒超时。
- 退出同步期间禁用重复退出触发，避免并发创建提交或重复调用 `QApplication.quit()`。
- 超时后终止 Git 子进程，清理临时资源，并按失败降级策略继续启动或退出。

## 7. 同步结果与错误处理

同步服务返回结构化结果：

- `success`：拉取覆盖或推送完成。
- `no_change`：本地 DB 与远端目标相同，无需提交。
- `skipped`：前置条件不满足，未执行同步。
- `failed`：Git、网络、认证、超时、SQLite 校验或推送失败。

结果包含同步阶段、面向用户的中文摘要和仅供日志使用的 Git stderr 摘要；stderr 最多保留末尾 2,000 个字符，并在展示前过滤 URL 中的用户名、密码和 token。任何结果都不得包含 Cookie 或数据库内容。

降级策略：

- 启动拉取失败：保留本地 DB 并继续启动。
- 远端 DB 校验失败：拒绝覆盖本地 DB。
- 退出提交或推送失败：保留本地 DB 并继续退出，下次启动仍可再次同步。
- 推送竞争：最多重新获取远端并重建提交 3 次；每次都从最新远端 tree 开始。
- Git 不存在、仓库根不匹配或远端未配置：返回明确失败或跳过结果，不影响应用本地功能。
- 临时文件与临时 index 在 `finally` 中清理。

## 8. 测试设计

新增同步模块测试，使用临时 Git 仓库、本地 bare remote 和临时工作目录；测试不得访问真实 `D:\python\data` 或 GitHub。

覆盖场景：

1. 启动时远端有效 DB 原子覆盖本地 DB。
2. 远端 DB 无效时本地 DB 保持不变。
3. fetch、远端缺失和超时失败时继续保留本地文件。
4. 退出提交只改变 `mimo-token-monitor/settings.db`。
5. 共享工作树另一目录存在未提交修改和未跟踪文件时完全不受影响。
6. 远端另一目录存在新提交时，退出同步提交保留其最新内容。
7. push 竞争时重新基于最新远端构造提交，并最终以本地 DB 覆盖目标路径。
8. DB 内容未变化时不创建提交。
9. 推送失败时本地 DB 保留。
10. 路径越界、仓库根不匹配和错误目标路径被拒绝。
11. 重复启动只唤醒旧实例，不重复拉取。
12. 关闭到托盘不推送，真正退出才推送。
13. 现有 `tests/test_config.py` 全部通过，SQLite 加载、迁移和 JSON fallback 行为不回归。

## 9. 文档更新

实现时同步更新 `README.md` 和 `AGENTS.md`，说明：

- 启动拉取和真正退出推送的时机。
- 启动远端优先、退出本机优先的二进制文件策略。
- 同步失败时的降级行为。
- 共享 Git 仓库中仅处理 `mimo-token-monitor/settings.db` 的隔离保证。
- 环境变量和 Git/SSH 前置要求。

## 10. 验收标准

1. 在网络正常且远端 DB 有效时，首次显示悬浮窗前加载远端配置。
2. 真正退出后，本地 DB 形成只修改目标路径的远端提交。
3. `financial-data-backup` 或其他兄弟目录的本地与远端内容均不因同步被覆盖、暂存、还原或删除。
4. 并发远端更新不会丢失其他路径的最新提交，目标 DB 最终以退出设备数据为准。
5. 网络、Git、认证或数据库校验失败不导致本地 DB 丢失，也不阻止应用启动或退出。
6. 新增和现有测试全部通过，文档与行为一致。
