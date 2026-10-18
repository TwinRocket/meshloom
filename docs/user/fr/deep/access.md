---
title: Récupérer l’accès
description: meshloom-admin, la commande qui réinitialise un mot de passe, crée un administrateur ou émet le code d’installation depuis la machine, pour chaque mode d’installation de Meshloom.
level: deep
order: 21
---

À partir de Meshloom 5.0, se connecter demande un compte utilisateur (voir [Sécurité](/docs/deep/security/)). Si vous perdez le mot de passe de l’administrateur, l’interface web n’est pas nécessaire pour revenir : `meshloom-admin` travaille sur la machine qui détient la base de données. Quiconque peut le lancer à cet endroit contrôle déjà les données de Meshloom, il ne demande donc aucune connexion.

Il modifie directement le fichier de la base. Un Meshloom en cours d’exécution voit le changement à sa requête suivante : pas de redémarrage.

## Les commandes

| Commande | Effet |
|---|---|
| `meshloom-admin reset-password <utilisateur>` | Définit un nouveau mot de passe (demandé deux fois, ou une ligne sur l’entrée standard avec `--stdin`) et déconnecte l’utilisateur partout. `--must-change` en demande un autre à la prochaine connexion. |
| `meshloom-admin create-admin <utilisateur>` | Crée un administrateur. Mêmes options de mot de passe, plus `--display-name`. |
| `meshloom-admin setup-code` | Émet un nouveau code d’installation et l’affiche (voir plus bas). |
| `meshloom-admin list-users` | Liste les comptes : identifiant, rôle, fournisseur, actif ou désactivé, mot de passe défini ou non. `--json` pour les scripts. |
| `meshloom-admin disable-user <utilisateur>` | Bloque un utilisateur et le déconnecte. Refuse de désactiver le dernier administrateur actif sauf avec `--force`. |
| `meshloom-admin enable-user <utilisateur>` | Autorise de nouveau un utilisateur désactivé à se connecter. |
| `meshloom-admin revoke-sessions <utilisateur>` ou `--all` | Déconnecte un utilisateur, ou tout le monde. |

Un mot de passe compte au moins 12 caractères et n’est jamais lu sur la ligne de commande, il reste donc hors de l’historique de votre shell. `meshloom-admin --help` et `meshloom-admin <commande> --help` listent toutes les options. Une commande se termine par le code 0 si elle a réussi, et par 1 avec un message sinon, par exemple quand le fichier de la base est absent, illisible ou pas encore mis à niveau (lancez d’abord Meshloom une fois dans la nouvelle version).

### Le code d’installation

Quand Meshloom est mis à jour depuis une version plus ancienne, le premier administrateur doit prouver qu’il a accès à la machine en saisissant un code d’installation. Meshloom ne garde qu’une empreinte du code, qui ne peut donc pas être réaffiché : `meshloom-admin setup-code` en fabrique un **nouveau** et le précédent cesse de fonctionner. La commande ne marche que tant qu’aucun compte n’existe ; ensuite, utilisez `create-admin` ou `reset-password`.

Le code a 8 chiffres ; Meshloom en imprime aussi un nouveau dans son journal à chaque démarrage tant qu’aucun compte n’existe. Les espaces ou tirets saisis au milieu sont ignorés. Une installation neuve ne demande pas de code. Si Meshloom n’a pas pu préparer le premier démarrage (le journal le dit), la création de l’administrateur est refusée jusqu’à ce qu’il y arrive ; redémarrez Meshloom si cela dure.

## La commande pour chaque installation

Remplacez `alice` par le nom du compte.

### Paquet Linux (apt ou dnf)

Lancez-la avec `sudo` :

```bash
sudo meshloom-admin reset-password alice
```

La commande refuse de s’exécuter sans les droits root. En root, elle lit le chemin de la base dans `/etc/meshloom/meshloom.env` (un fichier que seul root peut lire), puis passe à l’utilisateur `meshloom`, qui possède la base : SQLite ne laisse ainsi aucun fichier que le service ne peut pas écrire. La même commande existe sous le nom `/opt/meshloom/.venv/bin/meshloom-admin`.

### Docker et Docker Compose

Lancez-la dans le conteneur, depuis le dossier qui contient `docker-compose.yml` :

```bash
docker compose exec meshloom meshloom-admin reset-password alice
```

Avec `docker run`, utilisez `docker exec -it <conteneur> meshloom-admin reset-password alice`. Si le conteneur tourne sous l’utilisateur 10001 (`MESHLOOM_RUN_AS_USER`), ajoutez `--user 10001` à `exec`, pour que les fichiers de la base gardent leur propriétaire. Pour alimenter un script, lisez le mot de passe sans l’afficher et passez-le sur l’entrée standard, il n’apparaît ainsi jamais dans l’historique de votre shell : `read -rs pw; printf '%s\n' "$pw" | docker compose exec -T meshloom meshloom-admin reset-password alice --stdin`.

### Add-on Home Assistant

L’add-on n’a pas de comptes propres : Home Assistant vous connecte et Meshloom accepte la personne qui l’ouvre depuis la barre latérale. Si vous ne pouvez pas ouvrir Meshloom, rétablissez votre accès dans Home Assistant lui-même.

**Limite connue de la pré-version 5.0 :** cette connexion par Home Assistant n’existe pas encore, et via la barre latérale Meshloom ne peut pas garder de session (aucun cookie n’y est posé, exprès : tous les add-ons partagent cette adresse). En attendant, exigé pour la 5.0.0, ouvrez l’add-on sur son propre port (8000) et connectez-vous là. La commande existe quand même pour le cas rare où elle serait utile. Elle s’exécute dans le conteneur de l’add-on et demande donc d’atteindre le Docker de l’hôte : l’add-on *Advanced SSH & Web Terminal* avec le **Mode protégé** désactivé.

```bash
docker ps --format '{{.Names}}' | grep meshloom
docker exec -it addon_xxxxxxxx_meshloom meshloom-admin list-users
```

Utilisez le nom affiché par la première commande. La base de l’add-on est `/config/meshcore.db` dans le conteneur, que la commande trouve toute seule.

### Image Raspberry Pi

L’image installe le paquet Linux, la commande est donc la même. Ouvrez un shell sur le Pi en SSH (l’utilisateur et la clé choisis dans Raspberry Pi Imager), puis :

```bash
sudo meshloom-admin reset-password alice
```

Si vous avez besoin du code d’installation et ne pouvez pas lire le journal, `meshloom-admin setup-code` en donne un nouveau en SSH.

### Copie du code

Depuis le dossier de Meshloom, avec l’utilisateur qui le lance :

```bash
cd ~/meshloom
uv run python -m app.cli reset-password alice
```

La base est `data/meshcore.db` dans ce dossier.

## Si la commande échoue

- **`database not found`** : la commande a regardé le mauvais fichier. Donnez-lui le chemin avec `--db /chemin/vers/meshcore.db`, ou définissez `MESHCORE_DATABASE_PATH`.
- **`run it as root`** (paquet) : ajoutez `sudo` devant.
- **`cannot read /etc/meshloom/meshloom.env`** : donnez la base avec `--db /chemin/vers/meshcore.db` ou définissez `MESHCORE_DATABASE_PATH`.
- **`cannot read database` ou `cannot open database for writing`** : vous n’êtes pas l’utilisateur propriétaire du fichier. Utilisez `sudo meshloom-admin` (paquet) ou `docker compose exec` (Docker).
- **`start Meshloom once`** : la base est plus ancienne que le programme. Lancez Meshloom pour qu’il la mette à niveau, puis relancez la commande.
