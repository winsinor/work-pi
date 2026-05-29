"""PIL rendering pipeline — page dict → RGB image → RGB565 bytes."""
from __future__ import annotations

import copy
import io
import math
import os
import time as _time_mod

try:
    import requests as _requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

try:
    import cairosvg
    _CAIROSVG_AVAILABLE = True
except ImportError:
    _CAIROSVG_AVAILABLE = False

try:
    import numpy as _np
    _NUMPY = True
except ImportError:
    _np = None
    _NUMPY = False

_BASE = os.path.dirname(os.path.abspath(__file__))
_ICONS_DIR = os.path.join(_BASE, "icons")
_LAYOUT_FILE = os.path.join(_BASE, "work_layout.json")


# ── Layout ─────────────────────────────────────────────────────────────────────────

LAYOUT_DEFAULTS: dict = {
    "canvas":  {"width": 480, "height": 320},
    "header":  {"height": 0, "bg": [0, 0, 80], "title_color": "white"},
    "footer":  {"height": 0},
    "content": {"left_margin": 6, "right_margin": 6, "line_gap_min": 2},
    "icon":    {"radius": 107, "gap": 10, "x": 390, "y": 107},
    "aqi":     {"cx": 231, "y": 16, "label_size": 21, "value_size": 35},
    "grid":    {"height": 87, "columns": 5,
                "label_size": 18, "temp_size": 24, "rain_size": 18,
                "hum_size": 12, "wind_size": 12},
    "font":    {"path": "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"},
    "pages": {
        "clock":          {"enabled": True},
        "forecast":       {"enabled": True},
        "calendar":       {"enabled": True},
        "calendar_empty": {"enabled": True},
        "commute":        {"enabled": True},
        "wfh":            {"enabled": True},
        "ooo":            {"enabled": True},
        "holiday":        {"enabled": True},
        "setup":          {"enabled": True},
        "loading":        {"enabled": True},
        "custom_image":   {"enabled": True},
    },
    "line_positions": {
        "clock": [
            {"x": None, "y": 147, "h": 94},
            {"x": None, "y": 231, "h": 38},
            {"x": None, "y": 73,  "h": 38},
        ],
        "forecast": [
            {"x": 97,  "y": 36,  "h": 64},
            {"x": 10,  "y": 127, "h": 32},
            {"x": 10,  "y": 87,  "h": 27},
            {"x": 10,  "y": 163, "h": 27},
            {"x": 334, "y": 163, "h": 24},
            {"x": 339, "y": 192, "h": 24},
            {"x": 10,  "y": 199, "h": 27},
        ],
        "calendar": [
            {"x": None, "y": 64,  "h": 40},
            {"x": None, "y": 140, "h": 48},
            {"x": None, "y": 208, "h": 32},
            {"x": 136,  "y": 268, "h": 19},
        ],
        "calendar_empty": [
            {"x": None, "y": 128, "h": 40},
            {"x": None, "y": 189, "h": 40},
        ],
        "commute": [
            {"x": None, "y": None, "h": 32},
            {"x": None, "y": None, "h": 43},
            {"x": None, "y": None, "h": 21},
            {"x": None, "y": None, "h": 32},
            {"x": None, "y": None, "h": 43},
            {"x": None, "y": None, "h": 21},
        ],
        "wfh":      [{"x": 240, "y": None, "h": 40}],
        "ooo":      [{"x": None, "y": 147, "h": 48}, {"x": None, "y": 196, "h": 19}],
        "holiday":  [{"x": None, "y": None, "h": 37}],
        "setup":    [{"x": None, "y": None, "h": 24}, {"x": None, "y": None, "h": 24}, {"x": None, "y": None, "h": 32}, {"x": None, "y": None, "h": 18}],
        "loading":  [{"x": None, "y": None, "h": 24}],
        "error":    [{"x": None, "y": None, "h": 24}],
        "shutdown": [{"x": None, "y": None, "h": 40}],
    },
}

_layout_cache: dict = {
    "raw":        None,   # merged LAYOUT_DEFAULTS + file, un-scaled
    "mtime":      0.0,
    "scaled":     None,   # ready-to-use layout after scaling + font_path applied
    "scaled_key": None,   # (display_w, display_h, font_path) for the scaled entry
}


def load_layout(font_path: str | None = None,
                display_w: int | None = None,
                display_h: int | None = None) -> dict:
    """Merge work_layout.json over LAYOUT_DEFAULTS; cache by file mtime.

    If display_w/display_h are provided and differ from the canvas size in the
    layout file, every numeric position/size value is scaled proportionally so
    the layout fits the actual framebuffer without overflowing.

    Returns a shared reference. Callers must not mutate the returned dict.
    The scaled+font layout is cached separately so repeated calls with the same
    parameters incur no copy overhead.
    """
    import json
    try:
        mtime = os.path.getmtime(_LAYOUT_FILE)
    except OSError:
        mtime = 0.0

    if _layout_cache["raw"] is None or mtime != _layout_cache["mtime"]:
        try:
            with open(_LAYOUT_FILE) as f:
                overrides = json.load(f)
            merged = copy.deepcopy(LAYOUT_DEFAULTS)
            for k, v in overrides.items():
                if isinstance(v, dict) and k in merged and isinstance(merged[k], dict):
                    merged[k].update(v)
                else:
                    merged[k] = v
            _layout_cache["raw"]   = merged
            _layout_cache["mtime"] = mtime
        except Exception as exc:
            print(f"[layout] load failed: {exc}")
            _layout_cache["raw"]   = copy.deepcopy(LAYOUT_DEFAULTS)
            _layout_cache["mtime"] = mtime
        _layout_cache["scaled"]     = None  # invalidate on raw reload
        _layout_cache["scaled_key"] = None

    scaled_key = (display_w, display_h, font_path)
    if _layout_cache["scaled"] is None or _layout_cache["scaled_key"] != scaled_key:
        layout = copy.deepcopy(_layout_cache["raw"])  # deep-copy once for scaling/mutation
        if display_w and display_h:
            cw = layout["canvas"]["width"]
            ch = layout["canvas"]["height"]
            if cw != display_w or ch != display_h:
                sx = display_w / cw
                sy = display_h / ch
                sf = (sx * sy) ** 0.5  # geometric mean for font sizes
                layout = _scale_layout(layout, sx, sy, sf, display_w, display_h)
        if font_path:
            layout["font"]["path"] = font_path
        _layout_cache["scaled"]     = layout
        _layout_cache["scaled_key"] = scaled_key

    return _layout_cache["scaled"]  # shared reference; do not mutate


def _scale_layout(layout: dict, sx: float, sy: float, sf: float,
                  w: int, h: int) -> dict:
    """Return a copy of layout with all positions/sizes scaled."""
    layout["canvas"]["width"]  = w
    layout["canvas"]["height"] = h

    def sx_(v): return round(v * sx) if v is not None else None
    def sy_(v): return round(v * sy) if v is not None else None
    def sf_(v): return max(8, round(v * sf)) if v is not None else None

    ic = layout.get("icon", {})
    grid_h = sy_(layout.get("grid", {}).get("height", 0)) or 0
    max_r  = min(
        (h - grid_h) // 2 - 4,       # fit vertically above the grid
        w // 4,                        # at most a quarter of display width
    )
    ic["radius"] = min(sf_(ic.get("radius")), max_r)
    ic["gap"]    = sx_(ic.get("gap"))
    ic["x"]      = sx_(ic.get("x"))
    ic["y"]      = sy_(ic.get("y"))

    aq = layout.get("aqi", {})
    aq["cx"]         = sx_(aq.get("cx"))
    aq["y"]          = sy_(aq.get("y"))
    aq["label_size"] = sf_(aq.get("label_size"))
    aq["value_size"] = sf_(aq.get("value_size"))

    gr = layout.get("grid", {})
    gr["height"]    = sy_(gr.get("height"))
    gr["label_size"] = sf_(gr.get("label_size"))
    gr["temp_size"]  = sf_(gr.get("temp_size"))
    gr["rain_size"]  = sf_(gr.get("rain_size"))
    gr["hum_size"]   = sf_(gr.get("hum_size"))
    gr["wind_size"]  = sf_(gr.get("wind_size"))

    for page_lines in layout.get("line_positions", {}).values():
        for line in page_lines:
            line["x"] = sx_(line.get("x"))
            line["y"] = sy_(line.get("y"))
            line["h"] = sf_(line.get("h"))

    return layout


def invalidate_layout_cache() -> None:
    _layout_cache["raw"]        = None
    _layout_cache["mtime"]      = 0.0
    _layout_cache["scaled"]     = None
    _layout_cache["scaled_key"] = None
    # Font sizes / positions may have changed — clear derived render caches
    _page_bg_cache.clear()
    _page_strip_cache.clear()
    _spotify_render_cache["key"] = None


# ── Colors ─────────────────────────────────────────────────────────────────────────

_PIL_COLORS: dict = {
    "white":    (255, 255, 255),
    "cyan":     (0,   200, 220),
    "green":    (0,   210,  80),
    "yellow":   (255, 204,   0),
    "red":      (220,  50,  50),
    "orange":   (255, 140,   0),
    "darkgrey": (155, 155, 155),
    "grey":     (155, 155, 155),
    "black":    (  0,   0,   0),
    "magenta":  (220,  50, 220),
    "blue":     ( 40,  80, 220),
    "brown":    (160,  80,   0),
}


def _pil_color(name: str) -> tuple:
    return _PIL_COLORS.get(name, (255, 255, 255))


# ── Fonts ──────────────────────────────────────────────────────────────────────────

_pil_font_cache: dict = {}


def _get_font(pt_size, layout: dict):
    path = layout["font"]["path"]
    pt   = max(6, int(pt_size or 14))
    key  = (path, pt)
    if len(_pil_font_cache) > 80:
        _pil_font_cache.clear()
    if key not in _pil_font_cache:
        try:
            _pil_font_cache[key] = ImageFont.truetype(path, pt)
        except Exception:
            _pil_font_cache[key] = ImageFont.load_default()
    return _pil_font_cache[key]


# ── Icons ──────────────────────────────────────────────────────────────────────────

_STATIC_ICON_MAP: dict[str, str] = {
    "sun":           "clear-day.svg",
    "cloud":         "cloudy.svg",
    "partly_cloudy": "cloudy-2-day.svg",
    "rain":          "rainy-2.svg",
    "heavy_rain":    "rainy-3.svg",
    "thunderstorm":  "thunderstorms.svg",
    "snow":          "snowy-2.svg",
    "fog":           "fog.svg",
}
_static_icon_cache: dict = {}


def _center_icon(img_rgba: "Image.Image", target: int) -> "Image.Image":
    """Crop to the content bounding box, re-center in a square, resize to target×target.

    Each source PNG has different amounts of transparent padding, making the visual
    center land at different pixel offsets. This normalises all icons to a consistent
    centered square before they are pasted onto the display.
    """
    # Use the alpha channel bbox so transparent-but-coloured pixels don't count
    alpha_bbox = img_rgba.split()[3].getbbox()
    if alpha_bbox:
        img_rgba = img_rgba.crop(alpha_bbox)
    cw, ch = img_rgba.size
    sq = max(cw, ch)
    square = Image.new("RGBA", (sq, sq), (0, 0, 0, 0))
    square.paste(img_rgba, ((sq - cw) // 2, (sq - ch) // 2))
    return square.resize((target, target), Image.LANCZOS)


def _load_static_icon(name: str, r: int):
    if not _PIL_AVAILABLE:
        return None
    filename = _STATIC_ICON_MAP.get(name)
    if not filename:
        return None
    cache_key = (name, r)
    if cache_key in _static_icon_cache:
        return _static_icon_cache[cache_key]

    # 1. Try pre-converted PNG first (e.g. icons/clear-day.png)
    png_filename = filename.rsplit(".", 1)[0] + ".png"
    png_path = os.path.join(_ICONS_DIR, png_filename)
    if os.path.exists(png_path):
        try:
            img = _center_icon(Image.open(png_path).convert("RGBA"), r * 2)
            _static_icon_cache[cache_key] = img
            return img
        except Exception as exc:
            print(f"[icon] {png_filename}: {exc}")

    # 2. Fall back to cairosvg (SVG → PNG in memory)
    if not _CAIROSVG_AVAILABLE:
        _static_icon_cache[cache_key] = None
        return None
    svg_path = os.path.join(_ICONS_DIR, filename)
    if not os.path.exists(svg_path):
        _static_icon_cache[cache_key] = None
        return None
    try:
        png = cairosvg.svg2png(url=svg_path, output_width=r * 2)
        img = _center_icon(Image.open(io.BytesIO(png)).convert("RGBA"), r * 2)
        _static_icon_cache[cache_key] = img
        return img
    except Exception as exc:
        print(f"[icon] {filename}: {exc}")
        _static_icon_cache[cache_key] = None
        return None


def _draw_weather_icon(img, draw, name: str, cx: int, cy: int, r: int):
    static = _load_static_icon(name, r)
    if static is not None:
        iw, ih = static.size
        img.paste(static, (cx - iw // 2, cy - ih // 2), static)
        return

    sun_col   = (255, 200,  50)
    cloud_col = (190, 190, 190)
    rain_col  = ( 80, 150, 255)
    snow_col  = (220, 240, 255)
    fog_col   = (160, 160, 160)

    def circle(x, y, radius, fill):
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=fill)

    def line(x0, y0, x1, y1, fill, w=1):
        draw.line([(x0, y0), (x1, y1)], fill=fill, width=w)

    def cloud(ox, oy, cr):
        circle(ox,          oy,          cr,     cloud_col)
        circle(ox + cr,     oy,          cr - 2, cloud_col)
        circle(ox - cr + 2, oy,          cr - 3, cloud_col)
        circle(ox,          oy + cr - 2, cr - 2, cloud_col)

    if name == "sun":
        circle(cx, cy, r - 4, sun_col)
        for i in range(8):
            angle = math.radians(i * 45)
            ix = cx + int((r - 3) * math.cos(angle))
            iy = cy + int((r - 3) * math.sin(angle))
            ox = cx + int(r * math.cos(angle))
            oy = cy + int(r * math.sin(angle))
            line(ix, iy, ox, oy, sun_col, 2)
    elif name == "cloud":
        cloud(cx, cy, r // 2)
    elif name == "partly_cloudy":
        circle(cx - r // 4, cy - r // 4, r // 2, sun_col)
        cloud(cx + r // 6, cy + r // 6, r // 3)
    elif name in ("rain", "heavy_rain"):
        drops = 5 if name == "rain" else 7
        cloud(cx, cy - r // 4, r // 3)
        for i in range(drops):
            rx = cx - r // 2 + i * (r // (drops - 1))
            ry = cy + r // 4
            line(rx - 2, ry, rx + 2, ry + 5, rain_col, 2)
    elif name == "thunderstorm":
        cloud(cx, cy - r // 4, r // 3)
        bolt = [(cx, cy + 2), (cx - 4, cy + 10), (cx + 1, cy + 10), (cx - 3, cy + 18)]
        draw.line(bolt, fill=(255, 220, 0), width=3)
    elif name == "snow":
        cloud(cx, cy - r // 4, r // 3)
        for i in range(5):
            sx = cx - r // 2 + i * (r // 4)
            sy = cy + r // 4
            circle(sx, sy, 2, snow_col)
    elif name == "fog":
        for i in range(5):
            fy = cy - r // 2 + i * (r // 4)
            line(cx - r // 2, fy, cx + r // 2, fy, fog_col, 2)


# ── Text helpers ─────────────────────────────────────────────────────────────────────

def _text_size(draw, text: str, font) -> tuple:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0], bb[3] - bb[1]


def _truncate_to_fit(draw, text: str, font, max_w: int) -> str:
    if not text or _text_size(draw, text, font)[0] <= max_w:
        return text
    lo, hi = 0, len(text)
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if _text_size(draw, text[:mid] + "..", font)[0] <= max_w:
            lo = mid
        else:
            hi = mid
    return (text[:lo] + "..") if lo > 0 else ".."


def _fit_text(draw, text: str, f, pos_h: int, max_w: int, layout: dict):
    tw = _text_size(draw, text, f)[0]
    if tw <= max_w:
        return f, text, tw
    sf = _get_font(max(6, round(pos_h * 0.8)), layout)
    stw = _text_size(draw, text, sf)[0]
    if stw <= max_w:
        return sf, text, stw
    text = _truncate_to_fit(draw, text, sf, max_w)
    return sf, text, _text_size(draw, text, sf)[0]


# ── Overlay renderers ────────────────────────────────────────────────────────────────

def _render_aqi_overlay(draw, aqi: dict, layout: dict):
    cx    = layout["aqi"]["cx"]
    y0    = int(layout["aqi"].get("y") or 2)
    val   = str(aqi.get("value", ""))
    color = _pil_color(aqi.get("color", "green"))
    lbl_f = _get_font(layout["aqi"]["label_size"], layout)
    val_f = _get_font(layout["aqi"]["value_size"], layout)
    lw, lh = _text_size(draw, "AQI", lbl_f)
    draw.text((cx - lw // 2, y0), "AQI", font=lbl_f, fill=(200, 200, 200))
    vw, _  = _text_size(draw, val, val_f)
    draw.text((cx - vw // 2, y0 + lh + 2), val, font=val_f, fill=color)


def _render_hourly_grid(draw, items: list, grid_top: int, layout: dict):
    W    = layout["canvas"]["width"]
    H    = layout["canvas"]["height"]
    cols = min(layout["grid"]["columns"], len(items)) if items else layout["grid"]["columns"]
    if not items or cols == 0:
        return
    col_w   = W // cols
    sep_col = (50, 50, 50)
    draw.rectangle([0, grid_top, W, H], fill=(0, 0, 0))
    draw.line([(0, grid_top), (W, grid_top)], fill=sep_col, width=1)
    lbl_f = _get_font(layout["grid"]["label_size"], layout)
    tmp_f = _get_font(layout["grid"]["temp_size"], layout)
    rn_f  = _get_font(layout["grid"]["rain_size"], layout)

    _, lh = _text_size(draw, "12AM", lbl_f)
    _, th = _text_size(draw, "72\xb0", tmp_f)
    _, rh = _text_size(draw, "99%",   rn_f)
    gap     = 4
    total_h = lh + gap + th + gap + rh
    cell_h  = H - grid_top
    y0      = grid_top + max(4, (cell_h - total_h) // 2)
    y_tmp   = y0 + lh + gap
    y_rain  = y_tmp + th + gap

    for i, item in enumerate(items[:cols]):
        x = i * col_w
        if i > 0:
            draw.line([(x, grid_top + 4), (x, H - 4)], fill=sep_col, width=1)
        cx  = x + col_w // 2
        lbl = item.get("label", "")
        tmp = item.get("temp", "")
        rn  = item.get("rain", "")
        rc  = _pil_color(item.get("rain_color", "white"))

        lw, _ = _text_size(draw, lbl, lbl_f)
        tw, _ = _text_size(draw, tmp, tmp_f)
        rw, _ = _text_size(draw, rn,  rn_f)

        draw.text((cx - lw // 2, y0),     lbl, font=lbl_f, fill=(200, 200, 200))
        draw.text((cx - tw // 2, y_tmp),  tmp, font=tmp_f, fill=(255, 255, 255))
        draw.text((cx - rw // 2, y_rain), rn,  font=rn_f,  fill=rc)


# ── Custom image page ────────────────────────────────────────────────────────────────

_custom_image_cache: dict = {}  # (path, mtime, W, H) → PIL Image


def render_custom_image_page(image_path: str, layout: dict) -> "Image.Image":
    """Open image_path, resize/crop to canvas size using ImageOps.fit, return PIL image.

    Result is cached by (path, mtime, W, H) so repeated renders of the same
    image at the same size are free after the first load.
    """
    W = layout["canvas"]["width"]
    H = layout["canvas"]["height"]
    try:
        mtime = os.path.getmtime(image_path)
    except OSError:
        mtime = 0.0
    key = (image_path, mtime, W, H)
    if key not in _custom_image_cache:
        if len(_custom_image_cache) >= 8:
            _custom_image_cache.clear()
        img = Image.open(image_path).convert("RGB")
        _custom_image_cache[key] = ImageOps.fit(img, (W, H), Image.LANCZOS)
    return _custom_image_cache[key]


# ── Spotify renderer ─────────────────────────────────────────────────────────────────

_spotify_art_cache: dict = {}  # url → PIL Image (resized)


def _fetch_album_art(url: str, size: int) -> "Image.Image | None":
    if (url, size) in _spotify_art_cache:
        return _spotify_art_cache[(url, size)]
    if not _REQUESTS_OK:
        return None
    try:
        r = _requests.get(url, timeout=6)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        img = ImageOps.fit(img, (size, size), Image.LANCZOS)
        if len(_spotify_art_cache) >= 30:
            _spotify_art_cache.clear()
        _spotify_art_cache[(url, size)] = img
        return img
    except Exception:
        return None


def prefetch_spotify_art(url: str, size: int = 150) -> None:
    """Fetch and cache album art before the Spotify page first renders."""
    if url:
        _fetch_album_art(url, size)


def _album_bg_color(art_img: "Image.Image") -> tuple:
    """Sample album art and return a darkened tint safe for white text."""
    import colorsys
    r, g, b = art_img.resize((1, 1), Image.LANCZOS).getpixel((0, 0))
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    v2 = min(v * 0.5, 0.28)
    s2 = min(s * 1.2, 1.0)
    r2, g2, b2 = colorsys.hsv_to_rgb(h, s2, v2)
    return (int(r2 * 255), int(g2 * 255), int(b2 * 255))


def _wcag_lum(r: int, g: int, b: int) -> float:
    def ch(c):
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _min_gray_for_contrast(bg_lum: float, ratio: float) -> int:
    target = ratio * (bg_lum + 0.05) - 0.05
    if target <= 0:
        return 0
    if target >= 1.0:
        return 255
    if target <= 0.00313:
        return int(target * 12.92 * 255)
    return int((target ** (1.0 / 2.4) * 1.055 - 0.055) * 255)


def _adaptive_text_colors(bg: tuple) -> tuple:
    """Return (title_color, artist_color, album_color) with readable contrast against bg."""
    bg_lum = _wcag_lum(*bg)
    if bg_lum >= 0.179:
        return (15, 15, 15), (50, 50, 50), (80, 80, 80)
    title = (255, 255, 255)
    v_artist = max(160, _min_gray_for_contrast(bg_lum, 4.5))
    v_album  = max(130, _min_gray_for_contrast(bg_lum, 3.0))
    return title, (v_artist, v_artist, v_artist), (v_album, v_album, v_album)


def _draw_spotify_icon(draw, x: int, y: int, size: int, green):
    """Approximate Spotify circle icon: green disc with 3 arcs concentric from lower-left."""
    r = size // 2
    cx, cy = x + r, y + r
    draw.ellipse([x, y, x + size - 1, y + size - 1], fill=green)
    w = max(1, size // 8)
    # Arc centre at lower-left; arcs 280°–335° sweep the upper-right portion,
    # producing three tilted horizontal waves like the real Spotify logo.
    ac_x = x + size // 5
    ac_y = y + size
    for arc_r in [size * 9 // 22, size * 13 // 22, size * 17 // 22]:
        bb = [ac_x - arc_r, ac_y - arc_r, ac_x + arc_r, ac_y + arc_r]
        draw.arc(bb, start=280, end=335, fill=(255, 255, 255), width=w)


# ── Numpy fast-path helpers ───────────────────────────────────────────────────────────

# Per-page scroll strip cache: slot → {key, strip_pil, arr, tw_gap, strip_w, strip_h, max_w, color}
_page_strip_cache: dict = {}
# Per-page static background cache: page_name → {data_key, bg_arr, zones}
_page_bg_cache:    dict = {}
# Populated by render_page_pil; consumed by render_page_rgb565 for the fast path
_current_scroll_zones: list = []

# Spotify fast-path cache (separate from general page cache due to progress zone)
_spotify_render_cache: dict = {
    "key": None,    # (track, artist, album, art_url, ART_SIZE, W, H)
    "bg_arr": None, # uint16 H×W — complete static frame (title already drawn)
    "BAR_Y": 0, "T_Y": 0, "EDGE": 0,
    "f_time": None, "BG": None, "GREEN": None, "MUTED": None, "DIM": None,
}


def _pil_to_arr(img: "Image.Image") -> "_np.ndarray":
    """Convert PIL RGB image to H×W uint16 numpy array in little-endian RGB565."""
    a = _np.frombuffer(img.tobytes(), _np.uint8).reshape(-1, 3).astype(_np.uint16)
    p = ((a[:, 0] & 0xF8) << 8) | ((a[:, 1] & 0xFC) << 3) | (a[:, 2] >> 3)
    return p.astype('<u2').reshape(img.height, img.width)


_SCROLL_GAP = 40  # px gap between end of text and start of repeat

# ── Spotify scroll state ──────────────────────────────────────────────────────────────

_scroll_states: dict[str, dict] = {}
_SCROLL_SPEED   = 30.0  # pixels per second
_SCROLL_PAUSE_S = 1.5   # seconds to hold before starting to scroll


def _make_scroll_slot() -> dict:
    return {"key": "", "offset": 0.0, "needs_scroll": False,
            "entered_at": 0.0, "last_tick": 0.0, "cycles": 0}


def _tick_scroll(slot: str, key: str, title_w: int, text_w: int) -> tuple:
    """Advance scroll offset for a named slot. Returns (pixel_offset, needs_scroll)."""
    now   = _time_mod.time()
    needs = title_w > text_w
    state = _scroll_states.setdefault(slot, _make_scroll_slot())
    state["needs_scroll"] = needs
    if not needs:
        return 0, False
    if key != state["key"]:
        state.update(key=key, offset=0.0, needs_scroll=True,
                     entered_at=now, last_tick=now, cycles=0)
        return 0, True
    # Gap > 1s means the page was navigated away and is now re-entering
    if state["last_tick"] > 0 and now - state["last_tick"] > 1.0:
        state.update(offset=0.0, entered_at=now, last_tick=now, cycles=0)
        return 0, True
    if now - state["entered_at"] < _SCROLL_PAUSE_S:
        state["last_tick"] = now
        return 0, True
    dt    = now - state["last_tick"]
    state["last_tick"] = now
    gap   = _SCROLL_GAP
    total = title_w + gap
    raw   = state["offset"] + dt * _SCROLL_SPEED
    if raw >= total:
        state["cycles"] += 1
    state["offset"] = raw % total
    return int(state["offset"]), True


def spotify_needs_scroll() -> bool:
    return bool(_scroll_states.get("spotify", {}).get("needs_scroll"))


def spotify_scroll_complete() -> bool:
    s = _scroll_states.get("spotify", {})
    return not s.get("needs_scroll") or s.get("cycles", 0) >= 1


def calendar_needs_scroll() -> bool:
    return bool(_scroll_states.get("calendar_0", {}).get("needs_scroll"))


def calendar_scroll_complete() -> bool:
    s = _scroll_states.get("calendar_0", {})
    return not s.get("needs_scroll") or s.get("cycles", 0) >= 1


# ── Spotify progress interpolation ───────────────────────────────────────────────────

_spotify_progress: dict = {"track": "", "base_ms": 0, "received_at": 0.0}


def _interpolate_progress(track: str, progress_ms: int, duration_ms: int) -> int:
    """Return progress_ms advanced by wall-clock time since last API update."""
    now = _time_mod.time()
    sp  = _spotify_progress
    if track != sp["track"] or progress_ms != sp["base_ms"]:
        sp["track"] = track; sp["base_ms"] = progress_ms; sp["received_at"] = now
    elapsed_ms = int((now - sp["received_at"]) * 1000)
    current    = sp["base_ms"] + elapsed_ms
    return min(current, duration_ms) if duration_ms > 0 else current


def render_spotify_page(page: dict, layout: dict) -> "Image.Image":
    W = layout["canvas"]["width"]
    H = layout["canvas"]["height"]

    SPOTIFY_DARK = (25,  20,  20)
    GREEN        = (29, 185,  84)
    DIM          = (51,  51,  51)

    HEADER_H  = 28
    BAR_ZONE  = 26   # px reserved at bottom (bar + time labels + edge clearance)
    EDGE      = 8    # minimum margin from display edges
    CONTENT_Y = HEADER_H + 1
    CONTENT_H = H - CONTENT_Y - BAR_ZONE

    # ── Album art (left, fetch early for bg color) ────────────────────────────
    ART_PAD  = 4
    ART_SIZE = min(CONTENT_H - ART_PAD * 2, 150)
    art_x    = ART_PAD + 5   # 5px right of pad
    art_y    = CONTENT_Y + (CONTENT_H - ART_SIZE) // 2
    art_url  = page.get("art_url")
    art_img  = _fetch_album_art(art_url, ART_SIZE) if art_url else None

    BG = _album_bg_color(art_img) if art_img else SPOTIFY_DARK
    WHITE, MUTED, ALB_COLOR = _adaptive_text_colors(BG)

    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # ── Header ────────────────────────────────────────────────────────────────
    f_label = _get_font(14, layout)
    f_logo  = _get_font(12, layout)
    _, lbl_h = _text_size(draw, "Now Playing", f_label)
    draw.text((12, (HEADER_H - lbl_h) // 2), "Now Playing", font=f_label, fill=MUTED)

    ICON_SIZE = 16
    spot_text = "Spotify"
    sw, sh = _text_size(draw, spot_text, f_logo)
    logo_x  = W - 8 - sw - 4 - ICON_SIZE
    icon_y  = (HEADER_H - ICON_SIZE) // 2
    _draw_spotify_icon(draw, logo_x, icon_y, ICON_SIZE, GREEN)
    draw.text((logo_x + ICON_SIZE + 4, (HEADER_H - sh) // 2),
              spot_text, font=f_logo, fill=WHITE)
    draw.line([(0, HEADER_H), (W, HEADER_H)], fill=DIM, width=1)

    # ── Album art ─────────────────────────────────────────────────────────────
    draw.rectangle([art_x - 1, art_y - 1, art_x + ART_SIZE, art_y + ART_SIZE],
                   fill=(35, 35, 35), outline=DIM)
    if art_img:
        img.paste(art_img, (art_x, art_y))

    # ── Track / artist / album (right of art) ────────────────────────────────
    TEXT_X = art_x + ART_SIZE + 8
    TEXT_W = W - TEXT_X - EDGE
    GAP    = 7

    f_track  = _get_font(18, layout)
    f_artist = _get_font(14, layout)
    f_album  = _get_font(13, layout)

    track  = page.get("track",  "") or ""
    artist = _truncate_to_fit(draw, page.get("artist", "") or "", f_artist, TEXT_W)
    album  = _truncate_to_fit(draw, page.get("album",  "") or "", f_album,  TEXT_W)

    # Word-wrap the track title to 2 lines instead of scrolling
    _, th = _text_size(draw, track  or " ", f_track)
    _, ah = _text_size(draw, artist or " ", f_artist)
    _, lh = _text_size(draw, album  or " ", f_album)

    title_line1 = title_line2 = ""
    if track:
        tw, _ = _text_size(draw, track, f_track)
        if tw <= TEXT_W:
            title_line1 = track
        else:
            words = track.split()
            last_fit = 0
            for i in range(1, len(words) + 1):
                if _text_size(draw, " ".join(words[:i]), f_track)[0] <= TEXT_W:
                    last_fit = i
                else:
                    break
            if last_fit == 0:
                title_line1 = _truncate_to_fit(draw, track, f_track, TEXT_W)
            else:
                title_line1 = " ".join(words[:last_fit])
                title_line2 = _truncate_to_fit(draw, " ".join(words[last_fit:]), f_track, TEXT_W)

    TITLE_GAP = 2
    # Anchor artist/album using a 1-line block so their positions are stable.
    # When 2 title lines are needed, line 2 sits at track_y and line 1 floats
    # up above it — so nothing below shifts.
    block_h  = th + GAP + ah + GAP + lh
    ty0      = art_y + max(0, (ART_SIZE - block_h) // 2)
    track_y  = ty0
    artist_y = ty0 + th + GAP
    album_y  = ty0 + th + GAP + ah + GAP

    if title_line2:
        # Line 2 occupies the normal title row; line 1 shifts up
        draw.text((TEXT_X, track_y - th - TITLE_GAP), title_line1, font=f_track, fill=WHITE)
        draw.text((TEXT_X, track_y),                  title_line2, font=f_track, fill=WHITE)
    elif title_line1:
        draw.text((TEXT_X, track_y), title_line1, font=f_track, fill=WHITE)

    if artist:
        draw.text((TEXT_X, artist_y), artist, font=f_artist, fill=MUTED)
    if album:
        draw.text((TEXT_X, album_y),  album,  font=f_album,  fill=ALB_COLOR)

    # ── Progress bar with elapsed / remaining ────────────────────────────────
    BAR_Y       = H - BAR_ZONE + 4   # bar sits near top of the reserved zone
    T_Y         = BAR_Y + 7          # time text sits just below the bar
    f_time      = _get_font(10, layout)
    duration_ms = page.get("duration_ms") or 0
    current_ms  = _interpolate_progress(track, page.get("progress_ms") or 0, duration_ms)

    def _fmt_ms(ms: int) -> str:
        s = max(0, ms) // 1000
        return f"{s // 60}:{s % 60:02d}"

    BAR_X0, BAR_X1 = EDGE, W - EDGE
    draw.line([(BAR_X0, BAR_Y), (BAR_X1, BAR_Y)], fill=DIM, width=4)
    if duration_ms > 0:
        fill_x = BAR_X0 + int((BAR_X1 - BAR_X0) * min(current_ms / duration_ms, 1.0))
        if fill_x > BAR_X0:
            draw.line([(BAR_X0, BAR_Y), (fill_x, BAR_Y)], fill=GREEN, width=4)
        pos_str = _fmt_ms(current_ms)
        rem_str = _fmt_ms(max(0, duration_ms - current_ms))
        rw, _   = _text_size(draw, rem_str, f_time)
        draw.text((EDGE, T_Y), pos_str, font=f_time, fill=MUTED)
        draw.text((W - rw - EDGE, T_Y), rem_str, font=f_time, fill=MUTED)

    return img


# ── Spotify numpy fast-path renderer ─────────────────────────────────────────────────



def _render_spotify_fast(page: dict, layout: dict) -> "bytes | None":
    """Render Spotify page via numpy array cache. Returns RGB565 bytes or None on error."""
    if not _NUMPY:
        return None
    try:
        W = layout["canvas"]["width"]
        H = layout["canvas"]["height"]
        HEADER_H  = 28
        BAR_ZONE  = 26
        EDGE      = 8
        CONTENT_Y = HEADER_H + 1
        CONTENT_H = H - CONTENT_Y - BAR_ZONE
        ART_PAD   = 4
        ART_SIZE  = min(CONTENT_H - ART_PAD * 2, 150)
        art_x     = ART_PAD + 5
        TEXT_X    = art_x + ART_SIZE + 8
        TEXT_W    = W - TEXT_X - EDGE

        track   = page.get("track",  "") or ""
        artist  = page.get("artist", "") or ""
        album   = page.get("album",  "") or ""
        art_url = page.get("art_url")
        art_img = _fetch_album_art(art_url, ART_SIZE) if art_url else None
        BG      = _album_bg_color(art_img) if art_img else (25, 20, 20)

        cache_key = (track, artist, album, art_url, ART_SIZE, W, H)
        sc = _spotify_render_cache

        if sc["key"] != cache_key:
            img = render_spotify_page(page, layout)
            _, ARTIST_C, _ = _adaptive_text_colors(BG)
            sc.update(
                key=cache_key, bg_arr=_pil_to_arr(img),
                BAR_Y=H - BAR_ZONE + 4, T_Y=H - BAR_ZONE + 4 + 7, EDGE=EDGE,
                f_time=_get_font(10, layout),
                BG=BG, GREEN=(29, 185, 84), MUTED=ARTIST_C, DIM=(51, 51, 51),
            )

        # Compose frame from cache — title is static, only progress zone updates
        frame = sc["bg_arr"].copy()

        # Progress zone (small PIL image, height = BAR_ZONE rows)
        BAR_ZONE_ = BAR_ZONE
        bar_y_z   = sc["BAR_Y"] - (H - BAR_ZONE_)
        t_y_z     = sc["T_Y"]   - (H - BAR_ZONE_)
        EDGE_     = sc["EDGE"]
        DIM_      = sc["DIM"];   GREEN_ = sc["GREEN"];  MUTED_ = sc["MUTED"]
        f_time_   = sc["f_time"]

        prog = Image.new("RGB", (W, BAR_ZONE_), sc["BG"])
        pdraw = ImageDraw.Draw(prog)
        duration_ms = page.get("duration_ms") or 0
        current_ms  = _interpolate_progress(
            track, page.get("progress_ms") or 0, duration_ms)

        def _fmt(ms):
            s = max(0, ms) // 1000
            return f"{s // 60}:{s % 60:02d}"

        pdraw.line([(EDGE_, bar_y_z), (W - EDGE_, bar_y_z)], fill=DIM_, width=4)
        if duration_ms > 0:
            fill_x = EDGE_ + int((W - 2 * EDGE_) * min(current_ms / duration_ms, 1.0))
            if fill_x > EDGE_:
                pdraw.line([(EDGE_, bar_y_z), (fill_x, bar_y_z)], fill=GREEN_, width=4)
            pos_str = _fmt(current_ms)
            rem_str = _fmt(max(0, duration_ms - current_ms))
            rw, _   = _text_size(pdraw, rem_str, f_time_)
            pdraw.text((EDGE_, t_y_z),        pos_str, font=f_time_, fill=MUTED_)
            pdraw.text((W - rw - EDGE_, t_y_z), rem_str, font=f_time_, fill=MUTED_)

        frame[H - BAR_ZONE_:, :] = _pil_to_arr(prog)
        return frame.tobytes()
    except Exception as exc:
        print(f"[render] spotify fast path: {exc}")
        return None


# ── Main renderer ────────────────────────────────────────────────────────────────────

def render_page_pil(page: dict, layout: dict | None = None) -> "Image.Image":
    global _current_scroll_zones
    _current_scroll_zones = []

    if layout is None:
        layout = load_layout()

    # Spotify now-playing — bypass normal text renderer
    if page.get("_name") == "spotify":
        try:
            return render_spotify_page(page, layout)
        except Exception as exc:
            print(f"[render] spotify page: {exc}")

    # Custom image page — bypass normal text renderer
    if page.get("_name") == "custom_image" and page.get("image_path"):
        try:
            return render_custom_image_page(page["image_path"], layout)
        except Exception as exc:
            print(f"[render] custom_image {page['image_path']}: {exc}")
            # Fall through to blank black frame on error

    W  = layout["canvas"]["width"]
    H  = layout["canvas"]["height"]
    hh = layout["header"]["height"]
    fh = layout["footer"]["height"]
    lm = layout["content"]["left_margin"]
    rm = layout["content"]["right_margin"]
    gap_min = layout["content"]["line_gap_min"]

    img  = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    if hh > 0:
        draw.rectangle([0, 0, W, hh], fill=tuple(layout["header"]["bg"]))
        title   = page.get("title", "")
        title_f = _get_font(20, layout)
        tw, th  = _text_size(draw, title, title_f)
        draw.text(((W - tw) // 2, (hh - th) // 2), title,
                  font=title_f, fill=_pil_color(layout["header"]["title_color"]))
        draw.line([(0, hh), (W, hh)], fill=(60, 60, 60), width=1)

    if fh > 0:
        draw.rectangle([0, H - fh, W, H], fill=(0, 0, 30))
        draw.line([(0, H - fh), (W, H - fh)], fill=(60, 60, 60), width=1)

    grid_items = page.get("hourly_grid") or []
    gh         = layout["grid"]["height"] if grid_items else 0
    grid_top   = H - fh - gh
    content_y0 = hh + (1 if hh > 0 else 0)

    alert_banner = page.get("alert_banner")
    if alert_banner:
        BANNER_H = 18
        b0 = content_y0
        b1 = b0 + BANNER_H
        draw.rectangle([0, b0, W, b1], fill=(170, 20, 20))
        bfont = _get_font(12, layout)
        btext = f"! {alert_banner}"
        btw, bth = _text_size(draw, btext, bfont)
        draw.text((W - btw - 6, b0 + (BANNER_H - bth) // 2), btext,
                  font=bfont, fill=(255, 210, 210))
        content_y0 = b1 + 1
    content_h  = grid_top - content_y0

    lines      = list(page.get("lines") or [])
    page_name  = page.get("_name", "")
    positions  = layout.get("line_positions", {}).get(page_name, [])
    positions  = [(p if isinstance(p, dict) else {}) for p in positions]  # guard null entries
    icon_name  = page.get("weather_icon")
    icon_r    = layout["icon"]["radius"]
    icon_gap  = layout["icon"]["gap"]
    _ix = layout["icon"].get("x")
    _iy = layout["icon"].get("y")
    icon_cx = int(_ix) if _ix is not None else (lm + icon_r)
    icon_cy = int(_iy) if _iy is not None else (content_y0 + content_h // 2)

    line_fonts   = []
    line_heights = []
    for i, ln in enumerate(lines):
        pos = positions[i] if i < len(positions) else {}
        h   = max(6, int(pos.get("h") or 14))
        f   = _get_font(h, layout)
        _, lh = _text_size(draw, ln.get("text", "") or " ", f)
        line_fonts.append(f)
        line_heights.append(max(lh, h // 2))

    total_text_h = sum(lh for i, lh in enumerate(line_heights)
                       if (positions[i].get("y") if i < len(positions) else None) is None
                       and (positions[i].get("visible", True) if i < len(positions) else True))
    n_auto = sum(1 for i in range(len(lines))
                 if (positions[i].get("y") if i < len(positions) else None) is None
                 and (positions[i].get("visible", True) if i < len(positions) else True))
    gap    = max(gap_min, (content_h - total_text_h) // (n_auto + 1)) if n_auto > 0 else gap_min
    auto_y = content_y0 + gap

    for i, ln in enumerate(lines):
        pos        = positions[i] if i < len(positions) else {}
        if not pos.get("visible", True):
            continue
        explicit_y = pos.get("y")
        explicit_x = pos.get("x")
        f          = line_fonts[i]
        lh         = line_heights[i]
        text       = ln.get("text", "")
        color      = _pil_color(ln.get("color", "white"))
        right      = ln.get("right", "")
        rc         = _pil_color(ln.get("rightColor") or ln.get("color", "white"))

        pos_h    = max(6, int(pos.get("h") or 14))
        y_top    = (int(explicit_y) - pos_h // 2) if explicit_y is not None else auto_y
        right_bound = W - rm
        tw, _ = _text_size(draw, text, f)

        if ln.get("wrap") and not right and not (i == 0 and icon_name):
            max_w  = W - lm - rm
            pos_h  = max(6, int(pos.get("h") or 14))
            active_font = f
            for pct in range(0, 21, 5):
                pt = max(6, round(pos_h * (100 - pct) / 100))
                candidate = _get_font(pt, layout)
                active_font = candidate
                if _text_size(draw, text, candidate)[0] <= max_w:
                    break
            tw_full, th_sub = _text_size(draw, text, active_font)
            if tw_full <= max_w:
                cx = int(explicit_x) if explicit_x is not None else W // 2
                draw.text((cx - tw_full // 2, y_top), text, font=active_font, fill=color)
            else:
                words = text.split()
                last_mid = 0
                for mid in range(1, len(words) + 1):
                    if _text_size(draw, " ".join(words[:mid]), active_font)[0] <= max_w:
                        last_mid = mid
                    else:
                        break
                if last_mid == 0:
                    needs_marquee = True
                    l1, l2 = text, ""
                else:
                    l1     = " ".join(words[:last_mid])
                    l2_raw = " ".join(words[last_mid:])
                    needs_marquee = bool(l2_raw and _text_size(draw, l2_raw, active_font)[0] > max_w)
                    if not needs_marquee:
                        l2 = _truncate_to_fit(draw, l2_raw, active_font, max_w) if l2_raw else ""

                scroll_slot = f"{page_name}_{i}"
                if needs_marquee:
                    tw_s, th_s = _text_size(draw, text, active_font)
                    offset, _  = _tick_scroll(scroll_slot, text, tw_s, max_w)
                    center_y   = y_top + lh // 2
                    scroll_y   = center_y - th_s // 2
                    cx         = int(explicit_x) if explicit_x is not None else W // 2
                    paste_x    = cx - max_w // 2
                    if tw_s > 0:
                        gap_px  = _SCROLL_GAP
                        tw_gap  = tw_s + gap_px
                        strip_w = tw_gap + tw_s
                        # Build/update strip cache (expensive text draw, only on content change)
                        sc = _page_strip_cache.get(scroll_slot)
                        if sc is None or sc["key"] != text or sc.get("color") != color:
                            strip_pil = Image.new("RGB", (strip_w, th_s + 2), (0, 0, 0))
                            sd        = ImageDraw.Draw(strip_pil)
                            sd.text((0, 0),      text, font=active_font, fill=color)
                            sd.text((tw_gap, 0), text, font=active_font, fill=color)
                            _page_strip_cache[scroll_slot] = {
                                "key": text, "color": color,
                                "strip_pil": strip_pil,
                                "arr": _pil_to_arr(strip_pil) if _NUMPY else None,
                                "title_w": tw_s, "tw_gap": tw_gap, "strip_w": strip_w,
                                "strip_h": th_s + 2, "max_w": max_w,
                            }
                        else:
                            strip_pil = sc["strip_pil"]
                        # Record zone for bg cache (used by render_page_rgb565 fast path)
                        _current_scroll_zones.append(
                            (scroll_slot, paste_x, scroll_y, max_w, th_s + 2))
                        # PIL paste for correctness (also used by non-numpy callers)
                        off    = int(offset) % tw_gap
                        crop_w = min(max_w, strip_w - off)
                        if crop_w > 0:
                            img.paste(strip_pil.crop((off, 0, off + crop_w, th_s + 2)),
                                      (paste_x, scroll_y))
                else:
                    n_sub       = 2 if l2 else 1
                    gap2        = 2
                    total_block = th_sub * n_sub + gap2 * (n_sub - 1)
                    center_y    = y_top + lh // 2
                    sub_y       = center_y - total_block // 2
                    cx          = int(explicit_x) if explicit_x is not None else W // 2
                    for sub_text in ([l1, l2] if l2 else [l1]):
                        stw = _text_size(draw, sub_text, active_font)[0]
                        draw.text((cx - stw // 2, sub_y), sub_text, font=active_font, fill=color)
                        sub_y += th_sub + gap2

        elif ln.get("wrap_left") and not right and not (i == 0 and icon_name):
            x_start = int(explicit_x) if explicit_x is not None else lm
            max_w   = W - x_start - rm
            tw_full, th_sub = _text_size(draw, text, f)
            if tw_full <= max_w:
                draw.text((x_start, y_top), text, font=f, fill=color)
            else:
                words = text.split()
                last_mid = 0
                for mid in range(1, len(words) + 1):
                    if _text_size(draw, " ".join(words[:mid]), f)[0] <= max_w:
                        last_mid = mid
                    else:
                        break
                if last_mid == 0:
                    l1 = _truncate_to_fit(draw, words[0] if words else text, f, max_w)
                    l2 = ""
                else:
                    l1     = " ".join(words[:last_mid])
                    l2_raw = " ".join(words[last_mid:])
                    l2     = _truncate_to_fit(draw, l2_raw, f, max_w) if l2_raw else ""
                draw.text((x_start, y_top), l1, font=f, fill=color)
                if l2:
                    draw.text((x_start, y_top + th_sub + 2), l2, font=f, fill=color)

        elif i == 0 and icon_name:
            _draw_weather_icon(img, draw, icon_name, icon_cx, icon_cy, icon_r)
            pos_h = max(6, int(pos.get("h") or 14))
            cx = int(explicit_x) if explicit_x is not None else (lm + icon_r * 2 + icon_gap + tw // 2)
            max_w = min(W - lm - rm, max(1, 2 * (right_bound - cx)))
            f, text, tw = _fit_text(draw, text, f, pos_h, max_w, layout)
            if explicit_x is not None:
                x_left = max(lm, min(right_bound - tw, int(explicit_x) - tw // 2))
            else:
                x_left = lm + icon_r * 2 + icon_gap
            draw.text((x_left, y_top), text, font=f, fill=color)

        elif right:
            pos_h = max(6, int(pos.get("h") or 14))
            cx = int(explicit_x) if explicit_x is not None else lm
            max_w = min(W - lm - rm, max(1, 2 * (right_bound - cx)))
            f, text, tw = _fit_text(draw, text, f, pos_h, max_w, layout)
            if explicit_x is not None:
                x_left = max(lm, min(right_bound - tw, int(explicit_x) - tw // 2))
            else:
                x_left = lm
            draw.text((x_left, y_top), text, font=f, fill=color)
            rw, _ = _text_size(draw, right, f)
            draw.text((W - rm - rw, y_top), right, font=f, fill=rc)

        else:
            pos_h = max(6, int(pos.get("h") or 14))
            if ln.get("left_align"):
                x_left = int(explicit_x) if explicit_x is not None else lm
                max_w  = right_bound - x_left
                f, text, tw = _fit_text(draw, text, f, pos_h, max_w, layout)
                draw.text((x_left, y_top), text, font=f, fill=color)
            else:
                cx = int(explicit_x) if explicit_x is not None else W // 2
                max_w = min(W - lm - rm, max(1, 2 * (right_bound - cx)))
                f, text, tw = _fit_text(draw, text, f, pos_h, max_w, layout)
                x_left = max(lm, min(right_bound - tw, cx - tw // 2))
                draw.text((x_left, y_top), text, font=f, fill=color)

        if explicit_y is None:
            auto_y += lh + gap

    if page.get("aqi_overlay"):
        _render_aqi_overlay(draw, page["aqi_overlay"], layout)
    if grid_items:
        _render_hourly_grid(draw, grid_items, grid_top, layout)

    if page.get("stale"):
        draw.rectangle([0, 0, W - 1, H - 1], outline=(140, 90, 0), width=2)

    return img


def _img_to_rgb565(img: "Image.Image") -> bytes:
    """Convert a PIL RGB image to little-endian RGB565 bytes.

    Uses numpy vectorised ops when available (~5-10x faster on ARMv6),
    otherwise falls back to a pure-Python tobytes() loop (~2x faster than
    the previous px[x,y] nested loop approach).
    """
    if _NUMPY:
        a = _np.frombuffer(img.tobytes(), _np.uint8).reshape(-1, 3).astype(_np.uint16)
        p = ((a[:, 0] & 0xF8) << 8) | ((a[:, 1] & 0xFC) << 3) | (a[:, 2] >> 3)
        return p.astype('<u2').tobytes()
    raw = img.tobytes()
    n   = len(raw) // 3
    buf = bytearray(n * 2)
    for i in range(n):
        r, g, b = raw[i*3], raw[i*3+1], raw[i*3+2]
        p = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        buf[i*2]   = p & 0xFF
        buf[i*2+1] = p >> 8
    return bytes(buf)


def _compose_page_from_cache(page_name: str, page_id: int) -> "bytes | None":
    """Compose a page frame from cached bg_arr + strip arrays. Returns None on miss."""
    bg_e = _page_bg_cache.get(page_name)
    if bg_e is None or bg_e.get("page_id") != page_id:
        return None
    try:
        frame = bg_e["bg_arr"].copy()
        for slot, px, py, pw, ph in bg_e["zones"]:
            sc = _page_strip_cache.get(slot)
            if sc is None or sc.get("arr") is None:
                return None
            # Advance scroll state (normally done inside render_page_pil)
            off, needs = _tick_scroll(slot, sc["key"], sc["title_w"], sc["max_w"])
            if not needs:
                return None  # text no longer scrolling — fall back to PIL
            sa    = sc["arr"]
            sw    = sc["strip_w"]
            avail = sw - off
            if avail >= pw:
                frame[py:py+ph, px:px+pw] = sa[:, off:off+pw]
            else:
                frame[py:py+ph, px:px+avail]   = sa[:, off:]
                frame[py:py+ph, px+avail:px+pw] = sa[:, :pw-avail]
        return frame.tobytes()
    except Exception as exc:
        print(f"[render] page compose: {exc}")
        return None


def _build_page_cache(page_name: str, page_id: int,
                      img: "Image.Image", zones: list) -> None:
    """Store bg_arr (scroll zones blanked) and zone metadata, keyed by page object id."""
    try:
        bg_img  = img.copy()
        bg_draw = ImageDraw.Draw(bg_img)
        for _, px, py, pw, ph in zones:
            bg_draw.rectangle([px, py, px + pw, py + ph], fill=(0, 0, 0))
        _page_bg_cache[page_name] = {
            "page_id": page_id,
            "bg_arr":  _pil_to_arr(bg_img),
            "zones":   zones[:],
        }
    except Exception as exc:
        print(f"[render] page cache build: {exc}")


def _rotate_rgb565(data: bytes) -> bytes:
    """Reverse all 16-bit pixel words — equivalent to 180° rotation."""
    if _NUMPY:
        return _np.frombuffer(data, '<u2')[::-1].tobytes()
    n   = len(data) // 2
    buf = bytearray(n * 2)
    for i in range(n):
        j = n - 1 - i
        buf[i*2], buf[i*2+1] = data[j*2], data[j*2+1]
    return bytes(buf)


def render_page_rgb565(page: dict, layout: dict | None = None,
                       rotate_180: bool = False) -> bytes:
    """Render a page to raw RGB565 bytes for the framebuffer."""
    page_name = page.get("_name", "")

    page_id = id(page)

    # ── Spotify fast path: full numpy cache, tiny PIL progress zone ───────────
    if page_name == "spotify":
        data = _render_spotify_fast(page, layout)
        if data is not None:
            return _rotate_rgb565(data) if rotate_180 else data

    # ── General scroll page fast path: skip PIL on cache hit ─────────────────
    if _NUMPY and page_name not in ("custom_image",):
        data = _compose_page_from_cache(page_name, page_id)
        if data is not None:
            return _rotate_rgb565(data) if rotate_180 else data

    # ── Full PIL render (cache miss or numpy unavailable) ─────────────────────
    img = render_page_pil(page, layout)

    # If scroll zones were found, build/update bg cache for future ticks
    if _NUMPY and _current_scroll_zones and page_name not in ("custom_image",):
        _build_page_cache(page_name, page_id, img, _current_scroll_zones)
        # Use the newly cached data for this frame too
        data = _compose_page_from_cache(page_name, page_id)
        if data is not None:
            return _rotate_rgb565(data) if rotate_180 else data

    # ── Standard PIL → RGB565 (non-scroll pages or numpy unavailable) ─────────
    data = _img_to_rgb565(img)
    return _rotate_rgb565(data) if rotate_180 else data


def render_sleep_frame(W: int, H: int, x_off: int = 0, y_off: int = 0,
                       rotate_180: bool = False, layout: dict | None = None) -> bytes:
    """Render a black screensaver frame with 'zzz' shifted by (x_off, y_off) from center."""
    img  = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _get_font(64, layout or {"font": {"path": ""}})
    text = "zzz"
    tw, th = _text_size(draw, text, font)
    x = max(0, min((W - tw) // 2 + x_off, W - tw))
    y = max(0, min((H - th) // 2 + y_off, H - th))
    draw.text((x, y), text, font=font, fill=(70, 70, 70))
    data = _img_to_rgb565(img)
    if rotate_180 and _NUMPY:
        data = _np.frombuffer(data, '<u2')[::-1].tobytes()
    elif rotate_180:
        n   = len(data) // 2
        buf = bytearray(n * 2)
        for i in range(n):
            j = n - 1 - i
            buf[i*2], buf[i*2+1] = data[j*2], data[j*2+1]
        data = bytes(buf)
    return data


def solid_frame(W: int, H: int, color_rgb: tuple[int, int, int]) -> bytes:
    """Return a solid-color RGB565 frame (for shutdown/error states)."""
    r, g, b = color_rgb
    p = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    pixel = bytes([p & 0xFF, (p >> 8) & 0xFF])
    return pixel * (W * H)
