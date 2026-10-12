---
title: Transports radio
description: USB série, TCP ou Bluetooth, choisis dans l’interface web, et comment Meshloom se reconnecte.
level: deep
order: 11
---

Meshloom parle à une seule radio, par un seul lien à la fois. Ce lien s’appelle le transport. Il en existe trois : série (un câble USB), TCP (la radio est joignable par le réseau) et BLE (Bluetooth Low Energy). Le choix se fait dans l’interface web, pas dans des variables d’environnement. Il est enregistré dans la base de données.

Tant qu’aucun transport n’est enregistré, la radio reste en pause et la barre d’état l’indique.

## Choisir le transport

1. Ouvrez **Réglages > Radio**. Sur une installation neuve, la bannière **Connecter une radio** ou le bouton **Connecter** de la barre d’état y mènent.
2. Dans **Transport**, choisissez **Série**, **TCP** ou **Bluetooth**.
3. Remplissez les champs qui apparaissent, puis appuyez sur **Enregistrer et connecter**.

| Transport | Champs | Remarques |
|---|---|---|
| Série | **Port série**, **Débit** | Le port se choisit dans une liste. **Détection automatique** (le choix par défaut) convient quand une seule radio est branchée. **Actualiser les ports** relance la recherche. Le débit est `115200` ; ne le changez que si votre firmware l’exige. |
| TCP | **Hôte TCP**, **Port TCP** | L’hôte est une adresse IP ou un nom. Le port vaut `5000` par défaut. |
| Bluetooth | **Adresse BLE**, **PIN BLE** | **Rechercher** liste les radios proches ; cliquez sur l’une d’elles pour remplir l’adresse. Le PIN est obligatoire (il s’affiche sur l’écran de la radio). Une fois enregistré, laissez le champ PIN vide pour le conserver. |

Ne définissez pas `MESHCORE_SERIAL_PORT`, `MESHCORE_TCP_HOST` ni `MESHCORE_BLE_ADDRESS` pour choisir un transport : ces variables ne sont plus lues. Une base existante qui les contenait peut les importer une seule fois, au premier démarrage après une mise à jour.

Si un transport est inutilisable sur cette machine (pas de port série géré, pas d’adaptateur Bluetooth), le panneau en donne la raison à côté du champ.

## Série (USB)

C’est la configuration habituelle : la radio est branchée sur la machine qui fait tourner Meshloom.

Pour installer un firmware MeshCore sur une radio depuis votre navigateur (companion, répéteur ou serveur de salon), utilisez le [flasher](/flasher/).

Un port série ne peut servir qu’à un seul programme à la fois. Si une autre application MeshCore, une console série ou un second Meshloom le tient, les tentatives de connexion échouent en boucle. Après trois lignes « Serial Connection started » identiques, Meshloom cesse de les répéter et écrit un seul `WARNING` mentionnant une possible contention du port.

Avec Docker, donnez au conteneur l’accès à l’appareil dans `docker-compose.yml`. Le transport reste choisi dans l’interface ; dans le conteneur, la radio se trouve à `/dev/meshcore-radio` :

```yaml
devices:
  - /dev/serial/by-id/your-meshcore-radio:/dev/meshcore-radio
```

Si un chemin `by-id` contient un `:`, Docker Compose ne sait pas le lire. Utilisez un autre chemin, ou un lien symbolique sans deux-points côté hôte.

## TCP

Utilisez TCP quand la radio est joignable par le réseau, par exemple une radio dotée du Wi-Fi, ou un autre Meshloom qui partage sa radio (voir plus bas).

Meshloom ne peut pas supposer qu’il est le seul programme à parler à une radio sur le réseau. En TCP, il réécrit donc l’emplacement du canal dans la radio avant chaque message de canal, au lieu de se fier à sa propre mémoire du contenu de chaque emplacement. Voir [Radio, contacts et canaux](/docs/deep/radio/).

## Bluetooth (BLE)

Le BLE exige l’adresse et le PIN. Avec Docker, il demande en général des modifications manuelles du fichier Compose (accès au Bluetooth de l’hôte, parfois des privilèges supplémentaires). Avec Docker en root sur Linux, l’installeur mappe le socket D-Bus de l’hôte quand il le trouve, mais cela ne suffit pas toujours. Si la radio contient beaucoup de contacts, la première lecture de sa table de contacts peut dépasser le délai. Meshloom charge quand même vos favoris et vos contacts récents du mieux possible, et `MESHCORE_LOAD_WITH_AUTOEVICT=true` est prévu pour ce cas.

## Partager votre radio avec d’autres applications

**Réglages > Proxy** (**Proxy radio**) permet à Meshloom de se faire passer pour une radio companion MeshCore en TCP. Une application mobile, ou un autre Meshloom réglé sur le transport TCP, peut alors utiliser votre radio à travers celui-ci. Il reste désactivé tant que vous ne cochez pas **Activer le proxy companion TCP**.

| Réglage | Valeur par défaut |
|---|---|
| Adresse d’écoute | `0.0.0.0` (toutes les interfaces réseau) |
| Port | `5001` |
| Clients max | `8` |

Le protocole n’a aucune authentification : ne l’utilisez que sur un réseau de confiance. Un Meshloom ne peut pas prendre son propre proxy pour radio. Avec l’add-on Home Assistant, le port est fixé par l’add-on (voir [Installer](/docs/install/)).

## Ce que fait l’installeur

Le script Linux n’enregistre aucun transport. Une installation native ne pose aucune question sur la radio : vous la réglez dans l’interface web après le premier démarrage. Avec Docker, l’installeur ne cherche des appareils série que si Docker tourne en root sur Linux. S’il en trouve un seul, il le mappe automatiquement. S’il y en a plusieurs, il demande lequel. Voir [Autres chemins d’installation](/docs/deep/install-paths/).

## Reconnexion

Une fois connecté, Meshloom vérifie le lien toutes les cinq secondes et se reconnecte tout seul s’il tombe.

- **Reconnecter** (dans la barre d’état, ou dans **Réglages > Radio**) force une tentative immédiate.
- **Déconnecter** ferme le lien et suspend les tentatives automatiques, par exemple pour laisser une autre application utiliser la radio. Appuyez sur **Reconnecter** pour reprendre.

À chaque retour du lien, Meshloom refait son travail de démarrage : il se remet à l’écoute des événements de la radio, lit la clé privée en mémoire, règle l’horloge de la radio, puis synchronise contacts et canaux. Ce travail a une limite de 5 minutes. Si elle est dépassée, Meshloom réessaie une fois, puis signale que le démarrage de la radio semble bloqué et vous demande de redémarrer la radio et le serveur. Si la radio est seulement injoignable un moment, la surveillance réessaie toutes les cinq secondes.
