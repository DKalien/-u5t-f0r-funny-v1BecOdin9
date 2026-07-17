# MiMo Token Monitor

小米 MiMo API Token 用量实时监控桌面悬浮窗。

## 功能

- 桌面悬浮窗实时显示 Token Plan 用量（已用/总额度/剩余）
- **今日已用额度显示**：自动计算并显示当天使用量及折合金额
- 根据套餐总额自动匹配挡位（Lite/Standard/Pro/Max），显示已用额度折合金额
- 余额为 0 时自动隐藏余额显示
- **一键导入 Cookie**：通过 CDP 自动从浏览器读取，无需手动复制
- 自动读取小米平台 API 获取真实数据
- 进度条颜色随用量变化（绿→黄→红）
- 根据消耗速率估算剩余可用天数
- 支持按量付费用量查询
- 可拖动、半透明、置顶显示
- **系统托盘**：最小化到托盘，双击恢复，实时 tooltip
- **单实例运行**：防止重复启动，重复运行时提示检查托盘
- **Claude HUD 集成**：生成快照文件供 claude-hud 读取显示

## 使用方式

### 方式一：VBS 启动器（推荐，无控制台窗口）

双击 `MiMo-Token-Monitor.vbs`，程序会在后台运行，不会显示黑色控制台窗口。

**修改代码后无需任何操作，直接双击 .vbs 即可生效！**

### 方式二：批处理启动器（调试用）

双击 `MiMo-Token-Monitor.bat`，会显示控制台窗口，可以看到输出信息（调试时有用）。

### 方式三：直接运行源码

```bash
# 安装依赖（首次运行）
pip install -r requirements.txt

# 运行
python main.py
```

首次运行需要填入 Cookie。

**方式一：一键导入（推荐）**

1. 在 Edge 快捷方式「属性 → 目标」末尾添加（注意前面有空格）：
   ```
   --remote-debugging-port=9222 --remote-allow-origins=*
   ```
2. 关闭所有 Edge 窗口，从该快捷方式重新打开
3. 浏览器打开 [platform.xiaomimimo.com](https://platform.xiaomimimo.com) 并登录
4. 在程序设置中点击「从浏览器导入」按钮，自动读取并验证

**方式二：手动复制**

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
python -m PyInstaller MiMo-Token-Monitor.spec --clean
```

## 操作

- **拖动**：左键拖动窗口位置（除最小化按钮区域外）
- **双击悬浮窗**：立即刷新数据
- **右键悬浮窗**：刷新 / 从浏览器导入 / 设置 / 查看原始数据 / 退出
- **悬停悬浮窗**：显示详细 tooltip
- **最小化按钮**：右上角 `─` 按钮，点击最小化到系统托盘
- **系统托盘**：
  - 双击托盘图标：恢复显示悬浮窗
  - 右键托盘图标：显示主窗口 / 刷新 / 从浏览器导入 / 退出
  - 悬停托盘图标：显示用量概览

## 技术栈

- Python + PyQt6
- 直接调用小米平台 REST API（`/api/v1/tokenPlan/usage`）
- Cookie 认证，支持 CDP（Chrome DevTools Protocol）自动导入
- 数据纯本地存储

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
