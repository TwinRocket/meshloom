---
title: Essayer la pré-version
description: Installer une version candidate de Meshloom ou y passer en une commande, et comment revenir en arrière.
level: start
order: 2.9
---

Une pré-version (une version candidate, `5.0.0-rc.1` par exemple) est la prochaine version avant qu’elle soit finale. Elle peut avoir des bugs qu’une version stable n’aurait pas. Une machine qui ne lance pas la commande ci-dessous n’en reçoit jamais.

## Avant de commencer

Copiez d’abord vos données, ou essayez la pré-version sur une nouvelle instance. Si vous venez de la 4.18, lisez [Passer de la 4.18 à la 5.0](/docs/upgrade-4-18/). Ce qui change et les limites connues sont dans les [notes de version](https://github.com/TwinRocket/meshloom/releases).

## Installer la pré-version ou y passer

Collez ceci dans un terminal :

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app/next)"
```

C’est l’installateur habituel, avec les mêmes questions. Quand aucune pré-version n’est ouverte, il le dit et ne change rien.

Il fonctionne avec les paquets `apt` et `dnf` sur les machines 64 bits, et avec Docker. Les machines 32 bits n’ont pas de paquet : Docker y couvre l’ARMv7. L’ARMv6 et le x86 32 bits n’ont pas d’image.

## Passer à une pré-version plus récente

Relancez la même commande. **Réglages > Mises à jour** ne regarde que les versions stables : la page n’annonce pas une pré-version plus récente, et ne propose jamais de revenir à la 4.18.

## Quand la version finale sort

**Réglages > Mises à jour** la propose, et la machine reste sur la pré-version tant que vous n’acceptez pas.

- **Docker :** la mise à jour épingle cette version finale, et la machine suit ensuite les versions stables.
- **Paquet `apt` ou `dnf` :** la machine reste sur la source de paquets des pré-versions. Une mise à jour, depuis **Réglages > Mises à jour** ou avec `apt upgrade` ou `dnf upgrade`, installe ce que cette source sert. Si une pré-version suivante est déjà sortie, c’est elle que vous obtenez, même si la page proposait la version finale.

Avec un paquet, lancez donc la commande d’installation habituelle une fois la version finale sortie, pas avant : d’ici là, `get.meshloom.app` sert l’installeur de la 4.18 (voir plus bas). Restez sur la pré-version en attendant ; relancer `get.meshloom.app/next` la tient à jour. Une fois la version finale sortie, la commande d’installation habituelle revient à la source stable. Elle installe aussi la version finale si la machine est encore sur une pré-version, avec `apt` comme avec `dnf` (l’installeur stable met à jour un paquet `rpm` installé et installe `bubblewrap` s’il manque, pour que les extensions gardent leur isolation) :

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app)"
```

## Revenir en arrière

Deux possibilités :

- Utilisez une nouvelle instance, et gardez celle-ci telle quelle.
- Restaurez votre propre copie des données sur une installation neuve de la 4.18 : les étapes sont dans [Revenir à la 4.18](/docs/upgrade-4-18/#revenir-à-la-418).

Ne lancez jamais le script d’installation de la 4.18 sur une machine en pré-version, et tant que la 5.0.0 n’est pas sortie, c’est lui que sert `get.meshloom.app`. Il ne connaît pas les pré-versions : avec Docker, il propose une « mise à niveau » vers la 4.18 et la démarre sur la base migrée, sans demander le compte de la 5.0 ; avec un paquet, lancé depuis un compte `sudo`, il peut réécrire `/etc/meshloom/meshloom.env` et perdre les lignes que vous y avez ajoutées. Relancer `get.meshloom.app/next` remet la pré-version. Les versions suivantes de l’installateur refusent et ne changent rien.
