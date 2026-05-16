# Feature Tracker — work-pi & commute-display

_Consolidated view of implemented features, todos, and next steps across both projects._

---

## Architecture Recap

| Repo | Role |
|---|---|
| **work-pi** | Standalone work desk display — fetches and renders everything on the Pi itself; no external server |
| **commute-display** | Pi 4 server (`server.py`) + home CYD thin client + work Pi thin client |

Both projects drive a work Pi framebuffer display showing clock, calendar, weather, and commute. `work-pi` is the self-contained version; `commute-display` is the server-offloaded version.

---

## Implemented Features

### Work Display — Shared Features (both repos)

| Feature | work-pi | commute-display |
|---|---|---|
| Clock page (time, day, date) | ✅ | ✅ |
| Calendar page — Outlook ICS feed, today's events only | ✅ | ✅ |
| Florence / work weather page (Open-Meteo) | ✅ | ✅ |
| AQI overlay (Open-Meteo Air Quality) | ✅ | ✅ |
| 5-slot extended hourly grid | ✅ | ✅ |
| Commute page — TomTom routing, two routes, M–F 3–6 pm | ✅ | ✅ |
| WFH display override (all-day ICS keyword match) | ✅ | ✅ |
| OOO display override + return date | ✅ | ✅ |
| Holiday display override | ✅ | ✅ |
| OOO return date skips weekends & holidays | ✅ | ✅ |
| systemd service | ✅ | ✅ |
| Live layout via `work_layout.json` (hot-reload) | ✅ | ✅ |
| SVG weather icons | ✅ | ✅ |

### Work Display — work-pi only

| Feature | Notes |
|---|---|
| Web setup UI (8 tabs) | WiFi, Location, Addresses, API Keys, Calendar, Keywords, Display, Intervals |
| WiFi management via NetworkManager (`nmcli`) | Scan, connect, status — no SSH needed |
| Multi-hardware support (SPI framebuffer + HDMI) | Auto-detects `/dev/fb0` vs `/dev/fb1` |
| GPIO buttons — advance page + graceful shutdown | BCM pins 24 / 23; hold 5 s to shut down |
| NWS weather alerts | US only; shown on forecast page |
| Display rotation setting (0 / 90 / 180 / 270°) | Configurable from setup UI |
| PNG icon fallback (`rsvg-convert` pre-render) | Works on Pi 1 B+ / Zero W without cairosvg |
| Configurable WFH / OOO / Holiday keywords | Editable from setup UI Keywords tab |
| Per-page enabled/disabled toggle | `work_layout.json` `"enabled": false` |
| `display_cache_s` — cached frame reuse | Skips PIL redraw when data hasn't changed |

### Work Display — commute-display only

| Feature | Notes |
|---|---|
| Pre-rendered RGB565 frames served from server | Work Pi is a pure thin client — no Pillow on Pi |
| Web layout editor (`/work/editor`) | Sidebar, live preview, D-pad positioning, save/reset |
| Commute trend (`+X min vs normal`) | TomTom historical travel time |
| Traffic cause detection | TomTom Incidents API — accident / road work / weather / congestion |
| `/work/state` debug endpoint | Returns current WFH/OOO/HOLIDAY state + return date |
| `/work/restart` endpoint | Saves layout then restarts server process |

### Home Display — commute-display only

| Feature | Notes |
|---|---|
| Commute Times (3 routes, trend, cause) | TomTom routing + Incidents API |
| Rain Alert | When rain ≥ 40% within 3 hours |
| Weather — moon phase, astronomy events, sunset countdown | Open-Meteo + ephem |
| Plants watering tracker | Days since watered, tap to mark watered |
| Trash day reminder | Tuesday ≥ 3 pm |
| Sports pages (Reds / Bengals / FCC) | Season-gated |
| Cincy Events | From `config.json` |
| Word of the Day | Daily word + definition, cached |
| Pi Diagnostics | CPU temp, RAM, disk, IP |
| Custom Pages | Via home page editor |
| Calendar push endpoint | Fingerprint dedup, 24h window, file persistence |

---

## TODO

### work-pi

| Item | Priority | Notes |
|---|---|---|
| Stale-data indicator | Low | Visual warning if data fetch is stale |
| Weather alert banner | Low | NWS alerts already fetched; just needs a rendered banner on the forecast page |
| Stats / diagnostics page | Low | CPU temp, RAM, disk, uptime — data available locally |
| Brightness control via GPIO buttons | Low | If hardware buttons are wired, could reuse k1/k2/k3 for brightness |
| Color palette toggle | Low | White-background mode for brighter environments |

### commute-display

| Item | Priority | Notes |
|---|---|---|
| Extract `common.py`, `home_pages.py`, `work_pages.py` from `server.py` | Medium | Do when `server.py` next needs a big edit; don't split mid-feature |
| Update `CLAUDE.md` with file map after split | Low | After the split above |
| Hardware buttons — brightness control on work Pi | Low | k1/k2/k3 on Pi B+; framebuffer or backlight GPIO |
| Stale-data indicator | Low | Visual warning if `/work` fetch is older than 2× poll interval |
| Weather alert banner for work location | Low | NWS alert for Florence KY; colored bar on work weather page |
| Color palettes — white background option | Low | `dark` vs `light`; palette dict in config, not scattered `if dark` checks |
| Touch screen — manual page advance | Low | Tap anywhere; resume auto-cycle after |
| Stats page — Pi system info | Low | CPU temp, load, RAM, uptime, disk, throttle state |

---

## Next Steps

_From commute-display `NEXT.md` (updated 2026-05-08):_

### Immediate
- [ ] Physical verification on work display (rotation, colors, all pages)

### Short Term
- [ ] (None currently tracked)

### Nice-to-Haves
- [ ] Stale-data indicator on work display (both repos)
- [ ] Weather alert banner for work location (both repos)
