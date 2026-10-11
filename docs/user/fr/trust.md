---
title: Un réseau de confiance
description: Pas de comptes, des bots en Python, et pourquoi Meshloom a sa place sur un réseau que vous connaissez.
level: start
order: 6
---

Meshloom suppose qu’il tourne sur un réseau dont vous connaissez les utilisateurs. Ce n’est pas un oubli, c’est le modèle. Autant comprendre ce que cela implique.

## Il n’y a pas de comptes

Il n’y a ni comptes utilisateurs, ni sessions, ni rôles, ni permissions par fonction. Quiconque atteint le serveur sur le port `8000` obtient l’interface complète sans rien saisir.

Cela veut dire lire tout l’historique des messages directs et des canaux, écrire au nom de votre nœud, changer son nom et ses réglages radio, supprimer des contacts et vider la base de données. Il n’y a pas d’intermédiaire entre « ne peut pas ouvrir la page » et « contrôle tout ».

Le serveur accepte aussi les requêtes venant de n’importe quelle page web. C’est voulu : cela permet d’ouvrir l’interface depuis n’importe quel appareil du réseau sans configuration supplémentaire. Cela repose sur ce même réseau de confiance.

## Les bots exécutent du code

Meshloom sait lancer des bots : de petits programmes qui réagissent aux messages reçus. Ils s’écrivent en Python, et Meshloom les exécute tels quels, sans bac à sable ni liste d’instructions autorisées.

La conséquence est directe : **quiconque atteint Meshloom peut faire exécuter n’importe quel code à la machine hôte.** Pas seulement dans l’application : sur la machine, avec les droits du serveur. C’est une fonction d’automatisation voulue, et c’est aussi la partie la plus sensible d’une installation.

Tant que les bots sont activés et qu’aucun mot de passe n’est défini, Meshloom ouvre une fenêtre d’avertissement (« L’exécution non protégée de bots est activée »). Vous pouvez y désactiver les bots jusqu’au prochain redémarrage, ou cocher la reconnaissance et la fermer pour ce navigateur. Ce n’est pas décoratif.

Deux protections existent :

- Les bots sont **activés par défaut**, sauf dans le paquet Linux et l’image Raspberry Pi, qui les coupent (`MESHCORE_DISABLE_BOTS=true` dans `/etc/meshloom/meshloom.env`). L’installeur ne pose aucune question à ce sujet. Les installations Docker et l’add-on Home Assistant gardent les bots actifs (l’add-on a une option `disable_bots`).
- Définir `MESHCORE_DISABLE_BOTS=true` coupe tout le système de bots au démarrage. Aucun bot ne tourne, les modifications de bots sont refusées, et l’interface présente la fonction comme désactivée.

Si des personnes que vous ne connaissez pas toutes peuvent atteindre l’instance, gardez les bots désactivés.

## Le mot de passe facultatif

Meshloom peut demander un nom d’utilisateur et un mot de passe avant d’afficher quoi que ce soit. L’installeur ne le met pas en place. On le configure avec deux variables d’environnement, toujours ensemble :

```
MESHCORE_BASIC_AUTH_USERNAME
MESHCORE_BASIC_AUTH_PASSWORD
```

C’est un identifiant partagé unique, pas des comptes : un seul accès pour tout le monde, et celui qui l’a possède tout. C’est une barrière simple, utile pour empêcher un appareil quelconque du réseau d’ouvrir l’interface par accident. Ce n’est pas un système de permissions.

Il **exige aussi HTTPS**. En HTTP simple, le nom d’utilisateur et le mot de passe circulent en clair à chaque requête. [HTTPS](/docs/deep/https/) explique comment mettre en place un certificat, même auto-signé.

## En pratique

Quelques règles évitent la plupart des ennuis :

- **N’exposez pas le port `8000` à Internet.** Ne le redirigez pas depuis votre box. Pour un accès à distance, utilisez un VPN vers votre réseau domestique ou professionnel.
- Sur un réseau partagé (colocation, bureau, réseau invité), définissez le mot de passe et gardez les bots désactivés.
- Rappelez-vous que la clé privée de la radio est donnée au serveur pour qu’il déchiffre les messages directs. Elle reste en mémoire uniquement, et la relire par l’API est désactivé tant que vous ne l’activez pas. Mais une machine compromise reste une machine compromise.
- Si vous activez **Réglages > Proxy** pour partager votre radio avec d’autres applications, sachez qu’il n’a aucun identifiant, et que l’accès Basic ne le couvre pas.
- Traitez les canaux pour ce qu’ils sont. La clé est le seul accès : la donner à quelqu’un lui permet d’y lire et d’y écrire.

Les détails des réglages de sécurité, des certificats auto-signés et des variables associées sont dans [Sécurité](/docs/deep/security/).
