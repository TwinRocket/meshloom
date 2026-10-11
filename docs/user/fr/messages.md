---
title: Messages
description: Messages directs, canaux, canaux hashtag, et ce qui se passe après l’envoi.
level: start
order: 4
---

Il y a deux façons d’écrire sur le mesh, et elles se comportent très différemment.

## Messages directs

Un message direct va à un seul contact. Il est chiffré pour cette personne : les nœuds qui le relaient peuvent le transporter, mais pas le lire.

Il faut la **clé publique** du contact, une longue chaîne de caractères hexadécimaux (64) qui identifie un nœud sur le réseau. En général, vous n’avez rien à saisir : quand Meshloom entend l’annonce d’un nœud, le contact est créé et apparaît dans la liste des conversations. Sinon, **Ajouter canal/contact** permet de coller une clé publique à la main (onglet **Contact**).

Après l’envoi, le message montre l’avancement de sa livraison :

- Un **accusé de réception** signifie que le destinataire a confirmé avoir reçu le message. Sans accusé, le message est parti, mais rien ne prouve qu’il est arrivé.
- Un message direct sans accusé est renvoyé automatiquement, jusqu’à trois tentatives en tout. Avant la dernière, Meshloom oublie la route qu’il avait apprise : le message part alors en *flood*, c’est-à-dire qu’il se propage de répéteur en répéteur jusqu’à trouver le destinataire.
- Cliquez sur le nombre de sauts ou sur le chemin à côté d’un message pour voir par quels répéteurs il est passé.

Un accusé prend du temps. Le message doit traverser tous les répéteurs à l’aller, et la confirmation doit revenir. Sur plusieurs sauts, quelques dizaines de secondes sont normales.

Meshloom déchiffre les messages directs entrants sur le serveur, avec la clé privée que la radio lui donne au démarrage. Cela marche même quand le contact n’est plus chargé dans la mémoire de la radio.

## Canaux

Un canal est un espace partagé (MeshCore parle de salon ou de groupe, Meshloom dit **canal**). Tous ceux qui ont la même **clé de canal** peuvent y lire et y écrire. Il n’y a ni liste de membres, ni invitation, ni modération : la clé est l’accès.

Un canal **Public** existe dès le départ. C’est le canal par défaut de MeshCore, dont la clé est connue de tous : traitez-le comme une place publique.

Pour rejoindre un canal privé, demandez sa clé à quelqu’un qui l’a déjà. Ouvrez **Ajouter canal/contact**, choisissez l’onglet **Salon privé**, puis saisissez le nom et la clé. Le bouton **Générer une clé aléatoire** crée une nouvelle clé si vous lancez votre propre canal.

Les messages d’un canal reviennent souvent plusieurs fois : chaque répéteur qui en relaie un peut le ramener à portée de votre radio. Meshloom n’affiche pas ces copies comme des doublons. Il les compte comme des **échos** à côté du message et enregistre chaque route. C’est utile : beaucoup d’échos signifient que votre message a bien voyagé.

Juste après l’envoi d’un message de canal, un bouton **Renvoyer** reste disponible pendant 30 secondes, au cas où il n’aurait visiblement atteint personne. Renvoyer répète exactement le même message : seuls les répéteurs qui ne l’ont pas encore vu le relaient. **Renvoyer comme nouveau** l’envoie comme un nouveau message, que les destinataires peuvent voir en double. Si vous préférez ne pas le faire à la main, **Renvoyer automatiquement les messages de canal non entendus** dans **Réglages > Radio** (désactivé par défaut) renvoie une fois un message de canal si aucun écho n’est revenu au bout de 2 secondes.

L’envoi est bloqué quand le texte est trop long pour un paquet radio, et le champ de saisie indique « trop long ». Meshloom ne coupe pas un message à votre place.

## Répondre, réagir et partager

Dans une conversation, vous pouvez répondre à un message, y réagir avec un emoji, le copier et ouvrir ses détails. Le champ de saisie permet aussi d’insérer un emoji, un GIF (la recherche de GIF demande une clé Giphy dans **Réglages > Configuration locale** ; coller un lien marche sans clé) et la position de votre radio sous forme de repère. **Supprimer** retire un message de ce Meshloom seulement. Les autres nœuds gardent leur copie.

## Canaux hashtag

Retenir et saisir une clé de 32 caractères pour chaque conversation est pénible. Les canaux hashtag s’en passent : la clé est **calculée à partir du nom**.

Le nom, avec son `#` initial, passe dans une fonction de hachage (SHA-256, une recette qui donne toujours le même résultat pour la même entrée), et les 16 premiers octets du résultat forment la clé. Deux personnes qui tapent `#meteo` obtiennent la même clé et se retrouvent dans le même canal sans rien échanger. Elles doivent seulement s’accorder sur le nom.

Quelques conséquences :

- **Le nom est haché exactement tel qu’écrit.** Une majuscule, un espace ou un accent en trop donne une autre clé, donc un autre canal. Par défaut, Meshloom évite ces fautes, comme l’application officielle MeshCore : il retire les espaces aux deux bouts, met le nom en minuscules, et n’accepte que lettres, chiffres et tirets. L’option **Autoriser majuscules, espaces et caractères étendus** permet de rejoindre un canal créé ailleurs avec un nom inhabituel : le nom est alors haché tel quel, sauf les espaces aux deux extrémités. Toutes les applications ne retirent pas ces espaces extérieurs, évitez-les dans un nom de canal.
- **Le nom doit être court.** 30 octets au plus, `#` compris, par défaut (comme l’application officielle), et 32 avec l’option (la limite de la radio). Un caractère accentué peut occuper plusieurs octets.
- **Un nom facile à deviner est un canal ouvert.** N’importe qui peut taper `#meteo`. Un canal hashtag organise les conversations, il ne les cache pas.

Vous pouvez aussi en ajouter plusieurs d’un coup : l’onglet **Ajout groupé** accepte une liste de noms, séparés par des retours à la ligne, des espaces ou des virgules. Meshloom trouve aussi certains canaux hashtag tout seul : ils attendent dans **Canaux découverts**, où vous les gardez ou les écartez.

## Ajouter une clé plus tard

Meshloom conserve les paquets bruts qu’il entend, y compris ceux qu’il n’a pas pu déchiffrer. Quand vous ajoutez un canal, vous pouvez donc lui demander d’essayer de déchiffrer l’historique : l’option **Tenter de déchiffrer N paquets stockés** fait parcourir au serveur les paquets conservés et retrouver ceux que la nouvelle clé sait lire. La conversation de la semaine dernière apparaît alors, tant que ces paquets n’ont pas été purgés (voir **Réglages > Base de données**).

Ensuite : [Autour des messages](/docs/around/).
