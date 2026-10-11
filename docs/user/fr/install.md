---
title: Installer
description: Le one-liner Linux, service systemd ou Docker, les mises à jour, et pourquoi il faut garder /bin/bash -c.
level: start
order: 2
---

Le serveur Meshloom est conçu pour Linux. Une machine qui reste allumée fait un bien meilleur hôte qu’un portable qu’on referme : tant que le serveur tourne, il écoute le mesh et écrit ce qu’il entend. (Un Raspberry Pi convient très bien. Il a sa propre [image prête à l’emploi](/docs/rpi/).)

Un script d’installation fait le travail et pose ses questions en français ou en anglais. À coller dans un terminal :

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app)"
```

## Pourquoi `/bin/bash -c`

Cette forme n’est pas décorative. Le script est interactif : il a besoin de vos réponses. Avec `/bin/bash -c "$(...)"`, le téléchargement se termine d’abord, puis le script s’exécute avec le terminal libre pour ses questions.

Si on l’envoie dans un tube (`curl … | bash`), l’entrée du script est occupée par le téléchargement. Ses questions n’ont plus de terminal et l’installation part de travers. Gardez le `/bin/bash -c`.

## Ce que le script demande

Dans cet ordre :

1. **La langue.** Français ou anglais. Votre choix est retenu pour la fois suivante, et le choix par défaut suit la langue de votre système.
2. **La méthode.** Le script affiche ce qu’il a détecté (votre système, la présence de Docker) et propose jusqu’à trois choix :
   - **Installer comme service en arrière-plan.** Meshloom démarre avec la machine, sous systemd. Il fonctionne avec une radio USB, réseau ou Bluetooth. C’est le choix recommandé sous Linux.
   - **Lancer avec Docker.** Meshloom tourne dans un conteneur. Cela sépare l’installation du reste de la machine, mais limite l’accès au matériel. Partager une radio USB avec le conteneur exige Docker en root sous Linux. Sans cela (Docker Desktop, Docker rootless), la radio doit être sur le réseau.
   - **Ouvrir seulement Meshloom dans un navigateur.** N’installe rien. À choisir quand Meshloom tourne déjà sur une autre machine. Le script se contente de rappeler l’adresse à saisir.
3. **Docker uniquement : le dossier.** L’endroit où placer `docker-compose.yml` et le dossier `data` (par défaut `meshloom` dans votre dossier personnel, ou le dossier d’une installation existante). Si plusieurs appareils série sont branchés, il demande aussi lequel partager avec le conteneur. S’il n’y en a qu’un, il le partage sans demander.
4. **Un récapitulatif,** puis une confirmation, avant de lancer quoi que ce soit. Certaines étapes demandent les droits administrateur, et votre mot de passe peut être demandé.

L’installeur ne configure jamais la radio elle-même. Vous choisissez USB, réseau ou Bluetooth dans l’interface web ensuite (voir [Premier lancement](/docs/first-run/)). Les détails de chaque lien sont dans [Transports radio](/docs/deep/transports/).

Sur un système qui n’est pas Linux (macOS, par exemple), le choix du service n’est pas proposé. Utilisez Docker avec une radio sur le réseau, ou le choix « navigateur seulement », et installez Meshloom sur une machine Linux si vous voulez brancher la radio en USB ou en Bluetooth.

Pour un service, le script installe un paquet depuis le dépôt Meshloom signé (`apt` sur Debian et Ubuntu, `dnf` sur Fedora). Si le dépôt n’a rien pour votre machine, il se rabat sur le paquet joint à la version GitHub, puis, en dernier recours, sur une installation depuis le code source.

**La sécurité.** Le script ne pose aucune question de sécurité, et le choix compte. Les bots exécutent du code sur la machine et sont **activés par défaut**, sauf dans le paquet Linux (donc l’image Raspberry Pi), qui les coupe dans `/etc/meshloom/meshloom.env`. Avec Docker ou depuis les sources, ajoutez `MESHCORE_DISABLE_BOTS=true` pour les désactiver (dans l’add-on Home Assistant, activez son option `disable_bots`), et `MESHCORE_BASIC_AUTH_USERNAME` avec `MESHCORE_BASIC_AUTH_PASSWORD` pour imposer un identifiant partagé. Voir [Un réseau de confiance](/docs/trust/).

## Ouvrir l’interface

À la fin de l’installation, le script affiche l’adresse. Depuis la machine elle-même :

```
http://127.0.0.1:8000
```

Depuis un autre appareil du même réseau, remplacez `127.0.0.1` par l’adresse IP de la machine, en gardant le port `8000`.

Choisissez ensuite le lien avec la radio dans l’interface : [Premier lancement](/docs/first-run/).

Attention à ne pas confondre deux adresses proches. `http://127.0.0.1:8000/docs` est la documentation technique de l’API, générée par le serveur. Ce n’est pas cette documentation-ci.

Pour vérifier un service :

```bash
sudo systemctl status meshloom
```

## Mettre à jour

Ouvrez **Réglages > Mises à jour**. La page indique votre type d’installation (paquet Linux, Docker Compose, add-on Home Assistant…) et propose **Vérifier maintenant**. Quand une nouvelle version existe et que votre installation sait se mettre à jour seule, **Installer maintenant** l’installe. Les **mises à jour automatiques** restent éteintes tant que vous ne les activez pas, et vous pouvez les limiter à certains jours et certaines heures (elles suivent l’horloge du serveur, pas celle de votre navigateur). Cela ne met à jour que Meshloom et ne lance jamais un `apt upgrade` complet du système.

Les mises à jour sont signées. L’installeur embarque la clé de publication Meshloom et vérifie son empreinte ; il ne la télécharge jamais et n’ajoute jamais de source de paquets non signée. Le paquet Linux vérifie la signature du dépôt, et l’assistant de mise à jour refuse de tourner si la source Meshloom n’est pas vérifiée. Avec Docker, l’image est épinglée par empreinte dans le fichier `.env` voisin de `docker-compose.yml` (`MESHLOOM_IMAGE=…@sha256:…`). L’assistant ne déplace cet épinglage que vers une version plus récente dont l’empreinte est signée, et ne revient jamais en arrière.

Si **Réglages > Mises à jour** indique qu’il faut mettre à jour à la main ou relancer l’installeur, relancez-le. Il garde vos données, remplace un ancien assistant de mise à jour et, pour Docker, conserve l’ancien fichier Compose sous le nom `docker-compose.yml.bak-<date>`. Si vous avez modifié votre `docker-compose.yml` (pour ajouter un port, par exemple), l’installeur affiche vos modifications et propose de les garder, ce qui est le choix par défaut : il ne met alors à jour que ses propres lignes (l’image et l’assistant de mise à jour) et laisse les vôtres. S’il ne sait pas adapter votre fichier, il n’y touche pas et demande s’il faut en générer un nouveau. Lancez l’installeur avec le même compte que celui qui a créé la pile : `root` si elle se trouve dans `/root/meshloom`.

Deux cas se mettent à jour à la main :

- Docker sans l’assistant de mise à jour (Docker Desktop, Docker rootless, ou un nom de dossier avec des caractères spéciaux). Ces piles suivent `latest`.
- Un paquet Linux, si vous préférez le terminal.

```bash
sudo apt-get install --only-upgrade meshloom   # Debian / Ubuntu
sudo dnf upgrade --refresh meshloom            # Fedora
sudo docker compose pull && sudo docker compose up -d   # Docker sans l’assistant
```

Vos données restent où elles étaient : `/var/lib/meshloom` pour le paquet, le dossier `data` à côté de `docker-compose.yml` pour Docker. Les changements de base de données s’appliquent automatiquement au démarrage.

## Sur Home Assistant

Si Home Assistant tourne déjà sur la machine qui hébergera Meshloom, installez plutôt Meshloom comme add-on. L’interface apparaît dans la barre latérale de Home Assistant, la base de données est stockée avec le reste des données de Home Assistant, et Meshloom démarre et s’arrête avec lui.

[![Ajouter le dépôt à votre Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FTwinRocket%2Fmeshloom)

Le bouton ouvre la bonne fenêtre sur votre propre Home Assistant, avec l’adresse déjà remplie. C’est une simple redirection, qui n’apprend rien sur vous. À la main, allez dans **Paramètres > Modules complémentaires > Boutique > ⋮ > Dépôts**, ajoutez l’adresse du dépôt, puis installez **Meshloom** depuis la boutique.

Deux choses fonctionnent différemment :

**Le port de partage de la radio se règle dans Home Assistant, pas dans Meshloom.** L’interface s’atteint par la barre latérale et ne publie rien : le proxy radio (voir [Transports radio](/docs/deep/transports/)) est donc la seule chose visible sur le réseau. Changez son port dans le panneau **Réseau** de l’add-on. Le champ dans Meshloom ne fait que l’afficher, car une valeur saisie là ferait écouter Meshloom à un endroit où rien n’est redirigé.

**Les notifications demandent une adresse à elles.** La barre latérale n’a pas d’adresse publique stable, donc Web Push ne peut pas fonctionner à travers elle seule. Donnez à Meshloom un vrai nom d’hôte (l’add-on Cloudflared peut le faire sans ouvrir de port sur votre box) et ouvrez Meshloom à cette adresse. L’option `public_url` de l’add-on est acceptée, mais ne fait rien pour l’instant.

Ce n’est pas la même chose que [publier le mesh vers Home Assistant par MQTT](/docs/deep/home-assistant/), qui marche depuis n’importe quelle installation et ne demande aucun add-on.

## Autres chemins

Le script couvre le cas courant. Pour le reste (l’image Docker `ghcr.io/twinrocket/meshloom`, Portainer, HTTPS, une installation systemd manuelle, les variables d’environnement, ou un dépôt cloné pour développer), voir [Autres chemins d’installation](/docs/deep/install-paths/).

Suite : [Premier lancement](/docs/first-run/).
