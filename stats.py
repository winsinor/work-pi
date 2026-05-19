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
                        font_path: str, rotate_180: bool = False) -> bytes:
    """Render the stats overlay and return RGB565 bytes."""
    if not _PIL_OK:
        return bytes(W * H * 2)

    d    = monitor.get()
    img  = Image.new("RGB", (W, H), (8, 8, 24))
    draw = ImageDraw.Draw(img)

    def _font(size: int):
        try:
            return ImageFont.truetype(font_path, size)
        except Exception:
            return ImageFont.load_default()

    f_title = _font(16)
    f_label = _font(13)
    f_sm    = _font(11)
    f_btn   = _font(15)

    # ── Title ──────────────────────────────────────────────────────────────────
    draw.text((W // 2, 5), "System Stats",
              fill=(100, 200, 255), font=f_title, anchor="mt")

    # ── Bar helper ─────────────────────────────────────────────────────────────
    def bar(y: int, label: str, pct: float, color: tuple):
        bx, bw = 52, W - 52 - 38
        bh = 14
        draw.text((5, y), label, fill=(180, 180, 180), font=f_label)
        draw.rectangle([bx, y, bx + bw, y + bh],
                       fill=(28, 28, 48), outline=(50, 50, 72))
        fw = max(0, int(bw * min(pct, 100) / 100))
        if fw:
            draw.rectangle([bx, y, bx + fw, y + bh], fill=color)
        draw.text((bx + bw + 4, y), f"{pct:.0f}%",
                  fill=(160, 160, 160), font=f_sm)

    # ── Stats rows ─────────────────────────────────────────────────────────────
    cpu      = d.get("cpu_pct", 0.0)
    ram_u    = d.get("ram_used",  0)
    ram_t    = d.get("ram_total", 1)
    disk_u   = d.get("disk_used",  0)
    disk_t   = d.get("disk_total", 1)
    ram_pct  = ram_u  / ram_t  * 100 if ram_t  else 0
    disk_pct = disk_u / disk_t * 100 if disk_t else 0

    cpu_c  = (200, 60, 60) if cpu > 80 else (220, 160, 40) if cpu > 60 else (60, 200, 80)
    ram_c  = (200, 100, 40) if ram_pct > 80 else (60, 150, 220)
    disk_c = (200, 180, 40)

    bar(27, "CPU",  cpu,      cpu_c)
    bar(48, "RAM",  ram_pct,  ram_c)
    bar(69, "DISK", disk_pct, disk_c)

    draw.text((5, 90), f"{_fmt_bytes(ram_u)} / {_fmt_bytes(ram_t)}",
              fill=(100, 100, 130), font=f_sm)

    temp = d.get("temp_c")
    tc   = (255, 80, 80) if temp and temp > 70 else (180, 180, 180)
    draw.text((5, 106), f"Temp  {temp:.1f}°C" if temp else "Temp  —",
              fill=tc, font=f_label)

    draw.text((5, 126), f"↓  {_fmt_bytes(d.get('rx_bps', 0))}/s",
              fill=(80, 180, 255), font=f_label)
    draw.text((5, 144), f"↑  {_fmt_bytes(d.get('tx_bps', 0))}/s",
              fill=(80, 220, 100), font=f_label)

    # ── Power-off button ───────────────────────────────────────────────────────
    py = int(H * POWEROFF_Y_FRAC)
    draw.line([(4, py - 3), (W - 4, py - 3)], fill=(50, 50, 80))
    draw.rectangle([4, py, W - 4, H - 4],
                   fill=(100, 18, 18), outline=(200, 50, 50), width=2)
    draw.text((W // 2, py + (H - 4 - py) // 2), "Power Off",
              fill=(255, 200, 200), font=f_btn, anchor="mm")

    if rotate_180:
        img = img.rotate(180)

    # ── PIL → RGB565 ───────────────────────────────────────────────────────────
    W2, H2 = img.size
    buf = bytearray(W2 * H2 * 2)
    px  = img.load()
    for y in range(H2):
        for x in range(W2):
            r, g, b = px[x, y]
            p = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            i = (y * W2 + x) * 2
            buf[i]     = p & 0xFF
            buf[i + 1] = (p >> 8) & 0xFF
    return bytes(buf)
