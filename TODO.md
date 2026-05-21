# TODO

## Rendering / PIL performance

- **RGB565 conversion** (`render.py:666–675`): replace nested `px[x,y]` loop with
  `img.tobytes()` single loop — same pattern `stats.py` already uses. ~2x faster on ARMv6.

- **Shared RGB565 helper**: extract one `_img_to_rgb565(img)` function in `render.py`,
  import it in `stats.py` to eliminate duplicated conversion code.

- **numpy RGB565 acceleration**: `sudo apt install python3-numpy`; guard with
  `try/except ImportError`; vectorized path:
  `np.frombuffer(img.tobytes(), np.uint8).reshape(-1, 3)` → bitwise ops on uint16
  array → ~5–10x faster conversion vs pure Python loop. Add `python3-numpy` to
  `install.sh` as an optional dep (low-memory skip).

- **Stats overlay fonts** (`stats.py:169–173`): 4 font objects loaded on every call
  (~every 2s when stats active). Move to lazy module-level init — zero risk change.

- **Custom image cache** (`render.py:443–448`): `Image.open().convert().fit()` runs
  every frame for custom image pages. Cache keyed by `(path, mtime, W, H)`.
  _Risk_: stale render if file is replaced without mtime changing.

- **Layout deepcopy** (`render.py:130`): `copy.deepcopy` of the ~50 KB layout dict
  on every main-loop iteration even when the file hasn't changed. Return a shared
  reference instead — callers don't mutate the layout dict during rendering.

## Features

- **Brightness control via GPIO buttons**: map K1/K2/K3 physical buttons to
  dim/medium/bright backlight levels. Needs either a PWM-capable GPIO pin wired
  to the display's backlight, or framebuffer gamma adjustment as a fallback.

- **Stale-data indicator**: show a subtle visual warning (dim border, faded clock,
  or "!" badge) when the last successful data fetch is older than 2× the poll
  interval. Track `last_fetched_at` per data source in `DataStore`; check on each
  render cycle in `pages.py`.

- **Weather alert banner**: `fetch_alerts()` already runs and the result is shown
  as a small red text line on the weather page (`pages.py:74–75`). Enhance to a
  full-width colored banner (red/orange background strip) so active NWS alerts are
  more visually prominent.

- **Color palette toggle**: add a `color_palette` config key (`"dark"` / `"light"`)
  for a white-background, dark-text mode. Define palettes as a dict in `render.py`;
  expose toggle in the setup UI and `config.json`. All color references in
  `render_page_pil` would read from the active palette.

## Main loop

- **`glob.glob` every iteration** (`work_display.py:317`): scans the `custom_images/`
  directory on every loop iteration. Cache the sorted file list; invalidate on
  directory mtime change.
