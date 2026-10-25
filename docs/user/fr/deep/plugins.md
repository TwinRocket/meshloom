---
title: Extensions
description: Ce qu’une extension peut lire et envoyer, et les limites que Meshloom lui impose.
level: deep
order: 22
---

> Brouillon. Les extensions arrivent avec Meshloom 5.0.0 et remplacent les bots Python. Cette page décrit leurs permissions et leurs limites ; leur installation et leur configuration seront décrites avec l’interface.

Une extension tourne dans son propre processus, à part de Meshloom. Elle ne peut ouvrir ni votre base de données, ni vos fichiers, ni le réseau par elle-même : elle peut seulement demander à Meshloom, qui vérifie chaque demande d’après les permissions que vous lui avez accordées. Une demande qui ne lui a pas été accordée est refusée avant que quoi que ce soit ne se passe.

Une même extension peut être installée plusieurs fois (par exemple une copie par canal). Chaque copie a ses propres permissions, réglages, limites et données.

## Permissions

Une extension liste les permissions dont elle a besoin. Vous les voyez avant de l’installer, et elle n’obtient que ce qu’elle demande et que vous acceptez.

| Permission | Ce que l’extension peut faire |
|---|---|
| Lire les messages | Recevoir les messages que Meshloom entend. Par défaut seulement les **canaux publics** : le canal Public et les canaux `#` que tout le monde peut rejoindre. Elle peut aussi demander **tous les canaux** (privés compris), vos **messages privés** et **les messages que vous envoyez**. |
| Envoyer des messages | Envoyer depuis votre radio, sous votre nom. Par défaut **seulement des réponses**, dans la conversation du message auquel elle répond. Elle peut demander à écrire **aux canaux et contacts qu’elle peut lire**, ou **partout** : à n’accepter que pour une extension de confiance. |
| Lire les paquets radio | Recevoir les paquets bruts. Elle ne voit leur contenu déchiffré que pour les messages qu’elle peut lire. |
| Lire les contacts | Voir les noms, clés publiques, dernière activité et positions des nœuds que vous connaissez. |
| Lister les canaux | Voir les noms et les clés des canaux publics, et des canaux privés quand elle peut lire tous les canaux. La clé d’un canal permet à n’importe qui de le lire. |
| Lire la télémétrie | Recevoir la télémétrie de vos nœuds et l’état de Meshloom. |
| Stocker des données | Conserver jusqu’à 5 Mo de données à elle dans Meshloom. |
| S’exécuter à intervalles | Se réveiller plus tard, au plus tôt 10 secondes après l’avoir demandé. |
| Se connecter à internet | Demander à Meshloom de contacter les serveurs qu’elle liste, et eux seuls (voir plus bas). |

Vous pouvez restreindre encore ce qu’une extension lit en choisissant les canaux et contacts qu’elle voit, comme pour une intégration dans **MQTT et automatisation**.

Une extension ne reçoit jamais ses propres messages en retour, sauf si elle a le droit de lire les messages que vous envoyez.

### Se connecter à internet

Une extension n’ouvre jamais de connexion elle-même. Quand elle indique qu’elle **contacte `api.example.com`**, Meshloom fait la requête pour elle, et seulement vers les noms exacts listés (`api.example.com` ne couvre ni `www.example.com` ni un autre port). Meshloom vérifie aussi que :

- l’adresse est chiffrée (`https://`) ;
- le nom ne mène pas à votre réseau local (votre box, un NAS, `localhost`), quelle que soit l’adresse vers laquelle il pointe ;
- une redirection ne mène qu’à un autre nom listé, trois fois au plus ; un mot de passe ou une clé d’API envoyés par l’extension ne suivent pas une redirection vers un autre serveur ;
- la requête ne porte aucun cookie ni rien de propre à Meshloom (ni compte, ni session) ;
- chaque réponse est coupée à 1 Mo, une requête envoie au plus 256 Ko et dure au plus 10 secondes ;
- une copie d’extension fait au plus 60 requêtes par heure.

Les serveurs sous `meshloom.app` sont réservés aux extensions de Meshloom. Le journal de Meshloom montre chaque requête avec son adresse, mais jamais la partie après `?`, où se trouvent souvent les clés d’API.

Une extension peut aussi demander à **parler à un appareil de votre réseau** (une box domotique, une imprimante). Le consentement liste alors chaque appareil par son nom (`hue-bridge.local`, `192.168.1.20:8080`...). Seuls les noms locaux (`nas`, `*.local`, `*.lan`, `*.home.arpa`, `*.internal`) et les adresses locales sont acceptés, y compris celles d’un VPN comme Tailscale (`100.x.y.z`) ; une adresse IPv6 avec un port s’écrit entre crochets (`[fd00::1]:80`). Meshloom ne joint que ces appareils, sur le port indiqué, éventuellement sans chiffrement (`http://`) ou sans vérifier un certificat auto-signé ; il refuse la machine où tourne Meshloom elle-même. Dans un conteneur (Docker, add-on Home Assistant), Meshloom ne voit pas l’adresse propre de cette machine sur votre réseau : une extension qui la liste peut donc encore la joindre, proxy radio compris, ne l’acceptez pas. Dans l’add-on Home Assistant, le nom `homeassistant` est refusé volontairement : il mène à la passerelle du Superviseur, c’est-à-dire à la machine Home Assistant elle-même (son API et le proxy radio de Meshloom). Le nom doit mener à une adresse de votre réseau local à chaque résolution, une redirection ne peut aller que vers un autre appareil ou serveur listé, et toutes les autres règles ci-dessus s’appliquent. N’acceptez qu’un appareil que vous connaissez : l’extension pourra lui parler aussi librement que vous.

Associée à la lecture des canaux privés ou des messages privés, cette permission permet à une extension de les envoyer aux serveurs qu’elle liste : n’accordez les deux qu’à une extension de confiance.

## Limites

- **Messages envoyés.** Par défaut 30 par heure et 200 par jour pour chaque copie d’une extension. Une extension peut demander plus, jusqu’à 60 par heure et 500 par jour, et vous pouvez toujours baisser ces valeurs. Les compteurs survivent à un redémarrage de Meshloom.
- **Toutes les extensions ensemble.** Quelles que soient les limites de chaque copie, ensemble les extensions envoient au plus 120 messages par heure depuis votre Meshloom. Chaque canal reçoit au plus 20 messages par heure des extensions et chaque contact 10 par heure, toutes extensions confondues. Ces trois limites pourront être baissées, jamais relevées ; le réglage arrive avec l’écran des réglages des extensions (une version ultérieure). Seules les extensions sont concernées : les bots gardent leur propre espacement de 2 secondes et ne sont pas comptés. Les compteurs survivent à un redémarrage de Meshloom.
- **Boucles.** Une copie d’extension répond au plus une fois toutes les 30 secondes au même expéditeur, et ne répond pas à un message arrivé moins de 5 secondes après un de ses envois vers le même canal ou contact. Cela empêche deux répondeurs automatiques, même sur deux Meshloom différents, de se répondre sans fin.
- **Espacement.** Meshloom attend au moins 2 secondes entre deux messages automatiques, quelle que soit l’extension ou le bot qui les envoie, pour laisser aux répéteurs le temps de se remettre à l’écoute.
- **Taille.** Un message est limité à ce que la radio peut transporter : 156 octets pour un message privé, moins sur un canal, où le nom de votre nœud passe en premier.
- **Régions.** Une extension ne peut limiter un message de canal qu’à une région que Meshloom connaît déjà.
- **Stockage.** 5 Mo par copie d’une extension.
- **Internet.** 60 requêtes par heure par copie d’une extension, 10 secondes chacune, réponses coupées à 1 Mo. Ce compteur repart de zéro au redémarrage de Meshloom.

Une demande refusée apparaît dans le journal de l’extension avec sa raison, par exemple « non autorisé », « limite atteinte », « message trop long » ou « serveur non autorisé ». Pour les limites ci-dessus, la raison dit laquelle s’est déclenchée : la limite de la copie, toutes les extensions ensemble, le canal ou le contact, le délai par expéditeur ou le délai de calme.

## Isolation

Le système d’exploitation dresse des murs autour du processus de chaque extension. Leur solidité dépend de la façon dont vous avez installé Meshloom. Le niveau est choisi au démarrage du serveur, écrit dans le journal du serveur et indiqué par `/api/health` dans `plugins_isolation`.

### Isolé par bubblewrap (`bwrap`)

Le paquet Linux, l’image Raspberry Pi et une installation depuis les sources sous Linux utilisent [bubblewrap](https://github.com/containers/bubblewrap). L’extension ne voit que ses propres fichiers, le Python qui l’exécute et un dossier de travail privé. Elle ne voit ni la base de données, ni le reste des fichiers de Meshloom, ni la mémoire du serveur, et elle n’a aucun accès réseau : tout ce qu’il demande passe par Meshloom, qui vérifie les permissions de l’extension. Arrêter l’extension arrête tout ce qu’elle a lancé.

### Utilisateur dédié (`setuid`)

L’image Docker et l’add-on Home Assistant exécutent Meshloom en root, et Docker n’autorise pas bubblewrap. Les extensions tournent sous un compte séparé, `meshloom-plugin`, qui ne peut ni ouvrir la base de données ni lire la mémoire du serveur. Pour que ce soit vrai, Meshloom resserre au démarrage les droits de son dossier de données : le dossier passe en `0711` et ses fichiers en `0600` (lisibles par root seulement) ; les dossiers des extensions restent lisibles. Si vous ouvrez la base depuis l’hôte avec un autre compte, faites-le en root. À ce niveau, une extension qui contournerait les garde-fous internes de Meshloom pourrait encore atteindre le réseau, y compris le proxy radio de Meshloom sur la même machine, par lequel elle pourrait envoyer plus de messages que ses limites ne le permettent. Le compte a l’uid et le gid fixes 10002. Avec `MESHLOOM_RUN_AS_USER`, Meshloom ne garde que les trois droits nécessaires pour exécuter les extensions sous ce compte : les extensions restent à ce niveau, et le dossier de données appartient alors au compte que vous avez choisi.

### Hooks seulement (`none`)

Aucun des deux n’est disponible : il ne reste que les garde-fous de Meshloom à l’intérieur du processus de l’extension. Ils arrêtent les erreurs de bonne foi, pas un attaquant déterminé : une extension hostile pourrait lire la base de données et les fichiers de Meshloom. Le journal du serveur le signale par un avertissement à chaque démarrage. Cela arrive quand :

- bubblewrap n’est pas installé (le paquet Linux le recommande ; `sudo apt install bubblewrap` ou `sudo dnf install bubblewrap` l’ajoute) ;
- le système refuse les espaces de noms utilisateur dont bubblewrap a besoin (Ubuntu 24.04 et suivantes les restreignent ; un noyau durci peut les désactiver) ;
- Meshloom tourne sous macOS ou Windows.

### Vérifier votre niveau

Cherchez `Plugin isolation` dans le journal du serveur au démarrage, ou ouvrez `/api/health` une fois connecté et lisez `plugins_isolation` : `bwrap`, `setuid` ou `none`. Après avoir installé bubblewrap, redémarrez Meshloom pour qu’il vérifie à nouveau.

Franchir ces murs est une faille de sécurité : signalez-la comme l’indique la politique de sécurité du projet. Les extensions que vous installez restent du code auquel vous choisissez de faire confiance ; les murs limitent ce qu’une erreur ou une mauvaise mise à jour peut atteindre.

## Réglages → Extensions (brouillon)

Les administrateurs gèrent les extensions dans **Réglages → Extensions**. Le haut de la page indique le niveau d'isolation en vigueur et ce qu'il signifie. L'onglet **Installées** liste chaque copie avec son état, un badge quand elle demande votre attention (réglages requis, permissions à revoir, consentement requis), si elle est fournie, un interrupteur et des flèches pour réordonner. **Store** et **Mises à jour** sont des emplacements pour l'instant.

- **Ajouter une extension fournie** liste les extensions livrées avec Meshloom (voir plus bas), fonctionne sans Internet, et montre ce qu'elle peut faire en clair, les serveurs qu'elle contacte et sa déclaration de confidentialité. Quand elle peut lire les messages privés (messages directs ou tous les canaux) et joindre Internet, un avertissement dit qu'elle peut transmettre vos messages privés à ces serveurs. Rien n'est installé tant que vous n'avez pas coché la case et confirmé.
- Une extension **révoquée** ou **incompatible** ne peut pas être activée. Une extension à examiner reste arrêtée tant que vous ne l'avez pas ouverte et choisi **Accepter et activer**.
- Le tiroir de détail permet de décocher des permissions, de choisir les conversations qu'elle peut lire, de baisser ses limites d'envoi (jamais au-dessus de ce qu'elle demande), de lire son journal, de la redémarrer, de **copier un diagnostic** pour un rapport de bug (sans valeur de réglage ni secret) et de la désinstaller, en gardant ses données ou non.
- Le bloc **Réglages** du tiroir est généré à partir de la description que l'extension donne de ses propres réglages (les champs obligatoires portent une étoile). Un réglage secret (clé d'API, mot de passe) n'est jamais affiché, même après l'enregistrement : le tiroir indique seulement qu'une valeur est définie, et vous pouvez la **Remplacer** (laissez le champ vide pour garder la valeur actuelle) ou l'**Effacer**. Seul ce que vous changez est envoyé. Une extension qui attend des réglages obligatoires reste arrêtée ; son tiroir a un bouton **Aller aux réglages**.

### Extensions fournies

Deux extensions sont livrées avec Meshloom et s'installent sans aucun réseau :

- **Écho** répond `Plong!` à `!pling`. Elle lit les canaux publics et vos messages directs et ne fait que répondre. Son code sert de référence pour écrire le vôtre.
- **Auto-répondeur** répond aux messages à partir de règles que vous écrivez, sans code. Une règle par ligne, dans son réglage **Règles** : `déclencheur: texte => réponse`. Le déclencheur est `keyword` (le mot apparaît dans le message), `prefix` (le message commence par ce texte) ou `regex` (un motif de 100 caractères au plus, dans un dialecte restreint : au plus deux répétitions de plus de 5 fois, aucun groupe répété qui contient une répétition ou un choix, ni référence arrière ni assertion ; il ne lit que les 200 premiers caractères du message). Ajoutez des options après le déclencheur, séparées par des virgules : `channel=#nom` (ou `channel=dm`) pour limiter une règle à un canal, `cooldown=300` pour une pause en secondes avant de répondre de nouveau à la même personne. La réponse peut contenir `{sender}`, `{channel}` et `{text}`. Par exemple `prefix,channel=#test: !ping => Pong, {sender} !`. La première règle qui correspond décide ; une règle illisible est ignorée et écrite dans le journal. Autres réglages : respecter les majuscules et minuscules, répondre ou non aux messages directs, la pause par défaut (120 secondes) et le nombre maximal de réponses par heure (20). Au-delà, les limites ci-dessus s'appliquent toujours, dont une réponse toutes les 30 secondes au plus à la même personne.

### Extensions locales

**Nouvelle extension locale** (onglet Installées) crée un petit programme Python conservé dans Meshloom, à partir d'un code de départ (un écho qui répond `Plong!` à `!pling`, ou un code vide). Elle démarre éteinte, avec seulement les permissions que vous lui donnez. Dans son tiroir :

- **Code** : modifiez-le dans l'éditeur puis **Enregistrer le code**. À l'enregistrement le code est lu, jamais exécuté : les lignes à vérifier (module non autorisé, `print`, valeur perdue au redémarrage...) sont listées avec leur numéro de ligne, et un serveur cité dans le code (`https://...`) est proposé avec un bouton **Autoriser** qui l'ajoute aux serveurs que l'extension peut joindre.
- **Événements et permissions** : les événements écoutés et ce qu'elle a le droit de faire, modifiés et enregistrés au même endroit.
- **Vérifier** lit le manifeste et analyse le code sans rien exécuter ; erreurs et avertissements viennent avec leur ligne.
- **Tester avec un message** envoie un faux message à l'extension en cours d'exécution (depuis un canal de test, un de vos canaux ou un contact) et montre sa réponse et son journal. **Rien n'est envoyé à la radio** : les réponses sont interceptées. Enregistrez d'abord le code, et activez l'extension pour la tester.
- Le journal du tiroir montre ce que l'extension a écrit, pour voir les erreurs sans ligne de commande.
