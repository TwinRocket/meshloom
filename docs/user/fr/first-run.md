---
title: Premier lancement
description: Ouvrir l’interface, vérifier la radio, nommer le nœud, faire le tour de l’écran.
level: start
order: 3
---

Le serveur tourne. Reste à ouvrir la page, lier la radio, puis regarder ce qui s’y passe.

```
http://127.0.0.1:8000
```

Depuis un autre appareil du même réseau, la même adresse avec l’IP de la machine et le port `8000`.

## Lier la radio

Le transport — USB, TCP ou Bluetooth — se choisit dans l’interface, pas à l’installation. Tant qu’il n’est pas défini, la barre d’état reste en pause et un clic sur **Connecter** ouvre **Réglages > Radio**.

Choisissez le transport, renseignez le port, l’hôte, ou l’adresse BLE et le PIN, puis appliquez. Les détails sont dans [Transports radio](/docs/deep/transports/).

La barre passe ensuite par **Radio en connexion**, **Radio en initialisation**, puis **Radio OK** quand la liaison est établie et la synchronisation terminée. La première connexion prend un moment : le serveur lit la configuration de l’appareil, récupère ses contacts et ses salons, remet l’horloge à l’heure.

Si la barre affiche **Radio déconnectée**, un bouton **Reconnecter** est disponible à côté. Un serveur qui ne trouve pas la radio réessaie de lui-même toutes les quelques secondes, donc un câble rebranché ou une radio rallumée finit par être vu sans rien cliquer. Si l’état ne bouge pas : mauvais port série, mauvaise adresse IP, PIN Bluetooth refusé. Voir [Dépannage](/docs/deep/troubleshooting/).

## Première liaison d’identité

Meshloom lie la base à la clé publique de la radio. Sur une installation neuve, c’est silencieux. Après une mise à jour d’une base qui existait déjà, un dialogue demande de confirmer.

- **Lier sans effacer** si c’est la même radio : contacts et messages restent.
- **Nouvelle radio** si c’est un autre appareil : contacts et messages mesh locaux sont effacés. Les salons et la config serveur restent.

« Clé précédente : inconnue » veut dire que cette base précède le lien d’identité, pas que la radio a changé. Si la nouvelle clé est bien la vôtre, liez sans effacer.

Si une autre radio est branchée, le dialogue affiche les deux préfixes de clé. Continuer sans effacer n’est alors pas proposé : adopter l’autre identité exige l’effacement.

La barre d’état affiche aussi le nom du nœud, sa clé publique — cliquer dessus la copie — et son niveau de batterie quand la radio le remonte.

## Nommer le nœud

Un nœud sans nom apparaît chez les autres sous les premiers caractères de sa clé publique. Autant lui en donner un.

**Réglages**, puis la section **Radio**, groupe **Identité**, champ **Nom de la radio**. Ce nom part dans chaque annonce et c’est ce que les autres verront dans leur liste de contacts. Court vaut mieux que long : la place dans un paquet radio est comptée.

La même section contient les paramètres radio : préréglages, fréquence, largeur de bande, et le reste. Ils doivent correspondre à ceux des nœuds voisins, sinon personne ne s’entend. Si le nœud reste muet alors que d’autres appareils sont actifs à proximité, ces réglages sont le premier endroit à regarder — et il vaut mieux savoir quelles valeurs sont en usage localement avant d’y toucher.

## Se signaler

Plus bas dans la même section, le groupe **Annonces et découverte**. Une annonce est le petit paquet par lequel un nœud dit qu’il existe, avec son nom et sa clé publique.

- **Envoyer une annonce** en envoie une tout de suite. La version *flood* se propage via les répéteurs et va donc loin. La version *zéro saut* reste locale et consomme beaucoup moins de temps d’antenne.
- **Intervalle d’annonce périodique** règle la répétition automatique. `0` désactive. Le minimum est d’une heure, et vingt-quatre heures ou plus est la valeur recommandée : une annonce trop fréquente occupe l’antenne pour tout le monde.

L’inverse fonctionne pareil. Les contacts n’ont pas à être saisis à la main : chaque annonce entendue crée ou met à jour un contact tout seul. Une liste vide au démarrage n’est pas un problème, juste un réseau qu’on n’a pas encore écouté assez longtemps.

## Le tour de l’écran

Sur un téléphone, la barre du bas propose quatre destinations : **Conversations**, **Carte**, **Outils** et **Réglages**. Sur un grand écran, un rail d’icônes à gauche reprend **Conversations** et la **Carte**, puis les outils ; **Réglages** reste en bas du rail.

La liste des conversations est rangée par sections : **Favoris**, **Canaux**, **Contacts**, **Répéteurs**, **Serveurs de salon**. Un salon nommé **Public** existe dès le départ ; c’est le salon par défaut de MeshCore, ouvert à tout le monde.

Les outils s’ouvrent depuis l’écran **Outils**, ou depuis une icône du rail :

- **Flux de paquets** — tout ce que la radio entend, déchiffrable ou non.
- **Journal de contrôle** — requêtes, réponses et données de groupe, à part des conversations.
- **Live** — les paquets posés sur une carte au moment où ils sont entendus.
- **Visualiseur mesh** — les chemins réellement empruntés par les paquets.
- **Trace** — un test de route vers un nœud précis.
- **Localisation RF** — une zone de couverture estimée, pas un point GPS.
- **Recherche de messages** — recherche plein texte dans tout l’historique.
- **Canaux découverts** — les salons hashtag entendus, à garder ou à refuser.
- **Test radio** — envoie un message de test et montre qui l’a entendu. Il n’est proposé que lorsque Meshloom Community est actif.

Le bouton **Ajouter canal/contact** ouvre la création d’une conversation : un contact par sa clé publique, un salon par sa clé, ou un salon hashtag par son nom.

Une install neuve affiche une bannière **Community** tant qu’un code IATA d’aéroport n’est pas enregistré dans **Réglages > Community**. On peut quitter Community depuis cet onglet. Voir [Meshloom Community](/docs/deep/community/).

**Réglages > Notifications** est l’endroit du Web Push (cet appareil, défauts, exceptions, sujet VAPID). Il n’y a plus d’alertes bureau dans l’onglet.

Pour envoyer quelque chose : [Messages](/docs/messages/).
