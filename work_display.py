#!/usr/bin/env python3
"""Work dashboard — standalone display driver.

Fetches data directly (no server), renders pages with PIL, and writes
RGB565 frames to the framebuffer. On first run (no config), shows a
setup URL and starts the web config server.
"""

import os
import queue
import socket
import subprocess
import sys
import threading
import time

# Unbuffered stdout so print() appears immediately in journalctl
sys.stdout.reconfigure(line_buffering=True)

import config as cfg_module
import setup_server
from data import DataStore
from pages import (
    build_setup_page, build_loading_page, build_shutdown_page,
    get_display,
)
from render import (
    load_layout, render_page_rgb565, solid_frame,
    invalidate_layout_cache,
)

# ── Touch support (XPT2046 / evdev). Set True to enable. ─────────────────────
ENABLE_TOUCH   = False
TOUCH_DEVICE   = ""      # empty = auto-detect ads7846/xpt2046
TOUCH_X_MIN    = 200
TOUCH_X_MAX    = 3800
TOUCH_Y_MIN    = 200
TOUCH_Y_MAX    = 3800
TOUCH_SWAP_XY  = True    # swap X↔Y (typical for 90° HAT rotation)
TOUCH_INVERT_X = False
TOUCH_INVERT_Y = False

# ── Command queue — all input sources post here ───────────────────────────────
_cmd: queue.Queue = queue.Queue()

# ── Stale-data tracking ───────────────────────────────────────────────────────
_last_render_ok: float = 0.0
_STALE_PIXEL = bytes([0x20, 0xFD])  # orange in little-endian RGB565


# ── Framebuffer helpers ───────────────────────────────────────────────────────

def _write_frame(data: bytes, fb_path: str):
    try:
        with open(fb_path, "wb") as fb:
            fb.write(data)
    except OSError as exc:
        print(f"[fb] write failed: {exc}")


def _write_stale_stripe(fb_path: str, W: int, H: int):
    """Overwrite the bottom 8 rows with an orange stripe to signal stale data."""
    stripe = _STALE_PIXEL * W * 8
    try:
        with open(fb_path, "r+b") as fb:
            fb.seek(W * (H - 8) * 2)
            fb.write(stripe)
    except OSError as exc:
        print(f"[fb] stale stripe failed: {exc}")


# ── Shutdown ──────────────────────────────────────────────────────────────────

def _do_shutdown(cfg: dict, layout: dict):
    print("[shutdown] shutting down…")
    W   = cfg["display"]["width"]
    H   = cfg["display"]["height"]
    fb  = cfg["display"]["framebuffer"]
    rot = cfg["display"].get("rotation", 0)

    _write_frame(solid_frame(W, H, (80, 0, 0)), fb)
    time.sleep(2)

    frame = render_page_rgb565(build_shutdown_page(), layout, rotate_180=(rot == 180))
    _write_frame(frame, fb)
    time.sleep(2)

    _write_frame(solid_frame(W, H, (255, 255, 255)), fb)
    time.sleep(1)

    subprocess.run(["sudo", "shutdown", "-h", "now"])


# ── System stats overlay ──────────────────────────────────────────────────────

def _cpu_temp() -> float:
    try:
        return int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000.0
    except Exception:
        return 0.0


def _cpu_pct() -> float:
    def _sample():
        parts = open("/proc/stat").readline().split()
        vals  = list(map(int, parts[1:]))
        return vals[3], sum(vals)
    idle1, total1 = _sample()
    time.sleep(0.15)
    idle2, total2 = _sample()
    dt = total2 - total1
    return 0.0 if dt == 0 else 100.0 * (1.0 - (idle2 - idle1) / dt)


def _ram_info() -> tuple[int, int]:
    info = {}
    for line in open("/proc/meminfo"):
        k, v = line.split(":", 1)
        info[k.strip()] = int(v.strip().split()[0])
    total = info.get("MemTotal", 0)
    avail = info.get("MemAvailable", 0)
    return (total - avail) // 1024, total // 1024


def _uptime_str() -> str:
    secs = float(open("/proc/uptime").read().split()[0])
    d = int(secs // 86400); secs %= 86400
    h = int(secs // 3600);  secs %= 3600
    m = int(secs // 60)
    return f"{d}d {h}h {m}m" if d else (f"{h}h {m}m" if h else f"{m}m")


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "unknown"
    finally:
        try: s.close()
        except: pass


def _tailscale_ip() -> str:
    try:
        addrs = subprocess.check_output(["hostname", "-I"], timeout=2).decode().split()
        for a in addrs:
            if a.startswith("100."):
                return a
    except Exception:
        pass
    return ""


def _render_stats_frame(W: int, H: int, layout: dict) -> bytes | None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("[overlay] Pillow not available")
        return None

    font_path = layout["font"]["path"]

    def fnt(size):
        try:   return ImageFont.truetype(font_path, size)
        except: return ImageFont.load_default()

    cpu_temp_val     = _cpu_temp()
    cpu_pct_val      = _cpu_pct()
    ram_used, ram_total = _ram_info()
    uptime           = _uptime_str()
    local_ip         = _local_ip()
    ts_ip            = _tailscale_ip()

    img  = Image.new("RGB", (W, H), (15, 15, 15))
    draw = ImageDraw.Draw(img)
    draw.text((W // 2, 22), "System Info",
              font=fnt(26), fill=(255, 255, 255), anchor="mm")
    draw.line([(0, 42), (W, 42)], fill=(50, 50, 50), width=1)

    y, gap = 56, 36
    rows = [
        ("CPU Temp",  f"{cpu_temp_val:.1f} °C",             (255, 160,  50)),
        ("CPU",       f"{cpu_pct_val:.0f}%",                 (200, 200, 200)),
        ("RAM",       f"{ram_used} / {ram_total} MB",        (200, 200, 200)),
        ("Uptime",    uptime,                                (200, 200, 200)),
        ("Local IP",  local_ip,                              (100, 220, 100)),
        ("Tailscale", ts_ip if ts_ip else "not connected",
                      (100, 180, 255) if ts_ip else (100, 100, 100)),
    ]
    for label, value, color in rows:
        draw.text((16, y),         label, font=fnt(16), fill=(130, 130, 130))
        draw.text((W - 16, y),     value, font=fnt(18), fill=color, anchor="ra")
        y += gap

    draw.text((W // 2, H - 14), "Tap anywhere to dismiss",
              font=fnt(13), fill=(70, 70, 70), anchor="mm")

    # Convert to RGB565
    raw = img.tobytes()
    buf = bytearray(W * H * 2)
    j = 0
    for i in range(0, len(raw), 3):
        r, g, b = raw[i], raw[i + 1], raw[i + 2]
        p = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        buf[j]     = p & 0xFF
        buf[j + 1] = (p >> 8) & 0xFF
        j += 2
    return bytes(buf)


def _display_overlay(cfg: dict, layout: dict):
    W  = cfg["display"]["width"]
    H  = cfg["display"]["height"]
    fb = cfg["display"]["framebuffer"]

    frame = _render_stats_frame(W, H, layout)
    if frame is None:
        return
    _write_frame(frame, fb)

    # Drain any queued commands, then wait up to 30 s for the next one
    while not _cmd.empty():
        try: _cmd.get_nowait()
        except queue.Empty: break
    try:
        _cmd.get(timeout=30)
    except queue.Empty:
        pass


# ── GPIO buttons ──────────────────────────────────────────────────────────────

def _start_button_threads(cfg: dict, layout: dict) -> bool:
    """Wire up GPIO buttons. Returns True if buttons initialised successfully."""
    btn_cfg = cfg.get("buttons", {})
    if not btn_cfg.get("enabled", True):
        return False
    try:
        from gpiozero import Button
        k2 = Button(btn_cfg["shutdown_gpio"], pull_up=btn_cfg.get("pull_up", True),
                    hold_time=btn_cfg.get("shutdown_hold_s", 5))
        k3 = Button(btn_cfg["advance_gpio"],  pull_up=btn_cfg.get("pull_up", True))

        k2.when_held = lambda: _do_shutdown(cfg, layout)

        def _k3_loop():
            while True:
                k3.wait_for_press()
                print("[button] next page")
                _cmd.put("next")
                k3.wait_for_release()
                time.sleep(0.1)

        threading.Thread(target=_k3_loop, daemon=True).start()
        print("[buttons] GPIO buttons active")
        return True
    except Exception as exc:
        print(f"[buttons] GPIO init failed: {exc}")
        return False


# ── Touch input ───────────────────────────────────────────────────────────────

def _map_touch(val: int, lo: int, hi: int, size: int) -> int:
    return int((max(lo, min(hi, val)) - lo) / (hi - lo) * (size - 1))


def _touch_loop(cfg: dict, layout: dict):
    try:
        import evdev
    except ImportError:
        print("[touch] evdev not installed — run: pip3 install evdev")
        return
    import glob

    W = cfg["display"]["width"]
    H = cfg["display"]["height"]

    def _find_device():
        if TOUCH_DEVICE:
            return evdev.InputDevice(TOUCH_DEVICE)
        for path in sorted(glob.glob("/dev/input/event*")):
            try:
                dev  = evdev.InputDevice(path)
                name = dev.name.lower()
                if any(x in name for x in ("ads7846", "xpt2046", "waveshare")):
                    return dev
            except Exception:
                pass
        return None

    dev = _find_device()
    if dev is None:
        print("[touch] no touch device found")
        return
    print(f"[touch] using {dev.path} ({dev.name})")

    raw_x = raw_y = 0
    touch_down_t: float | None = None
    ec = evdev.ecodes

    for event in dev.read_loop():
        if event.type == ec.EV_ABS:
            if event.code == ec.ABS_X:
                raw_x = event.value
            elif event.code == ec.ABS_Y:
                raw_y = event.value
        elif event.type == ec.EV_KEY and event.code == ec.BTN_TOUCH:
            if event.value == 1:
                touch_down_t = time.monotonic()
            elif event.value == 0 and touch_down_t is not None:
                duration     = time.monotonic() - touch_down_t
                touch_down_t = None

                if TOUCH_SWAP_XY:
                    sx = _map_touch(raw_y, TOUCH_Y_MIN, TOUCH_Y_MAX, W)
                    sy = _map_touch(raw_x, TOUCH_X_MIN, TOUCH_X_MAX, H)
                else:
                    sx = _map_touch(raw_x, TOUCH_X_MIN, TOUCH_X_MAX, W)
                    sy = _map_touch(raw_y, TOUCH_Y_MIN, TOUCH_Y_MAX, H)

                if TOUCH_INVERT_X:
                    sx = W - 1 - sx
                if TOUCH_INVERT_Y:
                    sy = H - 1 - sy

                if sx < W // 3:
                    if duration >= 3.0:
                        _do_shutdown(cfg, layout)
                    else:
                        _cmd.put("prev")
                elif sx > 2 * W // 3:
                    _cmd.put("next")
                elif duration >= 2.0:
                    _cmd.put("overlay")
                else:
                    _cmd.put("next")


# ── Background data fetch threads ─────────────────────────────────────────────

def _start_fetch_threads(store: DataStore):
    cfg = store.cfg

    def _weather_loop():
        from data import fetch_weather
        while True:
            try:
                store.weather.set(fetch_weather(store))
            except Exception as exc:
                print(f"[weather] {exc}")
            time.sleep(cfg["weather"]["update_interval_s"])

    def _commute_loop():
        from data import fetch_commute, in_commute_window
        while True:
            if in_commute_window(cfg):
                try:
                    store.commute.set(fetch_commute(store))
                except Exception as exc:
                    print(f"[commute] {exc}")
            time.sleep(cfg["commute"]["update_interval_s"])

    def _calendar_loop():
        from data import fetch_ics_events, fetch_work_state
        interval = cfg["calendar"]["update_interval_s"]
        while True:
            try:
                store.ics_events.set(fetch_ics_events(store))
            except Exception as exc:
                print(f"[calendar] {exc}")
            try:
                state, ret, title = fetch_work_state(store)
                store.work_state.set(state)
                store.work_state._return_date = ret
                store.work_state._event_title = title
                store.work_state.fetched_at = time.time()
            except Exception as exc:
                print(f"[work-state] {exc}")
            time.sleep(interval)

    def _aqi_loop():
        from data import fetch_aqi
        while True:
            try:
                store.aqi.set(fetch_aqi(store))
            except Exception as exc:
                print(f"[aqi] {exc}")
            time.sleep(cfg["aqi"]["update_interval_s"])

    def _alerts_loop():
        from data import fetch_alerts
        while True:
            try:
                store.alerts.set(fetch_alerts(store))
            except Exception as exc:
                print(f"[alerts] {exc}")
            time.sleep(cfg["alerts"]["update_interval_s"])

    for fn in (_weather_loop, _commute_loop, _calendar_loop,
               _aqi_loop, _alerts_loop):
        threading.Thread(target=fn, daemon=True).start()

    print("[fetch] background threads started")


# ── Setup mode ────────────────────────────────────────────────────────────────

def _run_setup_mode(port: int, layout: dict, cfg: dict):
    """Show setup URL on screen and block until config is saved."""
    ip = setup_server.start(port)
    print(f"[setup] open http://{ip}:{port} to configure")

    W   = cfg["display"]["width"]
    H   = cfg["display"]["height"]
    fb  = cfg["display"]["framebuffer"]
    rot = cfg["display"].get("rotation", 0)

    page  = build_setup_page(ip, port)
    frame = render_page_rgb565(page, layout, rotate_180=(rot == 180))
    _write_frame(frame, fb)

    setup_server.config_saved.wait()
    print("[setup] config saved — restarting")
    os.execv(sys.executable, [sys.executable] + sys.argv)


# ── Main display loop ──────────────────────────────────────────────────────────

def main():
    cfg = cfg_module.load()

    # Always start the setup server (enables reconfiguration at any time)
    setup_server.start(cfg.get("setup_port", 8080))

    W           = cfg["display"]["width"]
    H           = cfg["display"]["height"]
    fb          = cfg["display"]["framebuffer"]
    rot         = cfg["display"].get("rotation", 0)
    default_dwell = cfg["display"].get("page_dwell_s", 8)
    font_path   = cfg_module.resolve_font_path(cfg)

    layout = load_layout(font_path)

    if not cfg_module.is_complete(cfg):
        _run_setup_mode(cfg.get("setup_port", 8080), layout, cfg)
        return

    store = DataStore(cfg)

    _start_button_threads(cfg, layout)
    _start_fetch_threads(store)

    if ENABLE_TOUCH:
        threading.Thread(target=_touch_loop, args=(cfg, layout), daemon=True).start()

    loading_frame = render_page_rgb565(build_loading_page(), layout, rotate_180=(rot == 180))
    _write_frame(loading_frame, fb)

    print("[main] waiting for first data fetch…")
    deadline = time.time() + 30
    while time.time() < deadline:
        d = get_display(store)
        if d and d.get("pages"):
            break
        time.sleep(1)
    else:
        print("[main] first fetch timed out, continuing anyway")

    global _last_render_ok
    idx = 0

    while True:
        layout = load_layout(font_path)

        display = get_display(store)
        pages   = display.get("pages") if display else None
        if not pages:
            time.sleep(1)
            continue

        total = len(pages)
        page  = pages[idx % total]
        dwell = (layout.get("pages", {})
                       .get(page.get("_name", ""), {})
                       .get("dwell_seconds", default_dwell))

        try:
            frame = render_page_rgb565(page, layout, rotate_180=(rot == 180))
            _write_frame(frame, fb)
            _last_render_ok = time.time()
        except Exception as exc:
            print(f"[render] {exc}")

        # Show stale-data stripe if renders have been failing for >2× dwell
        if _last_render_ok > 0 and (time.time() - _last_render_ok) > 2 * default_dwell:
            _write_stale_stripe(fb, W, H)

        try:
            cmd = _cmd.get(timeout=dwell)
        except queue.Empty:
            cmd = "next"

        if cmd == "next":
            idx = (idx + 1) % total
        elif cmd == "prev":
            idx = (idx - 1) % total
        elif cmd == "overlay":
            _display_overlay(cfg, layout)
            # Don't advance page after dismissing the overlay

        store.display.fetched_at = 0.0


if __name__ == "__main__":
    main()
