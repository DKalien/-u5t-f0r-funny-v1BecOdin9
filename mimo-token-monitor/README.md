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
- **系统托盘**：最小化到托盘，双击恢复，实时 tooltip
- **单实例运行**：防止重复启动，重复运行时提示检查托盘
- **Claude HUD 集成**：生成快照文件供 claude-hud 读取显示

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
python -m PyInstaller --onefile --noconsole --name "MiMo-Token-Monitor" --icon=icon.ico --add-data "icon.ico;." --hidden-import=PyQt6 --hidden-import=PyQt6.QtWidgets --hidden-import=PyQt6.QtCore --hidden-import=PyQt6.QtGui --hidden-import=PyQt6.sip main.py ; Remove-Item -Recurse -Force build\ ; Remove-Item -Force MiMo-Token-Monitor.spec ; Stop-Process -Name "MiMo-Token-Monitor" -Force -ErrorAction SilentlyContinue ; Copy-Item dist\MiMo-Token-Monitor.exe ([Environment]::GetFolderPath('Desktop')) -Force
```

## 操作

- **拖动**：左键拖动窗口位置（除最小化按钮区域外）
- **双击悬浮窗**：立即刷新数据
- **右键悬浮窗**：刷新 / 设置 / 查看原始数据 / 退出
- **悬停悬浮窗**：显示详细 tooltip
- **最小化按钮**：右上角 `─` 按钮，点击最小化到系统托盘
- **系统托盘**：
  - 双击托盘图标：恢复显示悬浮窗
  - 右键托盘图标：显示主窗口 / 刷新 / 退出
  - 悬停托盘图标：显示用量概览

## 技术栈

- Python + PyQt6
- 直接调用小米平台 REST API（`/api/v1/tokenPlan/usage`）
- Cookie 认证，数据纯本地存储

## Claude HUD 集成

本程序可以为 [claude-hud](https://github.com/DKalien/claude-hud) 生成用量快照，在 Claude Code 状态栏显示 MIMO 用量。

### 配置步骤

1. 在本程序的设置中，填写**快照路径**：
   ```
   ~/.claude/plugins/claude-hud/mimo-snapshot.json
   ```
   Windows 完整路径示例：`C:\Users\你的用户名\.claude\plugins\claude-hud\mimo-snapshot.json`

2. 在 claude-hud 配置中启用 MIMO 显示（`~/.claude/plugins/claude-hud/config.json`）：
   ```json
   {
     "display": {
       "showMimoUsage": true,
       "mimoSnapshotPath": "~/.claude/plugins/claude-hud/mimo-snapshot.json"
     }
   }
   ```

3. 重启 Claude Code，HUD 会显示：
   ```
   Context ███░░░░░░░ 29% │ Usage ██░░░░░░░░ 25% │ MIMO ██░░░░░░░░ 2% │ 1.9B / 82.0B
   ```

### 工作原理

- 本程序每次刷新数据时，自动写入快照 JSON 文件
- claude-hud 每 ~300ms 读取快照并显示
- 本程序关闭后，快照不再更新，claude-hud 在快照过期后停止显示 MIMO

## 隐私

- 纯本地运行，无第三方服务器
- Cookie 明文存储在 `~/.mimo-widget/config.json`
- 所有请求仅发往 `platform.xiaomimimo.com`
