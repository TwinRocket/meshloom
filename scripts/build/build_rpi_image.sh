#!/usr/bin/env bash
# Assemble a Raspberry Pi OS Lite 64-bit image with Meshloom preinstalled.
# Linux host (arm64 preferred so the chroot is native), kpartx (or losetup -P), xz.
# Not for PR CI.
#
# Usage:
#   scripts/build/build_rpi_image.sh --deb dist/meshloom_*_arm64.deb \
#     [--output-dir dist] [--version 4.12.0]
#
# MESHLOOM_COMMUNITY stays unset (product default: on for a new database).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RPI_DIR="$REPO_ROOT/pkg/rpi"

DEB=""
OUTPUT_DIR="$REPO_ROOT/dist"
IMAGE_URL="${MESHLOOM_RPI_BASE_URL:-}"
VERSION=""
WORKDIR=""
LOOP=""
ROOTMNT=""
KPARTX=0
GROW_BYTES=$((3 * 1024 * 1024 * 1024))

usage() {
    cat <<'EOF'
Usage: scripts/build/build_rpi_image.sh --deb PATH [--output-dir DIR] [--version X.Y.Z]

Build a bootable Raspberry Pi OS Lite (64-bit) image with Meshloom already
installed. Requires root on Linux and xz. Prefer an arm64 host so the
chroot does not need QEMU.

Do not bake Wi-Fi, passwords, or SSH keys into the image. Those go through
Raspberry Pi Imager 2.0.6+ (cloudinit-rpi) at flash time, using the generated
meshloom.rpi-imager-manifest (Use custom alone assumes init_format none).
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --deb) DEB="${2:-}"; shift 2 ;;
        --output-dir) OUTPUT_DIR="${2:-}"; shift 2 ;;
        --version) VERSION="${2:-}"; shift 2 ;;
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
        if [ "${KPARTX:-0}" = 1 ]; then
            kpartx -d "$LOOP" 2>/dev/null
        fi
        losetup -d "$LOOP" 2>/dev/null
    fi
    rm -rf "$WORKDIR"
}
trap cleanup EXIT

mkdir -p "$OUTPUT_DIR"
echo "[rpi] Disk before download:"
df -h "$OUTPUT_DIR" "$WORKDIR" || df -h
echo "[rpi] Downloading base image..."
curl -fL "$IMAGE_URL" -o "$WORKDIR/base.img.xz"
unxz -f "$WORKDIR/base.img.xz"
IMG="$WORKDIR/base.img"

echo "[rpi] Growing image by 3G so avahi + Meshloom fit..."
current_size="$(stat -c %s "$IMG")"
truncate -s "$((current_size + GROW_BYTES))" "$IMG"

LOOP="$(losetup -f --show -P "$IMG")"
sleep 1

resolve_parts() {
    BOOT=""
    ROOT=""
    local name part fstype
    name="$(basename "$LOOP")"
    for part in "${LOOP}p1" "${LOOP}p2" "/dev/mapper/${name}p1" "/dev/mapper/${name}p2"; do
        [ -b "$part" ] || continue
        fstype="$(blkid -o value -s TYPE "$part" || true)"
        case "$fstype" in
            vfat) BOOT="$part" ;;
            ext4) ROOT="$part" ;;
        esac
    done
}

resolve_parts
if [ -z "${BOOT:-}" ] || [ -z "${ROOT:-}" ]; then
    echo "[rpi] losetup -P did not publish partitions; trying kpartx -a"
    kpartx -avs "$LOOP"
    KPARTX=1
    sleep 1
    resolve_parts
fi
[ -n "${BOOT:-}" ] && [ -n "${ROOT:-}" ] || { echo "Could not find boot/root partitions on $LOOP" >&2; exit 1; }

if ! command -v growpart >/dev/null 2>&1; then
    echo "growpart is required (cloud-guest-utils) to enlarge the root filesystem." >&2
    exit 1
fi
echo "[rpi] Resizing root partition..."
growpart "$LOOP" 2
if [ "$KPARTX" = 1 ]; then
    kpartx -u "$LOOP" || true
    sleep 1
    resolve_parts
fi
resize2fs "$ROOT"

ROOTMNT="$WORKDIR/root"
BOOTMNT="$WORKDIR/boot"
mkdir -p "$ROOTMNT" "$BOOTMNT"
mount "$ROOT" "$ROOTMNT"
mount "$BOOT" "$BOOTMNT"
mkdir -p "$ROOTMNT/boot/firmware"
mount --bind "$BOOTMNT" "$ROOTMNT/boot/firmware"

echo "[rpi] Disk after mount:"
df -h "$ROOTMNT"

cp "$DEB" "$ROOTMNT/tmp/meshloom.deb"
install -D -m 0755 "$RPI_DIR/meshloom-console" "$ROOTMNT/usr/lib/meshloom/meshloom-console"
install -D -m 0644 "$RPI_DIR/meshloom-console.service" \
    "$ROOTMNT/usr/lib/systemd/system/meshloom-console.service"

# Apt source used by later UI upgrades. Fail the bake if the signing key is missing.
mkdir -p "$ROOTMNT/etc/apt/keyrings" "$ROOTMNT/etc/apt/sources.list.d"
curl -fsSL "https://twinrocket.github.io/meshloom/meshloom.gpg" \
    -o "$ROOTMNT/etc/apt/keyrings/meshloom.gpg"
echo "deb [signed-by=/etc/apt/keyrings/meshloom.gpg] https://twinrocket.github.io/meshloom/apt stable main" \
    >"$ROOTMNT/etc/apt/sources.list.d/meshloom.list"

# Do not set MESHLOOM_COMMUNITY — new databases join Community by default.
# Bots stay off via the packaged meshloom.env.

for fs in proc sys dev dev/pts; do
    mkdir -p "$ROOTMNT/$fs"
    mount --bind "/$fs" "$ROOTMNT/$fs"
done

chroot "$ROOTMNT" /bin/bash -s <<'CHROOT'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
echo "[rpi] Disk inside chroot before packages:"
df -h /
apt-get update -y
apt-get install -y --no-install-recommends avahi-daemon ca-certificates
dpkg -i /tmp/meshloom.deb || apt-get install -y -f
dpkg-query -W -f='${Status}\n' meshloom | grep -q 'ok installed'
rm -f /tmp/meshloom.deb
systemctl enable meshloom.service
systemctl enable meshloom-console.service
systemctl enable avahi-daemon.service
hostnamectl set-hostname meshloom 2>/dev/null || echo meshloom >/etc/hostname
echo "[rpi] Disk inside chroot after Meshloom:"
df -h /
CHROOT

if [ -f "$BOOTMNT/network-config" ]; then
    if ! grep -q "optional:" "$BOOTMNT/network-config"; then
        echo "[rpi] Leaving stock network-config (Imager will replace it)."
    fi
fi

umount "$ROOTMNT/boot/firmware"
umount "$ROOTMNT/proc" "$ROOTMNT/sys" "$ROOTMNT/dev/pts" "$ROOTMNT/dev"
umount "$ROOTMNT"
umount "$BOOTMNT"
if [ "$KPARTX" = 1 ]; then
    kpartx -d "$LOOP"
    KPARTX=0
fi
losetup -d "$LOOP"
LOOP=""
ROOTMNT=""

OUT_IMG="$OUTPUT_DIR/meshloom-rpi-lite-arm64.img"
cp "$IMG" "$OUT_IMG"
EXTRACT_SIZE="$(stat -c %s "$OUT_IMG")"
EXTRACT_SHA="$(sha256sum "$OUT_IMG" | awk '{print $1}')"
echo "[rpi] Compressing..."
xz -T0 -f "$OUT_IMG"
echo "[rpi] Wrote ${OUT_IMG}.xz"
ls -lh "${OUT_IMG}.xz"
echo "[rpi] Disk after compress:"
df -h "$OUTPUT_DIR"

if [ -n "$VERSION" ]; then
    VERSION="${VERSION#v}"
    DOWNLOAD_SIZE="$(stat -c %s "${OUT_IMG}.xz")"
    DOWNLOAD_SHA="$(sha256sum "${OUT_IMG}.xz" | awk '{print $1}')"
    RELEASE_DATE="$(date -u +%Y-%m-%d)"
    IMAGE_DOWNLOAD_URL="https://github.com/TwinRocket/meshloom/releases/download/${VERSION}/meshloom-rpi-lite-arm64.img.xz"
    MANIFEST="$OUTPUT_DIR/meshloom.rpi-imager-manifest"
    sed \
        -e "s#__IMAGE_URL__#${IMAGE_DOWNLOAD_URL}#g" \
        -e "s#__RELEASE_DATE__#${RELEASE_DATE}#g" \
        -e "s#__EXTRACT_SIZE__#${EXTRACT_SIZE}#g" \
        -e "s#__EXTRACT_SHA256__#${EXTRACT_SHA}#g" \
        -e "s#__DOWNLOAD_SIZE__#${DOWNLOAD_SIZE}#g" \
        -e "s#__DOWNLOAD_SHA256__#${DOWNLOAD_SHA}#g" \
        "$RPI_DIR/os-list.rpi-imager-manifest.tmpl" >"$MANIFEST"
    echo "[rpi] Wrote $MANIFEST"
fi
