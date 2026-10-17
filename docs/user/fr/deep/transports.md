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
| Bluetooth | **Adresse BLE**, **PIN BLE** | **Rechercher** liste les radios proches ; cliquez sur l’une d’elles pour remplir l’adresse. Le PIN est obligatoire (il s’affiche sur l’écran de la radio). Sous Linux, Meshloom appaire lui-même la radio avec ce PIN (voir [Appairage](#appairage)). Une fois enregistré, laissez le champ PIN vide pour le conserver. |

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

Le BLE exige l’adresse et le PIN. Dans l’add-on Home Assistant, le Bluetooth ne demande aucun réglage : l’add-on utilise l’adaptateur Bluetooth de la machine Home Assistant (intégré ou USB) via le service Bluetooth de l’hôte. L’intégration Bluetooth de Home Assistant peut rester active, car elle partage l’adaptateur. Les proxys Bluetooth (ESPHome, Shelly) ne comptent pas : la radio doit être à portée de la machine Home Assistant elle-même. Meshloom y appaire aussi lui-même la radio avec le PIN (voir [Appairage](#appairage)), mais ce n’est pas encore confirmé sur une machine Home Assistant : si l’appairage échoue sans cesse, passez par l’USB ou le TCP. Par ce même accès au Bluetooth, les bots peuvent atteindre les services système de l’hôte : désactivez-les (`disable_bots`) si l’add-on est joignable depuis l’extérieur de Home Assistant. Avec Docker, il demande en général des modifications manuelles du fichier Compose (accès au Bluetooth de l’hôte, parfois des privilèges supplémentaires). Avec Docker en root sur Linux, l’installeur mappe le socket D-Bus de l’hôte quand il le trouve, mais cela ne suffit pas toujours. Si la radio contient beaucoup de contacts, la première lecture de sa table de contacts peut dépasser le délai. Meshloom charge quand même vos favoris et vos contacts récents du mieux possible, et **Laisser la radio supprimer ses plus anciens contacts** (**Réglages → Radio → Avancé**) est prévu pour ce cas.

### Appairage

Une radio MeshCore demande son PIN une seule fois, la première fois qu’une machine s’appaire avec elle en Bluetooth. Sous Linux, Meshloom s’en charge lui-même quand il se connecte, avec le PIN enregistré : paquet Linux, image Raspberry Pi, Docker en root avec le socket D-Bus de l’hôte mappé, et add-on Home Assistant, via le D-Bus de l’hôte. Plus besoin de `bluetoothctl pair` ni `trust` au préalable. Un appairage fait auparavant avec `bluetoothctl` est réutilisé tel quel, quel que soit le PIN enregistré. Meshloom ne se déclare jamais agent d’appairage par défaut du système : un bureau ou Home Assistant continue de traiter ses propres demandes d’appairage.

Sous le champ PIN, **Appairage** indique où en sont les choses :

- **Appairée avec cette machine** : rien à faire.
- **Pas encore appairée**, ou **Cette machine n’a pas encore vu la radio** : Meshloom appaire à la prochaine connexion, et **Appairer maintenant** le fait tout de suite. La radio doit être allumée, à portée et pas connectée à un téléphone : une radio companion n’accepte qu’une connexion à la fois.
- **La dernière tentative d’appairage a échoué**, avec la raison. Après un PIN incorrect, Meshloom ne fait qu’une tentative, puis suspend l’appairage automatique pour ce PIN : corrigez le PIN et appuyez sur **Enregistrer et connecter**, ou appuyez sur **Réessayer l’appairage**. **Réessayer l’appairage** utilise toujours le PIN enregistré : tant qu’un nouveau PIN est saisi sans être enregistré, le bouton attend l’enregistrement. Quand la radio est absente ou ne répond pas, les tentatives automatiques s’espacent, de 5 secondes jusqu’à une minute, et le panneau indique quand aura lieu la suivante.
- **L’appairage automatique est indisponible**, avec la cause : service Bluetooth injoignable, aucun adaptateur, adaptateur éteint ou bloqué par rfkill, ou permission refusée. Meshloom se connecte alors comme avant, et la radio doit être appairée avec les outils du système.

Appuyez sur **Oublier l’appairage** quand la radio a été réinitialisée ou reflashée : elle ne connaît plus cette machine, et la connexion échoue en boucle. Quand la radio refuse la connexion trois fois de suite sur un appairage existant, le panneau le propose (une radio simplement éteinte ou hors de portée ne compte pas). Après **La radio a refusé l’appairage**, le panneau le propose aussi. Meshloom appaire ensuite de nouveau avec le PIN enregistré. Passer à une autre radio ne demande rien de particulier : l’ancien appairage ne gêne pas, et `bluetoothctl remove <adresse>` le supprime si vous le souhaitez.

Sous macOS et Windows, ou avec une adresse qui n’est pas une adresse MAC (`AA:BB:CC:DD:EE:FF`), appairez la radio avec les outils Bluetooth du système.

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

À chaque retour du lien, Meshloom refait son travail de démarrage : il se remet à l’écoute des événements de la radio, lit la clé privée en mémoire, règle l’horloge de la radio, puis synchronise contacts et canaux. Si la radio refuse l’heure parce que son horloge est en avance, Meshloom la redémarre une fois par radio et par démarrage de Meshloom, arrête le travail de démarrage et se reconnecte quand la radio est revenue ; si l’horloge est toujours en avance à cette reconnexion, Meshloom la fait repasser par zéro jusqu’à la bonne heure (voir [Dépannage](/docs/deep/troubleshooting/#lhorloge-de-la-radio-est-bloquée-dans-le-futur)). Ce travail a une limite de 5 minutes. Si elle est dépassée, Meshloom réessaie une fois, puis signale que le démarrage de la radio semble bloqué et vous demande de redémarrer la radio et le serveur. Si la radio est seulement injoignable un moment, la surveillance réessaie toutes les cinq secondes.
