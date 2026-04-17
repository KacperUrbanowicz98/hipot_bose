import json
import os

CONFIG_FILE = "config.json"

COLORS = {
    "bg": "#1a1a1a",
    "surface": "#242424",
    "card": "#2d2d2d",
    "border": "#3a3a3a",
    "primary": "#0078d4",
    "fail": "#d13438",
    "warning": "#ca5010",
    "success": "#107c10",
    "text": "#ffffff",
    "muted": "#9d9d9d",
}

def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        default = {"users": {}, "profiles": {}, "sn_prefix_map": {}}
        save_config(default)
        return default
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(config: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

def resolve_profile_for_sn(sn: str) -> tuple:
    config = load_config()
    sn_map = config.get("sn_prefix_map", {})
    profiles = config.get("profiles", {})

    prefix = sn[:6]
    profile_key = sn_map.get(prefix)

    if profile_key:
        profile = profiles.get(profile_key)
        if profile:
            return profile_key, profile

    return None, None