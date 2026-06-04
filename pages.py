"""Page builders — assemble page dicts for the renderer."""
from __future__ import annotations

import socket
import time
from datetime import datetime, timedelta

from data import (
    DataStore,
    WMO_DESC, WMO_ICONS,
    get_weather, get_aqi, get_alerts, get_commute,
    get_ics_events, get_work_state, get_spotify,
    in_commute_window, later_today_desc, wind_cardinal,
    local_now,
)
from render import get_raw_layout


# ── Individual page builders ─────────────────────────────────────────────────

def build_clock_page(tz: str | None = None) -> dict:
    now = local_now({"location": {"timezone": tz}}) if tz else datetime.now()
    return {
        "_name": "clock",
        "title": "Clock",
        "lines": [
            {"text": now.strftime("%-I:%M %p"), "size": 3, "color": "white"},
            {"text": now.strftime("%A"),         "size": 2, "color": "cyan"},
            {"text": now.strftime("%b %-d, %Y"), "size": 2, "color": "white"},
        ],
    }


def _cfg_err(name: str, lines: list[dict]) -> dict:
    """Return a minimal error page for a feature that isn't configured."""
    return {"_name": name, "title": name.title(), "lines": lines}


def build_weather_page(store: DataStore) -> dict:
    loc = store.cfg.get("location") or {}
    if not loc.get("lat") and not loc.get("lon"):
        return _cfg_err("forecast", [
            {"text": "Location not set", "size": 2, "color": "red"},
            {"text": "Add your location", "size": 1, "color": "darkgrey"},
            {"text": "in setup", "size": 1, "color": "darkgrey"},
        ])

    weather = get_weather(store)
    aqi     = get_aqi(store)
    # NWS alert banner muted for now — its positioning needs a fix (see todo.md).
    # The fetch thread still keeps the cache warm, so unmuting is a one-line revert.
    alert   = None
    lines: list[dict] = []

    cur    = weather.get("current", {})
    daily  = weather.get("daily", {})
    hourly = weather.get("hourly", {})
    now    = local_now(store.cfg)

    temp    = cur.get("temperature_2m", 0)
    wmo     = cur.get("weather_code", 0)
    cur_hum = cur.get("relative_humidity_2m", 0)
    cur_ws  = cur.get("wind_speed_10m", 0)
    cur_wd  = cur.get("wind_direction_10m", 0)
    hi   = (daily.get("temperature_2m_max") or [0])[0]
    lo   = (daily.get("temperature_2m_min") or [0])[0]

    htimes    = hourly.get("time", [])
    htemps    = hourly.get("temperature_2m", [])
    hprec     = hourly.get("precipitation_probability", [])
    now_hour  = now.replace(minute=0, second=0, microsecond=0)
    time_idx  = {t: i for i, t in enumerate(htimes)}

    pop = 0
    for h in range(4):
        i = time_idx.get((now_hour + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M"))
        if i is not None and i < len(hprec):
            pop = max(pop, int(hprec[i] or 0))

    lines.append({"text": f"{temp:.0f}°F", "size": 3, "color": "white"})
    desc = WMO_DESC.get(wmo, "")
    if desc:
        lines.append({"text": desc, "size": 1, "color": "white", "left_align": True})
    lines.append({"text": f"Hi {hi:.0f}°  Lo {lo:.0f}°", "size": 1, "color": "cyan", "left_align": True})
    rain_color = "cyan" if pop >= 60 else ("yellow" if pop >= 40 else "white")
    lines.append({"text": f"{pop}% chance of rain", "size": 1, "color": rain_color, "left_align": True})
    lines.append({"text": f"Hum: {cur_hum:.0f}%", "size": 0, "color": "cyan", "left_align": True})
    lines.append({"text": f"{cur_ws:.0f}mph {wind_cardinal(cur_wd)}", "size": 0, "color": "white", "left_align": True})

    later = later_today_desc(hourly, now)
    if later:
        lines.append({"text": later, "size": 0, "color": "darkgrey", "left_align": True})

    aqi_overlay = None
    if aqi.get("aqi") is not None:
        aqi_val   = aqi["aqi"]
        aqi_color = "green" if aqi_val <= 50 else ("yellow" if aqi_val <= 100 else "red")
        aqi_overlay = {"value": aqi_val, "color": aqi_color}

    first_hour = now.replace(minute=0, second=0, microsecond=0)
    if now.minute > 0:
        first_hour += timedelta(hours=1)
    grid = []
    for step in range(5):
        target = first_hour + timedelta(hours=step * 2)
        idx = time_idx.get(target.strftime("%Y-%m-%dT%H:%M"))
        if idx is None:
            continue
        tp = htemps[idx] if idx < len(htemps) else 0
        pp = int(hprec[idx]) if idx < len(hprec) else 0
        label = target.strftime("%-I%p").lower()
        rain_color = ("cyan"   if pp >= 60 else
                      "yellow" if pp >= 40 else
                      "white"  if pp >= 20 else "darkgrey")
        grid.append({"label": label, "temp": f"{tp:.0f}°",
                     "rain": f"{pp}%", "rain_color": rain_color})

    page = {
        "_name": "forecast",
        "title": "Forecast",
        "lines": lines,
        "hourly_grid": grid,
        "weather_icon": WMO_ICONS.get(wmo, "cloud"),
    }
    if aqi_overlay:
        page["aqi_overlay"] = aqi_overlay
    if alert:
        page["alert_banner"] = alert.lstrip("! ")
    # Alerts are muted, so don't let alert-cache staleness trip the stale border.
    if store.weather.stale():
        page["stale"] = True
    return page


def build_commute_page(store: DataStore) -> dict | None:
    cfg = store.cfg
    tomtom = (cfg.get("api_keys") or {}).get("tomtom", "").strip()
    home   = (cfg.get("addresses") or {}).get("home", "").strip()
    work   = (cfg.get("addresses") or {}).get("work", "").strip()
    if not tomtom or not home or not work:
        if not in_commute_window(cfg):
            return None
        missing = []
        if not tomtom:
            missing.append("TomTom key")
        if not home or not work:
            missing.append("addresses")
        return _cfg_err("commute", [
            {"text": "Commute not configured", "size": 1, "color": "red"},
            {"text": ", ".join(missing) + " missing", "size": 1, "color": "darkgrey"},
            {"text": "Open setup to fix", "size": 0, "color": "darkgrey"},
        ])

    if not in_commute_window(cfg):
        return None
    data  = get_commute(store)
    if not data:
        return None
    lines: list[dict] = []
    for route in data.get("routes", []):
        duration = route.get("duration_text", "??")
        delay    = route.get("traffic_delay_seconds", 0)
        via      = route.get("via_text", "")
        label    = route.get("label", "")
        color    = "red" if delay >= 600 else ("yellow" if delay >= 120 else "green")
        lines.append({"text": label,    "size": 1, "color": "white"})
        lines.append({"text": duration, "size": 2, "color": color})
        dc = "red" if delay >= 600 else "yellow"
        if delay >= 120:
            delay_str = f"+{round(delay / 60)} min"
            if route.get("cause"):
                delay_str += f" ({route['cause']})"
            lines.append({"text": delay_str, "size": 0, "color": dc})
        elif via:
            lines.append({"text": via, "size": 0, "color": "darkgrey"})
    if not lines:
        return None
    page = {"_name": "commute", "title": "Commute Home", "lines": lines}
    if store.commute.stale():
        page["stale"] = True
    if _bg_enabled(cfg):
        # Green when clear → amber → red as the worst route's traffic delay
        # climbs (heavy ≈ 10 min added).
        worst = max((r.get("traffic_delay_seconds", 0) for r in data.get("routes", [])),
                    default=0)
        page["bg"] = _ramp((_GO_GRAD, _WARM_GRAD, _HOT_GRAD), worst / 600)
    return page


def build_calendar_page(store: DataStore) -> dict | None:
    ics_url = (store.cfg.get("calendar") or {}).get("ics_url", "").strip()
    if not ics_url:
        return _cfg_err("calendar_empty", [
            {"text": "No calendar URL", "size": 2, "color": "red"},
            {"text": "Add .ics URL in setup", "size": 1, "color": "darkgrey"},
        ])

    events = get_ics_events(store)
    now    = local_now(store.cfg)
    today  = now.date()

    upcoming = []
    for ev in events:
        try:
            start = datetime.fromisoformat(ev["start_iso"])
            mins  = int((start - now).total_seconds() / 60)
            if mins > -30 and (mins <= 0 or start.date() == today):
                upcoming.append({**ev, "minutes_until": mins})
        except Exception:
            pass

    if not upcoming:
        lines: list[dict] = [
            {"text": "No upcoming", "size": 1, "color": "darkgrey"},
            {"text": "events today", "size": 1, "color": "darkgrey"},
        ]
        # Find next 1-2 events from any future day (up to 7 days out)
        future = []
        for ev in events:
            try:
                s = datetime.fromisoformat(ev["start_iso"])
                if s > now:
                    future.append(ev)
            except Exception:
                pass
        for i, ev in enumerate(future[:2]):
            try:
                s     = datetime.fromisoformat(ev["start_iso"])
                e     = datetime.fromisoformat(ev["end_iso"])
                title = ev.get("title", "Event")
                label    = "Next" if i == 0 else "Then"
                time_str = f"{s.strftime('%a. %-I:%M')} - {e.strftime('%-I:%M %p')}"
                lines.append({"text": f"{label}: {title}", "color": "grey"})
                lines.append({"text": time_str, "size": 1, "color": "grey"})
            except Exception:
                pass
        page = {"_name": "calendar_empty", "title": "Calendar", "lines": lines}
        if store.ics_events.stale():
            page["stale"] = True
        return page

    nxt  = upcoming[0]
    mins = nxt["minutes_until"]

    if mins < 0:
        countdown, cc = "Now", "cyan"
    elif mins < 10:
        countdown, cc = f"in {mins} min", "red"
    elif mins < 60:
        countdown, cc = f"in {mins} min", "yellow"
    else:
        h, m = divmod(mins, 60)
        countdown, cc = (f"in {h}h {m}m" if m else f"in {h}h"), "green"

    lines: list[dict] = [
        {"text": nxt.get("title", "Meeting"), "color": "white", "wrap": True},
        {"text": countdown, "size": 3, "color": cc},
    ]
    try:
        start = datetime.fromisoformat(nxt["start_iso"])
        end   = datetime.fromisoformat(nxt["end_iso"])
        lines.append({"text": f"{start.strftime('%-I:%M')} - {end.strftime('%-I:%M %p')}",
                      "size": 1, "color": "grey"})
    except Exception:
        pass

    loc = nxt.get("location", "").strip()
    lines.append({"text": loc, "size": 0, "color": "grey"})

    if len(upcoming) > 1:
        t2 = upcoming[1].get("title", "")
        lines.append({"text": f"Then: {t2}", "color": "grey"})
    else:
        lines.append({"text": "nothing after this event", "color": "grey"})

    page = {"_name": "calendar", "title": "Calendar", "lines": lines}
    if store.ics_events.stale():
        page["stale"] = True
    if _bg_enabled(store.cfg):
        # Calm ≥90 min out, warming to amber then red as the next event nears.
        page["bg"] = _ramp((_CALM_GRAD, _WARM_GRAD, _HOT_GRAD), (90 - mins) / 90)
    return page


def build_setup_page(ip: str, port: int) -> dict:
    no_network = ip.startswith("127.")
    try:
        hostname = socket.gethostname()
    except Exception:
        hostname = None

    if no_network:
        lines = [
            {"text": "WiFi not connected", "size": 1, "color": "red"},
            {"text": "SSH in and open:", "size": 0, "color": "darkgrey"},
        ]
        if hostname:
            lines.append({"text": f"http://{hostname}.local:{port}", "size": 1, "color": "white"})
        lines.append({"text": "to configure this display", "size": 0, "color": "darkgrey"})
    else:
        lines = [
            {"text": "Open in browser:", "size": 1, "color": "cyan"},
            {"text": f"http://{ip}:{port}", "size": 1, "color": "white"},
            {"text": "to configure this display", "size": 0, "color": "darkgrey"},
        ]
        if hostname:
            lines.append({"text": f"or {hostname}.local:{port}", "size": 0, "color": "darkgrey"})

    return {
        "_name": "setup",
        "title": "Setup Required",
        "lines": lines,
    }


def build_loading_page() -> dict:
    return {
        "_name": "loading",
        "title": "Loading",
        "lines": [
            {"text": "Fetching data...", "size": 1, "color": "darkgrey"},
        ],
    }


def build_error_page(msg: str) -> dict:
    return {
        "_name": "error",
        "title": "Error",
        "lines": [
            {"text": msg, "size": 1, "color": "red", "wrap": True},
        ],
    }


def build_sleep_page() -> dict:
    return {
        "_name": "sleep",
        "title": "",
        "lines": [{"text": "zzz", "size": 3, "color": "darkgrey"}],
    }


def build_shutdown_page() -> dict:
    return {
        "_name": "shutdown",
        "title": "Shutting Down",
        "lines": [
            {"text": "Safe to unplug", "size": 2, "color": "white"},
        ],
    }


# ── Weather-reactive background gradient ─────────────────────────────────────

def _scale(c: tuple, f: float) -> tuple:
    return tuple(max(0, min(255, int(v * f))) for v in c)


def _blend(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(max(0, min(255, int(a[i] * (1 - t) + b[i] * t))) for i in range(3))


def _sun_phase(daily: dict, now: datetime) -> str:
    """'day' | 'night' | 'dawn' | 'dusk' from today's sunrise/sunset times."""
    try:
        sr = datetime.fromisoformat(daily["sunrise"][0])
        ss = datetime.fromisoformat(daily["sunset"][0])
    except Exception:
        return "day" if 7 <= now.hour < 19 else "night"
    tw = 50 * 60  # twilight half-window, seconds
    if abs((now - sr).total_seconds()) <= tw:
        return "dawn"
    if abs((now - ss).total_seconds()) <= tw:
        return "dusk"
    return "day" if sr < now < ss else "night"


def _sky_gradient(cur: dict, daily: dict, now: datetime) -> tuple:
    """Vertical (top, bottom) RGB background reflecting sky condition + time.

    Deliberately kept dark so the bright page text stays readable on a 320×240
    TFT — it evokes the sky rather than reproducing a bright daytime blue.
    """
    code   = int(cur.get("weather_code") or 0)
    precip = int(cur.get("precipitation_probability") or 0)
    # Daytime base: saturated blue when clear → greyer as clouds/rain increase
    if   code in (0, 1):       base = ((18, 44, 92), (44, 92, 158))  # clear
    elif code == 2:            base = ((26, 46, 80), (52, 86, 134))  # partly cloudy
    elif code in (3, 45, 48):  base = ((40, 46, 56), (70, 78, 90))   # overcast / fog
    else:                      base = ((30, 38, 50), (54, 64, 78))   # precipitation
    if precip >= 55 and code not in (0, 1):
        base = ((30, 36, 46), (50, 58, 70))                          # likely rain → grey

    phase = _sun_phase(daily, now)
    if phase == "night":
        return (_scale(base[0], 0.32), _scale(base[1], 0.38))
    if phase == "dawn":
        warm = (120, 78, 70)
        return (_blend(_scale(base[0], 0.55), warm, 0.30),
                _blend(_scale(base[1], 0.70), warm, 0.45))
    if phase == "dusk":
        warm = (140, 70, 44)
        return (_blend(_scale(base[0], 0.55), warm, 0.32),
                _blend(_scale(base[1], 0.70), warm, 0.50))
    return base  # day


# Reactive gradients for calendar urgency + commute traffic. Kept dark so the
# bright page text stays readable (same principle as the sky gradient).
# Kept dark on purpose: the urgency text itself is coloured (yellow/red), and
# those are mid-luminance, so the background must stay dark to keep them legible.
# Hue carries the signal — teal/green (calm) → amber → maroon (urgent).
_CALM_GRAD = ((16, 32, 40), (26, 50, 60))    # cool teal — relaxed (calendar)
_GO_GRAD   = ((14, 36, 24), (24, 54, 38))    # green — clear roads (commute)
_WARM_GRAD = ((44, 34, 14), (64, 50, 18))    # amber — heads up
_HOT_GRAD  = ((46, 18, 14), (66, 26, 20))    # maroon — urgent


def _ramp(stops: tuple, t: float) -> tuple:
    """Blend a 3-stop (calm, mid, hot) gradient by t in [0, 1].

    Each stop is a (top_rgb, bottom_rgb) pair; top and bottom blend
    independently so the result is itself a (top, bottom) gradient.
    """
    t = max(0.0, min(1.0, t))
    if t <= 0.5:
        f, a, b = t * 2, stops[0], stops[1]
    else:
        f, a, b = (t - 0.5) * 2, stops[1], stops[2]
    return (_blend(a[0], b[0], f), _blend(a[1], b[1], f))


def _bg_enabled(cfg: dict) -> bool:
    return bool((cfg.get("display") or {}).get("weather_bg", True))


def _display_bg(store: DataStore):
    """Shared background gradient for every page this cycle, or None if disabled."""
    cfg = store.cfg
    if not _bg_enabled(cfg):
        return None
    try:
        weather = get_weather(store)
        cur   = weather.get("current") or {}
        daily = weather.get("daily") or {}
        if not cur and not daily:
            return None
        return _sky_gradient(cur, daily, local_now(cfg))
    except Exception as exc:
        print(f"[pages] background gradient failed: {exc}")
        return None


# ── Top-level display assembler ──────────────────────────────────────────────

def build_spotify_page(store: DataStore) -> dict | None:
    sp = get_spotify(store)
    if not sp:
        return None
    return {
        "_name":       "spotify",
        "title":       "Now Playing",
        "track":       sp.get("track", ""),
        "artist":      sp.get("artist", ""),
        "album":       sp.get("album", ""),
        "art_url":     sp.get("art_url"),
        "progress_ms": sp.get("progress_ms", 0),
        "duration_ms": sp.get("duration_ms", 0),
        "playlist":    sp.get("playlist", ""),
    }


def build_display(store: DataStore) -> dict:
    state, return_date, event_title = get_work_state(store)

    # One sky-driven gradient shared by every page this cycle (None = disabled).
    bg = _display_bg(store)

    def _result(pages, mode):
        if bg:
            for p in pages:
                p.setdefault("bg", bg)
        return {"pages": pages, "display_mode": mode}

    if state == "WFH":
        return _result([{"_name": "wfh", "title": "Working From Home", "lines": [
            {"text": "Working From Home", "size": 3, "color": "white"},
        ]}], "WFH")

    if state == "OOO":
        ret_str = return_date.strftime("%a %b %-d") if return_date else ""
        lines   = [{"text": "Out of Office", "size": 3, "color": "white"}]
        if ret_str:
            lines.append({"text": f"Returning on {ret_str}", "size": 1, "color": "cyan"})
        return _result([{"_name": "ooo", "title": "Out of Office", "lines": lines}], "OOO")

    if state == "HOLIDAY":
        title_text = event_title or "Holiday"
        return _result([{"_name": "holiday", "title": "Holiday", "lines": [
            {"text": title_text, "size": 3, "color": "white"},
        ]}], "HOLIDAY")

    tz = store.cfg.get("location", {}).get("timezone")
    layout_pages = get_raw_layout().get("pages", {})

    pages = []
    if layout_pages.get("clock", {}).get("enabled", True):
        pages.append(build_clock_page(tz))

    for fn in (build_spotify_page, build_calendar_page, build_weather_page, build_commute_page):
        try:
            page = fn(store)
            if page and layout_pages.get(page.get("_name", ""), {}).get("enabled", True):
                pages.append(page)
        except Exception as exc:
            print(f"[pages] {fn.__name__} failed: {exc}")

    return _result(pages, "NORMAL")


def get_display(store: DataStore) -> dict:
    if not store.display.fresh():
        store.display.set(build_display(store))
    return store.display.get()
