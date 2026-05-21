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

## Main loop

- **`glob.glob` every iteration** (`work_display.py:317`): scans the `custom_images/`
  directory on every loop iteration. Cache the sorted file list; invalidate on
  directory mtime change.
