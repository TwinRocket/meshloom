#!/usr/bin/env bash
# Step 2 of 2 of a release: tag the merged release commit.
#
# Run on a clean main equal to origin/main, after the PR opened by
# prepare_release.sh is merged. Refuses unless HEAD carries version X.Y.Z in
# every version source, has a CHANGELOG section for it, has the `all-quality`
# check green, and the tag exists neither locally nor on origin. Then creates an
# annotated (unsigned) X.Y.Z tag and, after confirmation, pushes it. The tag
# starts release.yml and docker.yml; nothing is built here.
#
# The tag is deliberately not GPG-signed: the artifacts it produces are signed
# by the release key (pkg/keys), which is what clients verify.
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/build/release_common.sh
source "$SCRIPT_DIR/release_common.sh"

GH_REPO="${MESHLOOM_GH_REPO:-TwinRocket/meshloom}"
QUALITY_CHECK="all-quality"
VERSION=""
DRY_RUN=0
ASSUME_YES=0

usage() {
    cat <<'EOF'
Usage: scripts/build/tag_release.sh X.Y.Z [options]

Creates and pushes the annotated X.Y.Z tag on main's HEAD once the release PR
from scripts/build/prepare_release.sh is merged.

Options:
  --dry-run                 Run every check, create and push nothing
  --yes                     Do not ask before pushing the tag
  --help                    Show this message
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --yes)
            ASSUME_YES=1
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

[ -n "$VERSION" ] || { usage >&2; release_die "The version is required."; }
VERSION="$(release_trim "$VERSION")"
release_validate_version "$VERSION"

echo -e "${YELLOW}=== Meshloom release, step 2/2: tag $VERSION ===${NC}"

# 1. A clean main, equal to origin/main: the tag must point at what was merged.
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[ "$BRANCH" = "main" ] || release_die "Tags are cut from main; you are on '$BRANCH'."
[ -z "$(git status --porcelain)" ] || release_die "The working tree is not clean; commit or stash first."
git fetch --quiet origin main
HEAD_SHA="$(git rev-parse HEAD)"
[ "$HEAD_SHA" = "$(git rev-parse origin/main)" ] \
    || release_die "main is not at origin/main; run 'git pull --ff-only' (and merge the release PR first)."
echo "ok    main is clean and at origin/main ($HEAD_SHA)"

# 2. HEAD is the release: every version source says X.Y.Z, and the changelog has it.
scripts/build/check_version_consistency.sh "$VERSION" >/dev/null \
    || { scripts/build/check_version_consistency.sh "$VERSION" >&2 || true
         release_die "HEAD does not carry version $VERSION; is the release PR merged?"; }
echo "ok    version sources = $VERSION"
TAG_NOTES_FILE="$(mktemp)"
trap 'rm -f "$TAG_NOTES_FILE"' EXIT
release_extract_changelog_section "$REPO_ROOT" "$VERSION" "$TAG_NOTES_FILE"
echo "ok    CHANGELOG.md has [$VERSION]"

# 3. The tag is new, here and on origin.
if git rev-parse -q --verify "refs/tags/$VERSION" >/dev/null; then
    release_die "Tag $VERSION already exists locally."
fi
set +e
git ls-remote --exit-code --tags origin "refs/tags/$VERSION" >/dev/null 2>&1
LS_REMOTE_STATUS=$?
set -e
case "$LS_REMOTE_STATUS" in
    0) release_die "Tag $VERSION already exists on origin." ;;
    2) ;;
    *) release_die "Could not query origin's tags (git ls-remote exited $LS_REMOTE_STATUS)." ;;
esac
echo "ok    tag $VERSION exists neither locally nor on origin"

# 4. All Quality is green on this exact commit (the push run on main).
command -v gh >/dev/null 2>&1 || release_die "The GitHub CLI (gh) is required to read the checks."
CHECK_STATE="$(gh api "repos/$GH_REPO/commits/$HEAD_SHA/check-runs?check_name=$QUALITY_CHECK&per_page=100" \
    --jq '[.check_runs[]] | sort_by(.started_at) | last | if . == null then "missing" else "\(.status)/\(.conclusion)" end')" \
    || release_die "Could not read the checks of $HEAD_SHA on $GH_REPO."
case "$CHECK_STATE" in
    completed/success) ;;
    missing) release_die "No '$QUALITY_CHECK' check on $HEAD_SHA yet; wait for All Quality on main." ;;
    queued/* | in_progress/* | waiting/* | pending/* | requested/*)
        release_die "'$QUALITY_CHECK' is still running on $HEAD_SHA ($CHECK_STATE); try again when it is green." ;;
    *) release_die "'$QUALITY_CHECK' is not green on $HEAD_SHA ($CHECK_STATE)." ;;
esac
echo "ok    $QUALITY_CHECK is green on $HEAD_SHA"
echo

if [ "$DRY_RUN" -eq 1 ]; then
    echo -e "${GREEN}Dry run: every check passed. Would run:${NC}"
    echo "  git tag -a $VERSION $HEAD_SHA -F <CHANGELOG section>"
    echo "  git push origin refs/tags/$VERSION"
    exit 0
fi

echo -e "${YELLOW}Creating annotated tag $VERSION at $HEAD_SHA...${NC}"
git tag -a "$VERSION" "$HEAD_SHA" -F "$TAG_NOTES_FILE"

if [ "$ASSUME_YES" -ne 1 ]; then
    echo "Pushing the tag starts the release: packages, GitHub release, apt/dnf repo, image."
    read -r -p "Push tag $VERSION to origin? [y/N] " ANSWER || ANSWER=""
    case "$ANSWER" in
        y | Y | yes | YES) ;;
        *)
            git tag -d "$VERSION" >/dev/null
            release_die "Aborted; the local tag $VERSION was deleted and nothing was pushed."
            ;;
    esac
fi

git push origin "refs/tags/$VERSION"
echo
echo -e "${GREEN}=== Tag $VERSION pushed ===${NC}"
echo "release.yml and docker.yml now build and publish $VERSION:"
echo "  gh run list -R $GH_REPO --branch $VERSION"
