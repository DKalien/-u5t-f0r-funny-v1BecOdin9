import json
import os

DEFAULT_CONFIG = {
    "cookie": "",
    "refresh_interval": 300,
    "opacity": 0.85,
    "position": [100, 100],
    "snapshot_path": "",  # Path for claude-hud snapshot (empty = disabled)
    "expiry_date": "",  # User-entered plan expiry date
    "expiry_alert_enabled": True,  # Highlight near-expiry dates in red
    "daily_baseline_date": "",  # Date of daily baseline (YYYY-MM-DD format)
    "daily_baseline_usage": 0,  # Month-used value at start of day
    "always_on_top": True,
    "third_party_base_url": "http://codex.wlbclub.com",
    "third_party_api_key": "",
    "display_mode": "mimo",
}

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".mimo-widget")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return dict(DEFAULT_CONFIG)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg)
    return merged


def save_config(cfg: dict):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
