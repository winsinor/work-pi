"""Embedded HTTP server for web-based configuration and WiFi management."""
from __future__ import annotations

import copy
import email.parser
import hashlib
import hmac
import io
import json
import mimetypes
import os
import secrets
import socket
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import config as cfg_module

_BASE = os.path.dirname(os.path.abspath(__file__))
_SETUP_HTML = os.path.join(_BASE, "setup", "index.html")
_CUSTOM_IMAGES_DIR = os.path.join(_BASE, "custom_images")
_ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp"}
_EDITOR_DIR  = os.path.join(_BASE, "editor")
_ICONS_DIR   = os.path.join(_BASE, "icons")
_LAYOUT_FILE = os.path.join(_BASE, "work_layout.json")

# Event signalled when user saves a valid config via the web UI
config_saved = threading.Event()

# Secret fields are masked in GET /api/config responses (the server binds to the
# LAN with no auth). When the client posts the sentinel back, it means the user
# didn't change the field, so the previously-saved value is preserved.
_SECRET_SENTINEL = "__SAVED__"
_SECRET_PATHS = (
    ("wifi", "password"),
    ("api_keys", "tomtom"),
    ("spotify", "client_secret"),
    ("spotify", "refresh_token"),
)


def _mask_secrets(cfg: dict) -> dict:
    """Return a deep copy of cfg with non-empty secret fields replaced by a sentinel."""
    out = copy.deepcopy(cfg)
    for section, key in _SECRET_PATHS:
        sec = out.get(section)
        if isinstance(sec, dict) and str(sec.get(key, "") or "").strip():
            sec[key] = _SECRET_SENTINEL
    return out


def _strip_secret_sentinels(incoming: dict) -> None:
    """Drop secret fields from POSTed config when they equal the sentinel, so the
    merge keeps the previously-saved value instead of overwriting it with the mask."""
    for section, key in _SECRET_PATHS:
        sec = incoming.get(section)
        if isinstance(sec, dict) and sec.get(key) == _SECRET_SENTINEL:
            sec.pop(key, None)


# ── Authentication ────────────────────────────────────────────────────────────────────
#
# The setup server binds to 0.0.0.0 and exposes config, WiFi control and other
# system endpoints, so it must not be an open door on a Tailscale/LAN network.
# A single shared password gates everything; a successful login issues a signed,
# stateless session cookie valid for `auth.session_days` (default 7). The cookie
# is HMAC-signed with `auth.session_secret`, which is rotated whenever the
# password changes — so changing the password invalidates every existing session.
#
# No Secure flag is set on the cookie because the server speaks plain HTTP; the
# transport is expected to be protected by Tailscale (WireGuard) or a trusted LAN.

_SESSION_COOKIE = "wp_session"
_PBKDF2_ITERS = 200_000
_MIN_PASSWORD_LEN = 8

# Endpoints reachable without a valid session (login flow + OAuth callback, which
# is hit by a cross-origin redirect from Spotify and carries a single-use code).
_PUBLIC_PATHS = frozenset({
    "/login", "/api/auth/login", "/api/auth/status", "/spotify/callback",
})

# Basic per-IP brute-force throttle for the login endpoint.
_login_lock = threading.Lock()
_login_fails: dict = {}            # ip -> [count, window_start_ts]
_LOGIN_MAX = 8
_LOGIN_WINDOW = 300               # seconds


def _hash_password(password: str, salt_hex: str, iters: int) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt_hex), iters).hex()


def _password_set(cfg: dict) -> bool:
    a = cfg.get("auth") or {}
    return bool(a.get("password_hash") and a.get("salt"))


def _verify_password(cfg: dict, password: str) -> bool:
    a = cfg.get("auth") or {}
    if not _password_set(cfg) or not password:
        return False
    try:
        calc = _hash_password(password, a["salt"], int(a.get("iterations") or _PBKDF2_ITERS))
    except Exception:
        return False
    return hmac.compare_digest(calc, str(a.get("password_hash", "")))


def _make_session_token(cfg: dict) -> str:
    a = cfg.get("auth") or {}
    secret = str(a.get("session_secret") or "")
    days = int(a.get("session_days") or 7)
    exp = int(time.time()) + days * 86400
    sig = hmac.new(secret.encode(), str(exp).encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def _valid_session_token(cfg: dict, token: str) -> bool:
    a = cfg.get("auth") or {}
    secret = str(a.get("session_secret") or "")
    if not secret or not token:
        return False
    try:
        exp_s, sig = token.split(".", 1)
        exp = int(exp_s)
    except (ValueError, AttributeError):
        return False
    if exp < time.time():
        return False
    good = hmac.new(secret.encode(), exp_s.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(good, sig)


def _session_max_age(cfg: dict) -> int:
    return int((cfg.get("auth") or {}).get("session_days") or 7) * 86400


def _login_allowed(ip: str) -> bool:
    now = time.time()
    with _login_lock:
        rec = _login_fails.get(ip)
        if not rec or now - rec[1] > _LOGIN_WINDOW:
            return True
        return rec[0] < _LOGIN_MAX


def _login_record_fail(ip: str) -> None:
    now = time.time()
    with _login_lock:
        rec = _login_fails.get(ip)
        if not rec or now - rec[1] > _LOGIN_WINDOW:
            _login_fails[ip] = [1, now]
        else:
            rec[0] += 1


def _login_reset(ip: str) -> None:
    with _login_lock:
        _login_fails.pop(ip, None)


_LOGIN_PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Sign in · work-pi</title>
<style>
body{font-family:system-ui,sans-serif;background:#111;color:#eee;display:flex;
min-height:100vh;align-items:center;justify-content:center;margin:0}
form{background:#1b1b1b;padding:32px;border-radius:14px;width:300px;box-shadow:0 8px 40px #000}
h2{color:#1db954;margin:0 0 18px}
input{width:100%;box-sizing:border-box;padding:11px;margin:6px 0 14px;border-radius:8px;
border:1px solid #333;background:#0e0e0e;color:#eee;font-size:15px}
button{width:100%;padding:12px;border:0;border-radius:8px;background:#1db954;color:#000;
font-weight:600;font-size:15px;cursor:pointer}
.err{color:#ff6b6b;font-size:13px;min-height:18px;margin-top:6px}
.hint{color:#888;font-size:12px;margin-top:14px;text-align:center}
</style></head><body>
<form id=f onsubmit="return false">
<h2>work-pi setup</h2>
<input type=password id=pw placeholder="Password" autofocus autocomplete=current-password>
<button id=go>Sign in</button>
<div class=err id=err></div>
<div class=hint>Stays signed in for up to a week on this device.</div>
</form>
<script>
const go=document.getElementById('go'),pw=document.getElementById('pw'),err=document.getElementById('err');
async function submit(){
  err.textContent='';go.disabled=true;
  try{
    const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({password:pw.value})});
    if(r.ok){location.href='/';return;}
    const d=await r.json().catch(()=>({}));
    err.textContent=d.error||(r.status===429?'Too many attempts. Wait a few minutes.':'Incorrect password');
  }catch(e){err.textContent='Network error';}
  go.disabled=false;pw.select();
}
go.addEventListener('click',submit);
pw.addEventListener('keydown',e=>{if(e.key==='Enter')submit();});
</script></body></html>"""


def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        try:
            s.close()
        except Exception:
            pass


def _in_cgnat(ip: str) -> bool:
    """True if ip is in 100.64.0.0/10 — the CGNAT range Tailscale assigns."""
    try:
        a, b = (int(x) for x in ip.split(".")[:2])
    except (ValueError, IndexError):
        return False
    return a == 100 and 64 <= b <= 127


def _tailscale_ip() -> str | None:
    """Best-effort lookup of this node's Tailscale IPv4, or None if not up."""
    # Canonical source: the tailscale CLI.
    try:
        r = subprocess.run(["tailscale", "ip", "-4"],
                           capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            ip = line.strip()
            if _in_cgnat(ip):
                return ip
    except Exception:
        pass
    # Fallback: scan interface addresses for the CGNAT range (covers setups
    # where the CLI isn't on PATH but the tailscale0 interface is up).
    try:
        r = subprocess.run(["ip", "-4", "-o", "addr", "show"],
                           capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            parts = line.split()
            if "inet" in parts:
                ip = parts[parts.index("inet") + 1].split("/")[0]
                if _in_cgnat(ip):
                    return ip
    except Exception:
        pass
    return None


def _resolve_bind(mode: str) -> tuple:
    """Map a setup_bind setting to (host, human_description).

    'all'       → 0.0.0.0   (LAN + Tailscale + everything; original behaviour)
    'tailscale' → the node's Tailscale IP only (not reachable from the LAN)
    'localhost' → 127.0.0.1 (on-device only)
    <ip>        → that exact address

    'tailscale' fails safe: if no Tailscale address is found it binds to
    localhost rather than silently falling back to LAN-wide exposure.
    """
    m = (mode or "all").strip().lower()
    if m in ("", "all", "0.0.0.0", "any"):
        return "0.0.0.0", "all interfaces (LAN + Tailscale)"
    if m in ("localhost", "loopback", "127.0.0.1"):
        return "127.0.0.1", "localhost only"
    if m == "tailscale":
        ip = _tailscale_ip()
        if ip:
            return ip, f"Tailscale only ({ip})"
        print("[setup] WARNING: setup_bind=tailscale but no Tailscale IP found; "
              "binding to localhost to avoid exposing the LAN.")
        return "127.0.0.1", "Tailscale unavailable — localhost only"
    return mode, f"custom address ({mode})"


# ── WiFi helpers (nmcli) ──────────────────────────────────────────────────────────────

_NM_UNMANAGED_CONF = "/etc/NetworkManager/conf.d/99-unmanaged-wifi.conf"


def _remove_unmanaged_override():
    """Delete the install-time NM unmanaged override so NM manages WiFi going forward."""
    try:
        if os.path.exists(_NM_UNMANAGED_CONF):
            os.remove(_NM_UNMANAGED_CONF)
            subprocess.run(["systemctl", "reload", "NetworkManager"],
                           capture_output=True, timeout=10)
    except Exception as exc:
        print(f"[setup] could not remove unmanaged override: {exc}")


def _wifi_scan() -> list[dict]:
    """Return available WiFi networks via nmcli."""
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "yes"],
            capture_output=True, text=True, timeout=15,
        )
        networks = []
        seen = set()
        for line in result.stdout.splitlines():
            parts = line.split(":")
            if len(parts) >= 3:
                ssid     = parts[0].strip()
                signal   = parts[1].strip()
                security = parts[2].strip()
                if ssid and ssid not in seen:
                    seen.add(ssid)
                    networks.append({
                        "ssid":     ssid,
                        "signal":   int(signal) if signal.isdigit() else 0,
                        "security": security or "Open",
                    })
        networks.sort(key=lambda n: -n["signal"])
        return networks
    except Exception as exc:
        return [{"error": str(exc)}]


def _wifi_connect(ssid: str, password: str) -> dict:
    """Connect to a WiFi network via nmcli."""
    try:
        cmd = ["nmcli", "device", "wifi", "connect", ssid]
        if password:
            cmd += ["password", password]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return {"status": "connected", "ssid": ssid}
        return {"status": "error", "message": result.stderr.strip() or result.stdout.strip()}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def _wifi_status() -> dict:
    """Return current WiFi connection status."""
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi", "list"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            parts = line.split(":", 1)
            if len(parts) == 2 and parts[0] == "yes" and parts[1].strip():
                return {"status": "connected", "ssid": parts[1].strip()}
        return {"status": "disconnected"}
    except Exception as exc:
        return {"status": "unknown", "message": str(exc)}


# ── Demo pages for editor preview ──────────────────────────────────────────────────

# Sample clear-day sky gradient so the editor preview reflects the live
# weather-reactive background (see pages._sky_gradient).
_DEMO_BG = [[18, 44, 92], [44, 92, 158]]

_DEMO_PAGES: dict = {
    "forecast": {
        "_name": "forecast",
        "title": "Forecast",
        "bg": _DEMO_BG,
        "lines": [
            {"text": "72°F",                  "size": 3, "color": "white"},
            {"text": "Partly Cloudy",          "size": 1, "color": "white",    "left_align": True},
            {"text": "Hi 78°  Lo 61°",         "size": 1, "color": "cyan",     "left_align": True},
            {"text": "20% chance of rain",     "size": 1, "color": "white",    "left_align": True},
            {"text": "Hum: 65%",               "size": 0, "color": "cyan",     "left_align": True},
            {"text": "8mph W",                 "size": 0, "color": "white",    "left_align": True},
            {"text": "becoming sunny later",   "size": 0, "color": "darkgrey", "left_align": True},
        ],
        "hourly_grid": [
            {"label": "1pm", "temp": "70°", "rain": "15%", "rain_color": "white"},
            {"label": "3pm", "temp": "72°", "rain": "10%", "rain_color": "white"},
            {"label": "5pm", "temp": "71°", "rain": "5%",  "rain_color": "darkgrey"},
            {"label": "7pm", "temp": "68°", "rain": "0%",  "rain_color": "darkgrey"},
            {"label": "9pm", "temp": "65°", "rain": "0%",  "rain_color": "darkgrey"},
        ],
        "weather_icon": "partly_cloudy",
        "aqi_overlay": {"value": 42, "color": "green"},
    },
    "forecast_stale": {
        "_name": "forecast",
        "title": "Forecast",
        "bg": _DEMO_BG,
        "lines": [
            {"text": "72°F",                  "size": 3, "color": "white"},
            {"text": "Partly Cloudy",          "size": 1, "color": "white",    "left_align": True},
            {"text": "Hi 78°  Lo 61°",         "size": 1, "color": "cyan",     "left_align": True},
            {"text": "20% chance of rain",     "size": 1, "color": "white",    "left_align": True},
            {"text": "Hum: 65%",               "size": 0, "color": "cyan",     "left_align": True},
            {"text": "8mph W",                 "size": 0, "color": "white",    "left_align": True},
            {"text": "becoming sunny later",   "size": 0, "color": "darkgrey", "left_align": True},
        ],
        "hourly_grid": [
            {"label": "1pm", "temp": "70°", "rain": "15%", "rain_color": "white"},
            {"label": "3pm", "temp": "72°", "rain": "10%", "rain_color": "white"},
            {"label": "5pm", "temp": "71°", "rain": "5%",  "rain_color": "darkgrey"},
            {"label": "7pm", "temp": "68°", "rain": "0%",  "rain_color": "darkgrey"},
            {"label": "9pm", "temp": "65°", "rain": "0%",  "rain_color": "darkgrey"},
        ],
        "weather_icon": "partly_cloudy",
        "aqi_overlay": {"value": 42, "color": "green"},
        "stale": True,
    },
    "calendar": {
        "_name": "calendar",
        "title": "Calendar",
        # Urgency vignette (~15 min out — warm; see pages._ramp / _WARM_VIG).
        "bg_vignette": [[160, 62, 29], [0, 0, 0]],
        "lines": [
            {"text": "Design Review",          "color": "white", "wrap": True},
            {"text": "in 15 min",              "size": 3, "color": "red"},
            {"text": "2:00 - 3:00 PM",         "size": 1, "color": "grey"},
            {"text": "Conference Room A",       "size": 0, "color": "grey"},
            {"text": "Then: 1:1 with Manager", "color": "grey"},
        ],
    },
    "calendar_empty": {
        "_name": "calendar_empty",
        "title": "Calendar",
        "bg": _DEMO_BG,   # empty state falls back to the shared sky gradient
        "lines": [
            {"text": "No upcoming",             "size": 1, "color": "darkgrey"},
            {"text": "events today",            "size": 1, "color": "darkgrey"},
            {"text": "Next: Electrical Meeting","color": "white"},
            {"text": "Mon. 9:00 - 10:00 AM",   "size": 1, "color": "grey"},
            {"text": "Then: Team Standup",      "color": "grey"},
            {"text": "Tue. 8:30 - 8:45 AM",    "size": 1, "color": "grey"},
        ],
    },
    "commute": {
        "_name": "commute",
        "title": "Commute Home",
        # Traffic vignette (~+8 min on the worst route — warm; see pages._ramp).
        "bg_vignette": [[159, 66, 28], [0, 0, 0]],
        "lines": [
            {"text": "Work → Home",              "size": 1, "color": "white"},
            {"text": "24 min",                   "size": 2, "color": "green"},
            {"text": "Via I-95",                 "size": 0, "color": "darkgrey"},
            {"text": "Work → Waypoint → Home",   "size": 1, "color": "white"},
            {"text": "31 min",                   "size": 2, "color": "yellow"},
            {"text": "+8 min (traffic)",          "size": 0, "color": "yellow"},
        ],
    },
    "wfh": {
        "_name": "wfh",
        "title": "Working From Home",
        "lines": [{"text": "Working From Home", "size": 3, "color": "white"}],
    },
    "ooo": {
        "_name": "ooo",
        "title": "Out of Office",
        "lines": [
            {"text": "Out of Office",        "size": 3, "color": "white"},
            {"text": "Returning Mon Jan 20", "size": 1, "color": "cyan"},
        ],
    },
    "holiday": {
        "_name": "holiday",
        "title": "Holiday",
        "lines": [{"text": "Martin Luther King Day", "size": 3, "color": "white"}],
    },
    "spotify": {
        "_name":       "spotify",
        "title":       "Now Playing",
        "track":       "A Really Long Song Title That Will Scroll Across The Screen",
        "artist":      "Artist Name",
        "album":       "Album Name",
        "art_url":     None,
        "progress_ms": 75000,
        "duration_ms": 210000,
    },
}


def _render_preview(page_name: str, posted_layout: dict,
                    icon: str | None, scale: int) -> tuple:
    """Build a demo page dict, render with PIL, return (PNG bytes, line_centers)."""
    from render import render_page_pil, LAYOUT_DEFAULTS
    from PIL import Image

    layout = copy.deepcopy(LAYOUT_DEFAULTS)
    for k, v in posted_layout.items():
        if isinstance(v, dict) and k in layout and isinstance(layout[k], dict):
            layout[k].update(v)
        else:
            layout[k] = v

    try:
        cfg = cfg_module.load()
        layout["font"]["path"] = cfg_module.resolve_font_path(cfg)
    except Exception:
        pass

    if page_name == "clock":
        from pages import build_clock_page
        page = build_clock_page()
    elif page_name in _DEMO_PAGES:
        page = copy.deepcopy(_DEMO_PAGES[page_name])
    else:
        page = {"_name": page_name, "title": page_name.title(), "lines": []}

    if icon:
        page["weather_icon"] = icon

    info: dict = {}
    img = render_page_pil(page, layout, _out=info)
    if scale > 1:
        resample = getattr(Image, "Resampling", Image).NEAREST
        img = img.resize((img.width * scale, img.height * scale), resample)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), info.get("line_centers")


# ── HTTP handler ────────────────────────────────────────────────────────────────────

class SetupHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"[setup] {self.address_string()} {fmt % args}")

    def _send_json(self, data: dict, status: int = 200, set_cookie: str = None):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(body)

    # ── auth helpers ──────────────────────────────────────────────────────────
    def _cookies(self) -> dict:
        jar = {}
        for part in (self.headers.get("Cookie") or "").split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                jar[k] = v
        return jar

    def _is_authed(self, cfg: dict) -> bool:
        # No password configured yet → leave the server open so first-run setup
        # (including setting the password) is possible.
        if not _password_set(cfg):
            return True
        return _valid_session_token(cfg, self._cookies().get(_SESSION_COOKIE, ""))

    def _gate(self, path: str) -> bool:
        """Return True if the request may proceed; otherwise emit a 401/redirect."""
        if path in _PUBLIC_PATHS:
            return True
        if self._is_authed(cfg_module.load()):
            return True
        # API/asset calls get a clean 401; browser page loads get redirected.
        if path.startswith("/api/") or path.startswith("/work/") or path.startswith("/spotify/"):
            self._send_json({"error": "auth required"}, 401)
        else:
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()
        return False

    def _session_cookie(self, cfg: dict) -> str:
        return (f"{_SESSION_COOKIE}={_make_session_token(cfg)}; HttpOnly; "
                f"SameSite=Lax; Path=/; Max-Age={_session_max_age(cfg)}")

    def _send_html_str(self, html: str, status: int = 200):
        body = f"<!doctype html><html><head><meta charset=utf-8><style>body{{font-family:sans-serif;padding:40px;background:#111;color:#eee}}h2{{color:#1db954}}</style></head><body>{html}</body></html>".encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html_str_raw(self, html: str, status: int = 200):
        body = html.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, path: str):
        try:
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_error(404, "Setup UI not found")

    def _send_file(self, abs_path: str, root: str = _BASE):
        # Confine the resolved path to `root` so `..` traversal can't escape the
        # served directory (e.g. /editor/../config.json reaching secrets).
        real      = os.path.realpath(abs_path)
        root_real = os.path.realpath(root)
        if real != root_real and not real.startswith(root_real + os.sep):
            self.send_response(403); self.end_headers(); return
        abs_path = real
        if not os.path.isfile(abs_path):
            self.send_error(404); return
        mime, _ = mimetypes.guess_type(abs_path)
        mime = mime or "application/octet-stream"
        with open(abs_path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    # Largest legitimate body is a custom-image upload; cap well below what
    # could exhaust the Pi's 512 MB of RAM (body is buffered in full).
    _MAX_BODY_BYTES = 8 * 1024 * 1024

    def _read_body(self) -> bytes | None:
        """Read the request body, or send a 413 and return None if oversized."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > self._MAX_BODY_BYTES:
            self._send_json({"error": "Request body too large"}, 413)
            return None
        return self.rfile.read(length) if length > 0 else b""

    def _send_icon(self, icon_name: str):
        """Render a weather-icon thumbnail PNG (used by the layout editor picker)."""
        try:
            from PIL import Image, ImageDraw
            from render import _draw_weather_icon
            SIZE = 48
            img = Image.new("RGB", (SIZE, SIZE), (18, 18, 18))
            draw = ImageDraw.Draw(img)
            _draw_weather_icon(img, draw, icon_name, SIZE // 2, SIZE // 2, SIZE // 2 - 3)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            png = buf.getvalue()
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(png)))
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        self.wfile.write(png)

    def do_GET(self):
        path = self.path.split("?")[0]
        if not self._gate(path):
            return

        if path == "/login":
            cfg = cfg_module.load()
            # Nothing to log into until a password exists, and an existing session
            # shouldn't see the login form — bounce both straight to the app.
            if not _password_set(cfg) or self._is_authed(cfg):
                self.send_response(302); self.send_header("Location", "/"); self.end_headers()
            else:
                self._send_html_str_raw(_LOGIN_PAGE)

        elif path in ("/", "/setup"):
            self._send_html(_SETUP_HTML)

        elif path == "/api/auth/status":
            cfg = cfg_module.load()
            self._send_json({"password_set": _password_set(cfg),
                             "authed": self._is_authed(cfg)})

        elif path == "/api/config":
            cfg = _mask_secrets(cfg_module.load())
            cfg.pop("auth", None)   # never expose password hash / session secret
            self._send_json(cfg)

        elif path == "/api/wifi/scan":
            self._send_json({"networks": _wifi_scan()})

        elif path == "/api/wifi/status":
            self._send_json(_wifi_status())

        elif path == "/api/local_ip":
            self._send_json({"ip": _get_local_ip()})

        elif path == "/api/custom-images":
            filenames = []
            if os.path.isdir(_CUSTOM_IMAGES_DIR):
                for fname in sorted(os.listdir(_CUSTOM_IMAGES_DIR)):
                    ext = os.path.splitext(fname)[1].lower()
                    if ext in _ALLOWED_IMAGE_EXTS:
                        filenames.append(fname)
            self._send_json({"images": filenames})

        elif path == "/api/geocode":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            q  = (qs.get("q") or [""])[0].strip()
            if not q:
                self._send_json({"error": "q parameter required"}, 400)
                return
            try:
                url = ("https://nominatim.openstreetmap.org/search"
                       f"?format=json&q={urllib.parse.quote(q)}&limit=1")
                req = urllib.request.Request(
                    url, headers={"User-Agent": "work-pi-dashboard/1.0"})
                with urllib.request.urlopen(req, timeout=6) as resp:
                    data = json.loads(resp.read())
                if data:
                    lat = float(data[0]["lat"])
                    lon = float(data[0]["lon"])
                    result = {
                        "lat": lat,
                        "lon": lon,
                        "display_name": data[0].get("display_name", ""),
                    }
                    # Auto-detect timezone from coordinates
                    try:
                        tz_url = (f"https://timeapi.io/api/timezone/coordinate"
                                  f"?latitude={lat}&longitude={lon}")
                        tz_req = urllib.request.Request(
                            tz_url, headers={"User-Agent": "work-pi-dashboard/1.0"})
                        with urllib.request.urlopen(tz_req, timeout=6) as tz_resp:
                            tz_data = json.loads(tz_resp.read())
                        tz = tz_data.get("timeZone", "")
                        if tz:
                            result["timezone"] = tz
                    except Exception:
                        pass  # timezone lookup is best-effort
                    self._send_json(result)
                else:
                    self._send_json({"error": "Location not found"}, 404)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 502)

        elif path in ("/editor", "/editor/", "/editor/work", "/editor/work/"):
            self._send_file(os.path.join(_EDITOR_DIR, "work", "index.html"), _EDITOR_DIR)

        elif path.startswith("/work/editor/"):
            rel = path[len("/work/editor/"):]
            self._send_file(os.path.join(_EDITOR_DIR, "work", rel), _EDITOR_DIR)

        elif path.startswith("/editor/"):
            rel = path[len("/editor/"):]
            self._send_file(os.path.join(_EDITOR_DIR, rel), _EDITOR_DIR)

        elif path.startswith("/icons/"):
            rel = path[len("/icons/"):]
            self._send_file(os.path.join(_ICONS_DIR, rel), _ICONS_DIR)

        elif path.startswith("/work/icon/"):
            self._send_icon(path[len("/work/icon/"):])

        elif path == "/api/screenshot":
            try:
                cfg = cfg_module.load()
                fb_path = cfg["display"]["framebuffer"]
                W = cfg["display"]["width"]
                H = cfg["display"]["height"]
                with open(fb_path, "rb") as f:
                    raw = f.read(W * H * 2)
                from PIL import Image
                img = Image.new("RGB", (W, H))
                pixels = []
                for i in range(W * H):
                    lo = raw[i * 2]
                    hi = raw[i * 2 + 1]
                    p = lo | (hi << 8)
                    r = (p >> 8) & 0xF8
                    g = (p >> 3) & 0xFC
                    b = (p << 3) & 0xF8
                    pixels.append((r, g, b))
                img.putdata(pixels)
                if cfg["display"].get("rotation", 0) == 180:
                    img = img.rotate(180)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                png = buf.getvalue()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(png)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(png)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)

        elif path == "/work/layout":
            from render import LAYOUT_DEFAULTS
            try:
                with open(_LAYOUT_FILE) as f:
                    saved = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                saved = {}
            merged = copy.deepcopy(LAYOUT_DEFAULTS)
            for k, v in saved.items():
                if isinstance(v, dict) and k in merged and isinstance(merged[k], dict):
                    merged[k].update(v)
                else:
                    merged[k] = v
            self._send_json(merged)

        elif path == "/api/spotify/redirect-uri":
            cfg  = cfg_module.load()
            port = cfg.get("setup_port", 8080)
            self._send_json({"redirect_uri": f"http://127.0.0.1:{port}/spotify/callback"})

        elif path == "/api/spotify/status":
            cfg = cfg_module.load()
            sp  = cfg.get("spotify") or {}
            connected = bool(sp.get("refresh_token", "").strip())
            self._send_json({"connected": connected,
                             "has_credentials": bool(sp.get("client_id") and sp.get("client_secret"))})

        elif path == "/api/spotify/auth-url":
            cfg = cfg_module.load()
            sp  = cfg.get("spotify") or {}
            client_id = sp.get("client_id", "").strip()
            if not client_id:
                self._send_json({"error": "client_id not set"}, 400)
                return
            port = cfg.get("setup_port", 8080)
            redirect_uri = f"http://127.0.0.1:{port}/spotify/callback"
            params = urllib.parse.urlencode({
                "client_id":     client_id,
                "response_type": "code",
                "redirect_uri":  redirect_uri,
                "scope":         "user-read-currently-playing user-read-playback-state",
            })
            self._send_json({"auth_url": f"https://accounts.spotify.com/authorize?{params}",
                             "redirect_uri": redirect_uri})

        elif path == "/spotify/callback":
            qs   = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code = (qs.get("code") or [None])[0]
            err  = (qs.get("error") or [None])[0]
            if err or not code:
                self._send_html_str(
                    f"<h2>Spotify auth failed</h2><p>{err or 'No code'}</p><p>Close this tab and try again.</p>")
                return
            cfg = cfg_module.load()
            sp  = cfg.get("spotify") or {}
            client_id     = sp.get("client_id", "").strip()
            client_secret = sp.get("client_secret", "").strip()
            port = cfg.get("setup_port", 8080)
            redirect_uri = f"http://127.0.0.1:{port}/spotify/callback"
            try:
                import base64 as _b64
                creds = _b64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
                r = urllib.request.Request(
                    "https://accounts.spotify.com/api/token",
                    data=urllib.parse.urlencode({
                        "grant_type":   "authorization_code",
                        "code":         code,
                        "redirect_uri": redirect_uri,
                    }).encode(),
                    headers={"Authorization": f"Basic {creds}",
                             "Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )
                with urllib.request.urlopen(r, timeout=10) as resp:
                    tokens = json.loads(resp.read())
                cfg.setdefault("spotify", {})["refresh_token"] = tokens["refresh_token"]
                cfg_module.save(cfg)
                self._send_html_str(
                    "<h2 style='color:#1db954'>&#x2713; Spotify connected!</h2>"
                    "<p>Your account is linked. You can close this tab.</p>"
                    "<script>setTimeout(()=>window.close(),2000)</script>")
            except Exception as exc:
                self._send_html_str(
                    f"<h2>Token exchange failed</h2><pre>{exc}</pre><p>Close this tab and try again.</p>")

        else:
            self.send_error(404)

    def do_POST(self):
        path = self.path.split("?")[0]
        if not self._gate(path):
            return
        body = self._read_body()
        if body is None:   # oversized — 413 already sent
            return

        if path == "/api/auth/login":
            ip = self.client_address[0]
            if not _login_allowed(ip):
                self._send_json({"error": "Too many attempts"}, 429)
                return
            try:
                password = json.loads(body or b"{}").get("password", "")
            except (json.JSONDecodeError, AttributeError):
                self._send_json({"error": "Invalid JSON"}, 400)
                return
            cfg = cfg_module.load()
            if not _password_set(cfg):
                self._send_json({"error": "No password configured"}, 400)
                return
            if _verify_password(cfg, password):
                _login_reset(ip)
                self._send_json({"status": "ok"}, set_cookie=self._session_cookie(cfg))
            else:
                _login_record_fail(ip)
                self._send_json({"error": "Incorrect password"}, 401)
            return

        elif path == "/api/auth/logout":
            self._send_json(
                {"status": "ok"},
                set_cookie=f"{_SESSION_COOKIE}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0")
            return

        elif path == "/api/auth/set-password":
            try:
                data = json.loads(body or b"{}")
            except json.JSONDecodeError:
                self._send_json({"error": "Invalid JSON"}, 400)
                return
            new = str(data.get("new", ""))
            current = str(data.get("current", ""))
            cfg = cfg_module.load()
            # Changing an existing password requires the current one (a stolen
            # session alone shouldn't be enough to lock the owner out).
            if _password_set(cfg) and not _verify_password(cfg, current):
                self._send_json({"error": "Current password is incorrect"}, 403)
                return
            if len(new) < _MIN_PASSWORD_LEN:
                self._send_json(
                    {"error": f"Password must be at least {_MIN_PASSWORD_LEN} characters"}, 400)
                return
            salt = secrets.token_hex(16)
            cfg.setdefault("auth", {})
            cfg["auth"]["salt"] = salt
            cfg["auth"]["iterations"] = _PBKDF2_ITERS
            cfg["auth"]["password_hash"] = _hash_password(new, salt, _PBKDF2_ITERS)
            # Rotate the signing secret so all previously issued sessions are killed.
            cfg["auth"]["session_secret"] = secrets.token_hex(32)
            cfg["auth"].setdefault("session_days", 7)
            cfg_module.save(cfg)
            # Re-issue a fresh cookie so the caller stays signed in.
            self._send_json({"status": "ok"}, set_cookie=self._session_cookie(cfg))
            return

        if path == "/api/config":
            try:
                incoming = json.loads(body)
            except json.JSONDecodeError:
                self._send_json({"error": "Invalid JSON"}, 400)
                return

            incoming.pop("auth", None)   # auth is managed only via /api/auth/*
            _strip_secret_sentinels(incoming)
            current = cfg_module.load()
            for k, v in incoming.items():
                if isinstance(v, dict) and k in current and isinstance(current[k], dict):
                    current[k].update(v)
                else:
                    current[k] = v

            cfg_module.save(current)
            complete = cfg_module.is_complete(current)
            # Sync Pi system timezone to match configured timezone
            tz_synced = False
            tz = current.get("location", {}).get("timezone", "").strip()
            if tz:
                try:
                    r = subprocess.run(
                        ["timedatectl", "set-timezone", tz],
                        capture_output=True, timeout=5)
                    tz_synced = r.returncode == 0
                except Exception:
                    pass
            self._send_json({"status": "saved", "complete": complete, "tz_synced": tz_synced})
            # Make sure the full response reaches the browser before anything
            # waiting on config_saved (e.g. the live-apply restart watcher) can
            # tear the process down mid-flush — otherwise the client sees the
            # connection reset and reports a spurious "Save failed".
            try:
                self.wfile.flush()
            except Exception:
                pass
            if complete:
                config_saved.set()

        elif path == "/api/spotify/exchange-code":
            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                self._send_json({"error": "Invalid JSON"}, 400)
                return
            code = data.get("code", "").strip()
            if not code:
                self._send_json({"error": "code required"}, 400)
                return
            cfg = cfg_module.load()
            sp  = cfg.get("spotify") or {}
            client_id     = sp.get("client_id", "").strip()
            client_secret = sp.get("client_secret", "").strip()
            port = cfg.get("setup_port", 8080)
            redirect_uri = f"http://127.0.0.1:{port}/spotify/callback"
            try:
                import base64 as _b64
                creds = _b64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
                r = urllib.request.Request(
                    "https://accounts.spotify.com/api/token",
                    data=urllib.parse.urlencode({
                        "grant_type":   "authorization_code",
                        "code":         code,
                        "redirect_uri": redirect_uri,
                    }).encode(),
                    headers={"Authorization": f"Basic {creds}",
                             "Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )
                with urllib.request.urlopen(r, timeout=10) as resp:
                    tokens = json.loads(resp.read())
                cfg.setdefault("spotify", {})["refresh_token"] = tokens["refresh_token"]
                cfg_module.save(cfg)
                self._send_json({"status": "connected"})
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)

        elif path == "/api/wifi/connect":
            try:
                data = json.loads(body)
                ssid     = data.get("ssid", "").strip()
                password = data.get("password", "")
            except (json.JSONDecodeError, AttributeError):
                self._send_json({"error": "Invalid JSON"}, 400)
                return
            if not ssid:
                self._send_json({"error": "ssid required"}, 400)
                return
            result = _wifi_connect(ssid, password)
            if result.get("status") == "connected":
                try:
                    current = cfg_module.load()
                    current["wifi"]["ssid"]     = ssid
                    current["wifi"]["password"] = password
                    cfg_module.save(current)
                except Exception:
                    pass
                _remove_unmanaged_override()
            self._send_json(result)

        elif path == "/api/upload-image":
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                self._send_json({"error": "multipart/form-data required"}, 400)
                return
            # Parse multipart body using email stdlib
            # Reconstruct a full MIME message so email.parser can handle it
            msg_bytes = (
                f"Content-Type: {content_type}\r\n\r\n".encode() + body
            )
            msg = email.parser.BytesParser().parsebytes(msg_bytes)
            file_data = None
            file_name = None
            for part in msg.walk():
                cd = part.get("Content-Disposition", "")
                if 'name="image"' in cd or "name=image" in cd:
                    raw_fn = part.get_filename() or ""
                    file_name = os.path.basename(raw_fn)
                    file_data = part.get_payload(decode=True)
                    break
            if not file_data or not file_name:
                self._send_json({"error": "No image file in request"}, 400)
                return
            ext = os.path.splitext(file_name)[1].lower()
            if ext not in _ALLOWED_IMAGE_EXTS:
                self._send_json({"error": f"Unsupported file type: {ext}"}, 400)
                return
            os.makedirs(_CUSTOM_IMAGES_DIR, exist_ok=True)
            dest = os.path.join(_CUSTOM_IMAGES_DIR, file_name)
            with open(dest, "wb") as f:
                f.write(file_data)
            self._send_json({"status": "saved", "filename": file_name})

        elif path == "/api/delete-image":
            try:
                filename = json.loads(body).get("filename", "")
            except (json.JSONDecodeError, AttributeError):
                self._send_json({"error": "Invalid JSON"}, 400)
                return
            if not filename or os.sep in filename or filename.startswith("."):
                self._send_json({"error": "Invalid filename"}, 400)
                return
            target = os.path.join(_CUSTOM_IMAGES_DIR, filename)
            if not os.path.normpath(target).startswith(os.path.normpath(_CUSTOM_IMAGES_DIR)):
                self._send_json({"error": "Invalid filename"}, 400)
                return
            if not os.path.isfile(target):
                self._send_json({"error": "File not found"}, 404)
                return
            os.remove(target)
            self._send_json({"status": "deleted", "filename": filename})

        elif path == "/work/layout/save":
            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                self._send_json({"error": "Invalid JSON"}, 400)
                return
            dir_ = os.path.dirname(os.path.abspath(_LAYOUT_FILE))
            os.makedirs(dir_, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp, _LAYOUT_FILE)
            except Exception as exc:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                self._send_json({"error": str(exc)}, 500)
                return
            try:
                from render import invalidate_layout_cache
                invalidate_layout_cache()
            except Exception:
                pass
            self._send_json({"ok": True})

        elif path.startswith("/work/preview/"):
            page_name = path[len("/work/preview/"):]
            qs = urllib.parse.parse_qs(
                self.path.split("?", 1)[1] if "?" in self.path else ""
            )
            scale = int(qs.get("scale", ["1"])[0])
            icon_list = qs.get("icon", [])
            icon = icon_list[0] if icon_list else None
            try:
                posted_layout = json.loads(body) if body else {}
            except json.JSONDecodeError:
                posted_layout = {}
            try:
                png, line_centers = _render_preview(page_name, posted_layout, icon, scale)
            except Exception as exc:
                print(f"[preview] {page_name}: {exc}")
                self._send_json({"error": str(exc)}, 500)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.send_header("Cache-Control", "no-cache")
            # Exact center-Y the renderer used for each line, so the editor can
            # freeze auto positions without re-deriving the layout math.
            if line_centers is not None:
                self.send_header("X-Line-Centers", json.dumps(line_centers))
            self.end_headers()
            self.wfile.write(png)

        else:
            self.send_error(404)

    def do_OPTIONS(self):
        # The setup UI is served same-origin, so no cross-origin access is granted.
        # Combined with SameSite=Lax session cookies this blocks CSRF from other sites.
        self.send_response(204)
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.end_headers()


# ── Server lifecycle ────────────────────────────────────────────────────────────────

_server: HTTPServer | None = None


def start(port: int) -> str:
    """Start the setup HTTP server in a daemon thread.

    Binds according to config `setup_bind` (default 'all'). Returns the address
    to advertise — the bound IP for a specific bind, else the LAN IP.
    """
    global _server
    if _server is not None:
        return _get_local_ip()
    mode = (cfg_module.load().get("setup_bind") or "all")
    host, desc = _resolve_bind(mode)
    HTTPServer.allow_reuse_address = True
    try:
        _server = HTTPServer((host, port), SetupHandler)
    except OSError as exc:
        # e.g. the chosen address vanished (Tailscale dropped) — fail safe to
        # localhost rather than the whole LAN.
        print(f"[setup] could not bind {host}:{port} ({exc}); using 127.0.0.1")
        host, desc = "127.0.0.1", "localhost only (bind fallback)"
        _server = HTTPServer((host, port), SetupHandler)

    def _run():
        print(f"[setup] listening on {host}:{port} — {desc}")
        _server.serve_forever()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    # Advertise the actual bound IP when it's a concrete address; for 0.0.0.0
    # fall back to the routable LAN IP so the on-screen URL is reachable.
    return host if host not in ("0.0.0.0",) else _get_local_ip()


def stop():
    global _server
    if _server:
        _server.shutdown()
        _server = None
