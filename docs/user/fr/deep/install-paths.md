---
title: Autres chemins d’installation
description: Paquet systemd, Docker, Portainer, et un checkout pour développer.
level: deep
order: 12
---

Le one-liner de la page [Installer](/docs/install/) couvre le cas courant. Cette page décrit ce qu’il fait réellement, et les trois autres chemins : Docker à la main, Portainer, et un checkout du dépôt.

## Ce que fait le one-liner

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app)"
```

Le `/bin/bash -c` n’est pas décoratif. Le script pose des questions, et un `curl | bash` prive ces questions de terminal. Sur Linux, il demande service systemd natif ou Docker. Docker peut demander USB ou réseau uniquement pour émettre un mapping Compose `devices:`. Le transport radio se configure dans l’interface.

En systemd natif, il installe le paquet `meshloom` via `apt-get` ou `dnf` quand il est disponible. Cette installation pose trois choses :

- l’unité systemd, nommée `meshloom`
- le fichier d’environnement `/etc/meshloom/meshloom.env`
- le répertoire de données `/var/lib/meshloom`

Si le paquet n’est pas disponible pour la plateforme, le script retombe sur une installation depuis les sources.

L’état du service se lit comme n’importe quel autre :

```bash
sudo systemctl status meshloom
```

## install_service.sh depuis un clone

Depuis un dépôt déjà cloné, l’installeur de checkout reste utilisable. Il tourne sous votre utilisateur courant, depuis le répertoire du dépôt. Le nom d’unité est le même : `meshloom`.

```bash
bash scripts/setup/install_service.sh
```

Le script est rejouable. Relancez-le après avoir récupéré une nouvelle version : si le service tourne déjà, il l’arrête, réécrit le fichier d’unité, recharge systemd, puis le redémarre. Il ne configure ni les bots ni l’authentification : posez vous-même `MESHCORE_DISABLE_BOTS` et `MESHCORE_BASIC_AUTH_*` dans l’environnement. Le transport radio se configure dans l’interface, pas dans le fichier d’unité.

## Docker

L’image est publiée sur `ghcr.io/twinrocket/meshloom`. Le dépôt fournit `docker-compose.example.yml` comme point de départ. Les éléments qui comptent :

- le volume `./data:/app/data`, qui contient la base SQLite
- un mapping `devices:` optionnel pour l’USB (TCP et BLE se configurent dans l’interface)
- `MESHCORE_DATABASE_PATH: data/meshcore.db`
- `restart: unless-stopped`

- `image: ${MESHLOOM_IMAGE:-ghcr.io/twinrocket/meshloom:latest}` : épinglez une version dans un fichier `.env` voisin, idéalement par empreinte (`MESHLOOM_IMAGE=ghcr.io/twinrocket/meshloom:X.Y.Z@sha256:…` ; l’asset signé `OCI-DIGESTS` de chaque version la donne)

Le conteneur tourne en root par défaut. Pour le faire tourner sous l’uid 10001, posez `MESHLOOM_RUN_AS_USER: "10001"` : l’entrypoint donne `./data` à cet uid et ajoute les groupes des ports série mappés ; si la radio reste inaccessible, il reste en root et l’écrit dans les logs. À éviter en Bluetooth.

Une pile écrite à la main n’a pas de mise à jour depuis l’interface. Le mode Docker de l’installeur ajoute sur l’hôte un assistant root qui épingle l’image par empreinte signée dans `.env` et applique les mises à jour demandées depuis Réglages → Mises à jour ; il ne modifie jamais `docker-compose.yml`.

BLE en conteneur demande des ajustements manuels supplémentaires. Voir [Transports radio](/docs/deep/transports/).

## Portainer

Pour une stack qui construit depuis le dépôt, utilisez [`docker-compose.dev.yaml`](https://github.com/TwinRocket/meshloom/blob/main/docker-compose.dev.yaml) comme chemin de Compose, et chargez les clés de [`.env.example`](https://github.com/TwinRocket/meshloom/blob/main/.env.example) dans la section Environment de Portainer, ou dans un `.env` local.

Les clés attendues sont peu nombreuses :

```text
MESHLOOM_HTTP_PORT=8123
MESHLOOM_DATA_PATH=/opt/docker/meshloom/data
MESHCORE_DATABASE_PATH=data/meshcore.db
MESHCORE_DISABLE_BOTS=false
MESHCORE_VAPID_SUBJECT=mailto:you@example.com
# MESHLOOM_COMMUNITY=0
```

`MESHCORE_VAPID_SUBJECT` n’est qu’un repli si le sujet VAPID de Réglages → Notifications est vide. Ne validez pas de vraies adresses VAPID dans le dépôt. L’hôte et le port radio se règlent dans l’interface. Un volume de données vide rejoint Community sauf `MESHLOOM_COMMUNITY=0`. Voir [Meshloom Community](/docs/deep/community/).

## Depuis un checkout

Pour développer, ou pour faire tourner une version non publiée. Le backend passe par `uv`.

```bash
uv sync
uv run uvicorn app.main:app --reload
```

`uv sync` n’est pas optionnel : c’est lui qui crée le `.venv` contenant les dépendances. Ne tentez pas de les installer avec `apt` ou `dnf` — la plupart ne sont pas empaquetées, et elles n’ont rien à faire sur le Python système. Si `uv run uvicorn` échoue sur `ModuleNotFoundError: No module named 'meshcore'`, la cause est presque toujours là. Voir [Dépannage](/docs/deep/troubleshooting/).

Le frontend est séparé en développement :

```bash
cd frontend
npm install
npm run dev
```

Le serveur Vite écoute sur `http://localhost:5173` et relaie `/api` vers le port 8000. Faire tourner les deux en parallèle donne le rechargement à chaud.

En production, c’est le backend FastAPI qui sert le frontend compilé. Il faut donc construire avant :

```bash
cd frontend && npm install && npm run build && cd ..
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Si `frontend/dist` est absent, le backend cherche `frontend/prebuilt`, présent par exemple dans l’archive zip de release. Si aucun des deux n’existe, le démarrage journalise une erreur explicite et continue à servir les routes API sans monter le frontend.

La suite de vérifications du dépôt se lance depuis la racine :

```bash
./scripts/quality/all_quality.sh
```

Le reste des conventions de contribution est dans [CONTRIBUTING.md](https://github.com/TwinRocket/meshloom/blob/main/CONTRIBUTING.md).

## Base de données et mises à jour

La base SQLite est le seul état persistant qui compte. Son emplacement dépend du chemin d’installation :

| Installation | Emplacement de la base |
|--------------|------------------------|
| Paquet systemd | `/var/lib/meshloom` |
| Docker | `./data` (volume monté sur `/app/data`) |
| Checkout | `data/meshcore.db`, relatif au dépôt |

`MESHCORE_DATABASE_PATH` permet de la déplacer.

Les mises à jour ne touchent pas la base. Les migrations de schéma s’appliquent au démarrage, dans l’ordre, en s’appuyant sur le `user_version` de SQLite.

Réglages → Mises à jour applique une mise à jour de Meshloom seul, depuis une source signée, quand l’assistant est présent. Sinon :

```bash
sudo apt-get install --only-upgrade meshloom
sudo dnf install meshloom
sudo docker compose pull && sudo docker compose up -d
```

## Après le démarrage

L’interface écoute sur `http://127.0.0.1:8000` par défaut. Sur le même hôte et le même port, FastAPI sert aussi une documentation d’API interactive sur `/docs`. C’est du Swagger généré depuis le code, pas ce site.
