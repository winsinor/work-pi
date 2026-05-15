"""Embedded HTTP server for web-based configuration and WiFi management."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import config as cfg_module

_BASE = os.path.dirname(os.path.abspath(__file__))
_SETUP_HTML = os.path.join(_BASE, "setup", "index.html")

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
            ["nmcli", "-t", "-f", "NAME,TYPE,STATE,CONNECTION", "connection", "show", "--active"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            parts = line.split(":")
            if len(parts) >= 3 and "wireless" in parts[1]:
                return {"status": "connected", "ssid": parts[0], "state": parts[2]}
        return {"status": "disconnected"}
    except Exception as exc:
        return {"status": "unknown", "message": str(exc)}


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

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length > 0 else b""

    def do_GET(self):
        path = self.path.split("?")[0]

        if path in ("/", "/setup"):
            self._send_html(_SETUP_HTML)

        elif path == "/api/config":
            self._send_json(cfg_module.load())

        elif path == "/api/wifi/scan":
            self._send_json({"networks": _wifi_scan()})

        elif path == "/api/wifi/status":
            self._send_json(_wifi_status())

        elif path == "/api/local_ip":
            self._send_json({"ip": _get_local_ip()})

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
            self._send_json(result)

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
