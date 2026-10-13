---
title: Meshloom Community
description: Rejoindre, associer un code IATA, publier les paquets entendus, et partager les noms hashtag.
level: deep
order: 17
---

Meshloom Community est un réseau d’observateurs optionnel. Quand il est activé, votre serveur peut :

- publier les **paquets bruts** que votre radio entend vers les serveurs Meshloom Stats officiels ;
- utiliser l’**annuaire communautaire**, qui donne des noms aux sauts, alimente la localisation RF et les compteurs « entendu par », ainsi que la page Live et le test radio ;
- partager et chercher les noms de **salons hashtag** dans une liste globale unique.

Ce n’est pas une intégration à créer dans **Réglages → MQTT et automatisation**. On rejoint et on quitte depuis **Réglages → Meshloom Community**.

## Rejoindre

1. Ouvrez **Réglages → Meshloom Community**.
2. Saisissez le **code IATA** de l’aéroport le plus proche : trois lettres, par exemple `CDG`. Si vous ne le connaissez pas, cherchez-le avec **Voir les codes IATA des aéroports** : la recherche d’aéroport (**Trouver un aéroport**) fait partie de Community, elle n’apparaît donc qu’une fois inscrit, pour changer de code.
3. Cliquez sur **Rejoindre Meshloom Community**.

Rejoindre est le seul moyen d’activer Community depuis les réglages : il n’y a pas d’interrupteur, et **Quitter Meshloom Community** (affiché une fois rejoint) ne fait que le désactiver. Meshloom enregistre d’abord le code auprès de Community, et alors seulement active Community et commence à publier. Si Community refuse le code (par exemple parce que cette radio a déjà changé de code trop souvent ces 7 derniers jours), Community reste désactivé, votre code enregistré ne change pas, et les réglages disent pourquoi.

Ce code est une étiquette grossière de votre région, pas une localisation précise. Meshloom le compare à la position de votre radio et vous dit si les deux concordent. Si vous êtes sûr de votre choix malgré une différence, **Je suis sûr de moi** le confirme. Community limite la fréquence à laquelle le code peut être modifié. Community efface cette confirmation quand la position de votre radio s’éloigne de plus de 100 km (par défaut : réglage `IATA_GPS_HYSTERESIS_KM` côté Community) de celle qu’il a enregistrée : comme Meshloom renvoie désormais la position de lui-même (voir plus bas), cela peut arriver sans aucune action dans les réglages, et **Je suis sûr de moi** réapparaît si le code et la position ne concordent toujours pas.

Ce code est aussi la région que la page Live affiche par défaut ; on peut y ajouter d’autres régions.

Tant qu’aucun code n’est enregistré, une bannière reste affichée. La publication des paquets, la page Live, la publication des noms hashtag et les recherches de canaux attendent ce code. L’annuaire (noms de sauts, localisation RF, « entendu par ») n’en a pas besoin. Tant que Community est désactivé (comme sur une nouvelle installation, ou après l’avoir quitté), vous pouvez masquer la bannière définitivement dans ce navigateur avec **Ne plus afficher**.

## En attente de position

Community a besoin de savoir à peu près où se trouve votre radio : il place chaque observateur sur sa carte pour montrer où les paquets sont entendus. Tant que Community est activé mais que votre radio n’a **aucune position** (aucune réglée, ou le `0, 0` « pas de GPS » de MeshCore), Community **attend votre position** :

- une bannière indique « Pour fonctionner, Meshloom Community a besoin de votre position », sur toutes les pages, réglages compris ;
- votre serveur garde sa connexion au broker Community uniquement pour publier son **état**, marqué en attente de position, afin que Community sache que l’observateur est là ;
- **aucun paquet n’est publié**, et la page Live, l’annuaire (noms de sauts, localisation RF, « entendu par »), le test radio, les statistiques, les noms hashtag et les recherches de canaux, la recherche d’aéroport et le miroir des versions de Community sont tous coupés, comme si vous l’aviez quitté. La page Live, le détail « entendu par » et le test radio indiquent que Community a besoin de votre position et proposent de la choisir.

Vous pouvez rejoindre alors que votre radio n’a pas de position : Community démarre alors dans cet état. Depuis la bannière :

- **Choisir ma position** ouvre une carte. Le point choisi est enregistré sur votre radio (comme dans **Réglages → Radio**). Si la **Source de position des annonces** de la radio est réglée sur **Inclure la position du nœud**, ses annonces diffuseront aussi cette position par les ondes à tous les nœuds à portée. Dès que la radio la signale, Meshloom l’envoie à Community avec votre code (arrondie à environ 1 km sauf si vos annonces la diffusent, voir plus bas). C’est seulement une fois qu’elle est acceptée par Community que Meshloom publie de nouveau son état et que tout ce qui précède revient, sans redémarrage ; si Community est injoignable, l’attente continue et Meshloom réessaie.
- **En savoir plus** explique pourquoi, et propose le même choix ou **Quitter Community**, qui est la désinscription habituelle.

Meshloom vérifie la position dès qu’il reconnaît la radio, avant d’envoyer quoi que ce soit à Community. Si la radio est déconnectée, ou pas encore reconnue après une reconnexion, Meshloom garde le dernier état connu, y compris après un redémarrage : la bannière ne va et vient pas pendant que la radio se reconnecte, et rien n’est envoyé à Community avant son retour. Retirer la position de la radio (la remettre à `0, 0`) remet aussitôt Community en attente.

## Installations neuves

Une **base de données toute neuve** démarre avec Community désactivé : le rejoindre est votre choix, fait dans **Réglages → Meshloom Community**. Aucune variable d’environnement ne l’active, ne fixe le code ni ne change les serveurs Community, qui sont fixes (`mqtt.meshloom.app` et `api.meshloom.app`).

`MESHLOOM_COMMUNITY`, `MESHLOOM_COMMUNITY_IATA`, `MESHLOOM_COMMUNITY_BROKER_HOST` et `MESHLOOM_COMMUNITY_API_BASE` ne sont plus lues. Sur une installation existante qui définit encore `MESHLOOM_COMMUNITY_IATA`, son code est recopié une seule fois dans les réglages, au premier démarrage après la mise à jour, pour que Community garde la région qu’il connaît ; l’activation de Community reste telle qu’elle était. Meshloom journalise ensuite un avertissement à chaque démarrage tant qu’une de ces variables est définie : supprimez-les.

Voir [Variables et réglages](/docs/deep/environment/).

## Ce qui quitte votre machine

Avec Community activé et un code enregistré, votre serveur envoie :

- Les **paquets bruts**, tels qu’ils circulent par les ondes, avec la force du signal, le nom de votre radio et sa clé publique. Les messages des salons privés et les messages directs restent chiffrés. Le texte de vos conversations ne passe jamais par ce chemin.
- La **position de votre radio**, si elle en a une, avec le code : quand vous rejoignez ou changez de code, puis d’elle-même quand la radio signale une nouvelle position ou une nouvelle **Source de position des annonces** (ce renvoi ne compte pas comme un changement de code). La radio les signale quand elle se connecte ou se reconnecte, quand Meshloom règle sa position et quand ce réglage change, pas à chaque mouvement du GPS : une radio qui se déplace avec son GPS actif envoie sa nouvelle position à la reconnexion ou au changement de réglage suivant. Elle est **exacte seulement si la radio la diffuse déjà** dans ses annonces (**Inclure la position du nœud**). Sinon elle est **arrondie à environ 1 km** (deux décimales). Community s’en sert pour vérifier le code, et **l’affiche sur sa carte publique** (Live, annuaire) : la position exacte est celle que vos annonces rendent déjà publique, la position arrondie place votre observateur près de sa commune, pas à son adresse. Une radio sans position n’en envoie aucune, et Community attend alors qu’elle en ait une (voir « En attente de position » plus haut).
- Un **message d’état**, à l’ouverture de la connexion puis toutes les cinq minutes : le nom de votre radio, sa clé publique, son modèle, sa version de firmware et ses paramètres radio (fréquence, largeur de bande, facteur d’étalement, taux de codage), la version de Meshloom, et les compteurs de la radio quand elle les fournit. Quand Community attend une position, il le dit aussi (`state: waiting_for_position`), et il est republié dès que cela change.
- Des **requêtes d’annuaire.** Pour nommer un saut ou dessiner une zone de localisation RF, votre serveur interroge Community sur les nœuds concernés. Le navigateur ne parle jamais à Community lui-même : c’est votre serveur qui relaie. Les sauts d’un seul octet ne sont jamais envoyés.
- Les **noms de salons hashtag.** Quand vous créez ou adoptez un salon hashtag, ou quand Meshloom en trouve un, son nom (50 au plus à la fois) est ajouté à la liste globale. Les noms ne sont pas regroupés par aéroport. Les clés ne sont pas partagées. Un salon dont la clé ne découle pas de son nom est privé : son nom n’est jamais partagé, même s’il commence par `#`.
- Des **recherches de canaux.** Pour reconnaître un canal inconnu, Meshloom envoie l’identifiant d’un octet du canal pour demander quels noms peuvent correspondre. Si rien ne correspond, il peut aussi envoyer un paquet chiffré de ce canal (une fois par identifiant tant que Meshloom tourne), pour que la communauté essaie d’en retrouver le nom. Seuls les canaux que vous ne pouvez pas lire sont concernés.

La clé privée de votre radio ne quitte jamais votre machine. Elle sert uniquement à signer les laissez-passer de courte durée qui prouvent à Community qui l’appelle. La clé publique de votre radio est votre compte : changer la clé de la radio démarre un nouvel historique et fait perdre le code associé.

**Désactiver Community coupe toute connexion à Community** (`*.meshloom.app`) ainsi que la recherche d’aéroport, immédiatement et sans redémarrage, avec la seule exception décrite au premier point :

- l’état de votre nœud sur le broker passe à `offline` puis est effacé, avant la fermeture de la connexion MQTT, pour qu’il ne reste pas affiché en ligne. Si le broker est injoignable à ce moment-là, rien ne peut être envoyé : le broker marque alors lui-même le nœud `offline` quand il s’aperçoit que la connexion est perdue, et cet `offline` resterait. C’est la **seule connexion faite après votre départ** : Meshloom se reconnecte au broker, seul, pour effacer cet état restant et rien d’autre (ni paquet, ni état, ni autre requête). Il essaie 6 fois au plus en 13 heures environ (après 1 minute, puis 5 minutes, 15 minutes, 1 heure, 4 heures et 8 heures), tient compte d’un redémarrage entre-temps, et abandonne définitivement au bout de 24 heures. Une fois l’état effacé, ou l’abandon acté, plus rien ne se connecte à Community ;
- le flux Live se ferme, et les requêtes Community encore en cours sont annulées ;
- les compteurs « entendu par » et le test radio indiquent que Community est désactivé au lieu d’afficher un résultat vide.

Changer le code IATA efface de la même façon l’état laissé sous l’ancien code, avec la même reprise unique si le broker est injoignable : l’ancien état est d’abord effacé, puis Community enregistre le nouveau code, et alors seulement Meshloom se reconnecte avec lui. Si Community refuse le nouveau code, votre nœud repasse en ligne sous l’ancien. Arrêter ou redémarrer Meshloom marque le nœud `offline`. Si le code change pendant que Meshloom est arrêté (une base restaurée, par exemple), ou si Community est désactivé entre-temps, l’état laissé sous l’ancien code est effacé au démarrage suivant, avec la même reprise unique. Il en va de même une fois après la mise à jour sur une installation qui utilisait son propre broker par `MESHLOOM_COMMUNITY_BROKER_HOST` : l’état laissé sur ce broker est effacé, puis Meshloom publie sur `mqtt.meshloom.app`. Rejoindre de nouveau avec le même code annule une reprise en attente : l’état redevient le vôtre.

**Community désactivé ne veut pas dire zéro trafic réseau.** Ceci continue :

- la vérification des nouvelles versions de Meshloom interroge GitHub seulement, une fois toutes les 6 heures. Avec Community activé, elle a lieu toutes les 5 minutes et se rabat sur un miroir de Community (`/v1/meshloom/latest`) quand GitHub ne répond pas ;
- Web Push et les intégrations que vous configurez appellent leurs propres serveurs ;
- les e-mails d’alerte partent par le serveur de messagerie que vous avez configuré. Le logo de Meshloom est inclus dans l’e-mail : l’ouvrir ne charge rien depuis `meshloom.app` ;
- les fonds de carte sont chargés par votre navigateur chez leurs fournisseurs (OpenStreetMap, CARTO, OpenTopoMap, Esri).

La recherche d’aéroport (`api.fx-port.com`, par l’intermédiaire de votre serveur) fait partie de Community : désactivé, elle n’est jamais appelée, et vous saisissez vous-même le code à trois lettres. La page Live s’en sert aussi pour placer les régions choisies sur la carte, une fois par code tant que la page reste ouverte, et seulement quand Community est activé.

## Dans l’interface

**Réglages → Meshloom Community** permet de rejoindre ou de quitter, de saisir le code IATA (ou de le chercher une fois inscrit), de voir si le service de publication est connecté, et de lire votre contribution (paquets uniques sur 24 heures et 7 jours, et votre rang dans votre région) à côté des totaux de la communauté. Quand Community attend une position, les réglages l’indiquent et n’affichent ni les statistiques ni la recherche d’aéroport.

Le détail « entendu par » et le test radio ne mesurent de distance que vers les observateurs dont la position exacte est connue. Un observateur qui publie une position arrondie apparaît quand même sur leurs cartes, mais sans distance, et il ne bat jamais le record de l’oreille la plus lointaine. Exception : si votre radio a entendu l’annonce de cet observateur avec sa position (même clé publique, jamais un simple nom identique), Meshloom utilise cette position exacte, et l’observateur compte alors dans les distances.

Voir [Variables et réglages](/docs/deep/environment/) et [Sécurité](/docs/deep/security/).
