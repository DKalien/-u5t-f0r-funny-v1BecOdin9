import os
import requests

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
