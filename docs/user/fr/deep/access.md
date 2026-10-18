---
title: Récupérer l’accès
description: meshloom-admin, la commande qui réinitialise un mot de passe, crée un administrateur ou émet le code d’installation depuis la machine, pour chaque mode d’installation de Meshloom.
level: deep
order: 21
---

À partir de Meshloom 5.0, se connecter demande un compte utilisateur (voir [Sécurité](/docs/deep/security/)). Si vous perdez le mot de passe de l’administrateur, l’interface web n’est pas nécessaire pour revenir : `meshloom-admin` travaille sur la machine qui détient la base de données. Quiconque peut le lancer à cet endroit contrôle déjà les données de Meshloom, il ne demande donc aucune connexion.

Il modifie directement le fichier de la base. Un Meshloom en cours d’exécution voit le changement à sa requête suivante : pas de redémarrage.

## Les rôles

Chaque compte a l’un des quatre rôles. Le serveur vérifie le rôle à chaque requête, quoi qu’affiche l’interface : une requête hors du rôle est refusée (`auth_forbidden`).

| Rôle | Peut |
|---|---|
| `admin` | Tout : réglages, radio, répéteurs et salons, intégrations et bots, sauvegarde et restauration, clé privée, mises à jour, Community, notifications, rapport de débogage. |
| `member` | Tout ce que peut un `viewer`, plus envoyer des messages, ajouter et modifier contacts, canaux et groupes de contacts, favoris, épingles et canaux en sourdine, et abonner son propre navigateur aux notifications push. |
| `viewer` | Lire les canaux et les messages directs, les marquer comme lus, gérer son propre profil et ses sessions. |
| `kiosk` | Lecture seule, pour un écran au mur : messages des canaux (canaux en sourdine et en attente compris), contacts, canaux, paquets, statistiques et carte. Jamais un message direct, aucun réglage, aucun profil. |

Le premier administrateur, et le compte que Meshloom crée à partir de l’identifiant Basic d’une installation 4.x, sont `admin` : une installation avec un seul administrateur fonctionne comme avant.

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

Un Meshloom en cours d’exécution voit ces changements dès sa requête suivante ; un navigateur resté ouvert sur une page déconnectée de cette façon perd sa connexion en direct en moins de 30 secondes.

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

Par la barre latérale, c’est Home Assistant qui vous connecte : Meshloom accepte l’utilisateur Home Assistant qui l’ouvre, sans mot de passe propre. Le premier utilisateur Home Assistant qui ouvre Meshloom en devient l’administrateur, même si un administrateur Meshloom existe déjà (issu des options `basic_auth_*` de la 4.18 ou de `meshloom-admin`), pour que la barre latérale mène toujours à un administrateur ; les suivants reçoivent le rôle **Membre**. Seuls les administrateurs de Home Assistant voient le panneau Meshloom : ce sont donc des personnes qui administrent déjà votre Home Assistant. Si vous ne pouvez pas ouvrir Meshloom, rétablissez votre accès dans Home Assistant lui-même. Si vous avez donné un port d’hôte au port 8000 de l’add-on, cette adresse demande un compte Meshloom comme toute autre installation. La commande existe quand même pour le cas rare où elle serait utile. Elle s’exécute dans le conteneur de l’add-on et demande donc d’atteindre le Docker de l’hôte : l’add-on *Advanced SSH & Web Terminal* avec le **Mode protégé** désactivé.

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
