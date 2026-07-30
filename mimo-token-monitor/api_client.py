import os
import requests
import json as _json
import math as _math
import pathlib as _pl

# 确保打包后 requests 也能找到 CA 证书
if "SSL_CERT_FILE" not in os.environ:
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
    except Exception:
        pass

BASE = "https://platform.xiaomimimo.com"
BALANCE_URL = f"{BASE}/api/v1/balance"

# Multiple candidate endpoints for usage/plan data
# Token Plan endpoints (from frontend JS: /tokenPlan/usage, /tokenPlan/subscription/status)
USAGE_URLS = [
    f"{BASE}/api/v1/tokenPlan/usage",
    f"{BASE}/api/v1/tokenPlan/subscription/status",
    f"{BASE}/api/v1/tokenPlan/subscription/order",
    f"{BASE}/api/v1/usage",
]


def _headers(cookie: str) -> dict:
    return {
        "Cookie": cookie,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0",
        "Referer": "https://platform.xiaomimimo.com/",
    }


def fetch_balance(cookie: str) -> dict:
    try:
        resp = requests.get(BALANCE_URL, headers=_headers(cookie), timeout=10)
        if resp.status_code == 401:
            return {"ok": False, "balance": None, "error": "Cookie 已过期，请重新获取"}
        if resp.status_code >= 400:
            return {"ok": False, "balance": None, "error": f"HTTP {resp.status_code}"}
        data = resp.json()
        balance = None
        if isinstance(data, dict):
            d = data.get("data", data)
            if isinstance(d, (int, float)):
                balance = d
            elif isinstance(d, dict):
                balance = d.get("balance") or d.get("amount") or d.get("remain")
            elif isinstance(d, str):
                balance = d
            # Direct on root
            if balance is None:
                balance = data.get("balance")
        return {"ok": True, "balance": balance, "error": None}
    except requests.exceptions.ConnectionError:
        return {"ok": False, "balance": None, "error": "网络连接失败"}
    except Exception as e:
        return {"ok": False, "balance": None, "error": str(e)[:100]}


def fetch_usage(cookie: str) -> dict:
    """Try multiple endpoints until one returns valid data."""
    headers = _headers(cookie)
    errors = []

    for url in USAGE_URLS:
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 401:
                return {"ok": False, "data": None, "error": "Cookie 已过期，请重新获取"}
            if resp.status_code == 404:
                errors.append(f"{url.split('/')[-1]}: 404")
                continue
            if resp.status_code >= 400:
                errors.append(f"{url.split('/')[-1]}: {resp.status_code}")
                continue
            data = resp.json()
            # Accept any successful JSON response with actual content
            if isinstance(data, dict) and data.get("code") == 0:
                return {"ok": True, "data": data, "error": None, "url": url}
            # Some endpoints might not have code field
            if isinstance(data, dict) and "data" in data:
                return {"ok": True, "data": data, "error": None, "url": url}
            errors.append(f"{url.split('/')[-1]}: 格式不匹配")
        except Exception as e:
            errors.append(f"{url.split('/')[-1]}: {str(e)[:30]}")

    return {"ok": False, "data": None, "error": f"所有端点均失败: {'; '.join(errors[:3])}"}

DEFAULT_THIRD_PARTY_BASE_URL = "http://codex.wlbclub.com"


def _third_party_usage_url(base_url: str) -> str:
    """Build the usage endpoint without duplicating a user-supplied /v1 path."""
    base = (base_url or DEFAULT_THIRD_PARTY_BASE_URL).strip().rstrip("/")
    if not base:
        base = DEFAULT_THIRD_PARTY_BASE_URL

    lower_base = base.casefold()
    if lower_base.endswith("/v1/usage"):
        return base
    if lower_base.endswith("/v1"):
        return f"{base}/usage"
    return f"{base}/v1/usage"


def parse_third_party_usage(data, window: str = "7d") -> dict:
    """Parse third-party usage API response."""
    empty = {
        "is_valid": False, "window": window, "used": 0, "limit": 0,
        "remaining": 0, "used_percent": 0, "remaining_percent": 0,
        "total_percent": 100, "unit": "%", "has_rate_limit": False,
    }
    if not isinstance(data, dict):
        return dict(empty)

    source = data
    rate_limits = source.get("rate_limits")
    if not isinstance(rate_limits, list) and isinstance(data.get("data"), dict):
        source = data["data"]
        rate_limits = source.get("rate_limits")
    if not isinstance(rate_limits, list):
        return dict(empty)

    entry = None
    for item in rate_limits:
        if isinstance(item, dict) and item.get("window") == window:
            entry = item
            break

    if entry is None:
        return dict(empty)

    try:
        limit_val = float(entry.get("limit", 0) or 0)
    except (ValueError, TypeError):
        limit_val = 0.0
    try:
        used_val = float(entry.get("used", 0) or 0)
    except (ValueError, TypeError):
        used_val = 0.0

    remaining_raw = entry.get("remaining")
    try:
        remaining_val = float(remaining_raw) if remaining_raw is not None else max(0.0, limit_val - used_val)
    except (ValueError, TypeError):
        remaining_val = max(0.0, limit_val - used_val)

    remaining_pct = (remaining_val / limit_val * 100) if limit_val > 0 else 0
    used_pct = (used_val / limit_val * 100) if limit_val > 0 else 0

    # The CC Switch extractor reads validity from the response root, not the
    # selected rate-limit entry. Keep the nullish fallback semantics of `??`.
    is_valid = data.get("isValid")
    if is_valid is None:
        is_valid = data.get("status") == "active"
        if "isValid" not in data and "status" not in data and source is not data:
            is_valid = source.get("status") == "active"

    return {
        "is_valid": bool(is_valid),
        "window": window,
        "used": used_val,
        "limit": limit_val,
        "total": 100.0,
        "remaining": remaining_val,
        "used_percent": round(used_pct, 2),
        "remaining_percent": round(remaining_pct, 2),
        "total_percent": 100,
        "unit": "%",
        "has_rate_limit": True,
    }


def fetch_third_party_usage(base_url: str, api_key: str, window: str = "7d") -> dict:
    """Fetch third-party usage from external API.

    GET the normalized base_url/v1/usage endpoint with Authorization: Bearer apiKey.
    Returns: ok, data, error, url
    """
    url = _third_party_usage_url(base_url)

    if not str(api_key or "").strip():
        return {"ok": False, "data": None, "error": "API Key 不能为空", "url": url}

    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
        if resp.status_code == 401:
            return {"ok": False, "data": None, "error": "API Key 无效或已过期", "url": url}
        if resp.status_code >= 400:
            return {"ok": False, "data": None, "error": f"HTTP {resp.status_code}", "url": url}

        raw = resp.json()
        parsed = parse_third_party_usage(raw, window)
        if not parsed["has_rate_limit"]:
            return {
                "ok": False,
                "data": None,
                "error": f"响应中未找到 {window} 用量数据",
                "url": url,
            }
        return {"ok": True, "data": parsed, "error": None, "url": url}
    except requests.exceptions.ConnectionError:
        return {"ok": False, "data": None, "error": "网络连接失败", "url": url}
    except requests.exceptions.JSONDecodeError:
        return {"ok": False, "data": None, "error": "响应不是有效 JSON", "url": url}
    except Exception as e:
        return {"ok": False, "data": None, "error": str(e)[:100], "url": url}

# -- GPT Weekly Usage -------------------------------------------------------

GPT_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
GPT_AUTH_SESSION_URL = "https://chatgpt.com/api/auth/session"
GPT_WEEKLY_WINDOW_SECONDS = 7 * 24 * 60 * 60

def _parse_gpt_secondary_window(data):
    """Extract weekly/secondary window fields from a ChatGPT usage response.

    Supports multiple response shapes:
      New object:  rate_limits is a dict with a .secondary sub-dict
      New list:    rate_limits is a list; find entry with window=weekly/7d/secondary
      Old:         rate_limit.secondary_window is a dict
    Nested data wrappers are unwrapped automatically.
    Returns a flat dict with used_percent, reset_at, reset_after_seconds, or None."""
    if not isinstance(data, dict):
        return None

    source = data.get("data") if isinstance(data.get("data"), dict) else data

    secondary = None

    rate_limits = source.get("rate_limits")
    if isinstance(rate_limits, dict):
        # New wham shape: rate_limits is an object with .secondary dict
        candidate = rate_limits.get("secondary")
        if isinstance(candidate, dict):
            secondary = candidate
    elif isinstance(rate_limits, list):
        # New list shape: find entry with window=weekly, 7d, or type=secondary
        for window_val in ("weekly", "7d", "secondary"):
            for entry in rate_limits:
                if isinstance(entry, dict) and entry.get("window") == window_val:
                    secondary = entry
                    break
            if secondary is not None:
                break
        if secondary is None:
            for entry in rate_limits:
                if isinstance(entry, dict) and entry.get("type") == "secondary":
                    secondary = entry
                    break

    # Old format: rate_limit.secondary_window is normally the weekly window.
    if secondary is None:
        rl = source.get("rate_limit") if isinstance(source.get("rate_limit"), dict) else None
        if rl and isinstance(rl.get("secondary_window"), dict):
            secondary = rl["secondary_window"]

        # Some current Plus responses put the 7-day window in primary_window
        # and leave secondary_window null. Do not mistake the usual 5-hour
        # primary window for a weekly quota.
        if secondary is None and rl and isinstance(rl.get("primary_window"), dict):
            primary = rl["primary_window"]
            try:
                window_seconds = int(primary.get("limit_window_seconds", 0) or 0)
            except (TypeError, ValueError):
                window_seconds = 0
            if window_seconds == GPT_WEEKLY_WINDOW_SECONDS:
                secondary = primary

    if secondary is None:
        return None

    raw_pct = secondary.get("used_percent")
    if raw_pct is None:
        raw_pct = secondary.get("usedPercent")
    if raw_pct is None:
        return None

    try:
        used_pct = float(raw_pct)
    except (ValueError, TypeError):
        return None
    if not _math.isfinite(used_pct):
        return None
    used_pct = max(0.0, min(100.0, used_pct))

    reset_at = secondary.get("resets_at") or secondary.get("reset_at")
    reset_after = secondary.get("reset_after_seconds") or secondary.get("reset_after")

    return {
        "used_percent": round(used_pct, 2),
        "remaining_percent": round(100.0 - used_pct, 2),
        "reset_at": reset_at,
        "reset_after_seconds": reset_after,
    }


def fetch_gpt_weekly_usage(session_cookie=""):
    """Fetch GPT weekly (secondary window) usage.

    Sources tried in order:
      1. Local Codex auth.json (offline, no user config needed)
      2. Configured ChatGPT session cookie (two-step auth exchange)
      3. Local JSONL session files (offline fallback)

    Returns: {ok, data: {used_percent, remaining_percent, reset_at,
             reset_after_seconds, source}, error}
    """
    sources = [
        (_gpt_try_local_auth, {}),
        (_gpt_try_session_cookie, {"session_cookie": session_cookie}),
        (_gpt_try_jsonl, {}),
    ]
    for method, kwargs in sources:
        try:
            result = method(**kwargs)
            if result is not None:
                return {"ok": True, "data": result, "error": None}
        except Exception:
            pass

    return {"ok": False, "data": None, "error": "未找到 GPT 周限额数据"}

def _gpt_try_local_auth():
    """Try reading access_token from local Codex auth.json and fetching usage."""
    home = _pl.Path.home()
    candidates = [
        home / ".codex" / "auth.json",
        home / ".chatgpt" / "auth.json",
    ]
    access_token = None
    account_id = None
    for path in candidates:
        if not path.exists():
            continue
        try:
            auth_data = _json.loads(path.read_text(encoding="utf-8"))
            tokens = auth_data.get("tokens") if isinstance(auth_data, dict) else None
            if isinstance(tokens, dict):
                access_token = tokens.get("access_token")
                # Priority: tokens.account_id > root.account_id > root.account.id
                account_id = tokens.get("account_id")
                if not account_id:
                    account_id = auth_data.get("account_id")
                if not account_id:
                    acct = auth_data.get("account")
                    if isinstance(acct, dict):
                        account_id = acct.get("id")
        except Exception:
            continue
        if access_token:
            break

    if not access_token:
        return None

    hdrs = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "Mozilla/5.0 Codex-Usage/1.0",
        "Originator": "codex-cli",
    }
    if account_id:
        hdrs["ChatGPT-Account-ID"] = account_id

    resp = requests.get(GPT_USAGE_URL, headers=hdrs, timeout=15)
    if resp.status_code != 200:
        return None

    data = resp.json()
    parsed = _parse_gpt_secondary_window(data)
    if parsed is None:
        return None

    parsed["source"] = "local_codex_auth"
    return parsed

def _gpt_try_session_cookie(session_cookie=""):
    """Two-step: exchange session cookie for access_token via GET, then fetch usage."""
    if not session_cookie or not session_cookie.strip():
        return None

    resp = requests.get(
        GPT_AUTH_SESSION_URL,
        headers={
            "Cookie": session_cookie,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0",
        },
        timeout=15,
    )
    if resp.status_code != 200:
        return None

    auth_data = resp.json()
    if not isinstance(auth_data, dict):
        return None

    access_token = auth_data.get("accessToken")
    if not access_token:
        return None

    account_id = None
    account = auth_data.get("account")
    if isinstance(account, dict):
        account_id = account.get("id")

    hdrs = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0",
        "Originator": "codex-cli",
    }
    if account_id:
        hdrs["ChatGPT-Account-ID"] = account_id

    resp2 = requests.get(GPT_USAGE_URL, headers=hdrs, timeout=15)
    if resp2.status_code != 200:
        return None

    data = resp2.json()
    parsed = _parse_gpt_secondary_window(data)
    if parsed is None:
        return None

    parsed["source"] = "session_cookie"
    return parsed


def _gpt_try_jsonl():
    """Try reading secondary window data from local Codex JSONL session files."""
    home = _pl.Path.home()
    sessions_dir = home / ".codex" / "sessions"
    if not sessions_dir.is_dir():
        return None

    jsonl_files = sorted(
        sessions_dir.rglob("rollout-*.jsonl"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )[:10]

    for jsonl_path in jsonl_files:
        try:
            text = jsonl_path.read_text(encoding="utf-8", errors="replace")
            for line in reversed(text.splitlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                payload = entry.get("payload") if isinstance(entry, dict) else None
                if not isinstance(payload, dict):
                    continue
                parsed = _parse_gpt_secondary_window(payload)
                if parsed is not None:
                    parsed["source"] = "local_jsonl"
                    return parsed
        except Exception:
            continue

    return None
