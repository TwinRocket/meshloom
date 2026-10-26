---
title: Un réseau de confiance
description: Comptes et rôles, des extensions qui exécutent du code, et pourquoi Meshloom a toujours sa place sur un réseau que vous connaissez.
level: start
order: 6
---

Meshloom suppose qu’il tourne sur un réseau dont vous connaissez les utilisateurs. Ce n’est pas un oubli, c’est le modèle. Autant comprendre ce que cela implique.

## Comptes et rôles

À partir de Meshloom 5.0, rien ne s’affiche avant la connexion. La première fois, Meshloom vous demande de créer le compte administrateur ; ensuite chaque visiteur se connecte avec un compte, et le serveur vérifie le rôle du compte à chaque requête (voir [Comptes et accès](/docs/deep/access/#les-rôles)). L’administrateur peut tout faire. Un membre peut lire et envoyer des messages et modifier les contacts et les canaux. Un lecteur lit. Un kiosque est en lecture seule, pour un écran mural, et ne voit jamais les messages directs (voir [Écran kiosque](/docs/deep/access/#écran-kiosque)). Sous Home Assistant, c’est Home Assistant qui vous connecte.

Les comptes ne font pas de Meshloom un service que l’on peut laisser ouvert à des inconnus. Un administrateur peut lire tout l’historique des messages directs et des canaux, écrire au nom de votre nœud, changer les réglages et le réseau Wi-Fi de la radio, la redémarrer, vider la base de données et, plus bas, faire exécuter du code à la machine. Un membre peut écrire au nom de votre nœud. Traitez chaque compte comme une clé de l’installation, et ne donnez de compte qu’à des personnes de confiance.

Les autres sites ouverts dans le même navigateur ne peuvent pas lire les réponses de Meshloom : le serveur ne répond qu’à sa propre adresse, plus les sites que vous ajoutez dans **Réglages > Configuration locale > Origines autorisées** (vide par défaut). Ils ne peuvent pas non plus ouvrir le flux en direct, et Meshloom refuse les modifications qui viennent d’un autre site (voir [Sécurité](/docs/deep/security/#ce-que-les-comptes-protègent-et-ce-quils-ne-protègent-pas)).

## Les extensions exécutent du code

Meshloom sait lancer des extensions (plugins) : de petits programmes Python qui réagissent aux messages reçus et peuvent y répondre. Chacune tourne dans un processus à part, avec les seules permissions qu’un administrateur a acceptées en l’installant et, selon l’installation de Meshloom, des murs que le système d’exploitation dresse autour d’elle (voir [Isolation](/docs/deep/plugins/#isolation)). C’est bien plus strict que les bots Python de la 4.18 et avant, qui tournaient dans Meshloom sans aucune limite ; la 5.0.0 les a convertis en extensions qui restent éteintes tant qu’un administrateur ne les a pas relues (voir [Les bots de la 4.18](/docs/deep/plugins/#les-bots-de-la-418-et-avant)).

C’est quand même du code auquel vous choisissez de faire confiance. Là où le système n’offre aucun mur (isolation `none`, affichée dans **Réglages → Extensions**), une extension hostile pourrait lire les fichiers de Meshloom, et un administrateur peut en écrire une. **Tout administrateur peut donc faire exécuter du code à la machine hôte.** C’est une fonction d’automatisation voulue, et c’est aussi une partie sensible d’une installation.

Deux protections existent :

- Rien ne tourne par défaut : une extension doit être ajoutée par un administrateur, qui voit d’abord ce qu’elle peut faire. L’installeur ne pose aucune question à ce sujet.
- Définir `MESHCORE_DISABLE_PLUGINS=true` verrouille tout le système d’extensions au démarrage. Aucune extension ne peut être installée, modifiée ni démarrée, et l’interface les montre comme coupées par le serveur (l’add-on a une option `disable_plugins`).

Si des personnes que vous ne connaissez pas toutes peuvent atteindre l’instance, verrouillez les extensions.

Qui peut en installer, en configurer ou en lancer une dépend du rôle du compte, pas du fait d’être sur votre réseau : seul un administrateur le peut, un membre voit la liste en lecture seule, et les comptes lecteur et kiosque ne voient rien. Chaque modification est inscrite au journal d’audit avec son auteur : vous savez après coup qui a installé quoi. Quand l’une d’elles se comporte mal, **Mettre en pause toutes les extensions** les arrête toutes d’un coup sans perdre leurs réglages (voir [Extensions](/docs/deep/plugins/#mettre-en-pause-toutes-les-extensions)).

## Le mot de passe et HTTPS

Votre mot de passe ne protège l’interface que dans la mesure où le réseau entre vous et Meshloom le fait. En HTTP simple, le nom d’utilisateur et le mot de passe circulent en clair à chaque connexion. [HTTPS](/docs/deep/https/) explique comment mettre en place un certificat, même auto-signé.

Quand vous mettez à jour depuis la 4.x, l’identifiant Basic partagé (`MESHCORE_BASIC_AUTH_USERNAME` et `MESHCORE_BASIC_AUTH_PASSWORD`) devient le compte administrateur au premier démarrage, puis Meshloom ne lit plus ces variables (retirez-les). Les scripts qui l’envoient encore continuent de fonctionner jusqu’à la 5.2 ([La connexion Basic de la 4.x](/docs/deep/access/#la-connexion-basic-de-la-4x)). Une instance sans mot de passe vous demande de créer l’administrateur ; si sa base existait avant la mise à jour, elle demande aussi un code d’installation imprimé dans le journal du serveur, pour que personne d’autre sur le réseau ne puisse se l’approprier avant vous. L’add-on Home Assistant ne demande rien par la barre latérale : Home Assistant vous connecte, et le premier utilisateur Home Assistant qui ouvre Meshloom en devient l’administrateur. Son port 8000 n’est pas publié par défaut ; pour joindre Meshloom hors de Home Assistant, et y créer un compte, donnez un port d’hôte à `8000/tcp` dans le panneau **Réseau** de l’add-on.

Sous l’ingress de Home Assistant, Meshloom fait confiance à l’identité que lui donne le Supervisor de Home Assistant, comme tout add-on : Home Assistant et ses add-ons se font confiance, donc un add-on à qui vous ne confieriez pas votre Home Assistant ne devrait pas tourner à côté de Meshloom non plus.

## En pratique

Quelques règles évitent la plupart des ennuis :

- **N’exposez pas le port `8000` à Internet.** Ne le redirigez pas depuis votre box. Pour un accès à distance, utilisez un VPN vers votre réseau domestique ou professionnel.
- Sur un réseau partagé (colocation, bureau, réseau invité), ne donnez de compte qu’à des personnes de confiance et verrouillez les extensions (`MESHCORE_DISABLE_PLUGINS=true`).
- Rappelez-vous que la clé privée de la radio est donnée au serveur pour qu’il déchiffre les messages directs. Elle reste en mémoire uniquement, mais un administrateur peut la relire, par l’API et dans la sauvegarde complète. Une machine compromise reste une machine compromise.
- Le service de partage optionnel (liens courts et photos, **Réglages > Partage : liens et fichiers**) est désactivé par défaut et jamais contacté tant qu’un administrateur n’a pas accepté ses conditions. Une fois activé, c’est le seul endroit où votre Meshloom envoie quelque chose à un service exploité par le projet : l’adresse que vous raccourcissez ou la photo que vous partagez, l’adresse IP de ce Meshloom et une clé pseudonyme qui lui est propre, jamais celle de la radio. Le service conserve les liens courts 365 jours et les fichiers 30 jours. Une photo **publique** peut être vue par quiconque a son lien, et une photo envoyée sur le canal Public ou dans un canal hashtag ne peut jamais être privée. Une photo **chiffrée** (canaux privés et messages directs seulement) cache son contenu au service et aux personnes qui n’ont que le lien, mais elle n’a pas de confidentialité persistante (forward secrecy) et quiconque détient la clé de la conversation peut la lire. Les sauvegardes ne contiennent ni la clé ni le consentement : une restauration n’active jamais le partage et donne toujours une clé neuve (voir [Liens courts et photos](/docs/deep/share/)).
- Le store en ligne (à venir) a son propre interrupteur, désactivé par défaut et distinct de Community : tant qu’un administrateur ne l’active pas, Meshloom ne contacte aucun serveur du store, et une sauvegarde ne contient jamais ce choix (voir [Ce qui quitte votre instance](/docs/deep/plugins/#ce-qui-quitte-votre-instance)).
- Si vous activez **Réglages > Proxy** pour partager votre radio avec d’autres applications, sachez qu’il n’a aucune connexion : les comptes ne le couvrent pas, et quiconque atteint son port (5051 par défaut) peut utiliser votre radio et envoyer au nom de votre nœud. C’est une décision, car les applications qui parlent ce protocole ne savent pas se connecter. Gardez ce port à l’intérieur de votre réseau.
- Traitez les canaux pour ce qu’ils sont. La clé est le seul accès : la donner à quelqu’un lui permet d’y lire et d’y écrire.

Les détails des réglages de sécurité, des certificats auto-signés et des variables associées sont dans [Sécurité](/docs/deep/security/).
