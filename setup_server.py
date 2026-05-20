"""Embedded HTTP server for web-based configuration and WiFi management."""
from __future__ import annotations

import copy
import email.parser
import io
import json
import mimetypes
import os
import socket
import subprocess
import tempfile
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import config as cfg_module

_BASE = os.path.dirname(os.path.abspath(__file__))
_SETUP_HTML       = os.path.join(_BASE, "setup", "index.html")
_SCREENSHOT_HTML  = os.path.join(_BASE, "setup", "screenshot.html")
_CUSTOM_IMAGES_DIR = os.path.join(_BASE, "custom_images")
_ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp"}
_EDITOR_DIR  = os.path.join(_BASE, "editor")
_ICONS_DIR   = os.path.join(_BASE, "icons")
_LAYOUT_FILE = os.path.join(_BASE, "work_layout.json")

# Event signalled when user saves a valid config via the web UI
config_saved = threading.Event()


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

_DEMO_PAGES: dict = {
    "forecast": {
        "_name": "forecast",
        "title": "Forecast",
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
    "calendar": {
        "_name": "calendar",
        "title": "Calendar",
        "lines": [
            {"text": "Design Review",          "color": "white", "wrap": True},
            {"text": "in 15 min",              "size": 3, "color": "red"},
            {"text": "2:00 - 3:00 PM",         "size": 1, "color": "grey"},
            {"text": "Then: 1:1 with Manager", "color": "grey"},
        ],
    },
    "calendar_empty": {
        "_name": "calendar_empty",
        "title": "Calendar",
        "lines": [
            {"text": "No upcoming",  "size": 1, "color": "darkgrey"},
            {"text": "events today", "size": 1, "color": "darkgrey"},
        ],
    },
    "commute": {
        "_name": "commute",
        "title": "Commute Home",
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
}


def _render_preview(page_name: str, posted_layout: dict,
                    icon: str | None, scale: int) -> bytes:
    """Build a demo page dict, render with PIL, return PNG bytes."""
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

    img = render_page_pil(page, layout)
    if scale > 1:
        resample = getattr(Image, "Resampling", Image).NEAREST
        img = img.resize((img.width * scale, img.height * scale), resample)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── HTTP handler ────────────────────────────────────────────────────────────────────

class SetupHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"[setup] {self.address_string()} {fmt % args}")

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
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

    def _send_file(self, abs_path: str):
        if not os.path.normpath(abs_path).startswith(os.path.normpath(_BASE)):
            self.send_response(403); self.end_headers(); return
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

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length > 0 else b""

    def do_GET(self):
        path = self.path.split("?")[0]

        if path in ("/", "/setup"):
            self._send_html(_SETUP_HTML)

        elif path == "/screenshot":
            self._send_html(_SCREENSHOT_HTML)

        elif path == "/api/config":
            self._send_json(cfg_module.load())

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
                    self._send_json({
                        "lat": float(data[0]["lat"]),
                        "lon": float(data[0]["lon"]),
                        "display_name": data[0].get("display_name", ""),
                    })
                else:
                    self._send_json({"error": "Location not found"}, 404)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 502)

        elif path in ("/editor", "/editor/", "/editor/work", "/editor/work/"):
            self._send_file(os.path.join(_EDITOR_DIR, "work", "index.html"))

        elif path.startswith("/work/editor/"):
            rel = path[len("/work/editor/"):]
            self._send_file(os.path.join(_EDITOR_DIR, "work", rel))

        elif path.startswith("/editor/"):
            rel = path[len("/editor/"):]
            self._send_file(os.path.join(_EDITOR_DIR, rel))

        elif path.startswith("/icons/"):
            rel = path[len("/icons/"):]
            self._send_file(os.path.join(_ICONS_DIR, rel))

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

        else:
            self.send_error(404)

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._read_body()

        if path == "/api/config":
            try:
                incoming = json.loads(body)
            except json.JSONDecodeError:
                self._send_json({"error": "Invalid JSON"}, 400)
                return

            current = cfg_module.load()
            for k, v in incoming.items():
                if isinstance(v, dict) and k in current and isinstance(current[k], dict):
                    current[k].update(v)
                else:
                    current[k] = v

            cfg_module.save(current)
            complete = cfg_module.is_complete(current)
            self._send_json({"status": "saved", "complete": complete})
            if complete:
                config_saved.set()

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
                png = _render_preview(page_name, posted_layout, icon, scale)
            except Exception as exc:
                print(f"[preview] {page_name}: {exc}")
                self._send_json({"error": str(exc)}, 500)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(png)

        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


# ── Server lifecycle ────────────────────────────────────────────────────────────────

_server: HTTPServer | None = None


def start(port: int) -> str:
    """Start the setup HTTP server in a daemon thread. Returns the local IP."""
    global _server
    if _server is not None:
        return _get_local_ip()
    HTTPServer.allow_reuse_address = True
    _server = HTTPServer(("0.0.0.0", port), SetupHandler)

    def _run():
        print(f"[setup] listening on port {port}")
        _server.serve_forever()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return _get_local_ip()


def stop():
    global _server
    if _server:
        _server.shutdown()
        _server = None
