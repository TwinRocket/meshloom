#!/usr/bin/env bash
# Retired: a release is now a pull request, then a tag. Neither step pushes main.
set -euo pipefail
cat >&2 <<'MSG'
scripts/build/publish.sh is retired: main only changes through pull requests.

  1. scripts/build/prepare_release.sh X.Y.Z   opens the release PR (branch release/X.Y.Z)
  2. merge it (merge commit), wait for All Quality on main, git pull --ff-only
  3. scripts/build/tag_release.sh X.Y.Z       checks, then pushes the annotated tag

The tag starts release.yml and docker.yml, as before.
MSG
exit 1
