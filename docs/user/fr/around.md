---
title: Autour des messages
description: La carte, le visualiseur, le flux de paquets — ce qu’on regarde sans rien envoyer.
level: start
order: 5
---

Une radio entend beaucoup plus que ce qui vous est adressé. Annonces, échos, paquets d’autres salons, paquets illisibles : tout passe à portée d’antenne. Meshloom garde cette matière et propose quelques manières de la regarder.

Rien de ce qui suit n’est nécessaire pour envoyer un message. C’est de l’observation. On peut s’en passer entièrement.

Sur un téléphone, on les ouvre depuis **Outils**. Sur un grand écran, elles sont sur le rail d’icônes. La carte des nœuds est une destination à part, à côté des conversations.

## La carte des nœuds

La carte s’ouvre par **Carte**, dans la barre du bas ou sur le rail. Chaque annonce peut transporter des coordonnées. Quand elle en contient, Meshloom pose le nœud sur une carte. Une annonce entendue, un point de plus.

Deux limites à garder en tête. Les nœuds ne diffusent pas tous leur position : ceux qui la gardent pour eux ne sont nulle part sur la carte, même s’ils sont très actifs. Et une position est celle de la dernière annonce reçue, pas une position en direct — un nœud mobile est affiché là où il se trouvait la dernière fois qu’il a parlé.

La carte sert surtout à comprendre la géographie du réseau local : où sont les répéteurs, dans quelle direction les messages partent, quelle colline explique qu’un nœud proche soit inaudible.

## Le visualiseur mesh

La carte montre où sont les nœuds. Le visualiseur montre par où passent les paquets.

Chaque paquet reçu porte la trace des sauts qu’il a traversés. En accumulant ces traces, Meshloom dessine le réseau tel qu’il fonctionne réellement, et pas tel qu’une carte le suggère. Les liens les plus empruntés se voient tout de suite.

L’intérêt est concret : cela finit par dire quel répéteur porte votre trafic. Utile quand ce répéteur tombe et qu’on cherche pourquoi plus rien ne sort.

Les identités ne sont pas toujours certaines. Un saut n’est identifié que par un fragment de clé, et deux nœuds peuvent partager le même fragment. Le visualiseur affiche alors une hypothèse, pas une certitude.

## Le flux de paquets bruts

Le flux affiche tout ce que la radio entend, au fil de l’eau, sans filtre : les messages destinés à vos salons, ceux des salons dont vous n’avez pas la clé, les annonces, les accusés de réception, et les paquets abîmés en route.

C’est un outil d’observation, pas une source d’information fiable. Un aquarium : intéressant à regarder, pratique pour copier un paquet précis ou vérifier que la radio entend bien quelque chose, et sans conséquence si on ne l’ouvre jamais.

Le flux défile en continu et se cale sur le dernier paquet. Une case à cocher permet de mettre ce défilement en pause, le temps de remonter à un paquet plus ancien. Cliquer sur un paquet en ouvre le détail. Un panneau de statistiques résume ce que la session a entendu : volumes, types de paquets, nœuds les plus bavards.

Les paquets qu’on n’a pas su déchiffrer ne sont pas perdus pour autant, et c’est ce qui permet de déchiffrer l’historique plus tard, quand une clé de salon arrive.

## Le reste

- **Trace de route** envoie un paquet de test qui traverse des répéteurs connus et revient à votre radio. Cela mesure une route plutôt que de la déduire.
- **Recherche de messages** cherche dans tout l’historique stocké, messages directs et salons confondus. Un résultat cliqué ouvre la conversation à cet endroit précis, avec le contexte autour.
- Les **Statistiques**, dans les réglages, agrègent ce que le nœud a vu : volumes, activité par période, salons les plus actifs.
- Le **chercheur de salons** essaie des noms hashtag sur les GroupText non déchiffrés — ceux de la session et un échantillon déjà stocké. Les noms Community et une liste MeshCore embarquée sont essayés quand ils sont disponibles. Il fonctionne aussi avec Community coupé.

## Le flux live

**Live** pose les paquets sur une carte au moment où la radio les entend. On y voit l’activité autour de soi, pas seulement les nœuds déjà placés par une annonce. Meshloom Community et un code IATA sont nécessaires pour l’ouvrir.

## Le journal de contrôle

Le **journal de contrôle** rassemble le trafic qui n’est pas une conversation : requêtes, réponses, requêtes anonymes et données de groupe. Quand la charge reste chiffrée, on voit l’enveloppe, pas le contenu.

## La localisation RF

**Localisation RF** trace une zone de couverture à zéro saut, à partir des radios qui ont entendu le nœud tout près. Ce n’est pas un point GPS, et rien n’est écrit sur le contact.

## Les canaux découverts

Un salon hashtag entendu, ou déchiffré grâce à un nom de salon public connu de la communauté, n’entre pas tout seul dans les discussions. Il apparaît dans **Canaux découverts**. On le garde ou on le refuse.

## Le test radio

**Test radio** envoie un message qui se propage sur le réseau et liste les radios de la communauté qui l’ont entendu. Il n’est disponible que lorsque Meshloom Community est actif.

## Ça prend de la place

Garder les paquets bruts a un coût. Ils s’accumulent, la base grossit. La section **Base de données** des réglages affiche sa taille et permet de purger les paquets anciens.

Purger ferme la porte au déchiffrement de l’historique pour la période supprimée : les messages déjà déchiffrés restent, les paquets illisibles disparaissent définitivement. Sur une machine qui n’a pas de contrainte d’espace, laisser courir quelques semaines ne pose pas de problème.

Dernier point avant de laisser tourner : [Un réseau de confiance](/docs/trust/).
