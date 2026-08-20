#!/usr/bin/env bash
#
# Permanently stop this host from thrashing itself to death.
#
# Incident (2026-08-20): the desktop froze under memory pressure while the
# machine still had ~2 GB RAM and ~1.4 GB swap free. Load average hit 30.
# Nothing was OOM-killed, because nothing had reached the kill thresholds --
# the box was simply swapping to an NVMe file faster than it could make
# progress. A freeze from thrash is not an OOM event; it happens *before* one.
#
# Why the existing protection did not fire
# ----------------------------------------
# earlyoom was installed, enabled, and running the whole time:
#
#   earlyoom -r 60 -m 6 -s 6 --avoid ... --prefer ...
#
# `-m 6 -s 6` means "act when available memory < 6% AND free swap < 6%".
# With 30 GiB RAM that is under 1.9 GiB, and the machine becomes unusable long
# before it gets there. The thresholds were set for "prevent an OOM kill", not
# for "keep the desktop responsive". This raises them so earlyoom intervenes
# while the box is still recoverable.
#
# Three changes, in order of impact:
#
#   1. zram -- a compressed block device in RAM used as high-priority swap.
#      Swapping to compressed RAM is orders of magnitude faster than swapping
#      to /swap.img on NVMe, so the thrash spiral cannot form in the first
#      place. This is the single most effective fix; the disk swap file stays
#      as a lower-priority overflow tier.
#   2. earlyoom thresholds raised to -m 12 -s 12, so it acts with ~3.6 GiB
#      still available rather than ~1.9 GiB.
#   3. vm.swappiness lowered so the kernel prefers reclaiming page cache over
#      evicting anonymous pages, and vm.vfs_cache_pressure left default.
#
# Run with:  sudo bash scripts/host/harden-memory.sh
# Undo with: sudo bash scripts/host/harden-memory.sh --revert

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "This script must run as root:  sudo bash $0" >&2
    exit 1
fi

EARLYOOM_DEFAULTS=/etc/default/earlyoom
ZRAM_GENERATOR_CONF=/etc/systemd/zram-generator.conf
SYSCTL_CONF=/etc/sysctl.d/60-nova-memory.conf
BACKUP_SUFFIX=".pre-nova-harden"

if [ "${1:-}" = "--revert" ]; then
    echo "==> reverting"
    [ -f "${EARLYOOM_DEFAULTS}${BACKUP_SUFFIX}" ] && mv "${EARLYOOM_DEFAULTS}${BACKUP_SUFFIX}" "$EARLYOOM_DEFAULTS"
    rm -f "$ZRAM_GENERATOR_CONF" "$SYSCTL_CONF"
    systemctl daemon-reload
    systemctl restart earlyoom 2>/dev/null || true
    swapoff /dev/zram0 2>/dev/null || true
    echo "reverted. reboot to fully clear zram."
    exit 0
fi

# ── 1. zram ────────────────────────────────────────────────────────────────
echo "==> configuring zram"
if ! dpkg -s systemd-zram-generator >/dev/null 2>&1; then
    apt-get update -qq
    apt-get install -y systemd-zram-generator
fi

# Half of RAM, capped at 8G. zstd gets ~3:1 on typical anonymous pages, so 8G
# of zram behaves like roughly 24G of swap without touching the disk.
cat > "$ZRAM_GENERATOR_CONF" <<'CONF'
[zram0]
zram-size = min(ram / 2, 8192)
compression-algorithm = zstd
swap-priority = 100
CONF

systemctl daemon-reload
systemctl start /dev/zram0 2>/dev/null || systemctl restart systemd-zram-setup@zram0.service 2>/dev/null || true

# ── 2. earlyoom thresholds ─────────────────────────────────────────────────
echo "==> raising earlyoom thresholds"
[ -f "$EARLYOOM_DEFAULTS" ] && [ ! -f "${EARLYOOM_DEFAULTS}${BACKUP_SUFFIX}" ] \
    && cp "$EARLYOOM_DEFAULTS" "${EARLYOOM_DEFAULTS}${BACKUP_SUFFIX}"

# -m 12 -s 12 : act at 12% available RAM / 12% free swap (was 6/6).
# --avoid     : never kill the session, the container runtime, or k3s -- killing
#               those turns a slow desktop into a dead one.
# --prefer    : node/esbuild/tsc/next-server are the build-time balloons; they
#               are restartable and are what actually blows the box up.
cat > "$EARLYOOM_DEFAULTS" <<'CONF'
EARLYOOM_ARGS="-r 60 -m 12 -s 12 \
  --avoid '(^|/)(systemd|systemd-oomd|gnome-shell|Xorg|gdm|sshd|dockerd|containerd|k3s-server|pm2|postgres)$' \
  --prefer '(^|/)(next-server|node|esbuild|tsc|vitest|playwright|Runner.Worker|chrome|firefox)$'"
CONF

systemctl restart earlyoom

# ── 3. sysctl ──────────────────────────────────────────────────────────────
echo "==> tuning vm sysctls"
# With zram as the primary swap tier, swapping is cheap, so a *higher*
# swappiness is correct -- the usual "lower it" advice assumes disk swap.
# 180 is the value systemd/Fedora use for zram-backed systems.
cat > "$SYSCTL_CONF" <<'CONF'
vm.swappiness = 180
vm.watermark_boost_factor = 0
vm.watermark_scale_factor = 125
vm.page-cluster = 0
CONF
sysctl -p "$SYSCTL_CONF" >/dev/null

echo
echo "==> result"
swapon --show
echo
systemctl --no-pager -p ActiveState show earlyoom | head -1
echo "done. zram is now the primary swap tier; earlyoom acts at 12% instead of 6%."
