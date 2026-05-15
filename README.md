# work-dashboard

A standalone desk display for your work Pi. Shows the time, today's calendar,
weather forecast with AQI, and commute time home. Reads your Outlook calendar
to automatically switch into WFH, OOO, or Holiday mode.

No external server required — all data is fetched and rendered directly on the Pi.
First-time setup is done through a web UI served by the Pi itself.

```
┌──────────────────────────────────────────────────────┐
│                                                      │
│   10:42 AM          ╭────────────────────╮           │
│                     │  Standup           │           │
│   Thursday          │  in 18 min         │           │
│                     │  10:00 - 10:30 AM  │           │
│   May 15, 2026      ╰────────────────────╯           │
│                                                      │
└──────────────────────────────────────────────────────┘
```

---

## Table of Contents

1. [Supported hardware](#supported-hardware)
2. [What you need](#what-you-need)
3. [Step 1 — Flash and configure Raspberry Pi OS](#step-1--flash-and-configure-raspberry-pi-os)
4. [Step 2 — Enable the framebuffer display](#step-2--enable-the-framebuffer-display)
5. [Step 3 — Install work-dashboard](#step-3--install-work-dashboard)
6. [Step 4 — First-boot setup](#step-4--first-boot-setup)
7. [Step 5 — Install as a service](#step-5--install-as-a-service)
8. [Pages and what triggers them](#pages-and-what-triggers-them)
9. [Layout customisation](#layout-customisation)
10. [Configuration reference](#configuration-reference)
11. [GPIO buttons](#gpio-buttons)
12. [Getting your API key and calendar URL](#getting-your-api-key-and-calendar-url)
13. [Troubleshooting](#troubleshooting)
14. [Data sources](#data-sources)

---

## Supported hardware

### Raspberry Pi boards

All models with a 40-pin GPIO header are supported. The app is pure Python and
works on every Pi from the original 1 B+ through the Pi 5.

| Board | RAM | Notes |
|---|---|---|
| **Pi 1 Model B+** | 512 MB | Fully supported. PIL rendering takes ~0.5s per frame — acceptable for an 8s dwell. Skip `cairosvg`; the built-in icon renderer is used automatically. |
| **Pi 2 Model B** | 1 GB | Good performance. All features work. |
| **Pi 3 Model B / B+** | 1 GB | Recommended for reliable rendering + built-in WiFi. |
| **Pi 4 Model B** | 1–8 GB | Best performance. Renders a frame in < 100 ms. |
| **Pi 5** | 4–8 GB | Works. Uses the same framebuffer interface. |
| **Pi Zero W** | 512 MB | Supported. Built-in WiFi. Rendering is slower (~1s/frame); reduce dwell or skip AQI overlay if needed. Skip `cairosvg`. |
| **Pi Zero 2 W** | 512 MB | Good balance of size and speed. Built-in WiFi. |

### Display modules

Any display that exposes a Linux framebuffer device (`/dev/fb0`, `/dev/fb1`, etc.)
will work. The most common options:

| Display | Resolution | Interface | Driver / Overlay |
|---|---|---|---|
| **Generic 2.4"/2.8" RPi Display** (ILI9341, XPT2046) | 320×240 | SPI | `ili9341` — see [setup notes](#generic-24-28-rpi-display-ili9341) |
| Waveshare 3.5" (A/B/C) | 480×320 | SPI | `waveshare35a`, `waveshare35b`, `waveshare35c` |
| Waveshare 3.5" (E/F) | 480×320 | SPI | `waveshare35e` |
| Waveshare 2.8" | 320×240 | SPI | `waveshare28` |
| Pimoroni HyperPixel 4 | 800×480 | SPI/DSI | `hyperpixel4` |
| Adafruit PiTFT 3.5" | 480×320 | SPI | `pitft35-resistive` |
| Adafruit PiTFT 2.8" | 320×240 | SPI | `pitft28-resistive` |
| Any HDMI monitor | any | HDMI | `/dev/fb0` (default) |
| Any composite monitor | 720×480 | Composite | `/dev/fb0` |

> **Other SPI displays**: If your display uses an ILI9486, ILI9488, ST7796, or
> ILI9341 controller and is supported by the `dtoverlay` system, it will work.
> Check your display's documentation for the correct overlay name.

---

## What you need

- Raspberry Pi (any model with 40-pin header)
- MicroSD card (8 GB or larger, Class 10 recommended)
- Compatible display module **or** an HDMI monitor
- Power supply appropriate for your Pi model
- Internet connection for initial setup (WiFi or Ethernet)
- A TomTom API key (free — see [Getting your API key](#getting-your-api-key-and-calendar-url))
- Optionally: your Outlook/Microsoft 365 calendar ICS URL

---

## Step 1 — Flash and configure Raspberry Pi OS

### 1a. Download and flash

Use **Raspberry Pi Imager** (download at [raspberrypi.com/software](https://www.raspberrypi.com/software/)):

1. Click **Choose OS** → **Raspberry Pi OS (other)** → **Raspberry Pi OS Lite (32-bit)**
   - 32-bit works on all models including Pi 1 B+ and Zero W
   - 64-bit works on Pi 3, 4, 5, Zero 2 W — use it if your board supports it
   - The desktop version works too but uses more RAM

2. Click **Choose Storage** and select your SD card.

3. Click the **gear icon** (Advanced options) before writing:
   - **Set hostname** — e.g. `work-display`
   - **Enable SSH** — check "Use password authentication"
   - **Configure wireless LAN** — enter your WiFi SSID and password
   - **Set username and password** — use `pi` / your chosen password

4. Click **Write**. This takes a few minutes.

### 1b. First boot

Insert the SD card and power on. Wait ~60 seconds for first boot to complete.

Find the Pi's IP address from your router's device list, or from another terminal:
```bash
ping work-display.local
```

SSH in:
```bash
ssh pi@work-display.local
# or: ssh pi@<ip-address>
```

Run the system updater:
```bash
sudo apt update && sudo apt upgrade -y
```

---

## Step 2 — Enable the framebuffer display

Skip this section if you're using an HDMI monitor — it uses `/dev/fb0` automatically.

### SPI display setup

#### Install the display driver

Most SPI displays use a device tree overlay. The method depends on your display.

**Option A — Generic 2.4"/2.8" RPi Display (ILI9341 + XPT2046)** {#generic-24-28-rpi-display-ili9341}

These blue HAT-style boards labelled "2.4/2.8inch RPi Display, 320×240 Pixel, XPT2046 Touch Controller"
are common on Amazon and AliExpress. They use an **ILI9341** display controller and plug
directly onto the 40-pin GPIO header.

```bash
sudo nano /boot/config.txt
```

Add at the bottom:
```
dtoverlay=ili9341,speed=32000000,fps=25,bgr=0,rotate=90
```

> If the display shows at the wrong angle, try `rotate=270`. If colours look
> wrong (blue/red swapped), add `bgr=1`.

Reboot:
```bash
sudo reboot
```

After rebooting, the display should appear as `/dev/fb1`. Test it:
```bash
sudo cat /dev/urandom > /dev/fb1
```

If the screen fills with coloured static, the driver is working.

> **Pi 1 B+ note**: The `ili9341` overlay is included in the mainline kernel and
> works on ARMv6. No additional driver installation is required.

> **If `/dev/fb1` does not appear**: the kernel may not have the `fbtft` module
> built in. Try installing it:
> ```bash
> sudo apt install raspberrypi-kernel-headers
> sudo modprobe fbtft_device name=adafruit28 gpios=dc:24,reset:25 speed=32000000 fps=25
> ```
> Then add it to `/etc/modules` to load on boot:
> ```
> fbtft_device name=adafruit28 gpios=dc:24,reset:25 speed=32000000 fps=25
> ```

**In the setup UI**, set:
- Width: `320`, Height: `240`
- Framebuffer device: `/dev/fb1`

And scale `work_layout.json` for 320×240 — see [Resolution presets](#resolution-presets).

---

**Option B — Waveshare displays**

```bash
# Download and run the Waveshare installer
git clone https://github.com/waveshare/LCD-show.git
cd LCD-show
# Replace 'LCD35B-show' with your display's script:
#   LCD35A-show   → 3.5" Type A (480×320)
#   LCD35B-show   → 3.5" Type B (480×320)
#   LCD28-show    → 2.8" (320×240)
sudo ./LCD35B-show
# The Pi will reboot. SSH back in after.
```

**Option C — `/boot/config.txt` overlay (Adafruit / other generic)**

```bash
sudo nano /boot/config.txt
```

Add at the bottom (example for Adafruit PiTFT 3.5"):
```
dtoverlay=pitft35-resistive,rotate=90,speed=32000000,fps=20
```

For a generic ILI9486 display:
```
dtoverlay=ili9486,rotate=90,speed=16000000
```

Reboot after editing:
```bash
sudo reboot
```

#### Verify the framebuffer

After rebooting, confirm the framebuffer device exists:
```bash
ls -la /dev/fb*
```

Expected output:
```
crw-rw---- 1 root video 29, 0 May 15 09:00 /dev/fb0   ← HDMI
crw-rw---- 1 root video 29, 1 May 15 09:00 /dev/fb1   ← SPI display
```

Test it — this should fill the display with static noise:
```bash
sudo cat /dev/urandom > /dev/fb1
```

Press Ctrl+C. If the screen filled with coloured pixels, the display is working.

#### Framebuffer permissions

The service runs as `root` by default, so framebuffer access is automatic.
If you prefer to run as `pi`, add the user to the `video` group:
```bash
sudo usermod -aG video pi
# Log out and back in for the group to take effect
```

#### Disable the text console on the SPI display (optional)

By default, boot messages and a login prompt appear on the SPI display. To get
a clean black screen on startup:

```bash
sudo nano /boot/cmdline.txt
```

Remove `console=tty1` from the line (keep everything else on one line).

Disable the framebuffer console service:
```bash
sudo systemctl disable getty@tty1
```

---

## Step 3 — Install work-dashboard

### 3a. Install system packages

```bash
sudo apt install -y \
    git \
    python3-pip \
    python3-dev \
    fonts-freefont-ttf \
    libcairo2 \
    libcairo2-dev \
    libffi-dev \
    network-manager
```

> **Pi 1 B+ and Zero W**: `libcairo2-dev` and `cairosvg` (the SVG icon renderer)
> may fail to build on 32-bit ARMv6. Skip `libcairo2-dev` and install only the
> core packages. The app falls back to a built-in vector icon renderer
> automatically — no action needed.

Make sure NetworkManager is running (needed for WiFi management from the setup UI):
```bash
sudo systemctl enable NetworkManager
sudo systemctl start NetworkManager
```

> If you configured WiFi via `wpa_supplicant` and it's not using NetworkManager,
> see [WiFi without NetworkManager](#wifi-without-networkmanager) in the
> troubleshooting section.

### 3b. Clone the repository

```bash
cd /home/pi
git clone https://github.com/winsinor/work-pi.git
cd work-pi
```

### 3c. Install Python dependencies

**Pi 3, 4, 5, Zero 2 W (full install):**
```bash
pip3 install -r requirements.txt
```

**Pi 1 B+ and Zero W (without cairosvg):**
```bash
pip3 install requests Pillow icalendar recurring-ical-events gpiozero RPi.GPIO
```

If `pip3` is not found:
```bash
sudo apt install python3-pip
# or on newer Pi OS:
pip3 install --break-system-packages -r requirements.txt
```

### 3d. Verify the install

```bash
python3 -c "from PIL import Image; print('Pillow OK')"
python3 -c "import requests; print('requests OK')"
```

---

## Step 4 — First-boot setup

### 4a. Start the app

```bash
cd /home/pi/work-dashboard
python3 work_display.py
```

Since `config.json` doesn't exist yet, the display shows:

```
Open in browser:
http://192.168.1.x:8080
to configure this display
```

### 4b. Open the setup UI

From your phone or laptop on the same WiFi network, open:
```
http://<pi-ip-address>:8080
```

You'll see a setup form with eight tabs. Work through them:

---

### WiFi tab

If the Pi is already connected via Ethernet or the WiFi you entered in
Raspberry Pi Imager, you can skip this tab. Use it if you need to switch
networks or if the Pi came up without WiFi.

1. Click **Scan for networks** — nearby networks appear in a list.
2. Click your network name.
3. Enter the password if prompted.
4. Click **Connect**. Status updates to "Connected: YourNetwork".

> WiFi management uses `nmcli` (NetworkManager). The service runs as root
> so this works without any extra configuration.

---

### Location tab

Enter the latitude and longitude of your **work location** (used for weather,
AQI, and NWS alerts).

- Find your lat/lon at [latlong.net](https://www.latlong.net) — search for your city or office address.
- Select your timezone from the dropdown.

Example:
```
Latitude:  39.1031
Longitude: -84.512
Timezone:  America/New_York
```

---

### Addresses tab

These are full street addresses used to calculate commute times via TomTom routing.

| Field | What to enter |
|---|---|
| **Home address** *(required)* | Your home street address, e.g. `123 Main St, Cincinnati, OH 45201` |
| **Work address** *(required)* | Your work building's street address |
| **Waypoint address** *(optional)* | A stop on the way home — daycare, gym, grocery store. Adds a second commute route. |
| **Route labels** | How the two routes are labelled on-screen, e.g. `Work → Home` and `Work → Daycare → Home` |

Addresses are geocoded once at startup using TomTom. If you change an address,
restart the service.

---

### API Keys tab

| Field | Where to get it |
|---|---|
| **TomTom API key** *(required)* | Free account at [developer.tomtom.com](https://developer.tomtom.com). See [detailed steps](#getting-your-tomtom-api-key). |

Weather, AQI, and NWS alerts use free APIs with no key.

---

### Calendar tab

| Field | What to enter |
|---|---|
| **ICS / Webcal URL** | Your Outlook calendar's publish link. See [getting your calendar URL](#getting-your-outlook-calendar-url). Leave blank to disable. |
| **Update interval** | How often to re-fetch the calendar (default 10 min). |

The calendar is used for two things:
1. **Today's events** — shown on the Calendar page.
2. **All-day event scanning** — if today has an all-day event titled "WFH",
   "OOO", or "Holiday", the display switches to the corresponding mode.

---

### Keywords tab

The words the app looks for in all-day calendar event titles. Use the tag
inputs to add or remove keywords. Matching is case-insensitive and
checks if the keyword appears *anywhere* in the title.

| Mode | Default keywords | Result |
|---|---|---|
| WFH | `wfh`, `working from home` | Shows "Working From Home" full-screen |
| OOO | `ooo`, `out of office`, `pto` | Shows "Out of Office" + next return date |
| Holiday | `holiday` | Shows the holiday name full-screen |

---

### Display tab

| Setting | Default | Notes |
|---|---|---|
| Width / Height | 480 / 320 | Match your display's actual resolution |
| Framebuffer device | `/dev/fb1` | Run `ls /dev/fb*` on the Pi to check. HDMI = `/dev/fb0`. |
| Rotation | 0° | Set to 180° if the image appears upside-down |
| Page dwell | 8s | How long each page stays on screen before cycling |
| GPIO buttons | Enabled | Uncheck if no buttons are wired to the Pi |
| Shutdown GPIO | 23 | Hold 5s to shut down safely |
| Advance GPIO | 24 | Press to skip to the next page |
| Font path | `/usr/share/fonts/truetype/freefont/FreeSansBold.ttf` | Installed by `fonts-freefont-ttf` |

> **If your display is upside-down**: set Rotation to 180° and save. The change
> takes effect immediately without restarting.

---

### Intervals tab

How often each data source is polled. Adjust based on your needs and Pi's
performance.

| Source | Default | Minimum recommended |
|---|---|---|
| Weather | 10 min | 5 min |
| Commute | 5 min | 2 min |
| Calendar | 10 min | 5 min |
| AQI | 15 min | 10 min |
| Commute window start | 15:00 (3 PM) | — |
| Commute window end | 18:00 (6 PM) | — |

The commute page only appears during the configured window. Outside that
window the slot is simply not rendered.

---

### 4c. Save

Click **Save settings**. A green toast confirms the save. Once the three
required fields (home address, work address, TomTom key) are filled, the
display automatically restarts and begins showing data.

---

## Step 5 — Install as a service

Install the systemd service so the display starts on boot and restarts
automatically if it crashes.

### 5a. Edit the service file

```bash
nano /home/pi/work-dashboard/work-dashboard.service
```

Change the paths if your install location isn't `/home/pi/work-dashboard`:
```ini
WorkingDirectory=/home/pi/work-dashboard
ExecStart=/usr/bin/python3 /home/pi/work-dashboard/work_display.py
```

### 5b. Install and enable

```bash
sudo cp /home/pi/work-dashboard/work-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable work-dashboard
sudo systemctl start work-dashboard
```

### 5c. Check status

```bash
sudo systemctl status work-dashboard
```

You should see `Active: active (running)`. View the live log:
```bash
sudo journalctl -u work-dashboard -f
```

### 5d. Stop/restart

```bash
sudo systemctl restart work-dashboard
sudo systemctl stop work-dashboard
```

---

## Pages and what triggers them

### Normal mode (NORMAL)

All four pages cycle in order. Each page is shown for its configured dwell time.

| Page | Always shown? | Condition |
|---|---|---|
| **Clock** | Yes | Shows time, day name, date |
| **Calendar** | Yes | Next event today; "No upcoming events" if empty |
| **Forecast** | Yes | Current temp, condition, hi/lo, rain %, humidity, wind, 5-slot hourly grid, AQI |
| **Commute** | No | Weekdays only, within the configured time window |

### Special modes (from all-day calendar events)

When the ICS calendar contains an all-day event today whose title matches a
configured keyword, the display switches to a single full-screen page:

| Mode | Trigger | Display |
|---|---|---|
| **WFH** | Event title contains a WFH keyword | "Working From Home" |
| **OOO** | Event title contains an OOO keyword | "Out of Office" + return date |
| **Holiday** | Event title contains a holiday keyword | Holiday name |

The app scans the calendar every 10 minutes (or your configured interval).
State changes take effect on the next scan.

---

## Layout customisation

`work_layout.json` controls how each page is rendered. Changes are picked up
**live** — edit the file and the next frame render uses the new values. No
restart needed.

### Canvas size

```json
"canvas": {"width": 480, "height": 320}
```

Change this to match your display's resolution. Also update `display.width`
and `display.height` in the setup UI to match.

### Per-page line positions

Each page has named line slots. `x`/`y` are pixel coordinates (null = auto),
`h` is the font height in points.

```json
"line_positions": {
  "clock": [
    {"x": null, "y": 147, "h": 94},   ← time "10:42 AM"
    {"x": null, "y": 231, "h": 38},   ← day "Thursday"
    {"x": null, "y": 73,  "h": 38}    ← date "May 15, 2026"
  ]
}
```

### Icon placement

```json
"icon": {"radius": 107, "gap": 10, "x": 390, "y": 107}
```

`radius` controls the icon size (diameter = radius×2). `x`/`y` is the icon
centre. Applies to the Forecast page weather icon.

### AQI overlay

```json
"aqi": {"cx": 231, "y": 16, "label_size": 21, "value_size": 35}
```

`cx` is the horizontal centre of the AQI readout. `y` is the top edge.

### Hourly grid

```json
"grid": {"height": 87, "columns": 5, "label_size": 18, "temp_size": 24, "rain_size": 18}
```

`height` is how many pixels the grid occupies at the bottom of the Forecast
page. `columns` controls how many time slots are shown (up to 5).

### Per-page dwell time

```json
"pages": {
  "clock":    {"enabled": true, "dwell_seconds": 8},
  "forecast": {"enabled": true, "dwell_seconds": 10},
  "calendar": {"enabled": true, "dwell_seconds": 12},
  "commute":  {"enabled": true, "dwell_seconds": 10}
}
```

Set `"enabled": false` to permanently hide a page.

### Resolution presets

The default layout targets **480×320**. To adapt for other resolutions,
scale all `x`, `y`, `h`, `radius`, `cx`, and grid size values by the
appropriate factor:

| Resolution | Scale x | Scale y | Scale fonts | Common display |
|---|---|---|---|---|
| 320×240 | ×0.67 | ×0.75 | ×0.71 | Generic 2.4"/2.8" ILI9341 HAT |
| 480×320 | ×1.00 | ×1.00 | ×1.00 (default) | Waveshare 3.5" A/B/C, Adafruit PiTFT 3.5" |
| 640×480 | ×1.33 | ×1.50 | ×1.41 | — |
| 800×480 | ×1.67 | ×1.50 | ×1.58 | Pimoroni HyperPixel 4 |

**Example `work_layout.json` canvas for 320×240** (generic 2.4"/2.8" display):

```json
"canvas": {"width": 320, "height": 240}
```

Apply the ×0.67 / ×0.75 / ×0.71 scale to all `x`, `y`, and `h` values in
`line_positions`, and scale `radius`, `cx`, and grid sizes proportionally.

---

## Configuration reference

All settings live in `config.json` (gitignored, created by the setup UI).
See `config.example.json` for a fully annotated template.

### Required fields

| Field | Description |
|---|---|
| `addresses.home` | Home street address for TomTom routing |
| `addresses.work` | Work street address for TomTom routing |
| `api_keys.tomtom` | TomTom API key |

### All fields

| Key | Default | Description |
|---|---|---|
| `wifi.ssid` | `""` | WiFi network name |
| `wifi.password` | `""` | WiFi password |
| `location.lat` | `0.0` | Work location latitude |
| `location.lon` | `0.0` | Work location longitude |
| `location.timezone` | `America/New_York` | Timezone for weather API and time display |
| `addresses.home` | `""` | Home full street address |
| `addresses.work` | `""` | Work full street address |
| `addresses.waypoint` | `""` | Optional waypoint (adds second commute route) |
| `api_keys.tomtom` | `""` | TomTom API key |
| `display.width` | `480` | Display width in pixels |
| `display.height` | `320` | Display height in pixels |
| `display.framebuffer` | `/dev/fb1` | Framebuffer device path |
| `display.rotation` | `0` | Screen rotation: 0, 90, 180, or 270 |
| `display.page_dwell_s` | `8` | Default seconds per page |
| `buttons.enabled` | `true` | Set `false` if no GPIO buttons wired |
| `buttons.shutdown_gpio` | `23` | GPIO BCM pin for shutdown button |
| `buttons.advance_gpio` | `24` | GPIO BCM pin for next-page button |
| `buttons.shutdown_hold_s` | `5` | Hold duration to trigger shutdown |
| `buttons.pull_up` | `true` | Use internal pull-up resistor |
| `fonts.path` | `/usr/share/fonts/truetype/freefont/FreeSansBold.ttf` | Primary font (TTF) |
| `fonts.fallback_paths` | see example | Tried in order if primary not found |
| `calendar.ics_url` | `""` | Outlook ICS URL |
| `calendar.update_interval_s` | `600` | Calendar refresh interval |
| `weather.update_interval_s` | `600` | Weather refresh interval |
| `commute.update_interval_s` | `300` | Commute refresh interval |
| `commute.window_start_h` | `15` | Hour to start showing commute (24h) |
| `commute.window_end_h` | `18` | Hour to stop showing commute (24h) |
| `commute.weekdays_only` | `true` | Only show commute page on weekdays |
| `aqi.update_interval_s` | `900` | AQI refresh interval |
| `alerts.update_interval_s` | `600` | NWS alerts refresh interval |
| `route_labels` | `["Work → Home", "Work → Waypoint → Home"]` | Labels for the two commute routes |
| `wfh_keywords` | `["wfh", "working from home"]` | Calendar event keywords for WFH mode |
| `ooo_keywords` | `["ooo", "out of office", "pto"]` | Calendar event keywords for OOO mode |
| `holiday_keywords` | `["holiday"]` | Calendar event keywords for Holiday mode |
| `setup_port` | `8080` | Port for the web config UI |
| `display_cache_s` | `60` | How long to cache the assembled display before rebuilding |

---

## GPIO buttons

Two physical buttons can be wired to GPIO pins on the Pi.

### Default pins (BCM numbering)

| Button | BCM Pin | Physical Pin | Action |
|---|---|---|---|
| Advance | GPIO 24 | Pin 18 | Short press: next page |
| Shutdown | GPIO 23 | Pin 16 | Hold 5 s: graceful shutdown |

### Wiring

Wire each button between the GPIO pin and GND (pin 6, 9, 14, 20, 25, 30, 34, or 39).
The internal pull-up resistor is enabled by default — no external resistor needed.

```
GPIO 24 ──[ button ]── GND
GPIO 23 ──[ button ]── GND
```

### Changing the pins

Edit the **Display → GPIO buttons** section in the setup UI, or set
`buttons.shutdown_gpio` and `buttons.advance_gpio` in `config.json`.

### No buttons

Set `"buttons": {"enabled": false}` in `config.json` (or uncheck in setup UI)
to skip GPIO initialisation entirely. The display still cycles pages automatically.

### Shutdown sequence

When the shutdown button is held for 5 seconds:
1. Display turns dark red
2. "Safe to unplug" appears
3. Display turns white
4. `sudo shutdown -h now` is called

---

## Getting your API key and calendar URL

### Getting your TomTom API key

TomTom's free tier provides 2,500 daily geocoding requests and 2,500 daily
routing requests — more than enough for a desk display.

1. Go to [developer.tomtom.com](https://developer.tomtom.com) and click **Get a free API key**.
2. Create an account (email + password, no credit card needed).
3. On the Dashboard, your API key is shown under **My Keys**.
4. Copy the key and paste it into the setup UI **API Keys** tab.

### Getting your Outlook calendar URL

This gives the display read-only access to your calendar events.

**Microsoft 365 / Outlook on the web:**

1. Go to [outlook.office.com](https://outlook.office.com) and sign in.
2. Click the **Settings** gear (top-right) → **View all Outlook settings**.
3. Go to **Calendar** → **Shared calendars**.
4. Under **Publish a calendar**, select your calendar and set permissions to
   **Can view all details**.
5. Click **Publish**.
6. Copy the **ICS** link (not the HTML link).
7. Paste it into the setup UI **Calendar** tab.

**Outlook desktop app (Windows):**

1. Right-click your calendar → **Share** → **Publish This Calendar**.
2. Follow the prompts and copy the ICS link.

> The ICS URL is a direct link to your calendar — treat it like a password.
> Anyone with the URL can read your calendar. The setup UI stores it in
> `config.json` which is not shared or committed to git.

---

## Troubleshooting

### Display stays black

1. Check the framebuffer device: `ls /dev/fb*`
2. Confirm the correct device is set in the setup UI (usually `/dev/fb1` for SPI, `/dev/fb0` for HDMI).
3. Test directly: `sudo cat /dev/urandom > /dev/fb1` — the display should flash with noise.
4. Check the service log: `sudo journalctl -u work-dashboard -f`

### Image is upside down

Set **Rotation: 180°** in the Display tab of the setup UI.

### Image colours are wrong (blue/red swapped)

Some displays use BGR pixel order instead of RGB. This is a display driver
configuration issue, not something work-dashboard controls. Check your
display's overlay documentation for a `bgr` parameter:
```
dtoverlay=waveshare35b,rotate=90,bgr=1
```

### Setup UI not reachable

1. Make sure you're on the same WiFi network as the Pi.
2. Find the Pi's actual IP: `hostname -I` on the Pi.
3. Make sure no firewall is blocking port 8080.
4. Check the app is running: `sudo systemctl status work-dashboard`

### WiFi scan shows no networks / connect fails

The setup UI uses `nmcli` (NetworkManager). Check that NetworkManager is running:
```bash
sudo systemctl status NetworkManager
```

If it shows inactive, start it:
```bash
sudo systemctl enable --now NetworkManager
```

#### WiFi without NetworkManager

If your Pi uses `wpa_supplicant` (the default on older Pi OS images), you have
two options:

**Option A**: Switch to NetworkManager (recommended):
```bash
sudo apt install network-manager
sudo systemctl disable dhcpcd wpa_supplicant
sudo systemctl enable NetworkManager
sudo systemctl start NetworkManager
sudo nmcli device wifi connect "YourSSID" password "YourPassword"
```

**Option B**: Configure WiFi manually and disable the WiFi tab in the setup UI:
```bash
sudo nano /etc/wpa_supplicant/wpa_supplicant.conf
```
Add:
```
network={
    ssid="YourSSID"
    psk="YourPassword"
}
```
The setup UI's WiFi tab will show an error if `nmcli` isn't available, but
all other settings still work.

### `gpiozero` / `RPi.GPIO` errors

If the Pi can't find the GPIO library, or you see errors about `/dev/gpiomem`:

```bash
sudo apt install python3-gpiozero python3-rpi.gpio
sudo usermod -aG gpio pi
```

Or simply disable buttons in the setup UI if you don't need them.

### Python version errors

The app requires **Python 3.9+**.

Check your version:
```bash
python3 --version
```

Pi OS **Bullseye** ships Python 3.9 — this is fine. Pi OS **Bookworm** ships
Python 3.11 — also fine. If you are on an older image (Buster or earlier) that
ships Python 3.7 or 3.8, reflash with Bullseye or Bookworm.

> **Note**: the source code uses `X | Y` union type hint syntax, but all files
> include `from __future__ import annotations` which makes that syntax valid on
> Python 3.7+. No upgrade is needed just for type hints.

### Service crashes on startup

View the last 50 log lines:
```bash
sudo journalctl -u work-dashboard -n 50
```

Common causes:
- Missing `config.json` (first run — should show setup screen instead of crashing)
- Wrong framebuffer path — set `display.framebuffer` to the correct device
- `gpiozero` not installed — install it or set `buttons.enabled: false`
- TomTom geocoding failed — check your API key and internet connection

### Slow rendering on Pi 1 B+ / Zero W

The display should still be usable — rendering takes ~0.5–1 second per frame.
To speed things up:

1. Remove `cairosvg` from requirements and reinstall. The fallback icon renderer
   is simpler and faster.
2. Reduce the hourly grid columns in `work_layout.json`: `"grid": {"columns": 3}`
3. Disable the AQI overlay: remove the `aqi_overlay` key handler won't be called
   (no code change needed — just keep AQI update interval high so it stays `null`).

---

## Data sources

| Data | API | Auth | Notes |
|---|---|---|---|
| Weather | [Open-Meteo](https://open-meteo.com) | None | Free, no account needed |
| AQI | [Open-Meteo Air Quality](https://air-quality-api.open-meteo.com) | None | US AQI index, free |
| NWS Alerts | [api.weather.gov](https://api.weather.gov) | None | US only; no alerts shown outside US |
| Geocoding | [TomTom Search API](https://developer.tomtom.com/search-api/documentation/geocoding-service/geocode) | API key (free) | Used once at startup per address |
| Routing | [TomTom Routing API](https://developer.tomtom.com/routing-api/documentation/routing/calculate-route) | API key (free) | Real-time traffic included |
| Incidents | [TomTom Traffic Incidents](https://developer.tomtom.com/traffic-api/documentation/traffic-incidents/incident-details) | API key (free) | Identifies accident/road work cause |
| Calendar | Outlook ICS / iCalendar | None (URL) | Any `.ics` webcal URL works |

All weather, AQI, and alert data is fetched from free, no-account APIs.
The only external account required is TomTom (free tier, no credit card).
