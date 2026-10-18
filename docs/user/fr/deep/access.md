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

Ce que chaque rôle voit dans l’interface suit ce tableau : les écrans qu’un rôle ne peut pas utiliser sont masqués ou grisés au lieu d’échouer au clic.

| | `admin` | `member` | `viewer` | `kiosk` |
|---|---|---|---|---|
| Canaux et paquets | oui | oui | oui | oui (canaux seulement, aucun message direct) |
| Messages directs | oui | oui | oui | non |
| Zone de saisie, nouvelle conversation, épingle, favori, sourdine, suppression | oui | oui | non | non |
| Volets répéteurs et salons, test du maillage | oui | non | non | non |
| Réglages | toutes les sections | Configuration locale | Configuration locale | aucun |
| Langue et thème de ce navigateur | oui | oui | oui | oui, depuis le bouton de compte |

Une action que le serveur refuse malgré tout (par exemple depuis une page restée ouverte avant un changement de rôle) affiche un seul message : « Ce compte n’a pas le droit de faire cela. »

Le premier administrateur, et le compte que Meshloom crée à partir de l’identifiant Basic d’une installation 4.x, sont `admin` : une installation avec un seul administrateur fonctionne comme avant.

### Quand un administrateur définit votre mot de passe

Si un administrateur définit votre mot de passe (ou `meshloom-admin reset-password --must-change`), Meshloom affiche **Changez votre mot de passe** à la place de l’application : saisissez le mot de passe actuel, puis un nouveau (12 caractères minimum, avec les règles rappelées en cas de refus). Rien d’autre ne fonctionne avant le changement, puis l’application s’ouvre là où vous étiez. Les autres appareils de ce compte sont déconnectés. Un écran kiosque n’a pas de mot de passe et n’a jamais cette page ; si elle s’affichait, demandez à un administrateur.

## Les délais de connexion

Les mauvais mots de passe ralentissent les tentatives suivantes ; ils ne verrouillent jamais un compte.

- **Depuis une adresse, pour un compte :** les 5 premiers mauvais mots de passe ne coûtent rien. Ensuite, la tentative suivante attend 1 seconde, puis 2, 4, 8 et ainsi de suite, jusqu’à 1 minute. Pendant ce temps, la page de connexion indique combien de secondes attendre. Une fois l’attente passée, le bon mot de passe fonctionne et efface le délai.
- **Pour un compte, depuis n’importe où :** après 5 mauvais mots de passe, le compte accepte au plus une tentative par seconde, qui que ce soit qui l’envoie. Quelqu’un qui inonde `admin` n’a qu’un essai par seconde, et un navigateur que Meshloom ne connaît pas est en concurrence avec lui pour cette seconde (voir plus bas).
- **Depuis une adresse, pour tous les comptes :** au-delà de 100 mauvais mots de passe en 15 minutes, cette adresse est refusée jusqu’à ce que son compteur redescende (une tentative de plus toutes les 9 secondes).

Une adresse IPv6 compte pour son bloc /64 : un foyer ou un serveur dispose en général d’un bloc entier et pourrait sinon changer d’adresse à chaque tentative.

**Votre propre navigateur entre toujours.** Après une connexion, Meshloom reconnaît le navigateur grâce à un cookie dédié (pendant 180 jours, pour le dernier compte connecté dessus). Avec ce cookie, le bon mot de passe passe aussitôt, quoi que d’autres envoient et d’où qu’ils l’envoient. Un navigateur qui se trompe 5 fois de mot de passe perd ce passe-droit et suit les règles ci-dessus ; ses erreurs sont oubliées après 15 minutes sans nouvelle erreur, ou à sa prochaine connexion réussie. Changer le mot de passe, le rôle ou le nom du compte, le désactiver, ou redémarrer Meshloom oublie les navigateurs : la connexion réussie suivante le fait reconnaître de nouveau.

Depuis un navigateur que Meshloom ne connaît pas encore (nouvel appareil, cookie effacé, après un redémarrage) :

- **Sur une installation Docker (Compose ou un simple conteneur) sans proxy de confiance**, tous les visiteurs peuvent arriver par l’adresse de Docker lui-même : la première et la dernière règle sont coupées et seule la règle par compte s’applique. Quelqu’un sur la même adresse est en concurrence avec vous pour l’unique tentative par seconde : s’il en envoie beaucoup par seconde, il peut vous tenir dehors tant que vous n’utilisez pas un navigateur que Meshloom connaît ou ne listez pas votre reverse-proxy dans **Proxies de confiance**.
- **Derrière un reverse-proxy non listé dans les Proxies de confiance**, tous les visiteurs comptent pour l’adresse du proxy : les délais d’un seul visiteur malveillant s’appliquent à tous. Listez le proxy ; Meshloom journalise un avertissement qui le nomme.
- **Sinon**, chaque visiteur a sa propre adresse et quelqu’un d’ailleurs ne vous fait attendre qu’une seconde au plus.

Les délais sont gardés en mémoire et repartent de zéro au redémarrage de Meshloom. Un délai est écrit une fois dans le journal d’audit, pas à chaque tentative refusée.

## Derrière un reverse-proxy

Les délais de connexion, le journal d’audit et la liste des sessions notent l’adresse d’où vient une requête. Derrière un reverse-proxy, c’est celle du proxy, pour tous les visiteurs, tant que vous ne listez pas le proxy dans **Réglages → Configuration locale → Proxies de confiance** ; Meshloom utilise alors l’adresse du visiteur que le proxy transmet. N’indiquez que l’adresse du proxy lui-même, jamais un réseau Docker ni sa passerelle. Voir [HTTPS, proxies de confiance](/docs/deep/https/#proxies-de-confiance).

## Le journal d’audit

Meshloom tient un journal des actions sensibles : connexions (réussies ou non), déconnexions et sessions déconnectées, comptes créés, renommés, désactivés ou réactivés, changements de mot de passe, création du premier administrateur, changements des réglages d’accès, robots créés, modifiés, supprimés ou arrêtés, restaurations de sauvegarde, imports de clé privée, réinitialisations d’usine de la radio et mises à jour lancées. Chaque commande `meshloom-admin` ci-dessous écrit aussi une entrée, y compris quand elle est refusée.

Chaque entrée garde l’heure, le compte qui a agi (aucun pour une connexion ratée ou la ligne de commande), l’adresse d’où il venait, la voie d’accès (réseau local, Home Assistant, ligne de commande, ou Meshloom lui-même pour une mise à jour automatique), ce qui a été visé et l’issue. Elle ne contient jamais de mot de passe, de code d’installation, de session, de clé ni le code d’un robot. Une connexion ratée ne nomme le compte que si le nom saisi correspond à un compte ; tout autre nom saisi est noté comme inconnu, si bien qu’un mot de passe tapé dans le champ du nom n’est pas gardé.

Les administrateurs le lisent par `GET /api/auth/audit`, du plus récent au plus ancien, par pages ; une visionneuse dans les Réglages viendra dans une version ultérieure. Les entrées sont gardées **366 jours**, puis supprimées par un nettoyage horaire ; un administrateur change cette durée avec `PATCH /api/auth/settings` et `{"audit_retention_days": <1 à 36500>}`. Le journal n’est pas dans les sauvegardes JSON ; le téléchargement de la base (**Réglages → Base de données**) le conserve.

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

Le code a 8 chiffres ; Meshloom en imprime aussi un nouveau dans son journal à chaque démarrage tant qu’aucun compte n’existe. Les espaces ou tirets saisis au milieu sont ignorés. Une installation neuve ne demande pas de code. Si Meshloom n’a pas pu préparer le premier démarrage (le journal le dit), la création de l’administrateur est refusée jusqu’à ce qu’il y arrive ; redémarrez Meshloom si cela dure. Un code imprimé entre-temps par `meshloom-admin setup-code` reste valable : Meshloom ne le remplace pas quand il réessaie.

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
