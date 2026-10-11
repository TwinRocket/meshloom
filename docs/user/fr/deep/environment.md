---
title: Variables et réglages
description: Ce qui se décide dans l’environnement, ce qui vit dans la base.
level: deep
order: 14
---

Meshloom se configure à deux endroits, et ils ne se recouvrent pas.

L’**environnement** contient ce qui doit être connu avant que quoi que ce soit démarre : où écrire la base de données, si les bots sont autorisés, si un mot de passe protège la page, et quels interrupteurs de diagnostic sont actifs. Le modifier impose un redémarrage.

Les **réglages** vivent dans la base de données. On les change dans l’interface web et ils s’appliquent tout de suite. La connexion à la radio en fait partie : c’est un réglage, pas une variable d’environnement. Voir [Transports radio](/docs/deep/transports/).

## Où écrire les variables

| Installation | Où | Après un changement |
|--------------|----|---------------------|
| Paquet Linux (`.deb` / `.rpm`) | `/etc/meshloom/meshloom.env`, propriété de root : à modifier avec `sudo` | `sudo systemctl restart meshloom` |
| Service installé depuis un dépôt cloné | Pas de fichier. Ajoutez des lignes `Environment=NOM=valeur` sous `[Service]` avec `sudo systemctl edit meshloom` | `sudo systemctl restart meshloom` |
| Docker | bloc `environment:` du fichier Compose, ou un `.env` à côté | `docker compose up -d` |
| Dépôt cloné lancé à la main | L’environnement du shell qui lance `uv run uvicorn` | Relancer la commande |

Rejouer `install_service.sh` réécrit l’unité systemd principale d’une installation depuis les sources. Un fichier complémentaire créé avec `systemctl edit` est conservé, et `meshloom.env` n’est pas concerné.

## Connexion radio

La connexion se choisit dans **Réglages → Radio** et se stocke dans la base :

| Réglage | Défaut | Description |
|---------|--------|-------------|
| `radio_transport` | *(non défini : en pause)* | `serial`, `tcp` ou `ble` |
| `radio_serial_port` | vide | Port série. Vide = détection automatique |
| `radio_serial_baudrate` | `115200` | Vitesse série |
| `radio_tcp_host` | vide | Adresse de la radio sur le réseau |
| `radio_tcp_port` | `5000` | Port TCP |
| `radio_ble_address` | vide | Adresse Bluetooth de la radio |
| `radio_ble_pin` | vide | Code PIN Bluetooth, obligatoire en Bluetooth |

Tant qu’aucune connexion n’est choisie, la radio reste en pause. Ne définissez pas `MESHCORE_SERIAL_PORT`, `MESHCORE_TCP_HOST` ni `MESHCORE_BLE_ADDRESS`. Une base neuve les ignore. Une base existante sans connexion enregistrée les recopie une seule fois, pour reprendre une ancienne configuration, puis ne les lit plus jamais.

## Serveur et données

| Variable | Défaut | Description |
|----------|--------|-------------|
| `MESHCORE_DATABASE_PATH` | `data/meshcore.db` | Emplacement de la base SQLite |
| `MESHCORE_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` ou `ERROR` |
| `MESHCORE_VAPID_SUBJECT` | `mailto:noreply@meshcore.local` | Adresse de contact des notifications push quand le champ des Réglages est vide (voir plus bas) |
| `MESHCORE_PUBLIC_URL` | *(vide)* | Acceptée, mais aucune fonction ne l’utilise aujourd’hui |
| `MESHCORE_MANAGED_PORTS` | `false` | Posée par un hôte qui décide lui-même des ports d’écoute, comme l’add-on Home Assistant. Les Réglages affichent alors le port du proxy en lecture seule |
| `MESHCORE_RADIO_PROXY_PORT` | *(non défini)* | Port du proxy radio choisi par l’hôte. Utilisée seulement avec `MESHCORE_MANAGED_PORTS=true`, où elle remplace le port enregistré à chaque démarrage. L’add-on Home Assistant la fixe à `5051` |
| `MESHCORE_EMBEDDABLE_SAME_ORIGIN` | `false` | Autorise une page de la même origine à afficher Meshloom dans un cadre. La barre latérale de Home Assistant fait exactement cela. Par défaut, Meshloom refuse d’être mis dans un cadre, ce qui donne un panneau blanc alors que le journal montre un `200` parfaitement sain |

Le contact VAPID se règle de préférence dans **Réglages → Notifications**. `MESHCORE_VAPID_SUBJECT` n’est utilisé que si ce champ est vide. Apple exige une adresse `mailto:` ou `https:` réelle et rejette le `.local` par défaut avec `403 BadJwtToken`. Voir [Notifications push](/docs/deep/push/) et la [documentation Apple](https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers).

## Meshloom Community

| Variable | Défaut | Description |
|----------|--------|-------------|
| `MESHLOOM_COMMUNITY` | *(activé)* | Ne concerne qu’une base toute neuve. Absente ou `1` : démarre avec Community activé. `0`, `false`, `off` ou `no` : démarre désactivé. Les bases existantes ne sont jamais basculées |
| `MESHLOOM_COMMUNITY_IATA` | *(vide)* | Code d’aéroport à trois lettres. Tant qu’elle est définie, elle remplace le code enregistré dans l’interface |
| `MESHLOOM_COMMUNITY_BROKER_HOST` | *(vide)* | Remplace le serveur de publication de Community (par défaut `mqtt.meshloom.app`) tant qu’elle est définie |
| `MESHLOOM_COMMUNITY_API_BASE` | *(vide)* | Remplace l’adresse de l’API Community (par défaut `https://api.meshloom.app`) tant qu’elle est définie |
| `MESHLOOM_COMMUNITY_LOCKED` | *(vide)* | Seule la valeur `1` compte : l’interface ne peut alors pas activer Community |

Voir [Meshloom Community](/docs/deep/community/).

## Sécurité

| Variable | Défaut | Description |
|----------|--------|-------------|
| `MESHCORE_DISABLE_BOTS` | `false` | Désactive le système de bots au démarrage. Le paquet Linux la met à `true` dans `meshloom.env` |
| `MESHCORE_BASIC_AUTH_USERNAME` | *(vide)* | Nom d’utilisateur demandé par le navigateur pour toute l’application |
| `MESHCORE_BASIC_AUTH_PASSWORD` | *(vide)* | Mot de passe associé |
| `MESHCORE_ENABLE_LOCAL_PRIVATE_KEY_EXPORT` | `false` | Autorise `GET /api/radio/private-key` |

Les deux variables d’authentification doivent être définies ensemble. Définir l’une sans l’autre empêche Meshloom de démarrer. Ce que chaque option protège, et ce qu’elle ne protège pas, est expliqué dans [Sécurité](/docs/deep/security/).

## Installation et mises à jour

L’installateur les définit pour vous. On les modifie rarement à la main.

| Variable | Description |
|----------|-------------|
| `MESHLOOM_INSTALL_KIND` | Comment Meshloom a été installé : `package`, `compose`, `addon`, `container` ou `source`. Cela décide si **Réglages → Mises à jour** peut installer une mise à jour |
| `MESHLOOM_UPDATE_HELPER` | `compose` pour une pile Docker gérée par l’assistant de mise à jour, `none` pour désactiver l’installation depuis l’interface |
| `MESHLOOM_RUN_AS_USER` | Docker uniquement. Un nombre comme `10001` fait tourner Meshloom sous cet utilisateur plutôt que root. Voir [Autres chemins d’installation](/docs/deep/install-paths/) |
| `MESHLOOM_IMAGE` | Docker uniquement. L’image que Compose démarre, idéalement figée par son empreinte |

## Diagnostic et contournements

Ces variables servent à diagnostiquer ou contourner des radios qui se comportent mal. Aucune n’est nécessaire en fonctionnement normal.

| Variable | Défaut | Description |
|----------|--------|-------------|
| `MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK` | `false` | Fait vérifier les messages en attente sur la radio toutes les 10 secondes au lieu de toutes les heures |
| `MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE` | `false` | Réécrit le salon dans la radio avant chaque envoi vers un salon |
| `MESHCORE_LOAD_WITH_AUTOEVICT` | `false` | Laisse la radio supprimer elle-même ses plus anciens contacts quand sa table est pleine |
| `MESHCORE_SKIP_POST_CONNECT_SYNC` | `false` | Après la connexion, saute la synchronisation des contacts et des salons, l’annonce de démarrage, la lecture des messages en attente sur la radio, et les tâches périodiques (synchronisation, annonces, audit, télémétrie) |
| `__CLOWNTOWN_DO_CLOCK_WRAPAROUND` | `false` | Très expérimental : tente un débordement d’horloge sur 32 bits |

L’audit tourne toujours ; la variable de sondage ne change que sa fréquence. Forcer la réécriture des salons ralentit un peu chaque envoi vers un salon. La variable « skip » est une sortie de secours pour le diagnostic : les gestionnaires d’événements, l’export de clé, la synchronisation d’horloge et la récupération automatique des messages continuent. La dernière est un dernier recours pour une radio dont l’horloge est coincée dans le futur, et peut ne pas être sûre sur toutes les cartes.

## Réglages stockés dans la base

On les change dans l’interface web. Rien de tout cela ne se définit par l’environnement.

| Où | Ce que cela commande |
|----|----------------------|
| **Radio** | La connexion (voir plus haut). Onglet **Messagerie** : `max_radio_contacts`, `flood_scope`, `known_regions`, `auto_resend_channel`. Onglet **Annonces** : `advert_interval` (saisi en heures dans l’interface, stocké en secondes ; `0` le désactive, toute autre valeur vaut au moins une heure) |
| **Base de données** | `auto_decrypt_dm_on_advert` |
| **Gestion radio-application** | `blocked_keys`, `blocked_names`, `discovery_blocked_types`, `tracked_telemetry_repeaters`, `tracked_telemetry_contacts` (8 de chaque au maximum), `telemetry_interval_hours`, `telemetry_routed_hourly`, `stale_contact_days` |
| **Alertes** | `telemetry_alert_rules` |
| **Notifications** | `push_defaults`, `push_conversation_overrides`, `vapid_subject`, `notification_destinations` (e-mail et webhook) |
| **Mises à jour** | `auto_update`, `auto_update_window_start`, `auto_update_window_end`, `auto_update_weekdays` |
| **Configuration locale** et **Navigation** | `ui_preferences` : thème, étiquette de l’instance, barre de gauche |
| Canaux découverts | `rejected_channels` : les canaux que vous avez refusés |
| Hors interface | `raw_packet_retention_days` : supprime automatiquement les paquets illisibles plus vieux que ce nombre de jours (`0` garde tout). À régler par `PATCH /api/settings` |

La paire de clés VAPID (`vapid_private_key` et `vapid_public_key`) est créée au premier démarrage. L’interface est la façon prévue de modifier les réglages ; certains peuvent aussi être lus et modifiés avec `GET` et `PATCH /api/settings`.

Les intégrations (MQTT, bots, webhooks, Apprise, SQS) sont stockées à part, dans `fanout_configs`. Meshloom Community a son propre point d’accès, `/api/community`. Voir [Fanout](/docs/deep/fanout/), [Meshloom Community](/docs/deep/community/) et [Radio, contacts et salons](/docs/deep/radio/).
