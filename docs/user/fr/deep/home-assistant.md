---
title: Home Assistant
description: MQTT Discovery, appareils, automatisations, et les sujets qui posent problème.
level: deep
order: 17
---

Meshloom peut publier ce qu’il sait de votre mesh vers Home Assistant grâce à MQTT Discovery. Les appareils et les entités (les capteurs et interrupteurs que montre Home Assistant) apparaissent tout seuls. Aucun composant personnalisé ni HACS n’est nécessaire.

Ce n’est pas la même chose que l’add-on Home Assistant, qui fait tourner Meshloom lui-même dans Home Assistant (voir [Installation](/docs/install/)). Le lien MQTT décrit ici fonctionne avec n’importe quelle installation.

## Ce qu’il vous faut

- Home Assistant avec l’[intégration MQTT](https://www.home-assistant.io/integrations/mqtt/) configurée.
- Un broker MQTT que Home Assistant et Meshloom peuvent tous deux joindre.
- Meshloom connecté à une radio.

## Mise en place

1. Dans Meshloom, ouvrez **Réglages → MQTT et automatisation**, choisissez **Ajouter une intégration**, puis **MQTT Discovery Home Assistant**.
2. Saisissez l’adresse et le port du broker (port 1883 par défaut). Ajoutez un nom d’utilisateur, un mot de passe et TLS si votre broker les exige. Le préfixe de sujet est `meshcore`, sauf si vous le changez.
3. Dans **Contacts suivis GPS**, choisissez les contacts que vous voulez voir sur la carte de Home Assistant.
4. Dans **Répéteurs suivis en télémétrie**, choisissez les répéteurs dont vous voulez les capteurs. Seuls les répéteurs qui collectent déjà leur télémétrie automatiquement apparaissent dans cette liste. Pour en ajouter un, ouvrez le tableau de bord du répéteur et cochez l’option de suivi tout en bas. Les répéteurs suivis sont listés dans **Réglages → Gestion radio-application**.
5. Dans **Événements de message**, choisissez quels messages déclenchent un événement.
6. Choisissez **Enregistrer et activer**.

Les appareils apparaissent dans Home Assistant sous **Paramètres → Appareils et services → MQTT**. Le formulaire liste aussi ce qui sera créé et les sujets exacts.

## Noms et identifiants

Meshloom identifie un nœud par les 12 premiers caractères de sa clé publique, en minuscules :

- clé publique : `ae92577bae6c4f1d...`
- identifiant du nœud : `ae92577bae6c`
- sujet de sa position : `meshcore/ae92577bae6c/gps`

Home Assistant construit le nom de ses entités à partir du nom des appareils et des capteurs, pas à partir de cet identifiant.

## Ce qui apparaît dans Home Assistant

### Votre radio

Un appareil portant le nom de votre radio. Il se rafraîchit toutes les 60 secondes.

| Entité | Description |
|--------|-------------|
| `binary_sensor.<radio>_connected` | Indique si la radio est connectée |
| `sensor.<radio>_noise_floor` | Bruit radio de fond, en dBm |
| `sensor.<radio>_battery` | Batterie, en V |
| `sensor.<radio>_uptime` | Temps écoulé depuis le démarrage de la radio, en s |
| `sensor.<radio>_last_rssi` et `_last_snr` | Force et qualité du dernier paquet reçu |
| `sensor.<radio>_tx_airtime` et `_rx_airtime` | Temps passé à émettre et à recevoir, en s |
| `sensor.<radio>_packets_received` et `_packets_sent` | Compteurs de paquets |

### Répéteurs

Un appareil par répéteur choisi. Ses capteurs se mettent à jour quand la télémétrie est collectée : automatiquement toutes les 8 heures par défaut (réglable dans **Gestion radio-application**), ou quand vous actualisez le tableau de bord du répéteur.

| Entité | Unité | Description |
|--------|-------|-------------|
| `sensor.<repeater>_battery_voltage` | V | Niveau de batterie |
| `sensor.<repeater>_noise_floor` | dBm | Bruit de fond au niveau du répéteur |
| `sensor.<repeater>_last_rssi` | dBm | Force du dernier paquet reçu |
| `sensor.<repeater>_last_snr` | dB | Qualité du dernier paquet reçu |
| `sensor.<repeater>_packets_received` | nombre | Paquets reçus |
| `sensor.<repeater>_packets_sent` | nombre | Paquets envoyés |
| `sensor.<repeater>_rx_errors` | nombre | Erreurs de réception |
| `sensor.<repeater>_uptime` | s | Temps écoulé depuis le dernier redémarrage |

Si le répéteur a des capteurs d’environnement (format CayenneLPP), Meshloom ajoute un capteur par mesure, comme la température ou l’humidité.

### Contacts

| Entité | Description |
|--------|-------------|
| `device_tracker.<contact>` | Position, avec `latitude`, `longitude` et parfois `altitude`. Elle se met à jour quand une annonce avec des coordonnées GPS est entendue, ou quand la télémétrie du contact contient une position |
| `sensor.<contact>_...` | Un capteur par mesure CayenneLPP d’un contact dont la télémétrie est suivie |

### Événements de messages

`event.<radio>_messages` se déclenche pour chaque message qui correspond à la portée choisie.

| Attribut | Exemple | Description |
|----------|---------|-------------|
| `event_type` | `message_received` | Toujours `message_received` |
| `sender_name` | `Alice` | Nom affiché |
| `sender_key` | `aabbccdd...` | Clé publique de l’expéditeur |
| `text` | `hello` | Texte du message |
| `message_type` | `PRIV` ou `CHAN` | Message direct ou salon |
| `channel_name` | `#general` | Nom du salon, ou vide pour un message direct |
| `conversation_key` | `aabbccdd...` | Clé du contact ou du salon |
| `outgoing` | `false` | Indique si c’est vous qui l’avez envoyé |

## Exemples d’automatisations

### Batterie de répéteur faible

```yaml
automation:
  - alias: "Repeater battery low"
    trigger:
      - platform: numeric_state
        entity_id: sensor.hilltop_battery_voltage
        below: 3.8
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "Repeater Battery Low"
          message: >-
            {{ state_attr('sensor.hilltop_battery_voltage', 'friendly_name') }}
            is at {{ states('sensor.hilltop_battery_voltage') }}V
```

### Radio hors ligne depuis cinq minutes

```yaml
automation:
  - alias: "Radio offline"
    trigger:
      - platform: state
        entity_id: binary_sensor.myradio_connected
        to: "off"
        for: "00:05:00"
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "MeshCore Radio Offline"
          message: "Radio has been disconnected for 5 minutes"
```

### Message dans un salon précis

Réglez la portée sur **Uniquement les canaux/contacts listés** et cochez le salon, ou filtrez tous les événements :

```yaml
automation:
  - alias: "Emergency channel alert"
    trigger:
      - platform: state
        entity_id: event.myradio_messages
    condition:
      - condition: template
        value_template: >-
          {{ trigger.to_state.attributes.channel_name == '#emergency' }}
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "Message in #emergency"
          message: >-
            {{ trigger.to_state.attributes.sender_name }}:
            {{ trigger.to_state.attributes.text }}
```

## Quand cela ne marche pas

Si aucun appareil n’apparaît, vérifiez que l’intégration MQTT de Home Assistant est connectée, que Meshloom affiche l’intégration comme connectée, et que les deux utilisent le même broker. Regardez ce que Meshloom annonce :

```text
mosquitto_sub -h <broker> -t 'homeassistant/#' -v
```

Meshloom n’annonce ses appareils qu’une fois qu’il connaît l’identité de la radio : la radio doit donc être connectée.

Les appareils périmés ou en double se suppriment en envoyant un message vide et retenu sur leur sujet de découverte :

```text
mosquitto_pub -h <broker> -t 'homeassistant/binary_sensor/meshcore_unknown/connected/config' -r -n
mosquitto_pub -h <broker> -t 'homeassistant/sensor/meshcore_unknown/noise_floor/config' -r -n
```

Certaines entités affichent `Inconnu` ou `Indisponible` un moment, ce qui est normal :

- Les capteurs de répéteur restent `Inconnu` tant que Meshloom n’a pas collecté de télémétrie, et passent à `Indisponible` si rien n’arrive pendant 10 heures.
- Le suivi d’un contact reste inconnu tant qu’une annonce avec position, ou un relevé de télémétrie, n’est pas arrivé.
- Les entités de la radio passent à `Indisponible` après 120 secondes sans nouvelle, par exemple quand Meshloom s’arrête.

Désactiver ou supprimer l’intégration retire ses entités de Home Assistant.

## Sujets

Home Assistant lit ces sujets. Vous pouvez aussi les utiliser depuis d’autres outils.

| Sujet | Contenu | Quand |
|-------|---------|-------|
| `meshcore/{node_id}/health` | `{"connected": true, "noise_floor_dbm": -110, ...}` avec les capteurs de la radio ci-dessus | Toutes les 60 s |
| `meshcore/{node_id}/telemetry` | `{"battery_volts": 4.1, ...}` | Quand la télémétrie est collectée |
| `meshcore/{node_id}/gps` | `{"latitude": 48.85, "longitude": 2.35, ...}` | Quand une position est entendue |
| `meshcore/{node_id}/events/message` | `{"event_type": "message_received", ...}` | À chaque message dans la portée |

Le préfixe `meshcore` est celui que vous avez saisi dans le formulaire. Les messages de découverte utilisent toujours le préfixe `homeassistant/` :

| Motif | Entité |
|-------|--------|
| `homeassistant/binary_sensor/meshcore_<node_id>/connected/config` | Connexion de la radio |
| `homeassistant/sensor/meshcore_<node_id>/<nom>/config` | Capteurs de la radio, des répéteurs et des contacts |
| `homeassistant/device_tracker/meshcore_<node_id>/config` | Position d’un contact |
| `homeassistant/event/meshcore_<node_id>/messages/config` | Événements de messages |
