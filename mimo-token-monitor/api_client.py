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
