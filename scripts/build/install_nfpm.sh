#!/usr/bin/env bash
# Install the pinned nFPM, after checking the tarball against its pinned sha256.
# Release jobs run it next to the signing key, so a download that does not match
# must never run. Digests: the v2.46.3 release assets on goreleaser/nfpm.
#
# NFPM_INSTALL_DIR overrides the destination (default /usr/local/bin, with sudo).
set -euo pipefail

NFPM_VER=2.46.3
case "$(uname -m)" in
    aarch64 | arm64)
        NFPM_ARCH=Linux_arm64
        NFPM_SHA256=7871d72bb1035924eab56031f962ae3f004b1f7695151789ef018bdbc507a2fa
        ;;
    x86_64 | amd64)
        NFPM_ARCH=Linux_x86_64
        NFPM_SHA256=9170d25ea056d7329def38134b2bcad98d02d221f8331610dcd619e84d28c565
        ;;
    *)
        echo "No pinned nFPM for $(uname -m)" >&2
        exit 1
        ;;
esac

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
TARBALL="$WORK/nfpm.tar.gz"
curl -fsSL "https://github.com/goreleaser/nfpm/releases/download/v${NFPM_VER}/nfpm_${NFPM_VER}_${NFPM_ARCH}.tar.gz" \
    -o "$TARBALL"
echo "${NFPM_SHA256}  ${TARBALL}" | sha256sum -c -
tar -xzf "$TARBALL" -C "$WORK" nfpm

if [ -n "${NFPM_INSTALL_DIR:-}" ]; then
    mkdir -p "$NFPM_INSTALL_DIR"
    install -m 0755 "$WORK/nfpm" "$NFPM_INSTALL_DIR/nfpm"
else
    sudo install -m 0755 "$WORK/nfpm" /usr/local/bin/nfpm
fi
