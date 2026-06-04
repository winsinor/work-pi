# TODO

## Ideas / Future Work

### Weather-reactive background gradient
Explore using a gradient background on the display pages to reflect current weather:
- Bright sunny blue sky when clear, getting darker/greyer as chance of rain increases
- Background shifts with sunrise/sunset — lighter during day, darker at night
- Display text must always remain readable regardless of background (ensure contrast)
- Could use the existing weather icon / precipitation probability from the forecast data

### Weather alert banner positioning (muted for now)
The NWS alert banner is currently muted in `pages.py` (`alert = None` in
`build_forecast_page`). Fix its positioning before re-enabling:
- It's drawn at a hardcoded spot in `render.py` (`render_page_pil`, ~L1097): a
  full-width 18px red strip at `content_y0` with right-aligned text. It is not
  represented in `work_layout.json`, so the layout editor can't move or preview it.
- An active alert pushes `content_y0` down for *auto*-positioned lines, but
  explicitly-positioned lines and the AQI overlay (top-right) keep their absolute
  Y — so the banner can overlap the AQI readout / fixed lines instead of cleanly
  reflowing the page.
- `BANNER_H` and the 12pt banner font are hardcoded for 320×240 and aren't
  auto-scaled to the actual display size like the rest of the layout.
- Re-enable by reverting the `alert = None` mute once positioning is handled
  (also add the banner to the layout editor + `_DEMO_PAGES` so it's previewable).
