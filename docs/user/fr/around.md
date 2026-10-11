---
title: Autour des messages
description: La carte, le visualiseur, le flux de paquets — ce qu’on regarde sans rien envoyer.
level: start
order: 5
---

Une radio entend beaucoup plus que ce qui vous est adressé. Annonces (un nœud qui signale sa présence), échos, paquets d’autres salons, paquets illisibles : tout passe à portée d’antenne. Meshloom garde cette matière et propose quelques manières de la regarder.

Rien de ce qui suit n’est nécessaire pour envoyer un message. C’est de l’observation. On peut s’en passer entièrement.

Sur un téléphone, on ouvre ces outils depuis **Outils**, dans la barre du bas. Chacun est accompagné d’une courte description. Sur un grand écran, ils sont sur la barre d’icônes à gauche, que l’on peut réordonner ou alléger dans **Réglages → Navigation**. La carte des nœuds est une destination à part, à côté des conversations.

## La carte des nœuds

La carte s’ouvre par **Carte**, dans la barre du bas ou sur la barre de gauche. Une annonce peut transporter des coordonnées. Quand elle en contient, Meshloom pose le nœud sur la carte. Une annonce entendue, un point de plus.

Deux limites à garder en tête :

- Tous les nœuds ne diffusent pas leur position. Ceux qui la gardent pour eux ne sont nulle part sur la carte, même s’ils sont très actifs.
- Une position est celle de la dernière annonce reçue, pas un suivi en direct. Un nœud mobile est affiché là où il se trouvait la dernière fois qu’il a parlé.

Quelques options aident à lire la carte :

- Le filtre **Depuis** n’affiche que les nœuds entendus après un moment donné : dernière heure, 24 heures, 3 jours, 7 jours, une date de votre choix (**Perso**), ou **Tous**. Par défaut, la carte montre les 7 derniers jours : un nœud silencieux depuis plus longtemps n’apparaît qu’après avoir choisi **Tous**. Le navigateur retient votre choix.
- **Relais internet** est une case à cocher qui ajoute les relais connus de Meshloom Community. Elle n’apparaît que si Community est activé.
- Le fond de carte peut être clair, sombre, topographique ou satellite.

La carte sert surtout à comprendre la géographie du réseau local : où sont les répéteurs, dans quelle direction les messages partent, quelle colline explique qu’un nœud proche soit inaudible.

## Le visualiseur mesh

La carte montre où sont les nœuds. Le visualiseur montre par où passent les paquets.

Chaque paquet reçu porte la trace des répéteurs (les sauts) qu’il a traversés. Tant que le visualiseur est ouvert, Meshloom accumule ces traces et dessine le réseau en 3D tel qu’il fonctionne réellement, et pas tel qu’une carte le suggère. Les liens les plus empruntés se voient tout de suite.

L’intérêt est concret : cela finit par dire quel répéteur porte votre trafic. Utile quand ce répéteur tombe et qu’on cherche pourquoi plus rien ne sort.

Les identités ne sont pas toujours certaines. Un saut n’est identifié que par un petit fragment de clé (souvent un seul octet), et deux nœuds peuvent partager le même fragment. Le visualiseur affiche alors une hypothèse, pas une certitude, et signale ces nœuds comme ambigus.

## Le flux de paquets bruts

Le **Flux de paquets** affiche tout ce que la radio entend, au fil de l’eau, sans filtre : les messages destinés à vos salons, ceux des salons dont vous n’avez pas la clé, les annonces, les accusés de réception, et les paquets abîmés en route.

C’est un outil d’observation, pas une source d’information fiable. Un aquarium : intéressant à regarder, pratique pour copier un paquet précis ou vérifier que la radio entend bien quelque chose, et sans conséquence si on ne l’ouvre jamais.

- La liste suit le dernier paquet. Décochez **Défilement auto** pour la mettre en pause et lire un paquet plus ancien.
- Cliquer sur un paquet en ouvre le détail.
- **Afficher les stats** ouvre un panneau qui résume ce qui a été entendu sur une fenêtre de temps (1, 5, 10 ou 30 minutes, ou toute la session) : paquets par minute, types de paquets, force du signal, voisins les plus entendus.
- **Analyser un paquet** permet de coller le texte hexadécimal d’un paquet pour l’inspecter.

Les paquets qu’on n’a pas su déchiffrer ne sont pas perdus pour autant. Ce sont eux qui permettent de déchiffrer d’anciens messages plus tard, quand vous obtenez la clé d’un salon.

## Le reste

- **Trace** envoie un paquet de test qui traverse les répéteurs que vous choisissez et revient à votre radio. Cela mesure une route au lieu de la deviner. Il faut pouvoir entendre le dernier répéteur de la chaîne pour que la trace réussisse.
- **Recherche de messages** cherche dans tout l’historique stocké, messages directs et salons confondus. Un résultat cliqué ouvre la conversation à cet endroit précis, avec les messages autour. On peut affiner avec `user:` ou `channel:` suivi d’un nom ou d’une clé. Les noms qui contiennent des espaces se mettent entre guillemets.
- Les **Statistiques**, dans les réglages, résument ce que le nœud a vu : contacts, répéteurs, canaux et messages, paquets par heure sur 72 heures, canaux les plus actifs sur 24 heures, niveau de bruit, et part du trafic qui utilise des portées régionales.

## Le flux live

**Live** dessine les paquets sur une carte au moment où ils sont entendus. Il montre les paquets entendus par les observateurs de Meshloom Community comme ceux de votre propre radio : on voit donc de l’activité bien au-delà de sa portée. On peut filtrer par code de région, masquer des types de paquets, ne garder que les traces dont la route est certaine, et choisir un thème sonore.

Les paquets de votre propre radio s’affichent toujours. Ceux des observateurs de Community demandent que Meshloom Community soit activé et qu’un code d’aéroport (IATA) soit enregistré ; sinon, un bandeau explique ce qui manque. Si Community refuse la connexion parce que l’horloge du serveur est fausse, le bandeau le dit : remettez le serveur à l’heure, puis choisissez **Réessayer**.

## Le journal de contrôle

Le **journal de contrôle** rassemble le trafic qui n’est pas une conversation : requêtes, réponses, requêtes anonymes (comme les connexions) et données de groupe. Les paquets sont regroupés par nœud concerné. Quand le contenu reste chiffré, on voit l’enveloppe, pas le contenu.

## La localisation RF

**Localisation RF** trace une zone de couverture prudente pour un nœud, à partir des radios qui l’ont entendu directement (à zéro saut). On saisit un nom, une clé entière ou le début d’une clé. Si plusieurs nœuds correspondent, on en choisit un. Ce n’est pas un point GPS, et rien n’est écrit sur le contact.

Elle part de ce que votre propre radio a entendu. Avec Meshloom Community activé, elle utilise aussi les observateurs de la communauté.

## Les canaux découverts

Meshloom essaie de reconnaître les salons hashtag qu’il entend sans en avoir la clé. Il essaie d’abord une liste intégrée de noms connus. Avec Community activé et un code d’aéroport enregistré, il demande aussi à Community des noms qui pourraient correspondre. Quand il en trouve un, il envoie une notification « Canaux trouvés » (voir [Notifications push](/docs/deep/push/)).

Un salon trouvé ainsi n’entre pas tout seul dans vos discussions. Il apparaît dans **Canaux découverts**, où **Adopter** l’ajoute à vos discussions et **Refuser** l’écarte. Les canaux refusés sont conservés dans une liste, ce qui permet de revenir sur sa décision.

Depuis cette même page, **Afficher le chercheur de canaux** ouvre le chercheur de canaux. Il essaie des noms sur les paquets illisibles : un dictionnaire de noms connus, puis des paires de mots, puis toutes les combinaisons jusqu’à une longueur que vous fixez. Cette dernière étape utilise la carte graphique de votre ordinateur (WebGPU) : il faut donc Chrome ou Edge 113 ou plus récent, et une page servie en HTTPS ou depuis `localhost` (voir [HTTPS](/docs/deep/https/)). Le chercheur fonctionne aussi avec Community coupé.

## Le test radio

**Test radio** envoie un message de test sur le réseau et liste les radios de Community qui l’ont entendu : combien, à quelle distance, avec combien de sauts. Il n’est disponible que lorsque Meshloom Community est actif. Le message part dans l’une des régions enregistrées sur votre radio : il faut donc en avoir enregistré au moins une dans les réglages radio.

## Ça prend de la place

Garder les paquets bruts a un coût. Ils s’accumulent, la base grossit. La section **Base de données** des réglages affiche sa taille et l’ancienneté du plus vieux paquet illisible. Elle propose deux nettoyages :

- **Supprimer les paquets non déchiffrés** retire les paquets illisibles plus vieux qu’un nombre de jours que vous choisissez. Les supprimer ferme la porte au déchiffrement de cette période : les messages déjà déchiffrés restent, les autres paquets disparaissent définitivement.
- **Purger les paquets d’archive** retire les paquets bruts qui se cachent derrière des messages déjà déchiffrés. On gagne de la place sans toucher aux messages ni au déchiffrement futur, mais on ne peut plus inspecter ces paquets.

Sur une machine qui n’a pas de contrainte d’espace, laisser courir quelques semaines ne pose pas de problème.

Dernier point avant de laisser tourner : [Un réseau de confiance](/docs/trust/).
