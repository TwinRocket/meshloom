---
title: Installer
description: Le one-liner Linux, systemd ou Docker, et pourquoi il faut garder /bin/bash -c.
level: start
order: 2
---

Le serveur Meshloom est conçu pour Linux. Une machine qui reste allumée fait un bien meilleur hôte qu’un portable qu’on referme : tant que le serveur tourne, il écoute le mesh et écrit ce qu’il entend.

Un script d’installation fait le travail et pose des questions, en français ou en anglais. À coller dans un terminal :

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app)"
```

## Pourquoi `/bin/bash -c`

Cette forme n’est pas décorative. Le script est interactif : il demande le mode d’installation, un mapping USB optionnel pour Docker, un mot de passe éventuel. Avec `/bin/bash -c "$(...)"`, le téléchargement se termine d’abord, puis le script s’exécute avec le terminal disponible pour ses questions.

Si on l’envoie dans un tube — `curl … | bash` — l’entrée du script est occupée par le téléchargement. Les questions n’ont plus de terminal pour s’afficher et l’installation part de travers. Gardez le `/bin/bash -c`.

## Ce que le script demande

Trois blocs de questions, dans cet ordre.

**Le mode.** Un service natif géré par systemd, ou Docker. Le service natif démarre automatiquement avec la machine et sait parler à une radio USB, réseau ou Bluetooth. Docker fonctionne dans un conteneur, ce qui isole l’installation mais restreint l’accès au matériel : partager une radio USB avec un conteneur suppose un Docker en mode root sur Linux. Si ce n’est pas le cas, le script le dit et propose la radio réseau.

**La radio.** Le transport se configure dans l’interface après l’installation, pas par variable d’environnement. Une installation systemd native ne demande ni port série, ni hôte TCP, ni PIN BLE. Docker peut encore demander USB ou réseau, uniquement pour émettre un mapping Compose `devices:` pour une radio USB.

Les détails de chaque transport sont dans [Transports radio](/docs/deep/transports/).

**La sécurité.** Les bots exécutent du code sur la machine : le script les laisse désactivés par défaut, et c’est le bon réglage tant que le réseau n’est pas entièrement de confiance. Il propose aussi de demander un identifiant et un mot de passe à l’ouverture. Il s’agit d’un accès partagé unique, pas de comptes utilisateurs. Voir [Un réseau de confiance](/docs/trust/).

Un récapitulatif s’affiche avant de lancer quoi que ce soit. Certaines étapes demandent les droits administrateur.

## Ouvrir l’interface

Une fois l’installation terminée, depuis la machine elle-même :

```
http://127.0.0.1:8000
```

Depuis un autre appareil du même réseau, remplacez `127.0.0.1` par l’adresse IP de la machine, en gardant le port `8000`.

Le transport radio se configure ensuite dans l’interface : [Premier lancement](/docs/first-run/).

Attention à ne pas confondre deux adresses proches. `http://127.0.0.1:8000/docs` est la documentation technique de l’API que le serveur génère lui-même. Ce n’est pas cette documentation-ci.

Pour vérifier ou redémarrer un service natif :

```bash
sudo systemctl status meshloom
```

## Mettre à jour

Selon le mode retenu :

```bash
sudo apt upgrade                          # Debian / Ubuntu
sudo dnf upgrade                          # Fedora / Rocky / Alma
sudo docker compose pull && sudo docker compose up -d
```

La base de données reste en place : `/var/lib/meshloom` pour le paquet, `./data` pour Docker. Les migrations de schéma s’exécutent au démarrage.

## Sur Home Assistant

Si Home Assistant tourne déjà sur la machine qui hébergera Meshloom, installez-le plutôt comme add-on. L'interface arrive dans la barre latérale, la base est conservée avec le reste des données de Home Assistant, et Meshloom démarre et s'arrête avec lui.

[![Ajouter le dépôt à votre Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FTwinRocket%2Fmeshloom)

Le bouton ouvre la fenêtre sur votre propre instance avec l'adresse déjà remplie ; c'est un redirecteur, il n'apprend rien sur vous. À la main, l'adresse se saisit dans **Paramètres → Modules complémentaires → Boutique → ⋮ → Dépôts**, puis on installe **Meshloom**.

Deux choses s'y passent différemment :

**Le port du proxy radio se règle dans Home Assistant, pas dans Meshloom.** L'interface passe par la barre latérale et ne publie rien : le proxy est la seule chose présente sur le réseau. Son port se change dans le panneau **Réseau** de l'add-on ; le champ dans Meshloom l'affiche et le dit, parce qu'une valeur saisie là laisserait le proxy à l'écoute là où rien n'est redirigé.

**Les notifications ont besoin d'une adresse à elles.** La barre latérale n'a pas d'adresse publique durable, donc Web Push ne peut pas fonctionner par ce seul chemin. Donner un vrai nom d'hôte à l'instance — l'add-on Cloudflared le fait sans ouvrir de port sur votre box — et le saisir comme `public_url` de l'add-on est ce qui rend les notifications possibles.

Ce n'est pas la même chose que [publier le mesh vers Home Assistant en MQTT](/fr/docs/deep/home-assistant/), qui fonctionne depuis n'importe quelle installation et ne demande aucun add-on.

## Les autres chemins

Le script couvre le cas courant. Le reste — image Docker `ghcr.io/twinrocket/meshloom`, Portainer, HTTPS, systemd à la main, variables d’environnement, ou un dépôt cloné pour développer — est dans [Autres chemins d’installation](/docs/deep/install-paths/).

Ensuite : [Premier lancement](/docs/first-run/).
