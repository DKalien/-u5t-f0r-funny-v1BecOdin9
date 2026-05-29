#!/usr/bin/env python3
"""MiMo Token HUD Sync — 将 MiMo 套餐用量数据写入 claude-hud 的 external usage snapshot 文件。

用法:
    python mimo_hud_sync.py              # 单次同步
    python mimo_hud_sync.py --daemon     # 后台持续运行（默认每 300 秒刷新）
    python mimo_hud_sync.py --daemon --interval 60  # 自定义刷新间隔（秒）
"""

import json
import os
import sys
import time
import argparse
from datetime import datetime, timezone

# 复用 mimo-token-monitor 的 API 客户端
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from api_client import fetch_balance, fetch_usage
from config import load_config

# ── 套餐挡位 ─────────────────────────────────────────────────────
PLAN_TIERS = [
    (82_000_000_000, "Max", 659),
    (38_000_000_000, "Pro", 329),
    (11_000_000_000, "Standard", 99),
    (4_100_000_000, "Lite", 39),
]

# ── 输出路径 ─────────────────────────────────────────────────────
HOME = os.path.expanduser("~")
SNAPSHOT_DIR = os.path.join(HOME, ".mimo-widget")
SNAPSHOT_PATH = os.path.join(SNAPSHOT_DIR, "hud-usage-snapshot.json")


def _get_plan_tier_info(total: int):
    for credits, name, price in sorted(PLAN_TIERS, reverse=True):
        if total >= credits * 0.95:
            return name, price, price / credits
    return None, None, None


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def _fmt_money(v) -> str:
    if v is None:
        return "--"
    try:
        return f"{float(v):.2f}"
    except (ValueError, TypeError):
        return "--"


def sync_once(cookie: str) -> bool:
    """执行一次同步，返回是否成功。"""
    bal_result = fetch_balance(cookie)
    usage_result = fetch_usage(cookie)

    if not bal_result.get("ok") and not usage_result.get("ok"):
        error = bal_result.get("error") or usage_result.get("error") or "未知错误"
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 获取失败: {error}", file=sys.stderr)
        return False

    # 解析余额
    balance = None
    if bal_result.get("ok") and bal_result.get("balance") is not None:
        try:
            balance = float(bal_result["balance"])
        except (ValueError, TypeError):
            pass

    # 解析套餐用量
    used = 0
    total = 0
    tier_name = None
    monthly_price = None

    if usage_result.get("ok") and usage_result.get("data"):
        data = usage_result["data"]
        inner = data.get("data", data) if isinstance(data, dict) else {}

        # 格式 1: Token Plan
        items = inner.get("usage", {}).get("items", [])
        for item in items:
            if item.get("name") == "plan_total_token":
                used = item.get("used", 0)
                total = item.get("limit", 0)
                break

        if total > 0:
            tier_name, monthly_price, _ = _get_plan_tier_info(total)

    # 构建 balance_label
    parts = []
    if tier_name:
        pct = round(used / total * 100) if total > 0 else 0
        parts.append(f"MiMo {tier_name} {pct}%")
    elif total > 0:
        pct = round(used / total * 100)
        parts.append(f"MiMo {pct}%")
    elif used > 0:
        parts.append(f"MiMo {_fmt_tokens(used)} tok")

    if balance is not None and balance > 0:
        parts.append(f"¥{_fmt_money(balance)}")

    balance_label = " | ".join(parts) if parts else None

    # 构建 snapshot
    pct = round(used / total * 100) if total > 0 else 0
    snapshot = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "five_hour": {"used_percentage": pct} if total > 0 else None,
        "seven_day": None,
    }
    if balance_label:
        snapshot["balance_label"] = balance_label

    # 写入文件
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 已同步: {balance_label or '(无数据)'}")
    return True


def main():
    parser = argparse.ArgumentParser(description="MiMo Token HUD 同步工具")
    parser.add_argument("--daemon", action="store_true", help="后台持续运行")
    parser.add_argument("--interval", type=int, default=300, help="刷新间隔（秒），默认 300")
    args = parser.parse_args()

    # 加载 cookie
    cfg = load_config()
    cookie = cfg.get("cookie", "")
    if not cookie:
        print("错误: 未配置 Cookie。请先运行 MiMo Token Monitor 主程序设置 Cookie。", file=sys.stderr)
        sys.exit(1)

    if args.daemon:
        print(f"MiMo HUD 同步已启动（间隔 {args.interval}s）")
        print(f"输出文件: {SNAPSHOT_PATH}")
        try:
            while True:
                sync_once(cookie)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n同步已停止")
    else:
        success = sync_once(cookie)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
