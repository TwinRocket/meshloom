---
title: Aller plus loin
description: Transports, chemins d’installation, variables, Community, liens courts, fanout, notifications, Home Assistant, dépannage.
level: deep
order: 10
---

Cette partie suppose que Meshloom tourne déjà. Elle couvre les réglages et les situations que les premières pages laissent de côté, pour que vous n’ayez pas à ouvrir le dépôt de code afin de trouver un réglage.

Elle emploie le vocabulaire du projet : transport (la façon dont l’ordinateur parle à la radio), fanout (envoyer ailleurs ce que Meshloom entend), portée de flood (la région dont un message est étiqueté), mode de hachage de chemin (combien d’octets identifient chaque répéteur sur une route) et contexte sécurisé (une page servie en HTTPS ou depuis `localhost`). Chaque page explique ceux dont elle a besoin. Si un terme reste flou, commencez par [Qu’est-ce que c’est](/docs/what/) et [Autour des messages](/docs/around/).

Deux pages valent une lecture avant les autres :

- [Variables et réglages](/docs/deep/environment/) sépare ce qui se décide au démarrage de ce qui vit dans la base et se change dans l’interface.
- [Sécurité](/docs/deep/security/) dit ce que Meshloom ne protège pas.

Pour être prévenu quand quelque chose se passe, lisez [Notifications](/docs/deep/notifications/) (canaux, événements et règles ; ntfy, Telegram, Discord, Home Assistant, e-mail, webhooks) et, pour les navigateurs, [Notifications push](/docs/deep/push/). Pour transmettre chaque message à un autre système, lisez [Fanout](/docs/deep/fanout/).

[Meshloom Community](/docs/deep/community/) reste désactivé tant que vous ne l’avez pas rejoint : lisez sa page pour savoir ce qu’il envoie avant de le faire. Il en va de même pour les [Liens courts et photos](/docs/deep/share/), le service de partage optionnel qui raccourcit les longues adresses web : il reste désactivé tant qu’un administrateur ne l’active pas.
