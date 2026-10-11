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
2. Saisissez le **code IATA** de l’aéroport le plus proche : trois lettres, par exemple `CDG`. Si vous ne le connaissez pas, tapez une ville dans **Trouver un aéroport**.
3. Enregistrez le code.

Ce code est une étiquette grossière de votre région, pas une localisation précise. Meshloom le compare à la position de votre radio et vous dit si les deux concordent. Si vous êtes sûr de votre choix malgré une différence, **Je suis sûr de moi** le confirme. Community limite la fréquence à laquelle le code peut être modifié.

Tant qu’aucun code n’est enregistré, une bannière reste affichée. La publication des paquets, la page Live et la publication des noms hashtag attendent ce code. L’annuaire (noms de sauts, localisation RF, « entendu par ») n’en a pas besoin. Si vous avez désactivé Community, vous pouvez masquer la bannière définitivement dans ce navigateur avec **Ne plus afficher**.

## Installations neuves

Une **base de données toute neuve** démarre avec Community activé, sauf si `MESHLOOM_COMMUNITY=0` (ou `false` / `off`) est défini avant le premier démarrage. Les bases existantes ne sont jamais basculées par cette variable : elles gardent ce qu’elles ont enregistré.

Trois autres variables s’adressent à ceux qui font tourner Meshloom pour d’autres personnes :

- `MESHLOOM_COMMUNITY_IATA` remplace le code enregistré dans l’interface tant qu’elle est définie : modifier le code dans les Réglages n’a alors aucun effet.
- `MESHLOOM_COMMUNITY_BROKER_HOST` et `MESHLOOM_COMMUNITY_API_BASE` font de même pour les deux serveurs Community.
- `MESHLOOM_COMMUNITY_LOCKED=1` empêche l’interface d’activer Community.

Voir [Variables et réglages](/docs/deep/environment/).

## Ce qui quitte votre machine

Avec Community activé et un code enregistré, votre serveur envoie :

- Les **paquets bruts**, tels qu’ils circulent par les ondes, avec la force du signal, le nom de votre radio et sa clé publique. Les messages des salons privés et les messages directs restent chiffrés. Le texte de vos conversations ne passe jamais par ce chemin.
- Des **requêtes d’annuaire.** Pour nommer un saut ou dessiner une zone de localisation RF, votre serveur interroge Community sur les nœuds concernés. Le navigateur ne parle jamais à Community lui-même : c’est votre serveur qui relaie. Les sauts d’un seul octet ne sont jamais envoyés.
- Les **noms de salons hashtag.** Quand vous créez ou adoptez un salon hashtag, ou quand Meshloom en trouve un, son nom (50 au plus à la fois) est ajouté à la liste globale. Les noms ne sont pas regroupés par aéroport. Les clés ne sont pas partagées.
- Des **recherches de canaux.** Pour reconnaître un canal inconnu, Meshloom envoie l’identifiant d’un octet du canal pour demander quels noms peuvent correspondre. Si rien ne correspond, il peut aussi envoyer un paquet chiffré de ce canal (une fois par identifiant tant que Meshloom tourne), pour que la communauté essaie d’en retrouver le nom. Seuls les canaux que vous ne pouvez pas lire sont concernés.

La clé privée de votre radio ne quitte jamais votre machine. Elle sert uniquement à signer les laissez-passer de courte durée qui prouvent à Community qui l’appelle. La clé publique de votre radio est votre compte : changer la clé de la radio démarre un nouvel historique et fait perdre le code associé.

**Community désactivé ne veut pas dire zéro trafic réseau.** Le désactiver arrête la publication et toutes les requêtes d’annuaire. Ceci continue :

- la vérification des nouvelles versions de Meshloom interroge GitHub, puis, en secours, un miroir de Community (`/v1/meshloom/latest`) ;
- la recherche d’aéroport dans les Réglages interroge `api.fx-port.com`, par l’intermédiaire de votre serveur ;
- Web Push et les intégrations que vous configurez appellent leurs propres serveurs.

## Dans l’interface

**Réglages → Meshloom Community** permet de rejoindre ou de quitter, de chercher ou de saisir le code IATA, de voir si le service de publication est connecté, et de lire votre contribution (paquets uniques sur 24 heures et 7 jours, et votre rang dans votre région) à côté des totaux de la communauté.

Voir [Variables et réglages](/docs/deep/environment/) et [Sécurité](/docs/deep/security/).
