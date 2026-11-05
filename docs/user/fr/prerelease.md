---
title: Essayer la pré-version
description: Installer une version candidate de Meshloom ou y passer en une commande, et comment revenir en arrière.
level: start
order: 2.9
---

Une pré-version (une version candidate, `5.0.0-rc.1` par exemple) est la prochaine version avant qu’elle soit finale. Elle peut avoir des bugs qu’une version stable n’aurait pas. Une machine qui ne lance pas la commande ci-dessous n’en reçoit jamais.

## Avant de commencer

Copiez d’abord vos données, ou essayez la pré-version sur une nouvelle instance. Si vous venez de la 4.18, lisez [Passer de la 4.18 à la 5.0](/docs/upgrade-4-18/).

## Installer la pré-version ou y passer

Collez ceci dans un terminal :

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app/next)"
```

C’est l’installateur habituel, avec les mêmes questions. Quand aucune pré-version n’est ouverte, il le dit et ne change rien.

Il fonctionne avec les paquets `apt` et `dnf` sur les machines 64 bits, et avec Docker. Les machines 32 bits n’ont pas de paquet : utilisez Docker.

## Passer à une pré-version plus récente

Relancez la même commande. **Réglages > Mises à jour** ne propose pas les pré-versions, et ne propose jamais de revenir à la 4.18.

## Quand la version finale sort

**Réglages > Mises à jour** la propose. Avec le paquet `apt` ou `dnf`, relancez ensuite la commande d’installation habituelle, pour que la machine quitte la source de paquets des pré-versions :

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app)"
```

## Revenir en arrière

Deux possibilités :

- Utilisez une nouvelle instance, et gardez celle-ci telle quelle.
- Restaurez votre propre copie des données sur une installation 4.18 : les étapes sont dans [Revenir à la 4.18](/docs/upgrade-4-18/#revenir-à-la-418).

N’utilisez pas le script d’installation de la 4.18 sur une machine en pré-version. Il ne connaît pas les pré-versions, et peut démarrer la 4.18 sur une base qu’elle ne sait pas lire. Les versions suivantes de l’installateur refusent et ne changent rien.
