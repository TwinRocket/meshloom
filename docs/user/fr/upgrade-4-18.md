---
title: Passer de la 4.18 à la 5.0
description: Ce qui change quand Meshloom 4.x devient 5.0, la copie de votre base faite avant la migration, pourquoi il n’y a pas de retour en place, et comment revenir en arrière depuis la copie.
level: start
order: 2.8
---

Meshloom 5.0 change la structure de votre base de données à son premier démarrage. Une version plus ancienne ne lit pas le résultat : **une installation ne peut pas revenir à la 4.18 en place**. Meshloom vous protège avec une copie faite avant tout changement ; cette page dit où elle se trouve, ce qui change, et comment revenir en arrière depuis la copie si besoin.

## Avant de mettre à jour

1. **Désactivez les mises à jour automatiques** dans **Réglages → Mises à jour** jusqu’à ce que vous soyez prêt, pour que la mise à jour ait lieu au moment que vous choisissez.
2. **Téléchargez une copie de la base** (**Réglages → Base de données → Télécharger meshcore.db**) et gardez-la ailleurs que sur cette machine. C’est votre propre filet de sécurité, en plus de la copie automatique ci-dessous. Gardez aussi avec elle le fichier `secrets.key` du dossier de données (voir [Autres chemins d’installation](/docs/deep/install-paths/#base-de-données-et-mises-à-jour)).
3. **Faites une sauvegarde complète** (**Réglages → Base de données → Sauvegarde et restauration**, voir [Sauvegarder votre radio](/docs/backup/)). Une sauvegarde faite en 5.0 a un format plus récent que la 4.18 ne lit pas entièrement : servez-vous-en pour restaurer en 5.0 ou après, jamais en 4.18.
4. **Notez vos bots Python** si vous en avez : ils deviennent des extensions (ci-dessous).

## La copie faite automatiquement

Au premier démarrage de la 5.0 sur une base 4.18, Meshloom fait une copie de la base **avant** de rien changer, dans le dossier de données, à côté de la base (voir [Autres chemins d’installation](/docs/deep/install-paths/#base-de-données-et-mises-à-jour) pour son emplacement). La copie est une copie de toute la base : elle contient vos messages, contacts et réglages tels que la 4.18 les a laissés. Gardez-la tant que vous n’êtes pas sûr de rester en 5.0 ; Meshloom ne s’en sert plus.

## Ce qui change

- **Des comptes remplacent le mot de passe partagé.** La connexion Basic de la 4.x n’existe plus et ses variables sont ignorées. Le premier démarrage vous demande de créer le compte administrateur et, puisque votre installation contenait déjà des données, de saisir le **code d’installation** que Meshloom affiche dans son journal (`journalctl -u meshloom`, `docker logs <conteneur>`, ou l’onglet **Journal** du module ; `meshloom-admin setup-code` en affiche un nouveau). Voir [Premier lancement](/docs/first-run/) et [Comptes et accès](/docs/deep/access/). Les scripts et capteurs Home Assistant qui envoyaient le mot de passe Basic utilisent maintenant un jeton d’API (`Authorization: Bearer`).
- **L’accès sans connexion reste désactivé.** Les règles de réseau de confiance que vous aviez sont conservées mais désactivées, jusqu’à ce qu’un administrateur active l’accès sans connexion sur le réseau local.
- **Les bots Python deviennent des extensions.** Chaque bot est transformé en extension locale dont le code est conservé, et reste **désactivée** jusqu’à ce qu’un administrateur examine ses permissions et choisisse **Accepter et activer**. Voir [Les bots de la 4.18](/docs/deep/plugins/#les-bots-de-la-418-et-avant).
- **La sauvegarde complète a un nouveau format.** La 4.18 ignore en silence ce qu’elle n’y connaît pas.
- **Meshloom Community reste comme il était** sur une installation existante. Il est désactivé par défaut sur une nouvelle.
- **Les installations depuis le code source demandent Python 3.13 ou plus.** Les paquets, Docker et l’add-on apportent le leur.

## Revenir à la 4.18

Il n’y a pas de retour en place : la 4.18 ne lit pas ce que la 5.0 a écrit : les bots convertis en 5.0 n’apparaîtraient pas comme des bots, et les canaux de notification ajoutés en 5.0 ne seraient pas vus.

Pour revenir en arrière, depuis la copie :

1. Arrêtez Meshloom.
2. Réinstallez la 4.18. Avec un paquet Linux, installez cette version du paquet ; avec Docker, remettez l’image 4.18 dans le fichier Compose. L’assistant de mise à jour ne passe jamais seul à une version plus ancienne.
3. Dans le dossier de données, remplacez `meshcore.db` par la copie de la base faite avant la migration (ou celle que vous avez téléchargée), avec le `secrets.key` qui l’accompagnait.
4. Démarrez Meshloom.

Tout ce que vous avez fait en 5.0 après la mise à jour (messages entendus, réglages, comptes) disparaît avec : la copie date du moment de la mise à jour.
