## [Unreleased]

This is about the hours after 4.12. You type a message at a desk and Enter should send it. You open a repeater's neighbours and want more than a name. You have a Pi 1, and the installer should not hand you a package that dies on the first instruction.

### What's new

- **Enter sends, on a desk.** 4.11 made Enter start a new line for everyone, which is right on a phone, a tablet and a foldable. A keyboard is a different thing. Enter now sends there; Shift+Enter is still the line break. Touch keeps the newline.
- **A neighbour on the map can say more than its name.** Detailed display puts SNR, distance and GPS on the pin you open. Permanent display leaves a label on every mapped neighbour so you are not hunting the same marker twice. The chip stays as wide as the name: a wrapping tooltip used to collapse to a sliver.

### Fixed

- **A Pi 1 and the original Zero are not offered the 32-bit package.** Raspberry Pi OS calls those boards armhf too, so mapping `armv6l` next to `armv7l` looked right. The package is built for armv7. It would install, then die on an illegal instruction, which is worse than the source install they now get.
- **Community stops asking Stats for samples when the hour is spent.** Stats allows thirty sample upserts per hour per key. A 429 used to look like any other failed call, so the next packet tried again. It now waits the hour out.

### Notes

The Raspberry Pi page now answers the question people actually arrive with: which board takes which route. The Pi 2 is in that table — 32-bit only, so the flashed image is not its path — and the page says what is known about a Pi 2 or a Pi 3 rather than inventing a number nobody has measured. A correction there also reaches [meshloom.app](https://meshloom.app): a push that touches the user docs asks that site to rebuild, instead of waiting for someone to redeploy it by hand.

---

### Français

Cette tranche parle des heures après la 4.12. Vous tapez un message au bureau et Entrée doit l'envoyer. Vous ouvrez les voisins d'un répéteur et voulez plus qu'un nom. Vous avez un Pi 1, et l'installateur ne doit pas vous tendre un paquet qui meurt à la première instruction.

#### Quoi de neuf

- **Entrée envoie, au bureau.** La 4.11 faisait commencer une nouvelle ligne à tout le monde, ce qui est juste sur un téléphone, une tablette et un pliable. Un clavier, c'est autre chose. Entrée envoie désormais ; Maj+Entrée reste le retour à la ligne. Le tactile garde le saut de ligne.
- **Un voisin sur la carte peut dire plus que son nom.** L'affichage détaillé pose le SNR, la distance et le GPS sur l'épingle que vous ouvrez. L'affichage permanent laisse un libellé sur chaque voisin cartographié, pour ne pas chercher le même marqueur deux fois. La pastille reste aussi large que le nom : un tooltip qui passait à la ligne se réduisait à une fente.

#### Corrections

- **Un Pi 1 et le Zero d'origine ne se voient plus proposer le paquet 32 bits.** Raspberry Pi OS appelle aussi ces cartes armhf, donc ranger `armv6l` à côté de `armv7l` avait l'air juste. Le paquet est construit pour armv7. Il s'installait, puis mourait sur une instruction illégale, ce qui est pire que l'install source qu'ils reçoivent maintenant.
- **Community arrête de demander des échantillons à Stats quand l'heure est épuisée.** Stats autorise trente envois d'échantillon par heure et par clé. Un 429 ressemblait à n'importe quel autre échec, donc le paquet suivant réessayait. Il attend désormais la fin de l'heure.

#### Remarques

La page Raspberry Pi répond maintenant à la question avec laquelle on arrive vraiment : quelle carte, quelle voie. Le Pi 2 est dans ce tableau — 32 bits seulement, donc l'image à flasher n'est pas son chemin — et la page dit ce qui est connu d'un Pi 2 ou d'un Pi 3 plutôt que d'inventer un chiffre que personne n'a mesuré. Une correction là-bas atteint aussi [meshloom.app](https://meshloom.app) : un push qui touche la doc utilisateur demande à ce site de se reconstruire, au lieu d'attendre que quelqu'un le redéploie à la main.

---

## [4.12.2] - 2026-09-17

The armhf package 4.12.1 promised is actually published now.

### Fixed

- **The 32-bit Raspberry Pi package builds.** It is assembled in an emulated armv7 container, which had a compiler but not `make`, so the dependency that builds libsodium stopped it. Nothing else was wrong with it: the rest of 4.12.1 went out as intended while that one job failed on its own.
- **Signing in again after a proxy session expires.** Behind Cloudflare Access or a similar front, an expired session answered every request with a redirect to a login page on another origin. An open page never sees that page, so the interface stayed up with every call failing and no way to sign in. It now notices and offers to reload, which is the only thing such a proxy responds to.

---

### Français

Le paquet armhf annoncé par la 4.12.1 est réellement publié.

#### Corrections

- **Le paquet pour Raspberry Pi 32 bits se construit.** Il est assemblé dans un conteneur armv7 émulé, qui disposait d'un compilateur mais pas de `make`, ce qui bloquait la dépendance chargée de compiler libsodium. Rien d'autre n'était en cause : le reste de la 4.12.1 est sorti normalement pendant que ce seul job échouait.
- **Se reconnecter après l'expiration d'une session au niveau du proxy.** Derrière Cloudflare Access ou équivalent, une session expirée répondait à chaque requête par une redirection vers une page de connexion sur une autre origine. Une page déjà ouverte ne la voit jamais : l'interface restait affichée, tous les appels échouaient, et rien ne permettait de se reconnecter. Elle le détecte désormais et propose de recharger, seule action à laquelle un tel proxy réagit.

---

## [4.12.1] - 2026-09-17

A Raspberry Pi running the 32-bit Raspberry Pi OS can install Meshloom again, and this time from a package.

### Fixed

- **A 32-bit Raspberry Pi installs.** The apt repository held amd64 and arm64, and the installer offered it to any machine with apt without checking whether it carried anything for that architecture. A Pi 3 or a Pi 4 running the 32-bit system ended up with a repository that could not serve it, apt found no candidate, and the install stopped there. It now installs from an armhf package of its own, and the documentation no longer says to reflash in 64-bit.
- **The repository cannot disagree with what it holds.** Its architecture list was written by hand in three places, and one of them missing an entry is exactly what caused the above. It is now read from the packages themselves, and the installer reads it from the repository rather than keeping its own copy.
- **The Raspberry Pi image builds without the Pages signing key**, which was fetched at bake time and failed the build when it was missing.

### Notes

The armhf package is built under emulation, which is slow, so it is attached to the release shortly after the others rather than holding them up. The 64-bit image is still the recommendation for a Pi that can run it: it is faster.

---

### Français

Un Raspberry Pi sous Raspberry Pi OS 32 bits peut de nouveau installer Meshloom, et cette fois depuis un paquet.

#### Corrections

- **Un Raspberry Pi 32 bits s'installe.** Le dépôt apt ne contenait que amd64 et arm64, et l'installeur le proposait à toute machine disposant d'apt sans vérifier qu'il servait cette architecture. Un Pi 3 ou un Pi 4 en 32 bits se retrouvait avec un dépôt incapable de le servir, apt ne trouvait aucun candidat, et l'installation s'arrêtait là. Il s'installe désormais depuis un paquet armhf, et la documentation ne dit plus de reflasher en 64 bits.
- **Le dépôt ne peut plus contredire ce qu'il contient.** Sa liste d'architectures était écrite à la main à trois endroits, et c'est précisément l'oubli de l'une d'elles qui a causé le point ci-dessus. Elle est maintenant lue depuis les paquets eux-mêmes, et l'installeur la lit dans le dépôt au lieu d'en garder une copie.
- **L'image Raspberry Pi se construit sans la clé de signature Pages**, qui était téléchargée au moment de la cuisson et faisait échouer la construction quand elle manquait.

#### Remarques

Le paquet armhf est construit sous émulation, donc lentement : il est attaché à la release peu après les autres plutôt que de les retarder. L'image 64 bits reste la recommandation pour un Pi qui peut la faire tourner, elle est plus rapide.

---

## [4.12.0] - 2026-09-17

This release is about putting Meshloom on a Raspberry Pi and keeping a node current from the app. You flash a card that already has Meshloom on it, you open Settings → About when a newer version is out, and the node installs Meshloom — not the rest of the operating system.

### What's new
- **A Raspberry Pi image you can flash.** A Pi used to mean installing the OS, then Meshloom, then hoping the first boot had a network. `meshloom-rpi-lite-arm64.img.xz` is a Lite 64-bit card with Meshloom already on it, for the Pi 3 and later including the Zero 2 W. The first boot works offline. Wi-Fi, the user and SSH are set in Raspberry Pi Imager 2.0.6 or newer. A screen, if you have one, shows `http://meshloom.local:8000`.
- **About can install the next release.** 4.8 told you a newer version was out. This one can put it on the node, when the apply helper is there — a package install, or Docker that the installer set up. Automatic updates stay off until you turn them on. That path upgrades Meshloom only. It never runs a silent `apt upgrade` of the OS.
- **The other installs keep an honest recipe.** A Home Assistant add-on still updates in Home Assistant. A container or a source tree without a helper still says to update Meshloom by hand. Re-running the installer will put the helper on if it was missing.

### Upgrading

Package and installer-managed Docker nodes pick this up on the next upgrade. An older node that re-runs the one-liner gets the helper if it was missing. Automatic updates stay off. The Pi image is a new file on the GitHub release; flashing it is how you start from a card, not how you upgrade a node that already runs.

---

### Français

Cette version est consacrée à installer Meshloom sur un Raspberry Pi, et à le tenir à jour depuis l'application. Vous flashez une carte qui embarque déjà Meshloom, vous ouvrez Réglages → À propos quand une version plus récente est sortie, et le nœud installe Meshloom — pas le reste du système.

#### Quoi de neuf
- **Une image Raspberry Pi à flasher.** Un Pi voulait dire installer le système, puis Meshloom, puis espérer que le premier démarrage ait un réseau. `meshloom-rpi-lite-arm64.img.xz` est une carte Lite 64-bit avec Meshloom déjà dessus, pour le Pi 3 et plus, Zero 2 W compris. Le premier démarrage marche hors ligne. Le Wi-Fi, l'utilisateur et SSH se règlent dans Raspberry Pi Imager 2.0.6 ou plus récent. Un écran, s'il y en a un, affiche `http://meshloom.local:8000`.
- **À propos peut installer la prochaine release.** La 4.8 vous disait qu'une version plus récente était sortie. Celle-ci peut la poser sur le nœud, quand le helper d'application est là — une install paquet, ou un Docker que l'installateur a mis en place. Les mises à jour automatiques restent éteintes tant que vous ne les activez pas. Ce chemin ne met à jour que Meshloom. Il ne lance jamais un `apt upgrade` silencieux du système.
- **Les autres installs gardent une recette honnête.** Un add-on Home Assistant se met toujours à jour dans Home Assistant. Un conteneur ou une arborescence source sans helper dit encore de mettre à jour Meshloom à la main. Relancer l'installateur pose le helper s'il manquait.

#### Mise à jour

Les nœuds paquet et Docker gérés par l'installateur le reçoivent à la prochaine mise à jour. Un nœud plus ancien qui relance le one-liner récupère le helper s'il manquait. Les mises à jour automatiques restent éteintes. L'image Pi est un nouveau fichier sur la release GitHub ; la flasher, c'est partir d'une carte, pas mettre à jour un nœud qui tourne déjà.

---

## [4.11.0] - 2026-09-17

This release is about living with Meshloom on a foldable, a tablet and a desk. The list and the chat share the screen the way two apps do: you drag the split, you close it when you need the room, and it remembers how you left it. Conversations themselves are a little quieter, and the Meshloom mark finally has a home on the rail.

### What's new
- **The conversation list folds and moves.** A fixed column used to crush a chat or a repeater dashboard on a foldable. The split is now draggable, with a grip in the middle so you can see it is meant to be moved, and you can close the list when you want the whole width. Tablet and desktop each remember their own width, so opening the same instance on a narrow window does not steal the desk layout.
- **Favourites stay in reach.** They wrap onto another row instead of hiding in a sideways scroll, and a chevron puts them away when you want the list back.
- **Radio status lives in one place.** The little "Radio OK" pill in the header said the same thing as the one at the bottom of the rail, and neither said enough. The rail tile now names the transport, and the dialog is where you advertise — flood to tell the whole network you are here, zero-hop for the neighbours in range.
- **Compose is one menu, not three buttons.** Emoji, GIFs and a location sit behind one control, with tabs and a search for emoji the way GIFs already had one. The field just says Message. Send keeps the keyboard open; Enter starts a new line. A message's menu can copy the text.
- **Avatars read as initials.** A hashtag no longer eats the first letter (`#meshloom` is Me, Public is Pu). Two even letters, not a chopped word.
- **An expanded listener on the map keeps its path to itself.** Opening one observer used to draw every other observer's hops at the same time. Collapse, and the whole neighbourhood comes back.
- **The Meshloom mark sits at the top of the desktop rail.** It opens meshloom.app in a new tab, with room beneath it so a reach for Chat cannot miss. Chat is still Chat.

### Upgrading

Nothing to do. The split you set is stored in the browser; an older width is reused until you move it on that screen.

---

### Français

Cette version est consacrée à la vie avec Meshloom sur un pliable, une tablette et un bureau. La liste et la conversation se partagent l'écran comme deux applications : vous déplacez la séparation, vous la fermez quand vous avez besoin de la place, et elle se souvient de comment vous l'avez laissée. Les discussions elles-mêmes sont un peu plus calmes, et le logo Meshloom a enfin une place sur le rail.

#### Quoi de neuf
- **La liste des conversations se plie et se déplace.** Une colonne fixe écrasait un chat ou un tableau de répéteur sur un pliable. La séparation se tire désormais, avec une prise au milieu pour qu'on voie qu'elle est faite pour bouger, et vous pouvez fermer la liste quand vous voulez toute la largeur. Tablette et bureau retiennent chacun leur largeur, si bien qu'ouvrir la même instance dans une fenêtre étroite n'emprunte pas la disposition du bureau.
- **Les favoris restent à portée.** Ils passent sur une autre ligne au lieu de se cacher dans un défilement latéral, et un chevron les range quand vous voulez retrouver la liste.
- **L'état radio habite un seul endroit.** La pastille « Radio OK » du bandeau disait la même chose que celle en bas du rail, et ni l'une ni l'autre n'en disait assez. La tuile du rail nomme maintenant le transport, et c'est dans la fenêtre que vous annoncez — flood pour dire à tout le réseau que vous êtes là, zéro saut pour les voisins à portée.
- **Composer, c'est un menu, plus trois boutons.** Emoji, GIF et un lieu se tiennent derrière une seule commande, avec des onglets et une recherche d'emoji comme les GIF en avaient déjà une. Le champ dit simplement Message. Envoyer garde le clavier ouvert ; Entrée commence une nouvelle ligne. Le menu d'un message peut copier le texte.
- **Les avatars se lisent comme des initiales.** Un dièse ne mange plus la première lettre (`#meshloom` donne Me, Public donne Pu). Deux lettres égales, pas un mot coupé.
- **Un écoutant déplié sur la carte garde son chemin pour lui.** Ouvrir un observateur dessinait en même temps les sauts de tous les autres. Refermez, et tout le voisinage revient.
- **Le logo Meshloom siège en haut du rail desktop.** Il ouvre meshloom.app dans un nouvel onglet, avec de la place en dessous pour qu'un geste vers Discussions ne puisse pas le rater. Discussions reste Discussions.

#### Mise à jour
Rien à faire. La séparation que vous réglez est stockée dans le navigateur ; une ancienne largeur est réutilisée jusqu'à ce que vous la bougiez sur cet écran.

---

## [4.10.1] - 2026-09-16

Publishing works again after the project moved to a new GitHub owner. No change to the application itself.

### Fixed

- **Images publish again.** The image name was built from the repository path, which is fine until an owner has a capital letter in it. `TwinRocket` does, a registry reference must be lowercase, and every build failed the moment the project moved. Nothing had been published since, which left the Home Assistant add-on pointing at an image that did not exist.
- **The dependency layer survives a release.** A release changes one line in `pyproject.toml` and one in `uv.lock`, its own version, and those two files decided whether the build could reuse what it built last time. It could not, so every release recompiled eight C extensions under emulation for armv7: half an hour, measured, for a string the layer never reads.

### Upgrading

Update to 4.10.1 if you install from the Home Assistant add-on store or pull the image directly. 4.10.0 shipped the same application; it is the publishing around it that was broken.

---

### Français

La publication fonctionne de nouveau après le changement de propriétaire du dépôt GitHub. Aucun changement dans l'application elle-même.

#### Corrections

- **Les images se publient de nouveau.** Le nom de l'image était construit à partir du chemin du dépôt, ce qui marche tant qu'aucune majuscule ne s'y trouve. `TwinRocket` en contient, une référence de registre doit être en minuscules, et tous les builds ont échoué dès le déplacement du projet. Plus rien n'était publié depuis, ce qui laissait l'add-on Home Assistant pointer sur une image inexistante.
- **La couche de dépendances survit à une release.** Une release change une ligne dans `pyproject.toml` et une dans `uv.lock`, sa propre version, et ces deux fichiers décidaient si le build pouvait réutiliser ce qu'il avait construit la fois précédente. Il ne le pouvait pas, donc chaque release recompilait huit extensions C sous émulation pour armv7 : une demi-heure, mesurée, pour une chaîne que cette couche ne lit jamais.

#### Mise à jour

Passez en 4.10.1 si vous installez depuis le magasin d'add-ons Home Assistant ou si vous tirez l'image directement. La 4.10.0 embarquait la même application, c'est la publication autour qui était cassée.

---

## [4.10.0] - 2026-09-16

Meshloom says what to do when it has no radio yet, and the Home Assistant add-on stops looking abandoned.

### What's new

- **A new installation tells you what it needs.** Until a radio is configured there are no contacts, no messages and no packets, so every view was empty for a reason none of them could explain. A banner now says so and opens the radio settings. It appears only while the server reports no transport at all, and never covers the settings page it points at.
- **The add-on has an icon, a logo and a description.** It appeared in the Home Assistant store as a grey placeholder with one line of text. Its page now says what Meshloom does before it says how to install it.

### Under the hood

- **Release images build in a fraction of the time.** A commit-dependent value sat above the dependency layer in the Dockerfile, so every push rebuilt it — which on armv7 means compiling eight C extensions under emulation, measured at 21 minutes 47 seconds per build. It now rebuilds only when the dependencies actually change. Each architecture also builds on its own runner instead of sharing one, and arm64 no longer runs under emulation at all.
- **A test that failed at random no longer does.** It reused a lock left behind by another test, which made it fail depending on which worker it landed on rather than on anything in the code.

### Upgrading

Nothing to do. The banner only appears where there is genuinely nothing configured, and an older server that does not report the field is left alone rather than assumed to be broken.

---

### Français

Meshloom dit quoi faire quand aucune radio n'est configurée, et l'add-on Home Assistant cesse d'avoir l'air à l'abandon.

#### Quoi de neuf

- **Une installation neuve annonce ce qui lui manque.** Tant qu'aucune radio n'est configurée, il n'y a ni contacts, ni messages, ni paquets : chaque vue était vide pour une raison qu'aucune d'elles ne pouvait expliquer. Un bandeau le dit désormais et ouvre les réglages radio. Il n'apparaît que si le serveur signale l'absence totale de transport, et jamais par-dessus la page vers laquelle il renvoie.
- **L'add-on a une icône, un logo et une description.** Il apparaissait dans le magasin Home Assistant comme un cadre gris avec une ligne de texte. Sa page dit maintenant ce que fait Meshloom avant d'expliquer comment l'installer.

#### Sous le capot

- **Les images de release se construisent bien plus vite.** Une valeur dépendant du commit se trouvait au-dessus de la couche de dépendances dans le Dockerfile : chaque push la reconstruisait, ce qui sur armv7 signifie compiler huit extensions C sous émulation — 21 minutes 47 secondes par build, mesurées. Elle n'est plus reconstruite que lorsque les dépendances changent réellement. Chaque architecture se construit en outre sur sa propre machine au lieu de les partager, et arm64 ne passe plus du tout par l'émulation.
- **Un test qui échouait au hasard ne le fait plus.** Il réutilisait un verrou laissé par un autre test, ce qui le faisait échouer selon la machine sur laquelle il tombait plutôt que selon le code.

#### Mise à jour

Rien à faire. Le bandeau n'apparaît que là où rien n'est réellement configuré, et un serveur plus ancien qui ne renseigne pas le champ est laissé tranquille plutôt que supposé en panne.

---

## [4.9.1] - 2026-09-16

The Home Assistant add-on's panel opened blank. It works now.

### What's new

- **The add-on panel shows Meshloom.** Opening it from the sidebar gave a blank panel while the log showed a perfectly healthy start. Home Assistant serves an add-on inside a frame, and Meshloom refused to be framed at all — the browser gave up before it asked for anything. It now accepts being framed by a page on the same address, which is what Home Assistant does, and refuses everything else as before.
- **Meshloom can have an address of its own.** The panel is served under Home Assistant's own address, which means it cannot have a hostname of its own — and Web Push needs one. The add-on's **Network** panel now offers the web port. It is blank by default and nothing is opened until a port is filled in; set one, point a tunnel at it, and set `public_url` to the hostname you chose. Use `basic_auth_username` / `basic_auth_password` unless the tunnel authenticates for you.

### Upgrading

Update the add-on and reload the panel. Nothing changes for any other installation: the framing allowance is off unless the host asks for it, and the add-on is the only thing that does.

---

### Français

Le panneau de l'add-on Home Assistant s'ouvrait vide. C'est réparé.

#### Quoi de neuf

- **Le panneau de l'add-on affiche Meshloom.** L'ouvrir depuis la barre latérale donnait un panneau vide alors que le journal montrait un démarrage parfaitement sain. Home Assistant sert un add-on dans un cadre, et Meshloom refusait tout encadrement — le navigateur renonçait avant même de demander quoi que ce soit. Il accepte désormais d'être encadré par une page de la même adresse, ce que fait Home Assistant, et refuse tout le reste comme avant.
- **Meshloom peut avoir une adresse à lui.** Le panneau est servi sous l'adresse de Home Assistant, donc il ne peut pas avoir de nom d'hôte propre — et le Web Push en exige un. Le panneau **Réseau** de l'add-on propose maintenant le port web. Il est vide par défaut et rien n'est ouvert tant qu'aucun port n'y est mis ; renseignez-en un, faites pointer un tunnel dessus, et mettez dans `public_url` le nom d'hôte choisi. Utilisez `basic_auth_username` / `basic_auth_password` à moins que le tunnel n'authentifie à votre place.

#### Mise à jour

Mettez l'add-on à jour et rechargez le panneau. Rien ne change pour les autres installations : l'autorisation d'encadrement reste inactive tant que l'hôte ne la demande pas, et l'add-on est seul à le faire.

---

## [4.9.0] - 2026-09-16

Meshloom installs as a Home Assistant add-on, and now runs on a Raspberry Pi 3.

### What's new

- **Install it from Home Assistant.** If Home Assistant already runs on the machine that will host Meshloom, add the repository to its add-on store and install Meshloom there. The interface arrives in the sidebar, the database is kept with the rest of Home Assistant's data, and it starts and stops with it. The install guide has a one-click button for adding the repository.
- **A Raspberry Pi 3 is enough.** Images are now built for armv7 alongside the 64-bit ones, so the older Pi that many Home Assistant installations run on is covered.
- **The radio proxy port belongs to whoever publishes it.** Inside the add-on, Home Assistant decides which port is reachable, so Meshloom shows the port and says where to change it rather than accepting a value that would leave the proxy listening where nothing is forwarded. Nothing changes for any other installation.
- **An address can be stated rather than guessed.** `MESHCORE_PUBLIC_URL` tells Meshloom the address it is reached at from outside. Proxy headers describe the hop that happened to arrive, and a tunnel can rewrite or strip them; when it does, links built from what arrived point somewhere reachable only from inside. Setting it settles the question once.

### Upgrading

Nothing to do, and nothing changes for an existing installation: both new settings do nothing unless set, and the add-on is a way to install in addition to the ones that exist rather than a change to them.

Release images take longer to build now, since armv7 is compiled under emulation.

---

### Français

Meshloom s'installe comme add-on Home Assistant, et tourne désormais sur un Raspberry Pi 3.

#### Quoi de neuf

- **Installation depuis Home Assistant.** Si Home Assistant tourne déjà sur la machine qui hébergera Meshloom, ajoutez le dépôt à sa boutique de modules complémentaires et installez Meshloom depuis là. L'interface arrive dans la barre latérale, la base est conservée avec le reste des données de Home Assistant, et Meshloom démarre et s'arrête avec lui. Le guide d'installation contient un bouton pour ajouter le dépôt en un clic.
- **Un Raspberry Pi 3 suffit.** Les images sont maintenant construites pour armv7 en plus des architectures 64 bits, ce qui couvre le Pi plus ancien sur lequel tournent beaucoup d'installations Home Assistant.
- **Le port du proxy radio appartient à qui le publie.** Dans l'add-on, c'est Home Assistant qui décide du port joignable : Meshloom affiche donc le port et indique où le changer, au lieu d'accepter une valeur qui laisserait le proxy à l'écoute là où rien n'est redirigé. Rien ne change pour les autres installations.
- **Une adresse peut être déclarée plutôt que devinée.** `MESHCORE_PUBLIC_URL` indique à Meshloom l'adresse à laquelle il est joint depuis l'extérieur. Les en-têtes de proxy décrivent le saut qui est arrivé, et un tunnel peut les réécrire ou les supprimer ; les liens construits à partir de ce qui est arrivé pointent alors vers un endroit joignable seulement de l'intérieur. La renseigner tranche la question une fois pour toutes.

#### Mise à jour

Rien à faire, et rien ne change pour une installation existante : les deux nouveaux réglages n'ont aucun effet tant qu'ils ne sont pas renseignés, et l'add-on est une façon d'installer en plus des existantes, pas une modification de celles-ci.

Les images de release mettent désormais plus longtemps à se construire, armv7 étant compilé sous émulation.

---

## [4.8.0] - 2026-09-16

This release is about the live map. It now keeps going for as long as you leave it open, it draws every kind of traffic in its own colour, and it holds on to what arrives in a rush instead of letting it go. Meshloom also tells you when a newer version is out.

### What's new

- **The live map stays alive.** Until now it filled up quietly: within a minute of opening it, the space it keeps for animations was taken by packets that were queued to be drawn and never got their turn, so the map went still while traffic kept flowing in. The longer you left the tab open, the emptier it looked. It now keeps pace for as long as you watch it.
- **Every kind of packet has its own colour.** Half the traffic used to arrive in the same neutral grey, simply because only five of the network's message kinds had a colour of their own. Requests, replies, group messages, route announcements, traces and the rest are now each recognisable at a glance, and grey has gone back to meaning what it should: something we genuinely could not identify.
- **A busy mesh no longer loses packets on the way to the screen.** When more arrived at once than the map could animate, the surplus was dropped. It now waits its turn and is drawn a moment later, and the oldest is only ever let go once the wait itself gets too long.
- **Hiding a packet kind no longer empties the map.** Filtering the map down to what interests you used to work against you: the hidden traffic still took up the room reserved for drawing, and within seconds nothing else could appear. Filters now do only what you asked of them.
- **The listeners panel tells the truth about how far a message travelled.** For every node that reported hearing a message, the panel showed the same hop count, taken from the first copy of the packet anyone reported rather than from each listener's own account of the route it took. Nodes hundreds of kilometres apart were all credited with hearing it directly. Each listener now carries the route it actually reported, so the hop counts differ from one to the next the way they always should have.
- **A node that never announced its position is no longer placed off the coast of Africa.** A radio with no GPS fix broadcasts zeroes, and zero is a real place on a map: the point where the equator meets the Greenwich meridian, in the Gulf of Guinea. Around one node in twenty was being pinned there, which put a listener 5,400 km from a message it had heard from next door and made the longest-distance figure meaningless. A radio that has not said where it is now simply has no pin.
- **Meshloom tells you when an update is available.** A discreet notice appears when a newer release has been published, with a link to what changed. Instances that have opted out of Community still get it — a fix is worth knowing about either way.

---

### Français

Cette version est consacrée à la carte live. Elle tient désormais la distance aussi longtemps que vous la laissez ouverte, elle dessine chaque type de trafic dans sa propre couleur, et elle conserve ce qui arrive en rafale au lieu de le laisser filer. Meshloom vous signale aussi la sortie d'une nouvelle version.

#### Quoi de neuf

- **La carte live ne s'endort plus.** Jusqu'ici elle se remplissait en silence : une minute après son ouverture, la place qu'elle réserve aux animations était occupée par des paquets en attente de dessin qui n'obtenaient jamais leur tour, si bien que la carte se figeait alors que le trafic continuait d'arriver. Plus l'onglet restait ouvert, plus elle paraissait vide. Elle suit maintenant le rythme aussi longtemps que vous la regardez.
- **Chaque type de paquet a sa couleur.** La moitié du trafic arrivait dans le même gris neutre, simplement parce que cinq types de messages seulement disposaient d'une couleur propre. Les requêtes, les réponses, les messages de groupe, les annonces de route, les traces et les autres sont désormais reconnaissables d'un coup d'œil, et le gris a retrouvé son sens : quelque chose que nous n'avons vraiment pas su identifier.
- **Un mesh chargé ne perd plus de paquets en route vers l'écran.** Quand il en arrivait plus d'un coup que la carte ne pouvait en animer, le surplus était abandonné. Il attend maintenant son tour et se dessine un instant plus tard ; seul le plus ancien finit par être laissé de côté, et uniquement si l'attente devient trop longue.
- **Masquer un type de paquet ne vide plus la carte.** Filtrer la carte pour ne garder que ce qui vous intéresse jouait contre vous : le trafic masqué occupait toujours la place réservée au dessin, et en quelques secondes plus rien d'autre ne pouvait apparaître. Les filtres ne font désormais que ce que vous leur demandez.
- **Le panneau des écoutes dit la vérité sur la distance parcourue par un message.** Pour chaque nœud ayant signalé avoir entendu un message, le panneau affichait le même nombre de sauts, tiré de la première copie du paquet signalée par quiconque plutôt que du trajet rapporté par chaque écoutant. Des nœuds séparés de centaines de kilomètres se voyaient tous crédités d'une écoute directe. Chaque écoutant porte désormais le trajet qu'il a réellement rapporté, si bien que les nombres de sauts diffèrent de l'un à l'autre comme ils auraient toujours dû le faire.
- **Un nœud qui n'a jamais annoncé sa position n'est plus placé au large de l'Afrique.** Une radio sans point GPS diffuse des zéros, et zéro est un lieu réel sur une carte : l'endroit où l'équateur croise le méridien de Greenwich, dans le golfe de Guinée. Environ un nœud sur vingt s'y retrouvait épinglé, ce qui plaçait un écoutant à 5 400 km d'un message qu'il avait entendu d'à côté et rendait la distance maximale dénuée de sens. Une radio qui n'a pas dit où elle se trouve n'a désormais tout simplement pas de repère.
- **Meshloom vous signale les mises à jour.** Un message discret apparaît lorsqu'une version plus récente a été publiée, avec un lien vers ce qui a changé. Les instances qui ont refusé Community le reçoivent aussi : un correctif mérite d'être connu dans tous les cas.

---

## [4.7.6] - 2026-09-15

The unread badge could show a conversation waiting while the unread filter found none to open.

### What's new

- **The unread badge and the unread filter now count the same thing.** The badge counted how many unread counters were not zero, while the filter counted conversations it could actually put on screen. A counter can outlive what it belonged to — a channel that was deleted, a contact this client has not loaded — so the badge could advertise a conversation nobody was able to open. Both now start from the conversations themselves.

---

### Français

Le badge de non-lus pouvait annoncer une conversation en attente alors que le filtre « Non lues » n'en trouvait aucune à ouvrir.

#### Quoi de neuf

- **Le badge de non-lus et le filtre « Non lues » comptent désormais la même chose.** Le badge comptait les compteurs non nuls, tandis que le filtre comptait les conversations qu'il pouvait réellement afficher. Un compteur peut survivre à ce à quoi il appartenait — un canal supprimé, un contact que ce client n'a pas chargé — si bien que le badge pouvait annoncer une conversation que personne ne pouvait ouvrir. Les deux partent maintenant des conversations elles-mêmes.

---

## [4.7.5] - 2026-09-15

The conversation list said how the last message was encoded rather than what it was.

### What's new

- **A GIF, a location pin or a reaction is named in the conversation list.** These travel across the mesh as ordinary text — a GIF is sent as `g:` followed by an id — and the conversation has always drawn them as a picture, a map or an emoji. The list showed the text as it arrived, so a conversation whose last message was a GIF read `g:APqEbxBsVIkWSuFpth`. It now reads GIF, a pin shows the name its sender gave the place, and a reaction shows its emoji. An ordinary message is left exactly as written.

---

### Français

La liste des conversations disait comment le dernier message était encodé plutôt que ce qu'il était.

#### Quoi de neuf

- **Un GIF, une position ou une réaction sont nommés dans la liste des conversations.** Ces contenus circulent sur le mesh sous forme de texte ordinaire — un GIF s'envoie sous la forme `g:` suivi d'un identifiant — et la conversation les a toujours dessinés comme une image, une carte ou un emoji. La liste, elle, affichait le texte tel qu'il arrivait : une conversation dont le dernier message était un GIF affichait `g:APqEbxBsVIkWSuFpth`. Elle affiche désormais GIF, une position montre le nom que son expéditeur a donné au lieu, et une réaction montre son emoji. Un message ordinaire reste exactement tel qu'il a été écrit.

---

## [4.7.4] - 2026-09-15

Opening the packet analyser on a phone left no way out of it.

### What's new

- **A dialog can no longer open underneath the status bar.** The packet analyser's title and its close control ended up in the strip iOS draws its own status bar over: blurred, out of reach, and with nothing else to close the screen with. Dialogs are now bounded by the space the device actually leaves them, so what they put at the top stays reachable.

---

### Français

Ouvrir l'analyseur de paquet sur un téléphone ne laissait aucun moyen d'en sortir.

#### Quoi de neuf

- **Une fenêtre ne peut plus s'ouvrir sous la barre d'état.** Le titre de l'analyseur de paquet et son bouton de fermeture se retrouvaient dans la bande où iOS dessine sa propre barre d'état : flous, hors d'atteinte, et sans autre moyen de quitter l'écran. Les fenêtres sont désormais bornées par la place que l'appareil leur laisse réellement, de sorte que ce qu'elles placent en haut reste accessible.

---

## [4.7.3] - 2026-09-15

Two screens could not be left. Message search and the repeater dashboard take the whole screen on a phone, and neither offered a way back to where you came from.

### What's new

- **Message search and the repeater dashboard can be left again.** Both fill the screen on a phone, and neither had a back control: once inside, the only way out was the browser's own gesture, which an installed app does not have. Both now carry the same control every other screen does.
- **The proxy settings say where their values go.** Every other group of settings states whether it writes to the radio, to Meshloom or to this browser, and whether the change applies at once. This one gave only its name, which is the one thing you already knew.

---

### Français

Deux écrans ne pouvaient pas être quittés. La recherche de messages et le tableau de bord d'un répéteur occupent tout l'écran sur un téléphone, et ni l'un ni l'autre ne proposait de revenir d'où l'on venait.

#### Quoi de neuf

- **On peut de nouveau quitter la recherche de messages et le tableau de bord d'un répéteur.** Les deux remplissent l'écran sur un téléphone et n'avaient aucun bouton de retour : une fois dedans, il ne restait que le geste du navigateur, dont une application installée ne dispose pas. Ils portent désormais le même contrôle que tous les autres écrans.
- **Les réglages du proxy disent où vont leurs valeurs.** Chaque autre groupe de réglages indique s'il écrit dans la radio, dans Meshloom ou dans ce navigateur, et si le changement s'applique immédiatement. Celui-ci ne donnait que son nom, c'est-à-dire la seule chose que vous saviez déjà.

---

## [4.7.2] - 2026-09-14

This release rebuilds the desktop around three columns, gives the left rail a configurable set of entries, and moves interface preferences off each browser and onto the instance, so the same Meshloom looks the same from every device reaching it.

### What's new

- **The desktop is a rail, a list, and the content.** The single 240px column that mixed the tools with the conversations is gone: with thirty channels the tools left the screen entirely, and every row truncated the name, the preview and the time together. The rail offers the same destinations the phone's bar does, so a place added to one can no longer be missing from the other.
- **The column beside the rail belongs to what you are looking at.** Conversations while you are in conversations, the section list while you are in settings, and nothing at all for the map and the tools — each of those is one full-width view, and the list was taking 384px from panes that lay out two columns of their own.
- **Every tool has its own place on the rail**, and you decide which ones and in what order. Settings → Navigation arranges it: move an entry up or down, take one off, put it back. It starts with everything on it, since removing what you do not use is easier than discovering what you never saw.
- **Interface preferences follow you between devices.** The rail's arrangement and the colour theme are kept with the instance rather than in each browser, so setting them on a computer is enough for the phone that reaches the same Meshloom.
- **The theme no longer flashes on load.** The page is served with the theme already applied instead of being told afterwards, and following the operating system is answered before any script runs. A device seeing the instance for the first time corrects itself once and then never again.
- **The radio status says what it knows.** The dot was enough to notice something was wrong and never enough to act on it: it opens a read-out of the radio's state, how this app is reaching it, which radio and firmware answered, and whether a proxy is in the way — without offering to change anything.
- **Android gets its own chrome.** Installed on Android, the bar runs edge to edge with the indicator behind the icon, the back control is a plain arrow, writing a new message is a floating button, and sending is a circle — the platform's conventions rather than another platform's.
- **Leaving settings takes you where you asked.** Choosing a destination from settings could return you to whatever was open beforehand instead.

### Note for upgrades

This release adds a column to the settings table. The change is additive and nothing is rewritten, but the upgrade does touch the database, so take a copy of it first if you keep one.

---

### Français

Cette version reconstruit le desktop autour de trois colonnes, rend la barre de gauche configurable, et sort les préférences d'interface de chaque navigateur pour les rattacher à l'instance : le même Meshloom se présente de la même façon depuis tous vos appareils.

#### Quoi de neuf

- **Le desktop, c'est une barre, une liste et le contenu.** L'unique colonne de 240 px qui mélangeait les outils et les conversations disparaît : avec trente canaux, les outils sortaient de l'écran, et chaque ligne tronquait ensemble le nom, l'aperçu et l'heure. La barre propose les mêmes destinations que celle du téléphone, si bien qu'un endroit ajouté à l'une ne peut plus manquer à l'autre.
- **La colonne à côté de la barre appartient à ce que vous regardez.** Les conversations quand vous y êtes, la liste des sections dans les réglages, et rien du tout pour la carte et les outils — chacun est une vue pleine largeur, et la liste prenait 384 px à des panneaux qui déploient déjà deux colonnes à eux.
- **Chaque outil a sa place dans la barre**, et vous choisissez lesquels et dans quel ordre. Réglages → Navigation s'en charge : monter, descendre, retirer, remettre. Elle démarre avec tout, parce que retirer ce dont on ne se sert pas est plus facile que découvrir ce qu'on n'a jamais vu.
- **Les préférences d'interface vous suivent d'un appareil à l'autre.** L'agencement de la barre et le thème sont conservés avec l'instance plutôt que dans chaque navigateur : les régler sur un ordinateur suffit pour le téléphone qui atteint le même Meshloom.
- **Le thème ne clignote plus au chargement.** La page est servie avec le thème déjà appliqué au lieu d'en être informée après coup, et « suivre le système » trouve sa réponse avant le moindre script. Un appareil qui découvre l'instance se corrige une fois, puis plus jamais.
- **L'état de la radio se raconte.** Le point suffisait à remarquer un problème, jamais à agir dessus : il ouvre un relevé de l'état de la radio, de la manière dont l'application l'atteint, de quel modèle et quel micrologiciel ont répondu, et si un proxy se trouve sur le chemin — sans rien proposer de modifier.
- **Android a son propre habillage.** Installée sur Android, la barre va d'un bord à l'autre avec l'indicateur derrière l'icône, le retour est une flèche simple, écrire un message est un bouton flottant et envoyer est un rond : les conventions de la plateforme plutôt que celles d'une autre.
- **Quitter les réglages vous emmène où vous avez demandé.** Choisir une destination depuis les réglages pouvait vous ramener à ce qui était ouvert auparavant.

#### À noter pour la mise à jour

Cette version ajoute une colonne à la table des réglages. Le changement est additif et rien n'est réécrit, mais la mise à jour touche bien la base : si vous en gardez une copie, faites-la avant.

---

## [4.7.1] - 2026-09-14

A client connecting to a Meshloom proxy could be told the radio had been swapped, and offered to erase its mesh history to adopt a node that does not exist.

### What's new

- **A proxy no longer claims an identity it does not have.** While a Meshloom is still coming up it knows neither its radio's key nor its own stored one, and it answered connecting clients with a key of all zeros and the name "Meshloom". That is a well-formed key, so the client compared it against the identity it was bound to, found it different, and offered to erase every mesh contact and message in order to adopt it. The proxy now answers with its real key, or with an error while it has none — a client that cannot be told the truth is told nothing.
- **An all-zero key is treated as no key at all.** The other half of the same fix, on the receiving side: a client refuses to read zeros as an identity whatever sent them, so it waits instead of proposing to erase anything. A radio whose key is genuinely different still raises the same warning it always did.

---

### Français

Un client se connectant à un proxy Meshloom pouvait s'entendre dire que la radio avait été remplacée, et se voir proposer d'effacer son historique mesh pour adopter un nœud qui n'existe pas.

#### Quoi de neuf

- **Un proxy n'annonce plus une identité qu'il n'a pas.** Pendant qu'un Meshloom démarre, il ne connaît ni la clé de sa radio ni la sienne en base, et il répondait aux clients qui se connectaient par une clé entièrement nulle et le nom « Meshloom ». Cette clé est bien formée : le client la comparait à l'identité à laquelle il est lié, la trouvait différente, et proposait d'effacer tous les contacts et messages mesh pour l'adopter. Le proxy répond désormais avec sa vraie clé, ou par une erreur tant qu'il n'en a pas — un client à qui on ne peut pas dire la vérité ne s'entend rien dire.
- **Une clé entièrement nulle vaut absence de clé.** L'autre moitié du même correctif, côté réception : un client refuse de lire des zéros comme une identité, quel que soit l'émetteur, et attend au lieu de proposer d'effacer quoi que ce soit. Une radio dont la clé est réellement différente déclenche toujours le même avertissement qu'avant.

---

## [4.7.0] - 2026-09-14

This release rebuilds the phone app around a bar at the bottom of the screen, and rebuilds the live map on a new engine. Installed on a phone, Meshloom now fills the screen and stays put.

### What's new

- **The phone app is navigated from the bottom.** Four destinations — conversations, map, tools, settings — on a bar that is always there, instead of a drawer behind a burger. The app header is gone with it: the radio state moved onto the screens that need it, and settings became a destination of its own.
- **Conversations are one list.** Channels and direct conversations sit together, newest first, with search, filters for unread, favourites, groups and direct, and a row of favourites at the top. It is the whole screen, not a drawer over one.
- **Tools have a screen.** The packet feed, the live map, the mesh visualiser, trace, RF locate, message search and the channel finder are a list with a line each saying what they are for. Each opens full screen with a way back.
- **Settings open on an index.** The sections are grouped by where their values go — the radio, the app, the data it keeps — rather than dropping straight into the radio one.
- **Installed on a phone, the app fills the screen and stays put.** The skeleton no longer drifts when you drag it, only content scrolls, and the keyboard no longer leaves the layout a third shorter than the screen for the rest of the session. Double-tapping no longer leaves the page zoomed.
- **A conversation has one scroll.** Reaching the top loads older messages without losing your place, and the composer stays reachable whatever the draft's length or the keyboard's state.
- **The live map is rebuilt.** Packets land on a MapLibre and deck.gl map as they are heard, with trails and hops coloured by packet type rather than grey, and zero-hop ears drawn where traffic was heard directly.
- **The dark map works again.** CARTO's free tiles began answering with an "API KEY REQUIRED" watermark past a certain zoom. The dark basemap is OpenStreetMap now, and it follows your theme without being asked.
- **Both maps open on your nodes.** Arriving frames everything being heard instead of restoring wherever the last visit ended; a control puts the whole mesh back once you have panned away.
- **Through the proxy, your own messages are yours.** A message sent from a connected client was filed under the contact it was sent to, as though they had written it.

---

### Français

Cette version reconstruit l'application téléphone autour d'une barre en bas de l'écran, et refait la carte live sur un nouveau moteur. Installé sur un téléphone, Meshloom occupe maintenant tout l'écran et ne bouge plus.

#### Quoi de neuf

- **L'application téléphone se navigue par le bas.** Quatre destinations — discussions, carte, outils, réglages — sur une barre toujours présente, au lieu d'un tiroir derrière un menu burger. L'en-tête de l'application disparaît avec lui : l'état de la radio a rejoint les écrans qui en ont besoin, et les réglages sont devenus une destination à part entière.
- **Les conversations forment une seule liste.** Salons et conversations directes ensemble, les plus récentes d'abord, avec recherche, filtres non lues / favoris / groupes / directs, et une rangée de favoris en haut. C'est tout l'écran, pas un tiroir par-dessus.
- **Les outils ont leur écran.** Le flux de paquets, la carte live, le visualiseur mesh, trace, la localisation RF, la recherche de messages et le chercheur de canaux forment une liste, chacun avec une ligne qui dit à quoi il sert. Chacun s'ouvre en plein écran avec un retour.
- **Les réglages s'ouvrent sur un index.** Les sections sont groupées selon l'endroit où vont leurs valeurs — la radio, l'application, les données qu'elle conserve — au lieu d'atterrir directement dans celle de la radio.
- **Installée sur un téléphone, l'application occupe tout l'écran et reste en place.** Le squelette ne dérive plus quand on tire la page, seul le contenu défile, et le clavier ne laisse plus la mise en page d'un tiers trop courte pour le reste de la session. Un double tap ne laisse plus la page zoomée.
- **Une conversation n'a qu'un seul défilement.** Arriver en haut charge les messages plus anciens sans perdre sa place, et la zone de saisie reste atteignable quelle que soit la longueur du brouillon ou l'état du clavier.
- **La carte live est refaite.** Les paquets arrivent sur une carte MapLibre et deck.gl au moment où ils sont entendus, avec des traces et des sauts colorés par type de paquet plutôt qu'en gris, et les oreilles zéro-hop dessinées là où le trafic a été entendu en direct.
- **La carte sombre refonctionne.** Les tuiles gratuites de CARTO se sont mises à répondre par un filigrane « API KEY REQUIRED » au-delà d'un certain zoom. Le fond sombre est désormais OpenStreetMap, et il suit votre thème sans qu'on le lui demande.
- **Les deux cartes s'ouvrent sur vos nœuds.** Arriver cadre tout ce qui est entendu au lieu de restaurer l'endroit où s'était arrêtée la visite précédente ; un bouton remet tout le mesh à l'écran une fois qu'on s'en est éloigné.
- **À travers le proxy, vos messages sont les vôtres.** Un message envoyé depuis un client connecté était classé sous le contact à qui il était destiné, comme s'il l'avait écrit.

---

## [4.6.1] - 2026-09-13

This release lets a second client use this radio without unplugging anything. A phone app or another Meshloom can talk to the mesh through this instance, and Web Push stops refusing the https address of your own server.

### What's new

- **A second client, without pulling the cable.** Sharing a radio used to mean unplugging it from one machine and plugging it into another. Settings > Proxy now lets Meshloom present itself as a MeshCore companion over TCP: a phone app or another Meshloom connects to it and sends and receives through this instance, while the radio stays exactly where it is.
- **Connected clients hear everything the radio hears.** Not only channel text. Adverts, paths, acks, traces and direct messages all come through, the same traffic this node overhears.
- **What a connected client cannot touch.** It cannot rename the node, change the radio settings, or take the private key with it. The companion protocol has no password either, so keep this on a network you trust.
- **Two Meshlooms, one identity.** A second Meshloom connecting here has to start on a fresh database, or explicitly adopt this node's identity. And aiming a Meshloom's own radio connection at its own proxy is refused, since it would only be talking to itself.
- **Web Push accepts the address of your own instance.** Putting your server's https address in the VAPID contact field, or leaving the Docker variable empty, used to stop notifications from ever being sent — the library refused the value without saying so. An https address now works, Meshloom keeping only the beginning of it, and an empty variable falls back to the built-in default.

---

### Français

Cette version permet à un second client d'utiliser cette radio sans rien débrancher. Une appli téléphone ou un autre Meshloom peut parler au mesh à travers cette instance, et Web Push cesse de refuser l'adresse https de votre propre serveur.

#### Quoi de neuf

- **Un second client, sans débrancher le câble.** Partager une radio voulait dire la retirer d'une machine pour la brancher sur une autre. Réglages > Proxy permet maintenant à Meshloom de se présenter comme un companion MeshCore en TCP : une appli téléphone ou un autre Meshloom s'y connecte, envoie et reçoit à travers cette instance, et la radio reste là où elle est.
- **Les clients connectés entendent tout ce que la radio entend.** Pas seulement le texte des salons. Les annonces, les chemins, les acks, les traces et les messages directs passent aussi, exactement le trafic que ce nœud capte.
- **Ce qu'un client connecté ne peut pas toucher.** Il ne peut pas renommer le nœud, modifier les réglages radio, ni emporter la clé privée. Le protocole companion n'a pas de mot de passe non plus : gardez tout ça sur un réseau de confiance.
- **Deux Meshloom, une seule identité.** Un second Meshloom qui se connecte ici doit démarrer sur une base neuve, ou adopter explicitement l'identité de ce nœud. Et brancher la radio d'un Meshloom sur son propre proxy est refusé : il ne ferait que se parler à lui-même.
- **Web Push accepte l'adresse de votre propre instance.** Mettre l'adresse https de votre serveur dans le champ de contact VAPID, ou laisser la variable Docker vide, empêchait les notifications de partir : la bibliothèque refusait la valeur sans rien dire. Une adresse https fonctionne maintenant, Meshloom n'en gardant que le début, et une variable vide retombe sur la valeur par défaut fournie.

---

## [4.6.0] - 2026-09-13

This release is about the interface rather than the mesh. The visualizer gets its graph back, the radio settings page stops hiding which button saves what, the repeater dashboard remembers what it was told, and a good part of the app finally fits on a phone.

### What's new

- **The mesh visualizer shows the mesh.** The control panels sat on top of the graph and covered more than half of it on a laptop — all of it on a phone — and the only way to close them was a checkbox inside the panel, which then left it hovering over the nodes anyway. Controls now live in a toolbar above the graph, grouped by what they do, and open one at a time. Touching the graph closes them. The visualizer was also drawing the same graph twice, one copy permanently invisible; it now draws it once.
- **The radio settings page says what each Save covers.** Two identical-looking checkboxes sat side by side: one was saved by a button further up the page, the other saved itself the moment you ticked it. Ticking the first and leaving the page lost the change without a word. Settings are now grouped by where they are written — to the radio, to Meshloom, or to this browser — each group carries its own Save, greyed out until you change something in that group, and the page splits into tabs so sending an advert no longer means scrolling past five screens.
- **The repeater dashboard opens on what it already knows.** Reloading emptied all nine panels, and the only way to see them again was to ask the repeater over the air — for its name, its radio settings, its regions, things that rarely change. The last answer is kept along with the time it arrived, so the page opens on it and only queries the mesh when you ask. It also stops sending you back to the login form on every reload.
- **The neighbours map is legible and framed on your repeater.** The links to zero-hop neighbours were drawn so faintly they disappeared over the map, and the map opened zoomed to street level on your repeater alone. It now opens centred on your repeater, zoomed out enough to show most of its neighbours, with links coloured by signal. You can switch between the map and the list, refresh either, and expand the map to full screen on a phone.
- **Loading everything tells you where it is.** Panels are fetched one at a time, because the radio answers one question at a time. Only one used to show anything; the other eight looked like panels nobody had asked for. Each now says whether it is being fetched or waiting its turn, the header counts the run down, and you can stop it. Refreshing a panel no longer wipes the values it was showing.
- **Buttons are readable.** Primary buttons were painted with the brand gradient, and white text on its cyan end was close to invisible. They are now solid, with text that passes accessibility contrast. The gradient stays where it belongs, on the logo.
- **The interface fits on a phone.** The theme button sat just outside the screen edge on every page. The conversation filter scrolled away with the list it filters. Opening a settings section from a link showed everything collapsed. All fixed, and every screen was checked at phone, tablet and desktop widths.
- **Empty screens stop promising what cannot come.** The packet feed told you packets would appear in real time while the radio was disconnected. It now says the radio is not connected and points at the control that fixes it.

---

### Français

Cette version concerne l'interface plutôt que le mesh. Le visualiseur rend son graphe, la page des réglages radio cesse de cacher quel bouton enregistre quoi, le tableau de bord d'un répéteur se souvient de ce qu'on lui a dit, et une bonne partie de l'application tient enfin sur un téléphone.

#### Quoi de neuf

- **Le visualiseur mesh montre le mesh.** Les panneaux de contrôle étaient posés sur le graphe et en couvraient plus de la moitié sur un portable — la totalité sur un téléphone — et la seule façon de les fermer était une case à cocher située dans le panneau, qui le laissait ensuite flotter au-dessus des nœuds. Les contrôles sont maintenant dans une barre d'outils au-dessus du graphe, groupés par usage, et s'ouvrent un à la fois. Toucher le graphe les referme. Le visualiseur dessinait aussi deux fois le même graphe, dont une copie invisible en permanence ; il ne le dessine plus qu'une fois.
- **La page des réglages radio dit ce que chaque Enregistrer couvre.** Deux cases d'apparence identique se trouvaient côte à côte : l'une était enregistrée par un bouton situé plus haut dans la page, l'autre s'enregistrait dès qu'on la cochait. Cocher la première et quitter la page perdait le changement sans un mot. Les réglages sont désormais groupés par destination — la radio, Meshloom, ou ce navigateur — chaque groupe porte son propre Enregistrer, grisé tant que rien n'a changé dans ce groupe, et la page se divise en onglets : envoyer une annonce ne demande plus de faire défiler cinq écrans.
- **Le tableau de bord d'un répéteur s'ouvre sur ce qu'il sait déjà.** Un rechargement vidait les neuf panneaux, et le seul moyen de les revoir était d'interroger le répéteur par les ondes — pour son nom, ses réglages radio, ses régions, des valeurs qui changent rarement. La dernière réponse est conservée avec l'heure à laquelle elle est arrivée : la page s'ouvre dessus et n'interroge le mesh que si vous le demandez. Elle ne vous renvoie plus non plus au formulaire de connexion à chaque rechargement.
- **La carte des voisins est lisible et cadrée sur votre répéteur.** Les liens vers les voisins à zéro saut étaient tracés si pâles qu'ils disparaissaient sur la carte, et celle-ci s'ouvrait au niveau de la rue sur votre seul répéteur. Elle s'ouvre maintenant centrée sur lui, dézoomée de quoi montrer la majorité de ses voisins, avec des liens colorés selon le signal. Vous pouvez basculer entre la carte et la liste, rafraîchir l'une ou l'autre, et agrandir la carte en plein écran sur un téléphone.
- **Le chargement complet dit où il en est.** Les panneaux sont récupérés un par un, parce que la radio ne répond qu'à une question à la fois. Un seul le montrait ; les huit autres ressemblaient à des panneaux que personne n'avait demandés. Chacun indique maintenant s'il est en cours de récupération ou en attente de son tour, l'en-tête décompte la progression, et vous pouvez arrêter. Rafraîchir un panneau n'efface plus les valeurs qu'il affichait.
- **Les boutons sont lisibles.** Les boutons principaux étaient peints avec le dégradé de la marque, et le texte blanc sur son extrémité cyan était quasiment invisible. Ils sont désormais unis, avec un texte qui respecte les contrastes d'accessibilité. Le dégradé reste là où il a sa place : sur le logo.
- **L'interface tient sur un téléphone.** Le bouton de thème dépassait du bord de l'écran sur toutes les pages. Le filtre des conversations défilait hors de vue avec la liste qu'il filtre. Ouvrir une section de réglages depuis un lien affichait tout replié. Tout est corrigé, et chaque écran a été vérifié en largeurs téléphone, tablette et bureau.
- **Les écrans vides cessent de promettre l'impossible.** Le flux de paquets annonçait des paquets en temps réel alors que la radio était déconnectée. Il dit maintenant que la radio n'est pas connectée et pointe le contrôle qui règle ça.

---

## [4.5.0] - 2026-09-13

This release keeps polishing the ears on your messages. Your own radio stops counting itself as a listener, a count that turned out wrong is no longer kept for a whole day, and the observer panel finally says something true about messages you received.

### What's new

- **Your own radio is no longer one of the ears.** A radio does not hear its own transmissions, so counting it inflated every badge by one. It is now removed on the server rather than in the interface, which also means the number on the badge and the list in the detail panel can no longer disagree.
- **A count that was wrong is corrected within the hour.** Final counts used to be held for a full day, so a correction made on the directory side could not reach a browser already holding the old answer. They are now held for an hour instead. The directory is still protected — it is what decides a count is final in the first place, not this cache.
- **The observer panel reads correctly on received messages.** It said "your message was heard by these observers" even when the message was not yours. It now simply says "this message".

---

### Français

Cette version continue de peaufiner les oreilles de vos messages. Votre propre radio cesse de se compter comme auditrice, un comptage qui s’avère faux n’est plus conservé une journée entière, et le panneau des observateurs dit enfin quelque chose de juste sur les messages reçus.

#### Quoi de neuf

- **Votre propre radio n’est plus une oreille.** Une radio n’entend pas ses propres émissions : la compter gonflait chaque badge d’une unité. Elle est désormais retirée côté serveur plutôt que dans l’interface, ce qui garantit aussi que le nombre affiché sur le badge et la liste du panneau de détail ne puissent plus diverger.
- **Un comptage erroné est corrigé dans l’heure.** Les comptages définitifs étaient conservés une journée entière, si bien qu’une correction faite côté annuaire ne pouvait pas atteindre un navigateur détenant déjà l’ancienne réponse. Ils ne sont plus conservés qu’une heure. L’annuaire reste protégé : c’est lui qui décide qu’un comptage est définitif, pas ce cache.
- **Le panneau des observateurs est juste sur les messages reçus.** Il annonçait « votre message a été entendu par ces observateurs » même quand le message n’était pas le vôtre. Il dit maintenant simplement « ce message ».

---

## [4.4.0] - 2026-09-13

This release is about the ears on your messages — the badge showing which other radios heard a packet. They come back when they were missing, they stop announcing a direct hop for a node on the other side of the country, and they stop asking the shared directory the same settled question forever.

### What's new

- **Every listener counts again.** The shared directory reports some of the radios it knows without a public key, and Meshloom was quietly discarding exactly those. That is why messages showed no ear at all, or fewer than had really heard them. All of them are counted now, and a radio known through two different directories counts once instead of twice.
- **Distant nodes no longer look like neighbours.** Hop details are passed along as the directory reports them, instead of being rebuilt on the way through, so an observer hundreds of kilometres away is no longer shown as a direct contact at zero hops.
- **A count that fails to load tries again.** If the directory was unreachable on the first attempt, the badge stayed empty until you reloaded the page. Meshloom now retries, spacing the attempts out so an unreachable directory is not hammered.
- **Settled counts stay settled.** A message older than ten minutes will never gain a new listener, so the directory now says when a count is final and Meshloom keeps it. Scrolling back through a long conversation no longer asks again about every message you pass.

Also: a count is only treated as final once an answer has actually been received, so a directory that was briefly down no longer freezes an empty badge in place for good.

---

### Français

Cette version porte sur les oreilles de vos messages — le badge qui indique quelles autres radios ont entendu un paquet. Elles reviennent quand elles manquaient, elles cessent d’annoncer un saut direct pour un nœud à l’autre bout du pays, et elles cessent de reposer indéfiniment à l’annuaire partagé une question déjà tranchée.

#### Quoi de neuf

- **Chaque auditeur compte à nouveau.** L’annuaire partagé signale certaines des radios qu’il connaît sans clé publique, et Meshloom écartait silencieusement celles-là précisément. D’où des messages sans aucune oreille, ou avec moins d’oreilles que de radios les ayant réellement entendus. Toutes sont désormais comptées, et une radio connue via deux annuaires différents compte une fois au lieu de deux.
- **Les nœuds lointains ne passent plus pour des voisins.** Les détails de saut sont transmis tels que l’annuaire les rapporte, au lieu d’être reconstruits au passage : un observateur à des centaines de kilomètres n’est plus présenté comme un contact direct à zéro saut.
- **Un comptage qui échoue réessaie.** Si l’annuaire était injoignable au premier essai, le badge restait vide jusqu’au rechargement de la page. Meshloom réessaie maintenant, en espaçant les tentatives pour ne pas marteler un annuaire hors service.
- **Les comptages tranchés le restent.** Un message de plus de dix minutes ne gagnera jamais un nouvel auditeur : l’annuaire indique donc quand un comptage est définitif, et Meshloom le conserve. Remonter une longue conversation ne réinterroge plus chaque message traversé.

Aussi : un comptage n’est considéré comme définitif qu’une fois une réponse réellement reçue, si bien qu’un annuaire momentanément hors service ne fige plus un badge vide pour de bon.

---

## [4.3.0] - 2026-09-13

This release adds an optional shared directory of nearby nodes, gathers every notification setting into one screen, and makes conversations easier to read.

### What's new

- **A shared directory of the nodes around you.** New databases join Meshloom Community, where this radio can publish the packets it overhears. Pooling those observations is what lets Meshloom name the repeaters a message passed through, show who else heard it, and narrow down where a node is. Set your area by searching for the nearest airport by name in Settings > Community; a banner asks for that code until you save one, and you can dismiss the banner if you would rather not join. Leaving is a single toggle in the same place, and existing databases are never opted in for you.
- **One screen for notifications.** Settings > Notifications now holds your registered devices, what notifies you by default, per-conversation exceptions, and the contact address Apple requires. Notifications come from the browser, so they still arrive when the Meshloom tab is closed; the old in-tab desktop alerts are gone. The bell in a conversation header is now a plain on/off switch, and Meshloom can also tell you the first time it hears a new companion, repeater, or sensor.
- **A channel finder that looks further back.** It now searches the channel packets you already stored but could not read, instead of only what arrived while the page was open. It also tries a bundled list of common MeshCore channel names, plus the hashtag names shared by nodes near your airport when Community is on — names only, never keys.
- **A more readable conversation.** The message column is centered on screen, and the sidebar shows an excerpt of the last message in each thread. Send now refuses a message that is too long for one radio packet instead of quietly cutting it short.

Also: new and upgraded databases get a `#meshloom` channel, and an existing `#remoteterm` is left alone. Dark map tiles accept an optional CARTO API key. Clicking an observer badge no longer hangs on “Loading observers…”.

To create a new database without joining Community: `MESHLOOM_COMMUNITY=0`.

---

### Français

Cette version ajoute un annuaire partagé des nœuds proches, en option, regroupe tous les réglages de notification sur un seul écran et rend les conversations plus lisibles.

#### Quoi de neuf

- **Un annuaire partagé des nœuds autour de vous.** Les nouvelles bases rejoignent Meshloom Community, où cette radio peut publier les paquets qu’elle capte. C’est la mise en commun de ces observations qui permet à Meshloom de nommer les répéteurs traversés par un message, de montrer qui d’autre l’a entendu et de situer approximativement un nœud. Définissez votre zone en cherchant l’aéroport le plus proche par son nom dans Réglages > Community : un bandeau réclame ce code jusqu’à ce que vous l’enregistriez, et vous pouvez le masquer si vous préférez ne pas participer. Quitter tient à un seul interrupteur au même endroit, et les bases existantes ne sont jamais inscrites à votre place.
- **Un seul écran pour les notifications.** Réglages > Notifications regroupe désormais vos appareils enregistrés, ce qui vous prévient par défaut, les exceptions par conversation et l’adresse de contact qu’Apple exige. Les notifications viennent du navigateur : elles arrivent donc même quand l’onglet Meshloom est fermé, et les anciennes alertes affichées dans l’onglet disparaissent. La cloche d’une conversation est maintenant un simple interrupteur, et Meshloom peut aussi vous signaler la première fois qu’il entend un nouveau compagnon, répéteur ou capteur.
- **Un chercheur de salons qui remonte plus loin.** Il fouille maintenant les paquets de salon déjà stockés mais illisibles, au lieu des seuls paquets arrivés pendant que la page était ouverte. Il essaie aussi une liste fournie de noms de salons MeshCore courants, ainsi que les noms de hashtags partagés par les nœuds proches de votre aéroport quand Community est activé — les noms seulement, jamais les clés.
- **Une conversation plus lisible.** La colonne des messages est centrée à l’écran et la barre latérale affiche un extrait du dernier message de chaque fil. L’envoi refuse désormais un message trop long pour un paquet radio, au lieu de le tronquer sans le dire.

Aussi : les bases neuves et mises à jour reçoivent un salon `#meshloom`, et un `#remoteterm` existant est laissé en place. Les tuiles sombres de la carte acceptent une clé d’API CARTO optionnelle. Un clic sur un badge d’observateur ne reste plus bloqué sur « Chargement des observateurs… ».

Pour créer une base neuve sans rejoindre Community : `MESHLOOM_COMMUNITY=0`.

---

## [4.2.1] - 2026-09-13

Observer-reach badges work again when Meshloom Community is on. CoreScope’s `POST /api/packets/observations` is ingest, not a batch query; counts now fall back to per-packet detail instead of hiding every ear.

### Highlights

- Flood-message observer ears come back with Community enabled, even without a manual CoreScope URL

### Fixed

- Bug: Community observer-reach counts treated CoreScope `POST /api/packets/observations` as a query. That route is ingest-only, so the directory call failed and the UI hid every badge. Counts now fall back to `GET` packet detail, the same path hop-name resolution already used

---

### Français

Les badges observateurs refonctionnent quand Meshloom Community est activé. Le `POST /api/packets/observations` de CoreScope est un ingest, pas une requête batch ; les comptages retombent sur le détail par paquet au lieu de cacher toutes les oreilles.

#### Points forts

- Les oreilles sur les messages flood reviennent avec Community, même sans URL CoreScope manuelle

#### Corrections

- Bug : les comptages observateurs Community traitaient le `POST /api/packets/observations` CoreScope comme une requête. Cette route est un ingest, l’appel directory échouait, et l’UI cachait chaque badge. Les comptages retombent maintenant sur le détail `GET` par hash, le même chemin que la résolution des nœuds

---

## [4.2.0] - 2026-09-13

Optional **Meshloom Stats** observer community: join from Settings, bind a 3-letter IATA code, and this server can publish overheard packets to the official Stats hosts. Existing installs stay off until you join.

### Highlights

- New **Settings > Meshloom Stats** pane to join, bind IATA, and see community contribution stats
- One opt-out stops both publish and community directory calls. Directory can fall back to a manual CoreScope URL in Radio-App
- Observer-reach detail can show hop paths and origin when the directory returns them

### Added

- Feature: Opt-in Meshloom Stats settings (`/api/community`), IATA bind, and a hidden system MQTT publisher (not listed in Fanout CRUD)
- Feature: Stats JWT includes `iata`. Existing databases stay opted out; `MESHLOOM_COMMUNITY=1` only seeds new installs
- Feature: Observer-reach views can render hop paths and origin metadata

### Changed

- Misc: Python, React 19, Vitest 5, and Playwright. Tailwind stays on 3; TypeScript stays on 5
- Misc: Backup restore keeps the SQLite dump intact but honest, validates directory restore before writes, and bounds in-memory CoreScope caches

### Fixed

- Bug: Repeater CLI `send_cmd` after meshcore 2.3.9 required a contact type
- Bug: OSM map tiles were blocked; the map now sends a Referer and uses the official tile URL
- Bug: Module-level asyncio locks broke when pytest-asyncio created a new event loop
- Bug: Joining Meshloom Stats did not turn on hop names, locate, or observer-reach UI; those stayed gated on the manual CoreScope toggle

---

### Français

Communauté d’observateurs **Meshloom Stats** en option : rejoindre depuis les Réglages, associer un code IATA à 3 lettres, et ce serveur peut publier les paquets entendus vers les hôtes Stats officiels. Les installs existantes restent hors ligne tant que tu n’as pas rejoint.

#### Points forts

- Nouvel onglet **Réglages > Meshloom Stats** pour rejoindre, associer l’IATA, et voir les stats de contribution
- Un seul opt-out arrête la publication **et** les appels directory communautaires. Le directory peut retomber sur une URL CoreScope manuelle dans Radio-App
- Le détail observateurs peut afficher les chemins de sauts et l’origine quand le directory les fournit

#### Ajouts

- Fonction : réglages Meshloom Stats opt-in (`/api/community`), association IATA, et publisher MQTT système invisible (pas dans le CRUD Fanout)
- Fonction : le JWT Stats inclut `iata`. Les bases existantes restent opt-out ; `MESHLOOM_COMMUNITY=1` ne seed que les nouvelles installs
- Fonction : les vues observateurs peuvent afficher chemins de sauts et métadonnées d’origine

#### Changements

- Divers : Python, React 19, Vitest 5 et Playwright. Tailwind reste en 3 ; TypeScript reste en 5
- Divers : la restauration de backup garde le dump SQLite intact mais honnête, valide le restore directory avant écriture, et borne les caches CoreScope en mémoire

#### Corrections

- Bug : le CLI répéteur `send_cmd` après meshcore 2.3.9 exigeait un type de contact
- Bug : les tuiles OSM étaient bloquées ; la carte envoie un Referer et utilise l’URL officielle
- Bug : les locks asyncio de module cassaient quand pytest-asyncio créait une nouvelle boucle
- Bug : rejoindre Meshloom Stats n’activait pas l’UI des noms de sauts, locate, ni portée observateurs ; elle restait liée au toggle CoreScope manuel

---

## [4.1.3] - 2026-09-11

Settings > Radio always offers USB serial, TCP, and Bluetooth. A missing port or a failed BLE scan no longer hides those choices. Re-running the Linux installer is an upgrade: it remembers the language and names the version change.

### Highlights

- Serial, TCP, and Bluetooth stay visible in **Settings > Radio** even when this host cannot see a device yet
- Saved language on the Linux one-liner, plus an explicit upgrade prompt (`4.1.2` → `4.2.3`)

### Changed

- Misc: Transport capability flags are always selectable. Missing serial ports or a missing Bluetooth adapter are shown as hints, not as a reason to hide the picker
- Misc: Loading radio settings no longer runs a BLE discovery scan. Scan stays on the **Scan** button
- Misc: systemd grants `bluetooth` when that group exists (`SupplementaryGroups`, `After=bluetooth.target`) so a later BLE choice can work
- Misc: Linux installer remembers `en` / `fr` in `installer.conf` and asks to upgrade (or reinstall) instead of “Start the installation?”



### Fixed

- Bug: USB and Bluetooth disappeared from Settings when no `/dev/ttyUSB*` / `/dev/ttyACM*` was listed, or when a 2-second BLE scan timed out
- Bug: Serial “unavailable” copy always talked about Docker. It now mentions `dialout` on a normal host, and container device mapping only inside a container

---



### Français

Réglages > Radio propose toujours le série USB, TCP et Bluetooth. Un port absent ou un scan BLE raté ne masque plus ces choix. Relancer l’installeur Linux est une mise à jour : la langue est mémorisée, et le changement de version est nommé.

#### Points forts

- Série, TCP et Bluetooth restent visibles dans **Réglages > Radio**, même si l’hôte ne voit pas encore de périphérique
- Langue mémorisée sur le one-liner Linux, et une question d’upgrade explicite (`4.1.2` → `4.2.3`)



#### Changements

- Divers : les transports restent sélectionnables. Ports série absents ou pas d’adaptateur Bluetooth = avertissement, pas un bouton caché
- Divers : ouvrir les réglages radio ne lance plus de scan BLE. Le scan reste sur le bouton **Rechercher**
- Divers : systemd ajoute `bluetooth` quand le groupe existe (`SupplementaryGroups`, `After=bluetooth.target`) pour un BLE choisi plus tard
- Divers : l’installeur Linux retient `en` / `fr` dans `installer.conf` et demande une mise à jour (ou une réinstall) au lieu de « Lancer l’installation ? »



#### Corrections

- Bug : USB et Bluetooth disparaissaient des réglages sans `/dev/ttyUSB*` / `/dev/ttyACM*`, ou si un scan BLE de 2 s expirait
- Bug : le texte « série indisponible » parlait toujours de Docker. Il parle de `dialout` sur un hôte normal, et du mapping de device seulement dans un conteneur

---



## [4.1.2] - 2026-09-11

The Linux one-liner actually downloads the GitHub package again before installing it. 4.1.1 named the tempfile `.deb` but skipped the download, so apt tried to install an empty file.

### Highlights

- Service install from the latest published `.deb` / `.rpm` downloads the package again



### Fixed

- Bug: the installer created an empty `/tmp/meshloom.*.deb` and `apt-get install` failed with `could not locate member control.tar` / `read, still have 8 to read but none left`

---



### Français

L’installeur Linux retélécharge le paquet GitHub avant de l’installer. La 4.1.1 nommait le fichier temporaire `.deb` mais sautait le téléchargement, donc apt installait un fichier vide.

#### Points forts

- L’installation service depuis le `.deb` / `.rpm` publié retélécharge le paquet



#### Corrections

- Bug : l’installeur créait un `/tmp/meshloom.*.deb` vide et `apt-get install` échouait avec `could not locate member control.tar` / `read, still have 8 to read but none left`

---



## [4.1.1] - 2026-09-11

The Linux one-liner can install the latest GitHub `.deb` again. Apt was rejecting a valid package because the tempfile had no `.deb` suffix.

### Highlights

- Service install from the latest published `.deb` / `.rpm` works again on Debian, Ubuntu, and Fedora



### Fixed

- Bug: `apt-get install` of the downloaded release package failed with `Unsupported file /tmp/tmp.… given on commandline` because the tempfile had no `.deb` extension

---



### Français

L’installeur Linux peut à nouveau installer le `.deb` GitHub. Apt refusait un paquet valide parce que le fichier temporaire n’avait pas le suffixe `.deb`.

#### Points forts

- L’installation service depuis le `.deb` / `.rpm` publié fonctionne à nouveau sur Debian, Ubuntu et Fedora



#### Corrections

- Bug : `apt-get install` du paquet téléchargé échouait avec `Unsupported file /tmp/tmp.… given on commandline` parce que le fichier temporaire n’avait pas l’extension `.deb`

---



## [4.1.0] - 2026-09-11

Flood messages can now show how many CoreScope MQTT observers heard the same packet, and chat metadata no longer wraps into the text.

### Highlights

- Optional **observer** ear on flood messages when CoreScope is enabled and at least one observer published the packet
- Click the ear for the observer list, hop/distance summary, and a map
- Date, time, observer count, path, and ACK sit on one line under the message body



### Added

- Feature: Persist firmware packet hashes on stored messages (`packet_hash`, `observer_reach_eligible`) so floods can be matched to CoreScope observations
- Feature: `POST /api/directory/packets/reach-counts` and `GET /api/directory/packets/{hash}/reach` — batch counts for visible floods, then a detail photo (list + map). One opted-in CoreScope instance only (`directory_enabled` + `directory_url`)
- Feature: Chat ear badge on channel floods, and on DMs only when a flood echo arrives. Hidden when the count is 0. Young floods refresh every 8s for 1 minute, then every 60s until 10 minutes; outgoing first lookup waits 20s



### Changed

- Misc: Message time, observer ear, hops / ACK, and region badge move to a single metadata row under the body. The sender name stays above incoming first-in-group messages



### Fixed

- Bug: Observer counts are looked up against the virtualizer’s oldest-first list, not the newest-first REST page
- Bug: Replacing the `messages` array no longer cancels in-flight CoreScope count fetches
- Bug: Empty CoreScope batch responses fall back to a per-hash GET so a live count can still appear
- Bug: Observer detail map has a real height and Leaflet CSS, so the dialog is usable

---



### Français

Les messages flood peuvent maintenant montrer combien d’observateurs MQTT CoreScope ont entendu le même paquet, et les métadonnées du chat ne se mélangent plus au texte.

#### Points forts

- Oreille **observateurs** optionnelle sur les floods quand CoreScope est activé et qu’au moins un observateur a publié le paquet
- Clic sur l’oreille : liste des observateurs, résumé sauts / distance, et carte
- Date, heure, nombre d’observateurs, chemin et ACK sur une seule ligne sous le message



#### Ajouts

- Fonction : conservation du hash firmware sur les messages stockés (`packet_hash`, `observer_reach_eligible`) pour les apparier aux observations CoreScope
- Fonction : `POST /api/directory/packets/reach-counts` et `GET /api/directory/packets/{hash}/reach` — comptages par lot pour les floods visibles, puis une photo de détail (liste + carte). Une seule instance CoreScope opt-in (`directory_enabled` + `directory_url`)
- Fonction : pastille oreille sur les floods canal, et sur les DM seulement si un écho flood arrive. Masquée si le compte est 0. Les floods jeunes se rafraîchissent toutes les 8 s pendant 1 minute, puis toutes les 60 s jusqu’à 10 minutes ; le premier lookup sortant attend 20 s



#### Changements

- Divers : l’heure, l’oreille, les sauts / ACK et le badge de région passent sur une seule ligne de métadonnées sous le corps. Le nom de l’expéditeur reste au-dessus des premiers messages d’un groupe entrant



#### Corrections

- Bug : les comptages d’observateurs sont lus sur la liste oldest-first du virtualizer, pas sur la page REST newest-first
- Bug : remplacer le tableau `messages` n’annule plus les lookups CoreScope en cours
- Bug : un lot CoreScope vide bascule sur un GET par hash pour qu’un compte puisse quand même apparaître
- Bug : la carte de détail a une hauteur réelle et le CSS Leaflet, donc le dialogue est utilisable

---



## [4.0.0] - 2026-09-11

First public **Meshloom** release. This is a new product branch of Jack Kingsman’s [Remote Terminal for MeshCore](https://github.com/jkingsman/Remote-Terminal-for-MeshCore), via [Ian Langworth’s 3.19.0 line](https://github.com/statico/remoteterm-meshcore/releases/tag/3.19.0). The original MIT copyright stays in `LICENSE.md`.

Meshloom is the same job as before — an always-on web client for a MeshCore companion radio — with a bilingual UI, a named identity for the database, radio setup in the browser, and a few observation tools the radio cannot do alone.

### Highlights

- English / French UI, toasts, push notifications, and installer
- Radio transport is chosen in **Settings > Radio** (USB, TCP, or BLE). The radio stays paused until that is set
- The database binds to the radio’s public key on first use (or after an upgrade from an unbound 3.x database)
- **RF Locate** draws conservative 0-hop coverage disks. It never invents a lat/lon pin
- Optional **CoreScope** directory to name unknown hops and search nodes
- New Linux installer, `.deb` / `.rpm` packages, and `ghcr.io/bagl3y/meshloom`
- In-browser Web Serial flasher for official MeshCore firmware



### Added

- Feature: Complete EN/FR i18n for the operator UI, toasts, and web push
- Feature: RF Locate (`#locate` / `#locate/{key}`) — unique identity, then 0-hop disks from local hearings and/or CoreScope (`local` / `corescope` / `mixte`). Ambiguous queries return 409 with candidates
- Feature: CoreScope directory proxy — hop resolve (2/3-byte only), node search, reach, and neighbors. Radio contacts stay authoritative over the overlay
- Feature: Local-only contact groups, optional stale-contact purge, and JSON backup/restore of contacts, channels, settings, and groups (plus a SQLite snapshot). The radio private key is never in the backup
- Feature: Richer chat — emoji picker, optional Giphy (browser-local API key), MeshCore Open reactions, hop-count badges, path inspector, and a MeshCore share QR
- Feature: Meshloom-branded Web Serial flasher for official companion / repeater / room-server firmware
- Feature: Linux one-liner installer at `https://get.meshloom.app` (systemd or Docker, EN/FR, root-or-sudo)
- Feature: Native `meshloom` `.deb` / `.rpm` for amd64 and arm64; Docker image `ghcr.io/bagl3y/meshloom`
- Feature: Portainer GitOps compose (`docker-compose.dev.yaml` + `.env.example`)
- Feature: EN/FR user docs in `docs/user/` (published at [https://meshloom.app/docs/](https://meshloom.app/docs/))



### Changed

- Breaking: Project, packages, and paths are Meshloom — image `ghcr.io/bagl3y/meshloom`, unit `meshloom`, data `/var/lib/meshloom`, env `/etc/meshloom/meshloom.env`
- Breaking: Radio transport is no longer configured with `MESHCORE_SERIAL_PORT`, `MESHCORE_TCP_HOST`, or `MESHCORE_BLE_ADDRESS`. Set it in the UI
- Breaking: An existing database without `radio_bound_public_key` opens the identity dialog (`identity_unbound_legacy`). Bind without wipe if this is the same radio; wipe mesh contacts/messages if it is a different device. A live key mismatch closes ingest until you adopt or reject
- Breaking: AUR packaging is removed. Use the installer, `.deb` / `.rpm`, or Docker
- Breaking: The installer no longer prompts for bots or HTTP Basic auth. Set `MESHCORE_DISABLE_BOTS` and optional `MESHCORE_BASIC_AUTH_*` in the environment yourself
- Misc: First Meshloom branding (cyan-to-violet theme) and renamed release artifacts (`meshloom-prebuilt-frontend-…`)
- Misc: Installer uses `as_root` / `priv` so it works on root-only hosts without assuming `sudo`
- Misc: Marketing site no longer lives in this repo; the README points at `docs/user/`



### Fixed

- Bug: Identity gate no longer deadlocks the radio lock when setup is paused
- Bug: Background radio sync does not write while the identity gate is closed
- Misc: Drop the unused vendored decoder, dead assets, and orphaned release scripts



### Install

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app)"
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000) and choose the radio under **Settings > Radio**. Do not pipe the installer into `bash` — it needs a real terminal.

- Docker: `ghcr.io/bagl3y/meshloom` (see `docker-compose.example.yml`)
- Docs: [https://meshloom.app/docs/](https://meshloom.app/docs/)
- Trusted network only. There are no user accounts. Bots can run arbitrary Python unless `MESHCORE_DISABLE_BOTS=true`



### Upgrade from Remote Terminal / 3.19.0

1. Install Meshloom (new image or package). Keep the existing SQLite file if you want the history (`./data` or the old data directory).
2. Set transport in **Settings > Radio**. Env-based serial/TCP/BLE settings are ignored.
3. Confirm the identity dialog. **Previous key: unknown** means the database predates binding, not that the radio changed.
4. Re-apply bot disable / Basic auth in `/etc/meshloom/meshloom.env` or Compose if you still want them.

Meshloom 4.0.0 diverged at 3.19.0. Later 3.x features from other forks are not included.

### Credits

Jack Kingsman built the original client. Ian Langworth carried the 3.x line this release starts from. Thank you both.

---



### Français

Première version publique de **Meshloom**. C’est une nouvelle branche du [client web MeshCore de Jack Kingsman](https://github.com/jkingsman/Remote-Terminal-for-MeshCore), à partir de la [ligne 3.19.0 d’Ian Langworth](https://github.com/statico/remoteterm-meshcore/releases/tag/3.19.0). Le copyright MIT d’origine reste dans `LICENSE.md`.

Meshloom fait le même métier qu’avant — un client web toujours à l’écoute d’une radio compagnon MeshCore — avec une interface bilingue, une identité liée à la base, la radio configurée dans le navigateur, et quelques outils d’observation qu’une radio seule ne peut pas faire.

#### Points forts

- Interface, toasts, notifications push et installeur en anglais / français
- Le transport radio se choisit dans **Réglages > Radio** (USB, TCP ou BLE). La radio reste en pause tant que ce n’est pas fait
- La base se lie à la clé publique de la radio au premier usage (ou après une mise à niveau depuis une base 3.x sans liaison)
- **RF Locate** dessine des disques de couverture 0-saut prudents. Il n’invente jamais un point lat/lon
- Annuaire **CoreScope** optionnel pour nommer les sauts inconnus et chercher des nœuds
- Nouvel installeur Linux, paquets `.deb` / `.rpm`, et image `ghcr.io/bagl3y/meshloom`
- Flasher Web Serial dans le navigateur pour le firmware MeshCore officiel



#### Ajouts

- Fonction : i18n EN/FR complète pour l’interface opérateur, les toasts et le web push
- Fonction : RF Locate (`#locate` / `#locate/{clé}`) — identité unique, puis disques 0-saut à partir des écoutes locales et/ou de CoreScope (`local` / `corescope` / `mixte`). Une requête ambiguë renvoie 409 avec des candidats
- Fonction : proxy d’annuaire CoreScope — résolution de sauts (2/3 octets seulement), recherche de nœuds, portée et voisins. Les contacts radio restent la source de vérité face à l’overlay internet
- Fonction : groupes de contacts locaux, purge optionnelle des contacts trop vieux, et sauvegarde / restauration JSON des contacts, canaux, réglages et groupes (plus un cliché SQLite). La clé privée de la radio n’est jamais dans la sauvegarde
- Fonction : chat plus riche — sélecteur d’emoji, Giphy optionnel (clé API locale au navigateur), réactions MeshCore Open, pastilles de nombre de sauts, inspecteur de chemin, et QR de partage MeshCore
- Fonction : flasher Web Serial aux couleurs Meshloom pour le firmware officiel compagnon / répéteur / serveur de salon
- Fonction : installeur Linux en une ligne sur `https://get.meshloom.app` (systemd ou Docker, EN/FR, root ou sudo)
- Fonction : paquets natifs `meshloom` `.deb` / `.rpm` amd64 et arm64 ; image Docker `ghcr.io/bagl3y/meshloom`
- Fonction : compose GitOps Portainer (`docker-compose.dev.yaml` + `.env.example`)
- Fonction : docs utilisateur EN/FR dans `docs/user/` (publiées sur [https://meshloom.app/docs/](https://meshloom.app/docs/))



#### Changements

- Cassant : le projet, les paquets et les chemins sont Meshloom — image `ghcr.io/bagl3y/meshloom`, unité `meshloom`, données `/var/lib/meshloom`, env `/etc/meshloom/meshloom.env`
- Cassant : le transport radio ne se configure plus avec `MESHCORE_SERIAL_PORT`, `MESHCORE_TCP_HOST` ou `MESHCORE_BLE_ADDRESS`. On le règle dans l’interface
- Cassant : une base existante sans `radio_bound_public_key` ouvre le dialogue d’identité (`identity_unbound_legacy`). Lier sans effacer si c’est la même radio ; effacer contacts et messages mesh si c’est un autre appareil. Un décalage de clé en direct ferme l’ingest jusqu’à adoption ou refus
- Cassant : le paquet AUR est retiré. Utiliser l’installeur, les `.deb` / `.rpm`, ou Docker
- Cassant : l’installeur ne demande plus les bots ni l’auth HTTP Basic. Régler soi-même `MESHCORE_DISABLE_BOTS` et, au besoin, `MESHCORE_BASIC_AUTH_*` dans l’environnement
- Divers : première identité visuelle Meshloom (thème cyan–violet) et artefacts de release renommés (`meshloom-prebuilt-frontend-…`)
- Divers : l’installeur utilise `as_root` / `priv` pour fonctionner sur un hôte root-only sans supposer `sudo`
- Divers : le site vitrine n’est plus dans ce dépôt ; le README pointe vers `docs/user/`



#### Corrections

- Bug : le verrou d’identité ne bloque plus le verrou radio quand l’initialisation est en pause
- Bug : la sync radio en arrière-plan n’écrit plus tant que le verrou d’identité est fermé
- Divers : suppression du décodeur vendored inutilisé, des assets morts et des scripts de release orphelins



#### Installation

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app)"
```

Puis ouvrir [http://127.0.0.1:8000](http://127.0.0.1:8000) et choisir la radio dans **Réglages > Radio**. Ne pas piper l’installeur dans `bash` : il a besoin d’un vrai terminal.

- Docker : `ghcr.io/bagl3y/meshloom` (voir `docker-compose.example.yml`)
- Docs : [https://meshloom.app/docs/](https://meshloom.app/docs/)
- Réseau de confiance seulement. Pas de comptes utilisateurs. Les bots peuvent exécuter du Python arbitraire sauf si `MESHCORE_DISABLE_BOTS=true`



#### Mise à niveau depuis Remote Terminal / 3.19.0

1. Installer Meshloom (nouvelle image ou paquet). Garder le fichier SQLite existant pour conserver l’historique (`./data` ou l’ancien répertoire de données).
2. Régler le transport dans **Réglages > Radio**. Les réglages serial/TCP/BLE par variables d’environnement sont ignorés.
3. Confirmer le dialogue d’identité. **Clé précédente** : **Inconnue** signifie que la base précède la liaison, pas que la radio a changé.
4. Remettre la désactivation des bots / l’auth Basic dans `/etc/meshloom/meshloom.env` ou Compose si vous les voulez encore.

Meshloom 4.0.0 a divergé à la 3.19.0. Les fonctions 3.x plus récentes des autres forks ne sont pas incluses.

#### Crédits

Jack Kingsman a écrit le client d’origine. Ian Langworth a porté la ligne 3.x dont part cette version. Merci à tous les deux.
