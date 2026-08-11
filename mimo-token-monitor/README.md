# MiMo Token Monitor

MiMo Token Plan、WLB 与 GPT 周限额用量监控桌面悬浮窗。

## 功能

- 桌面悬浮窗实时显示 Token Plan 用量（已用/总额度/剩余）
- **今日已用额度显示**：自动计算并显示当天使用量及折合金额
- 根据套餐总额自动匹配挡位（Lite/Standard/Pro/Max），显示已用额度折合金额
- 余额为 0 时自动隐藏余额显示
- **一键导入 Cookie**：通过 CDP 自动从浏览器读取，无需手动复制
- 自动读取小米平台 API 获取真实数据
- 进度条颜色随用量变化（绿→黄→红）
- 支持手动填写套餐有效期并显示“有效期至”
- 支持按量付费用量查询
- 可拖动、半透明、置顶显示；标题栏图钉按钮可切换置顶状态，默认置顶并自动保存
- **跨窗口边框吸附**：可与 ETF Tracker 悬浮窗相互吸附，主屏和副屏均支持，并兼容不同 DPI 的显示器
- **系统托盘**：最小化到托盘，双击恢复，实时 tooltip，并可直接重启悬浮窗
- **Codex Router 维护**：从托盘查看当前路由开关状态、更新模型元数据，并开启、关闭或重启路由器
- **单实例运行**：防止重复启动；再次运行会自动恢复并置顶已有窗口
- **WLB 用量显示**：标题栏循环图标可切换 MiMo Token / WLB 模式，支持配置 WLB API（默认 http://codex.wlbclub.com），显示剩余百分比、已用百分比、进度条和状态
- **用量总览页面**：标题栏切换到“总览”后，并列显示 Token Plan、WLB 与 GPT 周限额的使用百分比和进度条
- **Claude HUD 集成**：生成快照文件供 claude-hud 读取显示
- **本地设置数据库**：配置保存在外置 SQLite 文件中，不通过 Git 或网络同步

## 使用方式

### 方式一：轻量启动器 EXE（推荐）

双击 `dist\MiMo-Token-Monitor.exe`，程序会在后台运行，不显示控制台窗口。启动器会先检查代码项目仓库并拉取可安全快进的更新，再启动项目根目录中的 `main.py`；源码、配置和日志仍使用项目现有文件。

**修改任意 Python 源码后，直接重启 exe 即可生效，无需重新打包。** 仅首次使用需要在本机安装 Python 与项目依赖：

```bash
python -m pip install -r requirements.txt
```

### 方式二：直接运行源码

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

### WLB 设置（可选）

在设置中填写 **API Base URL** 和 **API Key** 后，可通过标题栏循环图标切换到第三方用量显示模式或“总览”页面：

- **Base URL**：默认 http://codex.wlbclub.com；可填写主域名、`/v1` 或完整的 `/v1/usage`，程序会自动避免重复拼接
- **API Key**：来自 CC Switch 的 API Key
- 切换后悬浮窗显示剩余百分比、已用百分比、7 天窗口和状态
- 未配置 API Key 时切换会显示提示，不会发送空请求

### GPT 周限额

总览会优先使用本机 Codex 登录信息查询 GPT 周限额，也可在设置中填写 ChatGPT Session Cookie 作为备用认证；本地 Codex 会话记录是最后兜底。网络、限流或服务端瞬时失败会自动重试一次；刷新仍失败时继续显示上次成功数据，并在 tooltip 中标明具体失败来源。

### 构建轻量启动器 EXE

```powershell
.\build-launcher.ps1
```

该脚本会生成 `dist\MiMo-Token-Monitor.exe`，并把 PyInstaller 的 spec 与中间文件放在 `.build-launcher`，不会覆盖完整发行版的 `MiMo-Token-Monitor.spec`。

### 构建独立完整发行版

```bash
pip install pyinstaller
python -m PyInstaller MiMo-Token-Monitor.spec --clean
```

## 操作

- **拖动**：左键拖动窗口位置（除最小化按钮区域外）
- **窗口吸附**：将 ETF Tracker 与 MiMo Token Monitor 的边框拖到约 15 个像素以内，会自动对齐左右/上下边框；两个窗口必须同时可见且未最小化，主屏和副屏均有效
- **置顶按钮**：点击标题栏图钉图标切换置顶/取消置顶；图标高亮表示当前处于置顶状态，取消置顶时显示斜杠
- **标题栏循环图标**：点击标题旁的图标，在 MiMo Token / WLB / 总览之间循环切换；总览会同时展示三个已接入来源的进度
- **双击悬浮窗**：立即刷新数据
- **右键悬浮窗**：刷新 / 从浏览器导入 / 设置 / 查看原始数据 / 退出
- **悬停悬浮窗**：显示详细 tooltip
- **最小化按钮**：右上角 `─` 按钮，点击最小化到系统托盘
- **系统托盘**：
  - 双击托盘图标：恢复显示悬浮窗
  - 右键托盘图标：显示主窗口 / 刷新 / 从浏览器导入 / 更新模型元数据 /
    路由控制 / 重启悬浮窗 / 退出
  - 路由控制：菜单显示“已开启”“已关闭”或“状态未知”；可开启、关闭或重启 Codex Router
  - 重启悬浮窗：退出当前进程并释放单实例锁后启动新实例
  - 悬停托盘图标：显示用量概览

### Codex Router 维护

- 需要先安装 Codex Router；程序默认从
  `~/.codex/codex-router/install-manifest.json` 读取当前源码目录。
- “更新模型元数据”依次运行 `node src/catalog.mjs` 和
  `node src/service.mjs restart`，更新失败时不会继续重启。
- “开启路由”和“关闭路由”复用路由器的 `codex-router.ps1 enable|disable`，因此会
  同步调整 Codex 配置和后台服务；“重启路由器”只重启现有服务。
- 程序启动后及关闭托盘菜单时，会在后台调用 `node src/config-manager.mjs status`，
  缓存并在下次打开菜单时显示路由已开启或已关闭；检测失败时显示“状态未知”。
- 所有操作均在后台线程执行。执行期间路由菜单会暂时禁用，完成后通过托盘通知结果；
  为避免中途销毁线程，操作完成前不能退出程序。
- 路由器源码不在安装清单记录的位置时，可设置
  `MIMO_TOKEN_MONITOR_ROUTER_ROOT` 指向有效的 Codex Router 项目根目录。

### 多屏吸附说明

- 两个程序使用固定的原生窗口标题识别彼此，不会吸附到普通应用窗口或设置对话框。
- 跨窗口吸附在主屏和副屏均启用。Win32 窗口矩形使用物理像素；程序会根据当前屏幕的 `devicePixelRatio` 转换为 Qt 坐标，以保持不同 DPI 显示器上的吸附位置一致。
- 吸附后的最终位置会在松开鼠标时保存，重启后从配置恢复。

## 技术栈

- Python + PyQt6
- 直接调用小米平台 REST API（`/api/v1/tokenPlan/usage`）
- Cookie 认证，支持 CDP（Chrome DevTools Protocol）自动导入
- 配置默认存储于本地外置 SQLite，不通过 Git 同步

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

## 数据存储

- 配置默认存储在外置 SQLite 文件：`D:\python\data\mimo-token-monitor\settings.db`（单表 `settings`，每行一个配置键）。
- 可通过环境变量 `MIMO_TOKEN_MONITOR_DATA_DIR` 覆盖本地数据目录。
- 首次运行时，如果外置库无配置且旧文件 `~/.mimo-widget/config.json` 存在，会自动读取并迁移；旧文件保留，但不再作为主配置来源。
- 数据库仅在本机读写，不执行 Git 拉取、提交或推送；轻量启动器的源码更新功能不受影响。
- **敏感数据提示**：数据库中会包含 Cookie、API Key 等明文凭据，请勿手动加入 Git 或公开仓库。

可用环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MIMO_TOKEN_MONITOR_DATA_DIR` | `D:\python\data\mimo-token-monitor` | 本地数据目录 |

## 隐私

- 配置与快照文件仅在本地存储。未配置 WLB API Key 时不会请求 WLB 服务
- Cookie、API Key 等明文存储在外置 SQLite 数据库（默认 `D:\python\data\mimo-token-monitor\settings.db`）；外置库不可用时可回退到旧 JSON（`~/.mimo-widget/config.json`）
- MiMo 请求发往 `platform.xiaomimimo.com`；启用 WLB 后，会按配置向第三方 Base URL 发送带 Bearer API Key 的用量请求
- GPT 周限额查询会优先使用本机 Codex 登录访问 `chatgpt.com`；也可能读取已配置的 ChatGPT Session Cookie 或本地 Codex 会话记录作为备用来源
