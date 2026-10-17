---
title: Dépannage
description: Logs DEBUG, instantané de support, et les problèmes de radio qui reviennent le plus souvent.
level: deep
order: 19
---

Commencez par deux choses : activer les logs `DEBUG`, et copier l’instantané de support. Ce sont aussi les deux éléments à joindre à un rapport de bug.

## Logs DEBUG

Définissez `MESHCORE_LOG_LEVEL=DEBUG`, puis redémarrez Meshloom.

- **Paquet Linux ou image Raspberry Pi :** ajoutez la ligne dans `/etc/meshloom/meshloom.env`, puis lancez `sudo systemctl restart meshloom`.
- **Docker :** ajoutez-la sous `environment:` dans `docker-compose.yml`, puis lancez `docker compose up -d`.
- **Depuis un dépôt cloné :**

```bash
MESHCORE_LOG_LEVEL=DEBUG uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Instantané de support

Ouvrez **Réglages > À propos > Ouvrir l’instantané de support de débogage** (les mêmes données sont sur `/api/debug`). Il liste la version, le système, l’état de la radio, l’écart entre la radio et la base de données (contacts et canaux), si la CLI compagnon de la radio a répondu (firmware v1.18 et suivants) et les logs récents. Hors des logs, il ne contient jamais la clé privée, et les autres clés n’y figurent que sous forme d’empreintes. Les logs ne contiennent jamais la clé privée ni un mot de passe Wi-Fi, même en `DEBUG` ; en `DEBUG`, ils peuvent encore contenir des noms et des clés de canal, les mots de passe de connexion aux répéteurs et aux salons, et le code PIN Bluetooth. Arrêtez la copie à la ligne `STOP COPYING HERE` si vous ne voulez pas les partager.

## `ModuleNotFoundError: No module named 'meshcore'`

Cela arrive en lançant depuis un dépôt cloné. Lancez `uv sync` à la racine du dépôt, vérifiez que `uv --version` répond, et démarrez toujours le serveur avec `uv run uvicorn ...`. La cause habituelle est un `/usr/bin/uvicorn` du système, utilisé à la place. Pour installer `uv` : `curl -LsSf https://astral.sh/uv/install.sh | sh`.

## La radio reste en pause : « Aucune radio connectée »

Une installation neuve affiche la bannière **Aucune radio connectée**, et la barre d’état indique **Radio en pause**. C’est normal : le lien vers la radio ne se choisit plus pendant l’installation. Appuyez sur **Connecter une radio** (ou sur **Connecter** dans la barre d’état), choisissez USB, TCP ou Bluetooth dans **Réglages > Radio**, puis **Enregistrer et connecter**. Voir [Premier lancement](/docs/first-run/) et [Transports radio](/docs/deep/transports/).

Les variables `MESHCORE_SERIAL_PORT`, `MESHCORE_TCP_HOST` et `MESHCORE_BLE_ADDRESS` ne sont plus lues. Une base existante qui les contenait peut les importer une seule fois, au premier démarrage après une mise à jour.

## « Radio déconnectée », ou la connexion n’aboutit jamais

1. Vérifiez le câble et l’alimentation, ou l’adresse de la radio en TCP et en Bluetooth.
2. Dans **Réglages > Radio**, vérifiez le port série (essayez **Actualiser les ports**), l’hôte et le port TCP, ou l’adresse et le PIN Bluetooth.
3. Assurez-vous que rien d’autre ne tient la radio : un port série ne sert qu’un programme à la fois (voir « Contention du port série » plus bas).
4. Une radio qui tourne avec le firmware répéteur ne répond pas à un client. Il lui faut le firmware companion.
5. Meshloom réessaie tout seul toutes les cinq secondes. **Reconnecter** force une tentative.

## L’appairage Bluetooth échoue

Le bloc **Appairage** de **Réglages > Radio** (sous le PIN BLE) en donne la raison. Voir [Appairage](/docs/deep/transports/#appairage).

| Message | Que faire |
|---|---|
| **PIN incorrect** | Vérifiez le PIN affiché par la radio, enregistrez-le, puis appuyez sur **Réessayer l’appairage**. D’ici là, l’appairage automatique reste suspendu pour ce PIN. |
| **La radio a refusé l’appairage** | La radio a été réinitialisée, ou appairée avec une autre machine : appuyez sur **Oublier l’appairage**, puis sur **Réessayer l’appairage**. Sous Ubuntu ou Home Assistant OS, le système peut aussi empêcher Meshloom de recevoir la demande de PIN ; le journal le signale alors. |
| **La radio refuse l’appairage enregistré sur cette machine** | La radio a été réinitialisée ou reflashée alors que cette machine gardait son appairage : appuyez sur **Oublier l’appairage**. |
| **Radio introuvable** ou **n’a pas répondu** | Allumez la radio, rapprochez-la et déconnectez le téléphone qui l’utilise. Meshloom réessaie tout seul, jusqu’à une fois par minute. |
| **Le PIN doit compter de 1 à 6 chiffres** | Saisissez le PIN tel que la radio l’affiche. |
| **Le service Bluetooth du système est injoignable** | Dans Docker, montez le `/run/dbus/system_bus_socket` de l’hôte dans le conteneur (l’installeur le fait avec Docker en root). Dans Home Assistant, l’add-on joint le bus de l’hôte tout seul : redémarrez-le, et si le problème persiste, son journal donne le détail. Meshloom tente quand même de se connecter sans appairer. |
| **bluetoothd n’est pas lancé** | Démarrez-le : `sudo systemctl start bluetooth`. |
| **n’a pas le droit d’appairer** | Le système a refusé à Meshloom l’accès au service Bluetooth. Vérifiez la politique D-Bus de BlueZ, ou appairez avec `bluetoothctl`. |
| **Aucun adaptateur Bluetooth**, **adaptateur éteint**, **bloqué (rfkill)** | Branchez ou allumez l’adaptateur : `bluetoothctl power on`, ou `rfkill unblock bluetooth`. |
| **bloquée dans les réglages Bluetooth de cette machine** | Débloquez la radio : `bluetoothctl unblock <adresse>`. |

## Fenêtre « Cette radio n’est pas liée » ou « ne correspond pas à l’identité enregistrée »

Meshloom lie sa base à la clé publique de votre radio, pour ne jamais mélanger l’historique de deux radios.

- **Cette radio n’est pas liée à cette instance** apparaît après une mise à jour quand la base contient déjà des contacts ou des messages, même si la radio est la même. **Clé précédente : Inconnue** signifie seulement que la base est antérieure à cette vérification, pas que la radio a changé. Si c’est votre radio, choisissez **Lier sans effacer** : l’historique reste. Si c’est une autre radio, choisissez **Nouvelle radio** : les contacts et messages du mesh, les paquets stockés et l’historique de télémétrie sont effacés.
- **Cette radio ne correspond pas à l’identité enregistrée** signifie qu’une radio avec une autre clé est connectée. Choisissez **Annuler** pour garder l’identité enregistrée (puis reconnectez la radio d’origine), ou **Effacer et continuer** pour adopter la nouvelle et effacer les mêmes données. Conserver l’historique n’est pas proposé, car il appartient à l’autre radio.

Dans les deux cas, les canaux et les réglages de Meshloom restent.

## « Impossible d’énumérer les contacts de la radio », ou table de contacts pleine

Une radio ne garde qu’un nombre limité de contacts. Meshloom y charge vos favoris et vos contacts récents pour qu’elle puisse accuser réception des messages directs. Si la table est pleine, vous pouvez la vider avec une autre application MeshCore, baisser **Contacts max sur la radio** dans **Réglages > Radio**, activer **Laisser la radio supprimer ses plus anciens contacts** dans **Réglages → Radio → Avancé** (la radio retire alors elle-même les vieux contacts), ou ignorer l’avertissement. **L’envoi et la réception de messages continuent de fonctionner.** Voir [Radio, contacts et canaux](/docs/deep/radio/).

## Des messages restent dans la radio

Meshloom reçoit normalement les messages dès que la radio les annonce, et vérifie une fois par heure s’il en a manqué (et si des emplacements de canal ont dérivé). Si des messages s’accumulent quand même dans la radio, activez **Vérifier les messages toutes les 10 secondes** dans **Réglages → Radio → Avancé** : la vérification a alors lieu toutes les 10 secondes.

## Un message part sur le mauvais canal

Une autre application modifie probablement les emplacements de canal de la radio sous les pieds de Meshloom. Activez **Réécrire le salon avant chaque envoi** dans **Réglages → Radio → Avancé** : Meshloom réécrit alors le canal dans la radio avant chaque envoi. C’est fiable, mais cela ajoute un court délai à chaque message.

## Contention du port série

Un port série ne peut servir qu’un programme. Si une autre application MeshCore, une console série ou un second Meshloom le tient, les tentatives de connexion échouent en boucle. Après trois lignes « Serial Connection started » identiques, Meshloom écrit un seul `WARNING` sur une possible contention et cesse de répéter la ligne. Fermez l’autre programme.

## Le travail de démarrage boucle

Si la radio est injoignable un instant, Meshloom relance son travail de démarrage toutes les cinq secondes. C’est voulu. Si ce travail dépasse cinq minutes deux fois de suite, Meshloom signale « Radio startup appears stuck » : redémarrez la radio et le serveur. Après trois échecs de suite, le log évoque les causes habituelles : un autre programme tient le port, la radio tourne avec le firmware répéteur au lieu du firmware companion, ou elle doit être débranchée puis rebranchée.

## L’horloge de la radio est bloquée dans le futur

La radio n’accepte qu’une heure postérieure à la sienne. Une radio dont l’horloge est en avance (par exemple en 2082) refuse toute mise à l’heure et date ses messages et ses annonces de cette heure fausse. Meshloom la corrige en deux temps, seulement quand l’horloge a plus de 30 secondes d’avance.

**1. Un redémarrage.** Meshloom redémarre la radio une fois par radio et par démarrage de Meshloom, que ce soit à la connexion, pendant la synchronisation périodique ou après une modification dans **Réglages > Radio** : les cartes dont l’horloge ne survit pas au redémarrage acceptent alors la bonne heure à la connexion suivante. Pendant que la radio redémarre, la barre d’état affiche **Radio en redémarrage** et Meshloom se reconnecte tout seul, puis refait son travail de démarrage. Inutile d’appuyer sur **Reconnecter** : pendant le redémarrage, il répond seulement que la radio redémarre. Si la radio n’est pas revenue au bout de 90 secondes, Meshloom le signale et affiche de nouveau les erreurs de reconnexion.

**2. Un passage par zéro de l’horloge**, si elle est toujours en avance après ce redémarrage (une carte qui garde son horloge au redémarrage). À la connexion suivante, Meshloom règle l’heure maximale que la radio peut contenir (l’an 2106) ; l’horloge de la radio repasse alors à zéro, et Meshloom règle la bonne heure puis la vérifie. Cela se fait seulement quand la radio a plus d’un an d’avance sur le serveur et que son horloge dépasse de plus de dix ans la date de cette version de Meshloom (une horloge du serveur en retard, même de plusieurs années sur une carte hors ligne, ne doit pas faire reculer une radio à l’heure), pendant le démarrage de la connexion (jamais pendant la synchronisation périodique), et au plus une fois par radio jusqu’au prochain démarrage de Meshloom, que cela ait marché ou non. **Corriger une horloge radio bloquée dans le futur**, dans **Réglages > Radio > Avancé**, l’active ou la désactive ; l’option est activée par défaut et s’applique à la prochaine connexion.

Le log montre chaque étape : `Radio clock at connect` donne l’horloge de la radio avant que Meshloom n’y touche, `Radio clock still ahead after reboot` annonce le passage par zéro et `Radio clock fixed by wraparound` le confirme. Si le log indique `Radio did not wrap its clock`, la carte n’est pas repassée à zéro et son horloge peut maintenant afficher 2106 : redémarrez la radio à la main, réglez son heure avec une autre application MeshCore si elle le permet, et désactivez l’option pour cette carte. Si rien n’y fait, la radio reste en avance et le log le signale à chaque connexion. Une radio qui a moins d’un an d’avance après le redémarrage, ou dont l’horloge ne dépasse pas de dix ans la date de cette version, est laissée telle quelle (`no clock wraparound` dans le log) : vérifiez d’abord l’heure du serveur.

Meshloom ne fait aucune des deux étapes tant que l’horloge du serveur lui-même est fausse (avant 2026 ou avant la date de cette version de Meshloom, par exemple une carte démarrée sans source d’heure réseau) : le log indique alors `System clock looks wrong`. Remettez d’abord le serveur à l’heure.

## `/docs` n’est pas cette documentation

`http://localhost:8000/docs` est la documentation technique de l’API, générée par le serveur. La documentation utilisateur est ce site.

## La page ne se charge pas mais l’API répond

Les fichiers de l’interface sont absents. Depuis un dépôt cloné, construisez-les : `cd frontend && npm install && npm run build`. Le serveur cherche dans `frontend/dist`, puis dans `frontend/prebuilt` ; si aucun n’existe, il ne sert que l’API. Les paquets publiés contiennent déjà l’interface.

## Rien ne se met à jour en direct

La connexion en direct (WebSocket, `/api/ws`) ne passe pas à travers votre reverse proxy. Vérifiez qu’il transmet les en-têtes d’upgrade, et que son délai d’inactivité dépasse 30 secondes (l’interface envoie un ping toutes les 30 secondes). Voir [HTTPS](/docs/deep/https/).

## Signaler un bug

Ouvrez une issue sur le [dépôt GitHub](https://github.com/TwinRocket/meshloom) en joignant les logs DEBUG et l’instantané de support.
