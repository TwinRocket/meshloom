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
STAGE_DIR=""
STAGE_ONLY=0
PACKAGE_ONLY=0
DEPS_ONLY=0
SKIP_DEPS=0

usage() {
    cat <<'EOF'
Usage: scripts/build/build_nfpm_packages.sh --version X.Y.Z --arch amd64|arm64|armhf [options]

Options:
  --output-dir DIR     Destination for .deb/.rpm (default: dist/)
  --skip-frontend      Reuse frontend/dist instead of building
  --stage-dir DIR      Assemble here and keep it, instead of a temporary directory
  --stage-only         Assemble the tree and stop, leaving nFPM to a later call
  --deps-only          Download Python and uv-sync deps; do not copy app/
  --skip-deps          Finish staging over an existing --deps-only tree
  --package-only       Pack an already assembled --stage-dir
  --help

The two halves exist for armhf. The tree can only be assembled where its own
interpreter runs, which for armv7 means emulation, and nFPM publishes no armv7
binary to run there. So the tree is assembled under emulation and packed after.

--deps-only / --skip-deps split that assembly so a Docker layer can cache the
compiled deps without being invalidated by app/ or a version bump.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --version) VERSION="${2:-}"; shift 2 ;;
        --arch) ARCH="${2:-}"; shift 2 ;;
        --output-dir) OUTPUT_DIR="${2:-}"; shift 2 ;;
        --skip-frontend) SKIP_FRONTEND=1; shift ;;
        --stage-dir) STAGE_DIR="${2:-}"; shift 2 ;;
        --stage-only) STAGE_ONLY=1; shift ;;
        --deps-only) DEPS_ONLY=1; shift ;;
        --skip-deps) SKIP_DEPS=1; shift ;;
        --package-only) PACKAGE_ONLY=1; shift ;;
        --help) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
    esac
done

[ -n "$VERSION" ] || { echo "--version is required" >&2; exit 1; }
case "$ARCH" in
    amd64) PY_TRIPLE="x86_64-unknown-linux-gnu"; NFPM_ARCH="amd64" ;;
    arm64) PY_TRIPLE="aarch64-unknown-linux-gnu"; NFPM_ARCH="arm64" ;;
    # Raspberry Pi OS 32-bit is hard-float. nFPM names this one after the Go
    # architecture and turns it into armhf for deb.
    armhf) PY_TRIPLE="armv7-unknown-linux-gnueabihf"; NFPM_ARCH="arm7" ;;
    *) echo "--arch must be amd64, arm64 or armhf" >&2; exit 1 ;;
esac

HALF_COUNT=$((STAGE_ONLY + PACKAGE_ONLY + DEPS_ONLY + SKIP_DEPS))
if [ "$HALF_COUNT" -gt 1 ]; then
    echo "--deps-only, --skip-deps, --stage-only, and --package-only are exclusive" >&2
    exit 1
fi
[ "$PACKAGE_ONLY" -eq 0 ] || [ -n "$STAGE_DIR" ] || {
    echo "--package-only needs --stage-dir" >&2
    exit 1; }
if [ "$DEPS_ONLY" -eq 1 ] || [ "$SKIP_DEPS" -eq 1 ]; then
    [ -n "$STAGE_DIR" ] || {
        echo "--deps-only and --skip-deps need --stage-dir" >&2
        exit 1; }
fi

PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PYDATE}/cpython-${PYVER}+${PYDATE}-${PY_TRIPLE}-install_only_stripped.tar.gz"

# Each half needs its own tool and not the other's. Demanding both would ask for
# nFPM inside the emulated container, where no armv7 build of it exists.
NEED_NFPM=1
NEED_UV=1
[ "$STAGE_ONLY" -eq 0 ] && [ "$DEPS_ONLY" -eq 0 ] && [ "$SKIP_DEPS" -eq 0 ] || NEED_NFPM=0
[ "$PACKAGE_ONLY" -eq 0 ] && [ "$SKIP_DEPS" -eq 0 ] || NEED_UV=0

if [ "$NEED_NFPM" -eq 1 ] && ! command -v nfpm >/dev/null 2>&1; then
    echo "nFPM is required. Install: https://nfpm.goreleaser.com/install/" >&2
    exit 1
fi
if [ "$NEED_UV" -eq 1 ] && ! command -v uv >/dev/null 2>&1; then
    echo "uv is required." >&2
    exit 1
fi

if [ "$PACKAGE_ONLY" -eq 0 ] && [ "$DEPS_ONLY" -eq 0 ]; then
    if [ "$SKIP_FRONTEND" -eq 0 ]; then
        (cd "$REPO_ROOT/frontend" && npm ci && npm run build)
    fi
    if [ ! -d "$REPO_ROOT/frontend/dist" ]; then
        echo "frontend/dist is missing. Build the frontend or omit --skip-frontend." >&2
        exit 1
    fi
fi

if [ -n "$STAGE_DIR" ]; then
    # Absolute, because the assembly cds into this tree and then hands uv the
    # path to the interpreter inside it. A relative one stops resolving there.
    mkdir -p "$STAGE_DIR"
    STAGING="$(cd "$STAGE_DIR" && pwd)"
else
    STAGING="$(mktemp -d)"
    trap 'rm -rf "$STAGING"' EXIT
fi
OPT="$STAGING/opt/meshloom"

require_assembled_tree() {
    [ -x "$OPT/python/bin/python3" ] || {
        echo "No assembled tree under $STAGE_DIR" >&2
        exit 1
    }
}

download_standalone_python() {
    echo "[nfpm] Downloading standalone Python ${PYVER} (${ARCH})..."
    mkdir -p "$OPT"
    PY_TGZ="$(mktemp)"
    curl -fL "$PY_URL" -o "$PY_TGZ"
    tar -xzf "$PY_TGZ" -C "$OPT"
    rm -f "$PY_TGZ"
    # tarball extracts a "python/" directory
    if [ ! -x "$OPT/python/bin/python3" ]; then
        echo "Standalone Python layout unexpected under $OPT/python" >&2
        exit 1
    fi
}

copy_manifests() {
    cp "$REPO_ROOT/pyproject.toml" "$REPO_ROOT/uv.lock" "$OPT/"
}

copy_project_files() {
    mkdir -p "$OPT/frontend"
    cp -a "$REPO_ROOT/app" "$OPT/app"
    copy_manifests
    [ -f "$REPO_ROOT/LICENSE.md" ] && cp "$REPO_ROOT/LICENSE.md" "$OPT/"
    [ -f "$REPO_ROOT/LICENSES.md" ] && cp "$REPO_ROOT/LICENSES.md" "$OPT/"
    cp -a "$REPO_ROOT/frontend/dist" "$OPT/frontend/dist"
    # Layer-2 bundled hashtag names: same snapshot as frontend/src/data/.
    # meshcore_channels.py looks here after the repo-tree path.
    cp "$REPO_ROOT/frontend/src/data/meshcoreChannels.snapshot.json" \
        "$OPT/app/data/meshcoreChannels.snapshot.json"
    ln -sfn /var/lib/meshloom "$OPT/data"
}

create_venv() {
    local extra=()
    if [ "${1:-}" = "no-project" ]; then
        extra+=(--no-install-project)
    fi
    echo "[nfpm] Creating venv..."
    (
        cd "$OPT"
        uv venv --python "$OPT/python/bin/python3" .venv
        if [ ${#extra[@]} -gt 0 ]; then
            uv sync --no-dev --frozen "${extra[@]}"
        else
            uv sync --no-dev --frozen
        fi
    )
}

rewrite_venv_paths() {
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
}

if [ "$PACKAGE_ONLY" -eq 1 ]; then
    require_assembled_tree
elif [ "$DEPS_ONLY" -eq 1 ]; then
    download_standalone_python
    copy_manifests
    create_venv no-project
    rewrite_venv_paths
elif [ "$SKIP_DEPS" -eq 1 ]; then
    require_assembled_tree
    [ -d "$OPT/.venv" ] || {
        echo "No dependency tree under $STAGE_DIR; run --deps-only first" >&2
        exit 1
    }
    copy_project_files
    rewrite_venv_paths
else
    download_standalone_python
    copy_project_files
    create_venv
    rewrite_venv_paths
fi

if [ "$STAGE_ONLY" -eq 1 ] || [ "$DEPS_ONLY" -eq 1 ] || [ "$SKIP_DEPS" -eq 1 ]; then
    echo "[nfpm] Assembled $ARCH tree in $STAGING"
    exit 0
fi

mkdir -p "$OUTPUT_DIR"
CFG="$(mktemp)"
sed \
    -e "s|__NFPM_ARCH__|$NFPM_ARCH|g" \
    -e "s|__NFPM_VERSION__|$VERSION|g" \
    -e "s|__STAGING__|$STAGING|g" \
    -e "s|__PKGDIR__|$PKGDIR|g" \
    "$PKGDIR/nfpm.yaml.tmpl" >"$CFG"

echo "[nfpm] Packaging $ARCH $VERSION..."
nfpm package --config "$CFG" --packager deb --target "$OUTPUT_DIR"
if [ "$ARCH" != "armhf" ]; then
    nfpm package --config "$CFG" --packager rpm --target "$OUTPUT_DIR"
fi
rm -f "$CFG"

echo "[nfpm] Wrote packages in $OUTPUT_DIR"
ls -l "$OUTPUT_DIR"/meshloom*"$VERSION"* || ls -l "$OUTPUT_DIR"
