---
title: Qu’est-ce que c’est
description: MeshCore, la radio compagnon, Meshloom — ce que fait chacun, sans le jargon.
level: start
order: 1
---

Trois choses différentes portent des noms proches. Autant les séparer tout de suite.

## MeshCore, le réseau

MeshCore est un réseau de messages qui circulent par radio, sans opérateur, sans abonnement et sans internet. Les appareils utilisent une radio longue portée et bas débit : quelques kilomètres en terrain dégagé, quelques centaines d’octets par message, pas de photos.

C’est un réseau **mesh** : chaque appareil peut relayer ce qu’il entend. Un message part de chez vous, un répéteur posé sur un point haut le reprend, un autre encore plus loin le reprend à son tour, et il finit par arriver chez quelqu’un que votre radio n’atteint pas directement. On appelle **nœud** chaque appareil du réseau, et **saut** chaque relais traversé en route.

Deux façons d’écrire à quelqu’un :

- Un **message direct**, chiffré pour un seul destinataire.
- Un **salon**, que l’interface de Meshloom appelle un canal : un espace partagé où tout le monde qui possède la même **clé** — une suite de caractères qui sert à chiffrer et déchiffrer — lit et écrit.

Les nœuds se signalent aussi tout seuls, à intervalle régulier, avec une **annonce** (un *advert*) : un petit paquet qui dit « je suis là, voilà mon nom, ma clé publique, éventuellement mes coordonnées ». C’est comme cela que les contacts apparaissent sans que personne les saisisse à la main.

## La radio compagnon, la boîte branchée

Une radio compagnon MeshCore est l’appareil physique : un petit boîtier avec une antenne, parfois un écran, souvent alimenté par USB ou batterie. Il fait la partie radio, et rien d’autre. Il n’a ni clavier confortable ni écran utile pour lire une conversation.

Il attend donc un client : téléphone, ordinateur, ou serveur. Le client affiche les messages, choisit à qui écrire, règle les paramètres. La radio, elle, émet, écoute et relaie.

Sa mémoire est petite. Elle tient quelques centaines de contacts et une poignée de salons, pas plus. Passé ce seuil, il faut faire de la place.

## Meshloom, le serveur et l’interface

Meshloom est un serveur à installer sur une machine Linux, plus une interface web. Il se connecte à la radio par câble USB, par le réseau (TCP) ou par Bluetooth, et il expose tout dans un navigateur.

Deux conséquences pratiques :

- **Il continue d’écouter quand l’onglet est fermé.** Le serveur reste connecté à la radio et écrit chaque message et chaque paquet entendu dans une base de données. On rouvre la page le lendemain, l’historique est là.
- **Il garde plus que ce que la radio retient.** Les contacts et les salons qui ne tiennent pas dans la mémoire de la radio restent côté serveur, avec les paquets bruts. Une clé de salon ajoutée la semaine prochaine déchiffre le trafic de la semaine dernière, tant que les paquets sont encore stockés.

Meshloom ajoute aussi ce qu’une radio seule ne peut pas faire : une carte des nœuds entendus, un visualiseur des chemins réellement empruntés par les paquets, un flux des paquets bruts, et des sorties vers MQTT, Home Assistant, un webhook, Apprise ou une file SQS. Meshloom Community ajoute l’annuaire partagé — noms de nœuds pas encore entendus, radios qui ont entendu le même paquet, test d’émission — et le déchiffrement des salons publics pas encore connus, qui arrivent alors dans Canaux découverts. Les répéteurs peuvent être relevés à intervalle régulier, avec une alerte si une mesure sort des bornes.

## Ce que Meshloom n’est pas

Ce n’est pas un firmware : la radio garde le sien, Meshloom ne le remplace pas. Le serveur tourne sur votre machine. Une **installation neuve** rejoint [Meshloom Community](/docs/deep/community/) sauf opt-out : des paquets bruts entendus peuvent alors partir vers les hôtes Stats officiels. Les bases existantes restent telles quelles.

Et un point à connaître avant de commencer : **Meshloom prend le contrôle des contacts et des salons de la radio.** Il les charge, les décharge et les remplace selon ce qu’il juge utile. C’est ce qui permet de dépasser la mémoire de l’appareil. En revanche, c’est un mauvais choix si vous changez souvent de radio en comptant sur l’appareil pour garder ses propres favoris de son côté.

Enfin, l’origine : Meshloom part du client web MeshCore écrit par Jack Kingsman, repris un moment par Ian Langworth. Le copyright d’origine figure toujours dans la licence, qui est une licence MIT.

Reste à l’installer : [Installer](/docs/install/).
