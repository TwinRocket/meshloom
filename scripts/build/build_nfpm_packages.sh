#!/usr/bin/env bash
# Assemble a self-contained Meshloom tree and pack .deb / .rpm with nFPM.
#
# Usage:
#   scripts/build/build_nfpm_packages.sh --version 4.0.0 --arch amd64 [--output-dir dist]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PKGDIR="$REPO_ROOT/pkg/nfpm"

PYVER="${MESHLOOM_STANDALONE_PYVER:-3.13.13}"
PYDATE="${MESHLOOM_STANDALONE_PYDATE:-20260408}"

VERSION=""
ARCH=""
OUTPUT_DIR="$REPO_ROOT/dist"
SKIP_FRONTEND=0

usage() {
    cat <<'EOF'
Usage: scripts/build/build_nfpm_packages.sh --version X.Y.Z --arch amd64|arm64 [options]

Options:
  --output-dir DIR     Destination for .deb/.rpm (default: dist/)
  --skip-frontend      Reuse frontend/dist instead of building
  --help
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --version) VERSION="${2:-}"; shift 2 ;;
        --arch) ARCH="${2:-}"; shift 2 ;;
        --output-dir) OUTPUT_DIR="${2:-}"; shift 2 ;;
        --skip-frontend) SKIP_FRONTEND=1; shift ;;
        --help) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
    esac
done

[ -n "$VERSION" ] || { echo "--version is required" >&2; exit 1; }
[ "$ARCH" = "amd64" ] || [ "$ARCH" = "arm64" ] || { echo "--arch must be amd64 or arm64" >&2; exit 1; }

if [ "$ARCH" = "amd64" ]; then
    PY_TRIPLE="x86_64-unknown-linux-gnu"
else
    PY_TRIPLE="aarch64-unknown-linux-gnu"
fi

PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PYDATE}/cpython-${PYVER}+${PYDATE}-${PY_TRIPLE}-install_only_stripped.tar.gz"

if ! command -v nfpm >/dev/null 2>&1; then
    echo "nFPM is required. Install: https://nfpm.goreleaser.com/install/" >&2
    exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required." >&2
    exit 1
fi

if [ "$SKIP_FRONTEND" -eq 0 ]; then
    (cd "$REPO_ROOT/frontend" && npm ci && npm run build)
fi
if [ ! -d "$REPO_ROOT/frontend/dist" ]; then
    echo "frontend/dist is missing. Build the frontend or omit --skip-frontend." >&2
    exit 1
fi

STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT
OPT="$STAGING/opt/meshloom"
mkdir -p "$OPT/frontend"

echo "[nfpm] Downloading standalone Python ${PYVER} (${ARCH})..."
curl -fL "$PY_URL" -o "$STAGING/python.tgz"
mkdir -p "$OPT"
tar -xzf "$STAGING/python.tgz" -C "$OPT"
# tarball extracts a "python/" directory
if [ ! -x "$OPT/python/bin/python3" ]; then
    echo "Standalone Python layout unexpected under $OPT/python" >&2
    exit 1
fi

cp -a "$REPO_ROOT/app" "$OPT/app"
cp "$REPO_ROOT/pyproject.toml" "$REPO_ROOT/uv.lock" "$OPT/"
[ -f "$REPO_ROOT/LICENSE.md" ] && cp "$REPO_ROOT/LICENSE.md" "$OPT/"
[ -f "$REPO_ROOT/LICENSES.md" ] && cp "$REPO_ROOT/LICENSES.md" "$OPT/"
cp -a "$REPO_ROOT/frontend/dist" "$OPT/frontend/dist"
# Layer-2 bundled hashtag names: same snapshot as frontend/src/data/.
# meshcore_channels.py looks here after the repo-tree path.
cp "$REPO_ROOT/frontend/src/data/meshcoreChannels.snapshot.json" \
    "$OPT/app/data/meshcoreChannels.snapshot.json"
ln -s /var/lib/meshloom "$OPT/data"

echo "[nfpm] Creating venv..."
(
    cd "$OPT"
    uv venv --python "$OPT/python/bin/python3" .venv
    uv sync --no-dev --frozen
)

echo "[nfpm] Rewriting venv paths..."
find "$OPT/.venv/bin" -type f -exec \
    sed -i "s|$OPT/.venv|/opt/meshloom/.venv|g; s|$OPT/python|/opt/meshloom/python|g" {} +
if [ -f "$OPT/.venv/pyvenv.cfg" ]; then
    sed -i \
        -e "s|$OPT/.venv|/opt/meshloom/.venv|g" \
        -e "s|$OPT/python|/opt/meshloom/python|g" \
        "$OPT/.venv/pyvenv.cfg"
fi
ln -sfn /opt/meshloom/python/bin/python3 "$OPT/.venv/bin/python"
ln -sfn python "$OPT/.venv/bin/python3"

mkdir -p "$OUTPUT_DIR"
CFG="$(mktemp)"
sed \
    -e "s|__NFPM_ARCH__|$ARCH|g" \
    -e "s|__NFPM_VERSION__|$VERSION|g" \
    -e "s|__STAGING__|$STAGING|g" \
    -e "s|__PKGDIR__|$PKGDIR|g" \
    "$PKGDIR/nfpm.yaml.tmpl" >"$CFG"

echo "[nfpm] Packaging $ARCH $VERSION..."
nfpm package --config "$CFG" --packager deb --target "$OUTPUT_DIR"
nfpm package --config "$CFG" --packager rpm --target "$OUTPUT_DIR"
rm -f "$CFG"

echo "[nfpm] Wrote packages in $OUTPUT_DIR"
ls -l "$OUTPUT_DIR"/meshloom*"$VERSION"* || ls -l "$OUTPUT_DIR"
