#!/usr/bin/env bash
# Crée la clé de signature des releases Meshloom, de bout en bout.
#
# Ce que fait le script (aucune connaissance de GPG nécessaire) :
#   1. génère une clé maître RSA-4096 (certification seulement) et une
#      sous-clé de signature RSA-4096 valable 2 ans, dans un dossier
#      temporaire en mémoire (/dev/shm), JAMAIS dans le dépôt ;
#   2. écrit les 3 fichiers PUBLICS à committer dans pkg/keys/ ;
#   3. écrit une SAUVEGARDE chiffrée (clé maître + certificat de révocation)
#      protégée par une phrase secrète que vous choisissez, puis vérifie
#      qu'elle se restaure et qu'elle signe ;
#   4. (optionnel) envoie la sous-clé de CI dans le secret GitHub
#      MESHLOOM_REPO_GPG_PRIVATE_KEY via `gh` ;
#   5. efface le dossier temporaire (shred).
#
# Usage : pkg/keys/create-release-key.sh [--out DOSSIER] [--no-gh] [--yes]
#   --out DOSSIER  où écrire public/ et backup/ (défaut : ~/meshloom-release-key-AAAAMMJJ)
#   --no-gh        ne pas envoyer le secret GitHub ; la sous-clé CI est écrite
#                  dans DOSSIER/ci-subkey-A-SUPPRIMER.asc (mode 600)
#   --yes          ne pose pas de question (tests). La phrase secrète doit alors
#                  être fournie dans MESHLOOM_KEY_PASSPHRASE.
set -euo pipefail

UID_STR="${MESHLOOM_KEY_UID:-Meshloom Release Signing <releases@meshloom.app>}"
REPO_SLUG="TwinRocket/meshloom"
SECRET_NAME="MESHLOOM_REPO_GPG_PRIVATE_KEY"
OUT=""
USE_GH=1
ASSUME_YES=0

while [ $# -gt 0 ]; do
    case "$1" in
        --out) OUT="${2:?--out demande un dossier}"; shift 2 ;;
        --no-gh) USE_GH=0; shift ;;
        --yes) ASSUME_YES=1; shift ;;
        -h | --help) sed -n '2,24p' "$0"; exit 0 ;;
        *) echo "Argument inconnu : $1" >&2; exit 2 ;;
    esac
done

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok() { printf '    \033[32mOK\033[0m %s\n' "$*"; }
die() { printf '\n\033[31mERREUR : %s\033[0m\n' "$*" >&2; exit 1; }
ask_yes() {
    [ "$ASSUME_YES" = 1 ] && return 0
    local a; printf '    %s [o/N] ' "$1" >/dev/tty; read -r a </dev/tty
    case "$a" in o | O | oui | y | Y | yes) return 0 ;; *) return 1 ;; esac
}

# --- Vérifications -----------------------------------------------------------
say "Vérification des outils"
command -v gpg >/dev/null || die "gpg introuvable (sudo apt install gnupg)"
command -v gpgconf >/dev/null || die "gpgconf introuvable (sudo apt install gnupg)"
command -v tar >/dev/null || die "tar introuvable"
command -v shred >/dev/null || die "shred introuvable (coreutils)"
gpg_ver="$(gpg --version | sed -n '1s/.* \([0-9][0-9.]*\)$/\1/p')"
case "$gpg_ver" in 2.2.* | 2.3.* | 2.4.* | 2.5.* | 3.*) ok "gpg $gpg_ver" ;; *) die "gpg >= 2.2 requis (trouvé : $gpg_ver)" ;; esac
if [ "$USE_GH" = 1 ]; then
    if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
        ok "gh connecté (le secret GitHub pourra être envoyé automatiquement)"
    else
        echo "    gh absent ou non connecté : le secret GitHub sera à créer à la main."
        USE_GH=0
    fi
fi

[ -n "$OUT" ] || OUT="$HOME/meshloom-release-key-$(date +%Y%m%d)"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
if git -C "$OUT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    die "$OUT est dans un dépôt git. Choisissez un dossier hors de tout dépôt (--out)."
fi
[ -e "$OUT/public" ] || [ -e "$OUT/backup" ] && die "$OUT contient déjà public/ ou backup/. Utilisez un autre --out."
chmod 700 "$OUT"
ok "dossier de sortie : $OUT"

# --- Phrase secrète de la sauvegarde ----------------------------------------
say "Phrase secrète de la SAUVEGARDE"
cat <<'EOF'
    Elle chiffre la copie de sauvegarde de la clé maître. Sans elle, la
    sauvegarde est inutilisable : notez-la dans votre gestionnaire de mots de
    passe ET sur papier, rangé avec les clés USB. 16 caractères minimum
    (une phrase de 5-6 mots marche très bien).
EOF
if [ -n "${MESHLOOM_KEY_PASSPHRASE:-}" ]; then
    PASS="$MESHLOOM_KEY_PASSPHRASE"
else
    [ "$ASSUME_YES" = 1 ] && die "--yes demande MESHLOOM_KEY_PASSPHRASE"
    { : </dev/tty; } 2>/dev/null || die "pas de terminal interactif : lancez le script dans un vrai terminal WSL (pas via '!' dans Claude Code)"
    echo "    (Les caractères tapés ne s'affichent PAS : c'est normal. Tapez puis Entrée.)"
    while :; do
        printf '    Phrase secrète : ' >/dev/tty; IFS= read -r -s PASS </dev/tty; printf '\n' >/dev/tty
        printf '    Encore une fois : ' >/dev/tty; IFS= read -r -s PASS2 </dev/tty; printf '\n' >/dev/tty
        [ "$PASS" = "$PASS2" ] || { echo "    Différentes, recommencez."; continue; }
        [ "${#PASS}" -ge 16 ] || { echo "    Trop courte (16 caractères minimum)."; continue; }
        break
    done
    unset PASS2
fi
[ "${#PASS}" -ge 16 ] || die "phrase secrète trop courte (16 caractères minimum)"

# --- Dossier de travail en mémoire ------------------------------------------
BASE=/dev/shm; [ -d "$BASE" ] && [ -w "$BASE" ] || BASE="${TMPDIR:-/tmp}"
WORK="$(mktemp -d "$BASE/mlkey.XXXXXX")"; chmod 700 "$WORK"
cleanup() {
    for h in "$WORK"/gh*; do [ -d "$h" ] && GNUPGHOME="$h" gpgconf --kill gpg-agent 2>/dev/null || true; done
    find "$WORK" -type f -exec shred -u {} + 2>/dev/null || true
    rm -rf "$WORK"
}
trap cleanup EXIT INT TERM
export GNUPGHOME="$WORK/gh-main"; mkdir -m 700 "$GNUPGHOME"
G=(gpg --batch --quiet --pinentry-mode loopback)

# --- 1. Génération -----------------------------------------------------------
say "1/5 Génération de la clé (RSA-4096, peut prendre une minute)"
"${G[@]}" --passphrase '' --quick-generate-key "$UID_STR" rsa4096 cert never
FPR="$(gpg --list-keys --with-colons 2>/dev/null | awk -F: '/^fpr/ {print $10; exit}')"
[[ "$FPR" =~ ^[0-9A-F]{40}$ ]] || die "empreinte inattendue : $FPR"
"${G[@]}" --passphrase '' --quick-add-key "$FPR" rsa4096 sign 2y
REV="$GNUPGHOME/openpgp-revocs.d/$FPR.rev"
[ -s "$REV" ] || die "certificat de révocation non généré"
ok "empreinte : $FPR"
ok "certificat de révocation généré"

# --- 2. Fichiers publics -----------------------------------------------------
say "2/5 Fichiers publics (à committer)"
mkdir -p "$OUT/public"
gpg --export "$FPR" >"$OUT/public/meshloom-archive-keyring.gpg"
gpg --armor --export "$FPR" >"$OUT/public/meshloom.asc"
printf '%s\n' "$FPR" >"$OUT/public/FINGERPRINT"
ok "$OUT/public/{meshloom-archive-keyring.gpg,meshloom.asc,FINGERPRINT}"

# --- 3. Sous-clé de CI (sans la clé maître) ----------------------------------
say "3/5 Sous-clé de signature pour GitHub Actions"
CI="$WORK/ci-subkey.asc"
gpg --armor --export-secret-subkeys "$FPR" >"$CI"
export GNUPGHOME="$WORK/gh-ci"; mkdir -m 700 "$GNUPGHOME"
"${G[@]}" --import "$CI" 2>/dev/null
gpg --list-secret-keys --with-colons "$FPR" | awk -F: '/^sec/ {print $15}' | grep -qx '#' \
    || die "la clé maître ne devrait PAS être dans l'export CI"
echo probe >"$WORK/probe"
"${G[@]}" --passphrase '' -u "$FPR" --detach-sign -o "$WORK/probe.sig" "$WORK/probe" \
    || die "la sous-clé CI ne signe pas sans phrase secrète"
gpgv --keyring "$OUT/public/meshloom-archive-keyring.gpg" "$WORK/probe.sig" "$WORK/probe" 2>/dev/null \
    || die "signature de test non vérifiable avec le keyring public"
ok "sous-clé CI sans clé maître, signe sans phrase secrète, signature vérifiée"
export GNUPGHOME="$WORK/gh-main"

# --- 4. Sauvegarde chiffrée + test de restauration ---------------------------
say "4/5 Sauvegarde chiffrée de la clé maître"
mkdir -p "$WORK/bundle" "$OUT/backup"
gpg --armor --export-secret-keys "$FPR" >"$WORK/bundle/meshloom-master-SECRET.asc"
cp "$REV" "$WORK/bundle/revocation-$FPR.rev"
cp "$OUT/public/"* "$WORK/bundle/"
cat >"$WORK/bundle/LISEZMOI-RESTAURATION.txt" <<EOF
Sauvegarde de la clé de signature Meshloom
Empreinte : $FPR
Créée le  : $(date -u +%Y-%m-%dT%H:%MZ)

Restaurer (sur une machine de confiance) :
  gpg --decrypt meshloom-key-backup-*.tar.gpg | tar -x
  gpg --import meshloom-master-SECRET.asc
Révoquer en urgence (clé compromise) :
  gpg --import revocation-$FPR.rev   (après avoir retiré le ':' en tête du bloc)
  gpg --armor --export $FPR > meshloom-revoked.asc   puis le publier
Renouveler la sous-clé avant expiration : voir pkg/keys/README.md (Rotation).
EOF
BACKUP="$OUT/backup/meshloom-key-backup-${FPR: -16}-$(date +%Y%m%d).tar.gpg"
tar -C "$WORK/bundle" -cf - . \
    | "${G[@]}" --symmetric --cipher-algo AES256 --s2k-digest-algo SHA512 --s2k-count 65011712 \
        --passphrase-fd 3 -o "$BACKUP" 3<<<"$PASS"
cp "$WORK/bundle/LISEZMOI-RESTAURATION.txt" "$OUT/backup/"
ok "$BACKUP"

# Restauration à blanc : déchiffre, importe dans un trousseau vierge, signe.
export GNUPGHOME="$WORK/gh-restore"; mkdir -m 700 "$GNUPGHOME" "$WORK/restore"
"${G[@]}" --passphrase-fd 3 --decrypt "$BACKUP" 3<<<"$PASS" 2>/dev/null | tar -C "$WORK/restore" -xf - \
    || die "la sauvegarde ne se déchiffre pas"
"${G[@]}" --import "$WORK/restore/meshloom-master-SECRET.asc" 2>/dev/null
gpg --list-secret-keys --with-colons "$FPR" | awk -F: '/^sec/ {print $15}' | grep -qvx '#' \
    || die "la sauvegarde ne contient pas la clé maître"
"${G[@]}" --passphrase '' -u "$FPR" --detach-sign -o "$WORK/probe2.sig" "$WORK/probe"
gpgv --keyring "$OUT/public/meshloom-archive-keyring.gpg" "$WORK/probe2.sig" "$WORK/probe" 2>/dev/null \
    || die "la clé restaurée ne produit pas de signature valide"
ok "restauration testée : déchiffrement, import, signature vérifiée"
export GNUPGHOME="$WORK/gh-main"

# --- Contrôle avec le vérificateur du dépôt (si lancé depuis le dépôt) -------
CHECK="$(cd "$(dirname "$0")/../.." 2>/dev/null && pwd)/scripts/build/check_signing_keys.sh"
if [ -x "$CHECK" ] || [ -f "$CHECK" ]; then
    MESHLOOM_KEYS_DIR="$OUT/public" bash "$CHECK" --secret-gnupghome "$WORK/gh-ci" >/dev/null \
        && ok "check_signing_keys.sh : OK" || die "check_signing_keys.sh a refusé la clé"
fi

# --- 5. Secret GitHub ----------------------------------------------------------
say "5/5 Secret GitHub $SECRET_NAME"
if [ "$USE_GH" = 1 ] && ask_yes "Envoyer la sous-clé CI dans le secret $SECRET_NAME de $REPO_SLUG ?"; then
    gh secret set "$SECRET_NAME" -R "$REPO_SLUG" <"$CI"
    gh secret list -R "$REPO_SLUG" | grep -q "^$SECRET_NAME" && ok "secret créé dans $REPO_SLUG"
else
    install -m 600 "$CI" "$OUT/ci-subkey-A-SUPPRIMER.asc"
    cat <<EOF
    Secret NON envoyé. Fichier écrit : $OUT/ci-subkey-A-SUPPRIMER.asc
    GitHub > $REPO_SLUG > Settings > Secrets and variables > Actions >
    New repository secret : nom $SECRET_NAME, valeur = tout le contenu du fichier.
    Puis supprimez-le : shred -u "$OUT/ci-subkey-A-SUPPRIMER.asc"
EOF
fi

unset PASS
say "Terminé"
cat <<EOF
    Empreinte : $FPR

    À FAIRE MAINTENANT :
    1. Copier le dossier $OUT/backup/ sur 2 clés USB (ou disques) rangées
       à 2 endroits différents. Le fichier .tar.gpg est chiffré : il ne
       sert à rien sans la phrase secrète.
    2. Garder la phrase secrète dans le gestionnaire de mots de passe ET
       sur papier avec les clés USB.
    3. Dire à Claude que c'est fait : il committe les 3 fichiers de
       $OUT/public/ dans pkg/keys/ (rien d'autre n'est à committer).
    4. Après copie sur les clés USB, vous pouvez supprimer $OUT/backup/ de
       cette machine (shred -u $OUT/backup/*).

    Rien de secret n'est resté sur le disque en dehors de $OUT/backup/
    (chiffré)$( [ -f "$OUT/ci-subkey-A-SUPPRIMER.asc" ] && echo " et du fichier ci-subkey-A-SUPPRIMER.asc à supprimer après usage").
EOF
