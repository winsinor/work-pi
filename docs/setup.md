# Setup Guide

## Hardware Bill of Materials

| Part | Notes |
|---|---|
| Raspberry Pi (any model) | Pi 4 recommended for primary device; Pi B+/Zero supported |
| 3.5" SPI display HAT (480×320) | Waveshare 3.5" Type A or compatible XPT2046 touch HAT |
| MicroSD card (8 GB+) | Class 10 / A1 recommended |
| Power supply | 5V 3A USB-C (Pi 4) or 5V 2.5A micro-USB (Pi B+/Zero) |

---

## Raspbian Version Matrix

> **Important:** Do NOT use Bookworm on a Pi B+ or Pi Zero. The ARMv6 CPU is
> no longer supported by Debian Bookworm's package feeds and you will hit
> missing `apt` packages and broken `git` behaviour.

| Pi model | CPU arch | Use this OS |
|---|---|---|
| Pi 4 / Pi 400 | ARMv8 (64-bit) | **Raspberry Pi OS Bookworm (64-bit)** |
| Pi 3 | ARMv8 (64-bit) | Raspberry Pi OS Bookworm (64-bit) |
| Pi 2 | ARMv7 | Raspberry Pi OS Bullseye (32-bit) |
| Pi B+ / Pi 1 / Zero / Zero W | ARMv6 | **Raspberry Pi OS Bullseye (32-bit)** — stop here, do not upgrade |
| Pi Zero 2 W | ARMv8 (64-bit) | Raspberry Pi OS Bookworm (64-bit) |

Download images: <https://www.raspberrypi.com/software/operating-systems/>

Flash with Raspberry Pi Imager. Enable SSH and set hostname/user in the
Imager's "Advanced options" before flashing.

---

## Display HAT Driver Setup

For a Waveshare 3.5" Type A HAT (or any SPI display using the ILI9486 /
XPT2046 chipset), add the following line to `/boot/config.txt`
(or `/boot/firmware/config.txt` on Bookworm):

```
dtoverlay=waveshare35a,rotate=90
```

Then reboot. The display will appear as `/dev/fb1`. Verify with:

```bash
cat /proc/fb
# should list: 1 RPi-Sense FB  (or similar)
ls /dev/fb*
```

Touch input will appear as `/dev/input/event0` (or similar). Check with:

```bash
evtest /dev/input/event0
```

---

## Step-by-Step Installation

### 1. Flash and boot

Flash Raspbian (see version matrix above), insert the SD card, and boot.
Connect via SSH once the Pi is on your network.

### 2. Clone the repository

```bash
cd ~
git clone https://github.com/winsinor/work-pi.git
cd work-pi
```

### 3. Run the install script

```bash
chmod +x install.sh
sudo ./install.sh
```

The script installs Python dependencies, sets up the systemd service, and
enables the display overlay. It will prompt before making changes.

### 4. Dependencies at a glance

```bash
# Always required
sudo apt install -y python3-pip fonts-freefont-ttf
pip3 install Pillow requests icalendar pytz

# SVG weather icons (optional — falls back to PIL-drawn icons if absent)
sudo apt install -y libcairo2-dev
pip3 install cairosvg

# Touch input (only if ENABLE_TOUCH = True in work_display.py)
pip3 install evdev

# GPIO buttons (only if buttons.enabled = true in config.json)
sudo apt install -y python3-gpiozero
```

### 5. Configure

Open a browser and navigate to `http://<pi-ip>:8080` to complete setup.
Required fields: home address, work address, TomTom API key.

Config is saved to `config.json` on the Pi. The display restarts automatically.

### 6. Enable at boot

If the install script did not enable the service:

```bash
sudo systemctl enable work-dashboard
sudo systemctl start work-dashboard
```

Check logs with:

```bash
journalctl -u work-dashboard -f
```

---

## Tailscale Setup (Recommended)

Some corporate and guest Wi-Fi networks block device-to-device LAN traffic,
which can prevent SSH access to the Pi after it connects to a new network.
Tailscale creates a private mesh VPN so you can always reach the Pi regardless
of the network it's on.

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Follow the authentication URL printed in the terminal. Once authenticated,
the Pi will have a stable `100.x.x.x` address visible in the stats overlay
(long-press center of the touch display).

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Display stays black | Overlay not loaded | Check `/boot/config.txt` for `dtoverlay=waveshare35a` line; reboot |
| `apt install` fails with "Package not found" | Wrong OS for Pi model | Re-flash with the correct Raspbian version (see matrix above) |
| `git clone` errors / slow | DNS or network issue | Try `ping 8.8.8.8`; check `/etc/resolv.conf` |
| Service crashes immediately | Missing config | Visit `http://<pi-ip>:8080` and complete setup |
| Touch not responding | Wrong device path | Set `TOUCH_DEVICE` in `work_display.py` to the correct `/dev/input/eventN` |
| `evdev` import error | Not installed | `pip3 install evdev` |
| Orange stripe at bottom of display | Data fetch failing | Check network / API key; run `journalctl -u work-dashboard -f` |
| Stats overlay shows wrong IP | Tailscale not running | `sudo tailscale up` |
| Font not found | FreeFont not installed | `sudo apt install -y fonts-freefont-ttf` |
