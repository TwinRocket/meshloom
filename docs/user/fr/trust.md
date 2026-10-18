---
title: Un réseau de confiance
description: Comptes et rôles, des bots en Python, et pourquoi Meshloom a toujours sa place sur un réseau que vous connaissez.
level: start
order: 6
---

Meshloom suppose qu’il tourne sur un réseau dont vous connaissez les utilisateurs. Ce n’est pas un oubli, c’est le modèle. Autant comprendre ce que cela implique.

## Comptes et rôles

À partir de Meshloom 5.0, rien ne s’affiche avant la connexion. La première fois, Meshloom vous demande de créer le compte administrateur ; ensuite chaque visiteur se connecte avec un compte, et le serveur vérifie le rôle du compte à chaque requête (voir [Récupérer l’accès](/docs/deep/access/#les-rôles)). L’administrateur peut tout faire. Un membre peut lire et envoyer des messages et modifier les contacts et les canaux. Un lecteur lit. Une borne est en lecture seule, pour un écran mural, et ne voit jamais les messages directs. Sous Home Assistant, c’est Home Assistant qui vous connecte.

Les comptes ne font pas de Meshloom un service que l’on peut laisser ouvert à des inconnus. Un administrateur peut lire tout l’historique des messages directs et des canaux, écrire au nom de votre nœud, changer les réglages et le réseau Wi-Fi de la radio, la redémarrer, vider la base de données et, plus bas, faire exécuter du code à la machine. Un membre peut écrire au nom de votre nœud. Traitez chaque compte comme une clé de l’installation, et ne donnez de compte qu’à des personnes de confiance.

Les autres sites ouverts dans le même navigateur ne peuvent pas lire les réponses de Meshloom : le serveur ne répond qu’à sa propre adresse, plus les sites que vous ajoutez dans **Réglages > Configuration locale > Origines autorisées** (vide par défaut). Ils ne peuvent pas non plus ouvrir le flux en direct, et Meshloom refuse les modifications qui viennent d’un autre site (voir [Sécurité](/docs/deep/security/#ce-qui-nexiste-pas)).

## Les bots exécutent du code

Meshloom sait lancer des bots : de petits programmes qui réagissent aux messages reçus. Ils s’écrivent en Python, et Meshloom les exécute tels quels, sans bac à sable ni liste d’instructions autorisées.

La conséquence est directe : **tout administrateur peut faire exécuter n’importe quel code à la machine hôte.** Pas seulement dans l’application : sur la machine, avec les droits du serveur. C’est une fonction d’automatisation voulue, et c’est aussi la partie la plus sensible d’une installation.

Tant que les bots sont activés et qu’aucun compte ne protège l’instance (depuis la 5.0, chaque page exige un compte connecté : vous ne la voyez normalement jamais), Meshloom ouvre une fenêtre d’avertissement (« L’exécution non protégée de bots est activée »). Vous pouvez y désactiver les bots jusqu’au prochain redémarrage, ou cocher la reconnaissance et la fermer pour ce navigateur. Ce n’est pas décoratif.

Deux protections existent :

- Les bots sont **activés par défaut**, sauf dans le paquet Linux et l’image Raspberry Pi, qui les coupent (`MESHCORE_DISABLE_BOTS=true` dans `/etc/meshloom/meshloom.env`). L’installeur ne pose aucune question à ce sujet. Les installations Docker et l’add-on Home Assistant gardent les bots actifs (l’add-on a une option `disable_bots`).
- Définir `MESHCORE_DISABLE_BOTS=true` coupe tout le système de bots au démarrage. Aucun bot ne tourne, les modifications de bots sont refusées, et l’interface présente la fonction comme désactivée.

Si des personnes que vous ne connaissez pas toutes peuvent atteindre l’instance, gardez les bots désactivés.

## Le mot de passe et HTTPS

Votre mot de passe ne protège l’interface que dans la mesure où le réseau entre vous et Meshloom le fait. En HTTP simple, le nom d’utilisateur et le mot de passe circulent en clair à chaque connexion. [HTTPS](/docs/deep/https/) explique comment mettre en place un certificat, même auto-signé.

Quand vous mettez à jour depuis la 4.x, l’identifiant Basic partagé (`MESHCORE_BASIC_AUTH_USERNAME` et `MESHCORE_BASIC_AUTH_PASSWORD`) devient le compte administrateur au premier démarrage, puis Meshloom ne lit plus ces variables (retirez-les). Les scripts qui l’envoient encore continuent de fonctionner jusqu’à la 5.2. Une instance sans mot de passe vous demande de créer l’administrateur ; si sa base existait avant la mise à jour, elle demande aussi un code d’installation imprimé dans le journal du serveur, pour que personne d’autre sur le réseau ne puisse se l’approprier avant vous. L’add-on Home Assistant ne demande rien par la barre latérale : Home Assistant vous connecte, et le premier utilisateur Home Assistant qui ouvre Meshloom en devient l’administrateur.

Sous l’ingress de Home Assistant, Meshloom fait confiance à l’identité que lui donne le Supervisor de Home Assistant, comme tout add-on : Home Assistant et ses add-ons se font confiance, donc un add-on à qui vous ne confieriez pas votre Home Assistant ne devrait pas tourner à côté de Meshloom non plus.

## En pratique

Quelques règles évitent la plupart des ennuis :

- **N’exposez pas le port `8000` à Internet.** Ne le redirigez pas depuis votre box. Pour un accès à distance, utilisez un VPN vers votre réseau domestique ou professionnel.
- Sur un réseau partagé (colocation, bureau, réseau invité), ne donnez de compte qu’à des personnes de confiance et gardez les bots désactivés.
- Rappelez-vous que la clé privée de la radio est donnée au serveur pour qu’il déchiffre les messages directs. Elle reste en mémoire uniquement, mais un administrateur peut la relire, par l’API et dans la sauvegarde complète. Une machine compromise reste une machine compromise.
- Le service optionnel de liens courts (**Réglages > Partage : liens et fichiers**) est désactivé par défaut et jamais contacté tant qu’un administrateur ne l’active pas. Il utilise une clé qui lui est propre, jamais celle de la radio, et les sauvegardes ne contiennent ni cette clé ni le consentement : une restauration n’active jamais le partage et donne toujours une clé neuve (voir [Liens courts](/docs/deep/share/)).
- Si vous activez **Réglages > Proxy** pour partager votre radio avec d’autres applications, sachez qu’il n’a aucun identifiant, et que l’accès Basic ne le couvre pas.
- Traitez les canaux pour ce qu’ils sont. La clé est le seul accès : la donner à quelqu’un lui permet d’y lire et d’y écrire.

Les détails des réglages de sécurité, des certificats auto-signés et des variables associées sont dans [Sécurité](/docs/deep/security/).
