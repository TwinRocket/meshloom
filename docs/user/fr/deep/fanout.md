---
title: Fanout
description: MQTT, webhooks, Apprise, SQS — ce que Meshloom entend peut repartir ailleurs.
level: deep
order: 16
---

Le fanout envoie ce que Meshloom entend vers d’autres systèmes (pour être prévenu vous-même d’événements, sur votre téléphone ou dans une discussion, utilisez plutôt les [Notifications](/docs/deep/notifications/)) : votre propre broker MQTT, Home Assistant, des webhooks, des services de messagerie et d’e-mail via Apprise, Amazon SQS, des collecteurs communautaires de paquets, et la carte publique MeshCore. Dans l’interface, il s’appelle **MQTT et automatisation**.

Chaque destination est une **intégration**. Pour en ajouter une :

1. Ouvrez **Réglages → MQTT et automatisation**.
2. Choisissez **Ajouter une intégration** et sélectionnez un type.
3. Remplissez le formulaire, puis choisissez **Enregistrer et activer** (ou **Enregistrer et désactiver** pour la garder de côté).

La page affiche un avertissement : les intégrations sont une fonctionnalité expérimentale en bêta ouverte. Chaque intégration montre son état, par exemple **Connecté**, **Déconnecté** ou **Erreur**. En cas d’erreur, **Voir la dernière erreur** affiche le dernier message.

[Meshloom Community](/docs/deep/community/) n’est pas une intégration. C’est une adhésion à part, avec sa propre page.

## Les types d’intégration

| Type | Ce qu’il fait |
|------|---------------|
| MQTT privé | Publie les messages et les paquets bruts vers votre propre broker MQTT |
| MQTT Discovery Home Assistant | Fait apparaître votre radio, vos répéteurs et vos contacts comme des appareils dans Home Assistant. Voir [Home Assistant](/docs/deep/home-assistant/) |
| MQTT communautaire / meshcoretomqtt | Envoie les paquets bruts à un collecteur communautaire. Des préréglages existent pour **MeshRank** et **LetsMesh** (US et EU) |
| Webhook | Envoie chaque message en JSON par HTTP, avec une signature facultative. Pas le webhook de notification, voir [Webhooks](#webhooks) |
| Apprise | Transmet chaque message vers Discord, Telegram, Slack, e-mail, SMS et une centaine d’autres services. Les notifications ont déménagé vers les [canaux](/docs/deep/notifications/), voir [Apprise](#apprise) |
| Amazon SQS | Dépose chaque événement dans une file Amazon SQS |
| Envoi vers la carte | Envoie les répéteurs et serveurs de salon entendus vers map.meshcore.io |

### MQTT privé

Vous indiquez l’adresse et le port de votre broker (port 1883 par défaut), des identifiants facultatifs, TLS, et un préfixe de sujet (`meshcore` par défaut). Meshloom publie vers :

- `<préfixe>/dm:<clé du contact>` pour les messages directs ;
- `<préfixe>/gm:<clé du salon>` pour les messages de salon ;
- `<préfixe>/raw/dm:<clé>`, `<préfixe>/raw/gm:<clé>` ou `<préfixe>/raw/unrouted` pour les paquets bruts.

Les messages partent **déchiffrés**, en clair, y compris ceux que vous envoyez. Ne le dirigez que vers un broker de confiance.

### MQTT communautaire

Il envoie les paquets bruts à un collecteur tenu par une communauté, comme LetsMesh ou MeshRank, pour que votre radio serve aussi d’observateur. Seuls les paquets bruts partent, jamais les messages déchiffrés. Le serveur par défaut est celui de LetsMesh US, en WebSockets. Un code de région (IATA) est obligatoire ; une adresse e-mail est facultative (elle permet au collecteur de relier le nœud à vous). Avec LetsMesh, votre radio signe le laissez-passer de connexion avec sa clé ; MeshRank n’en demande pas. En plus des paquets, le collecteur reçoit toutes les cinq minutes un message d’état : nom de la radio, modèle, version de firmware et paramètres radio.

C’est autre chose que le [Meshloom Community](/docs/deep/community/) officiel. Les deux peuvent fonctionner en même temps.

### Envoi vers la carte

Il envoie vers map.meshcore.io les annonces des répéteurs et des serveurs de salon qui diffusent leur position. Il a besoin de la clé privée de la radio pour les signer : le firmware de la radio doit donc autoriser l’export de la clé. Un même nœud est envoyé au plus une fois par heure.

**Il démarre en mode simulation (dry-run).** En mode simulation, Meshloom écrit seulement dans son journal ce qu’il enverrait. Rien n’arrive sur la carte tant que vous n’avez pas décoché **Mode simulation (journal uniquement, pas d’envoi)** dans le formulaire. Un périmètre géographique facultatif limite les envois aux nœuds situés dans un rayon autour de votre radio.

## Choisir ce qui part : la portée

Chaque intégration a une **portée** : quels messages elle reçoit, et si elle reçoit aussi les paquets bruts.

- **Messages** : tous, aucun, seulement les salons et contacts que vous listez, ou tous sauf ceux que vous listez. Avec « seulement », les salons et contacts ajoutés plus tard ne sont pas inclus automatiquement.
- **Transférer les paquets bruts** : oui ou non. Seuls MQTT privé et Amazon SQS vous laissent le choix.

Certains types ont une portée imposée :

| Type | Portée |
|------|--------|
| MQTT communautaire, Envoi vers la carte | Paquets bruts uniquement, jamais les messages |
| Webhook, Apprise, Home Assistant | Messages selon votre choix, jamais de paquets bruts |

La portée ne filtre que les messages et les paquets bruts. Les contacts, la télémétrie des répéteurs et les instantanés de santé de la radio (toutes les 60 secondes) vont à toutes les intégrations, et chacune garde ce dont elle a besoin.

Déchiffrer d’anciens paquets plus tard, après l’ajout d’une clé, ne déclenche jamais les intégrations. Ajouter une clé ne rejoue pas une semaine de notifications.

Ce que contient un message : son type (direct ou salon), la clé de la conversation, le texte, l’expéditeur, l’état d’accusé de réception, les chemins empruntés et les heures. Un paquet brut a deux identifiants. `id` identifie le paquet stocké, et un paquet réentendu par une autre route le partage. `observation_id` est unique à chaque arrivée par les ondes : c’est lui qu’il faut utiliser pour compter. Sa numérotation repart du début quand Meshloom redémarre.

## Bots

Les bots Python n’existent plus en 5.0.0 : ce sont des **extensions**. Ajoutez, écrivez et gérez-les dans **Réglages → Extensions** (l’extension fournie **Echo**, qui répond `Plong!` à `!pling`, est l’exemple pour commencer). Les bots que vous aviez ont été convertis en extensions locales qui attendent votre relecture avant de démarrer. Voir [Extensions](/docs/deep/plugins/) pour les permissions et les limites, et [Les bots de la 4.18](/docs/deep/plugins/#les-bots-de-la-418-et-avant).

## Webhooks

Il y a deux webhooks dans Meshloom, et ils ne sont pas les mêmes. Celui-ci, le **webhook du fanout**, est une intégration pour les machines : il envoie **chaque message** du périmètre choisi, en JSON, à un script, Node-RED ou n8n. Le **webhook de notification** (un canal *Webhook* des [Notifications](/docs/deep/notifications/#destinations-e-mail-et-webhook)) envoie les **événements** que vous cochez dans le tableau des règles (un message direct, radio hors ligne, une alerte de télémétrie…), avec sa propre signature et ses propres en-têtes (`X-Webhook-Id`, `X-Webhook-Timestamp`). Utilisez le webhook du fanout pour alimenter un système avec le trafic, celui des notifications pour être prévenu d’événements.

Meshloom envoie chaque message en JSON, avec la méthode que vous choisissez (`POST`, `PUT` ou `PATCH`) et les en-têtes supplémentaires que vous définissez. Il attend une réponse jusqu’à 10 secondes. Chaque requête porte un en-tête `X-Webhook-Event`.

Si vous définissez un **secret HMAC**, Meshloom signe le corps avec HMAC-SHA256 et envoie le résultat sous la forme `sha256=<hex>` dans un en-tête. L’en-tête est `X-Webhook-Signature`, sauf si vous en nommez un autre.

## Secrets

Les secrets ne se relisent pas : les adresses Apprise, l’URL, les valeurs d’en-tête et le secret HMAC du webhook, les mots de passe MQTT, l’identifiant de clé d’accès, la clé secrète et le jeton de session Amazon SQS ne sont plus jamais affichés une fois enregistrés. Leur champ reste vide avec **Inchangé** : laissez-le vide pour conserver le secret enregistré, saisissez-en un nouveau pour le remplacer, ou appuyez sur **Effacer** pour le supprimer à l’enregistrement (**Conserver** annule). La zone Apprise montre le service et l’hôte des adresses enregistrées ; y saisir du texte les remplace toutes. Le champ URL du webhook montre le schéma et l’hôte de l’URL enregistrée, tout comme la liste des intégrations. Les en-têtes du webhook gardent leur nom, chaque valeur enregistrée affichée `********` : laissez-la pour la conserver, saisissez une nouvelle valeur pour la remplacer, ou supprimez sa ligne. Si vous changez la destination d’un secret (l’URL du webhook, le broker MQTT, son port ou son TLS, le point de terminaison SQS), le champ du secret devient obligatoire : saisissez de nouveau le secret, ou effacez-le. Pour un webhook, cela vaut aussi pour les valeurs de ses en-têtes. Une restauration qui envoie une intégration ailleurs supprime son secret et le signale. Une intégration restaurée qui avait le droit de joindre un réseau privé revient désactivée, avec un avis : vérifiez son adresse avant de l’activer.

## Destinations sur votre réseau

Webhook, Apprise, Amazon SQS (point d’accès personnalisé) et Envoi vers la carte (adresse d’API personnalisée) refusent une destination sur votre réseau local, sur cette machine ou sur une adresse de métadonnées cloud, sauf si vous cochez **Autoriser une destination sur un réseau privé (réseau local, localhost)** dans l’intégration. Ainsi, un réglage modifié ne peut pas faire du serveur une sonde de votre réseau. Cochez-la pour Home Assistant, Gotify, ntfy, Node-RED, LocalStack ou un script sur votre réseau ; les adresses de métadonnées cloud restent refusées même dans ce cas.

L’adresse est vérifiée quand vous enregistrez une intégration activée, puis avant chaque envoi (pour SQS, au démarrage de l’intégration). Une adresse refusée affiche une erreur comme « cette adresse est sur un réseau privé » et rien n’est envoyé. Le webhook et l’envoi vers la carte se connectent uniquement à l’adresse vérifiée et ne suivent jamais de redirection.

Les intégrations configurées avant cette vérification continuent de fonctionner : la mise à jour coche la case pour elles, et un avis unique en haut de la page les liste. Décochez-la dans une intégration qui pointe vers un service public. La case cochée par la mise à jour ne vaut que pour l’adresse pour laquelle elle a été cochée : quand vous changez l’adresse, elle se décoche, et vous la cochez à nouveau si la nouvelle adresse est sur votre réseau.

## Apprise

**Les notifications ont déménagé.** Pour être prévenu d’événements (messages directs, mentions, radio hors ligne, nouveaux contacts, alertes) sur Telegram, Discord, ntfy, Home Assistant, Gotify ou tout service Apprise, ajoutez un **canal** dans les [Notifications](/docs/deep/notifications/) : il a ses propres règles, une pause, un bouton de test, des nouvelles tentatives, un journal des envois et une politique de texte qui garde les messages privés hors des services tiers. La sortie Apprise décrite ici reste, et continue de faire ce qu’elle faisait : transmettre chaque message des salons et contacts que vous choisissez, mis en forme avec vos propres modèles. Elle n’a ni règles, ni pause, ni choix par événement. Une conversion en un clic d’une sortie Apprise en canal de notification est prévue dans une version ultérieure ; d’ici là, si la même adresse figure aux deux endroits, vous serez prévenu deux fois.

Indiquez une adresse par ligne. Chaque message est envoyé à toutes. Vous pouvez écrire vos propres modèles de texte pour les messages directs et les messages de salon, avec des variables comme l’expéditeur, le texte, le salon, le nombre de sauts et la force du signal. Les messages que vous avez envoyés vous-même ne sont pas transmis, sauf si vous cochez l’option. Pour Discord, vous pouvez conserver le nom et l’image propres au webhook.

Apprise ignore les messages d’un contact ou d’un nom bloqué et ceux d’un salon en sourdine (jusqu’à la fin de la sourdine), comme la liste des conversations. Les sorties machine (MQTT privé, Webhook, Amazon SQS) les reçoivent toujours : un script ou un broker décide lui-même. Pour être averti des événements système (radio hors ligne, nouveaux contacts, alertes) par Apprise, utilisez plutôt un canal Apprise des [Notifications](/docs/deep/notifications/#destination-apprise).

Meshloom accepte ces services Apprise :

- **Sur un serveur que vous choisissez** (l’adresse passe par la vérification de réseau privé ci-dessus) : webhooks JSON, XML et formulaire (`json://`, `xml://`, `form://`), ntfy (`ntfy://` ; un sujet seul part vers ntfy.sh), Gotify, Home Assistant (`hassio://`), Matrix, e-mail (`mailto://` : c’est le serveur SMTP qui est vérifié), MQTT, Signal (API REST), Rocket.Chat, Mattermost, Nextcloud et Nextcloud Talk, Emby, et un serveur Apprise API (`apprise://`). Chacun existe aussi en forme `s` avec TLS (`jsons://`, `ntfys://`…).
- **Un service public à adresse fixe** : Telegram (`tgram://`), Discord, Pushover (`pover://`), Slack, Pushbullet (`pbul://`), Free Mobile, SFR, Google Chat (`gchat://`), Webex, Pushsafer, Prowl, IFTTT, Join et Opsgenie. Leurs adresses web habituelles, comme un lien de webhook Discord, fonctionnent aussi.

Tous les autres services Apprise sont refusés, réseau privé autorisé ou non : ceux du bureau et de la machine locale (`dbus://`, `windows://`, `syslog://`…), ceux qui utilisent des identifiants cloud (`sns://`, `ses://`) et ceux que personne n’a encore examinés. Une adresse Telegram doit indiquer un identifiant de discussion (`tgram://bot_token/chat_id`) : sans lui, Apprise écrirait à la dernière personne qui a parlé au bot. Meshloom ne suit jamais de redirection pour Apprise et n’envoie qu’à l’hôte que vous avez saisi : la découverte de serveur Matrix (`.well-known`) et la recherche de clé PGP des e-mails (`wkd=yes`) sont désactivées. Une adresse déjà enregistrée et désormais refusée reste dans la liste ; son intégration affiche l’erreur jusqu’à ce que vous la corrigiez. La restauration d’une ancienne sauvegarde qui contient une telle adresse ramène son intégration désactivée, avec un avis : corrigez l’adresse avant de l’activer.

## Amazon SQS

Vous indiquez l’URL de la file. La région et le point d’accès (utile avec LocalStack) sont facultatifs. Sans clés, Meshloom utilise les identifiants AWS habituels du serveur. Chaque message est un objet JSON avec `event_type` (`message` ou `raw_packet`) et `data`. Les messages sont envoyés déchiffrés.

## Par l’API

L’interface utilise ces points d’accès. Une intégration enregistrée est vérifiée à chaque fois, qu’elle soit activée ou non.

| Méthode | Point d’accès | Effet |
|---------|---------------|-------|
| GET | `/api/fanout` | Liste les intégrations |
| POST | `/api/fanout` | En crée une |
| PATCH | `/api/fanout/{id}` | La met à jour et la redémarre |
| DELETE | `/api/fanout/{id}` | L’arrête et la supprime |

`GET /api/health` indique l’état de chaque intégration activée dans `fanout_statuses`. Une intégration désactivée n’est simplement pas démarrée.
