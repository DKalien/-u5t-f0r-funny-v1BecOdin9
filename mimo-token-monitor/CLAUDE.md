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

# 打包 exe（自动清理临时文件并复制到桌面）
pip install pyinstaller
python -m PyInstaller --onefile --noconsole --name "MiMo-Token-Monitor" --icon=icon.ico --add-data "icon.ico;." --hidden-import=PyQt6 --hidden-import=PyQt6.QtWidgets --hidden-import=PyQt6.QtCore --hidden-import=PyQt6.QtGui --hidden-import=PyQt6.sip main.py ; Remove-Item -Recurse -Force build\ ; Remove-Item -Force MiMo-Token-Monitor.spec ; Stop-Process -Name "MiMo-Token-Monitor" -Force -ErrorAction SilentlyContinue ; Copy-Item dist\MiMo-Token-Monitor.exe ([Environment]::GetFolderPath('Desktop')) -Force
```

无测试、无 lint 配置。

## 架构

4 个 Python 模块，单目录扁平结构：

- **main.py** — 入口。单实例检查（Windows Mutex），创建 `QApplication`，加载配置，首次运行无 Cookie 时弹出 `SettingsDialog`，然后启动 `TokenWidget`。
- **config.py** — 配置管理。JSON 文件存储于 `~/.mimo-widget/config.json`，字段：`cookie`、`refresh_interval`（默认 300s）、`opacity`（默认 0.85）、`position`。
- **api_client.py** — API 客户端。`fetch_balance()` 和 `fetch_usage()` 两个函数。`fetch_usage()` 会依次尝试多个 endpoint 直到成功。
- **widget.py** — 全部 UI 代码。`FetchWorker(QThread)` 后台线程发请求，`SettingsDialog` 设置表单，`TokenWidget` 主悬浮窗（自定义 `paintEvent`、拖动+边缘吸附、右键菜单、定时刷新、数据解析、tooltip、系统托盘）。

数据流：`main.py` → `config.py` 加载配置 → `TokenWidget` 通过 `QTimer` 定时触发 → `FetchWorker` 在子线程调用 `api_client` → 信号回传 → `_parse_plan()` 解析 → `paintEvent()` 绘制。

## 关键实现细节

- UI 全部通过 `QPainter` 自定义绘制，不使用 QSS 样式表或 Qt Designer。
- 窗口 `FramelessWindowHint` + `WindowStaysOnTopHint`，通过 `mouseMoveEvent` 实现拖动，拖动时有屏幕边缘吸附逻辑。
- API 认证依赖用户手动从浏览器 DevTools 复制 Cookie。
- 平台目标为 Windows（字体 `Microsoft YaHei`，`.ico` 图标）。
- 内置 `PLAN_TIERS` 常量（4 个挡位：Lite ¥39 / Standard ¥99 / Pro ¥329 / Max ¥659），通过 `_get_plan_tier_info()` 根据套餐总额自动匹配挡位并计算每 Credit 单价，在悬浮窗内显示已用额度折合金额。
- 当余额为 0 时，悬浮窗自动隐藏余额显示（包括右上角金额和 tooltip 中的余额行）。
- 系统托盘：`QSystemTrayIcon` 实现最小化到托盘，右上角绘制最小化按钮（`─`），双击托盘图标恢复窗口，托盘 tooltip 与悬浮窗同步更新。
- 单实例：使用 Windows Mutex 防止重复启动，重复运行时提示"程序已在运行中"。
