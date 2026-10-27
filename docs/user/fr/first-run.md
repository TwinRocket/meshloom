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

## Créer votre compte

À la première ouverture de Meshloom, avant toute chose, une page demande de créer le compte administrateur. Choisissez un nom d’utilisateur et un mot de passe d’au moins 12 caractères (quelques mots à la suite en font un bon ; il doit différer du nom d’utilisateur et ne pas être un mot de passe courant), et si vous le souhaitez un nom affiché. Les boutons de langue sous le formulaire changent la langue de l’interface.

Si ce Meshloom contenait déjà des données avant d’exiger des comptes (une mise à jour), la page demande aussi le **code d’installation** : 8 chiffres que Meshloom écrit dans son journal à chaque démarrage tant qu’aucun compte n’existe. Un nouveau Meshloom ouvert pour la première fois depuis l’extérieur du réseau local (depuis une adresse publique, par un proxy que vous n’avez pas listé, ou avec une adresse publique déclarée) en demande un aussi : il l’écrit alors dans son journal. Depuis le réseau local, un nouveau Meshloom ne demande aucun code. Les espaces et les tirets du code sont sans importance. Pour le trouver :

- **Docker :** `docker logs <conteneur>` et cherchez « installation code ».
- **Installation par paquet :** `journalctl -u meshloom`.
- **Module Home Assistant :** l’onglet **Journal** du module.
- **Toute installation :** `meshloom-admin setup-code` sur l’hôte affiche un nouveau code.

Ensuite, Meshloom affiche une page de connexion à toute personne non connectée. Si une session se termine alors que la page est ouverte, une fenêtre redemande le mot de passe et vous reprenez où vous en étiez. Votre nom figure dans le bouton de compte (bas de la barre latérale, ou haut des **Réglages** sur téléphone), qui propose aussi **Se déconnecter**. En simple `http://`, votre mot de passe circule en clair sur le réseau, ce que la page de connexion signale : utilisez HTTPS ou un réseau de confiance.

## Langue de l’interface

L’interface s’ouvre en français par défaut. Pour passer à l’anglais, ouvrez **Réglages > Configuration locale** et changez **Langue**. Ce choix ne vaut que pour ce navigateur. Ce guide utilise les noms français des boutons et des menus.

## Connecter la radio

Meshloom ne sait pas encore comment joindre votre radio. Le lien (USB, réseau ou Bluetooth) se choisit dans l’interface, pas pendant l’installation. Tant qu’il n’est pas défini, la bannière **Aucune radio connectée** s’affiche et la barre d’état indique **Radio en pause**.

1. Appuyez sur **Connecter une radio** dans la bannière, ou sur **Connecter** dans la barre d’état. Cela ouvre **Réglages > Radio**.
2. Dans **Transport**, choisissez **Série** (câble USB), **TCP** (radio sur le réseau) ou **Bluetooth**.
3. Remplissez le champ qui apparaît : le port série (laissez **Détection automatique** si une seule radio est branchée), l’hôte et le port, ou l’adresse et le PIN Bluetooth. Sous Linux, Meshloom s’appaire avec la radio grâce à ce PIN.
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

Les deux fenêtres proposent aussi **Restaurer depuis une sauvegarde**. Choisissez-le après avoir reflashé une radio : une sauvegarde complète rend à la radio son ancienne identité, avec ses réglages, ses contacts et ses canaux (voir [Sauvegarder la radio](/docs/backup/)). La fenêtre nomme la radio connectée dont la nouvelle identité est remplacée et vous demande de confirmer. Si vous avez restauré une sauvegarde dans **Réglages → Base de données** avant que la radio soit connectée, cette fenêtre apparaît à la première connexion : Meshloom ne se lie jamais à l’identité de la sauvegarde avant qu’une radio ait montré qu’elle la détient, choisissez donc de nouveau **Restaurer depuis une sauvegarde**.

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
- **Fil** : tous les messages et le trafic de contrôle (requêtes, réponses, données de groupe) dans une seule chronologie. Le préréglage **Contrôle** (`#control`) n’affiche que le trafic de contrôle.
- **Live** : les paquets apparaissent sur une carte au fur et à mesure qu’ils sont entendus.
- **Visualiseur mesh** : les chemins réellement empruntés par les paquets.
- **Trace** : un test de route à travers des répéteurs choisis.
- **Localisation RF** : une zone de couverture estimée, pas un point GPS.
- **Recherche de messages** : recherche dans tout l’historique.
- **Canaux découverts** : les canaux hashtag que Meshloom a trouvés seul. Vous choisissez **Adopter** ou **Refuser** pour chacun.
- **Test radio** : envoie un message de test et montre qui l’a entendu. Il n’apparaît que si Meshloom Community est actif.

Le bouton **Ajouter canal/contact** crée une conversation : un contact à partir de sa clé publique, un salon privé à partir de sa clé, un salon hashtag à partir de son nom, ou plusieurs salons hashtag d’un coup.

[Meshloom Community](/docs/deep/community/) est facultatif et désactivé sur une nouvelle installation. Pour le rejoindre, saisissez le code de l’aéroport le plus proche (son code IATA, trois lettres) dans **Réglages > Meshloom Community** ; d’ici là, une bannière vous le propose (vous pouvez la masquer pour de bon) et rien n’est publié. Community a aussi besoin de la position de votre radio : tant qu’elle n’en a pas, une bannière vous la demande et seul l’état du nœud est envoyé. Vous pouvez quitter Community depuis cette page à tout moment.

Autres pages utiles dans **Réglages** : **Notifications** (ci-dessous), **Mises à jour**, **MQTT et automatisation** (sorties), **Alertes** (seuils de télémétrie), **Base de données** (taille et nettoyage du stockage), **Statistiques** et **À propos**.

**Réglages > Notifications** sert à choisir ce dont Meshloom vous prévient (nouveaux contacts, messages directs, nouveaux répéteurs ou capteurs, canaux trouvés, alertes télémétrie, mises à jour), et par quel moyen. Chaque moyen est un **canal** : Web Push sur ce navigateur, e-mail, un webhook, ntfy, Discord, Telegram, Gotify, Home Assistant ou tout service [Apprise](https://appriseit.com/). Web Push fonctionne aussi navigateur fermé, mais il exige HTTPS ; ntfy, Gotify ou Home Assistant sur votre réseau, non. Meshloom n’affiche aucune alerte contextuelle dans l’onglet ouvert. Voir [Notifications](/docs/deep/notifications/) et, pour les navigateurs, [Notifications push](/docs/deep/push/).

Pour envoyer votre premier message : [Messages](/docs/messages/).
