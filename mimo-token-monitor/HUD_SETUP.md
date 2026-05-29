# MiMo HUD 集成部署指南

将 MiMo 套餐用量（进度条 + 余额）集成到 claude-hud 状态栏。

## 效果

```
上下文 ████░░░░░░ 45% │ 用量 ██░░░░░░░░ MiMo Max 2%
```

## 前置条件

- Python 3.x
- claude-hud 插件已安装
- MiMo 账号 Cookie（通过 `python main.py` 设置）

## 快速安装（推荐）

将整个 `mimo-token-monitor` 目录复制到目标电脑，然后运行：

```bash
python setup_hud.py
```

脚本会自动完成：
- 定位 claude-hud cache 目录
- 复制补丁文件（修改 HUD 渲染逻辑）
- 更新 claude-hud 配置（添加同步路径）

最后手动设置 Cookie：
```bash
python main.py
```

重启 Claude Code 即可。

## 手动安装

如果自动脚本不适用，按以下步骤操作：

### 1. 复制项目文件

将以下文件复制到目标电脑（任意目录）：

```
mimo-token-monitor/
├── api_client.py        # API 客户端（必需）
├── config.py            # 配置管理（必需）
├── mimo_hud_sync.py     # HUD 同步脚本（必需）
├── setup_hud.py         # 一键安装脚本（推荐）
├── hud-patches/         # 编译好的补丁文件（必需）
│   ├── index.js
│   ├── config.js
│   ├── external-usage.js
│   └── render/
│       ├── session-line.js
│       └── lines/
│           └── usage.js
├── main.py              # 桌面悬浮窗（可选）
└── widget.py            # UI 代码（可选）
```

### 2. 配置 Cookie

运行主程序，在设置界面粘贴 MiMo Cookie：

```bash
python main.py
```

Cookie 保存在 `~/.mimo-widget/config.json`。

### 3. 修改 claude-hud 配置

编辑 `~/.claude/plugins/claude-hud/config.json`（若为 symlink 则编辑其指向的真实文件），在 `display` 中添加三个字段：

```json
{
  "display": {
    "externalUsagePath": "<HOME>/.mimo-widget/hud-usage-snapshot.json",
    "externalUsageFreshnessMs": 1800000,
    "externalSyncCmd": "python <项目路径>/mimo_hud_sync.py"
  }
}
```

- `externalUsagePath`：snapshot 文件路径，按实际用户目录调整
- `externalSyncCmd`：同步命令，按实际项目路径调整
- `externalUsageFreshnessMs`：数据刷新间隔（毫秒），1800000 = 30 分钟

### 4. 覆盖 claude-hud 缓存文件（关键）

HUD 实际从 `plugins/cache/` 目录加载，不是 `plugins/marketplaces/`。需要将 `hud-patches/` 中的文件复制到 cache。

**查找 cache 目录：**

```bash
ls -1d ~/.claude/plugins/cache/*/claude-hud/*/ | sort -V | tail -1
```

**复制补丁：**

```bash
CACHE=$(ls -1d ~/.claude/plugins/cache/*/claude-hud/*/ | sort -V | tail -1)dist
cp hud-patches/index.js "$CACHE/"
cp hud-patches/config.js "$CACHE/"
cp hud-patches/external-usage.js "$CACHE/"
cp hud-patches/render/session-line.js "$CACHE/render/"
cp hud-patches/render/lines/usage.js "$CACHE/render/lines/"
```

### 5. 验证

```bash
# 手动测试同步
python mimo_hud_sync.py

# 检查输出文件
cat ~/.mimo-widget/hud-usage-snapshot.json

# 重启 Claude Code，HUD 应显示 MiMo 用量
```

## 给 AI 助手的指令

直接告诉 AI：

> 请运行 `python setup_hud.py` 安装 MiMo HUD 集成，然后运行 `python main.py` 设置 Cookie。

## 注意事项

- claude-hud 插件更新后 cache 会被覆盖，需重新运行 `setup_hud.py`
- Cookie 过期后需通过 `python main.py` 重新设置
- 同步脚本由 HUD 渲染时自动触发，无需手动运行或后台进程
- 进度条在用量极低时（<10%）显示为全空 `░░░░░░░░░░`，属正常现象
