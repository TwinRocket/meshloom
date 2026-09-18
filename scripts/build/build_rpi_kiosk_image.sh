#!/usr/bin/env bash
# Assemble a Raspberry Pi OS Desktop 64-bit image with Meshloom and a
# Chromium kiosk on http://127.0.0.1:8000.
# Linux host (arm64 preferred so the chroot is native), kpartx (or losetup -P), xz.
# Manual / on demand. Not for PR CI — upload the result with:
#   gh release upload vX.Y.Z dist/meshloom-rpi-kiosk-arm64.img.xz \
#     dist/meshloom-kiosk.rpi-imager-manifest --clobber
#
# Usage:
#   scripts/build/build_rpi_kiosk_image.sh --deb dist/meshloom_*_arm64.deb \
#     [--output-dir dist] [--version 4.12.0]
#
# MESHLOOM_COMMUNITY stays unset (product default: on for a new database).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RPI_DIR="$REPO_ROOT/pkg/rpi"

DEB=""
OUTPUT_DIR="$REPO_ROOT/dist"
# Dated Desktop 64-bit (2026-06-18). Override with MESHLOOM_RPI_BASE_URL /
# MESHLOOM_RPI_BASE_SHA256 for a newer official image.
DEFAULT_IMAGE_URL="https://downloads.raspberrypi.com/raspios_arm64/images/raspios_arm64-2026-06-19/2026-06-18-raspios-trixie-arm64.img.xz"
DEFAULT_IMAGE_SHA256="123287c05f27b0eebd8f65456f6369b8f6635fa50a3d440a4f9f6223bf58c8e2"
MIN_FREE_KB=$((24 * 1024 * 1024))
IMAGE_URL="${MESHLOOM_RPI_BASE_URL:-$DEFAULT_IMAGE_URL}"
IMAGE_SHA256="${MESHLOOM_RPI_BASE_SHA256:-}"
VERSION=""
WORKDIR=""
LOOP=""
ROOTMNT=""
BOOTMNT=""
KPARTX=0
GROW_BYTES=$((3 * 1024 * 1024 * 1024))
TWO_GIB=$((2 * 1024 * 1024 * 1024))

usage() {
    cat <<'EOF'
Usage: scripts/build/build_rpi_kiosk_image.sh --deb PATH [--output-dir DIR] [--version X.Y.Z]

Build a bootable Raspberry Pi OS Desktop (64-bit) image with Meshloom already
installed and Chromium opening http://127.0.0.1:8000 in kiosk mode. Requires
root on Linux and xz. Prefer an arm64 host so the chroot does not need QEMU.
Needs about 25–30G free. Not wired to CI.

Do not bake Wi-Fi, passwords, or SSH keys into the image. Those go through
Raspberry Pi Imager 2.0.6+ (cloudinit-rpi) at flash time, using the generated
meshloom-kiosk.rpi-imager-manifest (Use custom alone assumes init_format none).

Do not enable meshloom-console: tty1 belongs to the graphical session.
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

if [ -z "$IMAGE_SHA256" ] && [ "$IMAGE_URL" = "$DEFAULT_IMAGE_URL" ]; then
    IMAGE_SHA256="$DEFAULT_IMAGE_SHA256"
fi

free_kb() {
    df -Pk "$1" 2>/dev/null | awk 'NR==2 {print $4}'
}

require_disk() {
    local dest="$1" label="$2" have
    have="$(free_kb "$dest")"
    if [ -z "$have" ]; then
        echo "Could not measure free disk on $label" >&2
        exit 1
    fi
    if [ "$have" -ge "$MIN_FREE_KB" ]; then
        echo "[rpi-kiosk] Free on $label: ${have}K"
        return 0
    fi
    echo "Not enough disk on $label: ${have}K free, need ${MIN_FREE_KB}K" >&2
    exit 1
}

write_meshloom_apt_source() {
    local dest_root="$1"
    local pages="https://twinrocket.github.io/meshloom"
    local key="$dest_root/etc/apt/keyrings/meshloom.gpg"
    local list="$dest_root/etc/apt/sources.list.d/meshloom.list"
    mkdir -p "$dest_root/etc/apt/keyrings" "$dest_root/etc/apt/sources.list.d"
    # Never fetch the key from Pages during bake. That URL 404s until the
    # linux-repo job has run, and a GITHUB_TOKEN release does not start it.
    if [ -n "${MESHLOOM_GPG_KEY:-}" ] && [ -f "$MESHLOOM_GPG_KEY" ]; then
        install -m 0644 "$MESHLOOM_GPG_KEY" "$key"
        echo "deb [signed-by=/etc/apt/keyrings/meshloom.gpg] ${pages}/apt stable main" >"$list"
        return
    fi
    echo "[rpi-kiosk] Writing an unsigned Meshloom apt source (no local GPG key)."
    echo "deb [trusted=yes] ${pages}/apt stable main" >"$list"
}

WORKDIR="$(mktemp -d /tmp/meshloom-rpi-kiosk.XXXXXX)"
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
    if [ -n "${BOOTMNT:-}" ]; then
        umount "$BOOTMNT" 2>/dev/null
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
echo "[rpi-kiosk] Disk before download:"
df -h "$OUTPUT_DIR" "$WORKDIR" || df -h
require_disk "$WORKDIR" "workdir"
require_disk "$OUTPUT_DIR" "output"
echo "[rpi-kiosk] Downloading pinned Desktop base image..."
curl -fL "$IMAGE_URL" -o "$WORKDIR/base.img.xz"
if [ -n "$IMAGE_SHA256" ]; then
    echo "$IMAGE_SHA256  $WORKDIR/base.img.xz" | sha256sum -c -
fi
unxz -f "$WORKDIR/base.img.xz"
IMG="$WORKDIR/base.img"

echo "[rpi-kiosk] Growing image by 3G so Meshloom fits..."
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
    echo "[rpi-kiosk] losetup -P did not publish partitions; trying kpartx -a"
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
echo "[rpi-kiosk] Resizing root partition..."
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

echo "[rpi-kiosk] Disk after mount:"
df -h "$ROOTMNT"

cp "$DEB" "$ROOTMNT/tmp/meshloom.deb"
install -D -m 0755 "$RPI_DIR/meshloom-kiosk" "$ROOTMNT/usr/lib/meshloom/meshloom-kiosk"
install -D -m 0755 "$RPI_DIR/meshloom-kiosk-setup" "$ROOTMNT/usr/lib/meshloom/meshloom-kiosk-setup"
install -D -m 0644 "$RPI_DIR/meshloom-kiosk.desktop" \
    "$ROOTMNT/etc/xdg/autostart/meshloom-kiosk.desktop"
install -D -m 0644 "$RPI_DIR/meshloom-kiosk-setup.service" \
    "$ROOTMNT/usr/lib/systemd/system/meshloom-kiosk-setup.service"

if [ -f "$ROOTMNT/etc/xdg/labwc/autostart" ] &&
    ! grep -q meshloom-kiosk "$ROOTMNT/etc/xdg/labwc/autostart"; then
    printf '\n/usr/lib/meshloom/meshloom-kiosk &\n' >>"$ROOTMNT/etc/xdg/labwc/autostart"
fi

# Apt source for later UI upgrades. Do not probe Pages here.
write_meshloom_apt_source "$ROOTMNT"

# Do not set MESHLOOM_COMMUNITY — new databases join Community by default.
# Bots stay off via the packaged meshloom.env.
# Do not install meshloom-console: it would seize tty1 from the desktop.

for fs in proc sys dev dev/pts; do
    mkdir -p "$ROOTMNT/$fs"
    mount --bind "/$fs" "$ROOTMNT/$fs"
done

chroot "$ROOTMNT" /bin/bash -s <<'CHROOT'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
echo "[rpi-kiosk] Disk inside chroot before packages:"
df -h /
apt-get update -y
apt-get install -y --no-install-recommends avahi-daemon ca-certificates curl
command -v chromium >/dev/null || command -v chromium-browser >/dev/null || {
    echo "chromium is missing from the Desktop base image" >&2
    exit 1
}
dpkg -i /tmp/meshloom.deb || apt-get install -y -f
dpkg-query -W -f='${Status}\n' meshloom | grep -q 'ok installed'
rm -f /tmp/meshloom.deb
systemctl enable meshloom.service
systemctl enable meshloom-kiosk-setup.service
systemctl enable avahi-daemon.service
systemctl --quiet set-default graphical.target
systemctl disable meshloom-console.service 2>/dev/null || true
hostnamectl set-hostname meshloom 2>/dev/null || echo meshloom >/etc/hostname
echo "[rpi-kiosk] Disk inside chroot after Meshloom:"
df -h /
CHROOT

if [ -f "$BOOTMNT/network-config" ]; then
    if ! grep -q "optional:" "$BOOTMNT/network-config"; then
        echo "[rpi-kiosk] Leaving stock network-config (Imager will replace it)."
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
BOOTMNT=""

OUT_IMG="$OUTPUT_DIR/meshloom-rpi-kiosk-arm64.img"
cp "$IMG" "$OUT_IMG"
EXTRACT_SIZE="$(stat -c %s "$OUT_IMG")"
EXTRACT_SHA="$(sha256sum "$OUT_IMG" | awk '{print $1}')"
echo "[rpi-kiosk] Compressing..."
xz -T0 -f "$OUT_IMG"
echo "[rpi-kiosk] Wrote ${OUT_IMG}.xz"
ls -lh "${OUT_IMG}.xz"
XZ_SIZE="$(stat -c %s "${OUT_IMG}.xz")"
if [ "$XZ_SIZE" -ge "$TWO_GIB" ]; then
    echo "[rpi-kiosk] ${OUT_IMG}.xz is ${XZ_SIZE} bytes (>= 2 GiB). GitHub release assets must stay under 2 GiB." >&2
    exit 1
fi
echo "[rpi-kiosk] Disk after compress:"
df -h "$OUTPUT_DIR"

if [ -n "$VERSION" ]; then
    VERSION="${VERSION#v}"
    DOWNLOAD_SIZE="$(stat -c %s "${OUT_IMG}.xz")"
    DOWNLOAD_SHA="$(sha256sum "${OUT_IMG}.xz" | awk '{print $1}')"
    RELEASE_DATE="$(date -u +%Y-%m-%d)"
    IMAGE_DOWNLOAD_URL="https://github.com/TwinRocket/meshloom/releases/download/${VERSION}/meshloom-rpi-kiosk-arm64.img.xz"
    MANIFEST="$OUTPUT_DIR/meshloom-kiosk.rpi-imager-manifest"
    sed \
        -e "s#__IMAGE_URL__#${IMAGE_DOWNLOAD_URL}#g" \
        -e "s#__RELEASE_DATE__#${RELEASE_DATE}#g" \
        -e "s#__EXTRACT_SIZE__#${EXTRACT_SIZE}#g" \
        -e "s#__EXTRACT_SHA256__#${EXTRACT_SHA}#g" \
        -e "s#__DOWNLOAD_SIZE__#${DOWNLOAD_SIZE}#g" \
        -e "s#__DOWNLOAD_SHA256__#${DOWNLOAD_SHA}#g" \
        "$RPI_DIR/os-list-kiosk.rpi-imager-manifest.tmpl" >"$MANIFEST"
    echo "[rpi-kiosk] Wrote $MANIFEST"
fi
