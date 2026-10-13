"""Read SuperGPT account balance using the key already stored by Codex Router."""

import math
import os
from pathlib import Path
import stat

import requests

USAGE_URL = "https://console.supergpt.dev/v1/usage"


def _api_key() -> str:
    value = os.environ.get("SUPERGPT_API_KEY", "").strip()
    if value:
        return value
    state_dir = (
        os.environ.get("MODEL_ROUTER_STATE_DIR")
        or os.environ.get("CODEX_ROUTER_STATE_DIR")
        or os.environ.get("KIMI_CODEX_STATE_DIR")
        or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "codex-router"
    )
    secret = Path(state_dir) / "supergpt-api-key.secret"
    try:
        if not stat.S_ISREG(secret.lstat().st_mode):
            raise OSError("Credential must be a regular file")
        return secret.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def _failure(message: str) -> dict:
    return {"ok": False, "balance": None, "currency": None, "error": message}


def fetch_balance() -> dict:
    try:
        key = _api_key()
    except (OSError, UnicodeError, RuntimeError):
        return _failure("无法读取 Router 的 SuperGPT 密钥")
    if not key:
        return _failure("请先通过 provider-key supergpt-relay set 配置密钥")
    try:
        response = requests.get(
            USAGE_URL,
            headers={"Authorization": f"Bearer {key}"},
            timeout=(3, 10),
            allow_redirects=False,
        )
    except requests.RequestException:
        return _failure("SuperGPT 余额查询网络连接失败")
    try:
        if response.status_code in (401, 403):
            return _failure("SuperGPT API Key 无效或已过期")
        if response.status_code != 200:
            return _failure(f"SuperGPT 余额查询 HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError:
            return _failure("SuperGPT 余额响应不是有效 JSON")
        if not isinstance(data, dict):
            return _failure("SuperGPT 余额响应格式无效")
        if data.get("isValid") is False:
            return _failure("SuperGPT API Key 当前不可用")
        raw_balance = data.get("balance")
        try:
            if isinstance(raw_balance, bool):
                raise ValueError("Not a monetary value")
            balance = float(raw_balance)
        except (TypeError, ValueError, OverflowError):
            return _failure("SuperGPT 未返回有效的账户余额")
        if not math.isfinite(balance):
            return _failure("SuperGPT 未返回有效的账户余额")
        currency = str(data.get("unit", "")).upper()
        if currency not in ("USD", "CNY"):
            return _failure("SuperGPT 返回了未知的余额币种")
        return {"ok": True, "balance": balance, "currency": currency, "error": None}
    finally:
        response.close()
