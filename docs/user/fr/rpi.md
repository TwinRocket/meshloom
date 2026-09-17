---
title: Image Raspberry Pi
description: Flasher une carte Lite 64-bit avec Meshloom déjà installé, puis renseigner le Wi-Fi dans Imager.
level: start
order: 2.5
---

L’image publiée est Raspberry Pi OS Lite 64-bit avec Meshloom déjà installé. Le premier démarrage n’a pas besoin d’Internet. Elle convient à un Pi 3B / 3B+ / 3A+, Compute Module 3, Zero 2 W, Pi 4 ou Pi 5. Flashez bien l’image **64-bit** même sur un Pi 3 : l’OS 32-bit ne peut pas installer le paquet Meshloom.

## Flasher

1. Téléchargez `meshloom-rpi-lite-arm64.img.xz` depuis la [release GitHub](https://github.com/TwinRocket/meshloom/releases). Le workflow Release la joint après le `.deb` arm64 (elle peut arriver un peu après les paquets).
2. Ouvrez **Raspberry Pi Imager 2.0.6 ou plus récent**. Les versions 1.9.x n’écrivent pas la personnalisation cloud-init de Trixie : hostname, Wi-Fi et SSH sont ignorés.
3. Choisissez l’image Meshloom (ou *Use custom*). Si Imager cache le panneau de personnalisation, chargez le manifeste `cloudinit-rpi` livré à côté de l’image.
4. Renseignez hostname, utilisateur, clé SSH et Wi-Fi là. Ces secrets ne sont pas dans le téléchargement.
5. Écrivez la carte, démarrez le Pi.

Au premier boot, Ethernet et Wi-Fi sont optionnels : la machine démarre même sans câble ni SSID.

## Ouvrir Meshloom

Si un écran est branché, la console affiche :

```
http://meshloom.local:8000
http://<ip-lan>:8000
```

Sans écran, utilisez le nom `.local` depuis un autre appareil du même réseau, ou l’adresse que le routeur a donnée au Pi. Puis choisissez la radio sous **Réglages → Radio**.

Sans aucun réseau, ouvrez `http://127.0.0.1:8000` sur le Pi. La messagerie et la radio fonctionnent hors ligne. Carte, Community, Web Push et mises à jour depuis l’interface attendent Internet.

## Mettre à jour

Quand l’image (ou une install paquet Linux) peut appliquer les mises à jour, **Réglages → À propos** propose *Installer* et une mise à jour automatique optionnelle. Ce chemin ne met à jour que Meshloom, pas tout le système.

Si À propos indique que vous devez mettre à jour Meshloom manuellement, suivez la recette affichée, ou relancez l’installeur Linux pour qu’il pose le helper manquant :

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app)"
```

Les add-ons Home Assistant se mettent à jour dans Home Assistant, pas depuis ce bouton.

`MESHLOOM_COMMUNITY` n’est pas posé sur l’image. Une base neuve rejoint Community par défaut, comme partout ailleurs.
