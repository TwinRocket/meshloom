# Releasing Meshloom

Developer reference for maintainers. The rules an agent must follow are in the
root `AGENTS.md`, "Releases and signing"; the key procedure and rotation are in
`pkg/keys/README.md`; the scripts are listed in `CONTRIBUTING.md`.

## Key

The key is `pkg/keys/`: primary fingerprint
`D852F2F0892ABF379F52D110FB3EB7BBC43935C8`, RSA-4096, certify only, kept offline.
CI only holds the signing subkey, in the secret `MESHLOOM_REPO_GPG_PRIVATE_KEY`.
Clients and installers trust the public key committed in `pkg/keys` (or embedded
in `install.sh`), never a key fetched at install time. The procedure and the
rotation steps are in `pkg/keys/README.md`.

## Branches

- `main` is the 4.18.x line: hotfixes, CI, docs. Releases are cut from it
  (below) and it publishes the `:edge` image.
- `preversion-5` is the 5.0.0 integration branch: 5.0.0 PRs branch off it and
  target it, under the same rules as `main` (PR, the three required checks,
  merge commit only). It publishes the `:next` image. At the 5.0.0 cut it
  merges into `main` and is deleted; no release is ever cut from it.
- `forward-merge.yml` keeps `preversion-5` up to date: every push to `main`
  that `preversion-5` does not already contain (compare API) reaches it through
  a PR `main → preversion-5`. The job opens it with `GITHUB_TOKEN`, or reuses
  the open one from `TwinRocket:main`, and enables auto-merge with the merge
  method (a merge commit, never a rebase or a force push). It does nothing
  while `preversion-5` does not exist (a 404); any other lookup error is a red
  run.
- Why a PR: the `preversion-5` ruleset (25317546, the same rules and checks as
  `main`, admin bypass only) requires one, and GitHub refuses GitHub Actions as
  a bypass actor on a repository ruleset (organisation rulesets need GitHub
  Team). The required checks are read on the PR head, which is `main`'s
  commit: the push run on `main` reports `all-quality` and the two `analyze`
  checks there. A PR opened with `GITHUB_TOKEN` starts no `pull_request`
  workflow, and needs none.
- A merge made with `GITHUB_TOKEN` (auto-merge included, since the job enabled
  it) starts no push workflow on `preversion-5`. So the job waits for the merge
  (polling, at most 60 minutes), then dispatches `docker.yml` (quality,
  `:next`, deploy) and `codeql.yml` on `preversion-5`. A failed dispatch is a
  red run.
- The run goes red, with a comment on the PR, when the PR has conflicts, when
  auto-merge cannot be enabled or GitHub switches it off (a failed merge), when
  the PR is closed, or when it is not merged within the wait (a required check
  red on `main`). If the PR merges later, after the wait, dispatch `docker.yml`
  and `codeql.yml` on `preversion-5` by hand (`gh workflow run docker.yml
  --ref preversion-5`).
- Resolve a conflict on a branch of your own: `git switch -c fm/main
  origin/main && git merge origin/preversion-5`, keep `preversion-5`'s side
  for 5.0.0 content and `main`'s for the 4.18.x lines, push, open a PR into
  `preversion-5` (merge commit). Never use the web conflict editor on the
  `main → preversion-5` PR: it would commit the resolution to `main`. Once your
  PR is merged, the `main → preversion-5` PR has nothing left and GitHub marks
  it merged. Expect conflicts from release commits (`publish.sh`: the version
  files, `uv.lock`, `CHANGELOG.md`, `LICENSES.md`) whenever `preversion-5` has
  changed the same lines. A `main` commit touching `.github/workflows/` may
  also be refused at merge time (GitHub limits what `GITHUB_TOKEN` may change
  there): auto-merge is switched off, the run goes red, and an admin merges
  the PR by hand.
- What an admin configures, outside the repository files: the `preversion-5`
  ruleset; "Allow GitHub Actions to create and approve pull requests" and
  "Allow auto-merge" (Settings); the `NEXT_DEPLOY_WEBHOOKS` secret (below).

## Chain

1. A release is one command, run by an admin on `main`:
   `scripts/build/publish.sh X.Y.Z`. It builds and publishes nothing itself.
   - Usual flow: write the `[X.Y.Z]` section of `CHANGELOG.md` without
     committing it, then run the script. That edit is the only local change it
     accepts (`release_tree_clean_but_changelog`); it then leaves the section
     as-is. Without a section, it asks for the bullets (or `--notes-file`).
   - It refuses unless you are on `main` equal to `origin/main` and the tag
     exists neither locally nor on origin. It runs the gate, regenerates
     `LICENSES.md`, bumps the version sources (`pyproject.toml`,
     `frontend/package.json`, `meshloom/config.yaml`, the `FROM` tag in
     `meshloom/Dockerfile`) and `uv.lock`, checks them with
     `check_version_consistency.sh`, commits exactly those files,
     `CHANGELOG.md` and `LICENSES.md` (unless `--skip-licenses`) ("Updating changelog + build for X.Y.Z"), pushes `main`,
     then creates and pushes an annotated, unsigned tag (the artifacts are what
     the release key signs). The tag is pushed only after `main` is.
   - `scripts/build/tag_release.sh X.Y.Z` is the recovery tool for a run that
     pushed `main` but not the tag. It refuses unless you are on a clean `main`
     equal to `origin/main`, HEAD carries `X.Y.Z` and a CHANGELOG section, the
     `all-quality` check is green on HEAD and the tag is new, then pushes the
     tag after confirmation (`--dry-run` runs the checks only).
2. A tag `X.Y.Z` triggers `release.yml` (`workflow_dispatch` only re-publishes
   an existing tag: `gh workflow run release.yml --ref X.Y.Z`; the workflow and
   `create_github_release.sh` never create a tag). That workflow runs the quality gate,
   then `preflight`: `check_version_consistency.sh` and a check that the secret
   matches `pkg/keys`. Then it builds the frontend zip and the nFPM `.deb`/`.rpm`
   for amd64 and arm64, signed by nFPM, and publishes the GitHub release with
   `install.sh`. Then it calls:
   - `publish-linux-repo.yml`: rebuilds a **signed apt + dnf repository** and
     deploys it to GitHub Pages (`https://twinrocket.github.io/meshloom`), with
     `InRelease`/`Release.gpg`, `repomd.xml.asc`, `meshloom.gpg` and
     `meshloom.asc`. It fails closed on a missing key, a missing package or an
     unsigned rpm.
   - `sign-manifest.yml`: attaches `SHA256SUMS(.asc)` (debs, rpms, zip,
     `install.sh`) and `OCI-DIGESTS(.asc)`
     (`ghcr.io/twinrocket/meshloom:X.Y.Z sha256:<index digest>`). It fails
     closed.
   - the Raspberry Pi image job, and a dispatch of `nfpm-armhf.yml`. The armhf
     job reruns the repo publication and the manifest once its package is
     attached.
   - `docs-site.yml`: POSTs the Cloudflare deploy hook
     (`DOCS_SITE_DEPLOY_HOOK`) so meshloom.app rebuilds its docs from the new
     tag. A doc change merged on `main` reaches the site only with the next
     release; `workflow_dispatch` on `docs-site.yml` rebuilds it by hand.
3. `docker.yml`, triggered by the same tag push, builds the multi-arch GHCR
   image (amd64, arm64, armv7): `:edge` and `:main` on `main`; `:next` and
   `:preversion-5` on `preversion-5`; `:latest`, `:X.Y.Z` and `:X.Y` on tags;
   `:sha-<short>` on all of them. Its `Deploy` job then POSTs Portainer
   webhooks: on `main`, those in the `EDGE_DEPLOY_WEBHOOKS` secret (stacks on
   `docker-compose.dev.yaml`); on `preversion-5`, those in
   `NEXT_DEPLOY_WEBHOOKS` (the stacks that run `:next`). Both send the
   Cloudflare Access service token from `CF_ACCESS_CLIENT_ID` /
   `CF_ACCESS_CLIENT_SECRET` when set (Portainer is behind Access). An unset
   webhook secret is a notice and a failed call a warning, never a red run.
   Tag builds also get a build provenance attestation. Pull requests build
   without pushing.
4. `install.sh` embeds the public key, its fingerprint, the compose helper and
   the apt pin. After changing any of them, run
   `scripts/setup/sync_installer_embeds.py`; the tests run it with `--check`.
