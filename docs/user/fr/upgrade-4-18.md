---
title: Passer de la 4.18 à la 5.0
description: Ce qui change quand Meshloom 4.x devient 5.0, la copie de votre base faite avant la migration, pourquoi il n’y a pas de retour en place, et comment revenir en arrière depuis cette copie.
level: start
order: 2.8
---

Meshloom 5.0 change la structure de votre base de données à son premier démarrage. La 4.18 ne comprend pas le résultat, et ne refuse pas non plus de l’ouvrir : **une installation ne peut pas revenir à la 4.18 en place**. Meshloom vous protège avec une copie faite avant tout changement ; cette page dit où elle se trouve, ce qui change, et comment revenir en arrière depuis la copie si besoin.

## Avant de mettre à jour

1. **Désactivez les mises à jour automatiques** dans **Réglages → Mises à jour** jusqu’à ce que vous soyez prêt, pour que la mise à jour ait lieu au moment que vous choisissez.
2. **Téléchargez une copie de la base en 4.18, avant la mise à jour** (**Réglages → Base de données → Télécharger meshcore.db**) et gardez-la ailleurs que sur cette machine. C’est votre propre filet de sécurité, en plus de la copie automatique ci-dessous. Une copie téléchargée en 5.0 est déjà migrée et expurgée : elle ne peut pas vous ramener en arrière.
3. **Faites une sauvegarde complète** (**Réglages → Base de données → Sauvegarde et restauration**, voir [Sauvegarder votre radio](/docs/backup/)). Une sauvegarde faite en 5.0 a un format plus récent que la 4.18 ne lit pas entièrement : servez-vous-en pour restaurer en 5.0 ou après, jamais en 4.18.
4. **Notez vos bots Python** si vous en avez : ils deviennent des extensions (ci-dessous).

## La copie faite automatiquement

Quand la 5.0 doit changer la structure de votre base, elle en fait d’abord une copie à côté, nommée d’après la structure qu’elle contient et la version qui l’a faite : `meshcore.db.schema95.before-5.0.0.db` depuis la 4.18.3, ou `...before-5.0.0-rc.1.db` sur une version candidate (schéma 87 pour les 4.18.0 à 4.18.2, schéma 95 pour la 4.18.3, la dernière 4.18). Elle se trouve dans le dossier de données : `/var/lib/meshloom/` pour le paquet Linux, `./data` pour Docker, `data/` pour une copie du code, `/config` pour le module Home Assistant. Une base 4.18 n’a pas de `secrets.key`, donc aucun fichier de clé n’est copié. La copie occupe autant de place que la base. Meshloom demande cette taille plus un quart (64 Mo au moins) de libre sur le disque, et ne met pas à jour sans cela : le journal dit combien il en faut. Seul vous la lisez (elle contient tout ce que contient la base). Gardez-la tant que vous n’êtes pas sûr de rester en 5.0. Le détail est dans [Autres chemins d’installation](/docs/deep/install-paths/#la-copie-faite-avant-une-mise-à-niveau).

## Ce qui change

- **Des comptes remplacent le mot de passe partagé.** La connexion Basic de la 4.x n’existe plus et ses variables sont ignorées. Le premier démarrage vous demande de créer le compte administrateur, depuis le réseau local ; tant que les variables Basic sont définies, la page demande aussi ce nom d’utilisateur et ce mot de passe de la 4.x. Créez le compte juste après la mise à jour, depuis le réseau local ou avec `meshloom-admin create-admin <utilisateur>`, surtout si cette instance est publiée sur Internet, ou gardez-la hors d’Internet jusque-là. Voir [Premier lancement](/docs/first-run/) et [Comptes et accès](/docs/deep/access/). Les scripts et capteurs Home Assistant qui envoyaient le mot de passe Basic utilisent maintenant un jeton d’API (`Authorization: Bearer`).
- **Après une mise à jour lancée depuis l’écran de la 4.18, la page se recharge d’elle-même.** Une page 4.18 restée ouverte pendant la mise à jour se recharge sur la nouvelle version dès que la 5.0 répond. Si elle affiche à la place une erreur disant que Meshloom n’est pas revenu, alors qu’il tourne, rechargez-la pour atteindre la nouvelle version et créer le compte.
- **Docker : le dossier de données n’est plus lisible que par Meshloom.** Une sauvegarde qui le lit avec votre propre utilisateur échoue avec « Permission denied » : utilisez `sudo`, ou **Réglages → Base de données**.
- **`apt-get upgrade` garde Meshloom en 4.18, sans erreur.** La 5.0 recommande un nouveau paquet, `bubblewrap`, et `apt-get upgrade` n’en ajoute jamais : il indique `meshloom` comme « kept back » (retenu) et s’arrête là. Utilisez `sudo apt upgrade`, `sudo apt-get full-upgrade` ou `sudo apt-get install --only-upgrade meshloom` (la mise à jour depuis l’application et l’installeur passent aussi par `apt-get install`). Dans un script ou une tâche cron, sans terminal :

  ```bash
  sudo apt-get update && sudo apt-get -y -o Dpkg::Options::=--force-confold install --only-upgrade meshloom
  ```

- **`apt` peut poser une question sur `/etc/meshloom/meshloom.env`, seulement si vous l’avez modifié et n’avez jamais installé la 4.18.3.** Le fichier est le même que dans la 4.18.3 : un hôte en 4.18.3 n’est pas interrogé. Un hôte qui l’a sautée (4.18.0 à 4.18.2) avec une copie modifiée l’est : gardez votre fichier, répondez `N`, la réponse par défaut. La commande ci-dessus le garde sans rien demander, l’installeur aussi.
- **Avec `dnf`, la mise à jour depuis l’application en 4.18 n’ajoute pas `bubblewrap`.** Sans lui, les extensions tournent sans isolation du système ; **Réglages → Extensions** indique le niveau. Installez-le, puis redémarrez Meshloom, qui ne le cherche qu’au démarrage : `sudo dnf install bubblewrap && sudo systemctl restart meshloom`. L’installeur habituel, en 5.0.0 ou plus récent, fait les deux et met aussi à jour un Meshloom déjà installé, comme `sudo dnf upgrade --refresh meshloom`. Tant que la 5.0.0 n’est pas sortie, `get.meshloom.app` sert encore l’installeur de la 4.18 : ne le lancez pas sur une 5.0. À partir de la 5.0.0, la mise à jour depuis l’application installe `bubblewrap` s’il manque, avant de redémarrer Meshloom.
- **L’accès sans connexion reste désactivé.** Les règles de réseau de confiance que vous aviez sont conservées mais désactivées, jusqu’à ce qu’un administrateur active l’accès sans connexion sur le réseau local.
- **Les bots Python deviennent des extensions.** Chaque bot est transformé en extension locale dont le code est conservé, et reste **désactivée** jusqu’à ce qu’un administrateur examine ses permissions et choisisse **Accepter et activer**. Voir [Les bots de la 4.18](/docs/deep/plugins/#les-bots-de-la-418-et-avant).
- **La sauvegarde complète a un nouveau format.** La 4.18 ignore en silence ce qu’elle n’y connaît pas.
- **Meshloom Community reste comme il était** sur une installation existante. Il est désactivé par défaut sur une nouvelle.
- **Les installations depuis le code source demandent Python 3.13 ou plus.** Les paquets, Docker et l’add-on apportent le leur.

## Revenir à la 4.18

Il n’y a pas de retour en place : la 4.18 ne comprend pas ce que la 5.0 a écrit, et ne refuse pas de démarrer dessus. Les bots convertis en 5.0 n’apparaîtraient pas comme des bots, et les canaux de notification ajoutés en 5.0 ne seraient pas vus. Ce qui a été reçu ou modifié depuis la mise à jour ne fait pas partie de ce que vous retrouvez. Il y a deux possibilités :

- **Une nouvelle instance.** Laissez celle-ci telle quelle et repartez d’une nouvelle installation.
- **Une installation neuve de la 4.18 avec votre copie des données.** Sur une autre machine, ou sur celle-ci après avoir supprimé Meshloom et déplacé son dossier de données : installez la 4.18, arrêtez-la, remplacez `meshcore.db` par votre copie dans son dossier de données et supprimez `meshcore.db-wal`, `meshcore.db-shm` et `meshcore.db.migration-in-progress`, puis démarrez-la. Revenez à la 4.18.3, la dernière 4.18, et choisissez la copie par son numéro de schéma (`schema95` pour la 4.18.3, `schema87` pour les 4.18.0 à 4.18.2), pas par la version de son nom. Désactivez les mises à jour automatiques dans les **Réglages → Mises à jour** de la 4.18, et bloquez le paquet (`sudo apt-mark hold meshloom`, ou `sudo dnf versionlock add meshloom`) pour que rien ne vous remette en 5.0. Les commandes pour la copie sont dans [Revenir à une version plus ancienne](/docs/deep/install-paths/#revenir-à-une-version-plus-ancienne), qui dit aussi ce qu’il faut faire ensuite sur une machine passée par `get.meshloom.app/next`.

Ne lancez jamais le script d’installation de la 4.18 sur une machine en 5.0 ou en pré-version. Il ne les connaît pas, et peut démarrer la 4.18 sur une base qu’elle ne sait pas lire.

Sur Home Assistant, restaurez la sauvegarde que Home Assistant propose de faire avant une mise à jour (laissez cette option cochée quand vous mettez à jour) : la restauration ramène l’ancienne base.

Une copie téléchargée en 4.18 avant la mise à jour convient aussi, si la copie automatique a disparu : remettez-la à la place de `meshcore.db` de la même façon. Une copie téléchargée en 5.0 ne convient pas.
