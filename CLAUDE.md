# CLAUDE.md — work-pi dashboard

Raspberry Pi 1B+ desk dashboard with a 320×240 ILI9341 SPI TFT display. Fetches weather, commute times, calendar events, AQI, and alerts; renders pages as RGB565 frames written directly to `/dev/fb1`.

## Hardware

- Pi 1B+ (ARMv6 32-bit, single core, 512 MB RAM)
- 320×240 ILI9341 TFT on SPI → `/dev/fb1`
- XPT2046 touch controller → `/dev/input/event4` (detected automatically)
- GPIO23 = K2 button (short press → toggle stats overlay)
- GPIO24 = K3 button (press → advance page)
- Service runs as root: `sudo systemctl restart work-dashboard`

## Pi install

- **Repo lives at `/home/pi/work-pi`** on the Pi
- Service is `work-dashboard`, runs as root
- `config.json` lives only on the Pi — **gitignored, never commit it**
- `config.example.json` is the committed template

**Standard update command (run on Pi after every push):**
```bash
cd /home/pi/work-pi && git pull origin main && sudo systemctl restart work-dashboard
```

## Development workflow

**All development commits go directly to `main`.** Do not create feature branches unless the user explicitly asks for one.

```bash
# After making changes in this remote session — commit and push to main
git add <files>
git commit -m "description"
git push origin main
# Then tell the user to run the standard update command above
```

```bash
# On Pi — edit config (owned by root, service runs as root)
sudo python3 -c "import json,sys; c=json.load(open('config.json')); c['key']='val'; json.dump(c,open('config.json','w'),indent=2)"

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
| `render.py` | PIL rendering, `load_layout()`, `render_page_rgb565()`, `solid_frame()` |
| `pages.py` | Page data builders (`get_display()`, `build_setup_page()`, etc.) |
| `data.py` | All data fetchers (weather, commute, calendar, AQI, alerts) + `DataStore` |
| `stats.py` | `StatsMonitor` + `render_stats_rgb565()` — bitmap-font stats overlay |
| `touch.py` | evdev touch reader — unbuffered, dual-axis coord capture |
| `setup_server.py` | HTTP config server (port 8080) + layout editor backend |
| `config.py` | `load()`, `is_complete()`, `resolve_font_path()` |
| `setup/index.html` | Setup web UI (Wi-Fi, location, API keys, display, GPIO, etc.) |
| `editor/work/` | Layout editor (HTML + JS + CSS) |
| `config.json` | Operational config — **gitignored, never commit** |
| `work_layout.json` | Visual layout overrides — committed, edited via `/editor/work` |
| `config.example.json` | Template for config.json |

## Architecture

```
work_display.py
  ├── _start_fetch_threads()  — weather/commute/calendar/aqi/alerts loops (daemon threads)
  ├── _start_button_threads() — gpiozero K2/K3 wiring
  ├── touch.start_touch()     — evdev touch → nav queue or stats toggle
  ├── _nav_q (Queue[int])     — +1 / -1 from both touch and K3 GPIO
  └── main loop
        ├── stats mode: stats_mod.render_stats_rgb565() → fb, wait on _stats_wake(2s)
        └── page mode:  render_page_rgb565(page, layout) → fb, wait on _nav_q(dwell)
```

**Config vs layout separation**: `config.json` (setup screen) = operational settings. `work_layout.json` (layout editor) = visual/positional layout. They don't overlap. `page_dwell_s` lives only in config.

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
- `POWEROFF_Y_FRAC = 0.59` controls where the button is drawn, not tap detection (power-off is long-press to avoid XPT2046 Y-axis inversion issues)
- Layout constants in `stats.py` (`ROW_H`, `LBL_W`, etc.) are tuned for 320×240. If the display size changes, update them manually — the stats overlay is **not** auto-scaled.
- **Do not use `anchor="rt"` with bitmap fonts** — it's silently ignored; use explicit `x = W - VAL_W + offset`

## Setup web UI (`http://<pi-ip>:8080`)

- **Always running**, even in normal display mode — allows reconfiguration without restart
- Main config endpoint: `POST /api/config` — saves `config.json`, runs `timedatectl set-timezone`, signals `config_saved` event which triggers `os.execv` restart
- Layout editor at `/editor/work` — saves to `work_layout.json`
- **Screenshot of the live display** is on the **Display tab** — button hits `/api/screenshot`
- Location "Look up" button geocodes via Nominatim then auto-detects timezone via `timeapi.io` — populates lat, lon, and timezone dropdown automatically

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

## RGB565 framebuffer

```python
raw = img.tobytes()  # RGBRGB... bytes
buf = bytearray(W * H * 2)
for i in range(W * H):
    r, g, b = raw[i*3], raw[i*3+1], raw[i*3+2]
    p = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    buf[i*2]   = p & 0xFF   # little-endian
    buf[i*2+1] = p >> 8
```

## Known Pi environment gotchas

**Timezone:**
- Pi system timezone defaults to UTC. `config.json → location.timezone` is the source of truth for display times.
- Always use `ZoneInfo(tz)` explicitly — **never `datetime.astimezone()` with no argument** (uses system timezone, not configured timezone).
- `setup_server.py` runs `timedatectl set-timezone` on every config save to keep system clock in sync.
- To manually fix: `sudo timedatectl set-timezone America/New_York` (or the correct zone).

**Calendar / ICS:**
- Outlook exports all-day events as midnight-to-midnight datetimes instead of bare `date` objects. `fetch_work_state` detects these by checking `sv.hour == 0 and sv.minute == 0` after timezone conversion.
- `recurring_ical_events.of(cal).between(start, end)` is passed naive datetimes from `local_now()` — these represent local time in the configured timezone.
- `icalendar>=6.1.0` is required. On Debian Bullseye/Bookworm, `pip install` may need `--break-system-packages` or use a venv if SSL errors occur.

**Data fetching:**
- All fetchers retry on a 30-second backoff after any failure.
- Fetch threads are daemon threads — they die with the main process.

**Display rotation:**
- If the setup screen appears upside-down, set `DISPLAY_ROTATION=180` environment variable.

**install.sh:**
- Configures NetworkManager to not manage the wlan0 interface until setup is complete, to avoid breaking existing WiFi.
- Waits for DNS resolution before running `pip install`.

## Touch calibration

Default calibration (`min_x=200, max_x=3900, min_y=200, max_y=3900`) may not be perfectly aligned for this screen — adjust via the `"touch"` section in `config.json` if taps register in the wrong position.
