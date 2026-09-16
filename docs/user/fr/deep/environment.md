---
title: Variables et réglages
description: Ce qui se décide dans l’environnement, ce qui vit dans la base.
level: deep
order: 14
---

Meshloom a deux surfaces de configuration, et elles ne se recouvrent pas.

L’**environnement** décide de ce qui doit être connu avant que quoi que ce soit tourne : où écrire la base, si les bots sont autorisés, si une authentification s’applique, quels contournements de diagnostic sont actifs. Ces variables sont lues au démarrage. Les changer implique de redémarrer le service.

Les **réglages d’exécution** vivent dans la table `app_settings` de la base et se modifient à chaud depuis l’interface ou via `GET` / `PATCH /api/settings`. Le transport radio est un réglage d’exécution, pas une variable d’environnement. Voir [Transports radio](/docs/deep/transports/).

## Où écrire les variables

| Installation | Emplacement |
|--------------|-------------|
| Paquet systemd | `/etc/meshloom/meshloom.env` |
| Docker | bloc `environment:` du Compose, ou un `.env` |
| Checkout | l’environnement du shell qui lance `uv run uvicorn` |

Pour l’installation par paquet, `bash scripts/setup/install_service.sh` peut être rejoué pour réécrire ce fichier sans l’éditer à la main.

## Connexion radio

Le transport se configure dans l’interface et est stocké dans `app_settings` :

| Colonne | Défaut | Description |
|---------|--------|-------------|
| `radio_transport` | *(non défini / en pause)* | `serial`, `tcp` ou `ble` |
| `radio_serial_port` | vide | Port série ; vide = auto-détection |
| `radio_serial_baudrate` | `115200` | Débit série |
| `radio_tcp_host` | vide | Hôte TCP de la radio |
| `radio_tcp_port` | `5000` | Port TCP |
| `radio_ble_address` | vide | Adresse BLE de la radio |
| `radio_ble_pin` | vide | Code PIN BLE, obligatoire avec BLE |

Tant que `radio_transport` n’est pas défini, la radio reste en pause. Ne définissez pas `MESHCORE_SERIAL_PORT`, `MESHCORE_TCP_HOST` ni `MESHCORE_BLE_ADDRESS` pour choisir un transport.

## Serveur et données

| Variable | Défaut | Description |
|----------|--------|-------------|
| `MESHCORE_DATABASE_PATH` | `data/meshcore.db` | Emplacement de la base SQLite |
| `MESHCORE_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `MESHCORE_VAPID_SUBJECT` | `mailto:noreply@meshcore.local` | Repli du sujet VAPID si `app_settings.vapid_subject` est vide |
| `MESHCORE_PUBLIC_URL` | *(vide)* | L'adresse à laquelle cette instance est jointe depuis l'extérieur. Prime sur les en-têtes `X-Forwarded-*`, qu'un tunnel peut réécrire ou supprimer — à renseigner quand un lien construit à partir de ce qui est arrivé pointerait vers un endroit joignable seulement de l'intérieur |
| `MESHCORE_MANAGED_PORTS` | `false` | Posée par un hôte qui décide lui-même des ports d'écoute, comme l'add-on Home Assistant. Le port du proxy vient alors de l'hôte, et le champ dans les réglages le dit au lieu d'accepter une valeur sans effet |
| `MESHLOOM_COMMUNITY` | *(on pour une base neuve)* | Seed Community sur une base toute neuve. Absent ou `1` = on ; `0` / `false` / `off` = opt-out. Les bases existantes ne sont jamais basculées |
| `MESHLOOM_COMMUNITY_IATA` | *(vide)* | Code IATA à 3 lettres optionnel à seeder sur une base neuve |
| `MESHLOOM_COMMUNITY_LOCKED` | `false` | À `1`, l’interface ne peut pas activer Community |

Le sujet VAPID se règle d’abord dans **Réglages → Notifications**. `MESHCORE_VAPID_SUBJECT` n’est utilisé que si ce champ est vide. Apple exige un `mailto:` ou un `https:` réel : APNs rejette le domaine `.local` par défaut avec `403 BadJwtToken`. Google FCM l’accepte. Voir [Notifications push](/docs/deep/push/) et la [documentation Apple](https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers).

## Sécurité

| Variable | Défaut | Description |
|----------|--------|-------------|
| `MESHCORE_DISABLE_BOTS` | `false` | Désactive entièrement le système de bots au démarrage |
| `MESHCORE_BASIC_AUTH_USERNAME` | *(vide)* | Identifiant HTTP Basic pour toute l’application |
| `MESHCORE_BASIC_AUTH_PASSWORD` | *(vide)* | Mot de passe associé |
| `MESHCORE_ENABLE_LOCAL_PRIVATE_KEY_EXPORT` | `false` | Autorise `GET /api/radio/private-key` |

Les deux variables d’authentification doivent être définies ensemble. L’une sans l’autre est une erreur de validation au démarrage. La portée exacte de chacune de ces options est décrite dans [Sécurité](/docs/deep/security/).

## Diagnostic et contournements

Ces variables existent pour diagnostiquer ou contourner des radios qui se comportent mal. Aucune n’est nécessaire en fonctionnement normal.

| Variable | Défaut | Description |
|----------|--------|-------------|
| `MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK` | `false` | Fait passer l’audit radio d’un contrôle horaire à un sondage `get_msg()` toutes les 10 secondes |
| `MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE` | `false` | Désactive la réutilisation des slots de salon et force `set_channel(...)` avant chaque envoi |
| `MESHCORE_LOAD_WITH_AUTOEVICT` | `false` | Charge les contacts en mode autoevict, la radio évinçant elle-même les plus anciens |
| `MESHCORE_SKIP_POST_CONNECT_SYNC` | `false` | Saute la synchronisation contacts/salons, la publicité de démarrage et les boucles périodiques |
| `__CLOWNTOWN_DO_CLOCK_WRAPAROUND` | `false` | Très expérimental : tente un débordement d’horloge sur 32 bits quand la RTC est bloquée dans le futur |

Trois précisions.

`MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK` ne crée pas une tâche : elle change la fréquence d’une tâche qui tourne toujours. Par défaut, cet audit passe une fois par heure et vérifie deux choses — si des messages sont restés sur la radio sans remonter par abonnement d’événement, et si les attentes de l’application sur les slots de salon correspondent encore à la réalité de la radio. En cas d’écart, une erreur apparaît dans l’interface et dans les logs.

`MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE` retarde chaque envoi de salon d’environ 500 ms. À réserver aux cas où un autre client modifie les slots sous les pieds de Meshloom.

`MESHCORE_SKIP_POST_CONNECT_SYNC` est une trappe de sortie de diagnostic. L’enregistrement des gestionnaires, l’export de clé, la synchronisation d’horloge et la récupération automatique des messages continuent ; c’est la prise en main des contacts et des salons qui est mise de côté. Utile quand l’état de la radio doit rester intact. Pas pour un usage normal.

`__CLOWNTOWN_DO_CLOCK_WRAPAROUND` est un dernier recours pour un nœud dont l’horloge est coincée dans le futur, sans mode rescue ni temps GPS disponibles. Elle repose sur un comportement dépendant de la carte et peut ne pas être sûre ni efficace sur toutes les cibles MeshCore.

## Réglages d’exécution en base

Ce qui suit vit dans `app_settings` et se pilote depuis l’interface ou `PATCH /api/settings`. Ce n’est pas configurable par l’environnement.

- `radio_transport`, `radio_serial_port`, `radio_serial_baudrate`, `radio_tcp_host`, `radio_tcp_port`, `radio_ble_address`, `radio_ble_pin`
- `max_radio_contacts` — capacité de contacts visée sur la radio, base de calcul des remplissages et déchargements
- `auto_decrypt_dm_on_advert` — déchiffrement historique des messages directs quand une clé devient connue
- `advert_interval`, `last_advert_time` — publicité périodique, `0` désactive
- `flood_scope`, `known_regions` — portée de flood régionale et liste de régions candidates au décodage
- `blocked_keys`, `blocked_names`, `discovery_blocked_types` — listes de blocage
- `tracked_telemetry_repeaters`, `tracked_telemetry_contacts`, `telemetry_interval_hours` — collecte de télémétrie
- `auto_resend_channel` — renvoi automatique de salon
- `last_message_times` — horodatages de tri côté serveur
- `push_defaults`, `push_conversation_overrides`, `vapid_subject` — règles et sujet VAPID du push web (l’ancien `push_conversations` n’est plus la surface de préférence)
- `vapid_private_key`, `vapid_public_key` — paire VAPID générée au premier démarrage

Les intégrations MQTT, bots, webhooks, Apprise et SQS ne sont pas dans `app_settings` : elles vivent dans la table `fanout_configs` et se gèrent via `/api/fanout`. L’état Community est stocké sur `app_settings` mais n’est pas dans `GET` / `PATCH /api/settings` : utiliser `/api/community`. Voir [Fanout](/docs/deep/fanout/) et [Meshloom Community](/docs/deep/community/).

Les conséquences radio de `max_radio_contacts`, `flood_scope` et `path_hash_mode` sont détaillées dans [Radio, contacts et salons](/docs/deep/radio/).
