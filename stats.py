"""System statistics collection and stats overlay rendering."""
from __future__ import annotations

import os
import threading
import time
from typing import Optional

try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

from render import _img_to_rgb565

# Taps at or below this Y-fraction trigger power-off
POWEROFF_Y_FRAC = 0.59

_TTF_PATHS = [
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
]


def _truetype(size: int):
    """Load a TTF font at the given size, falling back to PIL's default."""
    for path in _TTF_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


# Fonts loaded once on first render, then reused (not recreated every ~2s)
_stats_f_big:  object = None
_stats_f_info: object = None
_stats_f_btn:  object = None


def _ensure_stats_fonts() -> None:
    global _stats_f_big, _stats_f_info, _stats_f_btn
    if _stats_f_big is not None:
        return
    try:
        _stats_f_big  = ImageFont.load_default(size=22)
        _stats_f_info = ImageFont.load_default(size=13)
    except TypeError:  # Pillow < 10
        _stats_f_big = _stats_f_info = ImageFont.load_default()
    _stats_f_btn = _truetype(44)


class StatsMonitor:
    """Background thread that samples system stats once per second."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data: dict = {}
        self._prev_cpu: tuple | None = None
        self._prev_net: tuple[int, int] | None = None
        self._prev_t = time.monotonic()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                self._sample()
            except Exception:
                pass
            time.sleep(1)

    def _sample(self):
        cpu              = self._cpu_pct()
        ram_used, ram_t  = self._ram()
        disk_used, disk_t = self._disk()
        temp             = self._temp()
        rx, tx           = self._net_bytes()
        uptime           = self._uptime()
        now              = time.monotonic()

        rx_rate = tx_rate = 0.0
        if self._prev_net is not None:
            dt = now - self._prev_t
            if dt > 0:
                rx_rate = max(0, rx - self._prev_net[0]) / dt
                tx_rate = max(0, tx - self._prev_net[1]) / dt
        self._prev_net = (rx, tx)
        self._prev_t   = now

        with self._lock:
            self._data = {
                "cpu_pct":    cpu,
                "ram_used":   ram_used,
                "ram_total":  ram_t,
                "disk_used":  disk_used,
                "disk_total": disk_t,
                "temp_c":     temp,
                "rx_bps":     rx_rate,
                "tx_bps":     tx_rate,
                "uptime_s":   uptime,
            }

    def _cpu_pct(self) -> float:
        idle, total = self._stat()
        if self._prev_cpu is None:
            self._prev_cpu = (idle, total)
            return 0.0
        pi, pt = self._prev_cpu
        self._prev_cpu = (idle, total)
        dt = total - pt
        di = idle  - pi
        return max(0.0, min(100.0, (1 - di / dt) * 100)) if dt > 0 else 0.0

    def _stat(self) -> tuple[int, int]:
        with open("/proc/stat") as f:
            parts = f.readline().split()
        vals = [int(x) for x in parts[1:8]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)  # idle + iowait
        return idle, sum(vals)

    def _ram(self) -> tuple[int, int]:
        info: dict[str, int] = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                info[k.strip()] = int(v.split()[0]) * 1024
        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        return total - avail, total

    def _disk(self) -> tuple[int, int]:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free  = st.f_bfree  * st.f_frsize
        return total - free, total

    def _temp(self) -> Optional[float]:
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                return int(f.read()) / 1000.0
        except Exception:
            return None

    def _uptime(self) -> float:
        try:
            with open("/proc/uptime") as f:
                return float(f.read().split()[0])
        except Exception:
            return 0.0

    def _net_bytes(self) -> tuple[int, int]:
        rx = tx = 0
        try:
            with open("/proc/net/dev") as f:
                for line in f:
                    if ":" not in line:
                        continue
                    iface, data = line.split(":", 1)
                    if iface.strip() == "lo":
                        continue
                    cols = data.split()
                    rx += int(cols[0])
                    tx += int(cols[8])
        except Exception:
            pass
        return rx, tx

    def get(self) -> dict:
        with self._lock:
            return dict(self._data)


def _fmt_uptime(s: float) -> str:
    s = int(s)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m    = s // 60
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def render_stats_rgb565(monitor: StatsMonitor, W: int, H: int,
                        rotate_180: bool = False) -> bytes:
    """Render a fast bitmap-font stats overlay and return RGB565 bytes."""
    if not _PIL_OK:
        return bytes(W * H * 2)

    d    = monitor.get()
    img  = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    _ensure_stats_fonts()
    f_big, f_info, f_btn = _stats_f_big, _stats_f_info, _stats_f_btn

    cpu    = d.get("cpu_pct", 0.0)
    ram_u  = d.get("ram_used",  0)
    ram_t  = d.get("ram_total", 1)
    disk_u = d.get("disk_used",  0)
    disk_t = d.get("disk_total", 1)
    ram_pct  = ram_u  / ram_t  * 100 if ram_t  else 0
    disk_pct = disk_u / disk_t * 100 if disk_t else 0
    temp   = d.get("temp_c")

    cpu_c  = (255, 80,  80)  if cpu      > 80 else (255, 200, 0) if cpu      > 60 else (80, 255, 80)
    ram_c  = (255, 140, 0)   if ram_pct  > 80 else (80,  180, 255)
    disk_c = (220, 200, 60)
    temp_c = (255, 80,  80)  if temp and temp > 70 else (200, 200, 200)

    ROW_H = 26   # height of each bar row
    LBL_W = 54   # label column width
    VAL_W = 60   # value column width on right
    BAR_H = 12   # progress bar height
    GAP   = 3    # gap between rows

    def bar_row(y, label, val_str, val_c, pct, bar_c):
        ty = y + (ROW_H - 22) // 2
        draw.text((4, ty), label, fill=(160, 160, 160), font=f_big)
        bx = LBL_W
        bw = W - LBL_W - VAL_W - 6
        by = y + (ROW_H - BAR_H) // 2
        draw.rectangle([bx, by, bx + bw, by + BAR_H], fill=(30, 30, 30))
        fw = max(0, int(bw * min(pct, 100) / 100))
        if fw:
            draw.rectangle([bx, by, bx + fw, by + BAR_H], fill=bar_c)
        draw.text((W - VAL_W + 4, ty), val_str, fill=val_c, font=f_big)

    y0 = 6
    bar_row(y0,                   "CPU",  f"{cpu:.0f}%",      cpu_c,  cpu,      cpu_c)
    bar_row(y0 + ROW_H + GAP,     "RAM",  f"{ram_pct:.0f}%",  ram_c,  ram_pct,  ram_c)
    bar_row(y0 + 2 * (ROW_H+GAP), "DISK", f"{disk_pct:.0f}%", disk_c, disk_pct, disk_c)

    # Secondary info — two compact rows, each split left/right
    y_info = y0 + 3 * (ROW_H + GAP) + 6
    draw.text((4,      y_info), f"TEMP {temp:.1f} C" if temp else "TEMP --",
              fill=temp_c, font=f_info)
    draw.text((W // 2, y_info), f"MEM {_fmt_bytes(ram_u)} / {_fmt_bytes(ram_t)}",
              fill=(140, 140, 160), font=f_info)

    y_net = y_info + 16
    draw.text((4,      y_net), f"DOWN {_fmt_bytes(d.get('rx_bps', 0))}/s",
              fill=(80, 180, 255), font=f_info)
    draw.text((W // 2, y_net), f"UP {_fmt_bytes(d.get('tx_bps', 0))}/s",
              fill=(80, 220, 100), font=f_info)

    y_up = y_net + 16
    draw.text((4, y_up), f"UPTIME {_fmt_uptime(d.get('uptime_s', 0))}",
              fill=(160, 160, 160), font=f_info)

    # Power-off button
    py = int(H * POWEROFF_Y_FRAC)
    draw.line([(0, py - 2), (W, py - 2)], fill=(60, 60, 60))
    draw.rectangle([4, py + 2, W - 4, H - 4], fill=(120, 20, 20), outline=(220, 60, 60))
    try:
        bb = draw.textbbox((0, 0), "Power Off", font=f_btn)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        top_offset = bb[1]
    except AttributeError:  # Pillow < 8
        tw, th, top_offset = len("Power Off") * 6, 11, 0
    btn_top = py + 2
    btn_h   = H - 4 - btn_top
    tx = (W - tw) // 2
    ty = btn_top + (btn_h - th) // 2 - top_offset
    draw.text((tx, ty), "Hold to Power Off", fill=(255, 200, 200), font=f_btn)

    if rotate_180:
        img = img.rotate(180)

    return _img_to_rgb565(img)
