"""Touch input event reader for XPT2046/ADS7846-style touch controllers."""
from __future__ import annotations

import os
import re
import struct
import threading
import time
from typing import Callable, Optional

# struct input_event layout (64-bit vs 32-bit kernel)
_IS_64  = struct.calcsize("l") == 8
_FMT    = "qqHHi" if _IS_64 else "llHHi"
_SIZE   = struct.calcsize(_FMT)

EV_KEY            = 0x01
EV_ABS            = 0x03
BTN_TOUCH         = 0x14A
ABS_X             = 0x00
ABS_Y             = 0x01
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36

LONG_PRESS_S = 0.7    # seconds held before it's a long press
MAX_MOVE_RAW = 300    # max ADC-unit movement to still count as a tap (~25px on XPT2046)


def find_touch_device(cfg: dict | None = None) -> Optional[str]:
    """Return the evdev path for the first touchscreen found."""
    if cfg:
        override = cfg.get("touch", {}).get("device")
        if override and os.path.exists(override):
            return override

    try:
        with open("/proc/bus/input/devices") as f:
            content = f.read()
        keywords = ["xpt2046", "ads7846", "touchscreen", "ft5x", "stmpe",
                    "ili9341", "touch"]
        for block in content.split("\n\n"):
            if any(k in block.lower() for k in keywords):
                for line in block.splitlines():
                    if line.startswith("H:") and "event" in line:
                        m = re.search(r"event(\d+)", line)
                        if m:
                            path = f"/dev/input/event{m.group(1)}"
                            if os.path.exists(path):
                                return path
    except Exception:
        pass

    if os.path.exists("/dev/input/event0"):
        return "/dev/input/event0"
    return None


def start_touch(
    device: str,
    W: int,
    H: int,
    cfg: dict,
    on_tap: Callable[[int, int], None],
    on_long_press: Callable[[int, int], None],
) -> None:
    """Start background touch thread. Calls on_tap(sx,sy) or on_long_press(sx,sy)."""
    tcfg  = cfg.get("touch", {})
    min_x = tcfg.get("min_x",    200)
    max_x = tcfg.get("max_x",   3900)
    min_y = tcfg.get("min_y",    200)
    max_y = tcfg.get("max_y",   3900)
    swap  = tcfg.get("swap_axes", False)
    flip_x = tcfg.get("flip_x",  False)
    flip_y = tcfg.get("flip_y",  False)
    debug = tcfg.get("debug", False)

    def _scale(raw_x: int, raw_y: int) -> tuple[int, int]:
        if swap:
            raw_x, raw_y = raw_y, raw_x
        x = (raw_x - min_x) * W // max(1, max_x - min_x)
        y = (raw_y - min_y) * H // max(1, max_y - min_y)
        if flip_x:
            x = W - 1 - x
        if flip_y:
            y = H - 1 - y
        return max(0, min(W - 1, x)), max(0, min(H - 1, y))

    def _loop(f):
        press_t:   float | None  = None
        press_raw: list[int]     = [0, 0]
        cur_raw:   list[int]     = [0, 0]
        seen_x = False
        seen_y = False

        while True:
            data = f.read(_SIZE)
            if not data or len(data) < _SIZE:
                break
            _, _, etype, code, value = struct.unpack(_FMT, data)
            if debug and etype:
                print(f"[touch] ev type={etype:#x} code={code:#x} val={value}")

            if etype == EV_ABS:
                if code in (ABS_X, ABS_MT_POSITION_X):
                    cur_raw[0] = value
                    if press_t is not None:
                        seen_x = True
                elif code in (ABS_Y, ABS_MT_POSITION_Y):
                    cur_raw[1] = value
                    if press_t is not None:
                        seen_y = True
                # Lock in press position only once both axes updated after press
                if press_t is not None and seen_x and seen_y and press_raw == [0, 0]:
                    press_raw = list(cur_raw)

            elif etype == EV_KEY and code == BTN_TOUCH:
                if value == 1:                              # press
                    press_t   = time.monotonic()
                    press_raw = [0, 0]
                    seen_x = seen_y = False
                    if debug:
                        print("[touch] press")
                elif value == 0 and press_t is not None:   # release
                    dur   = time.monotonic() - press_t
                    press_t = None
                    if not (seen_x and seen_y):
                        if debug:
                            print("[touch] drop: no coords")
                        continue
                    moved = (abs(cur_raw[0] - press_raw[0])
                             + abs(cur_raw[1] - press_raw[1]))
                    if debug:
                        print(f"[touch] release: press={press_raw} cur={cur_raw} moved={moved} dur={dur:.2f}s")
                    if moved > MAX_MOVE_RAW:
                        if debug:
                            print(f"[touch] drop: drag (moved={moved})")
                        continue
                    sx, sy = _scale(press_raw[0], press_raw[1])
                    if debug:
                        print(f"[touch] {'long-press' if dur >= LONG_PRESS_S else 'tap'} sx={sx} sy={sy}")
                    if dur >= LONG_PRESS_S:
                        on_long_press(sx, sy)
                    else:
                        on_tap(sx, sy)

    def _run():
        while True:
            try:
                with open(device, "rb", buffering=0) as f:
                    _loop(f)
            except Exception as exc:
                print(f"[touch] {exc}")
            time.sleep(2)

    threading.Thread(target=_run, daemon=True).start()
    print(f"[touch] started on {device}")
