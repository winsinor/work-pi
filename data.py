"""Data fetching — weather, commute, calendar, AQI, work state."""
from __future__ import annotations

import hashlib
import re
import time
from datetime import date, datetime, timedelta
from urllib.parse import quote

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # Python < 3.9 fallback — system timezone used

import requests

try:
    import icalendar
    import recurring_ical_events
    _ICS_AVAILABLE = True
except ImportError:
    _ICS_AVAILABLE = False


def local_now(cfg: dict) -> datetime:
    """Return the current time as a naive datetime in the configured timezone."""
    tz = cfg.get("location", {}).get("timezone") if cfg else None
    if tz and ZoneInfo:
        return datetime.now(ZoneInfo(tz)).replace(tzinfo=None)
    return datetime.now()


# ── WMO code tables ──────────────────────────────────────────────────────────────────

WMO_ICONS = {
    0: "sun", 1: "sun", 2: "partly_cloudy", 3: "cloud",
    45: "fog", 48: "fog",
    51: "rain", 53: "rain", 55: "heavy_rain",
    61: "rain", 63: "rain", 65: "heavy_rain",
    71: "snow", 73: "snow", 75: "snow",
    80: "rain", 81: "rain", 82: "heavy_rain",
    95: "thunderstorm", 96: "thunderstorm", 99: "thunderstorm",
}

WMO_DESC = {
    0: "Clear", 1: "Mostly Clear", 2: "Partly Cloudy", 3: "Overcast",
    45: "Fog", 48: "Icy Fog",
    51: "Lt Drizzle", 53: "Drizzle", 55: "Hvy Drizzle",
    61: "Lt Rain", 63: "Rain", 65: "Heavy Rain",
    71: "Lt Snow", 73: "Snow", 75: "Heavy Snow",
    80: "Showers", 81: "Showers", 82: "Hvy Showers",
    95: "Thunderstorm", 96: "Thunderstorm", 99: "Thunderstorm",
}


# ── Simple cache helper ───────────────────────────────────────────────────────────────

class _Cache:
    def __init__(self, ttl: float):
        self.ttl = ttl
        self.data = None
        self.fetched_at = 0.0

    def fresh(self) -> bool:
        return self.data is not None and time.time() - self.fetched_at < self.ttl

    def stale(self) -> bool:
        return self.data is not None and time.time() - self.fetched_at > 2 * self.ttl

    def set(self, data):
        self.data = data
        self.fetched_at = time.time()

    def get(self):
        return self.data


class DataStore:
    """Holds all per-session caches; one instance shared across threads."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._coords: dict[str, tuple[float, float]] = {}

        ttl_w   = cfg["weather"]["update_interval_s"]
        ttl_c   = cfg["commute"]["update_interval_s"]
        ttl_ics = cfg["calendar"]["update_interval_s"]
        ttl_aqi = cfg["aqi"]["update_interval_s"]

        self.weather   = _Cache(ttl_w)
        self.commute   = _Cache(ttl_c)
        self.ics_events = _Cache(ttl_ics)
        self.work_state = _Cache(ttl_ics)
        self.aqi        = _Cache(ttl_aqi)
        self.spotify    = _Cache(cfg.get("spotify", {}).get("update_interval_s", 10))
        self.display    = _Cache(cfg.get("display_cache_s", 60))

        self.work_state.data = "NORMAL"
        self.work_state._return_date = None
        self.work_state._event_title = None


# ── Geocoding ──────────────────────────────────────────────────────────────────────

def geocode(address: str, tomtom_key: str) -> tuple[float, float]:
    url = f"https://api.tomtom.com/search/2/geocode/{quote(address)}.json"
    r = requests.get(url, params={"key": tomtom_key, "limit": 1}, timeout=10)
    r.raise_for_status()
    results = r.json().get("results", [])
    if not results:
        raise ValueError(f"Geocoding failed for: {address!r}")
    pos = results[0]["position"]
    return pos["lat"], pos["lon"]


def ensure_coords(store: DataStore) -> None:
    """Geocode configured addresses into store._coords (once per session)."""
    cfg = store.cfg
    key = cfg["api_keys"]["tomtom"]
    coords = store._coords

    if "home" not in coords:
        print("Geocoding home address...")
        coords["home"] = geocode(cfg["addresses"]["home"], key)
        print(f"  home: {coords['home']}")

    if "work" not in coords:
        print("Geocoding work address...")
        coords["work"] = geocode(cfg["addresses"]["work"], key)
        print(f"  work: {coords['work']}")

    waypoint = cfg["addresses"].get("waypoint", "").strip()
    if waypoint and "waypoint" not in coords:
        print("Geocoding waypoint address...")
        coords["waypoint"] = geocode(waypoint, key)
        print(f"  waypoint: {coords['waypoint']}")


# ── Routing helpers ──────────────────────────────────────────────────────────────────

def _road_priority(rn: str) -> int:
    u = rn.upper()
    if re.match(r'^I[-\s]\d', u):  return 0
    if re.match(r'^US[-\s]\d', u): return 1
    return 2


def _extract_via(instructions: list) -> str:
    seen = []
    for inst in instructions:
        roads = inst.get("roadNumbers", [])
        if roads:
            best = min(roads, key=_road_priority)
            if best not in seen:
                seen.append(best)
        if len(seen) >= 2:
            break
    if len(seen) >= 2:
        return f"via {seen[0]} & {seen[1]}"
    if seen:
        return f"via {seen[0]}"
    return ""


def _fetch_route_incidents(stop_coords: list[tuple[float, float]],
                           tomtom_key: str) -> str | None:
    lats = [c[0] for c in stop_coords]
    lons = [c[1] for c in stop_coords]
    bbox = f"{min(lons)-0.15},{min(lats)-0.15},{max(lons)+0.15},{max(lats)+0.15}"
    try:
        r = requests.get(
            "https://api.tomtom.com/traffic/services/5/incidentDetails",
            params={"bbox": bbox, "key": tomtom_key,
                    "fields": "{incidents{properties{iconCategory}}}"},
            timeout=8,
        )
        if r.status_code == 200:
            cats = [inc.get("properties", {}).get("iconCategory", 0)
                    for inc in r.json().get("incidents", [])]
            for test_cat, label in [
                (1, "accident"), (14, "accident"),
                (7, "road work"), (8, "road work"), (9, "road work"),
                (2, "weather"), (3, "weather"), (4, "weather"),
                (5, "weather"), (10, "weather"), (11, "weather"),
                (6, "congestion"),
            ]:
                if test_cat in cats:
                    return label
    except Exception:
        pass
    return None


def _fetch_route(stop_names: list[str], coords: dict, tomtom_key: str) -> dict:
    waypoints = ":".join(f"{coords[s][0]},{coords[s][1]}" for s in stop_names)
    url = f"https://api.tomtom.com/routing/1/calculateRoute/{waypoints}/json"
    r = requests.get(url, params={
        "key": tomtom_key,
        "traffic": "true",
        "travelMode": "car",
        "instructionsType": "text",
    }, timeout=10)
    r.raise_for_status()
    route    = r.json()["routes"][0]
    summary  = route["summary"]
    seconds  = summary["travelTimeInSeconds"]
    delay    = summary.get("trafficDelayInSeconds", 0)
    historic = summary.get("historicTrafficTravelTimeInSeconds")
    instrs   = route.get("guidance", {}).get("instructions", [])
    result = {
        "duration_seconds":      seconds,
        "duration_text":         f"{round(seconds / 60)} min",
        "distance_text":         f"{round(summary['lengthInMeters'] / 1609.34, 1)} mi",
        "traffic_delay_seconds": delay,
        "via_text":              _extract_via(instrs),
    }
    if historic and historic > 0:
        delta_min = round((seconds - historic) / 60)
        if abs(delta_min) >= 2:
            sign = "+" if delta_min > 0 else "–"
            result["trend_text"] = f"{sign}{abs(delta_min)} min vs normal"
            result["trend_bad"]  = delta_min > 0
    if delay >= 120:
        stops = [coords[n] for n in stop_names]
        cause = _fetch_route_incidents(stops, tomtom_key)
        if cause:
            result["cause"] = cause
    return result


# ── Commute window check ───────────────────────────────────────────────────────────────

def in_commute_window(cfg: dict) -> bool:
    dt = local_now(cfg)
    if cfg["commute"]["weekdays_only"] and dt.weekday() >= 5:
        return False
    t = dt.hour * 60 + dt.minute
    start = cfg["commute"]["window_start_h"] * 60
    end   = cfg["commute"]["window_end_h"] * 60
    return start <= t <= end


# ── Commute fetch ─────────────────────────────────────────────────────────────────────

def fetch_commute(store: DataStore) -> dict:
    cfg = store.cfg
    ensure_coords(store)
    coords  = store._coords
    key     = cfg["api_keys"]["tomtom"]
    labels  = cfg.get("route_labels", ["Work → Home", "Work → Waypoint → Home"])

    routes = []
    stops_main = ["work", "home"]
    r1 = _fetch_route(stops_main, coords, key)
    r1["label"] = labels[0] if labels else "Work → Home"
    routes.append(r1)

    if "waypoint" in coords:
        stops_via = ["work", "waypoint", "home"]
        r2 = _fetch_route(stops_via, coords, key)
        r2["label"] = labels[1] if len(labels) > 1 else "Work → Waypoint → Home"
        routes.append(r2)

    return {"routes": routes, "updated_at": int(time.time())}


def get_commute(store: DataStore) -> dict:
    if in_commute_window(store.cfg):
        if not store.commute.fresh():
            try:
                store.commute.set(fetch_commute(store))
            except Exception as exc:
                print(f"[commute] fetch failed: {exc}")
    return store.commute.get() or {}


# ── Weather ────────────────────────────────────────────────────────────────────────

def fetch_weather(store: DataStore) -> dict:
    cfg = store.cfg
    lat = cfg["location"]["lat"]
    lon = cfg["location"]["lon"]
    tz  = cfg["location"].get("timezone", "America/New_York")
    r = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude":         lat,
        "longitude":        lon,
        "current":          ("temperature_2m,apparent_temperature,weather_code,"
                             "precipitation_probability,relative_humidity_2m,"
                             "wind_speed_10m,wind_direction_10m"),
        "hourly":           "temperature_2m,precipitation_probability,weather_code",
        "daily":            "temperature_2m_max,temperature_2m_min,sunrise,sunset",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit":  "mph",
        "timezone":         tz,
        "forecast_days":    2,
    }, timeout=10)
    r.raise_for_status()
    return r.json()


def get_weather(store: DataStore) -> dict:
    if not store.weather.fresh():
        try:
            store.weather.set(fetch_weather(store))
        except Exception as exc:
            print(f"[weather] fetch failed: {exc}")
    return store.weather.get() or {}


# ── AQI ──────────────────────────────────────────────────────────────────────────

def _aqi_label_color(aqi: int) -> tuple[str, str]:
    if aqi <= 50:  return "Good",        "green"
    if aqi <= 100: return "Moderate",    "yellow"
    if aqi <= 150: return "Sensitive",   "orange"
    if aqi <= 200: return "Unhealthy",   "red"
    if aqi <= 300: return "V.Unhealthy", "magenta"
    return "Hazardous", "red"


def fetch_aqi(store: DataStore) -> dict:
    cfg = store.cfg
    lat = cfg["location"]["lat"]
    lon = cfg["location"]["lon"]
    tz  = cfg["location"].get("timezone", "America/New_York")
    r = requests.get("https://air-quality-api.open-meteo.com/v1/air-quality", params={
        "latitude":      lat,
        "longitude":     lon,
        "hourly":        "us_aqi",
        "timezone":      tz,
        "forecast_days": 1,
    }, timeout=10)
    r.raise_for_status()
    hourly  = r.json().get("hourly", {})
    times   = hourly.get("time", [])
    aqis    = hourly.get("us_aqi", [])
    now_str = local_now(store.cfg).strftime("%Y-%m-%dT%H:00")
    idx     = times.index(now_str) if now_str in times else -1
    if idx < 0 or idx >= len(aqis) or aqis[idx] is None:
        return {"aqi": None}
    aqi = int(aqis[idx])
    label, color = _aqi_label_color(aqi)
    return {"aqi": aqi, "label": label, "color": color}


def get_aqi(store: DataStore) -> dict:
    if not store.aqi.fresh():
        try:
            store.aqi.set(fetch_aqi(store))
        except Exception as exc:
            print(f"[aqi] fetch failed: {exc}")
    return store.aqi.get() or {"aqi": None}


# ── Calendar / ICS ───────────────────────────────────────────────────────────────────

def _event_id(ev: dict) -> str:
    raw = f"{ev.get('title','')}{ev.get('start_iso','')}{ev.get('end_iso','')}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def fetch_ics_events(store: DataStore) -> list[dict]:
    cfg = store.cfg
    ics_url = cfg["calendar"].get("ics_url", "").strip()
    if not ics_url or not _ICS_AVAILABLE:
        return []
    r = requests.get(ics_url, timeout=15)
    r.raise_for_status()
    try:
        cal    = icalendar.Calendar.from_ical(r.content)
        now    = local_now(store.cfg)
        start  = now - timedelta(minutes=30)
        end    = now + timedelta(days=7)
        raw    = recurring_ical_events.of(cal).between(start, end)
        tz_name = cfg.get("location", {}).get("timezone")
        local_tz = ZoneInfo(tz_name) if (ZoneInfo and tz_name) else None
        events: list[dict] = []
        for component in raw:
            if component.get("STATUS", "").upper() == "CANCELLED":
                continue
            dtstart = component.get("DTSTART")
            dtend   = component.get("DTEND")
            if dtstart is None:
                continue
            sv = dtstart.dt
            ev = dtend.dt if dtend else sv
            all_day = not isinstance(sv, datetime)
            if all_day:
                # Bare date → treat as all-day; convert to midnight naive datetime
                sv = datetime.combine(sv, datetime.min.time())
                ev = datetime.combine(ev, datetime.min.time()) if isinstance(ev, date) and not isinstance(ev, datetime) else (ev if isinstance(ev, datetime) else datetime.combine(ev, datetime.min.time()))
            if hasattr(sv, "tzinfo") and sv.tzinfo is not None:
                sv = sv.astimezone(local_tz).replace(tzinfo=None) if local_tz else sv.astimezone().replace(tzinfo=None)
            if hasattr(ev, "tzinfo") and ev.tzinfo is not None:
                ev = ev.astimezone(local_tz).replace(tzinfo=None) if local_tz else ev.astimezone().replace(tzinfo=None)
            title    = str(component.get("SUMMARY", "")).strip()
            location = str(component.get("LOCATION", "") or "").strip()
            if not title:
                continue
            entry = {
                "title":     title,
                "start_iso": sv.isoformat(),
                "end_iso":   ev.isoformat(),
                "location":  location,
                "all_day":   all_day,
                "_id":       _event_id({"title": title, "start_iso": sv.isoformat(),
                                        "end_iso": ev.isoformat()}),
            }
            events.append(entry)
        events.sort(key=lambda e: e["start_iso"])
        return events
    except Exception as exc:
        raise RuntimeError(f"ICS parse error: {exc}") from exc


def get_ics_events(store: DataStore) -> list[dict]:
    if not store.ics_events.fresh():
        try:
            store.ics_events.set(fetch_ics_events(store))
        except Exception as exc:
            print(f"[ics] error: {exc}")
    return store.ics_events.get() or []


# ── Work state (WFH / OOO / HOLIDAY / NORMAL) ───────────────────────────────────────

def _advance_to_workday(cal, d, ooo_kw: list[str] | None = None,
                        holiday_kw: list[str] | None = None,
                        local_tz=None) -> object:
    ooo_kw     = ooo_kw     or []
    holiday_kw = holiday_kw or ["holiday"]
    for _ in range(30):
        if d.weekday() >= 5:
            d += timedelta(days=1)
            continue
        s = datetime.combine(d, datetime.min.time())
        e = datetime.combine(d + timedelta(days=1), datetime.min.time())
        day_events = recurring_ical_events.of(cal).between(s, e)
        skip = False
        for comp in day_events:
            dtstart = comp.get("DTSTART")
            if dtstart is None:
                continue
            # Only consider all-day events (bare date or midnight datetime)
            if isinstance(dtstart.dt, datetime):
                sv = dtstart.dt
                if hasattr(sv, "tzinfo") and sv.tzinfo:
                    sv = sv.astimezone(local_tz).replace(tzinfo=None) if local_tz else sv.astimezone().replace(tzinfo=None)
                if sv.hour != 0 or sv.minute != 0:
                    continue
            title = str(comp.get("SUMMARY", "")).lower()
            if any(k in title for k in ooo_kw) or any(k in title for k in holiday_kw):
                skip = True
                break
        if skip:
            d += timedelta(days=1)
        else:
            break
    return d


def fetch_work_state(store: DataStore) -> tuple[str, object, str | None]:
    """Return (state, return_date, event_title). state is NORMAL|WFH|OOO|HOLIDAY."""
    cfg     = store.cfg
    ics_url = cfg["calendar"].get("ics_url", "").strip()

    if not ics_url or not _ICS_AVAILABLE:
        return "NORMAL", None, None

    wfh_kw     = [k.lower() for k in cfg.get("wfh_keywords", ["wfh", "working from home"])]
    ooo_kw     = [k.lower() for k in cfg.get("ooo_keywords", ["ooo", "out of office", "pto"])]
    holiday_kw = [k.lower() for k in cfg.get("holiday_keywords", ["holiday"])]

    r = requests.get(ics_url, timeout=15)
    r.raise_for_status()
    cal       = icalendar.Calendar.from_ical(r.content)
    today     = local_now(store.cfg).date()
    day_start = datetime.combine(today, datetime.min.time())
    day_end   = datetime.combine(today + timedelta(days=1), datetime.min.time())
    raw       = recurring_ical_events.of(cal).between(day_start, day_end)

    new_state  = "NORMAL"
    new_return = None
    new_title  = None

    tz_name  = cfg.get("location", {}).get("timezone")
    local_tz = ZoneInfo(tz_name) if (ZoneInfo and tz_name) else None

    for component in raw:
        dtstart = component.get("DTSTART")
        if dtstart is None:
            continue
        if isinstance(dtstart.dt, datetime):
            # Outlook exports all-day events as midnight-to-midnight datetimes
            # instead of bare dates. Treat those as all-day; skip real timed events.
            sv = dtstart.dt
            if hasattr(sv, "tzinfo") and sv.tzinfo:
                sv = sv.astimezone(local_tz).replace(tzinfo=None) if local_tz else sv.astimezone().replace(tzinfo=None)
            if sv.hour != 0 or sv.minute != 0:
                continue
        title = str(component.get("SUMMARY", "")).strip()
        if not title:
            continue
        tl = title.lower()

        # Priority: HOLIDAY > OOO > WFH. Don't break early — a later event in
        # the ICS file may have higher priority than an already-matched one.
        if any(k in tl for k in holiday_kw):
            new_state  = "HOLIDAY"
            new_title  = title
            new_return = None
            break  # nothing can beat HOLIDAY
        if any(k in tl for k in ooo_kw) and new_state != "HOLIDAY":
            dtend = component.get("DTEND")
            ev    = dtend.dt if dtend else today + timedelta(days=1)
            if isinstance(ev, datetime):
                if hasattr(ev, "tzinfo") and ev.tzinfo:
                    ev = ev.astimezone(local_tz).replace(tzinfo=None) if local_tz else ev.astimezone().replace(tzinfo=None)
                ev = ev.date()
            new_state  = "OOO"
            new_return = _advance_to_workday(cal, ev, ooo_kw, holiday_kw, local_tz)
        elif any(k in tl for k in wfh_kw) and new_state == "NORMAL":
            new_state = "WFH"

    return new_state, new_return, new_title


def get_work_state(store: DataStore) -> tuple[str, object, str | None]:
    """Cached work state; retains last known state on fetch error."""
    if not store.work_state.fresh():
        try:
            state, ret, title = fetch_work_state(store)
            store.work_state.set(state)
            store.work_state._return_date = ret
            store.work_state._event_title = title
        except Exception as exc:
            print(f"[work-state] ICS scan failed: {exc}")
            store.work_state.fetched_at = time.time()  # back off
    return (
        store.work_state.get() or "NORMAL",
        getattr(store.work_state, "_return_date", None),
        getattr(store.work_state, "_event_title", None),
    )


# ── "Later today" weather description ───────────────────────────────────────────────

def later_today_desc(hourly: dict, now: datetime) -> str | None:
    _WINDOWS = [
        ( 0,  6, "overnight"),
        ( 6, 12, "this morning"),
        (12, 18, "this afternoon"),
        (18, 24, "this evening"),
    ]
    _SEV = {95:9, 96:9, 99:9, 65:7, 75:7, 82:7, 63:6, 73:6, 81:6,
            61:5, 71:5, 80:5, 55:5, 53:4, 51:4, 45:3, 48:3,
            3:2, 2:1, 1:0, 0:0}

    cur = next(i for i, (s, e, _) in enumerate(_WINDOWS) if s <= now.hour < e)
    nxt = (cur + 1) % len(_WINDOWS)
    start_h, end_h, label = _WINDOWS[nxt]
    target_date = (now + timedelta(days=1)).date() if nxt == 0 else now.date()

    htimes = hourly.get("time", [])
    hcodes = hourly.get("weather_code", [])
    best_sev, best_wmo = -1, None
    for i, ts in enumerate(htimes):
        try:
            t = datetime.fromisoformat(ts)
        except Exception:
            continue
        if t.date() != target_date or not (start_h <= t.hour < end_h):
            continue
        wmo = hcodes[i] if i < len(hcodes) else 0
        sev = _SEV.get(wmo, 0)
        if sev > best_sev:
            best_sev, best_wmo = sev, wmo
    if best_wmo is None:
        return None
    return f"{WMO_DESC.get(best_wmo, 'Clear')} {label}"


def wind_cardinal(deg: float) -> str:
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[round(deg / 45) % 8]


# ── Spotify ───────────────────────────────────────────────────────────────────────────

import base64 as _base64

_spotify_token: dict = {"access_token": None, "expires_at": 0.0}


def _spotify_access_token(cfg: dict) -> str | None:
    """Return a valid Spotify access token, refreshing if necessary."""
    import config as _cfg_mod
    sp = cfg.get("spotify") or {}
    # Reload from disk if refresh token missing — handles OAuth done after service start
    if not sp.get("refresh_token", "").strip():
        sp = _cfg_mod.load().get("spotify") or {}
    client_id     = sp.get("client_id", "").strip()
    client_secret = sp.get("client_secret", "").strip()
    refresh_token = sp.get("refresh_token", "").strip()
    if not all([client_id, client_secret, refresh_token]):
        return None
    global _spotify_token
    if _spotify_token["access_token"] and time.time() < _spotify_token["expires_at"] - 30:
        return _spotify_token["access_token"]
    creds = _base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    r = requests.post(
        "https://accounts.spotify.com/api/token",
        headers={"Authorization": f"Basic {creds}",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=10,
    )
    r.raise_for_status()
    d = r.json()
    _spotify_token["access_token"] = d["access_token"]
    _spotify_token["expires_at"]   = time.time() + d.get("expires_in", 3600)
    return _spotify_token["access_token"]


_playlist_name_cache: dict = {}


def _fetch_playlist_name(token: str, uri: str) -> str:
    if uri in _playlist_name_cache:
        return _playlist_name_cache[uri]
    try:
        pid = uri.split(":")[-1]
        r = requests.get(
            f"https://api.spotify.com/v1/playlists/{pid}",
            headers={"Authorization": f"Bearer {token}"},
            params={"fields": "name"},
            timeout=5,
        )
        if r.status_code == 200:
            name = r.json().get("name", "")
            _playlist_name_cache[uri] = name
            return name
    except Exception:
        pass
    return ""


def fetch_spotify(store: DataStore) -> dict | None:
    """Return currently-playing track info, or None if nothing playing."""
    token = _spotify_access_token(store.cfg)
    if not token:
        return None
    r = requests.get(
        "https://api.spotify.com/v1/me/player/currently-playing",
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
    )
    if r.status_code == 204:   # no content — nothing playing
        return None
    if r.status_code != 200:
        return None
    data = r.json()
    if not data.get("is_playing"):
        return None
    item = data.get("item") or {}
    if not item:
        return None
    artists   = ", ".join(a.get("name", "") for a in item.get("artists", []))
    album     = item.get("album") or {}
    images    = sorted(album.get("images") or [], key=lambda x: x.get("width", 0))
    art_url   = next((img["url"] for img in images if img.get("width", 0) >= 150), None)
    if not art_url and images:
        art_url = images[-1]["url"]

    # Playlist name (if playing from a playlist)
    playlist = ""
    context  = data.get("context") or {}
    if context.get("type") == "playlist":
        uri = context.get("uri", "")
        playlist = _fetch_playlist_name(token, uri)
    elif context.get("type") == "collection":
        playlist = "Liked Songs"

    return {
        "track":       item.get("name", ""),
        "artist":      artists,
        "album":       album.get("name", ""),
        "art_url":     art_url,
        "progress_ms": data.get("progress_ms") or 0,
        "duration_ms": item.get("duration_ms") or 0,
        "playlist":    playlist,
    }


def get_spotify(store: DataStore) -> dict | None:
    if not store.spotify.fresh():
        try:
            store.spotify.set(fetch_spotify(store))
        except Exception as exc:
            print(f"[spotify] fetch failed: {exc}")
    return store.spotify.get()
