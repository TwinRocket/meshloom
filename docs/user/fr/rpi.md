---
title: Image Raspberry Pi
description: Flasher une carte Lite 64-bit avec Meshloom déjà installé, puis renseigner le Wi-Fi dans Imager.
level: start
order: 2.5
---

L’image publiée est Raspberry Pi OS Lite 64-bit avec Meshloom déjà installé. Le premier démarrage n’a pas besoin d’Internet.

## Quel Pi

| Votre carte | Comment installer |
|---|---|
| Pi 3B / 3B+ / 3A+, Compute Module 3, Zero 2 W, Pi 4, Pi 5 | Cette image. C’est le chemin le plus rapide et il n’y a rien d’autre à faire. |
| Pi 2, ou tout Pi tournant déjà sous Raspberry Pi OS 32-bit | La [ligne de commande](/fr/docs/install/). Elle détecte l’architecture et installe le paquet armhf. |
| Pi 1, Compute Module 1, Zero et Zero W de première génération | Non supportés. Ces cartes sont en ARMv6 et aucun paquet n’est construit pour elles. |

Préférez l’image 64-bit sur une carte capable de la faire tourner : elle est plus rapide, et le paquet 32-bit est construit sous émulation. Un Pi 2 ne tourne qu’en 32-bit, la ligne de commande est donc son chemin.

Le paquet 32-bit est récent, publié en 4.12.2. Les versions antérieures ne pouvaient pas s’installer sur un système 32-bit.

### À quoi s’attendre sur un Pi 2 ou un Pi 3

Personne n’a publié de mesures sur ces cartes : ce qui suit est donc ce que l’on
sait, pas une promesse.

Les deux ont 1 Go de mémoire, et l’essentiel de ce qui paraît coûteux dans
Meshloom ne tourne pas sur le Pi : la carte, la vue 3D et le flux de paquets sont
dessinés par le navigateur depuis lequel vous ouvrez l’interface. Le Pi, lui, fait
tourner le serveur, le lien radio et une base SQLite.

La limite la plus probable est la carte SD. La base est écrite à chaque message et
à chaque paquet observé, ce qu’une carte bon marché fait lentement et qui l’use.
Si vous comptez laisser un nœud tourner pendant des mois, une bonne carte, ou un
SSD USB, compte davantage que le modèle de carte.

Un Pi 2 est la plus lente des cartes supportées et l’interface s’en ressentira à
mesure que l’historique grossit. Si vous en testez un, dites-nous à partir de quand
ça cesse d’être confortable : ce chiffre, personne ne l’a encore.

## Flasher

1. Téléchargez `meshloom-rpi-lite-arm64.img.xz` **et** `meshloom.rpi-imager-manifest` depuis la [release GitHub](https://github.com/TwinRocket/meshloom/releases). Le workflow Release les joint après le `.deb` arm64 (ils peuvent arriver un peu après les paquets).
2. Ouvrez **Raspberry Pi Imager 2.0.6 ou plus récent**. Les versions 1.9.x n’écrivent pas la personnalisation cloud-init de Trixie : hostname, Wi-Fi et SSH sont ignorés.
3. Ouvrez le manifeste (double-clic, ou `rpi-imager --repo chemin/vers/meshloom.rpi-imager-manifest`). Ce fichier pose `init_format: cloudinit-rpi`. Ne choisissez **pas** *Use custom* sur le `.img.xz` seul : Imager 2.x suppose alors aucune personnalisation et ignore Wi-Fi, utilisateur et SSH.
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
