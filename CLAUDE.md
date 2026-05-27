# CLAUDE.md — work-pi dashboard

Raspberry Pi 1B+ desk dashboard with a 320×240 ILI9341 SPI TFT display. Fetches weather, commute times, calendar events, AQI, and alerts; renders pages as RGB565 frames written directly to `/dev/fb1`.

## Hardware

- Pi 1B+ (ARMv6 32-bit, single core, 512 MB RAM)
- 320×240 ILI9341 TFT on SPI → `/dev/fb1`
- XPT2046 touch controller → `/dev/input/event4` (detected automatically)
- GPIO23 = K2 button (short press → toggle stats overlay)
- GPIO24 = K3 button (press → advance page)
- Service runs as root: `sudo systemctl restart work-dashboard`

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

- Activated by long-press center screen or short-press K2 (GPIO23)
- Uses PIL bitmap font (`ImageFont.load_default(size=N)`) — no TrueType, no file I/O
- Updates every ~2 seconds (`_stats_wake.wait(timeout=2)`)
- Tap anywhere to dismiss; long-press anywhere to power off
- `POWEROFF_Y_FRAC = 0.59` controls where the button is drawn, not tap detection (power-off is long-press to avoid XPT2046 Y-axis inversion issues)
- Layout constants in `stats.py` (`ROW_H`, `LBL_W`, etc.) are tuned for 320×240. If the display size changes, update them manually — the stats overlay is **not** auto-scaled.
- **Do not use `anchor="rt"` with bitmap fonts** — it's silently ignored; use explicit `x = W - VAL_W + offset`

## Setup web UI (`http://<pi-ip>:8080`)

- Always running (even in normal mode — allows reconfiguration without restart)
- POST `/save` saves `config.json` then signals `config_saved` event
- Layout editor at `/editor/work` — saves to `work_layout.json`
- After saving config, `os.execv` restarts the process

## Dwell time

`page_dwell_s` from `config.json → cfg["display"]["page_dwell_s"]` is the **only** source of dwell time. The layout editor does not have per-page dwell. `default_dwell` is read once in `main()` and used directly:

```python
dwell = default_dwell  # always; do not look up from layout
```

## Custom images

PNG/JPG/BMP files in `custom_images/` directory are appended as extra pages. Time window controlled by `config.json`:

```json
"custom_images": { "display_start_h": 7, "display_end_h": 22 }
```

Supports overnight windows (start > end): `_ci_start > _ci_end and (_now_h >= _ci_start or _now_h <= _ci_end)`.

## GPIO buttons (gpiozero)

```python
k2 = Button(gpio=23, pull_up=True)
k3 = Button(gpio=24, pull_up=True)
k2.when_pressed = lambda: toggle_stats_fn()  # short press = stats toggle
# k3 handled in _k3_loop thread via k3.wait_for_press() → _nav_q.put(+1)
```

Shutdown via GPIO was removed. Power off only via stats overlay bottom tap.

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

## Development workflow

**Branch strategy — important:**
- The Pi (`/home/pi/work-pi`) always runs from **`main`**. It does `git pull origin main`.
- Claude Code sessions develop on a session feature branch (e.g. `claude/todo-review-vJkJw`).
- **Always merge the feature branch into `main` and push `main` before telling the user to pull.**
- Never tell the user to pull from a feature branch — they'll get "already up to date" and the fix won't land.

```bash
# Typical end-of-session deploy
git checkout main && git merge <feature-branch> --no-edit && git push origin main

# User runs on Pi
cd /home/pi/work-pi && git pull origin main && sudo systemctl restart work-dashboard
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

## Known Pi environment notes

- Pi system timezone defaults to UTC. `config.json → location.timezone` is the source of truth for display times. Always use `ZoneInfo(tz)` explicitly — never `datetime.astimezone()` with no argument.
- Setup server (`setup_server.py`) runs `timedatectl set-timezone` on config save to keep the system clock in sync.

## Touch calibration

Default calibration (`min_x=200, max_x=3900, min_y=200, max_y=3900`) may not be perfectly aligned for this screen — adjust via the `"touch"` section in `config.json` if taps register in the wrong position.
