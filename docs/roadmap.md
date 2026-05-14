# Feature Roadmap

Ideas and planned improvements that are not yet implemented.
Items are roughly grouped by area and include design notes to guide future work.

---

## Custom Weather Icons

### Full icon type set
Define a canonical PNG for every WMO weather code so custom icon packs can be
complete. The current named set covers the common codes; the gaps are:

| Missing name | WMO codes |
|---|---|
| `drizzle` | 51, 53 |
| `freezing_drizzle` | 56, 57 |
| `freezing_rain` | 66, 67 |
| `snow_showers` | 85, 86 |
| `sleet` | 68, 69 |
| `blizzard` | 75, 77 |
| `hail` | 96, 99 |

**Action:** Add the missing names to `_STATIC_ICON_MAP` in `render.py`, add
matching SVG fallbacks to `icons/`, and document the names in
`docs/layout-objects.md`.

### SVG custom icons
The current PNG loader scales raster images. A future SVG loader could use
`cairosvg` at the correct `output_width` to produce sharp icons at any size.
Priority chain would become: user PNG → user SVG → bundled SVG → PIL fallback.

### Icon template
Provide a blank `icons/template.png` (transparent, 200×200) with guides so
users can paint their own icons in the correct style.

---

## Grid Improvements

### Hours vs. days mode

Add a `mode` field to the `grid` object type:

```json
{"type": "grid", "mode": "hourly", "step_hours": 2, "start_offset_hours": 1}
{"type": "grid", "mode": "daily",  "days": 5}
```

- **Hourly:** label = formatted hour (`3pm`), temp = temperature at that hour,
  rain = precip probability. `step_hours` sets the gap between columns;
  `start_offset_hours` sets the offset from now (round up to next full hour if
  `round_up: true`).
- **Daily:** label = day-of-week abbreviation (`Mon`), temp = `"Hi / Lo"` string,
  rain = daily precip probability.

### Configurable cell dividers

```json
{"type": "grid", "divider_color": "grey", "divider_width": 1}
```

Currently the divider color and width are hard-coded in `_render_hourly_grid()`.
Expose them as per-object overrides (already has a `g_layout` merge pattern —
extend it to include `divider_color` and `divider_width`).

### Per-row Y offsets

Allow each data row within a column (label, temp, rain) to have its own
vertical offset relative to the grid top, so taller grids can breathe:

```json
{
  "type": "grid",
  "row_offsets": {"label": 5, "temp": 25, "rain": 52}
}
```

---

## Text / Layout Improvements

### Alignment-driven area expansion

Currently `max_width` is calculated from margins symmetrically. Left-aligned
objects should be able to grow rightward into unused space; right-aligned
objects should grow leftward. This makes the display area feel natural without
needing explicit `max_width` overrides.

Design sketch:
- `align: "left"` + `x: 10` → `max_width = W - 10 - right_margin`
- `align: "right"` + `x: 470` → `max_width = 470 - left_margin`
- `align: "center"` → max_width from margins (current behavior)

### Max-window bounding box (editor feature)

When a `max_width` is set on a text object, the layout editor (future) should
draw a dashed border showing the bounds. No change needed in the renderer.

---

## Touch / Input Improvements

### Options menu (center long-press)

Replace the stats-only overlay with a proper menu overlay:

```
┌─────────────────────────────────┐
│        Options                  │
│  [System Stats]                 │
│  [Shutdown ←hold 3s]            │
│  [Cancel]                       │
└─────────────────────────────────┘
```

State machine:
1. Center long-press (≥2 s) → show menu
2. Touch left zone → previous item, touch right zone → next item
3. Touch center short-press → activate selected item
4. "Shutdown" item: show hold-to-confirm animation (progress bar fills over 3 s)
5. Any non-action tap → dismiss

This requires a small UI state machine in `work_display.py`. Render the menu
as a PIL overlay (similar to `_render_stats_frame`).

### Brightness control

Add a brightness slider to the options menu. Control via:
- `echo <0-255> > /sys/class/backlight/*/brightness` (if backlight driver
  exposes it)
- Or PWM via `gpiozero` on the backlight pin

---

## Visual / Web Editor

A browser-based WYSIWYG editor served on the setup port would let users:

- Drag objects onto a live canvas preview
- Set all object fields from form controls
- Preview overflow behavior before saving
- Save the layout as `work_layout.json` or a custom page JSON

This is a significant undertaking (likely a separate JS front-end hitting a
small REST API). Sketch the API endpoints first:

```
GET  /editor          → HTML page
GET  /editor/preview  → render current editor state → PNG
POST /editor/save     → write to work_layout.json
```

---

## Installation / Infrastructure

### Directions for dummies

Expand `docs/setup.md` with screenshots or ASCII diagrams showing:
- The Raspberry Pi Imager "Advanced options" screen
- Where to find the IP address
- The browser config page

### CYD (Cheap Yellow Display) support

A second cheap ESP32-based display can show a mirrored or different page.
Would require a small HTTP endpoint returning a pre-rendered JPEG or the page
JSON, and a MicroPython client on the CYD.

---

## Known Bugs / Polish

- `render_page_pil` line-gap calculation counts `y=None` lines correctly but
  does not account for the icon row reducing available width — this can cause
  text to overlap the icon at small font sizes.
- The stale-data stripe is overwritten on every frame render; if the render
  itself fails, the stripe may appear for one cycle before clearing. Consider
  tracking the last successful `_write_frame` call instead of the last successful
  render to make the stripe stickier.
