# work-pi — Claude context

## Hardware

- **Display**: 320×240 landscape (ILI9341-style SPI, XPT2046 touch)
- **Framebuffer**: `/dev/fb1`
- **Rotation**: 90° (configured in `/boot/config.txt` dtoverlay)

## Layout scaling

`work_layout.json` stores absolute pixel coordinates at whatever canvas size it was
originally designed at. `render.py:load_layout()` auto-scales everything to the
actual display size (`display_w`/`display_h` from config) at runtime, so the JSON
does **not** need to match the display resolution. Do not rewrite the layout JSON
to match display dimensions.

## Stats overlay

`stats.py` renders directly to RGB565 bytes (no layout scaling). All constants
(`ROW_H`, `LBL_W`, etc.) and `POWEROFF_Y_FRAC` are tuned for 320×240. If the
display size ever changes, these need to be updated manually.
