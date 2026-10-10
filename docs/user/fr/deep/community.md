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
- Les noms de sauts, le RF locate et les compteurs « entendu par » utilisent l’annuaire communautaire. Community coupé, il n’y a plus d’annuaire du tout. Cela ne veut pas dire zéro trafic sortant : la vérification des mises à jour interroge toujours GitHub, puis le miroir de versions de Community (`/v1/meshloom/latest`) en secours ; la recherche IATA des Réglages interroge `api.fx-port.com` ; Web Push et les modules fanout que vous configurez ont leurs propres hôtes.
- Les **noms hashtag** des salons hashtag locaux (jusqu’à 50), et les noms trouvés par le chercheur de salons, peuvent être publiés dans la liste globale. La publication exige un code IATA posé. Les clés ne sont pas partagées.

Un seul opt-out arrête la publication **et** les appels d’annuaire communautaires. Il n’arrête pas la vérification des mises à jour.

## Dans l’interface

**Réglages > Community** : rejoindre ou quitter, chercher ou saisir un code IATA, voir les stats de contribution.

Le chercheur de salons fonctionne toujours hors ligne. Il essaie d’abord une liste MeshCore embarquée contre les échantillons GroupText stockés non déchiffrés, puis les noms Community quand Community est activé et qu’un code IATA est posé.

Voir [Variables et réglages](/docs/deep/environment/) et [Sécurité](/docs/deep/security/).
