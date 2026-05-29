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
    fonts-freefont-ttf \
    fonts-dejavu-core \
    network-manager \
    librsvg2-bin \
    git

# libtiff: Bullseye ships libtiff5, Bookworm ships libtiff6
apt-get install -y --no-install-recommends libtiff6 2>/dev/null \
    || apt-get install -y --no-install-recommends libtiff5 2>/dev/null \
    || info "    libtiff not found — Pillow may still work via its own bundled libs"

# numpy — optional; used for ~5-10x faster RGB565 conversion on ARMv6.
# Skip on very low memory systems (Pi Zero etc.) if install fails.
apt-get install -y --no-install-recommends python3-numpy 2>/dev/null \
    || info "    python3-numpy not available — RGB565 conversion will use pure Python fallback"

# cairosvg system dep (for weather icons — optional; degrades gracefully without)
apt-get install -y --no-install-recommends \
    libcairo2 libcairo2-dev libgdk-pixbuf2.0-0 libffi-dev \
    || info "    cairosvg native libs not available — SVG icons will be skipped"

# ── NetworkManager: install but don't break existing WiFi ─────────────────────
info "==> Configuring NetworkManager …"

# Disable the wait-online service — it blocks boot if WiFi credentials are
# not yet configured in NM (common on fresh installs over wpa_supplicant).
systemctl disable NetworkManager-wait-online.service 2>/dev/null || true

# If wpa_supplicant is managing WiFi already, tell NM not to fight it.
# The setup UI can take over later once credentials are entered there.
if systemctl is-active --quiet wpa_supplicant; then
    info "    wpa_supplicant is active — configuring NM to leave WiFi alone for now"
    mkdir -p /etc/NetworkManager/conf.d
    cat > /etc/NetworkManager/conf.d/99-unmanaged-wifi.conf <<'EOF'
[keyfile]
unmanaged-devices=interface-name:wlan0
EOF
fi

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

# Wait for DNS to be ready (network may still be coming up after NetworkManager start)
info "    Waiting for network …"
for i in 1 2 3 4 5; do
    ping -c1 -W2 pypi.org &>/dev/null && break
    [[ "$i" -eq 5 ]] && die "No network after 10s — check WiFi and try again"
    sleep 2
done

# shellcheck disable=SC2086
$PYTHON -m pip install --upgrade pip --quiet

# Helper: try pip install normally, fall back to HTTP index on SSL failure
pip_install() {
    local pkg="$1"
    $PYTHON -m pip install $PIP_EXTRA "$pkg" --quiet && return 0
    info "    Retrying $pkg via HTTP (piwheels SSL issue) …"
    $PYTHON -m pip install $PIP_EXTRA "$pkg" --quiet \
        --index-url http://pypi.org/simple/ \
        --trusted-host pypi.org \
        --trusted-host files.pythonhosted.org \
        && return 0
    red "    FAILED to install $pkg — run:  pip3 install \"$pkg\""
}

# icalendar>=6.1.0 required by recurring-ical-events 3.x
# (Raspberry Pi OS ships icalendar 4.x via apt — too old)
pip_packages=(requests "icalendar>=6.1.0" "recurring-ical-events>=2.0" gpiozero RPi.GPIO)
for pkg in "${pip_packages[@]}"; do
    pip_install "$pkg"
done

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

# ── pre-convert SVG icons to PNG ───────────────────────────────────────────────
info "==> Converting weather icons to PNG …"
if command -v rsvg-convert &>/dev/null; then
    for svg in "$INSTALL_DIR/icons/"*.svg; do
        png="${svg%.svg}.png"
        [[ -f "$png" ]] && continue
        rsvg-convert -w 200 -h 200 "$svg" -o "$png" 2>/dev/null \
            && info "    $(basename $png)" \
            || info "    skipped $(basename $svg)"
    done
else
    info "    rsvg-convert not found — install librsvg2-bin to enable SVG icons"
fi

# ── systemd service ────────────────────────────────────────────────────────────
info "==> Installing systemd service …"
SERVICE_SRC="$INSTALL_DIR/$SERVICE_FILE"

[[ -f "$SERVICE_SRC" ]] || die "Service file not found at $SERVICE_SRC"

sed "s|/home/pi/work-dashboard|$INSTALL_DIR|g" "$SERVICE_SRC" \
    > "/etc/systemd/system/${SERVICE_NAME}.service"

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"

# ── deploy shortcut ───────────────────────────────────────────────────────────
info "==> Installing 'deploy' command …"
ln -sf "$SCRIPT_DIR/deploy" /usr/local/bin/deploy
chmod +x "$SCRIPT_DIR/deploy"
green "    'deploy' available system-wide"

# ── auto-deploy timer (pull from GitHub every 2 min, restart only on changes) ─
info "==> Installing auto-deploy timer …"
chmod +x "$SCRIPT_DIR/auto-deploy.sh"

# Sudoers rule so the pi user can restart the service non-interactively
SUDOERS_LINE="pi ALL=(ALL) NOPASSWD: /bin/systemctl restart ${SERVICE_NAME}, /bin/systemctl restart ${SERVICE_NAME}.service, /usr/bin/rsync"
echo "$SUDOERS_LINE" > /etc/sudoers.d/work-dashboard-deploy
chmod 440 /etc/sudoers.d/work-dashboard-deploy

# Install unit files with the repo path substituted in
sed "s|__REPO__|$SCRIPT_DIR|g" "$SCRIPT_DIR/auto-deploy.service" \
    > /etc/systemd/system/auto-deploy.service
cp "$SCRIPT_DIR/auto-deploy.timer" /etc/systemd/system/auto-deploy.timer

systemctl daemon-reload
systemctl enable --now auto-deploy.timer
green "    auto-deploy.timer active — pulls every 2 min, restarts only on new commits"

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
