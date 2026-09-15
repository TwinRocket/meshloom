---
title: Meshloom Community
description: Rejoindre, associer un code IATA, publier les paquets entendus, et partager les noms hashtag.
level: deep
order: 17
---

Meshloom Community est un réseau d’observateurs optionnel. Quand il est activé, ce serveur peut publier les **paquets bruts** entendus vers les hôtes Stats officiels, utiliser l’annuaire communautaire (noms de sauts, locate, portée observateurs), et partager les **noms de salons hashtag** pour le même code IATA.

Ce n’est pas une ligne Fanout à créer dans Réglages > Fanout. On rejoint et on quitte depuis **Réglages > Community** (`#settings/community`).

## Défaut des installs neuves

Une **base toute neuve** seed Community activé, sauf si vous posez `MESHLOOM_COMMUNITY=0` (ou `false` / `off`) avant le premier démarrage. Les bases existantes ne sont jamais basculées par cette variable : elles gardent ce qui est déjà stocké.

Tant qu’un code IATA d’aéroport n’est pas enregistré, une bannière reste affichée. Les jetons ont besoin de ce code, donc la contribution et l’annuaire attendent. Un opérateur qui coupe Community peut masquer la bannière définitivement dans ce navigateur.

`MESHLOOM_COMMUNITY_IATA` peut seeder un code à 3 lettres sur une base neuve. `MESHLOOM_COMMUNITY_LOCKED=1` empêche l’interface d’activer Community.

## Ce qui quitte la machine

Avec Community activé et un IATA posé :

- Les **paquets bruts** entendus sont publiés vers les hôtes MQTT Stats officiels. Le texte décodé des conversations ne passe pas par ce chemin.
- Les noms de sauts, le RF locate et les compteurs « entendu par » utilisent l’annuaire communautaire. Community coupé, il n’y a plus d’annuaire du tout : Meshloom ne contacte aucun autre hôte.
- Les **noms hashtag** des salons hashtag locaux (jusqu’à 50), et les noms trouvés par le chercheur de salons, peuvent être publiés pour cet IATA. Les clés ne sont pas partagées.

Un seul opt-out arrête la publication **et** les appels d’annuaire communautaires.

## Dans l’interface

**Réglages > Community** : rejoindre ou quitter, chercher ou saisir un code IATA, voir les stats de contribution.

Le chercheur de salons fonctionne toujours hors ligne. Les noms Community, quand ils sont disponibles, sont essayés en premier avec une liste MeshCore embarquée et des échantillons GroupText stockés non déchiffrés.

Voir [Variables et réglages](/docs/deep/environment/) et [Sécurité](/docs/deep/security/).
