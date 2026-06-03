# CLAUDE.md — work-pi dashboard

Raspberry Pi 1B+ desk dashboard with a 320×240 ILI9341 SPI TFT display. Fetches weather, commute times, calendar events, AQI, alerts, and Spotify now-playing; renders pages as RGB565 frames written directly to `/dev/fb1`.

## Hardware

- Pi 1B+ (ARMv6 32-bit, single core, 512 MB RAM)
- 320×240 ILI9341 TFT on SPI → `/dev/fb1`
- XPT2046 touch controller → `/dev/input/event4` (detected automatically)
- GPIO23 = K2 button (short press → toggle stats overlay)
- GPIO24 = K3 button (press → advance page)
- Service runs as root: `sudo systemctl restart work-dashboard`

## Pi install

- **Repo lives at `/home/pi/work-pi`** on the Pi
- **Install dir is `/home/pi/work-dashboard`** — service runs from here, owned by root
- Service is `work-dashboard`, runs as root
- `config.json` lives only on the Pi at `/home/pi/work-dashboard/config.json` — **gitignored, never commit it**
- `config.example.json` is the committed template

**Standard update command (run on Pi after every push):**
```bash
cd /home/pi/work-pi && git pull origin main && sudo rsync -a --exclude='.git' --exclude='config.json' --exclude='__pycache__' /home/pi/work-pi/ /home/pi/work-dashboard/ && sudo systemctl restart work-dashboard
```

**Shortcut — `deploy` command:**
```bash
# Installed automatically by install.sh. If missing:
sudo ln -sf /home/pi/work-pi/deploy /usr/local/bin/deploy && sudo chmod +x /home/pi/work-pi/deploy
# Then just run:
deploy
```

> **Note**: `sudo deploy` won't work — sudo's PATH excludes `/usr/local/bin`. Use `deploy` as the `pi` user, or run the full rsync one-liner above.

## Development workflow

**All development commits go directly to `main`.** Do not create feature branches unless the user explicitly asks for one.

```bash
# After making changes in this remote session — commit and push to main
git add <files>
git commit -m "description"
git push origin main
# Then tell the user to run: deploy
```

```bash
# On Pi — edit config (owned by root, service runs as root)
sudo nano /home/pi/work-dashboard/config.json

# Restart service
sudo systemctl restart work-dashboard

# Tail logs
journalctl -u work-dashboard -f

# Check touch events raw
sudo evtest /dev/input/event4
```

## Key files

| File | Purpose |
|------|---------|
| `work_display.py` | Main loop: fetch → render → write to fb |
| `render.py` | PIL rendering, `load_layout()`, `render_page_rgb565()`, `_img_to_rgb565()`, `solid_frame()` |
| `pages.py` | Page data builders (`build_display()`, `build_spotify_page()`, etc.) |
| `data.py` | All data fetchers (weather, commute, calendar, AQI, alerts, Spotify) + `DataStore` + `_Cache` |
| `stats.py` | `StatsMonitor` + `render_stats_rgb565()` — bitmap-font stats overlay |
| `touch.py` | evdev touch reader — unbuffered, dual-axis coord capture |
| `setup_server.py` | HTTP config server (port 8080) + layout editor backend + Spotify OAuth |
| `config.py` | `load()`, `is_complete()`, `resolve_font_path()` |
| `setup/index.html` | Setup web UI (Wi-Fi, location, API keys, Spotify, display, GPIO, etc.) |
| `editor/work/` | Layout editor (HTML + JS + CSS) |
| `deploy` | One-liner update script: git pull + rsync + restart |
| `auto-deploy.sh` | Auto-deploy script run by systemd timer every 2 min |
| `config.json` | Operational config — **gitignored, never commit** |
| `work_layout.json` | Visual layout overrides — committed, edited via `/editor/work` |
| `config.example.json` | Template for config.json |
| `icons/spotify_logo.png` | Official Spotify full logo (not committed — place manually on Pi) |

## Architecture

```
work_display.py
  ├── _fetch_gate (threading.Event) — cleared while asleep; fetch threads block on it
  ├── _start_fetch_threads()  — weather/commute/calendar/aqi/alerts/spotify loops (daemon threads)
  │     each loop calls _fetch_gate.wait() before every network fetch
  ├── _start_button_threads() — gpiozero K2/K3 wiring
  ├── touch.start_touch()     — evdev touch → nav queue or stats toggle
  ├── _nav_q (Queue[int])     — +1 / -1 from both touch and K3 GPIO
  ├── _ci_files_cache         — custom images glob cached by directory mtime
  └── main loop
        ├── sleep mode: clears _fetch_gate, renders "zzz" screensaver, waits on _nav_q(30s)
        ├── stats mode: stats_mod.render_stats_rgb565() → fb, wait on _stats_wake(2s)
        └── page mode:  render_page_rgb565(page, layout) → fb, wait on _nav_q(dwell)
```

**Config vs layout separation**: `config.json` (setup screen) = operational settings. `work_layout.json` (layout editor) = visual/positional layout. They don't overlap. `page_dwell_s` lives only in config.

**Page rendering dispatch** in `render_page_pil`:
1. `_name == "spotify"` → `render_spotify_page()` (album art + text)
2. `_name == "custom_image"` → `render_custom_image_page()` (full-frame image)
3. Everything else → standard text/icon/grid renderer

## Layout scaling

`work_layout.json` stores absolute pixel coordinates at whatever canvas size it was
originally designed at. `render.py:load_layout()` auto-scales everything to the actual
display size (`display_w`/`display_h` from config) at runtime — so the JSON does **not**
need to match the display resolution. **Do not rewrite `work_layout.json` to match display
dimensions.**

## Touch input critical notes

`touch.py` has three non-obvious requirements:

1. **Unbuffered I/O**: `open(device, "rb", buffering=0)` — Python's `BufferedReader` blocks forever on sparse input devices.

2. **XPT2046 event order**: `BTN_TOUCH=1` fires **before** `ABS_X`/`ABS_Y`. Coords must be captured after the press event, not at press time.

3. **Dual-axis lock**: `ABS_X` and `ABS_Y` arrive as separate events. `press_raw` must only be locked in once **both** `seen_x` and `seen_y` are `True` after a press. A single-flag approach captures only one coordinate → stale axis → huge `moved` → classified as drag → dropped.

Enable raw event debug logging: set `"debug": true` in the `"touch"` section of `config.json`.

## Stats overlay

- Activated by **short-press K2** (GPIO23) or **long-press center screen**
- Shows CPU%, memory, temp, IP, uptime
- Uses PIL bitmap font (`ImageFont.load_default(size=N)`) — no TrueType, no file I/O
- Updates every ~2 seconds (`_stats_wake.wait(timeout=2)`)
- **Tap anywhere to dismiss; long-press anywhere to power off**
- `POWEROFF_Y_FRAC = 0.72` controls where the power-off button is drawn
- Fonts loaded once at module level via `_ensure_stats_fonts()` — not recreated each render
- Layout constants in `stats.py` (`ROW_H`, `LBL_W`, etc.) are tuned for 320×240. If the display size changes, update them manually — the stats overlay is **not** auto-scaled.
- **Do not use `anchor="rt"` with bitmap fonts** — it's silently ignored; use explicit `x = W - VAL_W + offset`

## Setup web UI (`http://<pi-ip>:8080`)

- **Always running**, even in normal display mode — allows reconfiguration without restart
- Main config endpoint: `POST /api/config` — saves `config.json`, runs `timedatectl set-timezone`, signals `config_saved` event which triggers `os.execv` restart
- Layout editor at `/editor/work` — saves to `work_layout.json`
- **Screenshot of the live display** is on the **Display tab** — button hits `/api/screenshot`
- Location "Look up" button geocodes via Nominatim then auto-detects timezone via `timeapi.io` — populates lat, lon, and timezone dropdown automatically
- **Spotify tab** — OAuth connect flow; see Spotify section below
- **Custom images**: `POST /api/upload-image` (multipart) and `POST /api/delete-image` (JSON `{filename}`) — delete button wired in the UI; path-traversal protected

## Config error pages

`pages.py` shows an error page instead of crashing when a feature is enabled but not configured:
- **Weather**: if `location.lat`/`lon` are both unset → "Location not set" page
- **Commute**: if TomTom key or home/work addresses are missing AND it's within the commute window → "Commute not configured" page
- **Calendar**: if `calendar.ics_url` is empty → "No calendar URL" page

These use the `_cfg_err(name, lines)` helper which returns a minimal page dict.

## GPIO buttons (gpiozero)

```python
k2 = Button(gpio=23, pull_up=True)
k3 = Button(gpio=24, pull_up=True)
k2.when_pressed = lambda: toggle_stats_fn()  # short press = stats toggle
# k3 handled in _k3_loop thread via k3.wait_for_press() → _nav_q.put(+1)
```

**Shutdown via GPIO was removed.** Power off only via long-press on the stats overlay.

## Dwell time

`page_dwell_s` from `config.json → cfg["display"]["page_dwell_s"]` is the **only** source of dwell time. There is no per-page dwell. `default_dwell` is read once in `main()` and used directly:

```python
dwell = default_dwell  # always; do not look up from layout
```

## Custom images

PNG/JPG/BMP files in `custom_images/` directory are appended as extra pages. Time window controlled by `config.json`:

```json
"custom_images": { "display_start_h": 7, "display_end_h": 22 }
```

Supports overnight windows (start > end): `_ci_start > _ci_end and (_now_h >= _ci_start or _now_h <= _ci_end)`.

File list is cached by directory mtime (`_ci_files_cache`) — only re-scanned when files are added/removed.

## RGB565 framebuffer

Shared helper `_img_to_rgb565(img)` in `render.py`, imported by `stats.py`. Uses numpy fast path when available (~5–10x faster on ARMv6), falls back to pure Python:

```python
# numpy path (preferred)
arr = np.frombuffer(img.tobytes(), np.uint8).reshape(-1, 3)
r, g, b = arr[:,0].astype(np.uint16), arr[:,1].astype(np.uint16), arr[:,2].astype(np.uint16)
buf = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
return buf.astype('<u2').tobytes()
```

## Performance notes (Pi 1B+)

- **Rendering**: ~200ms/frame × 8s dwell ≈ 2.5% average CPU — dominant cost
- **ICS/calendar parsing**: `recurring_ical_events` can spike 300–500ms on ARMv6 — **minimum 5-minute interval enforced** in setup UI; do not lower below 300s
- **Layout cache**: `load_layout()` returns a cached scaled dict; only reloads on file mtime change — no deepcopy on every loop iteration
- **Custom image cache**: keyed by `(path, mtime, W, H)` — `Image.open/fit` only runs on first render per track
- **Font cache**: stats overlay fonts loaded once at module level via `_ensure_stats_fonts()`
- **Spotify album art cache**: keyed by `(url, size)` — network fetch only on first render per track; cleared at 30 entries

## Spotify now-playing

Page only appears in rotation while music is actively playing. Disappears within ~10s of pause/stop.

**Setup (one-time):**
1. Create a Spotify developer app at developer.spotify.com/dashboard
2. Add the redirect URI shown in setup UI Spotify tab to your app's Redirect URIs — it's `http://<pi-local-ip>:8080/spotify/callback`
3. Enter Client ID + Client Secret in setup UI → Save
4. Click Connect Spotify → authorize → tab closes automatically

**Redirect URI gotcha**: always derived from `_get_local_ip()` + `setup_port` — NOT the HTTP `Host` header. Both the auth URL and the token exchange callback use the same source, so they always match. If the user sees "redirect_uri not matching", the URI in their Spotify app settings doesn't match what the Pi computed — re-copy from the Spotify tab.

**Config keys** (`config.json`):
```json
"spotify": {
  "client_id": "...",
  "client_secret": "...",
  "refresh_token": "...",
  "update_interval_s": 10
}
```
`refresh_token` is written automatically by the OAuth callback — never set it manually.

**OAuth endpoints** (setup_server.py):
- `GET /api/spotify/redirect-uri` — returns the canonical redirect URI
- `GET /api/spotify/auth-url` — returns Spotify authorization URL
- `GET /spotify/callback` — exchanges code for tokens, saves refresh_token, returns success page
- `GET /api/spotify/status` — returns `{connected, has_credentials}`

**Spotify page layout** (`render.py → render_spotify_page`):
- `HEADER_H = 42` — header bar height
- `BAR_ZONE = 34` — bottom zone for progress bar + time labels (14pt)
- `LOGO_H = round((HEADER_H - 10) * 0.8)` — logo height (≈26px); right-aligned with 3px right margin
- Logo: loaded from `icons/spotify_logo.png` (official full Spotify logo PNG, aspect-ratio scaled to fit). Falls back to drawn icon + "Spotify" text if file absent.
  - To install: `cp Spotify_Full_Logo_RGB_Green.png /home/pi/work-dashboard/icons/spotify_logo.png`
  - `_load_spotify_logo(target_h)` in render.py handles the cache
- Album art: pinned to `CONTENT_Y + 2` (near top of content area, not centered)
- Text layout: `GAP = 11` between track/artist/album; artist +5px down, album +2px down from computed positions
- Track title: `_wrap_title()` tries 22pt→14pt (22pt = 20% larger base; shrinks to fit in 1 or 2 lines)
- Artist and album: `_shrink_to_fit()` — both start at 16pt, shrink up to 25% (min 12pt) before truncating. Same parameters for both.
- 2-line title: `block_h` includes both lines before `ty0` is computed; clamped to `CONTENT_Y+2` so nothing overlaps the header
- Numpy fast path: `_render_spotify_fast()` caches static frame as uint16 H×W array; only re-renders the bottom `BAR_ZONE` rows on each tick for the progress bar

## Sleep mode

Configured via Setup → Display tab.

**Hourly window**: `sleep.start_h` / `sleep.end_h` apply on the days listed in `sleep.days` (0=Mon, 6=Sun).

**All-day sleep**: `sleep.all_day_days` — days that sleep the full 24 hours regardless of the time window. Useful for weekends. Checked first in `_in_sleep_window` before the hourly logic.

```json
"sleep": {
  "enabled": true,
  "start_h": 22,
  "end_h": 7,
  "days": [0, 1, 2, 3, 4, 5, 6],
  "all_day_days": [5, 6],
  "wake_minutes": 60
}
```

**Fetch gate**: `_fetch_gate` is a `threading.Event` in `work_display.py`. All six fetch threads call `_fetch_gate.wait()` at the top of each loop. The main loop clears the gate when entering true sleep (not manually woken) and sets it when awake. No network calls happen while sleeping.

**Manual wake**: touch or button press → `_sleep_woke_at = time.time()`. Display stays awake for `wake_minutes`. Fetch gate is re-opened immediately on manual wake.

## Weather alerts

Active NWS alerts render as a full-width red banner strip (18px) just below the page header, right-aligned text. Fetched via `api.weather.gov/alerts/active`. Alert text stored in `page["alert_banner"]` — not as a line in `page["lines"]`.

## Stale-data indicator

When a data source hasn't refreshed in 2× its TTL, the page gets `stale=True` and render.py draws a 2px amber border around the entire frame. `_Cache.stale()` method handles the check. Clears automatically once data refreshes.

## Layout editor

- Canvas is 320×240 — matches display, no coordinate translation needed
- Line positions stored in `work_layout.json → line_positions[page_name][index]`
- `setAt()` in app.js pads arrays with `{}` when setting out-of-bounds indices — prevents sparse arrays → JSON null → Python None → `.get()` crash
- `render.py` normalizes positions on read: `[(p if isinstance(p, dict) else {}) for p in positions]`
- Forecast page has "Preview state" dropdown: Normal / Alert banner / Stale data
- Calendar page has "No upcoming events view" toggle for the empty-state preview

**Demo pages** for preview (`setup_server.py → _DEMO_PAGES`): `forecast`, `forecast_alert`, `forecast_stale`, `calendar`, `calendar_empty`, `commute`, `wfh`, `ooo`, `holiday`. Add new page variants here when adding features.

**Adding a new tab to setup UI**: add `data-tab="name"` to the tabs bar AND add `"name"` to the `TABS` JS array — forgetting the array causes all subsequent tabs to show wrong panels.

## Known Pi environment gotchas

**Python 3.9 compatibility:**
- Pi 1B+ runs Python 3.9 (Debian Bullseye). `bytes | None` union syntax requires Python 3.10+.
- All files that use `X | Y` type annotations must have `from __future__ import annotations` as the first import. This makes all annotations lazy strings at runtime, fixing the crash.
- `work_display.py` already has this. Add it to any new file that uses union type hints.

**Two directories — repo vs install:**
- `/home/pi/work-pi` — git repo, owned by `pi` user
- `/home/pi/work-dashboard` — install dir, owned by root (service runs as root)
- `rsync` from repo → install dir is required on every deploy; a plain `git pull + restart` won't pick up changes
- `config.json` is excluded from rsync — it lives only in the install dir
- `icons/spotify_logo.png` is also not in the repo — must be placed manually in the install dir

**Timezone:**
- Pi system timezone defaults to UTC. `config.json → location.timezone` is the source of truth for display times.
- Always use `ZoneInfo(tz)` explicitly — **never `datetime.astimezone()` with no argument** (uses system timezone, not configured timezone).
- `setup_server.py` runs `timedatectl set-timezone` on every config save to keep system clock in sync.
- To manually fix: `sudo timedatectl set-timezone America/New_York` (or the correct zone).

**Calendar / ICS:**
- Outlook exports all-day events as midnight-to-midnight datetimes instead of bare `date` objects. `fetch_work_state` detects these by checking `sv.hour == 0 and sv.minute == 0` after timezone conversion.
- `recurring_ical_events.of(cal).between(start, end)` is passed naive datetimes from `local_now()` — these represent local time in the configured timezone.
- `icalendar>=6.1.0` is required. On Debian Bullseye/Bookworm, `pip install` may need `--break-system-packages` or use a venv if SSL errors occur.
- Fetch window is `now → now + 7 days` — wide enough to show next upcoming events on the empty-state calendar page.

**Data fetching:**
- All fetchers retry on a 30-second backoff after any failure.
- Fetch threads are daemon threads — they die with the main process.
- Fetch threads block on `_fetch_gate` (threading.Event) while the display is in true sleep mode.
- Spotify thread also invalidates `store.display.fetched_at = 0` on each update so the page appears/disappears promptly.

**Git on Pi (service runs as root):**
- If git push/pull fails with "insufficient permission for adding object to repository", root owns `.git/objects`: `sudo chown -R pi:pi /home/pi/work-pi/.git`
- Always use `sudo git stash / pull / pop` when the service has written files as root.
- If `work_layout.json` has a merge conflict after stash pop: `sudo git checkout --theirs work_layout.json`

**Display rotation:**
- If the setup screen appears upside-down, set `DISPLAY_ROTATION=180` environment variable.

**install.sh:**
- Configures NetworkManager to not manage the wlan0 interface until setup is complete, to avoid breaking existing WiFi.
- Waits for DNS resolution before running `pip install`.
- Installs `python3-numpy` as optional apt dep for faster RGB565 conversion.
- Symlinks `deploy` script to `/usr/local/bin/deploy`.
- Sudoers rule grants `pi` user passwordless `sudo rsync` and `sudo systemctl restart work-dashboard`.

## Touch calibration

Default calibration (`min_x=200, max_x=3900, min_y=200, max_y=3900`) may not be perfectly aligned for this screen — adjust via the `"touch"` section in `config.json` if taps register in the wrong position.
