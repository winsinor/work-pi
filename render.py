"""PIL rendering pipeline — page dict → RGB image → RGB565 bytes."""
from __future__ import annotations

import copy
import io
import math
import os

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
    gap   = 3
    y0    = grid_top + 5
    y_tmp  = y0 + lh + gap + 2
    y_rain = y_tmp + th + gap + 2

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


def render_spotify_page(page: dict, layout: dict) -> "Image.Image":
    W   = layout["canvas"]["width"]
    H   = layout["canvas"]["height"]
    hh  = layout["header"]["height"]
    img  = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    if hh > 0:
        draw.rectangle([0, 0, W, hh], fill=tuple(layout["header"]["bg"]))
        title_f = _get_font(20, layout)
        title   = page.get("title", "Now Playing")
        tw, th  = _text_size(draw, title, title_f)
        draw.text(((W - tw) // 2, (hh - th) // 2), title,
                  font=title_f, fill=_pil_color(layout["header"]["title_color"]))
        draw.line([(0, hh), (W, hh)], fill=(60, 60, 60), width=1)

    content_y0 = hh + 4
    content_h  = H - content_y0 - 4

    ART_PAD  = 8
    ART_SIZE = min(content_h - ART_PAD * 2, 110)
    art_x    = ART_PAD
    art_y    = content_y0 + (content_h - ART_SIZE) // 2

    art_url = page.get("art_url")
    if art_url:
        art_img = _fetch_album_art(art_url, ART_SIZE)
        if art_img:
            img.paste(art_img, (art_x, art_y))
        else:
            draw.rectangle([art_x, art_y, art_x + ART_SIZE, art_y + ART_SIZE],
                           fill=(35, 35, 35), outline=(70, 70, 70))
    else:
        draw.rectangle([art_x, art_y, art_x + ART_SIZE, art_y + ART_SIZE],
                       fill=(35, 35, 35), outline=(70, 70, 70))

    TEXT_X = art_x + ART_SIZE + 10
    TEXT_W = W - TEXT_X - 6

    f_track  = _get_font(17, layout)
    f_artist = _get_font(14, layout)
    f_album  = _get_font(12, layout)

    track  = page.get("track",  "") or ""
    artist = page.get("artist", "") or ""
    album  = page.get("album",  "") or ""

    def measure_block():
        lines = []
        for text, font in ((track, f_track), (artist, f_artist), (album, f_album)):
            text = _truncate_to_fit(draw, text, font, TEXT_W)
            _, lh = _text_size(draw, text or " ", font)
            lines.append((text, font, lh))
        return lines

    text_lines = measure_block()
    GAP = 5
    total_h = sum(lh for _, _, lh in text_lines) + GAP * (len(text_lines) - 1)
    ty = content_y0 + (content_h - total_h) // 2

    colors = [(255, 255, 255), (100, 190, 255), (140, 140, 140)]
    for (text, font, lh), color in zip(text_lines, colors):
        if text:
            draw.text((TEXT_X, ty), text, font=font, fill=color)
        ty += lh + GAP

    return img


# ── Main renderer ────────────────────────────────────────────────────────────────────

def render_page_pil(page: dict, layout: dict | None = None) -> "Image.Image":
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
        draw.text(((W - btw) // 2, b0 + (BANNER_H - bth) // 2), btext,
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
                    l1 = _truncate_to_fit(draw, words[0] if words else text, active_font, max_w)
                    l2 = ""
                else:
                    l1     = " ".join(words[:last_mid])
                    l2_raw = " ".join(words[last_mid:])
                    l2     = _truncate_to_fit(draw, l2_raw, active_font, max_w) if l2_raw else ""
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
        a = _np.frombuffer(img.tobytes(), dtype=_np.uint8).reshape(-1, 3)
        r = a[:, 0].astype(_np.uint16)
        g = a[:, 1].astype(_np.uint16)
        b = a[:, 2].astype(_np.uint16)
        p   = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out = _np.empty(p.size * 2, dtype=_np.uint8)
        out[0::2] = (p & 0xFF).astype(_np.uint8)
        out[1::2] = (p >> 8).astype(_np.uint8)
        return bytes(out)
    raw = img.tobytes()
    n   = len(raw) // 3
    buf = bytearray(n * 2)
    for i in range(n):
        r, g, b = raw[i*3], raw[i*3+1], raw[i*3+2]
        p = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        buf[i*2]   = p & 0xFF
        buf[i*2+1] = p >> 8
    return bytes(buf)


def render_page_rgb565(page: dict, layout: dict | None = None,
                       rotate_180: bool = False) -> bytes:
    """Render a page to raw RGB565 bytes for the framebuffer."""
    img = render_page_pil(page, layout)
    if rotate_180:
        img = img.rotate(180)
    return _img_to_rgb565(img)


def solid_frame(W: int, H: int, color_rgb: tuple[int, int, int]) -> bytes:
    """Return a solid-color RGB565 frame (for shutdown/error states)."""
    r, g, b = color_rgb
    p = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    pixel = bytes([p & 0xFF, (p >> 8) & 0xFF])
    return pixel * (W * H)
