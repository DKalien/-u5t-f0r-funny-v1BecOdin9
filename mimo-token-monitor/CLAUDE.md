# CLAUDE.md

This file provides guidance to coding agents when working with code in this repository.

## 项目概述

MiMo Token Monitor — 小米 MiMo API Token 用量桌面悬浮窗监控工具。Python + PyQt6 桌面应用，通过浏览器 Cookie 认证调用 `platform.xiaomimimo.com` REST API，实时显示 Token 用量、余额和消耗速率。

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

- **main.py** — 入口。单实例检查（Windows Mutex），创建 `QApplication`，加载配置，首次运行无 Cookie 时弹出 `SettingsDialog`，然后启动 `TokenWidget`。
- **config.py** — 配置管理。默认存储于外置 SQLite `D:\python\data\mimo-token-monitor\settings.db`，可由 `MIMO_TOKEN_MONITOR_DATA_DIR` 覆盖；首次运行会从旧的 `~/.mimo-widget/config.json` 迁移，外置库不可用时保留 JSON 回退。字段：`cookie`、`refresh_interval`（默认 300s）、`opacity`（默认 0.85）、`position`、`always_on_top`（默认 true）、`daily_baseline_date`（今日基准日期）、`daily_baseline_usage`（今日基准用量）。
- **api_client.py** — API 客户端。`fetch_balance()` 和 `fetch_usage()` 两个函数。`fetch_usage()` 会依次尝试多个 endpoint 直到成功。
- **cookie_reader.py** — 浏览器 Cookie 自动读取。优先通过 CDP 从运行中的浏览器读取明文 Cookie（绕过 v20 加密），回退到 `browser_cookie3` 读取本地数据库。设置对话框「从浏览器导入」按钮调用此模块。
- **widget.py** — 全部 UI 代码。`FetchWorker(QThread)` 后台线程发请求，`SettingsDialog` 设置表单，`TokenWidget` 主悬浮窗（自定义 `paintEvent`、拖动+边缘吸附、跨屏跨窗口吸附、标题栏置顶按钮、右键菜单、定时刷新、数据解析、tooltip、系统托盘）。悬浮窗和托盘右键菜单均支持「从浏览器导入」快速导入 Cookie（自动保存并刷新）。
- **window_snap.py** — Win32 顶层窗口枚举、窗口标题筛选、Qt 逻辑坐标与 Win32 物理像素坐标转换，以及跨屏跨窗口边框吸附的纯几何计算。
- **snapshot_writer.py** — 快照写入。为 claude-hud 生成用量快照 JSON 文件，包含余额、用量、今日用量等信息。
- **data_sync.py** — 以 Git plumbing、临时 index 和 SQLite 校验同步唯一目标 `mimo-token-monitor/settings.db`，不得操作共享仓库其他路径。
- **sync_runtime.py** — 用 QThread 编排启动拉取与真正退出推送，保证 Git 网络操作不阻塞 Qt 主线程。

数据流：`main.py` → `data_sync.py` 在程序启动时、窗口显示前拉取 → `config.py` 加载配置 → `TokenWidget` 通过 `QTimer` 定时触发 → `FetchWorker` 在子线程调用 `api_client` → 信号回传 → `_parse_plan()` 解析 → `paintEvent()` 绘制；真正退出时由 `sync_runtime.py` 在后台推送设置。

## 关键实现细节

- UI 全部通过 `QPainter` 自定义绘制，不使用 QSS 样式表或 Qt Designer。
- 窗口使用 `FramelessWindowHint` + `Tool`，默认附加 `WindowStaysOnTopHint`；标题栏图钉按钮可切换置顶状态并保存到 `always_on_top`。通过 `mouseMoveEvent` 实现拖动，拖动时有屏幕边缘吸附逻辑。拖动时设置 `WA_NoSystemBackground` 防止 Windows DWM 残留阴影导致闪烁。
- 跨窗口吸附在主屏和副屏均识别原生标题为 `ETF Tracker` 或 `MiMo Token Monitor` 的可见、非最小化窗口，阈值为 15 个 Qt 逻辑像素。`GetWindowRect()` 返回物理像素；拖动时必须按当前 `QScreen.geometry()` 原点和 `devicePixelRatio` 转换后再计算，并将最终坐标转换回 Qt 逻辑坐标。
- API 认证依赖 Cookie。支持通过 CDP 自动导入（需 Edge 快捷方式添加调试参数，见 README）或手动从 DevTools 复制。
- 平台目标为 Windows（字体 `Microsoft YaHei`，`.ico` 图标）。
- 内置 `PLAN_TIERS` 常量（4 个挡位：Lite ¥39 / Standard ¥99 / Pro ¥329 / Max ¥659），通过 `_get_plan_tier_info()` 根据套餐总额自动匹配挡位并计算每 Credit 单价，在悬浮窗内显示已用额度折合金额。
- 当余额为 0 时，悬浮窗自动隐藏余额显示（包括右上角金额和 tooltip 中的余额行）。
- 系统托盘：`QSystemTrayIcon` 实现最小化到托盘，右上角绘制最小化按钮（`─`），双击托盘图标恢复窗口，托盘 tooltip 与悬浮窗同步更新。
- 单实例：使用 Windows Mutex 防止重复启动，并通过命名事件唤醒已有窗口；禁止把第二次启动保留在阻塞提示框中。
- 进度条填充圆角动态调整：当填充宽度较小时，圆角半径限制为 `min(4, fill_w//2)`，避免超出外框圆角范围。
- 今日用量计算：通过记录每天首次刷新时的月累计用量作为基准，后续刷新时计算差值得到今日用量。基准值持久化存储，程序重启不丢失数据。
- 日常启动器：`launcher.py` 会被打包成轻量的 `dist/MiMo-Token-Monitor.exe`，从项目根目录或 `dist` 启动时读取项目中的 `main.py`。业务源码变更后只需重启启动器；只有修改启动器时才运行 `./build-launcher.ps1`。完整独立发行版仍使用 `MiMo-Token-Monitor.spec`，轻量构建不得覆盖该 spec。
- **红线规则**：PyQt6 在 Windows 上使用浮点数坐标调用 `QPainter.drawLine()` 会导致崩溃（退出码 `0xC0000409`），所有 UI 坐标必须使用整数。
- 设置 Git 同步操作的每次启动拉取或真正退出推送共享一个总 operation deadline，默认 30 秒，而不是每条 Git 命令各自 30 秒；同步只允许操作 `mimo-token-monitor/settings.db`；禁止用会修改共享工作树的 `git pull`、`checkout`、`reset`、`clean` 或普通 `git commit` 替代 plumbing 实现。
- 同步配置环境变量：`MIMO_TOKEN_MONITOR_DATA_DIR`（默认 `D:\python\data\mimo-token-monitor`）、`MIMO_TOKEN_MONITOR_GIT_REMOTE`（`origin`）、`MIMO_TOKEN_MONITOR_GIT_BRANCH`（`main`）、`MIMO_TOKEN_MONITOR_GIT_TIMEOUT_SECONDS`（`30` 秒总预算）、`MIMO_TOKEN_MONITOR_GIT_PUSH_RETRIES`（最多 `3` 次 push 尝试）。
