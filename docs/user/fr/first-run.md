---
title: Premier lancement
description: Ouvrir l’interface, connecter la radio, nommer le nœud, faire le tour de l’écran.
level: start
order: 3
---

Le serveur tourne. Reste à ouvrir la page, connecter la radio, puis regarder ce qui s’y passe.

```
http://127.0.0.1:8000
```

Depuis un autre appareil du même réseau, utilisez l’adresse IP de la machine avec le port `8000`.

## Langue de l’interface

L’interface s’ouvre en français par défaut. Pour passer à l’anglais, ouvrez **Réglages > Configuration locale** et changez **Langue**. Ce choix ne vaut que pour ce navigateur. Ce guide utilise les noms français des boutons et des menus.

## Connecter la radio

Meshloom ne sait pas encore comment joindre votre radio. Le lien (USB, réseau ou Bluetooth) se choisit dans l’interface, pas pendant l’installation. Tant qu’il n’est pas défini, la bannière **Aucune radio connectée** s’affiche et la barre d’état indique **Radio en pause**.

1. Appuyez sur **Connecter une radio** dans la bannière, ou sur **Connecter** dans la barre d’état. Cela ouvre **Réglages > Radio**.
2. Dans **Transport**, choisissez **Série** (câble USB), **TCP** (radio sur le réseau) ou **Bluetooth**.
3. Remplissez le champ qui apparaît : le port série (laissez **Détection automatique** si une seule radio est branchée), l’hôte et le port, ou l’adresse et le PIN Bluetooth.
4. Appuyez sur **Enregistrer et connecter**.

[Transports radio](/docs/deep/transports/) détaille chaque champ.

La barre d’état passe ensuite par **Radio en connexion**, **Radio en initialisation**, puis **Radio OK**. **Radio OK** veut dire que le lien est établi et que tout est synchronisé. La première connexion prend un moment : Meshloom lit la configuration de la radio, récupère ses contacts et ses canaux, et règle son horloge.

Si la barre indique **Radio déconnectée**, un bouton **Reconnecter** apparaît. Meshloom réessaie aussi tout seul toutes les quelques secondes : rebrancher le câble ou rallumer la radio suffit souvent. Si rien ne change, les causes habituelles sont un mauvais port série, une mauvaise adresse IP ou un mauvais PIN Bluetooth. Voir [Dépannage](/docs/deep/troubleshooting/).

Cliquez sur l’état de la radio dans la barre pour voir un résumé de la connexion.

## Première vérification d’identité

Meshloom lie sa base à la clé publique de votre radio, pour ne jamais mélanger l’historique de deux radios. Sur une installation neuve, cela se fait sans bruit. Après la mise à jour d’une base qui contient déjà des données, une fenêtre demande de confirmer.

- Choisissez **Lier sans effacer** s’il s’agit de la même radio. Contacts et messages restent.
- Choisissez **Nouvelle radio** s’il s’agit d’un autre appareil. Les contacts et messages du mesh, les paquets stockés et l’historique de télémétrie sont effacés. Les canaux et les réglages restent.

**Clé précédente : Inconnue** signifie seulement que la base est antérieure à cette vérification, pas que la radio a changé. Si la clé affichée est la vôtre, choisissez **Lier sans effacer**.

Si une radio avec une autre clé est connectée, la fenêtre montre les deux clés et propose **Annuler** ou **Effacer et continuer**. Garder l’historique n’est pas proposé, car cet historique appartient à l’autre radio.

Une fois connectée, la barre d’état affiche aussi le nom du nœud, sa clé publique (cliquez dessus pour la copier) et le niveau de batterie quand la radio le communique.

## Nommer votre nœud

Un nœud sans nom apparaît chez les autres sous les premiers caractères de sa clé publique. Donnez un nom au vôtre.

1. Ouvrez **Réglages > Radio**.
2. Dans le groupe **Identité**, renseignez **Nom de la radio**.
3. Enregistrez.

Ce nom part avec chaque annonce et c’est lui que les autres voient dans leur liste de contacts. Restez bref : sur un canal, votre nom part avec chaque message et occupe une partie de ses 156 octets.

La même page contient les paramètres radio (préréglage, fréquence, bande passante, etc.). Ils doivent correspondre à ceux des nœuds autour de vous, sinon personne n’entend personne. Si votre nœud reste muet alors que d’autres, tout près, sont actifs, regardez-les en premier, et notez les valeurs actuelles avant de rien changer.

## Vous annoncer

Une annonce (advert) est un petit paquet qui dit « je suis là », avec le nom et la clé publique du nœud. Dans **Réglages > Radio**, le groupe **Annonces et découverte** la contrôle.

- **Envoyer une annonce flood** passe par les répéteurs et va loin.
- **Envoyer une annonce zéro saut** reste locale et consomme bien moins de temps d’antenne.
- **Intervalle d’annonce périodique** envoie des annonces automatiquement. `0` désactive. Le minimum est d’1 heure, et 24 heures ou plus est recommandé : trop d’annonces encombrent les ondes pour tout le monde.

Le panneau de l’état radio (cliquez sur l’état dans la barre) propose aussi des boutons rapides **Flood** et **Zéro saut**.

L’inverse fonctionne aussi : vous n’avez pas à saisir les contacts à la main. Chaque annonce que Meshloom entend crée ou met à jour un contact. Une liste vide le premier jour signifie seulement que le réseau n’a pas encore été entendu. Vous pouvez ignorer certains types de nœuds dans **Réglages > Gestion radio-application**, avec **Bloquer la découverte de nouveaux types de nœuds** (clients, répéteurs, serveurs de salon, capteurs).

## Faire le tour

Sur téléphone, la barre du bas propose quatre entrées : **Discussions**, **Carte**, **Outils** et **Réglages**. Sur grand écran, une barre d’icônes à gauche montre les discussions, la carte et chaque outil, avec **Réglages** tout en bas. Vous choisissez les outils qui restent sur cette barre, et leur ordre, dans **Réglages > Navigation**.

La liste des conversations est découpée en sections : **Favoris**, **Canaux**, **Contacts**, **Répéteurs** et **Serveurs de salon**. Un canal **Public** existe dès le départ. C’est le canal par défaut de MeshCore, ouvert à tout le monde.

L’écran **Outils** (ou la barre d’icônes) ouvre :

- **Flux de paquets** : tous les paquets que la radio entend, qu’ils soient déchiffrables ou non.
- **Journal de contrôle** : requêtes, réponses et données de groupe, à part des conversations.
- **Live** : les paquets apparaissent sur une carte au fur et à mesure qu’ils sont entendus.
- **Visualiseur mesh** : les chemins réellement empruntés par les paquets.
- **Trace** : un test de route à travers des répéteurs choisis.
- **Localisation RF** : une zone de couverture estimée, pas un point GPS.
- **Recherche de messages** : recherche dans tout l’historique.
- **Canaux découverts** : les canaux hashtag que Meshloom a trouvés seul. Vous choisissez **Adopter** ou **Refuser** pour chacun.
- **Test radio** : envoie un message de test et montre qui l’a entendu. Il n’apparaît que si Meshloom Community est actif.

Le bouton **Ajouter canal/contact** crée une conversation : un contact à partir de sa clé publique, un salon privé à partir de sa clé, un salon hashtag à partir de son nom, ou plusieurs salons hashtag d’un coup.

Les nouvelles installations rejoignent [Meshloom Community](/docs/deep/community/). Tant que vous n’avez pas saisi le code de l’aéroport le plus proche (son code IATA, trois lettres) dans **Réglages > Meshloom Community**, une bannière vous le rappelle et rien n’est publié. Vous pouvez aussi quitter Community depuis cette page.

Autres pages utiles dans **Réglages** : **Notifications** (ci-dessous), **Mises à jour**, **MQTT et automatisation** (sorties et bots), **Alertes** (seuils de télémétrie), **Base de données** (taille et nettoyage du stockage), **Statistiques** et **À propos**.

**Réglages > Notifications** sert à choisir ce dont Meshloom vous prévient (nouveaux contacts, messages directs, nouveaux répéteurs ou capteurs, canaux trouvés, alertes télémétrie, mises à jour), et par quel moyen : Web Push sur ce navigateur, e-mail ou webhook. Le push fonctionne aussi navigateur fermé, mais il exige HTTPS. Meshloom n’affiche aucune alerte contextuelle dans l’onglet ouvert. Voir [Notifications push](/docs/deep/push/).

Pour envoyer votre premier message : [Messages](/docs/messages/).
