# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

MiMo Token Monitor — 小米 MiMo API Token 用量桌面悬浮窗监控工具。Python + PyQt6 桌面应用，通过浏览器 Cookie 认证调用 `platform.xiaomimimo.com` REST API，实时显示 Token 用量、余额和消耗速率。

## 常用命令

```bash
# 安装依赖
pip install -r requirements.txt

# 运行
python main.py

# 打包 exe（使用 spec 文件，自动包含 icon.ico）
pip install pyinstaller
python -m PyInstaller MiMo-Token-Monitor.spec --clean
```

无测试、无 lint 配置。

## 架构

6 个 Python 模块，单目录扁平结构：

- **main.py** — 入口。单实例检查（Windows Mutex），创建 `QApplication`，加载配置，首次运行无 Cookie 时弹出 `SettingsDialog`，然后启动 `TokenWidget`。
- **config.py** — 配置管理。JSON 文件存储于 `~/.mimo-widget/config.json`，字段：`cookie`、`refresh_interval`（默认 300s）、`opacity`（默认 0.85）、`position`、`daily_baseline_date`（今日基准日期）、`daily_baseline_usage`（今日基准用量）。
- **api_client.py** — API 客户端。`fetch_balance()` 和 `fetch_usage()` 两个函数。`fetch_usage()` 会依次尝试多个 endpoint 直到成功。
- **cookie_reader.py** — 浏览器 Cookie 自动读取。优先通过 CDP 从运行中的浏览器读取明文 Cookie（绕过 v20 加密），回退到 `browser_cookie3` 读取本地数据库。设置对话框「从浏览器导入」按钮调用此模块。
- **widget.py** — 全部 UI 代码。`FetchWorker(QThread)` 后台线程发请求，`SettingsDialog` 设置表单，`TokenWidget` 主悬浮窗（自定义 `paintEvent`、拖动+边缘吸附、右键菜单、定时刷新、数据解析、tooltip、系统托盘）。悬浮窗和托盘右键菜单均支持「从浏览器导入」快速导入 Cookie（自动保存并刷新）。
- **snapshot_writer.py** — 快照写入。为 claude-hud 生成用量快照 JSON 文件，包含余额、用量、今日用量等信息。

数据流：`main.py` → `config.py` 加载配置 → `TokenWidget` 通过 `QTimer` 定时触发 → `FetchWorker` 在子线程调用 `api_client` → 信号回传 → `_parse_plan()` 解析 → `paintEvent()` 绘制。

## 关键实现细节

- UI 全部通过 `QPainter` 自定义绘制，不使用 QSS 样式表或 Qt Designer。
- 窗口 `FramelessWindowHint` + `WindowStaysOnTopHint`，通过 `mouseMoveEvent` 实现拖动，拖动时有屏幕边缘吸附逻辑。拖动时设置 `WA_NoSystemBackground` 防止 Windows DWM 残留阴影导致闪烁。
- API 认证依赖 Cookie。支持通过 CDP 自动导入（需 Edge 快捷方式添加调试参数，见 README）或手动从 DevTools 复制。
- 平台目标为 Windows（字体 `Microsoft YaHei`，`.ico` 图标）。
- 内置 `PLAN_TIERS` 常量（4 个挡位：Lite ¥39 / Standard ¥99 / Pro ¥329 / Max ¥659），通过 `_get_plan_tier_info()` 根据套餐总额自动匹配挡位并计算每 Credit 单价，在悬浮窗内显示已用额度折合金额。
- 当余额为 0 时，悬浮窗自动隐藏余额显示（包括右上角金额和 tooltip 中的余额行）。
- 系统托盘：`QSystemTrayIcon` 实现最小化到托盘，右上角绘制最小化按钮（`─`），双击托盘图标恢复窗口，托盘 tooltip 与悬浮窗同步更新。
- 单实例：使用 Windows Mutex 防止重复启动，重复运行时提示"程序已在运行中"。
- 进度条填充圆角动态调整：当填充宽度较小时，圆角半径限制为 `min(4, fill_w//2)`，避免超出外框圆角范围。
- 今日用量计算：通过记录每天首次刷新时的月累计用量作为基准，后续刷新时计算差值得到今日用量。基准值持久化存储，程序重启不丢失数据。
- 脚本启动器：使用 VBS 脚本启动器（MiMo-Token-Monitor.vbs）运行项目，无需打包。修改源代码文件后直接运行即可，完全无需重新打包。
- **红线规则**：PyQt6 在 Windows 上使用浮点数坐标调用 `QPainter.drawLine()` 会导致崩溃（退出码 `0xC0000409`），所有 UI 坐标必须使用整数。
