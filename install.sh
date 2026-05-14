#!/usr/bin/env bash
# install.sh — work-dashboard installer
# Run as root:  sudo bash install.sh
set -euo pipefail

INSTALL_DIR="/home/pi/work-dashboard"
SERVICE_NAME="work-dashboard"
SERVICE_FILE="work-dashboard.service"
PYTHON="python3"

# ── helpers ────────────────────────────────────────────────────────────────────
red()   { echo -e "\033[0;31m$*\033[0m"; }
green() { echo -e "\033[0;32m$*\033[0m"; }
info()  { echo -e "\033[0;36m$*\033[0m"; }

die() { red "ERROR: $*"; exit 1; }

require_root() {
    [[ "$(id -u)" -eq 0 ]] || die "Please run as root:  sudo bash install.sh"
}

# ── system checks ──────────────────────────────────────────────────────────────
require_root

info "==> Checking Python version …"
PY_VER=$($PYTHON -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>/dev/null) \
    || die "python3 not found — install it first:  sudo apt install python3"
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
[[ "$PY_MAJOR" -ge 3 && "$PY_MINOR" -ge 9 ]] \
    || die "Python 3.9+ required (found $PY_VER)"
green "    Python $PY_VER — OK"

# ── system packages ────────────────────────────────────────────────────────────
info "==> Installing system packages …"
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3-pip \
    python3-pil \
    libatlas-base-dev \
    libopenjp2-7 \
    libtiff5 \
    fonts-freefont-ttf \
    fonts-dejavu-core \
    network-manager \
    git

# cairosvg system dep (for weather icons — optional; degrades gracefully without)
apt-get install -y --no-install-recommends \
    libcairo2 libcairo2-dev libgdk-pixbuf2.0-0 libffi-dev \
    || info "    cairosvg native libs not available — SVG icons will be skipped"

# ── NetworkManager: ensure it manages the WiFi interface ──────────────────────
info "==> Ensuring NetworkManager is active …"
systemctl enable NetworkManager 2>/dev/null || true
systemctl start  NetworkManager 2>/dev/null || true

# ── Python dependencies ────────────────────────────────────────────────────────
info "==> Installing Python packages …"

# Detect if we're on a low-memory Pi (1B+/Zero W ≈ ≤512 MB RAM)
TOTAL_KB=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
if [[ "$TOTAL_KB" -le 524288 ]]; then
    info "    Low-memory device detected — using pre-built wheels where possible"
    PIP_EXTRA="--no-build-isolation"
else
    PIP_EXTRA=""
fi

# shellcheck disable=SC2086
$PYTHON -m pip install --upgrade pip --quiet
$PYTHON -m pip install $PIP_EXTRA \
    requests \
    icalendar \
    "recurring-ical-events>=2.0" \
    gpiozero \
    RPi.GPIO \
    --quiet || true

# cairosvg is optional — SVG icon rendering; skip on low-mem devices
if [[ "$TOTAL_KB" -gt 524288 ]]; then
    $PYTHON -m pip install cairosvg --quiet \
        || info "    cairosvg install failed — weather icons will render without SVG"
fi

# ── copy files ─────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

info "==> Installing to $INSTALL_DIR …"
mkdir -p "$INSTALL_DIR"

rsync -a --exclude='.git' --exclude='config.json' --exclude='__pycache__' \
    "$SCRIPT_DIR/" "$INSTALL_DIR/" \
    2>/dev/null || {
    cp -r "$SCRIPT_DIR"/. "$INSTALL_DIR/"
    rm -rf "$INSTALL_DIR/.git" "$INSTALL_DIR/__pycache__"
    [[ -f "$SCRIPT_DIR/config.json" ]] || rm -f "$INSTALL_DIR/config.json"
}

chmod +x "$INSTALL_DIR/work_display.py" 2>/dev/null || true

# ── systemd service ────────────────────────────────────────────────────────────
info "==> Installing systemd service …"
SERVICE_SRC="$INSTALL_DIR/$SERVICE_FILE"

[[ -f "$SERVICE_SRC" ]] || die "Service file not found at $SERVICE_SRC"

sed "s|/home/pi/work-dashboard|$INSTALL_DIR|g" "$SERVICE_SRC" \
    > "/etc/systemd/system/${SERVICE_NAME}.service"

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"

# ── done ───────────────────────────────────────────────────────────────────────
echo ""
green "==> Installation complete!"
echo ""
echo "  Install directory : $INSTALL_DIR"
echo "  Service           : $SERVICE_NAME"
echo ""

if [[ -f "$INSTALL_DIR/config.json" ]]; then
    green "  Existing config.json found — skipped."
    echo "  To reconfigure, open  http://<pi-ip>:8080/setup  in a browser."
    echo "  Then restart the service:  sudo systemctl restart $SERVICE_NAME"
else
    info "  No config.json found — first-boot setup will run automatically."
    echo "  Start the service now:  sudo systemctl start $SERVICE_NAME"
    echo "  Then open             :  http://<pi-ip>:8080/setup"
fi
echo ""
echo "  View logs:  journalctl -u $SERVICE_NAME -f"
echo ""
