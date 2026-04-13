import json
import os

CONFIG_FILE = "config.json"

COLORS = {
    "bg":      "#1a1a1a",
    "surface": "#242424",
    "card":    "#2d2d2d",
    "border":  "#3a3a3a",
    "primary": "#0078d4",
    "fail":    "#d13438",
    "warning": "#ca5010",
    "success": "#107c10",
    "text":    "#ffffff",
    "muted":   "#9d9d9d",
}

def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        default = {"users": {}}
        save_config(default)
        return default
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(config: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)