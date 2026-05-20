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

# Taps at or below this Y-fraction trigger power-off
POWEROFF_Y_FRAC = 0.68


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

    # Use PIL's built-in bitmap font — no TrueType, no file I/O
    try:
        font = ImageFont.load_default(size=16)
        fsm  = ImageFont.load_default(size=13)
    except TypeError:  # Pillow < 10
        font = ImageFont.load_default()
        fsm  = font

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

    LX = 36   # label column width
    VAL_W = 52  # value column width on right

    def bar_row(y, label, val_str, val_c, pct, bar_c):
        draw.text((4, y), label, fill=(160, 160, 160), font=fsm)
        bx, bw, bh = LX, W - LX - VAL_W - 4, 12
        draw.rectangle([bx, y + 1, bx + bw, y + bh], fill=(30, 30, 30))
        fw = max(0, int(bw * min(pct, 100) / 100))
        if fw:
            draw.rectangle([bx, y + 1, bx + fw, y + bh], fill=bar_c)
        draw.text((W - VAL_W + 2, y), val_str, fill=val_c, font=fsm)

    def text_row(y, label, val_str, val_c):
        draw.text((4, y), label, fill=(160, 160, 160), font=fsm)
        draw.text((LX + 2, y), val_str, fill=val_c, font=fsm)

    bar_row(4,  "CPU",  f"{cpu:.0f}%",      cpu_c,  cpu,      cpu_c)
    bar_row(20, "RAM",  f"{ram_pct:.0f}%",  ram_c,  ram_pct,  ram_c)
    bar_row(36, "DISK", f"{disk_pct:.0f}%", disk_c, disk_pct, disk_c)
    text_row(52, "MEM",  f"{_fmt_bytes(ram_u)} / {_fmt_bytes(ram_t)}", (140, 140, 160))
    text_row(68, "TEMP", f"{temp:.1f} C" if temp else "--",            temp_c)
    text_row(84, "DOWN", f"{_fmt_bytes(d.get('rx_bps', 0))}/s",        (80, 180, 255))
    text_row(100,"UP",   f"{_fmt_bytes(d.get('tx_bps', 0))}/s",        (80, 220, 100))

    # Power-off button
    py = int(H * POWEROFF_Y_FRAC)
    draw.line([(0, py - 2), (W, py - 2)], fill=(60, 60, 60))
    draw.rectangle([4, py + 2, W - 4, H - 4], fill=(120, 20, 20), outline=(220, 60, 60))
    draw.text((W // 2, py + (H - py) // 2), "Power Off",
              fill=(255, 200, 200), font=font, anchor="mm")

    if rotate_180:
        img = img.rotate(180)

    raw = img.tobytes()  # RGBRGB...
    buf = bytearray(W * H * 2)
    for i in range(W * H):
        r, g, b = raw[i*3], raw[i*3+1], raw[i*3+2]
        p = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        buf[i*2]   = p & 0xFF
        buf[i*2+1] = p >> 8
    return bytes(buf)
