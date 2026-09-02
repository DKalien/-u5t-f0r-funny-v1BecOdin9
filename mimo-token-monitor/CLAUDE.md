# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) and other coding agents when working with code in this repository.

## 项目概述

MiMo Token Monitor — MiMo Token Plan、WLB 与 GPT 5 小时/周限额用量桌面悬浮窗。Python + PyQt6 桌面应用，通过浏览器 Cookie 认证调用 `platform.xiaomimimo.com` REST API，并提供 Codex Router 日常维护入口。

## 常用命令

```bash
# 安装依赖
pip install -r requirements.txt

# 运行
python main.py

# 运行全部 unittest
python -m unittest discover -s tests -v

# 打包 exe（使用 spec 文件，自动包含 icon.ico）
pip install pyinstaller
python -m PyInstaller MiMo-Token-Monitor.spec --clean
```

## 架构

Python 模块采用单目录扁平结构：

- **main.py** — 入口。单实例检查（Windows Mutex），创建 `QApplication`，加载配置，首次运行无 Cookie 时弹出 `SettingsDialog`，然后启动 `TokenWidget`；重启请求在事件循环结束并释放单实例锁后拉起新进程。
- **config.py** — 配置管理。默认存储于本地外置 SQLite `D:\python\data\mimo-token-monitor\settings.db`，可由 `MIMO_TOKEN_MONITOR_DATA_DIR` 覆盖，不通过 Git 同步；首次运行会从旧的 `~/.mimo-widget/config.json` 迁移，外置库不可用时保留 JSON 回退。主要字段包括 MiMo Cookie、刷新与显示设置、WLB 配置、GPT Session Cookie 和当前显示模式；完整默认值以 `config.py::DEFAULT_CONFIG` 为准。
- **api_client.py** — API 客户端。包含 MiMo、WLB 与 GPT 5 小时/周限额查询；WLB 从 `/v1/usage` 的 `rate_limits` 精确读取 `7d` 窗口并保留既有扁平字段，同时从同一响应附加 `daily` 的 `1d` 窗口（缺少 `1d` 只显示无数据，不影响 `7d` 成功），供总览和详情显示；当日、周窗口同时返回时，总览中的日使用百分比以日、周两者剩余量的较小值计算，原始日/周 `limit` 字段保持不变；GPT 查询依次尝试本机 Codex 登录、ChatGPT Session Cookie 和本地会话记录，并对网络、429、5xx 瞬时失败重试一次。
- **cookie_reader.py** — 浏览器 Cookie 自动读取。优先通过 CDP 从运行中的浏览器读取明文 Cookie（绕过 v20 加密），回退到 `browser_cookie3` 读取本地数据库。设置对话框「从浏览器导入」按钮调用此模块。
- **playwright_session.py** — 可选的独立 Playwright 持久化登录会话。定时刷新和过期恢复默认使用 headless Chromium 读取最新 Cookie；需要首次登录或验证码时由用户从菜单手动续期，不使用现有 Chrome/Edge User Data。
- **widget.py** — 全部 UI 代码。`FetchWorker(QThread)` 后台线程发请求，`SettingsDialog` 设置表单，`TokenWidget` 主悬浮窗（自定义 `paintEvent`、拖动+边缘吸附、跨屏跨窗口吸附、标题栏置顶按钮、右键菜单、定时刷新、数据解析、tooltip、系统托盘）。悬浮窗和托盘右键菜单均支持「从浏览器导入」快速导入 Cookie（自动保存并刷新）；托盘还通过后台线程显示 Codex Router 当前开关状态并提供元数据更新、启停和重启入口，以及重启悬浮窗入口。
- **router_control.py** — Codex Router 托盘操作边界。优先从安装清单读取源码目录，可由 `MIMO_TOKEN_MONITOR_ROUTER_ROOT` 覆盖；状态读取复用 Router 的 `config-manager.mjs status`，其余操作只串行调用路由器现有脚本，并在首个失败处停止。
- **window_snap.py** — Win32 顶层窗口枚举、窗口标题筛选、Qt 逻辑坐标与 Win32 物理像素坐标转换，以及跨屏跨窗口边框吸附的纯几何计算。
- **snapshot_writer.py** — 快照写入。为 claude-hud 生成用量快照 JSON 文件，包含余额、用量、今日用量等信息。
- **code_sync.py** — 检查并同步代码项目 `mimo-token-monitor/`；当前运行态只由轻量启动器在启动前调用，并且只允许干净工作区快进。模块保留独立的源码推送能力，但程序退出流程不调用它。

数据流：轻量启动器路径为 `launcher.py` → `code_sync.py` 检查并快进拉取代码 → 启动 `main.py`；直接运行 `main.py` 会跳过源码同步。进入应用后，`config.py` 加载本地配置 → `TokenWidget` 通过 `QTimer` 定时触发 → `FetchWorker` 在子线程调用 `api_client` → 信号回传 → `_parse_plan()` 解析 → `paintEvent()` 绘制；退出或重启悬浮窗不执行数据库 Git 操作，重启会在旧进程释放单实例锁后启动新进程。托盘路由操作走 `TokenWidget` → `RouterWorker` → `router_control.py` → Codex Router 现有脚本，不进入用量查询或设置持久化链路。

## 关键实现细节

- UI 全部通过 `QPainter` 自定义绘制，不使用 QSS 样式表或 Qt Designer。
- 窗口使用 `FramelessWindowHint` + `Tool`，默认附加 `WindowStaysOnTopHint`；标题栏图钉按钮可切换置顶状态并保存到 `always_on_top`。通过 `mouseMoveEvent` 实现拖动，拖动时有屏幕边缘吸附逻辑。拖动时设置 `WA_NoSystemBackground` 防止 Windows DWM 残留阴影导致闪烁。
- 跨窗口吸附在主屏和副屏均识别原生标题为 `ETF Tracker` 或 `MiMo Token Monitor` 的可见、非最小化窗口，阈值为 15 个 Qt 逻辑像素。`GetWindowRect()` 返回物理像素；拖动时必须按当前 `QScreen.geometry()` 原点和 `devicePixelRatio` 转换后再计算，并将最终坐标转换回 Qt 逻辑坐标。
- API 认证依赖 Cookie。支持通过 CDP 自动导入（需 Edge 快捷方式添加调试参数，见 README）、Playwright 独立会话自动续期，或手动从 DevTools 复制。
- 平台目标为 Windows（字体 `Microsoft YaHei`，`.ico` 图标）。
- 内置 `PLAN_TIERS` 常量（4 个挡位：Lite ¥39 / Standard ¥99 / Pro ¥329 / Max ¥659），通过 `_get_plan_tier_info()` 根据套餐总额自动匹配挡位并计算每 Credit 单价，在悬浮窗内显示已用额度折合金额。
- 当余额为 0 时，悬浮窗自动隐藏余额显示（包括右上角金额和 tooltip 中的余额行）。
- 系统托盘：`QSystemTrayIcon` 实现最小化到托盘，右上角绘制最小化按钮（`─`），双击托盘图标恢复窗口，托盘 tooltip 与悬浮窗同步更新；“重启悬浮窗”直接退出当前进程并在释放单实例锁后启动新进程。
- Codex Router 维护：`router_control.py` 优先使用 `MIMO_TOKEN_MONITOR_ROUTER_ROOT`，否则按 `$CODEX_HOME`（默认 `~/.codex`）读取 `codex-router/install-manifest.json` 的 `current.sourceRoot`，并校验所需入口文件。状态调用 `node src/config-manager.mjs status`；元数据更新依次运行 `node src/catalog.mjs` 和 `node src/service.mjs restart`；启停复用 `codex-router.ps1 enable|disable`；重启调用 `node src/service.mjs restart`。全部命令由 `RouterWorker` 后台串行执行，首个失败即停止，操作期间禁用路由菜单并阻止退出；托盘菜单关闭后的状态刷新延迟到事件循环下一轮，避免覆盖刚触发的操作。
- 路由操作开始时，悬浮窗底部显示进行中状态，完成后显示成功或失败摘要并在 5 秒后清除；托盘通知仍保留，用于即时反馈。
- 单实例：使用 Windows Mutex 防止重复启动，并通过命名事件唤醒已有窗口；禁止把第二次启动保留在阻塞提示框中。
- 进度条填充圆角动态调整：当填充宽度较小时，圆角半径限制为 `min(4, fill_w//2)`，避免超出外框圆角范围。
- 今日用量计算：通过记录每天首次刷新时的月累计用量作为基准，后续刷新时计算差值得到今日用量。基准值持久化存储，程序重启不丢失数据。
- 总览使用 9pt 单行双栏标签：Token Plan 标题显示剩余天数（无效日期不加后缀）；WLB 左侧显示 `WLB - HH:MM`（缺失时为 `WLB - --:--`），右侧只显示周重置剩余天数；GPT 左侧显示 `GPT - HH:MM`（缺失时为 `GPT - --:--`）和右对齐百分比，右侧只显示周重置剩余天数（缺失时为 `--天`）。WLB 日/周限额、GPT 5 小时/周限额均使用左右分段进度条，百分比右对齐且标签自动省略避免重叠。刷新失败时保留上次成功数据，界面与 tooltip 会标明数据已过期，并展示本机 Codex 登录、ChatGPT Cookie、本地会话三路的具体失败原因。
- 日常启动器：`launcher.py` 会被打包成轻量的 `dist/MiMo-Token-Monitor.exe`，从项目根目录或 `dist` 启动时读取项目中的 `main.py`。业务源码变更后只需重启启动器；只有修改启动器时才运行 `./build-launcher.ps1`。完整独立发行版仍使用 `MiMo-Token-Monitor.spec`，轻量构建不得覆盖该 spec。
- 代码同步：启动器先在代码仓库干净且本地分支落后时执行 `fetch` + `merge --ff-only`，不会为了拉取而覆盖本地修改；直接运行 `main.py` 以及退出/重启流程都不会自动提交或推送源码。
- **红线规则**：PyQt6 在 Windows 上使用浮点数坐标调用 `QPainter.drawLine()` 会导致崩溃（退出码 `0xC0000409`）；所有 `drawLine()` 坐标必须使用整数，`QRectF`/`QPointF` 等其他 Qt 几何 API 不受此约束。
- 本地设置目录环境变量：`MIMO_TOKEN_MONITOR_DATA_DIR`（默认 `D:\python\data\mimo-token-monitor`）；设置数据库不得通过 Git 拉取、提交或推送。
- 代码同步配置环境变量：`MIMO_TOKEN_MONITOR_CODE_REPO_ROOT`、`MIMO_TOKEN_MONITOR_CODE_PROJECT_PATH`、`MIMO_TOKEN_MONITOR_CODE_GIT_REMOTE`、`MIMO_TOKEN_MONITOR_CODE_GIT_BRANCH`、`MIMO_TOKEN_MONITOR_CODE_GIT_TIMEOUT_SECONDS`、`MIMO_TOKEN_MONITOR_CODE_SYNC_ENABLED`。
