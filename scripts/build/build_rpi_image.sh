#!/usr/bin/env bash
# Assemble a Raspberry Pi OS Lite 64-bit image with Meshloom preinstalled.
# Linux host with qemu-user-static, kpartx (or losetup -P), xz. Not for PR CI.
#
# Usage:
#   scripts/build/build_rpi_image.sh --deb dist/meshloom_*_arm64.deb [--output-dir dist]
#
# MESHLOOM_COMMUNITY stays unset (product default: on for a new database).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RPI_DIR="$REPO_ROOT/pkg/rpi"

DEB=""
OUTPUT_DIR="$REPO_ROOT/dist"
IMAGE_URL="${MESHLOOM_RPI_BASE_URL:-}"
WORKDIR=""

usage() {
    cat <<'EOF'
Usage: scripts/build/build_rpi_image.sh --deb PATH [--output-dir DIR]

Build a bootable Raspberry Pi OS Lite (64-bit) image with Meshloom already
installed. Requires root on Linux, qemu-user-static, and xz.

Do not bake Wi-Fi, passwords, or SSH keys into the image. Those go through
Raspberry Pi Imager 2.0.6+ (cloudinit-rpi) at flash time.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --deb) DEB="${2:-}"; shift 2 ;;
        --output-dir) OUTPUT_DIR="${2:-}"; shift 2 ;;
        --help) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
    esac
done

[ -n "$DEB" ] && [ -f "$DEB" ] || { echo "--deb must be an existing arm64 .deb" >&2; exit 1; }
[ "$(uname -s)" = "Linux" ] || { echo "This script must run on Linux." >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || { echo "Run as root (loop mounts and chroot)." >&2; exit 1; }

if [ -z "$IMAGE_URL" ]; then
    # Official Lite 64-bit latest index; pin by env for reproducible builds.
    IMAGE_URL="https://downloads.raspberrypi.com/raspios_lite_arm64_latest"
fi

WORKDIR="$(mktemp -d /tmp/meshloom-rpi.XXXXXX)"
cleanup() {
    set +e
    if [ -n "${ROOTMNT:-}" ]; then
        umount "$ROOTMNT/boot/firmware" 2>/dev/null
        umount "$ROOTMNT/proc" 2>/dev/null
        umount "$ROOTMNT/sys" 2>/dev/null
        umount "$ROOTMNT/dev/pts" 2>/dev/null
        umount "$ROOTMNT/dev" 2>/dev/null
        umount "$ROOTMNT" 2>/dev/null
    fi
    if [ -n "${LOOP:-}" ]; then
        kpartx -d "$LOOP" 2>/dev/null
        losetup -d "$LOOP" 2>/dev/null
    fi
    rm -rf "$WORKDIR"
}
trap cleanup EXIT

mkdir -p "$OUTPUT_DIR"
echo "[rpi] Downloading base image..."
curl -fL "$IMAGE_URL" -o "$WORKDIR/base.img.xz"
unxz -f "$WORKDIR/base.img.xz"
IMG="$WORKDIR/base.img"

LOOP="$(losetup -f --show -P "$IMG")"
BOOT=""
ROOT=""
for part in "${LOOP}p1" "${LOOP}p2"; do
    [ -b "$part" ] || continue
    fstype="$(blkid -o value -s TYPE "$part" || true)"
    case "$fstype" in
        vfat) BOOT="$part" ;;
        ext4) ROOT="$part" ;;
    esac
done
[ -n "$BOOT" ] && [ -n "$ROOT" ] || { echo "Could not find boot/root partitions on $LOOP" >&2; exit 1; }

ROOTMNT="$WORKDIR/root"
BOOTMNT="$WORKDIR/boot"
mkdir -p "$ROOTMNT" "$BOOTMNT"
mount "$ROOT" "$ROOTMNT"
mount "$BOOT" "$BOOTMNT"
mkdir -p "$ROOTMNT/boot/firmware"
mount --bind "$BOOTMNT" "$ROOTMNT/boot/firmware"

cp "$DEB" "$ROOTMNT/tmp/meshloom.deb"
install -D -m 0755 "$RPI_DIR/meshloom-console" "$ROOTMNT/usr/lib/meshloom/meshloom-console"
install -D -m 0644 "$RPI_DIR/meshloom-console.service" \
    "$ROOTMNT/usr/lib/systemd/system/meshloom-console.service"

# Apt source used by install.sh — so later UI upgrades hit the signed repo.
mkdir -p "$ROOTMNT/etc/apt/keyrings" "$ROOTMNT/etc/apt/sources.list.d"
if curl -fsSL "https://twinrocket.github.io/meshloom/meshloom.gpg" \
    -o "$ROOTMNT/etc/apt/keyrings/meshloom.gpg"; then
    echo "deb [signed-by=/etc/apt/keyrings/meshloom.gpg] https://twinrocket.github.io/meshloom/apt stable main" \
        >"$ROOTMNT/etc/apt/sources.list.d/meshloom.list"
else
    echo "deb [trusted=yes] https://twinrocket.github.io/meshloom/apt stable main" \
        >"$ROOTMNT/etc/apt/sources.list.d/meshloom.list"
fi

# Do not set MESHLOOM_COMMUNITY — new databases join Community by default.
# Bots stay off via the packaged meshloom.env.

for fs in proc sys dev dev/pts; do
    mkdir -p "$ROOTMNT/$fs"
    mount --bind "/$fs" "$ROOTMNT/$fs"
done

chroot "$ROOTMNT" /bin/bash -s <<'CHROOT'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -y || true
apt-get install -y --no-install-recommends avahi-daemon ca-certificates
dpkg -i /tmp/meshloom.deb || apt-get install -y -f
rm -f /tmp/meshloom.deb
systemctl enable meshloom.service
systemctl enable meshloom-console.service
systemctl enable avahi-daemon.service
hostnamectl set-hostname meshloom 2>/dev/null || echo meshloom >/etc/hostname
if [ -f /etc/cloud/cloud.cfg ]; then
    # Keep cloud-init so Imager 2.x can customise at flash time.
    true
fi
CHROOT

# Make first-boot networking optional if a stock netplan/cloud file exists.
# Imager overwrites these; this only prevents a hung boot with no cable.
if [ -f "$BOOTMNT/network-config" ]; then
    if ! grep -q "optional:" "$BOOTMNT/network-config"; then
        echo "[rpi] Leaving stock network-config (Imager will replace it)."
    fi
fi

umount "$ROOTMNT/boot/firmware"
umount "$ROOTMNT/proc" "$ROOTMNT/sys" "$ROOTMNT/dev/pts" "$ROOTMNT/dev"
umount "$ROOTMNT"
umount "$BOOTMNT"
kpartx -d "$LOOP" 2>/dev/null || true
losetup -d "$LOOP"
LOOP=""
ROOTMNT=""

OUT_IMG="$OUTPUT_DIR/meshloom-rpi-lite-arm64.img"
cp "$IMG" "$OUT_IMG"
echo "[rpi] Compressing..."
xz -T0 -f "$OUT_IMG"
echo "[rpi] Wrote ${OUT_IMG}.xz"
ls -lh "${OUT_IMG}.xz"
