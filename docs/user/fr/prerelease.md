---
title: Essayer la pré-version
description: Passer à une version candidate de Meshloom en une commande, la tenir à jour, arriver à la version finale et revenir au canal stable.
level: start
order: 2.9
---

Une pré-version (une version candidate, `5.0.0-rc.1` par exemple) est la prochaine version avant qu’elle soit finale. L’essayer aide, et elle peut avoir des bugs qu’une version stable n’aurait pas. Si cette machine est votre seul accès au réseau maillé, restez sur le canal stable.

Rien ne change sans que vous le demandiez : une machine sur le canal stable ne reçoit jamais de pré-version.

## Passer à la pré-version

Collez ceci dans un terminal, sur une machine neuve ou sur une machine en 4.18 :

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app/next)"
```

C’est l’installateur habituel, avec les mêmes questions. Le récapitulatif indique le canal avant de commencer. Quand aucune pré-version n’est ouverte, la commande le dit et ne change rien.

Si vous venez de la 4.18, lisez d’abord la page **Passer de la 4.18 à la 5.0**. Meshloom fait une copie de votre base de données avant de la modifier, et cette copie est votre chemin de retour.

## À quoi cela s’applique

| Installation | Pré-version |
|---|---|
| Paquet Linux (`apt`, `dnf`), 64 bits | Oui |
| Docker | Oui. Avec l’assistant de mise à jour, les mises à jour se font depuis **Réglages > Mises à jour**. Sans lui (Docker Desktop, Docker sans root), relancez la commande pour mettre à jour |
| Raspberry Pi 32 bits et autres machines armhf | Pas de paquet : le choix du service s’arrête, le dit et ne change rien. Docker peut y faire tourner une pré-version |
| Add-on Home Assistant | Non. L’add-on reste sur les versions stables et recevra la 5.0.0 quand elle sera finale |
| Installation depuis les sources | Pas de canal : vous choisissez ce que vous récupérez |

## Rester à jour

**Réglages > Mises à jour** indique le canal. Sur le canal pré-version, la page propose chaque version candidate plus récente, puis la version finale, et la mise à jour se fait comme d’habitude.

Le canal se choisit sur la machine, par la commande d’installation. Meshloom le lit et ne peut pas le changer : personne ne peut donc faire changer cette machine de canal depuis l’interface web.

## Quand la version finale sort

La version finale vous arrive comme une mise à jour normale. Vous avez alors le choix :

- **Revenir au canal stable.** Votre version ne change pas, et seules les versions stables seront proposées :

  ```bash
  /bin/bash -c "$(curl -fsSL https://get.meshloom.app)" -- --channel stable
  ```

- **Rester sur le canal pré-version.** La prochaine version candidate sera proposée dès qu’il y en aura une. Le canal ne change jamais tout seul.

## Revenir au stable

Tant que vous êtes sur une version candidate, ceci ramène la machine à la version stable actuelle :

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app/next)" -- --channel stable
```

La commande montre ce qu’elle va faire et demande d’abord. Elle réinstalle la version stable et remet en place la [copie de votre base faite avant la mise à niveau](/docs/deep/install-paths/#la-copie-faite-avant-une-mise-à-niveau). **Ce que vous avez fait depuis cette copie (messages entendus, réglages, comptes) ne revient pas avec elle.** La base que vous utilisiez est mise de côté, à côté de la copie, sous un nom contenant `aside`.

Si aucune copie ne convient à la version stable, la commande le dit. Elle peut alors repartir d’une base vide, seulement si vous répondez oui ; votre base actuelle est quand même mise de côté.

La commande d’installation habituelle, sans `--channel stable`, n’est pas le chemin de retour : elle peut s’arrêter sur une erreur de paquet, et votre version ne change pas. Utilisez la commande ci-dessus.

Docker fonctionne de la même façon : lancez la commande sur la machine, elle remet l’image stable.
