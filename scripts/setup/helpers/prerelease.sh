#!/usr/bin/env bash
# shellcheck shell=bash
# What only a pre-release needs: the way there and the way back. Part of install.sh, which
# carries it compressed (sync_installer_embeds.py) and evals it for those two uses only, so
# nothing here runs on a stable install. It uses that script's functions and variables.
# docs/dev/releasing.md, "Channels".

t_pre() {
    local key="$1"
    shift || true
    case "${ML_LANG}:${key}" in
        en:step_return) echo "Back to stable" ;;
        fr:step_return) echo "Retour à la version stable" ;;
        en:return_switch_q) echo "Move this machine to the stable channel? Only stable versions are offered from now on." ;;
        fr:return_switch_q) echo "Passer cette machine sur le canal stable ? Seules les versions stables seront proposées désormais." ;;
        en:return_switch_done) echo "This machine now follows the stable channel." ;;
        fr:return_switch_done) echo "Cette machine suit maintenant le canal stable." ;;
        en:return_uses_copy) echo "Your database goes back to the copy $1. What changed since then is not kept, and the current database stays beside it as $2." ;;
        fr:return_uses_copy) echo "Votre base revient à la copie $1. Ce qui a changé depuis n'est pas conservé, et la base actuelle reste à côté sous le nom $2." ;;
        en:return_no_copy) echo "No copy of your database was found for this version, so $1 starts on an empty database. The current one stays beside it as $2." ;;
        fr:return_no_copy) echo "Aucune copie de votre base n'existe pour cette version : la $1 démarre donc sur une base vide. La base actuelle reste à côté sous le nom $2." ;;
        en:return_q) echo "Go back to version $1?" ;;
        fr:return_q) echo "Revenir à la version $1 ?" ;;
        en:return_done) echo "Meshloom $1 is back, on the stable channel." ;;
        fr:return_done) echo "Meshloom $1 est de retour, sur le canal stable." ;;
        en:return_unsafe) echo "Something in the data folder is not what it should be: $1. Nothing was changed. Check that folder, then try again." ;;
        fr:return_unsafe) echo "Un élément du dossier de données n'est pas ce qu'il devrait être : $1. Rien n'a été modifié. Vérifiez ce dossier, puis réessayez." ;;
        en:return_copy_damaged) echo "The copy $1 is damaged, so it cannot be used. Nothing was changed, and your current database is still in place." ;;
        fr:return_copy_damaged) echo "La copie $1 est endommagée et ne peut pas servir. Rien n'a été modifié, et votre base actuelle est toujours en place." ;;
        en:return_source_failed) echo "The stable package source could not be reached. The source was put back and nothing else was changed. Try again shortly." ;;
        fr:return_source_failed) echo "La source de paquets stable est injoignable. La source a été remise en place et rien d'autre n'a été modifié. Réessayez dans un instant." ;;
        en:return_rolled_back) echo "The stable package could not be installed. Your database, the package source and the service are back as they were. Run the same command again." ;;
        fr:return_rolled_back) echo "Le paquet stable n'a pas pu être installé. Votre base, la source de paquets et le service sont revenus comme avant. Relancez la même commande." ;;
        en:return_midway) echo "The package step stopped part way. The service is stopped, the source is stable and the database is the copy. Running the same command again repairs this. Your previous database is $1." ;;
        fr:return_midway) echo "L'étape du paquet s'est arrêtée en cours de route. Le service est arrêté, la source est stable et la base est la copie. Relancer la même commande répare cela. Votre base précédente est $1." ;;
        en:return_image_unverified) echo "Could not verify the signed stable image. Nothing was changed. Try again shortly." ;;
        fr:return_image_unverified) echo "Impossible de vérifier l'image stable signée. Rien n'a été modifié. Réessayez dans un instant." ;;
        en:return_up_failed) echo "The stable version and the copy of your database are in place, but the stack did not start. Fix what the log says, then run: docker compose up -d, in $1. Your previous database is $2." ;;
        fr:return_up_failed) echo "La version stable et la copie de votre base sont en place, mais la pile n'a pas démarré. Corrigez ce qu'indique le journal, puis lancez docker compose up -d dans $1. Votre base précédente est $2." ;;
        en:return_manual) echo "This install cannot go back automatically. The steps: $1" ;;
        fr:return_manual) echo "Cette installation ne peut pas revenir en arrière automatiquement. La marche à suivre : $1" ;;
        en:return_stable_unknown) echo "Could not confirm the stable version. Nothing was changed. Try again shortly." ;;
        fr:return_stable_unknown) echo "Impossible de confirmer la version stable. Rien n'a été modifié. Réessayez dans un instant." ;;
        en:return_pull_failed) echo "Could not download the stable image. Nothing was changed." ;;
        fr:return_pull_failed) echo "Impossible de télécharger l'image stable. Rien n'a été modifié." ;;
        en:prerelease_none) echo "No pre-release is available right now. The stable version: /bin/bash -c \"\$(curl -fsSL https://get.meshloom.app)\"" ;;
        fr:prerelease_none) echo "Aucune pré-version n'est disponible pour le moment. La version stable : /bin/bash -c \"\$(curl -fsSL https://get.meshloom.app)\"" ;;
        en:prerelease_armhf) echo "Pre-releases have no 32-bit package, so nothing was changed here. Docker can run them: $1" ;;
        fr:prerelease_armhf) echo "Les pré-versions n'ont pas de paquet 32 bits : rien n'a été modifié ici. Docker peut les faire tourner : $1" ;;
        en:prerelease_no_package) echo "Pre-releases come as apt or dnf packages, or with Docker, so nothing was changed here: $1" ;;
        fr:prerelease_no_package) echo "Les pré-versions existent en paquets apt ou dnf, ou avec Docker : rien n'a été modifié ici. $1" ;;
        en:prerelease_following) echo "This machine now follows pre-releases. Settings > Updates offers the new ones." ;;
        fr:prerelease_following) echo "Cette machine suit maintenant les pré-versions. Réglages > Mises à jour propose les nouvelles." ;;
        en:prerelease_image_failed) echo "Could not verify the signed image of the pre-release. Nothing was changed." ;;
        fr:prerelease_image_failed) echo "Impossible de vérifier l'image signée de la pré-version. Rien n'a été modifié." ;;
        en:way_back_copy) echo "Copy of your database" ;;
        fr:way_back_copy) echo "Copie de votre base" ;;
        en:way_back_docs) echo "Going back, and returning to stable" ;;
        fr:way_back_docs) echo "Revenir en arrière, et retrouver la version stable" ;;
        *) t "$key" "$@" ;;
    esac
}

# Going back by hand: the section of this page. Only reached with a stable newer than 4.18,
# whose site has it.
install_paths_docs_url() {
    if [ "$ML_LANG" = "fr" ]; then
        printf 'https://meshloom.app/docs/deep/install-paths/'
    else
        printf 'https://meshloom.app/en/docs/deep/install-paths/'
    fi
}

# What the signed channel file says the testing suite serves. Fails on a download
# error, a bad signature or anything unexpected in it.
testing_channel_version() {
    local dir version
    dir="$(mktemp -d /tmp/meshloom-channel.XXXXXX)"
    if ! run_soft curl -fsSL --proto '=https' --max-time 20 "$CHANNEL_FILE_URL" -o "${dir}/testing.txt" ||
        ! run_soft curl -fsSL --proto '=https' --max-time 20 "${CHANNEL_FILE_URL}.asc" -o "${dir}/testing.txt.asc" ||
        ! run_soft gpgv --keyring "${RELEASE_KEY_DIR}/meshloom.gpg" "${dir}/testing.txt.asc" "${dir}/testing.txt" ||
        [ "$(sed -n 1p "${dir}/testing.txt")" != "schema=1" ] ||
        [ "$(sed -n 2p "${dir}/testing.txt")" != "channel=testing" ]; then
        rm -rf "$dir"
        return 1
    fi
    version="$(sed -n 's/^version=//p' "${dir}/testing.txt" | head -n 1)"
    rm -rf "$dir"
    printf '%s\n' "$version" | grep -Eq '^[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}-rc\.[1-9][0-9]{0,5}$' || return 1
    printf '%s' "$version"
}

# Digest of the image index for tag $1, from the release's signed OCI-DIGESTS.
signed_image_digest() {
    local tag="$1" dir base="https://github.com/${REPO}/releases/download/${1}" line
    dir="$(mktemp -d /tmp/meshloom-digest.XXXXXX)"
    if ! run_soft curl -fsSL --proto '=https' --max-time 60 "${base}/OCI-DIGESTS" -o "${dir}/OCI-DIGESTS" ||
        ! run_soft curl -fsSL --proto '=https' --max-time 60 "${base}/OCI-DIGESTS.asc" -o "${dir}/OCI-DIGESTS.asc" ||
        ! run_soft gpgv --keyring "${RELEASE_KEY_DIR}/meshloom.gpg" "${dir}/OCI-DIGESTS.asc" "${dir}/OCI-DIGESTS"; then
        rm -rf "$dir"
        return 1
    fi
    line="$(grep -xE "ghcr\.io/twinrocket/meshloom:${tag//./\\.} sha256:[0-9a-f]{64}" "${dir}/OCI-DIGESTS" || true)"
    rm -rf "$dir"
    [ -n "$line" ] || return 1
    printf '%s' "${line##* }"
}

# Before anything is written: is there a pre-release, and can this machine run it?
prepare_prerelease() {
    ensure_log
    if [ "$INSTALL_MODE" = "service" ]; then
        if [ "$(host_arch)" = "armhf" ]; then
            ui_err "$(t_pre prerelease_armhf "$(prerelease_docs_url)")"
            exit 1
        fi
        if [ "$(host_arch)" = "unknown" ] || { [ "$PKG_MGR" != "apt" ] && [ "$PKG_MGR" != "dnf" ]; }; then
            ui_err "$(t_pre prerelease_no_package "$(prerelease_docs_url)")"
            exit 1
        fi
    fi
    ensure_cmd curl "curl"
    ensure_gpgv
    prepare_release_key
    if ! PRERELEASE_VERSION="$(testing_channel_version)"; then
        ui_err "$(t_pre prerelease_none)"
        exit 1
    fi
}

package_db_path() {
    local value=""
    value="$(as_root sed -n 's/^MESHCORE_DATABASE_PATH=//p' /etc/meshloom/meshloom.env 2>/dev/null | tail -n 1 | tr -d "\"'")"
    printf '%s' "${value:-/var/lib/meshloom/meshcore.db}"
}

find_database_copies() {
    local db="$1"
    as_root find "$(dirname "$db")" -maxdepth 1 -type f \
        -name "$(basename "$db").schema*.before-*.db" 2>/dev/null || true
}

# The way back after an upgrade: the copy the server made, and the page about it.
print_way_back() {
    local db="$1" copy="" i=0
    if [ "$UPGRADE_KIND" = "upgrade" ]; then
        # The server makes it first thing at startup; a large database takes longer.
        while [ "$i" -lt 15 ]; do
            copy="$(find_database_copies "$db" | sort | head -n 1)"
            [ -z "$copy" ] || break
            i=$((i + 1))
            sleep 1
        done
    fi
    if [ -n "$copy" ]; then
        ui_dim "  $(t_pre way_back_copy): ${copy}"
    fi
    ui_dim "  $(t_pre way_back_docs): $(prerelease_docs_url)"
}

# Pre-release channel: from the signed `testing` suite and nothing else. No fallback to
# a release asset or a source install, which would quietly be something else.
install_prerelease_package() {
    phase "$(t using_repo)"
    add_signed_repo
    if [ "$PKG_MGR" = "apt" ]; then
        run_quiet as_root apt-get update
        run_quiet as_root apt-get install -y meshloom
    elif rpm -q meshloom >/dev/null 2>&1; then
        # dnf install does nothing on an installed package (dnf5).
        run_quiet as_root dnf -y --refresh upgrade meshloom
    else
        run_quiet as_root dnf -y --refresh install meshloom
    fi
    REPO_HAS_ARCH=1
    write_meshloom_env /etc/meshloom/meshloom.env
    start_meshloom_unit
    persist_installer_state
    ensure_update_helper
    phase_ok
    print_done_native
    print_way_back "$(package_db_path)"
    ui_dim "  $(t_pre prerelease_following)"
}

# The stable version, from the same strict redirect the update helper reads.
stable_release_tag() {
    local location tag
    location="$(curl -fsS --proto '=https' --max-time 20 -o /dev/null -w '%{redirect_url}' \
        "https://github.com/${REPO}/releases/latest")" || return 1
    tag="${location#"https://github.com/${REPO}/releases/tag/"}"
    [ "$tag" != "$location" ] || return 1
    printf '%s\n' "$tag" | grep -Eq '^[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}$' || return 1
    printf '%s' "$tag"
}

# A real SQLite file holding schema $2: its header and its user_version (offset 60).
copy_header_matches() {
    local hex
    hex="$(as_root od -An -v -tx1 -N 64 "$1" 2>/dev/null | tr -d '[:space:]')"
    [ "${#hex}" -eq 128 ] || return 1
    [ "${hex:0:32}" = "53514c69746520666f726d6174203300" ] || return 1
    [ "$((16#${hex:120:8}))" = "$2" ]
}

# The schema in a database's header, empty when the file is absent or not SQLite. A database
# still open with a WAL can be ahead of this: it only matters in the direction it can show.
database_schema() {
    local hex
    hex="$(as_root od -An -v -tx1 -N 64 "$1" 2>/dev/null | tr -d '[:space:]')"
    [ "${#hex}" -eq 128 ] || return 0
    [ "${hex:0:32}" = "53514c69746520666f726d6174203300" ] || return 0
    printf '%s' "$((16#${hex:120:8}))"
}

# The highest schema release $1 reads. Every 4.18 uses 87. Fails for any other: this script
# does not know them, and the steps are in the docs.
stable_schema() {
    case "$1" in
        4.18.*) printf '87' ;;
        *) return 2 ;;
    esac
}

# True when the stable version cannot read this database: it was already migrated, whatever
# the version running says. The usual 4.18.2 command puts that image on a migrated database.
database_above_stable() {
    local target schema
    target="$(stable_schema "$2")" || return 1
    schema="$(database_schema "$1")"
    [ -n "$schema" ] && [ "$schema" -gt "$target" ]
}

# The copy to go back to, chosen by schema number, never by the version in its name
# (MSHL-460): the highest schema the stable version can read, the most recent of equals.
# Returns 2 when this script does not know that schema for the stable version.
select_way_back_copy() {
    local db="$1" stable="$2" target entry n base best="" best_n=-1
    target="$(stable_schema "$stable")" || return 2
    base="$(basename "$db")"
    while IFS= read -r entry; do
        [ -n "$entry" ] || continue
        n="${entry##*/}"
        n="${n#"$base".schema}"
        n="${n%%.*}"
        case "$n" in
            '' | *[!0-9]*) continue ;;
        esac
        [ "$n" -le "$target" ] || continue
        [ "$n" -gt "$best_n" ] || { [ "$n" -eq "$best_n" ] && as_root test "$entry" -nt "$best"; } || continue
        if copy_header_matches "$entry" "$n"; then
            best="$entry"
            best_n="$n"
        fi
    done <<<"$(find_database_copies "$db" | sort)"
    printf '%s' "$best"
}

# Runs a command as uid $1, gid $2. The data folder belongs to the service, which an attacker
# in the service controls: what happens inside it must carry no more rights than that.
as_owner() {
    local uid="$1" gid="$2"
    shift 2
    if [ "$uid" = "$(id -u)" ]; then
        "$@"
    elif command -v setpriv >/dev/null 2>&1; then
        as_root setpriv --reuid="$uid" --regid="$gid" --clear-groups -- "$@"
    else
        return 127
    fi
}

# "uid gid" of a real folder (no link), or nothing.
data_dir_owner() {
    local dir="$1" owner
    as_root test -d "$dir" || return 1
    if as_root test -L "$dir"; then
        return 1
    fi
    owner="$(as_root stat -c '%u %g' "$dir" 2>/dev/null || as_root stat -f '%u %g' "$dir" 2>/dev/null)" || return 1
    case "$owner" in
        *[!0-9\ ]* | '') return 1 ;;
    esac
    printf '%s' "$owner"
}

# Every file the return touches is a regular file or absent. Prints the first that is not.
return_paths_unsafe() {
    local db="$1" copy="$2" aside="$3" dir f
    dir="$(dirname "$db")"
    for f in "$db" "${db}-wal" "${db}-shm" "${db}.migration-in-progress" "${dir}/secrets.key" \
        "${copy:-$db}" "${copy:+${copy%.db}.secrets.key}"; do
        [ -n "$f" ] || continue
        if as_root test -L "$f" || { as_root test -e "$f" && ! as_root test -f "$f"; }; then
            printf '%s' "$f"
            return 0
        fi
    done
    for f in "$aside" "${aside}-wal" "${aside}-shm" "${aside}.secrets.key"; do
        if as_root test -L "$f" || as_root test -e "$f"; then
            printf '%s' "$f"
            return 0
        fi
    done
    return 1
}

# Refuses, with nothing changed, what is not a plain file where one is expected.
check_return_paths() {
    local bad
    if ! data_dir_owner "$(dirname "$1")" >/dev/null; then
        ui_err "$(t_pre return_unsafe "$(dirname "$1")")"
        exit 1
    fi
    if bad="$(return_paths_unsafe "$1" "$2" "$3")"; then
        ui_err "$(t_pre return_unsafe "$bad")"
        exit 1
    fi
}

# The header's page count against the size, then SQLite's own check where something on this
# machine can run it (nothing guarantees a Docker host has one). The copy is read as its owner.
copy_is_intact() {
    local copy="$1" owner hex size page pages rc tool
    owner="$(data_dir_owner "$(dirname "$copy")")" || return 1
    hex="$(as_root od -An -v -tx1 -N 96 "$copy" 2>/dev/null | tr -d '[:space:]')"
    [ "${#hex}" -eq 192 ] || return 1
    page="$((16#${hex:32:4}))"
    [ "$page" -ne 1 ] || page=65536
    pages="$((16#${hex:56:8}))"
    # The page count holds when the change counter (24) equals version-valid-for (92).
    if [ "${hex:48:8}" = "${hex:184:8}" ] && [ "$pages" -gt 0 ]; then
        size="$(as_root sh -c 'wc -c <"$1"' sh "$copy" 2>/dev/null | tr -d '[:space:]')" || return 1
        [ "$size" = "$((page * pages))" ] || return 1
    fi
    # shellcheck disable=SC2086 # "uid gid"
    for tool in /opt/meshloom/.venv/bin/python python3; do
        command -v "$tool" >/dev/null 2>&1 || continue
        rc=0
        as_owner $owner "$tool" -I -c '
import sys, urllib.parse
try:
    import sqlite3
except ImportError:
    sys.exit(3)
try:
    c = sqlite3.connect("file:" + urllib.parse.quote(sys.argv[1]) + "?mode=ro&immutable=1", uri=True)
    sys.exit(0 if c.execute("PRAGMA quick_check(1)").fetchone()[0] == "ok" else 1)
except sqlite3.Error:
    sys.exit(1)
' "$copy" >/dev/null 2>&1 || rc=$?
        case "$rc" in
            0) return 0 ;;
            1) return 1 ;;
        esac
    done
    if command -v sqlite3 >/dev/null 2>&1; then
        # shellcheck disable=SC2086 # "uid gid"
        [ "$(as_owner $owner sqlite3 "file:${copy}?mode=ro&immutable=1" 'PRAGMA quick_check(1)' 2>/dev/null)" = "ok" ]
        return
    fi
    return 0
}

# Puts the copy in place of the database (MSHL-460): -wal, -shm and the migration marker go,
# and the current database stays beside it. Without a copy, the next start is empty. Runs as
# the owner of the folder, and refuses anything but plain files: a link planted there leads
# nowhere that owner could not already reach. Nothing moves until the new files are written.
restore_database() {
    local db="$1" copy="$2" aside="$3" owner
    owner="$(data_dir_owner "$(dirname "$db")")" || return 1
    # shellcheck disable=SC2086 # "uid gid"
    as_owner $owner sh -c '
set -eu
db=$1 copy=$2 aside=$3
dir=$(dirname "$db")
secrets=$dir/secrets.key
plain() { [ ! -L "$1" ] && { [ ! -e "$1" ] || [ -f "$1" ]; }; }
absent() { [ ! -L "$1" ] && [ ! -e "$1" ]; }
real() { [ ! -L "$1" ] && [ -f "$1" ]; }
for f in "$db" "$db-wal" "$db-shm" "$db.migration-in-progress" "$secrets"; do plain "$f"; done
for f in "$aside" "$aside-wal" "$aside-shm" "$aside.secrets.key"; do absent "$f"; done
umask 077
new_db="" new_key=""
trap "rm -f \"\$new_db\" \"\$new_key\"" EXIT
if [ -n "$copy" ]; then
    real "$copy"
    new_db=$(mktemp "$db.restoring.XXXXXX")
    cat "$copy" >"$new_db"
    key=${copy%.db}.secrets.key
    plain "$key"
    if [ -f "$key" ]; then
        new_key=$(mktemp "$db.restoring.XXXXXX")
        cat "$key" >"$new_key"
    fi
fi
for ext in "" -wal -shm; do
    if [ -e "$db$ext" ]; then
        mv -f "$db$ext" "$aside$ext"
        [ -L "$aside$ext" ] || chmod 0600 "$aside$ext"
    fi
done
rm -f "$db.migration-in-progress"
if [ -n "$new_db" ]; then
    mv -f "$new_db" "$db"
    new_db=""
fi
if [ -n "$new_key" ]; then
    if [ -e "$secrets" ]; then
        mv -f "$secrets" "$aside.secrets.key"
    fi
    mv -f "$new_key" "$secrets"
    new_key=""
fi
' sh "$db" "$copy" "$aside"
}

# The reverse, when the package step failed: the database that was running comes back.
undo_restore_database() {
    local db="$1" copy="$2" aside="$3" owner
    owner="$(data_dir_owner "$(dirname "$db")")" || return 1
    # shellcheck disable=SC2086 # "uid gid"
    as_owner $owner sh -c '
set -eu
db=$1 copy=$2 aside=$3
secrets=$(dirname "$db")/secrets.key
# The swap never began: what is in place is what was running.
[ -e "$aside" ] || exit 0
rm -f "$db" "$db-wal" "$db-shm"
for ext in "" -wal -shm; do
    if [ -f "$aside$ext" ] && [ ! -L "$aside$ext" ]; then
        mv -f "$aside$ext" "$db$ext"
    fi
done
if [ -f "$aside.secrets.key" ] && [ ! -L "$aside.secrets.key" ]; then
    mv -f "$aside.secrets.key" "$secrets"
elif [ -n "$copy" ] && [ -f "${copy%.db}.secrets.key" ]; then
    rm -f "$secrets"
fi
' sh "$db" "$copy" "$aside"
}

# Downloads the package of release $1 for this machine into $2, checked against the
# release's signed SHA256SUMS. The repository holds one version only, the release
# assets all of them. Prints the file.
fetch_stable_package() {
    local tag="$1" dir="$2" suffix ext name expected file
    if [ "$PKG_MGR" = "apt" ]; then
        suffix="_$(host_arch).deb"
        ext="deb"
    else
        suffix=".$(rpm_arch).rpm"
        ext="rpm"
    fi
    fetch_signed_manifest "$tag" "$dir" || return 1
    name="$(awk -v s="$suffix" '{ f = $2; sub(/^\*/, "", f) } length(f) > length(s) && substr(f, length(f) - length(s) + 1) == s { print f }' \
        "${dir}/SHA256SUMS" | head -n 1)"
    case "$name" in
        meshloom*) ;;
        *) return 1 ;;
    esac
    case "$name" in
        *[!A-Za-z0-9._+~-]*) return 1 ;;
    esac
    expected="$(awk -v f="$name" '$2 == f || $2 == "*" f { print $1 }' "${dir}/SHA256SUMS" | head -n 1)"
    file="${dir}/meshloom.${ext}"
    run_soft curl -fL --proto '=https' --max-time 300 \
        "https://github.com/${REPO}/releases/download/${tag}/${name}" -o "$file" || return 1
    pkg_file_is_valid "$file" "$ext" || return 1
    [ "$(sha256_of "$file")" = "$expected" ] || return 1
    printf '%s' "$file"
}

return_screen() {
    STEP_TOTAL=1
    ui_screen "$(t_pre step_return)" 1
}

package_source_file() {
    if [ "$PKG_MGR" = "apt" ]; then
        printf '/etc/apt/sources.list.d/meshloom.list'
    else
        printf '/etc/yum.repos.d/meshloom.repo'
    fi
}

# The source as it is now, so that a failure can put it back.
save_package_source() {
    SAVED_SOURCE="$(mktemp /tmp/meshloom-source.XXXXXX)"
    if ! as_root cat "$(package_source_file)" >"$SAVED_SOURCE" 2>/dev/null; then
        rm -f "$SAVED_SOURCE"
        SAVED_SOURCE=""
    fi
}

restore_package_source() {
    if [ -n "${SAVED_SOURCE:-}" ]; then
        as_root sh -c 'cat "$1" > "$2"' sh "$SAVED_SOURCE" "$(package_source_file)"
        rm -f "$SAVED_SOURCE"
        SAVED_SOURCE=""
        if [ "$PKG_MGR" = "apt" ]; then
            run_soft as_root apt-get update || true
        fi
    fi
}

# Moves the source to stable and reads it. On failure the source is back as it was.
switch_source_to_stable() {
    save_package_source
    add_signed_repo
    if [ "$PKG_MGR" = "apt" ] && ! run_soft as_root apt-get update; then
        restore_package_source
        ui_err "$(t_pre return_source_failed)"
        printf '  %s: %s\n' "$(t log_at)" "$INSTALL_LOG" >&2
        exit 1
    fi
}

# Nothing to undo: the version is not newer than stable, only the channel moves.
return_package_channel_only() {
    local rc=0
    if ! ui_yesno "$(t_pre return_switch_q)" y; then
        printf '\n%s\n' "$(t cancelled)"
        exit 130
    fi
    phase "$(t using_repo)"
    switch_source_to_stable
    if [ "$PKG_MGR" = "apt" ]; then
        run_soft as_root apt-get install -y --only-upgrade meshloom || rc=$?
    else
        run_soft as_root dnf -y --refresh upgrade meshloom || rc=$?
    fi
    if [ "$rc" -ne 0 ]; then
        restore_package_source
        ui_err "$(t_pre return_source_failed)"
        printf '  %s: %s\n' "$(t log_at)" "$INSTALL_LOG" >&2
        exit 1
    fi
    rm -f "${SAVED_SOURCE:-}"
    phase_ok
    ui_ok "  $(t_pre return_switch_done)"
}

return_package() {
    local stable="$1" installed db copy="" rc=0 aside tmpdir="" file="" downgrade=0
    installed="$(package_installed_version)"
    db="$(package_db_path)"
    return_screen
    if version_gt "$installed" "$stable"; then
        downgrade=1
    elif ! database_above_stable "$db" "$stable"; then
        return_package_channel_only
        return
    fi
    copy="$(select_way_back_copy "$db" "$stable")" || rc=$?
    if [ "$rc" -ne 0 ]; then
        ui_err "$(t_pre return_manual "$(install_paths_docs_url)")"
        exit 1
    fi
    aside="${db}.aside-${installed}-$(date +%Y%m%d-%H%M%S)"
    check_return_paths "$db" "$copy" "$aside"
    if [ -n "$copy" ] && ! copy_is_intact "$copy"; then
        ui_err "$(t_pre return_copy_damaged "$(basename "$copy")")"
        exit 1
    fi
    if [ "$downgrade" = 1 ]; then
        ensure_gpgv
        prepare_release_key
        tmpdir="$(mktemp -d /tmp/meshloom-return.XXXXXX)"
        if ! file="$(fetch_stable_package "$stable" "$tmpdir")"; then
            ui_err "$(t sig_failed)"
            printf '  %s: %s\n' "$(t log_at)" "$INSTALL_LOG" >&2
            rm -rf "$tmpdir"
            exit 1
        fi
    fi
    if [ -n "$copy" ]; then
        ui_wrap 2 70 "$(t_pre return_uses_copy "$(basename "$copy")" "$(basename "$aside")")"
    else
        ui_wrap 2 70 "$(t_pre return_no_copy "$stable" "$(basename "$aside")")"
    fi
    printf '\n'
    if ! ui_yesno "$(t_pre return_q "$stable")" "$([ -n "$copy" ] && echo y || echo n)"; then
        [ -z "$tmpdir" ] || rm -rf "$tmpdir"
        printf '\n%s\n' "$(t cancelled)"
        exit 130
    fi
    phase "$(t working)"
    switch_source_to_stable
    as_root systemctl stop meshloom 2>/dev/null || true
    if ! restore_database "$db" "$copy" "$aside"; then
        return_package_undo "$db" "$copy" "$aside" "$tmpdir"
    fi
    if [ "$downgrade" = 1 ]; then
        # The package's own scripts restart the service: the database is ready by then.
        rc=0
        if [ "$PKG_MGR" = "apt" ]; then
            run_soft as_root apt-get install -y --allow-downgrades "$file" || rc=$?
        else
            run_soft as_root dnf -y downgrade "$file" || rc=$?
        fi
        if [ "$rc" -ne 0 ]; then
            if [ "$(package_installed_version)" = "$installed" ]; then
                return_package_undo "$db" "$copy" "$aside" "$tmpdir"
            fi
            rm -rf "$tmpdir"
            rm -f "${SAVED_SOURCE:-}"
            ui_err "$(t_pre return_midway "$(basename "$aside")")"
            printf '  %s: %s\n' "$(t log_at)" "$INSTALL_LOG" >&2
            exit 1
        fi
        rm -rf "$tmpdir"
    fi
    rm -f "${SAVED_SOURCE:-}"
    # The unit may be at its start limit (StartLimitBurst).
    as_root systemctl reset-failed meshloom 2>/dev/null || true
    start_meshloom_unit
    TARGET_VERSION="$stable"
    persist_installer_state
    phase_ok
    ui_ok "  $(t_pre return_done "$stable")"
}

# Nothing is lost: the running database, the source and the service come back.
return_package_undo() {
    local db="$1" copy="$2" aside="$3" tmpdir="$4"
    undo_restore_database "$db" "$copy" "$aside" || true
    restore_package_source
    [ -z "$tmpdir" ] || rm -rf "$tmpdir"
    as_root systemctl reset-failed meshloom 2>/dev/null || true
    as_root systemctl start meshloom 2>/dev/null || true
    ui_err "$(t_pre return_rolled_back)"
    printf '  %s: %s\n' "$(t log_at)" "$INSTALL_LOG" >&2
    exit 1
}

# Writes MESHLOOM_IMAGE=$2 in the stack's .env, in one step, keeping the other lines.
compose_pin_env() {
    local env_file="${1}/.env" tmp
    tmp="$(mktemp /tmp/meshloom-env-new.XXXXXX)"
    {
        printf 'MESHLOOM_IMAGE=%s\n' "$2"
        as_root grep -v '^MESHLOOM_IMAGE=' "$env_file" 2>/dev/null || true
    } >"$tmp"
    as_root sh -c 'cat "$1" > "$2"' sh "$tmp" "$env_file"
    rm -f "$tmp"
}

# The signed stable image, downloaded without touching the stack: the variable wins over .env.
# Fails, with nothing changed and the reason said.
compose_pull_stable() {
    local stable="$1" dir="$2" dc="$3" digest
    ensure_gpgv
    prepare_release_key
    if ! digest="$(signed_image_digest "$stable")"; then
        ui_err "$(t_pre return_image_unverified)"
        printf '  %s: %s\n' "$(t log_at)" "$INSTALL_LOG" >&2
        return 1
    fi
    RETURN_IMAGE="${GHCR_IMAGE}:${stable}@${digest}"
    # shellcheck disable=SC2086 # "docker compose"
    if ! (cd "$dir" && run_soft as_root env MESHLOOM_IMAGE="$RETURN_IMAGE" $dc pull); then
        ui_err "$(t_pre return_pull_failed)"
        printf '  %s: %s\n' "$(t log_at)" "$INSTALL_LOG" >&2
        return 1
    fi
}

# .env, the helper's env and its status as they are, for compose_put_back.
compose_save_state() {
    local dir="$1" f name
    COMPOSE_SAVED="$(mktemp -d /tmp/meshloom-compose-saved.XXXXXX)"
    for f in "${dir}/.env" /etc/meshloom/compose-update.env "${dir}/update-status/status.json"; do
        name="$(basename "$f")"
        if as_root test -f "$f"; then
            as_root cat "$f" >"${COMPOSE_SAVED}/${name}"
        fi
    done
}

compose_put_back() {
    local dir="$1" managed="$2" f name
    for f in "${dir}/.env" /etc/meshloom/compose-update.env "${dir}/update-status/status.json"; do
        name="$(basename "$f")"
        if [ -f "${COMPOSE_SAVED}/${name}" ]; then
            as_root sh -c 'cat "$1" > "$2"' sh "${COMPOSE_SAVED}/${name}" "$f"
        fi
    done
    rm -rf "$COMPOSE_SAVED"
    if [ "$managed" = 1 ]; then
        enable_compose_update_helper
    fi
}

# Moves the stack to the stable image already pulled. On failure everything is put back.
compose_commit_stable() {
    local dir="$1" managed="$2" dc="$3"
    compose_save_state "$dir"
    if [ "$managed" = 1 ]; then
        compose_pin_env "$dir" "$RETURN_IMAGE"
        if ! COMPOSE_BOOTSTRAP_SOFT=1 install_compose_update_helper "$dir"; then
            compose_put_back "$dir" "$managed"
            ui_err "$(t_pre return_image_unverified)"
            return 1
        fi
        return 0
    fi
    write_unmanaged_env "$dir"
    # shellcheck disable=SC2086 # "docker compose"
    if ! (cd "$dir" && run_soft as_root $dc pull); then
        compose_put_back "$dir" "$managed"
        ui_err "$(t_pre return_pull_failed)"
        return 1
    fi
}

return_compose() {
    local stable="$1" dir="$2" installed="" db copy="" rc=0 aside managed=0 dc
    INSTALL_DIR="$dir"
    COMPOSE_DIR_SAVED="$dir"
    dc="$(compose_cmd)"
    installed="$(compose_image_version "${dir}/docker-compose.yml" || true)"
    db="${dir}/data/meshcore.db"
    if [ -f /etc/meshloom/compose-update.env ] && compose_helper_possible "$dir"; then
        managed=1
    fi
    return_screen
    # The pin alone does not say: the usual stable command of 4.18 pins it over a migrated database.
    if { [ -z "$installed" ] || ! version_gt "$installed" "$stable"; } &&
        ! database_above_stable "$db" "$stable"; then
        if ! ui_yesno "$(t_pre return_switch_q)" y; then
            printf '\n%s\n' "$(t cancelled)"
            exit 130
        fi
        phase "$(t compose_secure)"
        compose_pull_stable "$stable" "$dir" "$dc" || exit 1
        compose_commit_stable "$dir" "$managed" "$dc" || exit 1
        rm -rf "$COMPOSE_SAVED"
        # shellcheck disable=SC2086 # "docker compose"
        (cd "$dir" && run_quiet as_root $dc up -d)
        if [ "$managed" = 1 ]; then
            enable_compose_update_helper
        fi
        phase_ok
        ui_ok "  $(t_pre return_switch_done)"
        return
    fi
    copy="$(select_way_back_copy "$db" "$stable")" || rc=$?
    if [ "$rc" -ne 0 ] || ! as_root test -e "$db"; then
        ui_err "$(t_pre return_manual "$(install_paths_docs_url)")"
        exit 1
    fi
    aside="${db}.aside-${installed:-unknown}-$(date +%Y%m%d-%H%M%S)"
    check_return_paths "$db" "$copy" "$aside"
    if [ -n "$copy" ] && ! copy_is_intact "$copy"; then
        ui_err "$(t_pre return_copy_damaged "$(basename "$copy")")"
        exit 1
    fi
    if [ -n "$copy" ]; then
        ui_wrap 2 70 "$(t_pre return_uses_copy "$(basename "$copy")" "$(basename "$aside")")"
    else
        ui_wrap 2 70 "$(t_pre return_no_copy "$stable" "$(basename "$aside")")"
    fi
    printf '\n'
    if ! ui_yesno "$(t_pre return_q "$stable")" "$([ -n "$copy" ] && echo y || echo n)"; then
        printf '\n%s\n' "$(t cancelled)"
        exit 130
    fi
    phase "$(t working)"
    # Nothing is written until the signed image is here.
    compose_pull_stable "$stable" "$dir" "$dc" || exit 1
    compose_commit_stable "$dir" "$managed" "$dc" || exit 1
    # shellcheck disable=SC2086 # "docker compose"
    if ! (cd "$dir" && run_soft as_root $dc stop) || ! restore_database "$db" "$copy" "$aside"; then
        compose_put_back "$dir" "$managed"
        # shellcheck disable=SC2086 # "docker compose"
        (cd "$dir" && run_soft as_root $dc up -d) || true
        ui_err "$(t_pre return_unsafe "$db")"
        exit 1
    fi
    rm -rf "$COMPOSE_SAVED"
    # shellcheck disable=SC2086 # "docker compose"
    if ! (cd "$dir" && run_soft as_root $dc up -d); then
        ui_err "$(t_pre return_up_failed "$dir" "$(basename "$aside")")"
        printf '  %s: %s\n' "$(t log_at)" "$INSTALL_LOG" >&2
        exit 1
    fi
    if [ "$managed" = 1 ]; then
        enable_compose_update_helper
    fi
    TARGET_VERSION="$stable"
    persist_installer_state
    phase_ok
    ui_ok "  $(t_pre return_done "$stable")"
}

# `--channel stable`: this machine leaves the pre-release channel. A version newer than
# stable goes back through the copy made before the upgrade; otherwise only the channel moves.
return_to_stable() {
    local stable dir
    ensure_log
    ensure_cmd curl "curl"
    if ! stable="$(stable_release_tag)"; then
        ui_err "$(t_pre return_stable_unknown)"
        exit 1
    fi
    if package_installed_version >/dev/null; then
        return_package "$stable"
        return
    fi
    dir="$(saved_compose_dir)"
    if [ -n "$dir" ] && [ -f "${dir}/docker-compose.yml" ]; then
        return_compose "$stable" "$dir"
        return
    fi
    ui_err "$(t_pre return_manual "$(install_paths_docs_url)")"
    exit 1
}
