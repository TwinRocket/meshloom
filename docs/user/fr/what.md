---
title: Qu’est-ce que c’est
description: MeshCore, la radio compagnon, Meshloom : ce que fait chacun, sans le jargon.
level: start
order: 1
---

Trois choses différentes portent des noms proches. Autant les séparer tout de suite.

## MeshCore, le réseau

MeshCore est un réseau de messages qui circulent par radio, sans opérateur téléphonique, sans abonnement et sans Internet. Les appareils utilisent une radio longue portée et bas débit : quelques kilomètres en terrain dégagé, quelques centaines de caractères par message, pas de photos.

C’est un réseau **mesh** (maillé) : chaque appareil peut relayer ce qu’il entend. Un message quitte votre radio, un répéteur placé en hauteur le capte, un autre plus loin le transmet, et il arrive chez quelqu’un que votre radio ne peut pas joindre directement. Chaque appareil est un **nœud**, et chaque relais sur le chemin est un **saut**.

Il y a deux façons d’écrire à quelqu’un :

- Un **message direct**, chiffré pour un seul destinataire.
- Un **salon**, appelé **canal** dans Meshloom : un espace partagé où tous ceux qui ont la même **clé** (une chaîne secrète servant à chiffrer et déchiffrer) peuvent lire et écrire.

Les nœuds se signalent aussi de temps en temps par une **annonce** (advert) : un petit paquet qui dit « je suis là », avec un nom, une clé publique et parfois des coordonnées. C’est ainsi que des contacts apparaissent sans que personne les saisisse.

## La radio compagnon, la boîte sur le bureau

Une radio compagnon MeshCore est l’appareil physique : une petite boîte avec une antenne, parfois un écran, le plus souvent alimentée en USB ou par batterie. Elle fait le travail radio et presque rien d’autre. Elle n’a ni clavier confortable, ni écran suffisant pour lire une conversation.

Il lui faut donc un client : un téléphone, un ordinateur ou un serveur. Le client affiche les messages, choisit à qui écrire et modifie les réglages. La radio émet, écoute et relaie.

Sa mémoire est petite. Elle retient un nombre limité de contacts et de canaux, et quand elle est pleine, il faut bien faire de la place.

## Meshloom, le serveur et son interface

Meshloom est un serveur installé sur une machine Linux, plus une interface web. Il se connecte à la radio en USB, par le réseau (TCP) ou en Bluetooth, et montre tout dans un navigateur.

Deux conséquences pratiques :

- **Il continue d’écouter quand vous fermez l’onglet.** Le serveur reste connecté à la radio et enregistre dans une base de données chaque message et chaque paquet qu’il entend. Ouvrez la page demain, l’historique est là.
- **Il retient plus que la radio.** Les contacts et les canaux qui ne tiennent pas dans la radio restent sur le serveur, avec les paquets bruts. Si vous ajoutez la clé d’un canal la semaine prochaine, le trafic de la semaine dernière peut être déchiffré, tant que ces paquets sont encore stockés.

Meshloom ajoute aussi ce qu’une radio seule ne peut pas faire : une carte des nœuds entendus, un visualiseur des chemins réellement empruntés par les paquets, un flux de paquets bruts, une recherche dans tous vos messages, et des sorties vers MQTT, Home Assistant, un webhook, Apprise (Discord, Telegram, e-mail et d’autres) ou une file SQS. Les répéteurs peuvent être interrogés à intervalle régulier, avec une alerte quand une mesure sort des limites que vous fixez. Les notifications peuvent vous parvenir par Web Push, e-mail ou webhook. Meshloom peut aussi partager votre radio sur le réseau, pour qu’une application mobile l’utilise à travers lui.

**Meshloom Community** est un annuaire partagé facultatif. Il donne des noms aux nœuds que vous n’avez pas encore entendus, montre quelles autres radios ont entendu le même paquet, et propose un test de transmission. Il aide aussi à déchiffrer des canaux hashtag publics que vous ne connaissez pas encore ; ils apparaissent alors dans **Canaux découverts**. Meshloom embarque déjà une liste de noms de canaux publics courants et les essaie seul, même sans Community.

## Ce que Meshloom n’est pas

Ce n’est pas un firmware : la radio garde le sien, et Meshloom ne le remplace pas. Le serveur tourne sur votre machine. Une **nouvelle installation** rejoint [Meshloom Community](/docs/deep/community/) sauf si vous refusez, mais rien n’est publié tant que vous n’avez pas saisi le code de votre aéroport le plus proche. Ensuite, il peut publier les paquets bruts qu’il entend vers les serveurs Stats officiels. Les bases existantes restent comme elles étaient.

Une chose à savoir avant de commencer : **Meshloom prend le contrôle des contacts et des canaux de la radio.** Il les charge, les décharge et les remplace selon ce qu’il juge utile. C’est ainsi qu’il contourne la limite de mémoire de l’appareil. Ce n’est pas adapté si vous changez souvent de radio et attendez que chaque appareil garde ses propres favoris.

Quant à son origine : Meshloom est parti du client web MeshCore écrit par Jack Kingsman, que Ian Langworth a prolongé un temps. Le copyright d’origine demeure dans la licence MIT.

Suite : [Installer](/docs/install/).
