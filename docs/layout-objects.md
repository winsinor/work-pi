# Layout Objects Reference

The object-based renderer (`render_objects_pil`) is activated automatically
when a page dict contains an `"objects"` key. It replaces the legacy
line-position system for those pages and provides full pixel-level control
over every element.

---

## Coordinate System

All coordinates are **center-based**: `x` and `y` describe the center of the
object's bounding box, not its top-left corner.

| Value | Meaning |
|---|---|
| `x: null` | Horizontal center of the canvas |
| `y: null` (text only) | Auto-distributed evenly in the content area |
| `y: null` (non-text) | Vertical center of the canvas |
| Explicit integer | Exact pixel coordinate (center of the object) |

The **content area** is the canvas height minus any bottom-anchored grid height.
Auto-distributed text objects fill this area evenly, ignoring explicitly
positioned objects and grids.

---

## Object Types

### `background`

Sets the canvas background color or image. Must appear before other objects
(or at least before the first render call). At most one background object
should appear per page.

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | string | — | `"background"` |
| `color` | string | `"black"` | Named color (see color palette) |
| `image` | string | `""` | Path to a JPEG/PNG file. Relative paths are resolved from the project root. Overrides `color` if file exists. |

```json
{"type": "background", "color": "black"}
{"type": "background", "image": "assets/bg.jpg"}
```

---

### `text`

Draws a single line of text (or wrapped text if `overflow` includes `"wrap"`).

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | string | — | `"text"` |
| `text` | string | `""` | The string to render |
| `h` | int | `14` | Font size in points |
| `color` | string | `"white"` | Named color |
| `align` | string | `"center"` | `"center"`, `"left"`, or `"right"` |
| `x` | int\|null | `null` | Center-x pixel (null = canvas center) |
| `y` | int\|null | `null` | Center-y pixel (null = auto-distribute) |
| `max_width` | int\|null | `null` | Max pixel width before overflow rules apply. Default is computed from margins and alignment. |
| `overflow` | list\|string | `["shrink","truncate"]` | Ordered list of overflow strategies: `"shrink"`, `"wrap"`, `"truncate"` |
| `visible` | bool | `true` | Set `false` to hide without removing |

**Overflow strategies** are applied in order:

1. `"shrink"` — reduce font size down to `overflow.min_font_pct` % of `h`
2. `"wrap"` — split into multiple lines (wraps at word boundaries)
3. `"truncate"` — cut the string and append `..`

```json
{"type": "text", "text": "Hello World", "h": 32, "color": "white"}
{"type": "text", "text": "Long label", "h": 18, "align": "left", "x": 10, "y": 50,
 "overflow": ["shrink", "truncate"]}
```

---

### `line`

Draws a straight line between two points.

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | string | — | `"line"` |
| `x1` | int | `0` | Start x |
| `y1` | int | `H/2` | Start y |
| `x2` | int | `W` | End x |
| `y2` | int | `H/2` | End y |
| `color` | string | `"grey"` | Named color |
| `width` | int | `1` | Line width in pixels |

```json
{"type": "line", "x1": 0, "y1": 160, "x2": 480, "y2": 160, "color": "grey", "width": 1}
```

---

### `image`

Pastes a PNG or JPEG onto the canvas. Supports transparency (RGBA PNG).

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | string | — | `"image"` |
| `path` | string | — | File path. Relative paths resolved from project root. |
| `x` | int\|null | `null` | Center-x (null = canvas center) |
| `y` | int\|null | `null` | Center-y (null = canvas center) |
| `width` | int | source width | Scale to this width |
| `height` | int | source height | Scale to this height |

```json
{"type": "image", "path": "assets/logo.png", "x": 240, "y": 40, "width": 80, "height": 80}
```

---

### `icon`

Draws a weather icon by name. Checks `icons/<name>.png` first (user-supplied),
then `icons/<name>.svg` (bundled), then falls back to a PIL-drawn primitive.

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | string | — | `"icon"` |
| `icon` | string | `"sun"` | Icon name (see table below) |
| `x` | int\|null | `null` | Center-x |
| `y` | int\|null | `null` | Center-y |
| `size` | int | `56` | Diameter in pixels (`size × size` bounding box) |

**Canonical icon names:**

| Name | WMO codes | Description |
|---|---|---|
| `sun` | 0 | Clear sky |
| `partly_cloudy` | 1, 2 | Mainly clear / partly cloudy |
| `cloud` | 3, 45, 48 | Overcast / fog |
| `rain` | 51–67, 80–82 | Drizzle / rain / showers |
| `heavy_rain` | 55, 65, 67, 82 | Heavy rain |
| `thunderstorm` | 95–99 | Thunderstorm |
| `snow` | 71–77, 85, 86 | Snow |
| `fog` | 45, 48 | Fog / rime fog |

To supply a custom icon, drop a PNG named `<icon-name>.png` into the `icons/`
directory. It will be scaled to `size × size` pixels and composited with RGBA
transparency support.

```json
{"type": "icon", "icon": "partly_cloudy", "x": 420, "y": 60, "size": 80}
```

---

### `grid`

Renders the hourly forecast strip across the bottom of the display.

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | string | — | `"grid"` |
| `data` | list | `[]` | List of cell dicts (see below) |
| `y` | int\|null | `null` | Top-y of the grid (null = bottom-anchored) |
| `height` | int | layout default | Grid height in pixels |
| `columns` | int | layout default | Number of columns |
| `label_size` | int | layout default | Font size for label row |
| `temp_size` | int | layout default | Font size for temperature row |
| `rain_size` | int | layout default | Font size for rain % row |

**Cell dict fields:**

| Field | Type | Description |
|---|---|---|
| `label` | string | Column header (e.g. `"3pm"`) |
| `temp` | string | Temperature string (e.g. `"72°"`) |
| `rain` | string | Precipitation string (e.g. `"40%"`) |
| `rain_color` | string | Named color for the rain value |

```json
{
  "type": "grid",
  "height": 90,
  "data": [
    {"label": "3pm", "temp": "72°", "rain": "20%", "rain_color": "white"},
    {"label": "5pm", "temp": "68°", "rain": "60%", "rain_color": "cyan"}
  ]
}
```

---

## Color Palette

Named colors supported by `_pil_color()`:

| Name | RGB |
|---|---|
| `white` | 255, 255, 255 |
| `cyan` | 0, 200, 220 |
| `green` | 0, 210, 80 |
| `yellow` | 255, 204, 0 |
| `red` | 220, 50, 50 |
| `orange` | 255, 140, 0 |
| `grey` / `darkgrey` | 155, 155, 155 |
| `black` | 0, 0, 0 |
| `magenta` | 220, 50, 220 |
| `blue` | 40, 80, 220 |
| `brown` | 160, 80, 0 |

Any unrecognised name falls back to white.

---

## Global Overflow Setting

`LAYOUT_DEFAULTS["overflow"]["min_font_pct"]` (default `60`) sets the floor for
the `"shrink"` strategy as a percentage of the original font size. For example,
with `h: 20` and `min_font_pct: 60`, shrink will not go below `12 pt`.

Override in `work_layout.json`:

```json
{
  "overflow": {"min_font_pct": 50}
}
```

---

## Complete Page Example

```json
{
  "_name": "my_page",
  "objects": [
    {"type": "background", "color": "black"},
    {"type": "text", "text": "Good Morning", "h": 40, "color": "white"},
    {"type": "text", "text": "Today's weather", "h": 18, "color": "cyan"},
    {"type": "icon", "icon": "partly_cloudy", "x": 420, "y": 60, "size": 80},
    {"type": "line", "x1": 10, "y1": 200, "x2": 470, "y2": 200, "color": "grey"},
    {"type": "text", "text": "Tap right to advance, left to go back",
     "h": 14, "color": "darkgrey", "y": 290}
  ]
}
```

---

## Testing

Render the built-in demo page to a PNG (no display hardware required):

```bash
cd /home/pi/work-pi
python3 - <<'EOF'
from pages import build_objects_demo_page
from render import render_page_pil
img = render_page_pil(build_objects_demo_page())
img.save("/tmp/objects_demo.png")
print("Saved /tmp/objects_demo.png")
EOF
```

Copy the PNG to your computer to inspect the layout:

```bash
scp pi@<pi-ip>:/tmp/objects_demo.png .
```
