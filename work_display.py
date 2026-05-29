#!/usr/bin/env python3
"""Work dashboard — standalone display driver.

Fetches data directly (no server), renders pages with PIL, and writes
RGB565 frames to the framebuffer. On first run (no config), shows a
setup URL and starts the web config server.
"""
from __future__ import annotations

import glob
import os
import queue
import subprocess
import sys
import threading
import time

# Unbuffered stdout so print() appears immediately in journalctl
sys.stdout.reconfigure(line_buffering=True)

import config as cfg_module
import setup_server
import stats as stats_mod
import touch as touch_mod
from data import DataStore, local_now
from pages import (
    build_setup_page, build_loading_page, build_shutdown_page,
    get_display,
)
from render import (
    load_layout, render_page_rgb565, render_sleep_frame, solid_frame,
    linescan_transition, invalidate_layout_cache,
    spotify_needs_scroll, spotify_scroll_complete,
    calendar_needs_scroll, calendar_scroll_complete,
    prefetch_spotify_art,
)

_SPOTIFY_SCROLL_TICK = 0.067  # seconds between re-renders (≈15 fps)

# Set True while the Spotify page is on screen; fetch threads back off to avoid GIL spikes
_spotify_page_active: bool = False

# Pre-render cache: id(page) → rgb565 bytes rendered in background
_prerender_cache:   dict = {}
_prerender_started: set  = set()


_ci_files_cache: dict = {"mtime": -1.0, "files": []}

# Linescan transition state
_last_frame:        bytes | None = None
_page_just_changed: bool         = False

# Sleep mode: timestamp of last manual wake (0 = never woken)
_sleep_woke_at: float = 0.0
# Screensaver drift state — position offsets from screen centre and step direction
_sleep_x_off: int = 0
_sleep_y_off: int = 0
_sleep_dx: int    = 20   # px per tick
_sleep_dy: int    = 15


def _in_sleep_window(cfg: dict, now_dt) -> bool:
    sc = cfg.get("sleep", {})
    if not sc.get("enabled", False):
        return False
    start_h   = sc.get("start_h", 22)
    end_h     = sc.get("end_h", 7)
    days      = sc.get("days", list(range(7)))
    now_h     = now_dt.hour
    overnight = start_h > end_h
    if overnight:
        in_time   = now_h >= start_h or now_h < end_h
        # attribute past-midnight hours to the day the sleep window started
        check_dow = now_dt.weekday() if now_h >= start_h else (now_dt.weekday() - 1) % 7
    else:
        in_time   = start_h <= now_h < end_h
        check_dow = now_dt.weekday()
    return in_time and (check_dow in days)

# ── Framebuffer helpers ───────────────────────────────────────────────────────────────

def _write_frame(data: bytes, fb_path: str):
    try:
        with open(fb_path, "wb") as fb:
            fb.write(data)
    except OSError as exc:
        print(f"[fb] write failed: {exc}")


def _launch_prerender(page: dict, layout: dict, rotate_180: bool) -> None:
    """Warm numpy caches for `page` in a background thread (one-shot per page object)."""
    pid = id(page)
    if pid in _prerender_started:
        return
    _prerender_started.add(pid)
    def _work():
        try:
            data = render_page_rgb565(page, layout, rotate_180=rotate_180)
            _prerender_cache[pid] = data
        except Exception as exc:
            print(f"[prerender] {exc}")
    threading.Thread(target=_work, daemon=True).start()


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
                           advance_event: threading.Event,
                           toggle_stats_fn) -> bool:
    """Wire up GPIO buttons. Returns True if buttons initialised successfully."""
    btn_cfg = cfg.get("buttons", {})
    if not btn_cfg.get("enabled", True):
        return False
    try:
        from gpiozero import Button
        k2 = Button(btn_cfg.get("shutdown_gpio", 23), pull_up=btn_cfg.get("pull_up", True))
        k3 = Button(btn_cfg.get("advance_gpio",  24), pull_up=btn_cfg.get("pull_up", True))

        k2.when_pressed = lambda: toggle_stats_fn()

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

    def _wait_for_spotify_clear():
        """Block until the Spotify page is no longer active."""
        while _spotify_page_active:
            time.sleep(0.5)

    def _weather_loop():
        from data import fetch_weather
        while True:
            _wait_for_spotify_clear()
            try:
                store.weather.set(fetch_weather(store))
                time.sleep(cfg["weather"]["update_interval_s"])
            except Exception as exc:
                print(f"[weather] {exc}")
                time.sleep(30)

    def _commute_loop():
        from data import fetch_commute, in_commute_window
        while True:
            _wait_for_spotify_clear()
            if in_commute_window(cfg):
                try:
                    store.commute.set(fetch_commute(store))
                except Exception as exc:
                    print(f"[commute] {exc}")
                    time.sleep(30)
                    continue
            time.sleep(cfg["commute"]["update_interval_s"])

    def _calendar_loop():
        from data import fetch_ics_events, fetch_work_state
        interval = cfg["calendar"]["update_interval_s"]
        while True:
            _wait_for_spotify_clear()
            ok = True
            try:
                store.ics_events.set(fetch_ics_events(store))
            except Exception as exc:
                print(f"[calendar] {exc}")
                ok = False
            try:
                state, ret, title = fetch_work_state(store)
                store.work_state.set(state)
                store.work_state._return_date = ret
                store.work_state._event_title = title
                store.work_state.fetched_at = time.time()
            except Exception as exc:
                print(f"[work-state] {exc}")
                ok = False
            time.sleep(interval if ok else 30)

    def _aqi_loop():
        from data import fetch_aqi
        while True:
            _wait_for_spotify_clear()
            try:
                store.aqi.set(fetch_aqi(store))
                time.sleep(cfg["aqi"]["update_interval_s"])
            except Exception as exc:
                print(f"[aqi] {exc}")
                time.sleep(30)

    def _alerts_loop():
        from data import fetch_alerts
        while True:
            _wait_for_spotify_clear()
            try:
                store.alerts.set(fetch_alerts(store))
                time.sleep(cfg["alerts"]["update_interval_s"])
            except Exception as exc:
                print(f"[alerts] {exc}")
                time.sleep(30)

    def _spotify_loop():
        from data import fetch_spotify
        interval = cfg.get("spotify", {}).get("update_interval_s", 10)
        _last_track_key = None
        while True:
            try:
                data = fetch_spotify(store)
                store.spotify.set(data)
                # Only invalidate display cache when track or playing state changes.
                # Keeping fetched_at stable means the page dict keeps the same id()
                # between polls → the fast-path numpy cache stays warm.
                track_key = (
                    (data or {}).get("track"),
                    (data or {}).get("is_playing"),
                )
                if track_key != _last_track_key:
                    _last_track_key = track_key
                    store.display.fetched_at = 0
                if data and data.get("art_url"):
                    prefetch_spotify_art(data["art_url"])
            except Exception as exc:
                print(f"[spotify] {exc}")
            time.sleep(interval)

    for fn in (_weather_loop, _commute_loop, _calendar_loop,
               _aqi_loop, _alerts_loop, _spotify_loop):
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

def _hide_console_cursor():
    for tty in ("/dev/tty0", "/dev/tty1"):
        try:
            with open(tty, "w") as f:
                f.write("\033[?25l")
        except Exception:
            pass


def main():
    _hide_console_cursor()
    cfg = cfg_module.load()

    # Always start the setup server (enables reconfiguration at any time)
    setup_server.start(cfg.get("setup_port", 8080))

    W   = cfg["display"]["width"]
    H   = cfg["display"]["height"]
    fb  = cfg["display"]["framebuffer"]
    rot = cfg["display"].get("rotation", 0)
    default_dwell = cfg["display"].get("page_dwell_s", 8)
    font_path = cfg_module.resolve_font_path(cfg)

    global _spotify_page_active, _sleep_woke_at, _sleep_x_off, _sleep_y_off, _sleep_dx, _sleep_dy
    global _last_frame, _page_just_changed

    layout = load_layout(font_path, display_w=W, display_h=H)

    if not cfg_module.is_complete(cfg):
        _run_setup_mode(cfg.get("setup_port", 8080), layout, cfg)
        return

    store = DataStore(cfg)

    advance_event = threading.Event()

    # ── Stats monitor (always running) ────────────────────────────────────────
    _stats_mon    = stats_mod.StatsMonitor()
    _stats_active = False
    _stats_wake   = threading.Event()

    def _toggle_stats():
        nonlocal _stats_active
        _stats_active = not _stats_active
        print(f"[button] stats {'on' if _stats_active else 'off'}")
        _stats_wake.set()

    _start_button_threads(cfg, layout, advance_event, _toggle_stats)
    _start_fetch_threads(store)

    # ── Navigation queue (touch + GPIO button share it) ───────────────────────
    _nav_q: queue.Queue[int] = queue.Queue()

    # Wire existing GPIO advance button into the nav queue
    _orig_advance = advance_event

    def _gpio_advance_watcher():
        while True:
            _orig_advance.wait()
            _orig_advance.clear()
            _nav_q.put(+1)

    threading.Thread(target=_gpio_advance_watcher, daemon=True).start()

    # ── Touch callbacks ───────────────────────────────────────────────────────
    def _on_tap(sx: int, sy: int) -> None:
        nonlocal _stats_active
        if _stats_active:
            _stats_active = False
            _stats_wake.set()
        else:
            _nav_q.put(+1 if sx >= W // 2 else -1)

    def _on_long_press(sx: int, sy: int) -> None:
        nonlocal _stats_active
        if _stats_active:
            _do_shutdown(cfg, layout)
        elif W // 3 <= sx <= 2 * W // 3:
            _stats_active = True
            _stats_wake.set()

    _touch_dev = touch_mod.find_touch_device(cfg)
    if _touch_dev:
        touch_mod.start_touch(_touch_dev, W, H, cfg, _on_tap, _on_long_press)
    else:
        print("[touch] no touch device found — touch disabled")

    # ── Initial loading screen ────────────────────────────────────────────────
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
    _page_entered = time.time()
    while True:
        layout = load_layout(font_path, display_w=W, display_h=H)

        # ── Sleep mode ────────────────────────────────────────────────────────
        _now_dt = local_now(cfg)
        _asleep = _in_sleep_window(cfg, _now_dt)
        _wake_s = cfg.get("sleep", {}).get("wake_minutes", 60) * 60
        _manually_awake = (time.time() - _sleep_woke_at) < _wake_s
        if _asleep and not _manually_awake:
            _spotify_page_active = False
            # Bounce "zzz" around the screen (screensaver drift, ≈30s per step)
            _sleep_x_off += _sleep_dx
            _sleep_y_off += _sleep_dy
            if abs(_sleep_x_off) >= 60:
                _sleep_dx = -_sleep_dx
                _sleep_x_off = max(-60, min(60, _sleep_x_off))
            if abs(_sleep_y_off) >= 40:
                _sleep_dy = -_sleep_dy
                _sleep_y_off = max(-40, min(40, _sleep_y_off))
            _write_frame(render_sleep_frame(W, H, _sleep_x_off, _sleep_y_off,
                                            rotate_180=(rot == 180), layout=layout), fb)
            try:
                _nav_q.get(timeout=30)
                _sleep_woke_at = time.time()
            except queue.Empty:
                pass
            continue

        display = get_display(store)
        pages   = list(display.get("pages") or []) if display else None
        if not pages:
            time.sleep(1)
            continue

        # Inject custom image pages from custom_images/ directory
        _custom_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "custom_images")
        if os.path.isdir(_custom_dir):
            _ci_cfg   = cfg.get("custom_images", {})
            _ci_start = _ci_cfg.get("display_start_h", 0)
            _ci_end   = _ci_cfg.get("display_end_h", 23)
            _now_h = local_now(cfg).hour
            _in_window = (_ci_start <= _ci_end and _ci_start <= _now_h <= _ci_end) or \
                         (_ci_start > _ci_end and (_now_h >= _ci_start or _now_h <= _ci_end))
            if _in_window:
                _img_exts = (".png", ".jpg", ".jpeg", ".bmp")
                _dir_mtime = os.path.getmtime(_custom_dir)
                if _dir_mtime != _ci_files_cache["mtime"]:
                    _ci_files_cache["files"] = [
                        f for f in sorted(glob.glob(os.path.join(_custom_dir, "*")))
                        if os.path.splitext(f)[1].lower() in _img_exts
                    ]
                    _ci_files_cache["mtime"] = _dir_mtime
                for _img_file in _ci_files_cache["files"]:
                    pages.append({"_name": "custom_image", "image_path": _img_file})

        page  = pages[idx % len(pages)]
        _pname = page.get("_name", "")
        dwell  = int(layout.get("pages", {}).get(_pname, {}).get("dwell_seconds") or default_dwell)

        if _stats_active:
            try:
                sf = stats_mod.render_stats_rgb565(
                    _stats_mon, W, H, rot == 180)
                _write_frame(sf, fb)
            except Exception as exc:
                print(f"[stats] {exc}")
            _stats_wake.wait(timeout=2)
            _stats_wake.clear()
        else:
            _pn = _pname
            _spotify_page_active = (_pn == "spotify")
            pid = id(page)
            cached_frame = _prerender_cache.pop(pid, None)
            try:
                frame = cached_frame if cached_frame is not None else \
                        render_page_rgb565(page, layout, rotate_180=(rot == 180))
            except Exception as exc:
                print(f"[render] {exc}")
                frame = None
            if frame:
                if _page_just_changed and _last_frame is not None:
                    try:
                        for _tf in linescan_transition(_last_frame, frame, W, H):
                            _write_frame(_tf, fb)
                            time.sleep(0.010)
                    except Exception as exc:
                        print(f"[transition] {exc}")
                _write_frame(frame, fb)
                _last_frame = frame
            _page_just_changed = False
            scroll_active = (_pn == "spotify" and spotify_needs_scroll()) or \
                            (_pn == "calendar" and calendar_needs_scroll())
            scroll_done   = (_pn == "spotify" and spotify_scroll_complete()) or \
                            (_pn == "calendar" and calendar_scroll_complete())
            elapsed = time.time() - _page_entered
            # Pre-render the next page during the first tick (text is static during initial pause)
            if _pn == "spotify" and elapsed < 0.2 and len(pages) > 1:
                _launch_prerender(pages[(idx + 1) % len(pages)], layout, rot == 180)
            if _pn == "spotify":
                # Always fast-tick Spotify — progress bar needs live updates
                wait = _SPOTIFY_SCROLL_TICK
            elif scroll_active and not scroll_done:
                wait = _SPOTIFY_SCROLL_TICK
            elif scroll_active and scroll_done:
                remaining = dwell - elapsed
                wait = max(remaining, 0.1)
            else:
                wait = dwell
            try:
                delta = _nav_q.get(timeout=wait)
                idx = (idx + delta) % len(pages)
                _page_entered = time.time()
                _page_just_changed = True
                store.display.fetched_at = 0.0
            except queue.Empty:
                elapsed = time.time() - _page_entered
                if _pn == "spotify":
                    # Advance after dwell; allow extra time if still mid-scroll
                    if scroll_active and not scroll_done and elapsed < dwell * 3:
                        pass  # still scrolling within grace period
                    elif elapsed >= dwell:
                        idx = (idx + 1) % len(pages)
                        _page_entered = time.time()
                        _page_just_changed = True
                        store.display.fetched_at = 0.0
                    # else: within dwell, keep fast-ticking
                elif scroll_active and elapsed < dwell * 3:
                    if not scroll_done:
                        pass  # scroll tick — keep re-rendering
                    else:
                        idx = (idx + 1) % len(pages)
                        _page_entered = time.time()
                        _page_just_changed = True
                        store.display.fetched_at = 0.0
                else:
                    idx = (idx + 1) % len(pages)
                    _page_entered = time.time()
                    _page_just_changed = True
                    store.display.fetched_at = 0.0


if __name__ == "__main__":
    main()
