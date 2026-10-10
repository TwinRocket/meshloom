---
title: Meshloom Community
description: Rejoindre, associer un code IATA, publier les paquets entendus, et partager les noms hashtag.
level: deep
order: 17
---

Meshloom Community est un réseau d’observateurs optionnel. Quand il est activé, ce serveur peut publier les **paquets bruts** entendus vers les hôtes Stats officiels, utiliser l’annuaire communautaire (noms de sauts, locate, portée observateurs), et partager les **noms de salons hashtag** dans une liste globale unique (les noms ne sont pas regroupés par aéroport).

Ce n’est pas une ligne Fanout à créer dans Réglages > Fanout. On rejoint et on quitte depuis **Réglages > Community** (`#settings/community`).

## Défaut des installs neuves

Une **base toute neuve** seed Community activé, sauf si vous posez `MESHLOOM_COMMUNITY=0` (ou `false` / `off`) avant le premier démarrage. Les bases existantes ne sont jamais basculées par cette variable : elles gardent ce qui est déjà stocké.

Tant qu’un code IATA d’aéroport n’est pas enregistré, une bannière reste affichée. La publication des paquets, le Live et la publication des noms hashtag attendent ce code. L’annuaire (noms de sauts, locate, « entendu par ») n’en a pas besoin. Un opérateur qui coupe Community peut masquer la bannière définitivement dans ce navigateur.

Tant que `MESHLOOM_COMMUNITY_IATA` est posée, son code à 3 lettres remplace à chaque lecture celui enregistré dans l’interface ; `MESHLOOM_COMMUNITY_BROKER_HOST` et `MESHLOOM_COMMUNITY_API_BASE` font de même pour les hôtes.

## Ce qui quitte la machine

Avec Community activé et un IATA posé :

- Les **paquets bruts** entendus sont publiés vers les hôtes MQTT Stats officiels. Le texte décodé des conversations ne passe pas par ce chemin.
- Les noms de sauts, le RF locate et les compteurs « entendu par » utilisent l’annuaire communautaire. Community coupé, il n’y a plus d’annuaire du tout : la fenêtre « entendu par » et le test radio indiquent que Community est désactivé.
- La recherche d’aéroport des Réglages et du choix de position sur la carte interroge `api.fx-port.com`. Elle fait partie de Community : Community coupé, elle n’est jamais appelée, et vous saisissez vous-même le code à 3 lettres.
- Les **noms hashtag** des salons hashtag locaux (jusqu’à 50), et les noms trouvés par le chercheur de salons, peuvent être publiés dans la liste globale. La publication exige un code IATA posé. Les clés ne sont pas partagées.

Couper Community coupe toutes les connexions vers les hôtes Community (`*.meshloom.app`, ou les hôtes fixés par `MESHLOOM_COMMUNITY_BROKER_HOST` et `MESHLOOM_COMMUNITY_API_BASE`) et vers la recherche d’aéroport, tout de suite et sans redémarrage :

- le statut `online` du nœud sur le broker est effacé avant la fermeture de la connexion MQTT : le nœud ne reste pas affiché en ligne ;
- le flux Live se ferme, et les requêtes Community en cours sont annulées ;
- la vérification des mises à jour n’interroge plus que GitHub, une fois toutes les 6 heures. Avec Community activé, elle tourne toutes les 5 minutes et se rabat sur le miroir de versions de Community quand GitHub ne répond pas.

Changer de code IATA efface de la même façon le statut laissé sous l’ancien code.

Community coupé ne veut pas dire zéro trafic sortant : la vérification des mises à jour (GitHub), Web Push et les modules fanout que vous configurez ont leurs propres hôtes.

## Dans l’interface

**Réglages > Community** : rejoindre ou quitter, saisir un code IATA (ou le chercher une fois membre), voir les stats de contribution.

Le chercheur de salons fonctionne toujours hors ligne. Il essaie d’abord une liste MeshCore embarquée contre les échantillons GroupText stockés non déchiffrés, puis les noms Community quand Community est activé et qu’un code IATA est posé.

Voir [Variables et réglages](/docs/deep/environment/) et [Sécurité](/docs/deep/security/).
