---
title: Fanout
description: MQTT, bots, webhooks, Apprise, SQS — ce que Meshloom entend peut repartir ailleurs.
level: deep
order: 16
---

Le fanout envoie ce que Meshloom entend vers d’autres systèmes : votre propre broker MQTT, Home Assistant, des bots Python, des webhooks, des services de messagerie et d’e-mail via Apprise, Amazon SQS, des collecteurs communautaires de paquets, et la carte publique MeshCore. Dans l’interface, il s’appelle **MQTT et automatisation**.

Chaque destination est une **intégration**. Pour en ajouter une :

1. Ouvrez **Réglages → MQTT et automatisation**.
2. Choisissez **Ajouter une intégration** et sélectionnez un type.
3. Remplissez le formulaire, puis choisissez **Enregistrer et activer** (ou **Enregistrer et désactiver** pour la garder de côté).

La page affiche un avertissement : les intégrations sont une fonctionnalité expérimentale en bêta ouverte. Chaque intégration montre son état : connectée, déconnectée ou en erreur. En cas d’erreur, **Voir la dernière erreur** affiche le dernier message.

[Meshloom Community](/docs/deep/community/) n’est pas une intégration. C’est une adhésion à part, avec sa propre page.

## Les types d’intégration

| Type | Ce qu’il fait |
|------|---------------|
| MQTT privé | Publie les messages et les paquets bruts vers votre propre broker MQTT |
| MQTT Discovery Home Assistant | Fait apparaître votre radio, vos répéteurs et vos contacts comme des appareils dans Home Assistant. Voir [Home Assistant](/docs/deep/home-assistant/) |
| MQTT communautaire / meshcoretomqtt | Envoie les paquets bruts à un collecteur communautaire. Des préréglages existent pour **MeshRank** et **LetsMesh** (US et EU) |
| Bot Python | Exécute du code Python que vous écrivez en réponse aux messages |
| Webhook | Envoie chaque message en JSON par HTTP, avec une signature facultative |
| Apprise | Envoie des notifications vers Discord, Telegram, Slack, e-mail, SMS et une centaine d’autres services |
| Amazon SQS | Dépose chaque événement dans une file Amazon SQS |
| Envoi vers la carte | Envoie les répéteurs et serveurs de salon entendus vers map.meshcore.io |

### MQTT privé

Vous indiquez l’adresse et le port de votre broker (port 1883 par défaut), des identifiants facultatifs, TLS, et un préfixe de sujet (`meshcore` par défaut). Meshloom publie vers :

- `<préfixe>/dm:<clé du contact>` pour les messages directs ;
- `<préfixe>/gm:<clé du salon>` pour les messages de salon ;
- `<préfixe>/raw/dm:<clé>`, `<préfixe>/raw/gm:<clé>` ou `<préfixe>/raw/unrouted` pour les paquets bruts.

Les messages partent **déchiffrés**, en clair, y compris ceux que vous envoyez. Ne le dirigez que vers un broker de confiance.

### MQTT communautaire

Il envoie les paquets bruts à un collecteur tenu par une communauté, comme LetsMesh ou MeshRank, pour que votre radio serve aussi d’observateur. Seuls les paquets bruts partent, jamais les messages déchiffrés. Le serveur par défaut est celui de LetsMesh US, en WebSockets. Un code de région (IATA) est obligatoire ; une adresse e-mail est facultative (elle permet au collecteur de relier le nœud à vous). Votre radio signe le laissez-passer de connexion avec sa clé.

C’est autre chose que le [Meshloom Community](/docs/deep/community/) officiel. Les deux peuvent fonctionner en même temps.

### Envoi vers la carte

Il envoie les annonces des répéteurs et des serveurs de salon vers map.meshcore.io. Il a besoin de la clé privée de la radio pour les signer : le firmware de la radio doit donc autoriser l’export de la clé. Un même nœud est envoyé au plus une fois par heure.

**Il démarre en mode simulation (dry-run).** En mode simulation, Meshloom écrit seulement dans son journal ce qu’il enverrait. Rien n’arrive sur la carte tant que vous n’avez pas décoché **Mode simulation** dans le formulaire. Un périmètre géographique facultatif limite les envois aux nœuds situés dans un rayon autour de votre radio.

## Choisir ce qui part : la portée

Chaque intégration a une **portée** : quels messages elle reçoit, et si elle reçoit aussi les paquets bruts.

- **Messages** : tous, aucun, seulement les salons et contacts que vous listez, ou tous sauf ceux que vous listez. Avec « seulement », les salons et contacts ajoutés plus tard ne sont pas inclus automatiquement.
- **Paquets bruts** : oui ou non. Seuls MQTT privé et Amazon SQS vous laissent le choix.

Certains types ont une portée imposée :

| Type | Portée |
|------|--------|
| MQTT communautaire, Envoi vers la carte | Paquets bruts uniquement, jamais les messages |
| Bot Python | Tous les messages, pas de paquets bruts |
| Webhook, Apprise, Home Assistant | Messages selon votre choix, jamais de paquets bruts |

La portée ne filtre que les messages et les paquets bruts. Les contacts, la télémétrie des répéteurs et les instantanés de santé de la radio (toutes les 60 secondes) vont à toutes les intégrations, et chacune garde ce dont elle a besoin.

Déchiffrer d’anciens paquets plus tard, après l’ajout d’une clé, ne déclenche jamais les intégrations. Ajouter une clé ne rejoue pas une semaine de notifications.

Ce que contient un message : son type (direct ou salon), la clé de la conversation, le texte, l’expéditeur, l’état d’accusé de réception, les chemins empruntés et les heures. Un paquet brut a deux identifiants. `id` identifie le paquet stocké, et un paquet réentendu par une autre route le partage. `observation_id` est unique à chaque arrivée par les ondes : c’est lui qu’il faut utiliser pour compter.

## Bots

Un bot est du code Python que vous écrivez dans l’interface. Le serveur l’exécute à chaque message reçu.

**C’est de l’exécution de code arbitraire, par conception.** Le code tourne sur le serveur avec un accès complet à la machine. Toute personne qui peut ouvrir la page de Meshloom peut écrire et lancer du code. N’activez les bots que sur un réseau de confiance. Voir [Sécurité](/docs/deep/security/) et [Un réseau de confiance](/docs/trust/). Les bots sont désactivés au départ sur les installations faites avec le paquet Linux.

```python
def bot(sender_name, sender_key, message_text, is_dm,
        channel_key, channel_name, sender_timestamp, path):
    if "!echo" in message_text.lower():
        return f"[ECHO] {message_text}"
    return None
```

La fonction peut aussi accepter `is_outgoing`, `path_bytes_per_hop`, `packet_hash` et, si vous les nommez ou utilisez `**kwargs`, `region` et `scoped`. Les bots voient tous les messages, y compris ceux que vous envoyez : évitez donc les réponses qui relancent le bot. Pour les messages de salon, `sender_key` vaut `None` et le préfixe « nom de l’expéditeur : » est retiré du texte.

Un bot renvoie `None` (pas de réponse), un texte, une liste de textes envoyés dans l’ordre, ou `{"region": ..., "message": ...}` pour envoyer une réponse de salon dans une région donnée. Les régions ne s’appliquent qu’aux réponses de salon.

Limites : un bot attend deux secondes avant de s’exécuter (pour reconnaître les échos), chaque exécution est interrompue au bout de 10 secondes, 100 au plus tournent en même temps, et les réponses d’un bot sont espacées d’au moins deux secondes pour que les répéteurs n’entrent pas en collision.

`MESHCORE_DISABLE_BOTS=true` désactive le système au démarrage : aucun bot ne tourne, en créer ou en modifier un est refusé, et la page le dit. `POST /api/fanout/bots/disable-until-restart` arrête tous les bots jusqu’au prochain redémarrage, sans toucher à l’environnement.

## Webhooks

Meshloom envoie chaque message en JSON, avec la méthode que vous choisissez (`POST`, `PUT` ou `PATCH`) et les en-têtes supplémentaires que vous définissez. Il attend une réponse jusqu’à 10 secondes. Chaque requête porte un en-tête `X-Webhook-Event`.

Si vous définissez un **secret HMAC**, Meshloom signe le corps avec HMAC-SHA256 et envoie le résultat sous la forme `sha256=<hex>` dans un en-tête. L’en-tête est `X-Webhook-Signature`, sauf si vous en nommez un autre.

## Apprise

Indiquez une adresse par ligne. Chaque message est envoyé à toutes. Vous pouvez écrire vos propres modèles de texte pour les messages directs et les messages de salon, avec des variables comme l’expéditeur, le texte, le salon, le nombre de sauts et la force du signal. Les messages que vous avez envoyés vous-même ne sont pas transmis, sauf si vous cochez l’option. Pour Discord, vous pouvez conserver le nom et l’image propres au webhook.

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
