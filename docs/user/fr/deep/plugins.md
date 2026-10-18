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

Vous pouvez restreindre encore ce qu’une extension lit en choisissant les canaux et contacts qu’elle voit, comme pour une intégration dans **MQTT et automatisation**.

Une extension ne reçoit jamais ses propres messages en retour, sauf si elle a le droit de lire les messages que vous envoyez.

## Limites

- **Messages envoyés.** Par défaut 30 par heure et 200 par jour pour chaque copie d’une extension. Une extension peut demander plus, jusqu’à 60 par heure et 500 par jour, et vous pouvez toujours baisser ces valeurs. Les compteurs survivent à un redémarrage de Meshloom.
- **Espacement.** Meshloom attend au moins 2 secondes entre deux messages automatiques, quelle que soit l’extension ou le bot qui les envoie, pour laisser aux répéteurs le temps de se remettre à l’écoute.
- **Taille.** Un message est limité à ce que la radio peut transporter : 156 octets pour un message privé, moins sur un canal, où le nom de votre nœud passe en premier.
- **Régions.** Une extension ne peut limiter un message de canal qu’à une région que Meshloom connaît déjà.
- **Stockage.** 5 Mo par copie d’une extension.

Une demande refusée apparaît dans le journal de l’extension avec sa raison, par exemple « non autorisé », « limite atteinte » ou « message trop long ».

## Isolation

Le système d’exploitation dresse des murs autour du processus de chaque extension. Leur solidité dépend de la façon dont vous avez installé Meshloom. Le niveau est choisi au démarrage du serveur, écrit dans le journal du serveur et indiqué par `/api/health` dans `plugins_isolation`.

### Isolé par bubblewrap (`bwrap`)

Le paquet Linux, l’image Raspberry Pi et une installation depuis les sources sous Linux utilisent [bubblewrap](https://github.com/containers/bubblewrap). L’extension ne voit que ses propres fichiers, le Python qui l’exécute et un dossier de travail privé. Elle ne voit ni la base de données, ni le reste des fichiers de Meshloom, ni la mémoire du serveur, et elle n’a aucun accès réseau : tout ce qu’il demande passe par Meshloom, qui vérifie les permissions de l’extension. Arrêter l’extension arrête tout ce qu’elle a lancé.

### Utilisateur dédié (`setuid`)

L’image Docker et l’add-on Home Assistant exécutent Meshloom en root, et Docker n’autorise pas bubblewrap. Les extensions tournent sous un compte séparé, `meshloom-plugin`, qui ne peut ni ouvrir la base de données ni lire la mémoire du serveur. Pour que ce soit vrai, Meshloom resserre au démarrage les droits de son dossier de données : le dossier passe en `0711` et ses fichiers en `0600` (lisibles par root seulement) ; les dossiers des extensions restent lisibles. Si vous ouvrez la base depuis l’hôte avec un autre compte, faites-le en root. À ce niveau, une extension qui contournerait les garde-fous internes de Meshloom pourrait encore atteindre le réseau, y compris le proxy radio de Meshloom sur la même machine, par lequel elle pourrait envoyer plus de messages que ses limites ne le permettent. Le compte a l’uid et le gid fixes 10002.

### Hooks seulement (`none`)

Aucun des deux n’est disponible : il ne reste que les garde-fous de Meshloom à l’intérieur du processus de l’extension. Ils arrêtent les erreurs de bonne foi, pas un attaquant déterminé : une extension hostile pourrait lire la base de données et les fichiers de Meshloom. Le journal du serveur le signale par un avertissement à chaque démarrage. Cela arrive quand :

- bubblewrap n’est pas installé (le paquet Linux le recommande ; `sudo apt install bubblewrap` ou `sudo dnf install bubblewrap` l’ajoute) ;
- le système refuse les espaces de noms utilisateur dont bubblewrap a besoin (Ubuntu 24.04 et suivantes les restreignent ; un noyau durci peut les désactiver) ;
- l’image Docker tourne avec `MESHLOOM_RUN_AS_USER`, qui renonce aux droits nécessaires pour passer au compte des extensions ;
- Meshloom tourne sous macOS ou Windows.

### Vérifier votre niveau

Cherchez `Plugin isolation` dans le journal du serveur au démarrage, ou ouvrez `/api/health` une fois connecté et lisez `plugins_isolation` : `bwrap`, `setuid` ou `none`. Après avoir installé bubblewrap, redémarrez Meshloom pour qu’il vérifie à nouveau.

Franchir ces murs est une faille de sécurité : signalez-la comme l’indique la politique de sécurité du projet. Les extensions que vous installez restent du code auquel vous choisissez de faire confiance ; les murs limitent ce qu’une erreur ou une mauvaise mise à jour peut atteindre.
