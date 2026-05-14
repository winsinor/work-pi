"""PIL rendering pipeline — page dict → RGB image → RGB565 bytes."""
from __future__ import annotations

import copy
import io
import math
import os

try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

try:
    import cairosvg
    _CAIROSVG_AVAILABLE = True
except ImportError:
    _CAIROSVG_AVAILABLE = False

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
        "clock":          {"enabled": True, "dwell_seconds": 8},
        "forecast":       {"enabled": True, "dwell_seconds": 10},
        "calendar":       {"enabled": True, "dwell_seconds": 12},
        "calendar_empty": {"enabled": True, "dwell_seconds": 8},
        "commute":        {"enabled": True, "dwell_seconds": 10},
        "wfh":            {"enabled": True, "dwell_seconds": 10},
        "ooo":            {"enabled": True, "dwell_seconds": 10},
        "holiday":        {"enabled": True, "dwell_seconds": 10},
        "setup":          {"enabled": True, "dwell_seconds": 10},
        "loading":        {"enabled": True, "dwell_seconds": 3},
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
        "setup":    [{"x": None, "y": None, "h": 24}, {"x": None, "y": None, "h": 32}, {"x": None, "y": None, "h": 18}],
        "loading":  [{"x": None, "y": None, "h": 24}],
        "error":    [{"x": None, "y": None, "h": 24}],
        "shutdown": [{"x": None, "y": None, "h": 40}],
    },
    "overflow": {"min_font_pct": 60},
}

_layout_cache: dict = {"data": None, "mtime": 0.0}


def load_layout(font_path: str | None = None) -> dict:
    """Merge work_layout.json over LAYOUT_DEFAULTS; cache by file mtime."""
    import json
    try:
        mtime = os.path.getmtime(_LAYOUT_FILE)
    except OSError:
        mtime = 0.0

    if _layout_cache["data"] is None or mtime != _layout_cache["mtime"]:
        try:
            with open(_LAYOUT_FILE) as f:
                overrides = json.load(f)
            merged = copy.deepcopy(LAYOUT_DEFAULTS)
            for k, v in overrides.items():
                if isinstance(v, dict) and k in merged and isinstance(merged[k], dict):
                    merged[k].update(v)
                else:
                    merged[k] = v
            _layout_cache["data"]  = merged
            _layout_cache["mtime"] = mtime
        except Exception as exc:
            print(f"[layout] load failed: {exc}")
            _layout_cache["data"]  = copy.deepcopy(LAYOUT_DEFAULTS)
            _layout_cache["mtime"] = mtime

    layout = _layout_cache["data"]
    if font_path:
        layout = copy.deepcopy(layout)
        layout["font"]["path"] = font_path
    return layout


def invalidate_layout_cache() -> None:
    _layout_cache["data"] = None
    _layout_cache["mtime"] = 0.0


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


def _load_static_icon(name: str, r: int):
    if not _CAIROSVG_AVAILABLE or not _PIL_AVAILABLE:
        return None
    filename = _STATIC_ICON_MAP.get(name)
    if not filename:
        return None
    cache_key = (name, r)
    if cache_key in _static_icon_cache:
        return _static_icon_cache[cache_key]
    svg_path = os.path.join(_ICONS_DIR, filename)
    if not os.path.exists(svg_path):
        _static_icon_cache[cache_key] = None
        return None
    try:
        png = cairosvg.svg2png(url=svg_path, output_width=r * 2)
        img = Image.open(io.BytesIO(png)).convert("RGBA")
        _static_icon_cache[cache_key] = img
        return img
    except Exception as exc:
        print(f"[icon] {filename}: {exc}")
        _static_icon_cache[cache_key] = None
        return None


def _load_png_icon(name: str, size: int) -> "Image.Image | None":
    """Load a user-supplied PNG from icons/<name>.png, scaled to size×size."""
    if not _PIL_AVAILABLE:
        return None
    cache_key = (f"{name}.png", size)
    if cache_key in _static_icon_cache:
        return _static_icon_cache[cache_key]
    path = os.path.join(_ICONS_DIR, f"{name}.png")
    if not os.path.exists(path):
        _static_icon_cache[cache_key] = None
        return None
    try:
        img = Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
        _static_icon_cache[cache_key] = img
        return img
    except Exception as exc:
        print(f"[icon] PNG load failed for {path}: {exc}")
        _static_icon_cache[cache_key] = None
        return None


def _draw_weather_icon(img, draw, name: str, cx: int, cy: int, r: int):
    # Priority: user PNG → static SVG → PIL-drawn fallback
    png = _load_png_icon(name, r * 2)
    if png is not None:
        iw, ih = png.size
        img.paste(png, (cx - iw // 2, cy - ih // 2), png)
        return
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


# ── Main renderer ────────────────────────────────────────────────────────────────────

def render_page_pil(page: dict, layout: dict | None = None) -> "Image.Image":
    if layout is None:
        layout = load_layout()
    if "objects" in page:
        return render_objects_pil(page, layout)
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
    content_h  = grid_top - content_y0

    lines      = list(page.get("lines") or [])
    page_name  = page.get("_name", "")
    positions  = layout.get("line_positions", {}).get(page_name, [])
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

        y_top    = (int(explicit_y) - lh // 2) if explicit_y is not None else auto_y
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

    return img


def render_page_rgb565(page: dict, layout: dict | None = None,
                       rotate_180: bool = False) -> bytes:
    """Render a page to raw RGB565 bytes for the framebuffer."""
    img = render_page_pil(page, layout)
    if rotate_180:
        img = img.rotate(180)
    W, H = img.size
    buf = bytearray(W * H * 2)
    px  = img.load()
    for y in range(H):
        for x in range(W):
            r, g, b = px[x, y]
            p = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            i = (y * W + x) * 2
            buf[i]     = p & 0xFF
            buf[i + 1] = (p >> 8) & 0xFF
    return bytes(buf)


def render_objects_pil(page: dict, layout: dict) -> "Image.Image":
    """Render a page whose content is defined by page["objects"] — a list of typed object dicts.

    Coordinates are center-based: x/y are the center of the object's bounding box.
    x=None defaults to horizontal canvas center. y=None on text objects triggers
    automatic even distribution within the content area. All other object types
    default to canvas center when x/y are omitted.
    """
    if not _PIL_AVAILABLE:
        raise RuntimeError("Pillow is required for rendering (pip3 install Pillow)")
    W        = layout["canvas"]["width"]
    H        = layout["canvas"]["height"]
    gap_min  = layout["content"].get("line_gap_min", 2)
    min_pct  = float(layout.get("overflow", {}).get("min_font_pct", 60.0))

    objects: list[dict] = list(page.get("objects", []))
    if page.get("hourly_grid") and not any(o.get("type") == "grid" for o in objects):
        objects.append({"type": "grid", "data": page["hourly_grid"]})

    # ── Canvas background ──────────────────────────────────────────────────
    bg_obj  = next((o for o in objects if o.get("type") == "background"), None)
    bg_col  = _pil_color((bg_obj or {}).get("color", "black"))
    bg_path = (bg_obj or {}).get("image", "")
    if bg_path and not os.path.isabs(bg_path):
        bg_path = os.path.join(_BASE, bg_path)
    if bg_path and os.path.exists(bg_path):
        try:
            img = Image.open(bg_path).convert("RGB").resize((W, H), Image.LANCZOS)
        except Exception:
            img = Image.new("RGB", (W, H), bg_col)
    else:
        img = Image.new("RGB", (W, H), bg_col)
    draw = ImageDraw.Draw(img)

    # ── Reserve space for bottom-anchored grids ────────────────────────────
    grid_h = 0
    for o in objects:
        if o.get("type") == "grid" and o.get("y") is None:
            grid_h = max(grid_h, int(o.get("height") or layout["grid"]["height"]))
    content_h = H - grid_h

    # ── Auto-Y distribution for text objects with y=None ──────────────────
    auto_text_idxs = [
        i for i, o in enumerate(objects)
        if o.get("type") == "text" and o.get("visible", True) and o.get("y") is None
    ]
    auto_y_map: dict[int, int] = {}
    if auto_text_idxs:
        heights = []
        for i in auto_text_idxs:
            o  = objects[i]
            f  = _get_font(int(o.get("h", 14)), layout)
            _, th = _text_size(draw, str(o.get("text", "")), f)
            heights.append(max(th, int(o.get("h", 14))))
        total_h = sum(heights)
        n       = len(heights)
        gap     = max(gap_min, (content_h - total_h) // (n + 1))
        for slot, obj_i in enumerate(auto_text_idxs):
            auto_y_map[obj_i] = gap * (slot + 1) + sum(heights[:slot]) + heights[slot] // 2

    # ── Render each object ─────────────────────────────────────────────────
    for obj_i, obj in enumerate(objects):
        otype = obj.get("type")
        if not obj.get("visible", True) or otype == "background":
            continue

        if otype == "text":
            text     = str(obj.get("text", ""))
            h_pt     = int(obj.get("h", 14))
            color    = _pil_color(obj.get("color", "white"))
            align    = obj.get("align", "center")
            max_w    = obj.get("max_width")
            overflow = obj.get("overflow", ["shrink", "truncate"])
            if isinstance(overflow, str):
                overflow = [overflow]
            font = _get_font(h_pt, layout)
            cx   = W // 2 if obj.get("x") is None else int(obj["x"])
            cy   = auto_y_map.get(obj_i, H // 2) if obj.get("y") is None else int(obj["y"])
            if max_w is None:
                lm = layout["content"].get("left_margin", 4)
                rm = layout["content"].get("right_margin", 4)
                if align == "center":
                    max_w = W - lm - rm
                elif align == "left":
                    max_w = W - cx - rm
                else:
                    max_w = cx - lm
            tw, th = _text_size(draw, text, font)
            if tw > max_w:
                if "shrink" in overflow:
                    min_h  = max(6, int(h_pt * min_pct / 100.0))
                    cur_h  = h_pt - 1
                    while cur_h >= min_h:
                        font = _get_font(cur_h, layout)
                        tw, th = _text_size(draw, text, font)
                        if tw <= max_w:
                            break
                        cur_h -= 1
                if "wrap" in overflow and tw > max_w:
                    words: list[str] = text.split()
                    wrap_lines: list[str] = []
                    cur: list[str] = []
                    for word in words:
                        probe = " ".join(cur + [word])
                        if _text_size(draw, probe, font)[0] <= max_w:
                            cur.append(word)
                        else:
                            if cur:
                                wrap_lines.append(" ".join(cur))
                            cur = [word]
                    if cur:
                        wrap_lines.append(" ".join(cur))
                    if len(wrap_lines) > 1:
                        line_gap  = 2
                        line_hs   = [_text_size(draw, ln, font)[1] for ln in wrap_lines]
                        total_wh  = sum(line_hs) + line_gap * (len(wrap_lines) - 1)
                        ly        = cy - total_wh // 2
                        for ln, lh in zip(wrap_lines, line_hs):
                            lw, _ = _text_size(draw, ln, font)
                            lx = (cx - lw // 2 if align == "center"
                                  else cx if align == "left" else cx - lw)
                            draw.text((lx, ly), ln, font=font, fill=color)
                            ly += lh + line_gap
                        continue
                if "truncate" in overflow and tw > max_w:
                    text = _truncate_to_fit(draw, text, font, max_w)
                    tw, th = _text_size(draw, text, font)
            draw_x = (cx - tw // 2 if align == "center"
                      else cx if align == "left" else cx - tw)
            draw.text((draw_x, cy - th // 2), text, font=font, fill=color)

        elif otype == "line":
            draw.line(
                [(int(obj.get("x1", 0)),  int(obj.get("y1", H // 2))),
                 (int(obj.get("x2", W)),  int(obj.get("y2", H // 2)))],
                fill=_pil_color(obj.get("color", "grey")),
                width=int(obj.get("width", 1)),
            )

        elif otype == "image":
            path = obj.get("path", "")
            if not os.path.isabs(path):
                path = os.path.join(_BASE, path)
            if not os.path.exists(path):
                continue
            try:
                pil_img = Image.open(path).convert("RGBA")
                iw = int(obj["width"])  if "width"  in obj else pil_img.width
                ih = int(obj["height"]) if "height" in obj else pil_img.height
                if (iw, ih) != pil_img.size:
                    pil_img = pil_img.resize((iw, ih), Image.LANCZOS)
                ix = W // 2 if obj.get("x") is None else int(obj["x"])
                iy = H // 2 if obj.get("y") is None else int(obj["y"])
                img.paste(pil_img, (ix - iw // 2, iy - ih // 2), pil_img)
            except Exception as exc:
                print(f"[objects] image '{obj.get('path')}': {exc}")

        elif otype == "icon":
            size = int(obj.get("size", 56))
            ix   = W // 2 if obj.get("x") is None else int(obj["x"])
            iy   = H // 2 if obj.get("y") is None else int(obj["y"])
            _draw_weather_icon(img, draw, obj.get("icon", "sun"), ix, iy, size // 2)

        elif otype == "grid":
            items = obj.get("data") or []
            if not items:
                continue
            gh       = int(obj.get("height") or layout["grid"]["height"])
            grid_top = (H - gh) if obj.get("y") is None else int(obj["y"])
            g_layout = dict(layout)
            g_layout["grid"] = dict(layout["grid"])
            for key in ("columns", "label_size", "temp_size", "rain_size", "hum_size", "wind_size"):
                if key in obj:
                    g_layout["grid"][key] = obj[key]
            _render_hourly_grid(draw, items, grid_top, g_layout)

    if page.get("aqi_overlay"):
        _render_aqi_overlay(draw, page["aqi_overlay"], layout)
    return img


def solid_frame(W: int, H: int, color_rgb: tuple[int, int, int]) -> bytes:
    """Return a solid-color RGB565 frame (for shutdown/error states)."""
    r, g, b = color_rgb
    p = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    pixel = bytes([p & 0xFF, (p >> 8) & 0xFF])
    return pixel * (W * H)
