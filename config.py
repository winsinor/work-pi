"""Configuration management — load, save, and validate config.json."""

import copy
import json
import os
import tempfile

_BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(_BASE, "config.json")

DEFAULTS: dict = {
    "wifi": {
        "ssid": "",
        "password": "",
    },
    "location": {
        "lat": 0.0,
        "lon": 0.0,
        "timezone": "America/New_York",
    },
    "addresses": {
        "home": "",
        "work": "",
        "waypoint": "",
    },
    "api_keys": {
        "tomtom": "",
    },
    "display": {
        "width": 320,
        "height": 240,
        "framebuffer": "/dev/fb1",
        "rotation": 0,          # degrees: 0 or 180 (hardware only supports these)
        "page_dwell_s": 8,      # default seconds per page
        "weather_bg": True,     # weather/time-reactive background gradient
    },
    "buttons": {
        "shutdown_gpio": 23,    # long-press to shut down
        "advance_gpio": 24,     # press to skip to next page
        "shutdown_hold_s": 5,
        "pull_up": True,
        "enabled": True,        # set False if no GPIO buttons wired
    },
    "fonts": {
        "path": "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "fallback_paths": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        ],
    },
    "calendar": {
        "ics_url": "",
        "update_interval_s": 600,
    },
    "weather": {
        "update_interval_s": 600,
    },
    "commute": {
        "update_interval_s": 300,
        "window_start_h": 15,   # 3 PM — show commute page from this hour
        "window_end_h": 18,     # 6 PM — hide commute page after this hour
        "weekdays_only": True,
    },
    "aqi": {
        "update_interval_s": 900,
    },
    "alerts": {
        "update_interval_s": 600,
    },
    "display_cache_s": 60,
    "setup_port": 8080,
    # Web setup-server authentication. Empty password_hash = no password set yet
    # (the server stays open for first-run setup until a password is configured).
    # Managed only via the dedicated /api/auth/* endpoints, never the config form.
    "auth": {
        "password_hash": "",      # PBKDF2-HMAC-SHA256 hex digest
        "salt": "",               # hex
        "iterations": 200000,
        "session_secret": "",     # HMAC key for signed session cookies (rotates on pw change)
        "session_days": 7,        # how long a login stays valid
    },
    "route_labels": [
        "Work → Home",
        "Work → Waypoint → Home",
    ],
    "wfh_keywords": ["wfh", "working from home"],
    "ooo_keywords": ["ooo", "out of office", "pto"],
    "holiday_keywords": ["holiday"],
}

# Fields that must be non-empty for the display to start
_REQUIRED: list[tuple[str, str]] = [
    ("addresses", "home"),
    ("addresses", "work"),
    ("api_keys", "tomtom"),
]


def load() -> dict:
    """Load config.json, filling in defaults for any missing keys."""
    try:
        with open(CONFIG_FILE) as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        raw = {}

    merged = copy.deepcopy(DEFAULTS)
    for k, v in raw.items():
        if isinstance(v, dict) and k in merged and isinstance(merged[k], dict):
            merged[k].update(v)
        else:
            merged[k] = v

    # DISPLAY_ROTATION env var overrides config — useful before config.json exists
    env_rot = os.environ.get("DISPLAY_ROTATION")
    if env_rot is not None:
        try:
            merged["display"]["rotation"] = int(env_rot)
        except ValueError:
            pass

    # Clamp rotation: only 0 and 180 are supported by the framebuffer driver
    merged["display"]["rotation"] = 180 if merged["display"].get("rotation") == 180 else 0

    return merged


def save(cfg: dict) -> None:
    """Atomically write config to disk."""
    dir_ = os.path.dirname(os.path.abspath(CONFIG_FILE))
    os.makedirs(dir_, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(cfg, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CONFIG_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def is_complete(cfg: dict) -> bool:
    """Return True if all required fields are filled."""
    for section, key in _REQUIRED:
        if not (cfg.get(section) or {}).get(key, "").strip():
            return False
    return True


def resolve_font_path(cfg: dict) -> str:
    """Return the first font path that actually exists on this system."""
    candidates = [cfg["fonts"]["path"]] + cfg["fonts"]["fallback_paths"]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]  # caller will handle missing font gracefully
