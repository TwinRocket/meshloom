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
   image (amd64, arm64, armv7): `:edge` and `:main` on `main`; `:latest`,
   `:X.Y.Z` and `:X.Y` on tags; `:sha-<short>` on both. On `main` only, its
   `Deploy edge` job then POSTs the Portainer webhooks listed in the
   `EDGE_DEPLOY_WEBHOOKS` secret (stacks on `docker-compose.dev.yaml`). A
   failure there is a warning, never a red run. Tag builds also get a
   build provenance attestation. Pull requests build without pushing.
4. `install.sh` embeds the public key, its fingerprint, the compose helper and
   the apt pin. After changing any of them, run
   `scripts/setup/sync_installer_embeds.py`; the tests run it with `--check`.
