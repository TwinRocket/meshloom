---
title: Autres chemins d’installation
description: Docker, systemd, Portainer, et un dépôt cloné pour le développement.
level: deep
order: 12
---

L’installateur en une ligne décrit dans [Installation](/docs/install/) couvre le cas courant. Cette page explique ce qu’il fait, puis traite Docker à la main, Portainer, et l’exécution depuis une copie du code.

## Ce que fait l’installateur

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app)"
```

Utilisez `/bin/bash -c` plutôt qu’un tube vers `bash` : le script pose des questions et a besoin de votre terminal. Il demande une langue (français ou anglais), puis comment Meshloom doit fonctionner :

- **Installer comme service en arrière-plan** (Linux uniquement, recommandé). Il démarre avec la machine.
- **Lancer avec Docker.** Meshloom tourne dans un conteneur.
- **Seulement ouvrir Meshloom dans un navigateur.** Rien n’est installé. À choisir si Meshloom fonctionne déjà sur une autre machine.

Vous ne choisissez jamais la connexion à la radio ici. USB, réseau ou Bluetooth se choisit plus tard dans l’interface web (voir [Transports radio](/docs/deep/transports/)). La seule question sur la radio que l’installateur peut poser concerne Docker et l’USB : s’il trouve une radio sur un port USB, il la relie au conteneur.

### En service en arrière-plan

L’installateur choisit la première méthode qui fonctionne sur votre machine :

1. **Un paquet signé du dépôt Meshloom** (`apt` ou `dnf`), s’il en existe un pour votre processeur.
2. **Un paquet téléchargé depuis la page des versions**, installé avec `apt` ou `dnf`.
3. **Une copie du code** : s’il n’y a pas de paquet pour votre système, il télécharge la dernière version dans un dossier (par défaut `~/meshloom`) et lance `install_service.sh` depuis ce dossier.

Une installation par paquet crée :

- un service systemd nommé `meshloom`, exécuté par un utilisateur dédié `meshloom` qui peut utiliser les périphériques série et Bluetooth ;
- le programme dans `/opt/meshloom` ;
- le fichier de réglages `/etc/meshloom/meshloom.env`, où les bots sont **désactivés** par défaut ;
- la base de données et vos données dans `/var/lib/meshloom`.

Vérifiez avec :

```bash
sudo systemctl status meshloom
```

Meshloom écoute sur le port 8000 de tous les réseaux de la machine.

Une installation par copie du code s’exécute sous votre propre utilisateur depuis ce dossier, et sa base est `data/meshcore.db` dans le dossier. Elle ne se met pas à jour depuis l’interface : mettez-la à jour à la main (voir plus bas).

### Avec Docker

L’installateur demande où ranger le fichier Compose (par défaut `~/meshloom`), puis y écrit :

- `docker-compose.yml`, avec le port 8000, un dossier `./data` pour la base et, si une radio USB a été trouvée, la liaison du périphérique ;
- `.env`, qui contient `MESHLOOM_IMAGE`, l’image à lancer.

Un `docker-compose.yml` existant est d’abord sauvegardé. Si vous l’aviez modifié, l’installateur propose de conserver vos modifications.

## Lancer `install_service.sh` soi-même

Depuis une copie du code :

```bash
bash scripts/setup/install_service.sh
```

Il faut `uv` et Python 3.11 ou plus récent. Le script demande s’il faut construire l’interface avec Node.js 20 et npm 9 ou plus récents, ou en télécharger une toute prête. Il écrit ensuite `/etc/systemd/system/meshloom.service`, le démarre sous votre utilisateur, et donne à cet utilisateur l’accès aux périphériques série et Bluetooth.

On peut le rejouer après une mise à jour du code : il arrête le service, réécrit l’unité, recharge systemd et le redémarre. Il ne configure ni les bots ni un mot de passe. Pour définir des variables, voir [Variables et réglages](/docs/deep/environment/).

Pour mettre à jour une telle installation :

```bash
cd ~/meshloom
git pull
uv sync
cd frontend && npm install && npm run build && cd ..
sudo systemctl restart meshloom
```

## Docker à la main

L’image est `ghcr.io/twinrocket/meshloom`. Le dépôt contient `docker-compose.example.yml`. Ses éléments principaux :

- `image: ${MESHLOOM_IMAGE:-ghcr.io/twinrocket/meshloom:latest}`. Sans fichier `.env`, elle suit `:latest`. Pour rester sur une version précise, écrivez la ligne `MESHLOOM_IMAGE=ghcr.io/twinrocket/meshloom:X.Y.Z@sha256:...` dans un fichier `.env` à côté. Chaque version publie ses empreintes dans un fichier `OCI-DIGESTS` signé.
- `ports: "8000:8000"`.
- `./data:/app/data`, pour la base de données.
- `devices:`, uniquement pour donner une radio USB au conteneur. Une radio réseau ou Bluetooth se configure dans l’interface web.
- `MESHCORE_DATABASE_PATH: data/meshcore.db`, et `restart: unless-stopped`.

Le conteneur tourne par défaut sous root. Pour le faire tourner sous l’utilisateur numéro 10001, définissez `MESHLOOM_RUN_AS_USER: "10001"`. Au démarrage, le conteneur confie `./data` à cet utilisateur et lui donne les groupes des périphériques série. Si la radio reste inaccessible ainsi, Meshloom reste sous root et écrit un avertissement dans son journal. À ne pas utiliser avec le Bluetooth, qui demande une mise en place manuelle plus poussée, décrite dans [Transports radio](/docs/deep/transports/).

Un fichier Compose écrit à la main ne peut pas être mis à jour depuis l’interface. Le mode Docker de l’installateur ajoute un petit assistant qui tourne sous root sur l’hôte : il fige l’image par son empreinte signée dans `.env` et installe les mises à jour quand vous le demandez dans **Réglages → Mises à jour**. Il ne modifie jamais `docker-compose.yml`. Docker Desktop et Docker sans root n’ont pas cet assistant : la pile suit `:latest`.

## Portainer

Le fichier [`docker-compose.dev.yaml`](https://github.com/TwinRocket/meshloom/blob/main/docker-compose.dev.yaml) du dépôt est prévu pour une pile qui **construit l’image depuis le dépôt**. Dans Portainer, faites-y pointer la pile et chargez les variables de `.env.example` :

```text
MESHLOOM_HTTP_PORT=8123
MESHLOOM_DATA_PATH=/opt/docker/meshloom/data
MESHCORE_DATABASE_PATH=data/meshcore.db
MESHCORE_DISABLE_BOTS=false
MESHCORE_VAPID_SUBJECT=mailto:you@example.com
# MESHLOOM_COMMUNITY=0
```

Le fichier publie aussi le port 5001 (`MESHLOOM_PROXY_PORT`), utilisé par le proxy radio une fois activé. Si vous ne voulez pas d’une construction sur le serveur, utilisez plutôt `docker-compose.example.yml`.

`MESHCORE_VAPID_SUBJECT` n’est utilisé que si le contact de **Réglages → Notifications** est vide. Ne mettez pas d’adresse réelle dans un dépôt public. L’adresse et le port de la radio se règlent dans l’interface web. Un nouveau dossier de données vide rejoint Community, sauf si `MESHLOOM_COMMUNITY=0`. Voir [Meshloom Community](/docs/deep/community/).

## Depuis une copie du code, pour le développement

```bash
uv sync
uv run uvicorn app.main:app --reload
```

`uv sync` crée le `.venv` propre au projet. N’installez pas ces dépendances avec `apt` ni `dnf`. Si `uv run uvicorn` s’arrête sur `ModuleNotFoundError: No module named 'meshcore'`, commencez ici. Voir [Dépannage](/docs/deep/troubleshooting/).

Pour travailler sur l’interface :

```bash
cd frontend
npm install
npm run dev
```

Le serveur de développement écoute sur `http://localhost:5173` et transmet les appels `/api` au port 8000. Pour la production, construisez l’interface une fois, puis démarrez le serveur :

```bash
cd frontend && npm install && npm run build && cd ..
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Le serveur sert `frontend/dist` s’il existe, sinon `frontend/prebuilt` (une interface toute prête que `scripts/setup/fetch_prebuilt_frontend.py` télécharge). Si aucun des deux n’existe, il ne sert que l’API. Lancez les contrôles de qualité du dépôt depuis sa racine :

```bash
./scripts/quality/all_quality.sh
```

## Base de données et mises à jour

| Installation | Base de données |
|--------------|-----------------|
| Paquet Linux | `/var/lib/meshloom/meshcore.db` |
| Docker | `./data`, monté sur `/app/data` |
| Copie du code | `data/meshcore.db` dans le dossier |

`MESHCORE_DATABASE_PATH` la déplace. Une mise à jour ne remplace jamais votre base : sa structure est mise à niveau au démarrage, étape par étape.

**Réglages → Mises à jour** affiche l’état des mises à jour et peut chercher une nouvelle version (**Vérifier maintenant**). Là où c’est pris en charge, il installe la mise à jour depuis une source signée, soit à la demande (**Installer maintenant**), soit tout seul dans une plage horaire et les jours que vous choisissez. Cela fonctionne pour un paquet Linux et pour une pile Docker gérée par l’assistant. Ailleurs, mettez à jour à la main :

```bash
sudo apt-get install --only-upgrade meshloom
sudo dnf upgrade meshloom
sudo docker compose pull && sudo docker compose up -d
```

La première ligne vaut pour Debian, Ubuntu et Raspberry Pi OS, la deuxième pour Fedora et les systèmes proches, la troisième pour Docker.

L’adresse par défaut est `http://127.0.0.1:8000`. La page `/docs` documente l’interface de programmation (API). Ce n’est pas ce site.
