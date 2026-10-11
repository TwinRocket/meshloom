#!/usr/bin/env bash
# Cuts a release in one command: scripts/build/publish.sh X.Y.Z
#
# Run by an admin on main, equal to origin/main (the `main` ruleset lets admins
# push directly; X.Y.Z tags are reserved to admins). The only local change allowed
# is an uncommitted CHANGELOG.md edit: write the [X.Y.Z] section by hand, then run
# this. It runs the quality gate, regenerates LICENSES.md, bumps the four version
# sources (pyproject.toml, frontend/package.json, meshloom/config.yaml, the FROM
# tag of meshloom/Dockerfile) and uv.lock, checks them, commits exactly those files
# (plus CHANGELOG.md), pushes main, then creates and pushes the annotated X.Y.Z tag.
# The tag starts release.yml and docker.yml; nothing is built here.
#
# The tag is pushed only once main is. If main went out but the tag did not,
# finish with scripts/build/tag_release.sh X.Y.Z.
#
# The tag is deliberately not GPG-signed: the artifacts it produces are signed by
# the release key (pkg/keys), which is what clients verify.
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/build/release_common.sh
source "$SCRIPT_DIR/release_common.sh"

# The Docker workflow builds and pushes the multi-arch image when the version
# tag lands; this is only used for the summary at the end.
DOCKER_IMAGE="ghcr.io/twinrocket/meshloom"
GH_REPO="${MESHLOOM_GH_REPO:-TwinRocket/meshloom}"
VERSION=""
NOTES_FILE=""
SKIP_QUALITY=0
SKIP_LICENSES=0

usage() {
    cat <<'EOF'
Usage: scripts/build/publish.sh [X.Y.Z] [options]

Releases X.Y.Z from main: commits the release, pushes main, then pushes the
annotated X.Y.Z tag. An uncommitted CHANGELOG.md edit (the [X.Y.Z] section
written by hand) is committed with the release; any other local change is
refused.

Options:
  --version VERSION         Release version (same as the positional argument);
                            prompts if omitted
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
        -*)
            usage >&2
            release_die "Unknown argument: $1"
            ;;
        *)
            [ -z "$VERSION" ] || release_die "Version given twice: '$VERSION' and '$1'"
            VERSION="$1"
            shift
            ;;
    esac
done

echo -e "${YELLOW}=== Meshloom release ===${NC}"
echo

if [ -n "$NOTES_FILE" ] && [ ! -f "$NOTES_FILE" ]; then
    release_die "Notes file not found: $NOTES_FILE"
fi

# A release is cut from an up-to-date main: the commit below is pushed to main and
# the tag points at it. Anything else would push unrelated work or tag a commit
# that is not on main, so refuse before changing anything.
RELEASE_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$RELEASE_BRANCH" != "main" ]; then
    release_die "Releases are cut from main; you are on '$RELEASE_BRANCH'."
fi
# The release notes are the one local change a release may carry.
if ! release_tree_clean_but_changelog; then
    git status --short >&2
    release_die "The working tree has changes other than CHANGELOG.md; commit or stash them first."
fi
git fetch --quiet origin main
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
    if git merge-base --is-ancestor HEAD origin/main; then
        release_die "main is behind origin/main; run 'git pull --ff-only' first."
    fi
    release_die "main is not at origin/main (it has commits origin does not); push or drop them first."
fi

echo -e "${YELLOW}Current versions:${NC}"
echo -n "  pyproject.toml: "
grep '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/'
echo -n "  package.json:   "
grep '"version"' frontend/package.json | head -1 | sed 's/.*"version": "\(.*\)".*/\1/'
echo

# Ask for the version before the long quality gate, not after it.
if [ -z "$VERSION" ]; then
    read -r -p "Enter new version (e.g., 1.2.3): " VERSION
fi
VERSION="$(release_trim "$VERSION")"
release_validate_version "$VERSION"

# The tag must be new, here and on origin.
if git rev-parse -q --verify "refs/tags/$VERSION" >/dev/null; then
    release_die "Tag $VERSION already exists locally; pick another version."
fi
set +e
git ls-remote --exit-code --tags origin "refs/tags/$VERSION" >/dev/null 2>&1
LS_REMOTE_STATUS=$?
set -e
case "$LS_REMOTE_STATUS" in
    0) release_die "Tag $VERSION already exists on origin; pick another version." ;;
    2) ;;
    *) release_die "Could not query origin's tags (git ls-remote exited $LS_REMOTE_STATUS)." ;;
esac

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

echo -e "${YELLOW}Updating pyproject.toml...${NC}"
release_sed_i "s/^version = \".*\"/version = \"$VERSION\"/" pyproject.toml

echo -e "${YELLOW}Updating frontend/package.json...${NC}"
release_sed_i "s/\"version\": \".*\"/\"version\": \"$VERSION\"/" frontend/package.json

# The add-on advertises the image it installs, so its version is the release's or it
# is a lie. Written here rather than by hand, for the same reason the other two are.
echo -e "${YELLOW}Updating Home Assistant add-on...${NC}"
release_sed_i "s/^version: \".*\"/version: \"$VERSION\"/" meshloom/config.yaml
release_sed_i "s|^FROM ghcr.io/twinrocket/meshloom:.*|FROM ghcr.io/twinrocket/meshloom:$VERSION|" meshloom/Dockerfile

echo -e "${YELLOW}Updating uv.lock...${NC}"
uv sync

scripts/build/check_version_consistency.sh "$VERSION"
echo -e "${GREEN}Version updated to $VERSION${NC}"
echo

RAW_CHANGELOG_INPUT_FILE="$(mktemp)"
FORMATTED_CHANGELOG_INPUT_FILE="$(mktemp)"
TAG_NOTES_FILE="$(mktemp)"
cleanup() {
    rm -f "$RAW_CHANGELOG_INPUT_FILE" "$FORMATTED_CHANGELOG_INPUT_FILE" "$TAG_NOTES_FILE"
}
trap cleanup EXIT

if release_changelog_has_version "$REPO_ROOT" "$VERSION"; then
    echo -e "${GREEN}CHANGELOG.md already has [$VERSION]; leaving it as-is.${NC}"
    if [ -n "$NOTES_FILE" ]; then
        echo -e "${YELLOW}Ignoring --notes-file because that section already exists.${NC}"
    fi
    echo
else
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

    DATE=$(date +%Y-%m-%d)
    CHANGELOG_HEADER="## [$VERSION] - $DATE"

    # Prepend to CHANGELOG.md (after the title if it exists)
    if [ -f CHANGELOG.md ]; then
        if head -1 CHANGELOG.md | grep -q "^# "; then
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
release_extract_changelog_section "$REPO_ROOT" "$VERSION" "$TAG_NOTES_FILE"

echo -e "${YELLOW}Committing changes...${NC}"
# Only the files this script writes: never `git add .`, which would also commit
# whatever else the quality gate's autofixers touched.
git add pyproject.toml uv.lock frontend/package.json meshloom/config.yaml \
    meshloom/Dockerfile CHANGELOG.md
if [ "$SKIP_LICENSES" -eq 0 ]; then
    git add LICENSES.md
fi
git commit -m "Updating changelog + build for $VERSION"
FULL_GIT_HASH="$(git rev-parse HEAD)"
if [ -n "$(git status --porcelain)" ]; then
    echo -e "${RED}Warning: the quality gate left uncommitted changes; they are NOT in the release:${NC}"
    git status --short
    echo
fi

# The tag is created locally first, so a bad tag fails before anything leaves
# this machine, and it is pushed only once main is on origin.
git tag -a "$VERSION" "$FULL_GIT_HASH" -F "$TAG_NOTES_FILE"

echo -e "${YELLOW}Pushing main...${NC}"
if ! git push origin main; then
    git tag -d "$VERSION" >/dev/null
    release_die "Pushing main failed: nothing was pushed, and the local tag $VERSION was deleted. The release commit $FULL_GIT_HASH stays on your local main; once 'git push origin main' works, finish with scripts/build/tag_release.sh $VERSION."
fi

echo -e "${YELLOW}Pushing tag $VERSION...${NC}"
if ! git push origin "refs/tags/$VERSION"; then
    git tag -d "$VERSION" >/dev/null
    release_die "main is pushed but the tag $VERSION is not (the local tag was deleted). Finish with scripts/build/tag_release.sh $VERSION once All Quality is green on main."
fi
echo

echo -e "${GREEN}=== Release $VERSION pushed ===${NC}"
echo -e "Commit: ${YELLOW}$FULL_GIT_HASH${NC}"
echo "release.yml and docker.yml now build and publish $VERSION:"
echo "  gh run list -R $GH_REPO --branch $VERSION"
echo "Docker image: $DOCKER_IMAGE:$VERSION and :latest (built by docker.yml)"
