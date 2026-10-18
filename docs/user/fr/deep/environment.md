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
| Add-on Home Assistant | Aucune variable à modifier : les options de l’add-on (niveau de journal, mot de passe, bots) deviennent des variables, et l’add-on définit lui-même les autres. Voir [Sur Home Assistant](/docs/install/#sur-home-assistant) | Redémarrer l’add-on |

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
| `radio_ble_pin` | vide | Code PIN Bluetooth, obligatoire en Bluetooth. Sous Linux, Meshloom s’appaire avec (1 à 6 chiffres) |

Tant qu’aucune connexion n’est choisie, la radio reste en pause. Ne définissez pas `MESHCORE_SERIAL_PORT`, `MESHCORE_TCP_HOST` ni `MESHCORE_BLE_ADDRESS` (ni leurs compléments `MESHCORE_SERIAL_BAUDRATE`, `MESHCORE_TCP_PORT` et `MESHCORE_BLE_PIN`). Une base neuve les ignore. Une base existante sans connexion enregistrée les recopie une seule fois, pour reprendre une ancienne configuration, puis ne les lit plus jamais. Si plusieurs des trois sont définies, aucune n’est recopiée.

## Serveur et données

| Variable | Défaut | Description |
|----------|--------|-------------|
| `MESHCORE_DATABASE_PATH` | `data/meshcore.db` | Emplacement de la base SQLite. Le paquet Linux la place dans `/var/lib/meshloom/meshcore.db`, l’add-on Home Assistant dans `/config/meshcore.db` |
| `MESHCORE_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` ou `ERROR`. La clé privée et les mots de passe Wi-Fi sont masqués même en `DEBUG`, et la sortie de débogage de la bibliothèque Bluetooth reste coupée |
| `MESHCORE_MANAGED_PORTS` | `false` | Posée par un hôte qui décide lui-même des ports d’écoute, comme l’add-on Home Assistant. Les Réglages affichent alors le port du proxy en lecture seule |
| `MESHCORE_RADIO_PROXY_PORT` | *(non défini)* | Port du proxy radio choisi par l’hôte. Utilisée seulement avec `MESHCORE_MANAGED_PORTS=true`, où elle remplace le port enregistré à chaque démarrage. L’add-on Home Assistant la fixe à `5051` |
| `MESHCORE_EMBEDDABLE_SAME_ORIGIN` | `false` | Autorise une page de la même origine à afficher Meshloom dans un cadre. La barre latérale de Home Assistant fait exactement cela. Par défaut, Meshloom refuse d’être mis dans un cadre, ce qui donne un panneau blanc alors que le journal montre un `200` parfaitement sain |

L’adresse de contact des notifications push (sujet VAPID) n’est pas une variable : elle se règle dans **Réglages → Notifications**. Apple exige une adresse `mailto:` ou `https:` réelle et rejette le `.local` par défaut avec `403 BadJwtToken`. Voir [Notifications push](/docs/deep/push/).

## Meshloom Community

Aucune variable ne règle Community : vous le rejoignez et le quittez dans **Réglages → Meshloom Community**, et une base neuve démarre avec Community désactivé. Ses serveurs sont fixes (`mqtt.meshloom.app`, `api.meshloom.app`).

Les anciennes variables `MESHLOOM_COMMUNITY*` sont listées plus bas, dans **Variables retirées**.

Voir [Meshloom Community](/docs/deep/community/).

## Sécurité

| Variable | Défaut | Description |
|----------|--------|-------------|
| `MESHCORE_DISABLE_BOTS` | `false` | Désactive le système de bots au démarrage. Les bots exécutent du code Python avec un accès complet à la machine. Le paquet Linux la met à `true` dans `meshloom.env` |
| `MESHCORE_DISABLE_PLUGINS` | `false` | Verrou d'hôte du système d'extensions : à `true`, aucune extension ne démarre, quels que soient les réglages. Indépendant de `MESHCORE_DISABLE_BOTS` |

À partir de la 5.0 `MESHCORE_BASIC_AUTH_USERNAME` et `MESHCORE_BASIC_AUTH_PASSWORD` sont retirées (voir aussi **Variables retirées**) : quand les deux sont définies et que Meshloom n’a encore aucun compte, le premier démarrage en crée le compte administrateur, puis elles ne sont plus lues et un avertissement est journalisé à chaque démarrage tant qu’elles sont définies (retirez-les ; les comptes se gèrent dans **Réglages → Utilisateurs et accès**). En définir une seule n’importe rien et n’empêche plus Meshloom de démarrer. `Authorization: Basic` avec le nom et le mot de passe d’un compte reste accepté jusqu’à la 5.2, puis sera retiré. Un nom d’utilisateur de plus de 64 caractères ne peut pas devenir un compte : l’import est ignoré avec une erreur dans le journal, et vous créez l’administrateur avec le code d’installation imprimé dans le journal. Ce que chaque option protège, et ce qu’elle ne protège pas, est expliqué dans [Sécurité](/docs/deep/security/).

## Installation et mises à jour

L’installateur les définit pour vous. On les modifie rarement à la main.

| Variable | Description |
|----------|-------------|
| `MESHLOOM_INSTALL_KIND` | Comment Meshloom a été installé : `package`, `compose`, `addon`, `container` ou `source`. Cela décide si **Réglages → Mises à jour** peut installer une mise à jour |
| `MESHLOOM_UPDATE_HELPER` | `compose` pour une pile Docker gérée par l’assistant de mise à jour, `none` pour désactiver l’installation depuis l’interface |
| `MESHLOOM_RUN_AS_USER` | Docker uniquement. Un nombre comme `10001` fait tourner Meshloom sous cet utilisateur plutôt que root. Voir [Autres chemins d’installation](/docs/deep/install-paths/) |
| `MESHLOOM_IMAGE` | Docker uniquement. L’image que Compose démarre, idéalement figée par son empreinte |

## Variables retirées

Ces variables ne changent plus rien. Leurs réglages sont dans l’interface :

| Variable | Désormais dans |
|----------|----------------|
| `MESHCORE_VAPID_SUBJECT` | **Réglages → Notifications**, champ **Sujet VAPID** |
| `MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK` | **Réglages → Radio → Avancé**, « Vérifier les messages toutes les 10 secondes » |
| `MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE` | **Réglages → Radio → Avancé**, « Réécrire le salon avant chaque envoi » |
| `MESHCORE_LOAD_WITH_AUTOEVICT` | **Réglages → Radio → Avancé**, « Laisser la radio supprimer ses plus anciens contacts » |

Si l’une d’elles est encore définie au moment de la mise à jour, Meshloom la recopie une seule fois dans sa base, au premier démarrage : le sujet VAPID seulement si le champ des Réglages est vide, un contournement seulement si la variable vaut `true`. Votre installation continue donc de fonctionner comme avant. Ensuite Meshloom ne les lit plus jamais et écrit un avertissement dans son journal à chaque démarrage tant que l’une d’elles reste définie : retirez-la de votre environnement, et changez désormais le réglage dans l’interface.

Meshloom Community ne lit plus aucune variable non plus :

| Variable retirée | Ce qu’elle devient |
|----------|--------|
| `MESHLOOM_COMMUNITY` | Ignorée. Elle ne concernait qu’une base toute neuve, qui démarre désormais avec Community désactivé |
| `MESHLOOM_COMMUNITY_IATA` | Sur une installation existante, recopiée une seule fois dans les réglages au premier démarrage après la mise à jour, puis ignorée. Changez le code dans les réglages |
| `MESHLOOM_COMMUNITY_BROKER_HOST` | Ignorée. L’état laissé sur un broker personnalisé est effacé une fois après la mise à jour |
| `MESHLOOM_COMMUNITY_API_BASE` | Ignorée |

Meshloom écrit le même avertissement à chaque démarrage tant que l’une d’elles reste définie.

| Variable retirée | Ce qu’elle devient |
|----------|--------|
| `MESHCORE_BASIC_AUTH_USERNAME`, `MESHCORE_BASIC_AUTH_PASSWORD` | Lues uniquement au premier démarrage sans compte, pour créer l’administrateur ; compatibilité jusqu’à la 5.2 : `Authorization: Basic` reste accepté, puis sera retiré. Les comptes se gèrent dans **Réglages → Utilisateurs et accès**. Même avertissement à chaque démarrage |
| `MESHCORE_ENABLE_LOCAL_PRIVATE_KEY_EXPORT` | Ignorée, avec le même avertissement. La clé privée de la radio peut toujours être relue : par `GET /api/radio/private-key` et dans la sauvegarde complète (**Réglages → Base de données**, voir [Sauvegarder la radio](/docs/backup/)). Voir [Sécurité](/docs/deep/security/#la-clé-privée-du-nœud) |

## Réglages stockés dans la base

On les change dans l’interface web. Rien de tout cela ne se définit par l’environnement.

| Où | Ce que cela commande |
|----|----------------------|
| **Radio** | La connexion (voir plus haut). Onglet **Messagerie** : `max_radio_contacts`, `flood_scope`, `known_regions`, `auto_resend_channel`. Onglet **Annonces** : `advert_interval` (saisi en heures dans l’interface, stocké en secondes ; `0` le désactive, toute autre valeur vaut au moins une heure). Onglet **Avancé** : `enable_message_poll_fallback`, `force_channel_slot_reconfigure`, `load_with_autoevict`, `auto_clock_wraparound` |
| **Base de données** | `auto_decrypt_dm_on_advert` |
| **Gestion radio-application** | `blocked_keys`, `blocked_names`, `discovery_blocked_types`, `tracked_telemetry_repeaters`, `tracked_telemetry_contacts` (8 de chaque au maximum), `telemetry_interval_hours`, `telemetry_routed_hourly`, `stale_contact_days` |
| **Alertes** | `telemetry_alert_rules` |
| **Notifications** | `push_defaults`, `push_conversation_overrides`, `vapid_subject`, `notification_destinations` (e-mail et webhook) |
| **Mises à jour** | `auto_update`, `auto_update_window_start`, `auto_update_window_end`, `auto_update_weekdays` |
| **Configuration locale** et **Navigation** | `ui_preferences` : thème, étiquette de l’instance, barre de gauche ; `auth_allowed_origins` : les autres sites autorisés à appeler l’API (**Origines autorisées**) |
| Canaux découverts | `rejected_channels` : les canaux que vous avez refusés |
| Hors interface | `raw_packet_retention_days` : supprime automatiquement les paquets illisibles plus vieux que ce nombre de jours (`0` garde tout). À régler par `PATCH /api/settings` |

La paire de clés VAPID (`vapid_private_key` et `vapid_public_key`) est créée au premier démarrage. L’interface est la façon prévue de modifier les réglages ; certains peuvent aussi être lus et modifiés avec `GET` et `PATCH /api/settings`.

Les intégrations (MQTT, bots, webhooks, Apprise, SQS) sont stockées à part, dans `fanout_configs`. Meshloom Community a son propre point d’accès, `/api/community`. Voir [Fanout](/docs/deep/fanout/), [Meshloom Community](/docs/deep/community/) et [Radio, contacts et salons](/docs/deep/radio/).
