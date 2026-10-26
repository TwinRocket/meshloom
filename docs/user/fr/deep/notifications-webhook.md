---
title: Webhook de notification
description: Le corps v2 du webhook des canaux de notification, champ par champ, avec un exemple par événement et la vérification de sa signature en Python et dans Node-RED.
level: deep
order: 17.5
---

Un canal de notification **Webhook** (voir [Notifications](/docs/deep/notifications/#destinations-e-mail-et-webhook)) envoie chaque notification par un `POST` HTTP au corps JSON vers une adresse que vous contrôlez : Home Assistant, Node-RED, n8n ou votre propre script. Cette page décrit ce corps et les en-têtes, pour qu’un récepteur puisse s’y fier.

## Format du corps : v1 ou v2

Le formulaire du canal a un champ **Format du corps** :

- **v2 (documenté, recommandé)** est le format décrit sur cette page. Un canal webhook créé à partir de la 5.0.0 l’utilise d’emblée.
- **v1 (ancien)** est le corps de la 4.18.x, conservé à l’octet près pour ne pas casser un récepteur existant. Tout canal webhook qui existait avant ce champ garde la v1 jusqu’à ce que vous la changiez dans son formulaire.

Le webhook importé des réglages 4.18.x (`legacy:webhook`) envoie toujours la v1, puisque ses réglages restent dans le formulaire 4.18.x. Pour recevoir la v2, ajoutez un nouveau canal webhook (**Ajouter un canal → Webhook**) et retirez l’ancienne destination quand votre récepteur est prêt.

Le schéma JSON du corps v2 est publié à côté de la page anglaise : [notifications-webhook.schema.json](https://raw.githubusercontent.com/TwinRocket/meshloom/main/docs/user/en/deep/notifications-webhook.schema.json). Il est généré à partir du code, et un test échoue si les deux divergent.

## Le corps v2

```text
{
  "version": 2,              toujours 2
  "event": "...",            l’événement, voir les exemples ci-dessous
  "event_id": "...",         identifiant unique de cet événement
  "occurred_at": 1767225600, moment de l’événement (secondes epoch, UTC)
  "dedup_key": "...",        le même à chaque nouvel essai d’une notification
  "urgent": false,           true pour un événement urgent : radio hors ligne, alerte de télémétrie, canal modifié
  "instance": {
    "name": "...",           le nom de la radio
    "radio_public_key": "..." la clé publique de la radio (64 caractères hexadécimaux)
  },
  "data": {...},             typé selon l’événement
  "rendered": {
    "title": "...",          la notification telle que les autres canaux l’affichent,
    "body": "..."            dans la langue du canal
  }
}
```

- `data` ne contient jamais plus que le corps v1 du même événement, et moins à deux endroits. Le `text` d’un message vaut `null` quand le réglage **Texte des messages** du canal le retient (**Canaux publics seulement** pour un message direct ou un canal privé, ou **Jamais**) ; `rendered.body` ne contient alors pas de texte non plus. Et aucun message ne porte sa clé de conversation : pour un canal privé, cette clé est le secret du canal. Un corps v1 envoie le texte quel que soit le réglage.
- `radio_public_key` est la clé publique de votre radio, celle que chaque nœud à portée entend déjà dans ses annonces. Elle permet à un même récepteur de distinguer plusieurs instances Meshloom.
- `rendered` est dans la **Langue des notifications** du canal.
- La version 2 peut gagner de nouveaux champs dans `data`. Un récepteur doit ignorer les champs qu’il ne connaît pas. Un changement qui retire ou renomme un champ passera par une version 3.
- Les trois événements `summary.*` sont les résumés produits par le regroupement et les heures calmes du canal. Leur `event_id` est dérivé de leur contenu : un nouvel essai garde le même identifiant. Leur `occurred_at` est en revanche l’heure de chaque envoi : le corps d’un résumé change d’un essai à l’autre, donc dédupliquez sur `event_id` ou `X-Webhook-Id`, jamais sur le corps.

## En-têtes et signature

| En-tête | Valeur |
|---|---|
| `Content-Type` | `application/json` |
| `X-Webhook-Version` | `2` |
| `X-Webhook-Event` | l’événement, comme dans le corps |
| `X-Webhook-Id` | le `dedup_key` : le même à chaque nouvel essai d’une notification |
| `X-Webhook-Timestamp` | secondes epoch de cet envoi |
| `X-Webhook-Signature` | `sha256=` suivi du HMAC-SHA256, en hexadécimal, de l’horodatage, d’un point et du corps ; seulement si le canal a un secret HMAC |

La signature couvre l’horodatage, contrairement à la v1 (qui ne signe que le corps) :

```text
X-Webhook-Signature = "sha256=" + hex(HMAC-SHA256(secret, X-Webhook-Timestamp + "." + corps))
```

Pour vérifier une requête, calculez la même valeur sur les **octets bruts** du corps tel que reçu, comparez-la en temps constant, et refusez un horodatage trop ancien (cinq minutes est une bonne fenêtre) : une requête rejouée plus tard échoue alors. Pour ignorer un nouvel essai honnête, retenez les valeurs de `X-Webhook-Id` déjà traitées.

### Python

```python
import hashlib
import hmac
import time


def verify(secret: str, timestamp: str, body: bytes, signature: str, max_age: int = 300) -> bool:
    """True when the request is signed with `secret` and less than `max_age` seconds old."""
    try:
        age = abs(time.time() - int(timestamp))
    except ValueError:
        return False
    if age > max_age:
        return False
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return hmac.compare_digest("sha256=" + mac.hexdigest(), signature)
```

### Node-RED

Dans un nœud **function** placé après un nœud **http in** (méthode `POST`) dont la case **Ne pas analyser le corps de la requête** est cochée. Cette option existe à partir de Node-RED 4.1. La signature couvre les octets exacts envoyés par Meshloom : vérifiez-la sur le corps brut, puis décodez le JSON, comme le fait l’extrait. Jamais sur `msg.payload` décodé puis resérialisé, qui change les octets (une décimale comme une `value` de télémétrie à `4.0` revient sous la forme `4`) et fait échouer la vérification. Avec un Node-RED plus ancien, qui décode toujours un corps JSON, mettez-le à jour, ou vérifiez la signature dans un script ou un service placé devant.

```javascript
// Nœud function après un nœud "http in" (POST) avec "Ne pas analyser le corps de la requête"
// coché : msg.payload est alors le corps brut (un Buffer). Ajoutez le module `crypto` dans
// l'onglet Setup du nœud, et le secret dans les variables d'environnement du flux.
const secret = env.get("MESHLOOM_WEBHOOK_SECRET");
const timestamp = msg.req.get("X-Webhook-Timestamp") || "";
const signature = msg.req.get("X-Webhook-Signature") || "";
if (!Buffer.isBuffer(msg.payload)) {
    node.error("Cochez \"Ne pas analyser le corps de la requête\" sur le nœud http in");
    return null;
}
const raw = msg.payload.toString("utf8");

const age = Math.abs(Date.now() / 1000 - Number(timestamp));
const expected = "sha256=" + crypto.createHmac("sha256", secret)
    .update(timestamp + "." + raw).digest("hex");
const a = Buffer.from(expected);
const b = Buffer.from(signature);
if (!(age <= 300 && a.length === b.length && crypto.timingSafeEqual(a, b))) {
    node.warn("Webhook Meshloom : signature invalide ou requête trop ancienne");
    return null;
}
msg.payload = JSON.parse(raw);
return msg;
```

## Un exemple par événement

Ces corps sont produits par le code de Meshloom (radio nommée `Base`, canal en français, **Texte des messages** sur **Toujours**).

### `message.dm`

```json
{
  "version": 2,
  "event": "message.dm",
  "event_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
  "occurred_at": 1767225600,
  "dedup_key": "099a0aa364018cda",
  "urgent": false,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "type": "PRIV",
    "text": "On se retrouve au relais ?",
    "sender_name": "Alice",
    "channel_name": "",
    "mention": false
  },
  "rendered": {
    "title": "Message de Alice",
    "body": "On se retrouve au relais ?"
  }
}
```

### `message.channel`

```json
{
  "version": 2,
  "event": "message.channel",
  "event_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
  "occurred_at": 1767225600,
  "dedup_key": "ae9da36225fbd73b",
  "urgent": false,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "type": "CHAN",
    "text": "Bonjour",
    "sender_name": "Bob",
    "channel_name": "Public",
    "mention": false
  },
  "rendered": {
    "title": "Public",
    "body": "Bonjour"
  }
}
```

### `message.mention`

```json
{
  "version": 2,
  "event": "message.mention",
  "event_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
  "occurred_at": 1767225600,
  "dedup_key": "22a2711e5e8950bf",
  "urgent": false,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "type": "CHAN",
    "text": "@[Base] tu me reçois ?",
    "sender_name": "Bob",
    "channel_name": "Public",
    "mention": true
  },
  "rendered": {
    "title": "Mention dans Public par Bob",
    "body": "@[Base] tu me reçois ?"
  }
}
```

### `contact.first_seen`

```json
{
  "version": 2,
  "event": "contact.first_seen",
  "event_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
  "occurred_at": 1767225600,
  "dedup_key": "b49e378692f3e372",
  "urgent": false,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "public_key": "ab12cd34ef560123456789abcdef0123456789abcdef0123456789abcdef0123456789ab",
    "name": "Alice",
    "contact_type": 1
  },
  "rendered": {
    "title": "Nouveau contact : Alice",
    "body": "Alice est apparu sur le mesh"
  }
}
```

### `channel.found`

```json
{
  "version": 2,
  "event": "channel.found",
  "event_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
  "occurred_at": 1767225600,
  "dedup_key": "783d8aaa6df4925a",
  "urgent": false,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "name": "#meshloom",
    "channel_key": "A1B2C3D4E5F60718293A4B5C6D7E8F90"
  },
  "rendered": {
    "title": "Nouveau canal détecté : #meshloom",
    "body": "Nouveau canal détecté : #meshloom"
  }
}
```

### `telemetry.alert`

```json
{
  "version": 2,
  "event": "telemetry.alert",
  "event_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
  "occurred_at": 1767225600,
  "dedup_key": "4b5884b6c2d75a9d",
  "urgent": true,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "rule_id": "battery",
    "public_key": "ab12cd34ef560123456789abcdef0123456789abcdef0123456789abcdef0123456789ab",
    "name": "Relais Nord",
    "metric": "battery",
    "unit": "V",
    "value": 3.46,
    "threshold": 3.5,
    "op": "lt"
  },
  "rendered": {
    "title": "Batterie faible : Relais Nord",
    "body": "3.46 V (seuil 3.5 V)"
  }
}
```

### `radio.offline`

```json
{
  "version": 2,
  "event": "radio.offline",
  "event_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
  "occurred_at": 1767225600,
  "dedup_key": "4cf48071f6e14756",
  "urgent": true,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "offline_s": 300,
    "connection": "TCP: 192.168.1.20:5000"
  },
  "rendered": {
    "title": "Radio hors ligne depuis 5 min (TCP 192.168.1.20:5000)",
    "body": "La radio est injoignable. Meshloom continue de se reconnecter."
  }
}
```

### `radio.online`

```json
{
  "version": 2,
  "event": "radio.online",
  "event_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
  "occurred_at": 1767225600,
  "dedup_key": "c76b51d7385fcc87",
  "urgent": false,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "downtime_s": 125,
    "connection": "TCP: 192.168.1.20:5000"
  },
  "rendered": {
    "title": "Radio de nouveau en ligne après 2 min",
    "body": "La radio est de nouveau joignable (TCP 192.168.1.20:5000)."
  }
}
```

### `app.started`

```json
{
  "version": 2,
  "event": "app.started",
  "event_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
  "occurred_at": 1767225600,
  "dedup_key": "ccd49217b402106a",
  "urgent": false,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "version": "5.0.0",
    "connect_s": 12,
    "radio_connected": true
  },
  "rendered": {
    "title": "Meshloom a démarré (5.0.0)",
    "body": "Radio connectée en 12 s"
  }
}
```

### `app.update`

```json
{
  "version": 2,
  "event": "app.update",
  "event_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
  "occurred_at": 1767225600,
  "dedup_key": "6bf0631fa24481f7",
  "urgent": false,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "current": "5.0.0",
    "latest": "5.1.0"
  },
  "rendered": {
    "title": "Mise à jour Meshloom disponible",
    "body": "La version 5.1.0 est disponible (vous avez 5.0.0)"
  }
}
```

### `notify.destination_changed`

```json
{
  "version": 2,
  "event": "notify.destination_changed",
  "event_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
  "occurred_at": 1767225600,
  "dedup_key": "64e626bdf16a8d9f",
  "urgent": false,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "kinds": [
      "webhook"
    ]
  },
  "rendered": {
    "title": "Destination de notification modifiée",
    "body": "Modifié : webhook. Si ce n'est pas vous, vérifiez les réglages."
  }
}
```

### `notify.channel_changed`

```json
{
  "version": 2,
  "event": "notify.channel_changed",
  "event_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
  "occurred_at": 1767225600,
  "dedup_key": "08d5d02ca52f2f29",
  "urgent": true,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "action": "created",
    "channel_type": "webhook",
    "name": "Node-RED"
  },
  "rendered": {
    "title": "Canal de notification modifié",
    "body": "Canal « Node-RED » créé. Si ce n'est pas vous, vérifiez les réglages."
  }
}
```

### `notify.test`

```json
{
  "version": 2,
  "event": "notify.test",
  "event_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
  "occurred_at": 1767225600,
  "dedup_key": "00000000000000000000000000000000",
  "urgent": false,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {},
  "rendered": {
    "title": "Batterie faible : Test repeater",
    "body": "3.46 V (seuil 3.5 V)"
  }
}
```

### `summary.grouped`

```json
{
  "version": 2,
  "event": "summary.grouped",
  "event_id": "ef2f20bc5ba52d181a4775668e7eddfc",
  "occurred_at": 1767225600,
  "dedup_key": "ef2f20bc5ba52d181a4775668e7eddfc",
  "urgent": false,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "count": 7,
    "dms": 2,
    "private": 0,
    "others": 0,
    "channels": {
      "Public": 5
    }
  },
  "rendered": {
    "title": "+7 messages: 5 dans Public, 2 MP",
    "body": "+7 messages: 5 dans Public, 2 MP"
  }
}
```

### `summary.first_seen`

```json
{
  "version": 2,
  "event": "summary.first_seen",
  "event_id": "f2957bc1a8d16995273116e523f285f6",
  "occurred_at": 1767225600,
  "dedup_key": "f2957bc1a8d16995273116e523f285f6",
  "urgent": false,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "count": 4
  },
  "rendered": {
    "title": "+4 nouveaux contacts",
    "body": "+4 nouveaux contacts"
  }
}
```

### `summary.digest`

```json
{
  "version": 2,
  "event": "summary.digest",
  "event_id": "83165c6200c16c6d10b8c0f1a1507abd",
  "occurred_at": 1767225600,
  "dedup_key": "83165c6200c16c6d10b8c0f1a1507abd",
  "urgent": false,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "scope": "quiet_hours",
    "count": 9,
    "conversations": 3,
    "items": [
      [
        "Public",
        6
      ]
    ],
    "more": 0,
    "dms": 3,
    "private": 0,
    "contacts": 2,
    "others": 0
  },
  "rendered": {
    "title": "Résumé des heures calmes",
    "body": "Pendant les heures calmes : 9 messages dans 3 conversations (Public 6, 3 MP), 2 nouveaux contacts"
  }
}
```
