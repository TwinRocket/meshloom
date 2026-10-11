# Release signing keys

In plain words: every file Meshloom publishes for installation is signed with a
private key that only the owner controls, and this folder holds the matching
public key that your machine uses to check the signature. If a file were
tampered with, the check would fail. This page is for maintainers; users only
benefit from it.

This directory holds the **public** half of the key that signs every Meshloom
release artifact: the `.deb` / `.rpm` packages, the apt and dnf repository
metadata (`InRelease`, `Release.gpg`, `repomd.xml.asc`) and the release manifest
`SHA256SUMS` (`SHA256SUMS.asc`). Clients and installers trust *this directory's
content*, never a key downloaded at install time.

| File | Content | Used by |
|---|---|---|
| `meshloom-archive-keyring.gpg` | binary (dearmored) public keyring | shipped by the package to `/usr/share/keyrings/meshloom-archive-keyring.gpg`; apt `signed-by=`; `gpgv`; published as `meshloom.gpg` on the repo |
| `meshloom.asc` | the same key, ASCII-armored | dnf `gpgkey=`; published as `meshloom.asc` on the repo |
| `FINGERPRINT` | 40 hex characters of the primary key, uppercase, no spaces, one line | embedded in `install.sh`, checked by CI against both files and the CI secret |

## Current key

Primary fingerprint `D852F2F0892ABF379F52D110FB3EB7BBC43935C8` (RSA-4096,
certify only), signing subkey RSA-4096 expiring 2028-10-09. Check with
`gpg --show-keys pkg/keys/meshloom.asc`.

`scripts/build/check_signing_keys.sh` checks that `FINGERPRINT`, the `.gpg`, the
`.asc` and (in CI) the secret `MESHLOOM_REPO_GPG_PRIVATE_KEY` all describe the same
primary key, with a valid, non-expired, non-revoked signing subkey. Release jobs
run it strict; pull-request jobs pass `--allow-placeholder`, which only matters if
the files are ever reset to placeholders.

## Key layout

* **Primary key**: RSA-4096, capability `cert` only, **kept offline**. It never
  goes into CI. It certifies subkeys, extends their expiry and revokes.
* **Signing subkey**: RSA-4096, capability `sign`, 2-year expiry, renewed before
  it lapses. Only this subkey is exported to the GitHub secret
  (`gpg --export-secret-subkeys`), so a CI leak never exposes the primary.
* **RSA-4096 was chosen for maximum compatibility** (RHEL 8, FIPS-mode systems,
  old `gpgv`/`rpm`), over ed25519.
* **The CI subkey has no passphrase.** nFPM cannot use a passphrase-protected
  key ("signing key is encrypted"); the GitHub secret is its protection. The
  import step fails if the subkey is encrypted.
* UID: `Meshloom Release Signing <releases@meshloom.app>`.

## What the signatures protect

* **apt** never verifies a per-`.deb` origin signature. Only `InRelease` /
  `Release.gpg` protect apt clients (they cover the package hashes). The `.deb`
  signature nFPM adds is for `debsig-verify` and audit only.
* **dnf** with `gpgcheck=1` verifies every `.rpm` signature, and with
  `repo_gpgcheck=1` the `repomd.xml.asc`.
* `SHA256SUMS` (+ `.asc`) covers debs, rpms, the zip and `install.sh`;
  `OCI-DIGESTS` (+ `.asc`) pins the image: `ghcr.io/twinrocket/meshloom:X.Y.Z sha256:<index digest>`.

## Create the key: the easy way (recommended)

`pkg/keys/create-release-key.sh` does everything below automatically (French
messages, one question: the backup passphrase). It generates the key in RAM
(`/dev/shm`), writes the three public files and an AES-256 encrypted backup
**outside the repository**, test-restores the backup, checks everything with
`check_signing_keys.sh`, optionally sets the GitHub secret with `gh`, and
shreds its working directory.

```bash
pkg/keys/create-release-key.sh            # output in ~/meshloom-release-key-YYYYMMDD/
```

Options: `--out DIR` chooses the output folder (it must be outside any git
repository), `--no-gh` skips the GitHub secret and leaves the CI subkey in
`DIR/ci-subkey-A-SUPPRIMER.asc` (mode 600), `--yes` asks no question (tests; the
passphrase then comes from `MESHLOOM_KEY_PASSPHRASE`).

Difference with the manual procedure: the primary key itself has no passphrase
inside the throwaway keyring; the offline backup is protected by symmetric
AES-256 encryption with your passphrase instead.

## Create the key manually (once, on an offline-capable machine)

> **Work OUTSIDE the repository checkout.** All secret material lives in a
> dedicated temporary directory; only the three public files are copied into
> `pkg/keys/` at the end. `.gitignore` also blocks `*.SECRET*`, `*-subkey.asc`
> and `*revoke*` as a safety net, but do not rely on it.

```bash
WORK="$(mktemp -d)"; chmod 700 "$WORK"; cd "$WORK"     # NOT inside the repo
export GNUPGHOME="$WORK/gnupg"; mkdir -m 700 "$GNUPGHOME"
mkdir public

# 1. Primary key, certify only, no expiry (the subkeys carry the expiry).
#    Give it a passphrase: it is the offline master.
gpg --quick-generate-key "Meshloom Release Signing <releases@meshloom.app>" rsa4096 cert never
FPR="$(gpg --list-keys --with-colons | awk -F: '/^fpr/ {print $10; exit}')"

# 2. Signing subkey, 2 years.
gpg --quick-add-key "$FPR" rsa4096 sign 2y

# 3. Revocation certificate, stored with the backup (step 6).
#    `gpg --gen-revoke` is INTERACTIVE (reason, comment, confirmations).
gpg --output "meshloom-revoke-$FPR.asc" --gen-revoke "$FPR"

# 4. Public material (the only files that go into the repo).
gpg --export "$FPR"          > public/meshloom-archive-keyring.gpg
gpg --armor --export "$FPR"  > public/meshloom.asc
printf '%s\n' "$FPR"         > public/FINGERPRINT

# 5. Signing subkey only (no primary secret) for the CI secret, WITHOUT passphrase.
#    Work on a copy: `passwd` is interactive (enter the old passphrase, leave the new
#    one EMPTY, confirm "without protection"), and it must not touch the master.
cp -a "$GNUPGHOME" "$WORK/gnupg.ci" && chmod 700 "$WORK/gnupg.ci"
GNUPGHOME="$WORK/gnupg.ci" gpg --edit-key "$FPR"      # gpg> passwd  ... gpg> save
GNUPGHOME="$WORK/gnupg.ci" gpg --armor --export-secret-subkeys "$FPR" > meshloom-ci-subkey.asc
#    Check: the primary is a stub ("sec#") and the subkey signs with no passphrase.
gpg --show-keys meshloom-ci-subkey.asc
T="$(mktemp -d)"; chmod 700 "$T"; GNUPGHOME="$T" gpg --batch --import meshloom-ci-subkey.asc
echo probe | GNUPGHOME="$T" gpg --batch --pinentry-mode loopback --passphrase '' \
    --detach-sign -u "$FPR" -o /dev/null && echo "CI subkey OK"

# 6. Offline backup of the primary secret key (see below).
gpg --armor --export-secret-keys "$FPR" > "meshloom-primary-SECRET-$FPR.asc"

# 7. Copy ONLY the public files into the checkout.
cp public/meshloom-archive-keyring.gpg public/meshloom.asc public/FINGERPRINT /path/to/meshloom/pkg/keys/

# 8. After the offline backup is verified (restore test below) and the CI secret
#    is set: securely delete every working copy.
cd / && find "$WORK" "$T" -type f -exec shred -u {} + && rm -rf "$WORK" "$T"
```


Then, in GitHub (repository secrets, **by the owner only**):

* `MESHLOOM_REPO_GPG_PRIVATE_KEY` = content of `meshloom-ci-subkey.asc`
  (no passphrase secret exists)

Commit `pkg/keys/*` (public files and `FINGERPRINT`), open the PR and run
`scripts/build/check_signing_keys.sh` (strict) locally: it must print `OK`.
Publish the fingerprint out of band too (website, release notes, a signed git tag)
so users can compare.

## Offline backup

* Write `meshloom-primary-SECRET-<FPR>.asc` and the revocation certificate to at
  least **two** encrypted offline media (e.g. LUKS USB sticks) stored in
  different places, plus a printed copy of the revocation certificate
  (`paperkey` works for the secret key).
* Test the backup: import it into a fresh `GNUPGHOME`, sign a file, verify it.
* Never store the primary in GitHub, in CI, in the password manager that syncs
  to the cloud, or on the machine that holds the CI subkey.
* Losing the primary does **not** break existing clients (the public key is
  unchanged) but removes the ability to extend the subkey or rotate cleanly; the
  only recovery is the emergency rotation below.

## Rotation

### Routine: the subkey expires or is rotated (primary unchanged)

1. On the offline machine, extend the expiry (`gpg --edit-key FPR`, `key 1`,
   `expire`) or add a new subkey (`addkey`).
2. Re-export `pkg/keys/meshloom-archive-keyring.gpg` / `.asc` (same primary
   fingerprint, new subkey self-signature) and update the CI secret with the new
   `--export-secret-subkeys` output.
3. Merge and release. The new keyring ships inside the next package (and
   `install.sh`), so clients refresh it with a normal upgrade. The fingerprint
   does not change.

### New primary key (compromise, loss, algorithm change)

1. Generate the new key as above. Have the **old** key sign the release that
   introduces the new keyring (the new keyring is delivered inside a package
   signed by the old key; this is the only chain of trust clients have).
2. During one or more releases ship a keyring file containing **both** keys, and
   sign with the new subkey only once most clients have upgraded.
3. Set `FINGERPRINT` to the **new** primary and update the CI secret. The keyring
   may contain additional (previous) keys; the check only requires that
   `FINGERPRINT` is in the keyring and the `.asc` and matches the CI secret.
4. Publish the revocation certificate of the old key after the transition.
5. If the old key was compromised: revoke immediately, accept that clients that
   cannot reach a fresh package must re-run the installer (`install.sh`, which
   embeds the key), and say so in the release notes.

## Why publishing a signed repository cannot break existing clients

*Historical note, written for the 4.18.0 change that introduced signed
repositories.*

Every install made before this change uses `[trusted=yes]` (apt) or `gpgcheck=0`
(dnf). Both options make the package manager *ignore* signatures, so adding
`InRelease`, `Release.gpg`, `repomd.xml.asc`, signed `.rpm` files and
`meshloom.gpg` / `meshloom.asc` to the repository is invisible to them: they keep
reading `Release` / `repomd.xml` and the packages exactly as before. Signature
enforcement only starts when a client's source is rewritten to `signed-by=` /
`gpgcheck=1` by a package that ships the keyring (release N), and that
package is itself only published by a workflow that fails when the key is missing.
There is no separate republication of the old release: the first signed
publication is **release 4.18.0 itself**, whose repository metadata and rpms are
signed in the same publish run. The already-published `install.sh` switches new
dnf installs to `gpgcheck=1` as soon as `meshloom.asc` appears, which is only safe
if every rpm served as latest is signed; `publish-linux-repo.yml` therefore fails
when an rpm is unsigned.
