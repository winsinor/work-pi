#!/usr/bin/env bash
# auto-deploy.sh — pull from main and restart only when there are new commits
REPO="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
INSTALL_DIR="/home/pi/work-dashboard"
SERVICE="work-dashboard"

cd "$REPO"

# Fetch silently; bail out without error on network failure
git fetch origin main --quiet 2>/dev/null || {
    echo "[auto-deploy] fetch failed (network?) — skipping"
    exit 0
}

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
    exit 0   # nothing new — no log noise
fi

echo "[auto-deploy] $(date '+%Y-%m-%d %H:%M:%S') — new commits, deploying…"
git log --oneline "$LOCAL..$REMOTE"

git pull origin main
# Exclude work_layout.json: it's edited live via the layout editor in the install
# dir and must not be reverted to the repo copy on every upstream commit.
sudo rsync -a --exclude='.git' --exclude='config.json' --exclude='work_layout.json' --exclude='__pycache__' \
    "$REPO/" "$INSTALL_DIR/"
# Seed the layout only on first deploy — never overwrite the user's saved layout.
if [ ! -f "$INSTALL_DIR/work_layout.json" ]; then
    sudo cp "$REPO/work_layout.json" "$INSTALL_DIR/work_layout.json"
fi
sudo systemctl restart "$SERVICE"
echo "[auto-deploy] done"
