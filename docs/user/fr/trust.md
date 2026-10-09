---
title: Un réseau de confiance
description: Pas de comptes, des bots en Python, et pourquoi l’instance reste sur un réseau que vous connaissez.
level: start
order: 6
---

Meshloom part d’un postulat simple : il tourne sur un réseau dont vous connaissez les occupants. Ce n’est pas un oubli, c’est le modèle. Autant savoir ce qu’il implique.

## Il n’y a pas de comptes

Pas de comptes utilisateurs, pas de sessions, pas de rôles, pas de permissions par fonction. Quiconque atteint l’adresse du serveur sur le port `8000` obtient l’interface complète, sans rien à saisir.

Cela veut dire : lire tout l’historique des messages directs et des salons, écrire au nom de votre nœud, changer le nom et les paramètres radio, supprimer des contacts, purger la base. Il n’y a aucun niveau intermédiaire entre « ne peut pas ouvrir la page » et « contrôle tout ».

Le serveur n’applique pas non plus de restriction d’origine pour les requêtes : n’importe quelle page web peut appeler son API. C’est délibéré — cela permet d’ouvrir l’interface depuis n’importe quel appareil du réseau sans configuration — et cela suppose le même réseau de confiance.

Tant que les bots sont actifs et qu’aucun mot de passe n’est posé, l’interface affiche un avertissement qui rappelle cette posture, jusqu’à ce que vous en preniez acte. Il n’est pas décoratif.

## Les bots exécutent du code

Meshloom peut déclencher des bots : de petits programmes qui réagissent aux messages reçus. Ils sont écrits en Python et exécutés tels quels sur la machine, sans bac à sable ni liste d’instructions autorisées.

La conséquence est directe : **toute personne capable d’atteindre Meshloom peut faire exécuter du code arbitraire sur la machine qui l’héberge.** Pas seulement dans l’application — sur la machine, avec les droits du serveur. C’est une fonctionnalité assumée, pensée pour l’automatisation, et c’est aussi le point le plus sensible de l’installation.

Deux garde-fous :

- Les bots sont **activés par défaut**. Seul le paquet Linux les coupe (`MESHCORE_DISABLE_BOTS=true` dans `/etc/meshloom/meshloom.env`) ; l’installeur ne pose aucune question à leur sujet, et les installations Docker les laissent actifs.
- La variable d’environnement `MESHCORE_DISABLE_BOTS=true` coupe le système de bots au démarrage. Aucun bot ne s’exécute, les réglages correspondants sont refusés, et l’interface affiche la fonction comme désactivée.

Si l’instance est accessible à des personnes que vous ne connaissez pas toutes, gardez les bots coupés.

## Le mot de passe optionnel

Meshloom peut demander un identifiant et un mot de passe avant d’ouvrir quoi que ce soit. Le script d’installation ne le met pas en place ; cela se règle avec deux variables d’environnement, qui vont toujours ensemble :

```
MESHCORE_BASIC_AUTH_USERNAME
MESHCORE_BASIC_AUTH_PASSWORD
```

Il s’agit d’un accès partagé unique, pas de comptes : un seul identifiant pour tout le monde, et celui qui l’a a tout. C’est un portail grossier, utile pour éviter qu’un appareil de passage sur le réseau tombe sur l’interface par hasard. Ce n’est pas un modèle d’autorisation.

Et cela **suppose HTTPS**. En HTTP simple, l’identifiant et le mot de passe circulent en clair à chaque requête sur le réseau. Mettre en place un certificat, même auto-signé, est décrit dans [HTTPS](/docs/deep/https/).

## En pratique

Quelques règles qui évitent l’essentiel des ennuis :

- **Ne pas exposer le port `8000` sur internet.** Pas de redirection de port sur la box. Pour un accès à distance, un VPN vers le réseau local est la bonne réponse.
- Sur un réseau partagé — colocation, bureau, réseau invité — activer le mot de passe et garder les bots coupés.
- Se souvenir que la clé privée de la radio est communiquée au serveur pour déchiffrer les messages directs. Elle n’est gardée qu’en mémoire, et son export par l’API est désactivé sauf activation explicite. Mais une machine compromise reste une machine compromise.
- Traiter les salons comme ce qu’ils sont. La clé est l’unique accès : la donner, c’est donner l’historique à venir et la capacité d’écrire.

Le détail des réglages de sécurité, du certificat auto-signé et des variables associées est dans [Sécurité](/docs/deep/security/).
