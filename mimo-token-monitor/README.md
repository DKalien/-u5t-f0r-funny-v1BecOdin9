# MiMo Token Monitor

小米 MiMo API Token 用量实时监控桌面悬浮窗。

## 功能

- 桌面悬浮窗实时显示 Token Plan 用量（已用/总额度/剩余）
- 根据套餐总额自动匹配挡位（Lite/Standard/Pro/Max），显示已用额度折合金额
- 余额为 0 时自动隐藏余额显示
- 自动读取小米平台 API 获取真实数据
- 进度条颜色随用量变化（绿→黄→红）
- 根据消耗速率估算剩余可用天数
- 支持按量付费用量查询
- 可拖动、半透明、置顶显示

## 使用方式

### 直接运行 exe（推荐）

下载 `MiMo-Token-Monitor.exe`，双击运行。

首次运行需要填入 Cookie：
1. 浏览器打开 [platform.xiaomimimo.com](https://platform.xiaomimimo.com) 并登录
2. 按 F12 → Network → 刷新页面 → 点任意请求
3. 复制 Request Headers 中的 Cookie 值
4. 粘贴到设置中

### 从源码运行

```bash
pip install -r requirements.txt
python main.py
```

### 打包 exe

```bash
pip install pyinstaller
python -m PyInstaller --onefile --noconsole --name "MiMo-Token-Monitor" --icon=icon.ico --hidden-import=PyQt6 --hidden-import=PyQt6.QtWidgets --hidden-import=PyQt6.QtCore --hidden-import=PyQt6.QtGui --hidden-import=PyQt6.sip main.py ; Remove-Item -Recurse -Force build\ ; Remove-Item -Force MiMo-Token-Monitor.spec
```

## 操作

- **拖动**：左键拖动窗口位置
- **双击**：立即刷新数据
- **右键菜单**：刷新 / 设置 / 查看原始数据 / 退出
- **悬停**：显示详细 tooltip

## 技术栈

- Python + PyQt6
- 直接调用小米平台 REST API（`/api/v1/tokenPlan/usage`）
- Cookie 认证，数据纯本地存储

## claude-hud 集成

可将 MiMo 套餐用量（进度条 + 余额）显示在 Claude Code 的状态栏中。一键安装：

```bash
python setup_hud.py
```

详见 [HUD_SETUP.md](HUD_SETUP.md)。

## 隐私

- 纯本地运行，无第三方服务器
- Cookie 明文存储在 `~/.mimo-widget/config.json`
- 所有请求仅发往 `platform.xiaomimimo.com`
