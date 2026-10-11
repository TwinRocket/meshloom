---
title: Image Raspberry Pi
description: Écrire une carte avec Meshloom déjà installé, régler le Wi-Fi dans Raspberry Pi Imager, et démarrer.
level: start
order: 2.5
---

L’image publiée est un Raspberry Pi OS Lite 64 bits avec Meshloom déjà installé. Le premier démarrage n’a pas besoin d’Internet.

## Quel Pi ?

| Votre carte | Comment installer |
|---|---|
| Pi 3B / 3B+ / 3A+, Compute Module 3, Zero 2 W, Pi 4, Pi 5 | Cette image. C’est le chemin le plus rapide, et rien d’autre n’est nécessaire. |
| Pi 2, ou un Pi 3 ou plus récent qui tourne déjà sous Raspberry Pi OS 32 bits | Le [one-liner](/docs/install/). Il détecte l’architecture et installe le paquet 32 bits (armhf). |
| Pi 1, Compute Module 1, Zero et Zero W d’origine | Non pris en charge. Ces cartes ont un processeur plus ancien (ARMv6) et aucun paquet n’est construit pour lui. Le one-liner le signale et s’arrête avant de modifier quoi que ce soit. |

Préférez l’image 64 bits sur toute carte qui peut la faire tourner : elle est plus rapide, et le paquet 32 bits est construit sous émulation. Un Pi 2 ne tourne qu’en 32 bits, donc le one-liner est son chemin.

Le paquet 32 bits existe depuis la 4.12.2. Les versions précédentes ne s’installaient pas du tout sur un système 32 bits.

### À quoi s’attendre sur un Pi 2 ou un Pi 3

Personne n’a publié de mesures pour ces cartes : prenez ce qui suit pour ce qu’on sait, pas pour une promesse.

Les deux ont 1 Go de mémoire, et l’essentiel de ce qui paraît lourd dans Meshloom ne l’est pas sur le Pi : la carte, la vue 3D et le flux de paquets sont dessinés par le navigateur dans lequel vous ouvrez l’interface. Le Pi fait seulement tourner le serveur, le lien avec la radio et une base SQLite.

La limite la plus probable est la carte SD. La base est écrite à chaque message et à chaque paquet entendu. Une carte bon marché est lente à cela, et s’use. Si vous comptez laisser un nœud tourner des mois, une bonne carte ou un SSD USB compte plus que la carte Pi.

Le Pi 2 est la carte prise en charge la plus lente, et l’interface s’en ressentira quand l’historique grossira. Si vous l’essayez, dites-nous où cela cesse d’être confortable : personne n’a encore ce chiffre.

## Écrire la carte

Il vous faut un ordinateur avec un lecteur de carte SD et [Raspberry Pi Imager](https://www.raspberrypi.com/software/) **en version 2.0.6 ou plus récente**. Les versions 1.9.x plus anciennes n’appliquent pas les réglages de premier démarrage (cloud-init) de ce système, donc le nom d’hôte, le Wi-Fi et l’SSH seraient ignorés.

1. Allez sur la [page des versions GitHub](https://github.com/TwinRocket/meshloom/releases) et téléchargez `meshloom.rpi-imager-manifest` dans la dernière version. Il est joint un peu après les paquets : patientez si vous ne le voyez pas encore. Inutile de télécharger l’image (`meshloom-rpi-lite-arm64.img.xz`) : le manifeste pointe vers elle, et Imager la télécharge depuis la même version.
2. Ouvrez le manifeste plutôt que l’image : double-cliquez sur `meshloom.rpi-imager-manifest`, ou lancez `rpi-imager --repo chemin/vers/meshloom.rpi-imager-manifest`. Ce fichier indique à Imager comment appliquer vos réglages (`init_format: cloudinit-rpi`). N’utilisez **pas** *Use custom* sur le seul `.img.xz` : Imager 2.x suppose alors qu’il n’y a rien à personnaliser et ignore le Wi-Fi, l’utilisateur et l’SSH.
3. Dans Imager, choisissez votre modèle de Pi, l’entrée Meshloom, puis votre carte.
4. Renseignez le nom d’hôte, l’utilisateur, votre clé SSH si vous en voulez une, et le Wi-Fi. Ces réglages sont écrits sur la carte par Imager. Ils ne sont pas dans le fichier téléchargé.
5. Écrivez la carte, insérez-la dans le Pi et mettez-le sous tension.

Ethernet et Wi-Fi sont tous deux facultatifs au premier démarrage : le Pi démarre même si aucun câble n’est branché ou si le réseau Wi-Fi est indisponible.

## Ouvrir Meshloom

Si un écran est branché sur le Pi, il affiche un message « Meshloom is ready » avec les adresses à utiliser :

```
http://meshloom.local:8000
http://<adresse-ip>:8000
```

Sans écran, utilisez le nom en `.local` depuis un autre appareil du même réseau, ou l’adresse que votre box a donnée au Pi. Le nom en `.local` est le nom d’hôte choisi dans Imager, suivi de `.local` (`meshloom` si vous avez laissé la valeur par défaut). Branchez ensuite la radio et choisissez-la dans **Réglages > Radio** (voir [Premier lancement](/docs/first-run/)).

Sans aucun réseau, ouvrez `http://127.0.0.1:8000` sur le Pi lui-même. La messagerie et la radio fonctionnent hors ligne. Les cartes, Meshloom Community, Web Push et les mises à jour depuis l’application attendent que le Pi puisse joindre Internet.

Les bots, qui exécutent du code sur la machine, sont désactivés sur cette image, comme dans tout paquet Linux. Voir [Un réseau de confiance](/docs/trust/).

## Mettre à jour

Quand l’image (ou toute installation par paquet Linux) sait se mettre à jour seule, **Réglages > Mises à jour** propose **Installer maintenant** et une mise à jour automatique facultative. Cela ne met à jour que Meshloom, depuis le dépôt Meshloom signé, jamais tout le système d’exploitation. Les images à partir de la 4.18 sont prêtes pour cela dès la sortie. Sur une image plus ancienne, un seul `sudo apt update && sudo apt install meshloom` l’active.

Si **Réglages > Mises à jour** indique qu’il faut mettre à jour Meshloom à la main, suivez les étapes qu’il montre, ou relancez l’installeur pour qu’il rétablisse l’assistant manquant :

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app)"
```

Les add-ons Home Assistant se mettent à jour dans Home Assistant, pas avec ce bouton.

Une nouvelle base rejoint Meshloom Community par défaut, comme sur toute autre installation. Rien n’est publié tant que vous n’avez pas saisi un code d’aéroport dans **Réglages > Meshloom Community**.
