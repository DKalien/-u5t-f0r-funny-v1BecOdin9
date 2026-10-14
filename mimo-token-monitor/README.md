# MiMo Token Monitor

MiMo Token Plan、WLB、GPT 5 小时/周限额用量及 SuperGPT 账户余额监控桌面悬浮窗。

## 功能

- 桌面悬浮窗实时显示 Token Plan 用量（已用/总额度/剩余）
- **今日已用额度显示**：自动计算并显示当天使用量及折合金额
- 根据套餐总额自动匹配挡位（Lite/Standard/Pro/Max），显示已用额度折合金额
- MiMo 余额为 0 时自动隐藏；SuperGPT 余额为 0 仍显示 $0.00
- **一键导入 Cookie**：通过 CDP 自动从浏览器读取，无需手动复制
- **Playwright 自动续期**：使用独立浏览器配置定时刷新登录状态，减少重复登录
- 自动读取小米平台 API 获取真实数据
- 进度条颜色随用量变化（绿→黄→红）
- 支持手动填写套餐有效期并显示“有效期至”
- 支持按量付费用量查询
- 可拖动、半透明、置顶显示；标题栏图钉按钮可切换置顶状态，默认置顶并自动保存
- **跨窗口边框吸附**：可与 ETF Tracker 悬浮窗相互吸附，主屏和副屏均支持，并兼容不同 DPI 的显示器
- **系统托盘**：最小化到托盘，双击恢复，实时 tooltip，并可直接重启悬浮窗
- **Codex Router 维护**：从托盘查看当前状态、切换官方 / WLB / SuperGPT 路由，并开启、关闭或重启路由器
- **单实例运行**：防止重复启动；再次运行会自动恢复并置顶已有窗口
- **WLB 用量显示**：标题栏循环图标可切换 MiMo Token / WLB 模式，支持配置 WLB API（默认 https://codex.wlbclub.com），显示剩余百分比、已用百分比、进度条和状态；接口返回 `reset_at` 时同时显示 7 日窗口重置日期
- **用量总览页面**：第一行左侧为 Mimo 套餐用量及剩余天数，右侧为 API余额（SuperGPT 账户余额），金额右对齐、进度条以 100 美元为满额。下方 WLB 日/周限额与 GPT 主/周窗口分别显示重置时分或剩余天数、右对齐剩余百分比和左右分段进度条
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

### Playwright 自动续期（可选）

设置中的“Playwright 自动续期”默认开启，默认每 6 小时打开一次 MiMo 页面并从独立的
`playwright-profile` 配置读取最新 Cookie。首次使用需要安装浏览器运行时：

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
```

该配置目录位于 `MIMO_TOKEN_MONITOR_DATA_DIR` 下，不使用也不修改现有 Edge/Chrome 用户目录。
该目录包含登录状态，请勿复制给他人或提交到 Git。

续期会打开平台页并等待小米账号 SSO 回跳，读取 `api-platform_serviceToken` 等
`xiaomimimo.com` Cookie（SSO 往往需要十余秒，不能打开后立刻读取）。自动续期和过期恢复
默认使用 headless Chromium，不会闪现浏览器窗口；手动「Playwright 续期」会打开有界面浏览器，
并保持到 Cookie 通过 API 校验或超时，以便完成登录/验证码。

若当前配置的 Cookie 仍有效、只是独立 Playwright 会话尚未写入平台 Cookie，自动续期失败只会在
悬浮窗底部和托盘提示「Playwright 未登录，当前 Cookie 仍可用」，不会弹阻塞对话框。真正需要
人工登录时，可从托盘或悬浮窗菜单手动执行「Playwright 续期」。验证码仍然需要人工输入。
Playwright 不能延长服务端设置的 Cookie 有效期。

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

- **Base URL**：默认 https://codex.wlbclub.com；可填写主域名、`/v1` 或完整的 `/v1/usage`，程序会自动避免重复拼接
- **API Key**：来自 CC Switch 的 API Key
- 切换后悬浮窗显示剩余百分比、已用百分比、7 天窗口和状态；当日、周窗口同时返回时，总览中的日剩余百分比以日、周两者剩余量的较小值计算，原始日/周 `limit` 字段保持不变；接口返回 `reset_at` 或 `reset_after` 时显示重置日期时间，总览中的 WLB 左侧显示日窗口重置时分（如 `WLB - 18:18`，缺失时为 `WLB - --:--`），右侧只显示周重置剩余天数（如 `5天`，缺失时为 `--天`）
- 未配置 API Key 时切换会显示提示，不会发送空请求

### 总览与 SuperGPT 余额

总览中的 Mimo、WLB 日/周和 GPT 5 小时/周百分比及悬停提示均显示剩余比例；进度条仍按已用比例填充并沿用原有颜色。

总览标题为 **Mimo**，进度条与下方 WLB 左侧进度条等宽（111 px，扣除栏间距后均分）；右侧标题为 **API余额**，金额右对齐，下方进度条以 **100 美元**为满额基准（余额越多，填充越长；超过 100 美元显示满条，金额仍显示实际值）。
余额沿用刷新间隔在独立后台线程查询，无需复制密钥到设置：优先读取 `SUPERGPT_API_KEY`，
否则复用 Router 状态目录中的 `supergpt-api-key.secret`（支持 Router 的状态目录环境变量）。
查询使用 `https://api.supergpt.dev/v1/usage` 的 `balance` 和 `unit`，不把密钥额度当作充值余额。
美元显示 `$`、人民币显示 `¥`（非美元不计算美元基准进度）；缺少数据时显示 `--`，零余额仍显示，进度条为空。失败时保留上次余额、进度和原有颜色，
悬停可查看完整金额、最后成功时间和错误原因。切换到 SuperGPT 路由后，余额标题会变绿。

### GPT 5 小时与周限额

总览会优先使用本机 Codex 登录信息查询 GPT 5 小时与周限额，也可在设置中填写 ChatGPT Session Cookie 作为备用认证；本地 Codex 会话记录是最后兜底。GPT 行使用一条左右分段的进度条，左侧显示 `GPT - HH:MM` 主窗口重置时分，右侧只显示周重置剩余天数；缺失重置数据时分别显示 `GPT - --:--` 与 `--天`。网络、限流或服务端瞬时失败会自动重试一次；刷新仍失败时继续显示上次成功数据，并在 tooltip 中标明具体失败来源。

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
- **标题栏循环图标**：点击标题旁的图标，在 MiMo Token / WLB / 总览之间循环切换；总览会同时展示 Mimo 套餐用量、SuperGPT API余额、WLB 日/周限额和 GPT 双窗口进度
- **双击悬浮窗**：立即刷新数据
- **右键悬浮窗**：刷新 / 从浏览器导入 / 设置 / 查看原始数据 / 退出
- **悬停悬浮窗**：显示详细 tooltip
- **最小化按钮**：右上角 `─` 按钮，点击最小化到系统托盘
- **系统托盘**：
  - 双击托盘图标：恢复显示悬浮窗
  - 右键托盘图标：显示主窗口 / 刷新 / 从浏览器导入 / Playwright 续期 /
    路由控制 / 重启悬浮窗 / 退出
  - 路由控制：菜单显示“已开启”“已关闭”或“状态未知”；可切换到 WLB / SuperGPT / GPT，也可开启、关闭或重启 Codex Router
  - 重启悬浮窗：退出当前进程并释放单实例锁后启动新实例
  - 悬停托盘图标：显示用量概览

### Codex Router 维护

- 需要先安装 Codex Router；程序默认从
  `~/.codex/codex-router/install-manifest.json` 读取当前源码目录。
- “切换到 WLB”“切换到 SuperGPT”和“切换到 GPT”调用 Router 的
  `gpt-route.mjs wlb|supergpt|official`，不重启服务、不自动开启 Router。请先开启路由；
  切换影响所有任务后续的原生 GPT 请求，已识别的 GPT 子代理固定走官方。
  菜单勾选已确认的当前服务商，总览对应的 WLB / GPT 整行标题文字显示绿色；WLB
  单独页面的标题也同步变绿。SuperGPT 勾选菜单项并高亮余额标题，不高亮现有的官方 GPT / WLB
  用量栏。官方直连时标记 GPT；状态未知或 Router 不健康时不高亮。
- “开启路由”和“关闭路由”复用路由器的 `codex-router.ps1 enable|disable`，因此会
  同步调整 Codex 配置和后台服务；“重启路由器”只重启现有服务。
- 程序启动后、关闭托盘菜单时及每 30 秒，会在后台调用 `node src/status.mjs --json`，
  缓存并在下次打开菜单时显示路由已开启或已关闭；检测失败时显示“状态未知”。菜单
  关闭后的状态刷新会延迟到事件循环下一轮，避免点击启停后被旧状态刷新竞态覆盖。
  菜单可见或已有操作运行时跳过轮询；切换成功后立即刷新高亮和勾选。
  后台状态检查中点击切换，会等待检查完成后串行执行。
- 所有操作均在后台线程执行。执行期间路由菜单会暂时禁用；悬浮窗底部显示“正在…”状态，
  完成后显示成功或失败摘要并在 5 秒后清除，同时保留托盘通知。为避免中途销毁线程，
  操作完成前不能退出程序。
- 路由源码根目录优先取 `MIMO_TOKEN_MONITOR_ROUTER_ROOT`；未设置时读取 `$CODEX_HOME`
  （默认 `~/.codex`）下 `codex-router/install-manifest.json` 的
  `current.sourceRoot`，并校验所需入口文件。状态使用 `node src/status.mjs --json`，
  启停使用
  `codex-router.ps1 enable|disable`，重启使用 `node src/service.mjs restart`。

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
- MiMo / ChatGPT Cookie 和 WLB API Key 等明文存储在外置 SQLite 数据库（默认 `D:\python\data\mimo-token-monitor\settings.db`）；外置库不可用时可回退到旧 JSON（`~/.mimo-widget/config.json`）
- MiMo 请求发往 `platform.xiaomimimo.com`；启用 WLB 后，会按配置向第三方 Base URL 发送带 Bearer API Key 的用量请求
- SuperGPT 密钥仅从环境变量或 Router 的受保护 `supergpt-api-key.secret` 读取，不复制进 SQLite、快照或日志；总览页向 `https://api.supergpt.dev/v1/usage` 发送带 Bearer API Key 的余额请求，不跟随重定向，未配置密钥时不请求
- GPT 5 小时/周限额查询会优先使用本机 Codex 登录访问 `chatgpt.com`；也可能读取已配置的 ChatGPT Session Cookie 或本地 Codex 会话记录作为备用来源
