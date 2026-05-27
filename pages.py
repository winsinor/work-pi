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


def build_weather_page(store: DataStore) -> dict:
    weather = get_weather(store)
    aqi     = get_aqi(store)
    alert   = get_alerts(store)
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
        grid.append({"label": label, "temp": f"{tp:.0f}\xb0",
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
    if store.weather.stale() or store.alerts.stale():
        page["stale"] = True
    return page


def build_commute_page(store: DataStore) -> dict | None:
    if not in_commute_window(store.cfg):
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
    return page


def build_calendar_page(store: DataStore) -> dict | None:
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
                if len(title) > 28:
                    title = title[:27] + "…"
                label    = "Next" if i == 0 else "Then"
                time_str = f"{s.strftime('%a. %-I:%M')} - {e.strftime('%-I:%M %p')}"
                lines.append({"text": f"{label}: {title}", "color": "white", "wrap_left": True})
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

    if len(upcoming) > 1:
        t2 = upcoming[1].get("title", "")
        lines.append({"text": f"Then: {t2}", "color": "grey", "wrap_left": True})
    else:
        lines.append({"text": "nothing after this event", "color": "grey"})

    page = {"_name": "calendar", "title": "Calendar", "lines": lines}
    if store.ics_events.stale():
        page["stale"] = True
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


def build_shutdown_page() -> dict:
    return {
        "_name": "shutdown",
        "title": "Shutting Down",
        "lines": [
            {"text": "Safe to unplug", "size": 2, "color": "white"},
        ],
    }


# ── Top-level display assembler ──────────────────────────────────────────────

def build_spotify_page(store: DataStore) -> dict | None:
    sp = get_spotify(store)
    if not sp:
        return None
    return {
        "_name":   "spotify",
        "title":   "Now Playing",
        "track":   sp.get("track", ""),
        "artist":  sp.get("artist", ""),
        "album":   sp.get("album", ""),
        "art_url": sp.get("art_url"),
    }


def build_display(store: DataStore) -> dict:
    state, return_date, event_title = get_work_state(store)

    if state == "WFH":
        return {"pages": [{"_name": "wfh", "title": "Working From Home", "lines": [
            {"text": "Working From Home", "size": 3, "color": "white"},
        ]}], "display_mode": "WFH"}

    if state == "OOO":
        ret_str = return_date.strftime("%a %b %-d") if return_date else ""
        lines   = [{"text": "Out of Office", "size": 3, "color": "white"}]
        if ret_str:
            lines.append({"text": f"Returning on {ret_str}", "size": 1, "color": "cyan"})
        return {"pages": [{"_name": "ooo", "title": "Out of Office", "lines": lines}],
                "display_mode": "OOO"}

    if state == "HOLIDAY":
        title_text = event_title or "Holiday"
        return {"pages": [{"_name": "holiday", "title": "Holiday", "lines": [
            {"text": title_text, "size": 3, "color": "white"},
        ]}], "display_mode": "HOLIDAY"}

    tz = store.cfg.get("location", {}).get("timezone")
    pages = [build_clock_page(tz)]
    for fn in (build_spotify_page, build_calendar_page, build_weather_page, build_commute_page):
        try:
            page = fn(store)
            if page:
                pages.append(page)
        except Exception as exc:
            print(f"[pages] {fn.__name__} failed: {exc}")

    return {"pages": pages, "display_mode": "NORMAL"}


def get_display(store: DataStore) -> dict:
    if not store.display.fresh():
        store.display.set(build_display(store))
    return store.display.get()
