#!/usr/bin/env python3
"""MiMo HUD 一键安装脚本。

自动完成：
1. 定位 claude-hud cache 目录
2. 复制补丁文件
3. 更新 claude-hud 配置

需要手动完成：
- 运行 python main.py 设置 MiMo Cookie
"""

import json
import os
import sys
import shutil
import glob
from pathlib import Path


def find_home():
    return Path.home()


def find_claude_config_dir():
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env and os.path.isdir(env):
        return Path(env)
    return find_home() / ".claude"


def find_hud_cache(claude_dir):
    """查找 claude-hud 的 cache dist 目录"""
    cache_base = claude_dir / "plugins" / "cache"
    if not cache_base.is_dir():
        return None
    # 匹配 */claude-hud/*/dist/
    for path in sorted(cache_base.rglob("claude-hud/*/dist/index.js")):
        return path.parent
    return None


def find_hud_config(claude_dir):
    """查找 claude-hud 配置文件（解析 symlink）"""
    config_path = claude_dir / "plugins" / "claude-hud" / "config.json"
    if config_path.is_symlink():
        real = config_path.resolve()
        if real.exists():
            return real
        return real  # symlink 目标不存在，返回解析后路径供创建
    if config_path.exists():
        return config_path
    return config_path


def patch_cache(cache_dir, patch_dir):
    """复制补丁文件到 cache 目录"""
    files = [
        "index.js",
        "config.js",
        "external-usage.js",
        "render/session-line.js",
        "render/lines/usage.js",
    ]
    for f in files:
        src = patch_dir / f
        dst = cache_dir / f
        if not src.exists():
            print(f"  [跳过] 补丁文件不存在: {src}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"  [OK] {f}")


def update_config(config_path, project_dir):
    """更新 claude-hud 配置，添加 MiMo 相关字段"""
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config = {}

    display = config.setdefault("display", {})

    snapshot_path = str(find_home() / ".mimo-widget" / "hud-usage-snapshot.json").replace("\\", "/")
    sync_cmd = f"python {str(project_dir / 'mimo_hud_sync.py').replace(chr(92), '/')}"

    changed = False
    if display.get("externalUsagePath") != snapshot_path:
        display["externalUsagePath"] = snapshot_path
        changed = True
    if display.get("externalSyncCmd") != sync_cmd:
        display["externalSyncCmd"] = sync_cmd
        changed = True
    if display.get("externalUsageFreshnessMs") != 1800000:
        display["externalUsageFreshnessMs"] = 1800000
        changed = True

    if changed:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print(f"  [OK] 配置已更新: {config_path}")
    else:
        print(f"  [OK] 配置已是最新")


def main():
    print("=== MiMo HUD 安装 ===\n")

    project_dir = Path(__file__).resolve().parent
    patch_dir = project_dir / "hud-patches"

    if not patch_dir.is_dir():
        print(f"错误: 找不到补丁目录 {patch_dir}")
        sys.exit(1)

    claude_dir = find_claude_config_dir()
    print(f"Claude 配置目录: {claude_dir}")

    # Step 1: 找到 cache 目录
    cache_dir = find_hud_cache(claude_dir)
    if not cache_dir:
        print("错误: 找不到 claude-hud cache 目录。请确认 claude-hud 插件已安装。")
        sys.exit(1)
    print(f"HUD cache 目录: {cache_dir}\n")

    # Step 2: 复制补丁
    print("[1/2] 复制补丁文件到 cache...")
    patch_cache(cache_dir, patch_dir)

    # Step 3: 更新配置
    print("\n[2/2] 更新 claude-hud 配置...")
    config_path = find_hud_config(claude_dir)
    update_config(config_path, project_dir)

    # 完成
    print("\n=== 安装完成 ===")
    print("\n还需手动完成一步：设置 MiMo Cookie")
    print(f"  python {project_dir / 'main.py'}")
    print("\n然后重启 Claude Code，HUD 应显示 MiMo 用量。")


if __name__ == "__main__":
    main()
