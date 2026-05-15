#!/usr/bin/env python3
"""Work dashboard — standalone display driver.

Fetches data directly (no server), renders pages with PIL, and writes
RGB565 frames to the framebuffer. On first run (no config), shows a
setup URL and starts the web config server.
"""

import os
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


# ── Framebuffer helpers ───────────────────────────────────────────────────────────────

def _write_frame(data: bytes, fb_path: str):
    try:
        with open(fb_path, "wb") as fb:
            fb.write(data)
    except OSError as exc:
        print(f"[fb] write failed: {exc}")


# ── Shutdown ────────────────────────────────────────────────────────────────────────

def _do_shutdown(cfg: dict, layout: dict):
    print("[shutdown] shutting down…")
    W = cfg["display"]["width"]
    H = cfg["display"]["height"]
    fb = cfg["display"]["framebuffer"]
    rot = cfg["display"].get("rotation", 0)

    _write_frame(solid_frame(W, H, (80, 0, 0)), fb)
    time.sleep(2)

    frame = render_page_rgb565(build_shutdown_page(), layout, rotate_180=(rot == 180))
    _write_frame(frame, fb)
    time.sleep(2)

    _write_frame(solid_frame(W, H, (255, 255, 255)), fb)
    time.sleep(1)

    subprocess.run(["sudo", "shutdown", "-h", "now"])


# ── GPIO buttons ───────────────────────────────────────────────────────────────────

def _start_button_threads(cfg: dict, layout: dict,
                           advance_event: threading.Event) -> bool:
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
                advance_event.set()
                k3.wait_for_release()
                time.sleep(0.1)

        threading.Thread(target=_k3_loop, daemon=True).start()
        print("[buttons] GPIO buttons active")
        return True
    except Exception as exc:
        print(f"[buttons] GPIO init failed: {exc}")
        return False


# ── Background data fetch threads ──────────────────────────────────────────────────

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


# ── Setup mode ────────────────────────────────────────────────────────────────────

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


# ── Main display loop ───────────────────────────────────────────────────────────────────

def main():
    cfg = cfg_module.load()

    # Always start the setup server (enables reconfiguration at any time)
    setup_server.start(cfg.get("setup_port", 8080))

    W   = cfg["display"]["width"]
    H   = cfg["display"]["height"]
    fb  = cfg["display"]["framebuffer"]
    rot = cfg["display"].get("rotation", 0)
    default_dwell = cfg["display"].get("page_dwell_s", 8)
    font_path = cfg_module.resolve_font_path(cfg)

    layout = load_layout(font_path, display_w=W, display_h=H)

    if not cfg_module.is_complete(cfg):
        _run_setup_mode(cfg.get("setup_port", 8080), layout, cfg)
        return

    store = DataStore(cfg)

    advance_event = threading.Event()
    _start_button_threads(cfg, layout, advance_event)
    _start_fetch_threads(store)

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

    idx = 0
    while True:
        layout = load_layout(font_path, display_w=W, display_h=H)

        display = get_display(store)
        pages   = display.get("pages") if display else None
        if not pages:
            time.sleep(1)
            continue

        page  = pages[idx % len(pages)]
        dwell = (layout.get("pages", {})
                      .get(page.get("_name", ""), {})
                      .get("dwell_seconds", default_dwell))

        try:
            frame = render_page_rgb565(page, layout, rotate_180=(rot == 180))
            _write_frame(frame, fb)
        except Exception as exc:
            print(f"[render] {exc}")

        idx = (idx + 1) % len(pages)
        advance_event.wait(timeout=dwell)
        advance_event.clear()

        store.display.fetched_at = 0.0


if __name__ == "__main__":
    main()
