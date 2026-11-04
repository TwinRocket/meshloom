---
title: Fanout
description: MQTT, bots, webhooks, Apprise, SQS — ce que Meshloom entend peut repartir ailleurs.
level: deep
order: 16
---

Le fanout est le bus qui redistribue les événements du mesh vers l’extérieur. Toutes les intégrations passent par lui : MQTT privé, MQTT communautaire, bots, webhooks, Apprise, SQS, et l’envoi de publicités vers la carte publique. [Meshloom Community](/docs/deep/community/) est un état d’adhésion séparé, pas une ligne Fanout.

Chaque intégration est une configuration stockée en base, dans la table `fanout_configs`. Elles se gèrent depuis Réglages > Intégrations, ou via l’API :

| Méthode | Endpoint | Effet |
|---------|----------|-------|
| GET | `/api/fanout` | Liste les configurations |
| POST | `/api/fanout` | Crée une configuration |
| PATCH | `/api/fanout/{id}` | Met à jour, ce qui recharge le module |
| DELETE | `/api/fanout/{id}` | Supprime, ce qui arrête le module |

Une configuration désactivée est traitée comme un brouillon : sa validation n’est pas exécutée. L’état de chaque module est visible dans `GET /api/health`, dans `fanout_statuses`, avec trois valeurs possibles : `connected`, `disconnected`, `error`.

## Les types de modules

| Type | Ce qu’il fait |
|------|---------------|
| `mqtt_private` | Publie vers votre propre broker MQTT. Hôte, port, identifiants, TLS, préfixe de sujet. |
| `mqtt_community` | Publie vers un broker communautaire. Paquets bruts uniquement. |
| `bot` | Exécute du code Python en réponse aux messages. |
| `webhook` | POST / PUT / PATCH HTTP, signature HMAC-SHA256 optionnelle, en-têtes libres. |
| `apprise` | Notifications via Apprise, une URL de service par ligne. |
| `sqs` | Dépose une enveloppe JSON dans une file Amazon SQS. |
| `map_upload` | Envoie les publicités de répéteurs et de salons entendues vers map.meshcore.io. |

L’intégration Home Assistant est un cas particulier de MQTT Discovery et a sa propre page : [Home Assistant](/docs/deep/home-assistant/).

## La portée (scope)

Chaque configuration porte un blob `scope` qui décide quels événements l’atteignent :

```json
{"messages": "all", "raw_packets": "all"}
{"messages": "none", "raw_packets": "all"}
{"messages": {"channels": ["key1"], "contacts": "all"}, "raw_packets": "none"}
```

La portée ne filtre que deux flux : les messages décodés et les paquets RF bruts. Trois autres flux — mises à jour de contacts, snapshots de télémétrie de répéteurs, snapshots de santé radio — sont distribués à tous les modules sans condition. Chaque module filtre lui-même selon sa configuration.

Deux types ont une portée imposée :

- MQTT communautaire est verrouillé sur `{"messages": "none", "raw_packets": "all"}`. Il ne transporte jamais le contenu de vos messages.
- `map_upload` est verrouillé sur la même valeur : paquets bruts uniquement.

Pour les webhooks et Apprise, l’interface n’offre pas l’option « aucun message », qui rendrait l’intégration inerte.

Le déchiffrement historique ne déclenche aucun fanout. Ce chemin est marqué non temps réel, et la distribution est court-circuitée. Autrement dit, ajouter une clé de salon et rattraper le trafic de la semaine passée ne va pas rejouer une semaine de notifications.

## Ce que reçoit un module

Cinq points d’entrée, tous facultatifs.

- **Messages** — le modèle de message complet : `type` (`PRIV` ou `CHAN`), `conversation_key`, `text`, `sender_name`, `sender_key`, `outgoing`, `acked`, `paths`, `sender_timestamp`, `received_at`.
- **Paquets bruts** — `id` (identité de stockage), `observation_id` (identité par arrivée RF), `raw` en hexadécimal, `timestamp`, et un `decrypted_info` optionnel.
- **Contacts** — le modèle contact : `public_key`, `name`, `type`, `lat`, `lon`, `last_seen`, `first_seen`, `on_radio`.
- **Télémétrie** — snapshot d’un répéteur après enregistrement : tension batterie, plancher de bruit, RSSI et SNR, compteurs de paquets, temps d’antenne, uptime.
- **Santé** — snapshot radio toutes les 60 secondes : état de connexion, identité du nœud, plancher de bruit, batterie, uptime, compteurs.

Deux identités cohabitent pour les paquets bruts, et la distinction compte si vous consommez ce flux : `id` est une identité de **stockage**, déduplique sur le hachage de payload en excluant les octets de chemin, donc un même payload réentendu par un autre chemin partage une ligne. `observation_id` est unique par arrivée RF. Pour compter ou dédupliquer des observations, c’est `observation_id`.

## Bots

Un bot est du code Python que vous écrivez dans l’interface et que le serveur exécute. C’est une fonctionnalité de puissance, pas un bac à sable : le code tourne avec `exec()` et l’ensemble des `__builtins__`. Toute personne ayant accès à l’interface peut donc exécuter du code arbitraire sur la machine. Voir [Sécurité](/docs/deep/security/) et [Un réseau de confiance](/docs/trust/).

La fonction reçoit, dans cet ordre : `sender_name`, `sender_key`, `message_text`, `is_dm`, `channel_key`, `channel_name`, `sender_timestamp`, `path`, puis éventuellement `is_outgoing`, `path_bytes_per_hop`, `packet_hash`.

```python
def bot(sender_name, sender_key, message_text, is_dm,
        channel_key, channel_name, sender_timestamp, path):
    if "!echo" in message_text.lower():
        return f"[ECHO] {message_text}"
    return None
```

Deux arguments supplémentaires, `region` et `scoped`, ne sont livrés qu’aux bots qui utilisent `**kwargs` ou nomment explicitement le paramètre. C’est délibéré : les signatures existantes continuent de fonctionner sans modification. `scoped` lève l’ambiguïté d’un `region` à `None` — non scopé, ou scopé vers une région inconnue de la liste.

Le retour peut être `None` (pas de réponse), une chaîne, une liste de chaînes envoyées dans l’ordre, ou un dictionnaire `{"region": ..., "message": ...}` qui scope la réponse à une région pour cet envoi seulement. Le scope de région ne s’applique qu’aux réponses de salon ; il est ignoré pour les DM.

Pour les messages de salon, le texte passé au bot est normalisé : le préfixe `"{sender_name}: "` est retiré quand il correspond à l’expéditeur du payload.

L’exécution est bornée : pool de threads, délai maximal, limite de concurrence, et limitation de débit sur les messages sortants pour rester compatible avec les répéteurs.

Deux interrupteurs existent. `MESHCORE_DISABLE_BOTS=true` désactive le système au démarrage : plus d’exécution, `403` sur les modifications de configuration de bots, et un message d’indisponibilité dans l’interface. `POST /api/fanout/bots/disable-until-restart` arrête les modules bots et les maintient désactivés jusqu’au redémarrage du processus, sans toucher à l’environnement.

On ne crée ou ne modifie un bot que depuis une adresse locale ou l’ingress Home Assistant, pas via un nom de domaine. Voir [Sécurité](/docs/deep/security/).

Une limite de principe du projet, valable aussi pour les bots : pas de trafic radio réellement automatisé, et pas d’injection de contenu venu d’Internet sur le mesh. Les réponses de bots sont la limite de ce que le projet veut automatiser.

## MQTT communautaire

Ce module ne publie que des paquets bruts. Le champ `raw` est toujours l’hexadécimal du paquet d’origine.

Un détail de lecture : quand un paquet direct porte un champ `path`, il est émis comme une liste d’identifiants de saut séparés par des virgules, exactement comme le paquet les rapporte. La largeur d’un identifiant suit le `path_hash_mode` du paquet — 1, 2 ou 3 octets. Ce n’est volontairement pas un rendu octet par octet.

## Webhooks

`hmac_secret`, quand il est défini, ajoute une signature HMAC-SHA256 du corps JSON. Le nom d’en-tête est configurable via `hmac_header` et vaut `X-Webhook-Signature` par défaut. La valeur est au format `sha256=<hex>`.
