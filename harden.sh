#!/usr/bin/env bash
#
# harden.sh — reduce SD-card writes and improve power-loss resilience on the
# Raspberry Pi running the work-pi dashboard.
#
# The dashboard itself is already nearly read-only at runtime (frames go to
# /dev/fb1, all data is cached in RAM, config/layout are written only on user
# action and atomically). So this script targets the OS-level write sources:
# swap, system logging, the apt/man-db background timers, and atime updates —
# plus it reclaims GPU RAM so going swap-light is safe.
#
# Properties:
#   • Idempotent — safe to re-run.
#   • Reversible — every system file it edits is backed up to <file>.harden.bak,
#     and changes are applied as drop-ins / disables rather than destructive edits
#     where possible. A reversal cheat-sheet is printed at the end.
#   • Conservative — it does NOT make the card read-only (see CLAUDE.md for that),
#     does NOT touch config.json / work_layout.json / the app, and never reboots.
#
# Usage:
#   sudo ./harden.sh             apply changes
#   ./harden.sh --dry-run        show what would change (no root needed)
#
set -u

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

# ── tunables ──────────────────────────────────────────────────────────────────
ZRAM_PERCENT=50          # zram size as % of RAM (compressed, lives in RAM)
JOURNAL_MAX=16M          # cap on volatile (RAM) journald usage
TMP_SIZE=128M            # /tmp tmpfs cap (only consumes RAM as used)
GPU_MEM=16               # MB reserved for GPU; no HDMI here, so the minimum

# ── pretty output ───────────────────────────────────────────────────────────
c_ok=$'\033[32m'; c_warn=$'\033[33m'; c_info=$'\033[36m'; c_off=$'\033[0m'
APPLIED=(); SKIPPED=(); WARNED=(); REBOOT=0
log()  { printf "%s==>%s %s\n" "$c_info" "$c_off" "$*"; }
ok()   { printf "  %s✓%s %s\n" "$c_ok"  "$c_off" "$*"; APPLIED+=("$*"); }
skip() { printf "  %s•%s %s\n" "$c_info" "$c_off" "$*"; SKIPPED+=("$*"); }
warn() { printf "  %s!%s %s\n" "$c_warn" "$c_off" "$*"; WARNED+=("$*"); }

# Run a command, or just print it under --dry-run.
run() {
    if [ "$DRY_RUN" = 1 ]; then printf "  [dry-run] %s\n" "$*"; return 0; fi
    "$@"
}

# Back up a file once (the first time we touch it).
backup_once() {
    local f="$1"
    [ -f "$f" ] || return 0
    [ -f "$f.harden.bak" ] && return 0
    if [ "$DRY_RUN" = 1 ]; then printf "  [dry-run] backup %s -> %s.harden.bak\n" "$f" "$f"; return 0; fi
    cp -a "$f" "$f.harden.bak"
}

# Write file contents atomically (used for drop-ins).
write_file() {
    local path="$1" content="$2"
    if [ "$DRY_RUN" = 1 ]; then
        printf "  [dry-run] write %s:\n" "$path"
        printf '%s\n' "$content" | sed 's/^/      | /'
        return 0
    fi
    mkdir -p "$(dirname "$path")"
    printf '%s\n' "$content" > "$path"
}

if [ "$DRY_RUN" != 1 ] && [ "$(id -u)" -ne 0 ]; then
    echo "This applies system changes — run with sudo:  sudo ./harden.sh"
    echo "(or preview with:  ./harden.sh --dry-run)"
    exit 1
fi

echo
log "work-pi SD-card hardening${DRY_RUN:+ (dry-run)}"
[ "$DRY_RUN" = 1 ] && echo "    (dry-run: nothing will be changed)"
echo

# ── 1. swap → zram (compressed RAM swap, zero SD writes) ───────────────────────
log "Swap → zram"
if command -v dphys-swapfile >/dev/null 2>&1 && systemctl is-enabled dphys-swapfile >/dev/null 2>&1; then
    run dphys-swapfile swapoff
    run systemctl disable --now dphys-swapfile
    run rm -f /var/swap
    ok "disabled dphys-swapfile (on-card swap) and removed /var/swap"
else
    skip "on-card swap (dphys-swapfile) already disabled"
fi
if dpkg -s zram-tools >/dev/null 2>&1; then
    skip "zram-tools already installed"
else
    if run apt-get install -y zram-tools; then
        ok "installed zram-tools"
    else
        warn "could not install zram-tools (no network?). Re-run when online, or zram won't be active."
    fi
fi
if [ -f /etc/default/zramswap ] || [ "$DRY_RUN" = 1 ]; then
    backup_once /etc/default/zramswap
    write_file /etc/default/zramswap "# Managed by harden.sh
ALGO=lz4
PERCENT=${ZRAM_PERCENT}"
    run systemctl restart zramswap 2>/dev/null || true
    ok "configured zram (lz4, ${ZRAM_PERCENT}% of RAM)"
fi
echo

# ── 2. logging to RAM (journald volatile + drop rsyslog) ───────────────────────
log "Logging off the card"
write_file /etc/systemd/journald.conf.d/harden.conf "# Managed by harden.sh — keep the journal in RAM, capped.
[Journal]
Storage=volatile
RuntimeMaxUse=${JOURNAL_MAX}"
run systemctl restart systemd-journald 2>/dev/null || true
ok "journald set to volatile (RAM), capped at ${JOURNAL_MAX}"
if dpkg -s rsyslog >/dev/null 2>&1; then
    run systemctl disable --now rsyslog
    ok "disabled rsyslog (redundant with journald; was writing /var/log/syslog)"
else
    skip "rsyslog not installed"
fi
echo

# ── 3. mask write-heavy background timers ──────────────────────────────────────
log "Background timers (periodic SD writes + wakeups)"
for unit in apt-daily.timer apt-daily-upgrade.timer man-db.timer; do
    if systemctl list-unit-files "$unit" >/dev/null 2>&1 && \
       [ "$(systemctl is-enabled "$unit" 2>/dev/null)" != "masked" ]; then
        run systemctl mask --now "$unit"
        ok "masked $unit"
    else
        skip "$unit already masked / absent"
    fi
done
echo

# ── 4. reclaim GPU RAM (no HDMI display attached) ──────────────────────────────
log "GPU memory"
CONFIG_TXT=/boot/config.txt
[ -f /boot/firmware/config.txt ] && CONFIG_TXT=/boot/firmware/config.txt
if [ -f "$CONFIG_TXT" ] || [ "$DRY_RUN" = 1 ]; then
    if grep -qE "^\s*gpu_mem=${GPU_MEM}\s*$" "$CONFIG_TXT" 2>/dev/null; then
        skip "gpu_mem already ${GPU_MEM}"
    else
        backup_once "$CONFIG_TXT"
        if grep -qE "^\s*gpu_mem=" "$CONFIG_TXT" 2>/dev/null; then
            run sed -i -E "s/^\s*gpu_mem=.*/gpu_mem=${GPU_MEM}/" "$CONFIG_TXT"
        else
            run sh -c "printf '\n# Managed by harden.sh\ngpu_mem=%s\n' '${GPU_MEM}' >> '$CONFIG_TXT'"
        fi
        ok "set gpu_mem=${GPU_MEM} in $CONFIG_TXT (frees ~48MB to the system)"
        REBOOT=1
    fi
else
    warn "no config.txt found — skipped gpu_mem"
fi
echo

# ── 5. fstab: noatime on root + /tmp in RAM ────────────────────────────────────
log "Filesystem mount options"
FSTAB=/etc/fstab
if [ -f "$FSTAB" ]; then
    # 5a. noatime on the root filesystem (stops a write on every file read)
    root_opts=$(awk '$1!~/^#/ && $2=="/"{print $4; exit}' "$FSTAB")
    if [ -z "$root_opts" ]; then
        warn "could not find the root (/) entry in $FSTAB — skipped noatime"
    elif printf '%s' "$root_opts" | grep -q "noatime"; then
        skip "root already mounted noatime"
    else
        backup_once "$FSTAB"
        if [ "$DRY_RUN" = 1 ]; then
            printf "  [dry-run] add noatime to root options (%s -> %s,noatime)\n" "$root_opts" "$root_opts"
        else
            tmp=$(mktemp)
            awk '$1!~/^#/ && $2=="/" && $4!~/noatime/{$4=$4",noatime"} {print}' OFS='\t' "$FSTAB" > "$tmp"
            # Sanity-check: root entry must still be present before we commit.
            if awk '$1!~/^#/ && $2=="/"{f=1} END{exit !f}' "$tmp"; then
                cat "$tmp" > "$FSTAB"; rm -f "$tmp"
                ok "added noatime to root in $FSTAB"
            else
                rm -f "$tmp"
                warn "fstab rewrite looked unsafe — left $FSTAB untouched (backup at $FSTAB.harden.bak)"
            fi
        fi
    fi

    # 5b. /tmp on tmpfs (RAM). The app doesn't use /tmp at runtime; this only
    #     matters for big apt/pip installs — set TMPDIR to a disk path for those.
    if awk '$1!~/^#/ && $2=="/tmp"{f=1} END{exit !f}' "$FSTAB"; then
        skip "/tmp already has an fstab entry"
    else
        backup_once "$FSTAB"
        run sh -c "printf 'tmpfs\t/tmp\ttmpfs\tdefaults,noatime,nosuid,nodev,size=%s\t0\t0\n' '${TMP_SIZE}' >> '$FSTAB'"
        ok "/tmp → tmpfs (size=${TMP_SIZE}); takes effect on reboot"
        REBOOT=1
    fi

    if [ "$DRY_RUN" != 1 ] && command -v findmnt >/dev/null 2>&1; then
        findmnt --verify --fstab >/dev/null 2>&1 \
            && ok "fstab passed findmnt --verify" \
            || warn "findmnt --verify reported issues — review $FSTAB before rebooting (backup: $FSTAB.harden.bak)"
    fi
else
    warn "$FSTAB not found — skipped mount-option changes"
fi
echo

# ── summary ────────────────────────────────────────────────────────────────────
log "Summary"
printf "  applied: %d   skipped: %d   warnings: %d\n" "${#APPLIED[@]}" "${#SKIPPED[@]}" "${#WARNED[@]}"
if [ "${#WARNED[@]}" -gt 0 ]; then
    echo
    echo "  Warnings:"
    for w in "${WARNED[@]}"; do echo "    - $w"; done
fi
echo
echo "  NOT done by this script (deliberately):"
echo "    - Read-only SD / overlay root  (separate, riskier — see CLAUDE.md)"
echo "    - fake-hwclock writes           (need a DS3231 RTC to remove; also fixes boot clock)"
echo "    - auto-deploy git-fetch churn   (lengthen auto-deploy.timer or disable it)"
echo
echo "  To reverse: restore any *.harden.bak files, delete"
echo "    /etc/systemd/journald.conf.d/harden.conf, 'systemctl unmask' the timers,"
echo "    re-enable dphys-swapfile/rsyslog, then reboot."
echo
if [ "$REBOOT" = 1 ] && [ "$DRY_RUN" != 1 ]; then
    printf "%s  Reboot required%s to apply gpu_mem and /tmp tmpfs:  sudo reboot\n" "$c_warn" "$c_off"
elif [ "$DRY_RUN" = 1 ]; then
    echo "  (dry-run complete — re-run with sudo to apply)"
else
    echo "  Done. A reboot is recommended to apply everything cleanly."
fi
echo