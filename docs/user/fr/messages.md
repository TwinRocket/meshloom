---
title: Messages
description: Messages directs, salons, salons hashtag — et ce qui se passe après l’envoi.
level: start
order: 4
---

Deux manières d’écrire sur le mesh, et elles ne se ressemblent pas beaucoup.

## Messages directs

Un message direct va à un seul contact. Il est chiffré pour lui : les nœuds qui le relaient au passage le transportent sans pouvoir le lire.

Pour écrire à quelqu’un, il faut sa **clé publique** — une suite de caractères hexadécimaux qui l’identifie sur le réseau. Le plus souvent, on n’a rien à saisir : une annonce entendue crée le contact toute seule et il apparaît dans la liste des conversations. Sinon, **Ajouter canal/contact** permet de coller une clé publique à la main.

Après l’envoi, le message porte des indications de suivi :

- Un **accusé de réception** signifie que le destinataire a confirmé l’avoir reçu. Sans accusé, le message est parti mais rien ne prouve qu’il est arrivé.
- Un message direct sans accusé est réessayé automatiquement, jusqu’à trois tentatives. Le dernier essai part en *flood*, c’est-à-dire diffusé de répéteur en répéteur au lieu de suivre une route apprise.
- Le **chemin** indique par quels sauts la réponse est revenue. Cliquer dessus montre le détail.

Un accusé peut prendre du temps : le message doit traverser les répéteurs à l’aller, et la confirmation doit revenir. Sur plusieurs sauts, quelques dizaines de secondes n’ont rien d’anormal.

Meshloom déchiffre les messages directs entrants côté serveur, avec la clé privée que la radio lui a communiquée au démarrage. Cela fonctionne donc même quand le contact n’est plus chargé dans la mémoire de la radio.

## Salons

Un salon — l’interface les appelle des **canaux** — est un espace partagé. Tout le monde qui possède la même **clé de salon** lit et écrit dedans. Il n’y a pas de liste de membres, pas d’invitation, pas de modération : la clé est l’accès.

Un salon **Public** existe dès le départ. C’est le salon par défaut de MeshCore, avec une clé connue de tous, donc à traiter comme une place publique.

Pour rejoindre un salon privé, il faut sa clé, transmise par quelqu’un qui l’a déjà. **Ajouter canal/contact**, onglet du salon, puis nom et clé.

Les messages de salon reviennent souvent en plusieurs exemplaires : chaque répéteur qui les relaie les fait repasser à portée de votre radio. Meshloom ne les affiche pas en double. Il les compte comme des **échos** à côté du message, et chaque écho ajoute son chemin. C’est une indication utile : beaucoup d’échos veut dire que le message est bien reparti dans le réseau.

Un message de salon qu’on vient d’envoyer garde un bouton de renvoi pendant trente secondes, pour le cas où il n’a manifestement atteint personne.

L’envoi est bloqué si le brouillon ferait déborder le paquet radio. Meshloom ne tronque plus un message trop long à l’envoi.

## Salons hashtag

Retenir une clé de trente-deux caractères hexadécimaux pour organiser une conversation est pénible. Les salons hashtag résolvent ça : la clé est **calculée à partir du nom**.

Concrètement, le nom du salon est haché — passé dans une fonction qui produit toujours la même clé pour la même entrée — et le `#` du début fait partie de ce qui est haché. Deux personnes qui tapent `#meteo` obtiennent la même clé et se retrouvent dans le même salon, sans jamais s’échanger quoi que ce soit. Il suffit de se mettre d’accord sur le nom.

Quelques conséquences à connaître :

- **Le nom est haché tel quel.** Une majuscule, un espace ou un accent en trop produisent une clé différente, donc un autre salon. Par défaut, Meshloom met le nom en minuscules et le limite aux lettres, chiffres et tirets, ce qui évite ce genre de faux jumeaux. Une option permet d’autoriser majuscules, espaces et caractères étendus, pour rejoindre un salon créé ailleurs avec un nom exotique.
- **Le nom est court par obligation.** Trente-deux octets au maximum, `#` compris. Les caractères accentués en consomment plus d’un.
- **Un nom devinable est un salon lisible par n’importe qui.** `#meteo` n’est pas un secret. Un salon hashtag sert à s’organiser, pas à se cacher.

L’ajout en lot existe aussi : coller plusieurs noms d’un coup pour créer les salons correspondants.

## Ajouter une clé après coup

Meshloom conserve les paquets bruts qu’il a entendus, y compris ceux qu’il n’a pas su déchiffrer. En ajoutant un salon, une option propose donc de **déchiffrer l’historique** : le serveur repasse sur les paquets stockés et récupère les messages que la nouvelle clé permet enfin de lire. Le trafic de la semaine dernière apparaît alors dans la conversation, tant que ces paquets n’ont pas encore été purgés.

Ensuite : [Autour des messages](/docs/around/).
