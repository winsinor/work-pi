#!/usr/bin/env bash
# auto-deploy.sh — pull from main and restart only when there are new commits
REPO="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
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
sudo systemctl restart "$SERVICE"
echo "[auto-deploy] done"
