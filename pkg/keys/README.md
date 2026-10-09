# Release signing keys

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

## Current status: PLACEHOLDERS

The three files are **placeholders** until the owner creates the key (procedure
below). `scripts/build/check_signing_keys.sh` detects them and:

* in release jobs (strict mode) **fails the run** before anything is built or published;
* in pull-request jobs (`--allow-placeholder`) only prints a notice, so packaging
  can still be tested.

A real key is accepted only if `FINGERPRINT`, the `.gpg`, the `.asc` and (in CI)
the secret `MESHLOOM_REPO_GPG_PRIVATE_KEY` all describe the same primary key, and
that key has a valid, non-expired, non-revoked signing subkey.

## Key layout

* **Primary key**: ed25519, capability `cert` only, **kept offline**. It never
  goes into CI. It certifies subkeys, extends their expiry and revokes.
* **Signing subkey**: ed25519, capability `sign`, 2-year expiry, renewed before it
  lapses. Only this subkey is exported to the GitHub secret
  (`gpg --export-secret-subkeys`), so a CI leak never exposes the primary.
* Compatibility note: rpm older than 4.19 (RHEL/Alma/Rocky 9) cannot verify
  EdDSA signatures. Fedora 41+, Debian and Ubuntu are fine. If those distributions
  must be supported, use `rsa4096` for the primary and the subkey instead; nothing
  else in the pipeline changes.

## Create the key (once, on an offline-capable machine)

```bash
export GNUPGHOME="$(mktemp -d)"; chmod 700 "$GNUPGHOME"   # throwaway home

# 1. Primary key, certify only, no expiry (the subkeys carry the expiry).
gpg --quick-generate-key "Meshloom Release Signing <macri.pascal@gmail.com>" ed25519 cert never
FPR="$(gpg --list-keys --with-colons | awk -F: '/^fpr/ {print $10; exit}')"

# 2. Signing subkey, 2 years.
gpg --quick-add-key "$FPR" ed25519 sign 2y

# 3. Revocation certificate, stored with the backup (step 6).
gpg --gen-revoke "$FPR" > "meshloom-revoke-$FPR.asc"

# 4. Public material into the repo.
gpg --export "$FPR"          > pkg/keys/meshloom-archive-keyring.gpg
gpg --armor --export "$FPR"  > pkg/keys/meshloom.asc
printf '%s\n' "$FPR"         > pkg/keys/FINGERPRINT

# 5. Signing subkey only (no primary secret) for the CI secret.
#    Protect it with a passphrase, or leave it empty and skip the passphrase secret.
gpg --armor --export-secret-subkeys "$FPR" > meshloom-ci-subkey.asc
#    Check: the export must show the primary as a stub ("sec#").
gpg --show-keys meshloom-ci-subkey.asc

# 6. Offline backup of the primary secret key (see below), then
#    delete the primary from the working machine.
gpg --armor --export-secret-keys "$FPR" > meshloom-primary-SECRET-$FPR.asc
```

Then, in GitHub (repository secrets, **by the owner only**):

* `MESHLOOM_REPO_GPG_PRIVATE_KEY` = content of `meshloom-ci-subkey.asc`
* `MESHLOOM_REPO_GPG_PASSPHRASE` = its passphrase (omit if none)

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

Every install made before this change uses `[trusted=yes]` (apt) or `gpgcheck=0`
(dnf). Both options make the package manager *ignore* signatures, so adding
`InRelease`, `Release.gpg`, `repomd.xml.asc`, signed `.rpm` files and
`meshloom.gpg` / `meshloom.asc` to the repository is invisible to them: they keep
reading `Release` / `repomd.xml` and the packages exactly as before. Signature
enforcement only starts when a client's source is rewritten to `signed-by=` /
`gpgcheck=1` by a package that ships the keyring (release N), and that
package is itself only published by a workflow that fails when the key is missing.
Hence the order: key created, repository republished (step 0), then release N.
Verify step 0 on a VM with the previous release installed: `apt update` and
`dnf check-update` must still succeed.
