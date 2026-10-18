---
title: Isolation des plugins
description: Comment Meshloom tient un plugin à l’écart de votre base de données et du réseau, et comment vérifier le niveau de votre installation.
level: deep
order: 22
---

Meshloom 5 exécute chaque plugin dans un processus séparé, et le système d’exploitation dresse des murs autour de ce processus. Leur solidité dépend de la façon dont vous avez installé Meshloom. Le niveau est choisi au démarrage du serveur, écrit dans le journal du serveur et indiqué par `/api/health` dans `plugins_isolation`.

## Les trois niveaux

**Isolé par bubblewrap** (`bwrap`). Le paquet Linux, l’image Raspberry Pi et une installation depuis les sources sous Linux utilisent [bubblewrap](https://github.com/containers/bubblewrap). Le plugin ne voit que ses propres fichiers, le Python qui l’exécute et un dossier de travail privé. Il ne voit ni la base de données, ni le reste des fichiers de Meshloom, ni la mémoire du serveur, et il n’a aucun accès réseau : tout ce qu’il demande passe par Meshloom, qui vérifie les permissions du plugin. Arrêter le plugin arrête tout ce qu’il a lancé.

**Utilisateur dédié** (`setuid`). L’image Docker et l’add-on Home Assistant exécutent Meshloom en root, et Docker n’autorise pas bubblewrap. Les plugins tournent sous un compte séparé, `meshloom-plugin`, qui ne peut ni ouvrir la base de données ni lire la mémoire du serveur. Pour que ce soit vrai, Meshloom resserre au démarrage les droits de son dossier de données : le dossier passe en `0711` et ses fichiers en `0600` (lisibles par root seulement) ; les dossiers des plugins restent lisibles. Si vous ouvrez la base depuis l’hôte avec un autre compte, faites-le en root. À ce niveau, un plugin qui contournerait les garde-fous internes de Meshloom pourrait encore atteindre le réseau.

**Hooks seulement** (`none`). Aucun des deux n’est disponible : il ne reste que les garde-fous de Meshloom à l’intérieur du processus du plugin. Ils arrêtent les erreurs de bonne foi, pas un attaquant déterminé : un plugin hostile pourrait lire la base de données et les fichiers de Meshloom. Le journal du serveur le signale par un avertissement à chaque démarrage. Cela arrive quand :

- bubblewrap n’est pas installé (le paquet Linux le recommande ; `sudo apt install bubblewrap` ou `sudo dnf install bubblewrap` l’ajoute) ;
- le système refuse les espaces de noms utilisateur dont bubblewrap a besoin (Ubuntu 24.04 et suivantes les restreignent ; un noyau durci peut les désactiver) ;
- l’image Docker tourne avec `MESHLOOM_RUN_AS_USER`, qui renonce aux droits nécessaires pour passer au compte des plugins ;
- Meshloom tourne sous macOS ou Windows.

## Vérifier votre niveau

Cherchez `Plugin isolation` dans le journal du serveur au démarrage, ou ouvrez `/api/health` une fois connecté et lisez `plugins_isolation` : `bwrap`, `setuid` ou `none`. Après avoir installé bubblewrap, redémarrez Meshloom pour qu’il vérifie à nouveau.

Franchir ces murs est une faille de sécurité : signalez-la comme l’indique la politique de sécurité du projet. Les plugins que vous installez restent du code auquel vous choisissez de faire confiance ; les murs limitent ce qu’une erreur ou une mauvaise mise à jour peut atteindre.
