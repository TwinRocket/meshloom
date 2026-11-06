# Releasing Meshloom

Developer reference for maintainers. The rules an agent must follow are in the
root `AGENTS.md`, "Releases and signing"; the key procedure and rotation are in
`pkg/keys/README.md`; the scripts are listed in `CONTRIBUTING.md`.

## Key

The key is `pkg/keys/`: primary fingerprint
`D852F2F0892ABF379F52D110FB3EB7BBC43935C8`, RSA-4096, certify only, kept offline.
CI only holds the signing subkey, in the secret `MESHLOOM_REPO_GPG_PRIVATE_KEY`
(see "Who can read the key" below).
Clients and installers trust the public key committed in `pkg/keys` (or embedded
in `install.sh`), never a key fetched at install time. The procedure and the
rotation steps are in `pkg/keys/README.md`.

## Branches

- `main` is the 4.18.x line: hotfixes, CI, docs. Releases are cut from it
  (below) and it publishes the `:edge` image.
- `preversion-5` is the 5.0.0 integration branch: 5.0.0 PRs branch off it and
  target it, under the same rules as `main` (PR, the three required checks,
  merge commit only). It publishes the `:next` image. At the 5.0.0 cut it
  merges into `main` and is deleted. No stable release is ever cut from it, only
  release candidates (below).
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
  "Allow auto-merge" (Settings); the `EDGE_DEPLOY_WEBHOOKS` secret (below).

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
   for amd64 and arm64 (`nfpm-stage` assembles the tree without the key, `nfpm`
   signs it with nFPM), and publishes the GitHub release with `install.sh`. Then
   it calls:
   - `publish-linux-repo.yml`: rebuilds a **signed apt + dnf repository** and
     deploys it to GitHub Pages (`https://twinrocket.github.io/meshloom`), with
     `InRelease`/`Release.gpg`, `repomd.xml.asc`, `meshloom.gpg` and
     `meshloom.asc`. It fails closed on a missing key, a missing package, an
     unsigned package or one of another version in stable. Its `build` job holds
     the key, its `deploy` job the Pages token.
   - `sign-manifest.yml`: attaches `SHA256SUMS(.asc)` (debs, rpms, zip,
     `install.sh`) and `OCI-DIGESTS(.asc)`
     (`ghcr.io/twinrocket/meshloom:X.Y.Z sha256:<index digest>`). It fails
     closed.
   - the Raspberry Pi image job, and a dispatch of `nfpm-armhf.yml`. The armhf
     job reruns the repo publication and the manifest once its package is
     attached.
   - both `linux-repo` and `sign-manifest` take the key as a named secret, never
     `secrets: inherit`.
   - `docs-site.yml`: POSTs the Cloudflare deploy hook
     (`DOCS_SITE_DEPLOY_HOOK`) so meshloom.app rebuilds its docs from the new
     tag. A doc change merged on `main` reaches the site only with the next
     release; `workflow_dispatch` on `docs-site.yml` rebuilds it by hand.
3. `docker.yml`, triggered by the same tag push, builds the multi-arch GHCR
   image (amd64, arm64, armv7): `:edge` and `:main` on `main`; `:next` and
   `:preversion-5` on `preversion-5`; `:latest`, `:X.Y.Z` and `:X.Y` on `X.Y.Z`
   tags; `:X.Y.Z-rc.N` alone on `X.Y.Z-rc.N` tags; `:sha-<short>` on all of them. Its `Deploy` job then POSTs Portainer
   webhooks: on `main` those in the `EDGE_DEPLOY_WEBHOOKS` secret (stacks on
   `docker-compose.dev.yaml`), on `preversion-5` those in `NEXT_DEPLOY_WEBHOOKS`
   (the `meshloom-next` stack on mininode1). It runs on `ubuntu-latest` and sends
   the Cloudflare Access service token from `CF_ACCESS_CLIENT_ID` /
   `CF_ACCESS_CLIENT_SECRET` when set: both Portainers are behind the same Access
   application (mininode1's is published as `mininode1.pmacri.com` through the
   Cloudflare tunnel), and a self-hosted runner is not an option on a public
   repository (a fork pull request could run code on the owner's machine).
   Portainer's git-stack webhook reads no query parameter (there is no
   `?pullimage=true`; checked in Portainer 2.45, `webhook_invoke.go`), so the
   call never forces an image pull: `meshloom-next` only pulls a new `:next` if
   "Re-pull image" is enabled on the stack in the Portainer UI (owner action,
   MSHL-270). The stack must not be changed through the API. An unset webhook
   secret is a notice and a failed call a warning, never a red run.
   Tag builds also get a build provenance attestation. Pull requests build
   without pushing.
4. `install.sh` embeds the public key, its fingerprint, the compose helper and
   the apt pin. After changing any of them, run
   `scripts/setup/sync_installer_embeds.py`; the tests run it with `--check`.

## Release candidates

`scripts/build/publish.sh X.Y.Z-rc.N`, by an admin on `main` or `preversion-5`
equal to its origin, after writing the `[X.Y.Z-rc.N]` section of `CHANGELOG.md`.
It pushes that branch, then the tag. Only this form is accepted: `rc.N` with `N`
from 1, no leading zero, no `beta`. `tag_release.sh` recovers it the same way.

It bumps `pyproject.toml`, `frontend/package.json` and `uv.lock` (which records
the PEP 440 form, `5.0.0rc1`). `meshloom/config.yaml` and the `FROM` of
`meshloom/Dockerfile` stay on the last stable release, and
`check_version_consistency.sh` requires that: Home Assistant reads `config.yaml`
from the default branch, and bumping it would offer the rc to every add-on user.

### What an rc publishes

| Channel | `X.Y.Z-rc.N` |
|---|---|
| GitHub release | Flagged pre-release, `--latest=false`, at creation and at every edit |
| Packages | `meshloom_X.Y.Z~rc.N-1_{amd64,arm64}.deb`, `meshloom-X.Y.Z~rc.N-1.{x86_64,aarch64}.rpm`, signed. GitHub turns `~` into `.` in the asset name; the version inside does not change. No armhf |
| Other assets | `install.sh`, the prebuilt frontend zip, `SHA256SUMS(.asc)`, `OCI-DIGESTS(.asc)`, the Pi Lite image and its manifest |
| Image | `ghcr.io/twinrocket/meshloom:X.Y.Z-rc.N` and `:sha-<short>`, with a provenance attestation |
| apt/dnf | Suite `testing` |

Nothing else moves: not `:latest`, not `:X.Y`, not the apt `stable` suite, not
`releases/latest` (so neither `get.meshloom.app`, nor the updater, nor the
Community mirror, which rejects pre-releases), not the add-on, not the site
(`release.yml` skips `docs-site.yml`, which also ignores a hand-published
pre-release). A dispatch of `docker.yml` on an rc tag is just as safe: `:latest`
depends on the ref being a strict `X.Y.Z` tag, and it refuses any tag `release.yml`
would refuse (`-rc.0`, `-beta.1`).

`publish` attaches exactly the assets above (`list_release_assets.sh`): anything
else in its artifacts stops the release.

`nfpm-armhf.yml` and `rpi-image.yml` refuse an rc. Their recipe comes from the
default branch, not from the rc's commit. The rc's image comes from the
`rpi-image` job of `release.yml`; to redo it, re-run that job.

Versions order correctly everywhere: `4.18.2-1 < 5.0.0~rc.1-1 < 5.0.0~rc.2-1 <
5.0.0-1` for dpkg and rpm, and PEP 440 reads `5.0.0-rc.1` as a pre-release of
`5.0.0`.

### The rc's notes

`[X.Y.Z-rc.N]` in `CHANGELOG.md` becomes the body of the GitHub release
(`create_github_release.sh`) and the annotation of the tag (`publish.sh`,
`git tag -F`, whose default cleanup drops every line starting with `#`, so the tag
has no headings; the GitHub release keeps them). `release_extract_changelog_section`
takes everything from the line starting `## [X.Y.Z-rc.N]` up to the next `## [`.

- Write it for a tester: `### Known limits of this pre-release` near the top, then what a
  user notices, then a `### Français` part with the same content, as in stable sections.
- Keep it well under GitHub's limit of 125,000 characters for a release body. Past it,
  `gh release create` fails after the tag is pushed, with the assets built.
- Links are absolute, to the tag (`.../blob/X.Y.Z-rc.N/docs/...`): a release body does not
  resolve a relative path, and the site is not rebuilt for an rc.
- `publish.sh` leaves an existing section as it is, date included: fix the date before
  running it. The section may already be committed.
- `## [Unreleased]` stays on top for what lands next. `rc.2` gets its own section, and the
  final `X.Y.Z` gets a rewritten one, without the pre-release parts.
- 5.0.0-rc.1's section replaced the development log of the cycle. That log, per feature,
  is `CHANGELOG.md` at commit `3003b5511ef5450762fa3369ddc00637f922b066`: not revised, so
  some entries were superseded later. Check any of it against the code.
- During the rc, all the links of `docs/user/*/prerelease.md` and `docs/user/*/upgrade-4-18.md`
  are absolute GitHub links to `preversion-5`, because `/next/docs` sends a tester to GitHub,
  where a site path (`/en/docs/...`) answers 404. The other pages keep site paths. Put site
  paths back in these two before the final.

### How a tester gets an rc

`get.meshloom.app/next` redirects to the `install.sh` of the rc that the `testing`
suite serves (the `meshloom-website` Worker reads
`apt/dists/testing/main/binary-amd64/Packages`), and answers with a script that changes
nothing when that suite serves a stable version.

- The rc's `install.sh` is `scripts/setup/install.sh` with one line changed, which names
  the rc (`scripts/build/stage_install_sh.sh`). A stable release attaches the file byte
  for byte.
- Packages: it moves the apt or dnf source to `testing` and installs from there, with no
  fallback. armhf is refused: `testing` has no `binary-armhf`. Docker: it pins the rc by
  the digest in its signed `OCI-DIGESTS`, without the helper's `--bootstrap`, which pins
  the latest stable release.
- Updates: there is no channel. The updater only reads `releases/latest`, so an rc machine
  is offered nothing until a newer stable exists, then that release. A tester gets a newer
  rc by running `/next` again. `compose-update` accepts an rc pin, refuses to go from it
  to an older release, and `apply-update` only ever upgrades.
- Every installer from this version on refuses, before writing anything, to go below an
  installed rc. The 4.18 installers do not: under Docker they pin 4.18 over the migrated
  database. `docs/user/{en,fr}/prerelease.md` says not to run them there.
- After the final, an apt or dnf machine stays on `testing`, which serves that final until
  a later rc is published: the updater then announces the stable release and installs that
  rc. The regular installer puts the source back on `stable`. Docker keeps its rc pin until
  the helper applies the release, then follows `releases/latest`.
- Known limits:
  - With no answer from `releases/latest`, the regular installer has no target, so it does not refuse.
  - On dnf, that missing answer exits 0 with the source on `stable`; `/next` restores it.
  - A stale `installer.conf` can refuse a fresh stack after an rc above stable.
  - It can also refuse after the documented return from a 5.1 rc to 5.0.0: treat before the 5.1 cycle.
  - A failed `up` keeps the rc's pin, and its message does not say to run `/next` again, which repairs it.
  - After a failed `up` the helper stays disabled until `/next` is run again.
  - On dnf, the regular installer does not move an rc to the final: `dnf install` does nothing on an installed package.
- There is no return command: testers restore their own copy on a fresh 4.18 install or
  use a new instance (`docs/user/{en,fr}/upgrade-4-18.md`).
- `install.sh` must stay under Linux's limit for one argument (128 KiB, `MAX_ARG_STRLEN`):
  `bash -c "$(curl ...)"` passes the whole file as one, and above it the command does not
  start, with `Argument list too long`. `tests/test_install_prerelease.py` holds it under
  126 000 bytes.

### The repository's suites

GitHub Pages replaces the whole site on every deployment, so **every** run of
`publish-linux-repo.yml` rebuilds both suites from the published releases
(`scripts/build/resolve_linux_repo_channels.py`), whichever release started it.

```
apt/dists/stable/    pool/main/       the X.Y.Z asked for, else GitHub's latest release
apt/dists/testing/   pool/testing/    the newest complete rc newer than stable
                     pool/main/       stable's packages, when there is no such rc
rpm/$basearch/                        stable
rpm/testing/$basearch/                testing
```

- Client lines: `deb [signed-by=...] https://twinrocket.github.io/meshloom/apt testing main`,
  and `baseurl=https://twinrocket.github.io/meshloom/rpm/testing/$basearch`.
- Same key and same `Origin` as stable. The apt pin (`meshloom.pref`) goes by
  host, so a source on `testing` needs no change there.
- `testing` has no `binary-armhf` during an rc.
- An rc in `stable` is refused three times: the resolver takes only a strict
  `X.Y.Z` not flagged pre-release (a tag `5.0.0` flagged pre-release is refused
  too, it used to reach every stable user), `check_linux_repo_packages.sh` requires
  each `.deb` to be exactly `X.Y.Z-1` and each `.rpm` to have that release's name,
  and the two trees are scanned apart.
- Every package served is signed. `check_deb_signed.py` verifies a `.deb`'s origin
  signature against `pkg/keys` (a `.deb` released since 4.18 carries one; dpkg and
  apt never read it), and `check_rpm_signed.py` checks that an `.rpm` has a signature
  header.
- An rc that is incomplete, unflagged, unsigned, of the wrong version or too large
  is left out with a warning, and `testing` serves stable, unless the run asked for
  that rc by name: a broken candidate must not stop a stable publication
  (`fetch_linux_repo_packages.sh`). Stable always fails closed.
- A run that fails deploys nothing, and the live repository stays as it was.
- To withdraw an rc: delete its GitHub release, then re-run the publication.
- A rollback with `-f version=4.18.1` holds only until the next run that names no
  stable release, and an rc is such a run: stable goes back to GitHub's `latest`.
  Move `latest` to the rollback target as well (`gh release edit 4.18.1 --latest`),
  or the next rc undoes it.
- `Date:` in `dists/stable/Release` changes on every deployment, also for an rc.
  To check that stable did not move, compare the hash of
  `dists/stable/main/binary-*/Packages`.

**While `preversion-5` is not merged**, `main` carries the old
`publish-linux-repo.yml`, which knows nothing about `testing`. Any run from `main`
(a 4.18.x release, its armhf job, the fallback `gh workflow run publish-linux-repo.yml
-f version=4.18.2`) deploys a site without that suite. Stable users see nothing,
testers get a 404 on `apt update`. Restore it with
`gh workflow run publish-linux-repo.yml --ref X.Y.Z-rc.N -f version=X.Y.Z-rc.N`, once
the armhf job of that release has finished. Dispatching on `preversion-5` itself
is not an option: the `github-pages` environment allows `main` and tags only.

The size of the site is the other limit: GitHub allows 1 GB for a Pages site, and
measured on 4.18.2 and a 5.0 candidate it is 556 MiB without an rc, 716 MiB with one.
Above 950 MiB of packages, which leaves 50 MiB for the metadata and the keys, the
rc is left out before anything is built, under the rule above.
`build_linux_repo.sh` warns above 900 MiB in any case.

## Who can read the key

No job that runs third-party code holds the key or a write token:

| Workflow | Without the key | With the key |
|---|---|---|
| `release.yml` | `frontend` (npm), `nfpm-stage` (`uv sync`) | `preflight`, `nfpm` |
| `nfpm-armhf.yml` | `frontend` (npm), `assemble` (build backends, under QEMU) | `package` |
| `publish-linux-repo.yml` | `deploy` (Pages token) | `build` |
| `sign-manifest.yml` | | `sign-manifest` |

Between importing the key and erasing it, these jobs run only the checked-out
scripts, `python3 -I` on `check_rpm_signed.py`, `gpg` and nFPM. Every other step,
the third-party actions included (`docker/login-action`, `attest-build-provenance`,
`upload-artifact`, `upload-pages-artifact`), runs before the import or after the
erasure. nFPM (`install_nfpm.sh`) and the standalone Python (`build_nfpm_packages.sh`)
are checked against a pinned sha256, and `setup-uv` has a pinned version: bump the
digest with the version. The assembled tree reaches the signer as data, one tarball
unpacked outside the workspace: a compromised build dependency can end up in the
package, but cannot read the key or replace what handles it. A branch cannot stand
in for a release: these workflows check out `refs/tags/<version>`.

These jobs declare `environment: release-signing`. Before any job naming it,
`release.yml` runs `signing-environment` (`check_signing_environment.py`, no key,
no privilege) and stops unless that environment exists and is limited to selected
refs: GitHub creates a missing environment by itself, with no protection at all.
This also applies to a stable tag once this code is on `main`. It only covers
`release.yml`: dispatching another of these workflows by hand before the setup below
would create the environment unprotected.

**Setup, by the owner, in this order, before the first rc:**

1. Settings > Environments > New environment: `release-signing`.
2. Deployment branches and tags > Selected branches and tags. Add the branch `main`
   and the tag pattern `[0-9]*.[0-9]*.[0-9]*`.
3. In that environment, add the secret `MESHLOOM_REPO_GPG_PRIVATE_KEY`: the same
   value as the repository secret (GitHub does not show it, it comes from the offline
   copy in `pkg/keys/README.md`).
4. **Keep the repository secret.** The workflows on `main` have no `environment:` until
   `preversion-5` merges: without it, a 4.18.x would be built with an empty key.
5. Only once `preversion-5` is on `main`: delete the repository secret.

Until then, `main` also runs its own `nfpm-armhf.yml`, which still builds the frontend
next to the key.
