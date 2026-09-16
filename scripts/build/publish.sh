#!/usr/bin/env bash
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/build/release_common.sh
source "$SCRIPT_DIR/release_common.sh"

# The Docker workflow builds and pushes the multi-arch image when the version
# tag lands; this is only used for the summary at the end.
DOCKER_IMAGE="ghcr.io/bagl3y/meshloom"
VERSION=""
NOTES_FILE=""
SKIP_QUALITY=0
SKIP_LICENSES=0

usage() {
    cat <<'EOF'
Usage: scripts/build/publish.sh [options]

Options:
  --version VERSION         Release version; prompts if omitted
  --notes-file PATH         Changelog bullets if CHANGELOG.md has no [$VERSION] yet
  --skip-quality            Skip ./scripts/quality/all_quality.sh
  --skip-licenses           Skip regenerating LICENSES.md
  --help                    Show this message
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --version)
            VERSION="${2:-}"
            shift 2
            ;;
        --notes-file)
            NOTES_FILE="${2:-}"
            shift 2
            ;;
        --skip-quality)
            SKIP_QUALITY=1
            shift
            ;;
        --skip-licenses)
            SKIP_LICENSES=1
            shift
            ;;
        --help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            release_die "Unknown argument: $1"
            ;;
    esac
done

echo -e "${YELLOW}=== Meshloom Publish Script ===${NC}"
echo

if [ "$SKIP_QUALITY" -eq 0 ]; then
    echo -e "${YELLOW}Running repo quality gate...${NC}"
    ./scripts/quality/all_quality.sh
    echo -e "${GREEN}Quality gate passed!${NC}"
    echo
fi

if [ "$SKIP_LICENSES" -eq 0 ]; then
    echo -e "${YELLOW}Regenerating LICENSES.md...${NC}"
    bash scripts/build/collect_licenses.sh LICENSES.md
    echo -e "${GREEN}LICENSES.md updated!${NC}"
    echo
fi

# Prompt for version
echo -e "${YELLOW}Current versions:${NC}"
echo -n "  pyproject.toml: "
grep '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/'
echo -n "  package.json:   "
grep '"version"' frontend/package.json | head -1 | sed 's/.*"version": "\(.*\)".*/\1/'
echo

if [ -z "$VERSION" ]; then
    read -r -p "Enter new version (e.g., 1.2.3): " VERSION
fi
VERSION="$(release_trim "$VERSION")"
release_validate_version "$VERSION"

# Update pyproject.toml
echo -e "${YELLOW}Updating pyproject.toml...${NC}"
release_sed_i "s/^version = \".*\"/version = \"$VERSION\"/" pyproject.toml

# Update frontend package.json
echo -e "${YELLOW}Updating frontend/package.json...${NC}"
release_sed_i "s/\"version\": \".*\"/\"version\": \"$VERSION\"/" frontend/package.json

# The add-on advertises the image it installs, so its version is the release's or it
# is a lie. Written here rather than by hand, for the same reason the other two are.
echo -e "${YELLOW}Updating Home Assistant add-on...${NC}"
release_sed_i "s/^version: \".*\"/version: \"$VERSION\"/" meshloom/config.yaml
release_sed_i "s|^FROM ghcr.io/bagl3y/meshloom:.*|FROM ghcr.io/bagl3y/meshloom:$VERSION|" meshloom/Dockerfile

# Update uv.lock with new version
echo -e "${YELLOW}Updating uv.lock...${NC}"
uv sync

echo -e "${GREEN}Version updated to $VERSION${NC}"
echo

if release_changelog_has_version "$REPO_ROOT" "$VERSION"; then
    echo -e "${GREEN}CHANGELOG.md already has [$VERSION]; leaving it as-is.${NC}"
    if [ -n "$NOTES_FILE" ]; then
        echo -e "${YELLOW}Ignoring --notes-file because that section already exists.${NC}"
    fi
    echo
else
    # Prompt for changelog entry
    RAW_CHANGELOG_INPUT_FILE="$(mktemp)"
    FORMATTED_CHANGELOG_INPUT_FILE="$(mktemp)"
    cleanup() {
        rm -f "$RAW_CHANGELOG_INPUT_FILE" "$FORMATTED_CHANGELOG_INPUT_FILE"
    }
    trap cleanup EXIT

    if [ -n "$NOTES_FILE" ]; then
        cp "$NOTES_FILE" "$RAW_CHANGELOG_INPUT_FILE"
    else
        echo -e "${YELLOW}Enter changelog entry for version $VERSION${NC}"
        echo -e "${YELLOW}(Enter your changes, then press Ctrl+D when done):${NC}"
        echo
        cat > "$RAW_CHANGELOG_INPUT_FILE"
    fi

    release_format_markdown_list "$RAW_CHANGELOG_INPUT_FILE" "$FORMATTED_CHANGELOG_INPUT_FILE"
    [ -s "$FORMATTED_CHANGELOG_INPUT_FILE" ] || release_die "Changelog entry cannot be empty"

    # Create changelog entry with date
    DATE=$(date +%Y-%m-%d)
    CHANGELOG_HEADER="## [$VERSION] - $DATE"

    # Prepend to CHANGELOG.md (after the title if it exists)
    if [ -f CHANGELOG.md ]; then
        # Check if file starts with a title
        if head -1 CHANGELOG.md | grep -q "^# "; then
            # Insert after title line
            {
                head -1 CHANGELOG.md
                echo
                echo "$CHANGELOG_HEADER"
                echo
                cat "$FORMATTED_CHANGELOG_INPUT_FILE"
                echo
                tail -n +2 CHANGELOG.md
            } > CHANGELOG.md.tmp
            mv CHANGELOG.md.tmp CHANGELOG.md
        else
            # No title, prepend directly
            {
                echo "$CHANGELOG_HEADER"
                echo
                cat "$FORMATTED_CHANGELOG_INPUT_FILE"
                echo
                cat CHANGELOG.md
            } > CHANGELOG.md.tmp
            mv CHANGELOG.md.tmp CHANGELOG.md
        fi
    else
        # Create new changelog
        {
            echo "# Changelog"
            echo
            echo "$CHANGELOG_HEADER"
            echo
            cat "$FORMATTED_CHANGELOG_INPUT_FILE"
        } > CHANGELOG.md
    fi

    echo
    echo -e "${GREEN}Changelog updated!${NC}"
    echo
fi

# Commit the changes
echo -e "${YELLOW}Committing changes...${NC}"
git add .
git commit -m "Updating changelog + build for $VERSION"
git push
echo -e "${GREEN}Changes committed!${NC}"
echo

# Tag the release. The Release workflow builds the prebuilt-frontend artifact
# and publishes the GitHub release from this tag, so doing it here too would
# race with CI on `gh release create`.
FULL_GIT_HASH=$(git rev-parse HEAD)
TAG_NOTES_FILE="$(mktemp)"
release_extract_changelog_section "$REPO_ROOT" "$VERSION" "$TAG_NOTES_FILE"

echo -e "${YELLOW}Tagging $VERSION...${NC}"
git tag -a "$VERSION" "$FULL_GIT_HASH" -F "$TAG_NOTES_FILE"
rm -f "$TAG_NOTES_FILE"
git push origin "$VERSION"
echo -e "${GREEN}Tag pushed! CI will build the artifact and publish the release.${NC}"
echo

echo -e "${GREEN}=== Publish complete! ===${NC}"
echo -e "Version: ${YELLOW}$VERSION${NC}"
echo -e "Git hash: ${YELLOW}$(git rev-parse --short HEAD)${NC}"
echo -e "Docker image: built and pushed by the Docker workflow on the $VERSION tag:"
echo -e "  - $DOCKER_IMAGE:latest"
echo -e "  - $DOCKER_IMAGE:$VERSION"
echo -e "GitHub release: published by the Release workflow on the $VERSION tag"
