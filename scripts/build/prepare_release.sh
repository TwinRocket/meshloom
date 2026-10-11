#!/usr/bin/env bash
# Step 1 of 2 of a release: open the release pull request.
#
# From an up-to-date origin/main, creates the branch release/X.Y.Z, runs the
# quality gate, regenerates LICENSES.md, bumps the four version sources
# (pyproject.toml, frontend/package.json, meshloom/config.yaml, the FROM tag of
# meshloom/Dockerfile) and uv.lock, adds the CHANGELOG section, commits exactly
# those files, pushes the branch and opens the PR against main.
#
# It never pushes main and never tags. Once the PR is merged (merge commit) and
# All Quality is green on main, run scripts/build/tag_release.sh X.Y.Z.
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

GH_REPO="${MESHLOOM_GH_REPO:-TwinRocket/meshloom}"
VERSION=""
NOTES_FILE=""
SKIP_QUALITY=0
SKIP_LICENSES=0

usage() {
    cat <<'EOF'
Usage: scripts/build/prepare_release.sh [X.Y.Z] [options]

Opens the release pull request for X.Y.Z (branch release/X.Y.Z). After it is
merged, tag with scripts/build/tag_release.sh X.Y.Z.

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

echo -e "${YELLOW}=== Meshloom release, step 1/2: release pull request ===${NC}"
echo

command -v gh >/dev/null 2>&1 || release_die "The GitHub CLI (gh) is required to open the PR."
if [ -n "$NOTES_FILE" ] && [ ! -f "$NOTES_FILE" ]; then
    release_die "Notes file not found: $NOTES_FILE"
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
BRANCH="release/$VERSION"

# The release branch starts from origin/main as it is now, on a clean tree, so
# the PR carries the release commit and nothing else.
if [ -n "$(git status --porcelain)" ]; then
    release_die "The working tree is not clean; commit or stash first."
fi
git fetch --quiet origin main
if git rev-parse -q --verify "refs/tags/$VERSION" >/dev/null \
    || git ls-remote --exit-code --tags origin "refs/tags/$VERSION" >/dev/null 2>&1; then
    release_die "Tag $VERSION already exists; pick another version."
fi
if git rev-parse -q --verify "refs/heads/$BRANCH" >/dev/null \
    || git ls-remote --exit-code --heads origin "refs/heads/$BRANCH" >/dev/null 2>&1; then
    release_die "Branch $BRANCH already exists (locally or on origin); delete it or reuse its PR."
fi
git switch --quiet -c "$BRANCH" --no-track origin/main
echo -e "${GREEN}On $BRANCH, from origin/main $(git rev-parse --short HEAD).${NC}"
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
PR_BODY_FILE="$(mktemp)"
SECTION_FILE="$(mktemp)"
cleanup() {
    rm -f "$RAW_CHANGELOG_INPUT_FILE" "$FORMATTED_CHANGELOG_INPUT_FILE" "$PR_BODY_FILE" "$SECTION_FILE"
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

echo -e "${YELLOW}Committing changes...${NC}"
# Only the files this script writes: never `git add .`, which would also commit
# whatever else the quality gate's autofixers touched.
git add pyproject.toml uv.lock frontend/package.json meshloom/config.yaml \
    meshloom/Dockerfile CHANGELOG.md
if [ "$SKIP_LICENSES" -eq 0 ]; then
    git add LICENSES.md
fi
git commit -m "Updating changelog + build for $VERSION"
if [ -n "$(git status --porcelain)" ]; then
    echo -e "${RED}Warning: the quality gate left uncommitted changes; they are NOT in the release PR:${NC}"
    git status --short
    echo
fi

echo -e "${YELLOW}Pushing $BRANCH...${NC}"
git push --set-upstream origin "$BRANCH"

release_extract_changelog_section "$REPO_ROOT" "$VERSION" "$SECTION_FILE"
{
    echo "Release $VERSION: version bump (pyproject.toml, uv.lock, frontend/package.json, meshloom/config.yaml, meshloom/Dockerfile), LICENSES.md and changelog."
    echo
    echo "Opened by \`scripts/build/prepare_release.sh\`. Nothing is built or published until the tag exists."
    echo
    echo "### Changelog"
    echo
    tail -n +2 "$SECTION_FILE"
    echo
    echo "### After merging (merge commit)"
    echo
    echo "1. Wait for **All Quality** (\`all-quality\`) to be green on \`main\`."
    echo "2. \`git switch main && git pull --ff-only\`"
    echo "3. \`scripts/build/tag_release.sh $VERSION\` (checks, then pushes the annotated tag after confirmation)."
    echo "4. The tag starts \`release.yml\` and \`docker.yml\`."
} > "$PR_BODY_FILE"

echo -e "${YELLOW}Opening the pull request...${NC}"
gh pr create -R "$GH_REPO" --base main --head "$BRANCH" \
    --title "Release $VERSION" --body-file "$PR_BODY_FILE"
echo

echo -e "${GREEN}=== Release PR for $VERSION is open ===${NC}"
echo -e "Next: merge it with a merge commit, wait for All Quality on main, then run"
echo -e "  ${YELLOW}scripts/build/tag_release.sh $VERSION${NC}"
