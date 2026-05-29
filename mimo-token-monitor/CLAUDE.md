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

# HUD 同步（已集成到 claude-hud 插件，自动触发；也可手动运行）
python mimo_hud_sync.py

# 打包 exe（自动清理临时文件）
pip install pyinstaller
python -m PyInstaller --onefile --noconsole --name "MiMo-Token-Monitor" --icon=icon.ico --hidden-import=PyQt6 --hidden-import=PyQt6.QtWidgets --hidden-import=PyQt6.QtCore --hidden-import=PyQt6.QtGui --hidden-import=PyQt6.sip main.py ; Remove-Item -Recurse -Force build\ ; Remove-Item -Force MiMo-Token-Monitor.spec
```

无测试、无 lint 配置。

## 架构

5 个 Python 模块 + 2 个工具脚本 + 1 个补丁目录，单目录扁平结构：

- **main.py** — 入口。创建 `QApplication`，加载配置，首次运行无 Cookie 时弹出 `SettingsDialog`，然后启动 `TokenWidget`。
- **config.py** — 配置管理。JSON 文件存储于 `~/.mimo-widget/config.json`，字段：`cookie`、`refresh_interval`（默认 300s）、`opacity`（默认 0.85）、`position`。
- **api_client.py** — API 客户端。`fetch_balance()` 和 `fetch_usage()` 两个函数。`fetch_usage()` 会依次尝试多个 endpoint 直到成功。
- **widget.py** — 全部 UI 代码。`FetchWorker(QThread)` 后台线程发请求，`SettingsDialog` 设置表单，`TokenWidget` 主悬浮窗（自定义 `paintEvent`、拖动+边缘吸附、右键菜单、定时刷新、数据解析、tooltip）。
- **mimo_hud_sync.py** — claude-hud 集成脚本。复用 `api_client.py` 获取 MiMo 数据，写入 `~/.mimo-widget/hud-usage-snapshot.json`（claude-hud 的 external usage snapshot 格式）。已通过 `externalSyncCmd` 配置集成到 claude-hud 插件，HUD 渲染时自动触发同步（snapshot 过期时才执行）。
- **setup_hud.py** — 一键安装脚本。自动定位 claude-hud cache 目录、复制补丁文件、更新配置，用于在新机器上部署 HUD 集成。
- **hud-patches/** — claude-hud 编译补丁（5 个 JS 文件）。修改了 external usage 的过期检查、进度条渲染、自动同步触发逻辑。claude-hud 更新后需重新运行 `setup_hud.py`。详见 `HUD_SETUP.md`。

数据流：`main.py` → `config.py` 加载配置 → `TokenWidget` 通过 `QTimer` 定时触发 → `FetchWorker` 在子线程调用 `api_client` → 信号回传 → `_parse_plan()` 解析 → `paintEvent()` 绘制。

## 关键实现细节

- UI 全部通过 `QPainter` 自定义绘制，不使用 QSS 样式表或 Qt Designer。
- 窗口 `FramelessWindowHint` + `WindowStaysOnTopHint`，通过 `mouseMoveEvent` 实现拖动，拖动时有屏幕边缘吸附逻辑。
- API 认证依赖用户手动从浏览器 DevTools 复制 Cookie。
- 平台目标为 Windows（字体 `Microsoft YaHei`，`.ico` 图标）。
- 内置 `PLAN_TIERS` 常量（4 个挡位：Lite ¥39 / Standard ¥99 / Pro ¥329 / Max ¥659），通过 `_get_plan_tier_info()` 根据套餐总额自动匹配挡位并计算每 Credit 单价，在悬浮窗内显示已用额度折合金额。
- 当余额为 0 时，悬浮窗自动隐藏余额显示（包括右上角金额和 tooltip 中的余额行）。
