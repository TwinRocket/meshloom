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
3. Enregistrez le code.

Ce code est une étiquette grossière de votre région, pas une localisation précise. Meshloom le compare à la position de votre radio et vous dit si les deux concordent. Si vous êtes sûr de votre choix malgré une différence, **Je suis sûr de moi** le confirme. Community limite la fréquence à laquelle le code peut être modifié.

Ce code est aussi la région que la page Live affiche par défaut ; on peut y ajouter d’autres régions.

Tant qu’aucun code n’est enregistré, une bannière reste affichée. La publication des paquets, la page Live, la publication des noms hashtag et les recherches de canaux attendent ce code. L’annuaire (noms de sauts, localisation RF, « entendu par ») n’en a pas besoin. Si vous avez désactivé Community, vous pouvez masquer la bannière définitivement dans ce navigateur avec **Ne plus afficher**.

## Installations neuves

Une **base de données toute neuve** démarre avec Community activé, sauf si `MESHLOOM_COMMUNITY=0` (ou `false`, `off`, `no`) est défini avant le premier démarrage. Les bases existantes ne sont jamais basculées par cette variable : elles gardent ce qu’elles ont enregistré.

Trois autres variables s’adressent à ceux qui font tourner Meshloom pour d’autres personnes :

- `MESHLOOM_COMMUNITY_IATA` remplace le code enregistré dans l’interface tant qu’elle est définie : modifier le code dans les Réglages n’a alors aucun effet.
- `MESHLOOM_COMMUNITY_BROKER_HOST` et `MESHLOOM_COMMUNITY_API_BASE` font de même pour les deux serveurs Community.

Voir [Variables et réglages](/docs/deep/environment/).

## Ce qui quitte votre machine

Avec Community activé et un code enregistré, votre serveur envoie :

- Les **paquets bruts**, tels qu’ils circulent par les ondes, avec la force du signal, le nom de votre radio et sa clé publique. Les messages des salons privés et les messages directs restent chiffrés. Le texte de vos conversations ne passe jamais par ce chemin.
- Un **message d’état**, à l’ouverture de la connexion puis toutes les cinq minutes : le nom de votre radio, sa clé publique, son modèle, sa version de firmware et ses paramètres radio (fréquence, largeur de bande, facteur d’étalement, taux de codage), la version de Meshloom, et les compteurs de la radio quand elle les fournit.
- Des **requêtes d’annuaire.** Pour nommer un saut ou dessiner une zone de localisation RF, votre serveur interroge Community sur les nœuds concernés. Le navigateur ne parle jamais à Community lui-même : c’est votre serveur qui relaie. Les sauts d’un seul octet ne sont jamais envoyés.
- Les **noms de salons hashtag.** Quand vous créez ou adoptez un salon hashtag, ou quand Meshloom en trouve un, son nom (50 au plus à la fois) est ajouté à la liste globale. Les noms ne sont pas regroupés par aéroport. Les clés ne sont pas partagées. Un salon dont la clé ne découle pas de son nom est privé : son nom n’est jamais partagé, même s’il commence par `#`.
- Des **recherches de canaux.** Pour reconnaître un canal inconnu, Meshloom envoie l’identifiant d’un octet du canal pour demander quels noms peuvent correspondre. Si rien ne correspond, il peut aussi envoyer un paquet chiffré de ce canal (une fois par identifiant tant que Meshloom tourne), pour que la communauté essaie d’en retrouver le nom. Seuls les canaux que vous ne pouvez pas lire sont concernés.

La clé privée de votre radio ne quitte jamais votre machine. Elle sert uniquement à signer les laissez-passer de courte durée qui prouvent à Community qui l’appelle. La clé publique de votre radio est votre compte : changer la clé de la radio démarre un nouvel historique et fait perdre le code associé.

**Désactiver Community coupe toute connexion à Community** (`*.meshloom.app`, ou les serveurs définis par `MESHLOOM_COMMUNITY_BROKER_HOST` et `MESHLOOM_COMMUNITY_API_BASE`) ainsi que la recherche d’aéroport, immédiatement et sans redémarrage, avec la seule exception décrite au premier point :

- l’état de votre nœud sur le broker passe à `offline` puis est effacé, avant la fermeture de la connexion MQTT, pour qu’il ne reste pas affiché en ligne. Si le broker est injoignable à ce moment-là, rien ne peut être envoyé : le broker marque alors lui-même le nœud `offline` quand il s’aperçoit que la connexion est perdue, et cet `offline` resterait. C’est la **seule connexion faite après votre départ** : Meshloom se reconnecte au broker, seul, pour effacer cet état restant et rien d’autre (ni paquet, ni état, ni autre requête). Il essaie 6 fois au plus en 13 heures environ (après 1 minute, puis 5 minutes, 15 minutes, 1 heure, 4 heures et 8 heures), tient compte d’un redémarrage entre-temps, et abandonne définitivement au bout de 24 heures. Une fois l’état effacé, ou l’abandon acté, plus rien ne se connecte à Community ;
- le flux Live se ferme, et les requêtes Community encore en cours sont annulées ;
- les compteurs « entendu par » et le test radio indiquent que Community est désactivé au lieu d’afficher un résultat vide.

Changer le code IATA efface de la même façon l’état laissé sous l’ancien code, avec la même reprise unique si le broker est injoignable. Arrêter ou redémarrer Meshloom marque le nœud `offline`. Rejoindre de nouveau avec le même code annule une reprise en attente : l’état redevient le vôtre.

**Community désactivé ne veut pas dire zéro trafic réseau.** Ceci continue :

- la vérification des nouvelles versions de Meshloom interroge GitHub seulement, une fois toutes les 6 heures. Avec Community activé, elle a lieu toutes les 5 minutes et se rabat sur un miroir de Community (`/v1/meshloom/latest`) quand GitHub ne répond pas ;
- Web Push et les intégrations que vous configurez appellent leurs propres serveurs ;
- les e-mails d’alerte partent par le serveur de messagerie que vous avez configuré. Le logo de Meshloom est inclus dans l’e-mail : l’ouvrir ne charge rien depuis `meshloom.app` ;
- les fonds de carte sont chargés par votre navigateur chez leurs fournisseurs (OpenStreetMap, CARTO, OpenTopoMap, Esri).

La recherche d’aéroport (`api.fx-port.com`, par l’intermédiaire de votre serveur) fait partie de Community : désactivé, elle n’est jamais appelée, et vous saisissez vous-même le code à trois lettres. La page Live s’en sert aussi pour placer les régions choisies sur la carte, une fois par code tant que la page reste ouverte.

## Dans l’interface

**Réglages → Meshloom Community** permet de rejoindre ou de quitter, de saisir le code IATA (ou de le chercher une fois inscrit), de voir si le service de publication est connecté, et de lire votre contribution (paquets uniques sur 24 heures et 7 jours, et votre rang dans votre région) à côté des totaux de la communauté.

Voir [Variables et réglages](/docs/deep/environment/) et [Sécurité](/docs/deep/security/).
