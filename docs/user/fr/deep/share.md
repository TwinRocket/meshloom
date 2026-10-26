---
title: Liens courts et photos
description: Raccourcir des adresses web et partager des photos, publiques ou chiffrées, avec le service de partage Meshloom : consentement, ce qui sort de votre machine, limites du chiffrement, bannissement et quotas.
level: deep
order: 23
---

Un message MeshCore contient 156 octets, et une adresse web en prend souvent davantage. Le **service de partage** transforme une longue adresse en un lien court comme `https://meshl.ink/Ab3dE9x` (25 octets), qui tient dans un message et que tout le monde peut ouvrir. Il est exploité par le projet Meshloom sur son propre domaine, `meshl.ink`, et il est **optionnel et désactivé par défaut** : tant qu’un administrateur ne l’active pas, ce Meshloom ne le contacte jamais, pas même pour résoudre son nom.

Cette version raccourcit des **adresses web** et envoie des **photos** depuis le panneau **Envoyer une photo** de la zone de saisie. Une photo est soit **publique**, et toute personne qui a son lien peut l’ouvrir, soit, dans un canal privé ou un message direct, **chiffrée** pour cette conversation, et ses membres l’ouvrent dans Meshloom. Le chiffrement n’est proposé que là où il protège quelque chose, et il a des limites qu’il faut connaître : lisez [Fichiers chiffrés](#fichiers-chiffrés) avant de vous y fier. Meshloom ne peut pas savoir qu’un canal a été renommé avec une autre casse ou sous un nom sans rapport : sa clé peut rester publique. Les sections ci-dessous ne décrivent que ce qui existe.

## L’activer

Ouvrez **Réglages > Partage : liens et fichiers** (administrateurs ; un membre n’y voit que **Mes envois**, voir plus bas). La section indique si le partage est activé, si l’identité de partage de ce Meshloom est prête et si les fichiers chiffrés pour les messages directs sont disponibles.

Cocher **Activer le partage de liens et de fichiers** ouvre toujours une fenêtre de consentement. Elle liste ce qui quitte ce Meshloom, qui exploite le service et combien de temps les données sont conservées, renvoie vers les conditions d’utilisation et la politique de confidentialité du service, et demande de cocher que vous acceptez les conditions. Annuler, fermer la fenêtre ou laisser la case décochée ne change rien. Votre acceptation est enregistrée sur ce Meshloom, avec la version des conditions et la date : si les conditions changent dans une version ultérieure, le partage reste désactivé et la section vous le signale, jusqu’à ce qu’un administrateur lise et accepte les nouvelles conditions en le réactivant. Désactiver le partage ne demande aucun consentement et prend effet aussitôt : les requêtes déjà en route sont interrompues.

Si le service ne répond pas, ses conditions ne peuvent pas être lues : le partage ne peut alors pas être activé avant qu’il réponde de nouveau (le désactiver reste toujours possible).

## Ce qui sort de votre machine

Seul ce serveur contacte le service, jamais votre navigateur, et toujours en HTTPS vers `meshl.ink` uniquement : Meshloom refuse toute redirection vers un autre hôte. Il n’envoie jamais vos clés de canal ni la clé de votre radio.

- **Quand vous raccourcissez un lien** (un message que vous écrivez, ou l’API) : l’adresse à raccourcir, la moitié **publique** de la clé de partage de ce Meshloom dans un jeton signé valable moins de deux minutes et pour cette seule requête, et l’adresse IP de ce Meshloom, que le service voit à chaque requête.
- **Quand vous envoyez une photo :** l’image, réduite dans votre navigateur et sans sa position ni les données de l’appareil, sa taille et son empreinte, le même type de jeton signé, et l’adresse IP de ce Meshloom. Une photo publique part telle quelle, et quiconque a son lien peut la voir. Une photo chiffrée est scellée par ce serveur avant de partir, avec une clé qui reste ici (voir [Fichiers chiffrés](#fichiers-chiffrés)) : le service reçoit des octets illisibles.
- **Quand vous lisez :** rien à l’affichage d’un message, sauf une photo partagée, que ce serveur télécharge quand le réglage des aperçus plus bas la laisse se charger ou quand vous la touchez (une seule fois : elle est ensuite gardée dans un cache local). Quand vous survolez un lien court ou lui donnez le focus, Meshloom demande au service ce que c’est (sa nature, l’hôte de sa destination et son expiration) pour afficher l’indication décrite plus bas. Ces requêtes portent l’identifiant de l’objet et l’adresse IP de ce Meshloom, sans jeton.

Le service conserve les liens courts 365 jours et les fichiers, publics ou chiffrés, 30 jours. Ses conditions d’utilisation et sa politique de confidentialité, accessibles depuis la fenêtre de consentement et publiées sur le domaine du service, disent ce qu’il conserve et pour qui.

Meshloom n’utilise jamais la clé de la radio pour cela. Voir [La clé de partage](/docs/deep/security/#la-clé-de-partage) : une clé aléatoire qui lui est propre, créée la première fois que vous raccourcissez un lien, et que le service ne voit que comme un pseudonyme. Les sauvegardes ne contiennent jamais le consentement, l’acceptation des conditions enregistrée, ni cette clé (voir [Sauvegarder la radio](/docs/backup/)).

## Raccourcir depuis le champ de saisie

**Réglages > Partage : liens et fichiers > Raccourcir les liens à l’écriture d’un message** propose trois modes :

- **Automatique** (par défaut) : quand un message est trop long à cause d’une longue adresse, un bouton **Raccourcir** apparaît à côté du compteur d’octets. Il remplace chaque longue adresse de la zone de texte par un lien court, indique combien d’octets il a gagnés et propose **Annuler** (Annuler ne marche que tant que vous n’avez pas modifié le texte depuis). Rien n’est envoyé par ce geste : vous relisez le résultat, puis vous appuyez sur Envoyer.
- **Toujours** : Envoyer raccourcit d’abord les longues adresses, vérifie ensuite la taille, puis envoie. Si le message reste trop long, il reste dans la zone de texte et rien n’est envoyé.
- **Jamais** : les adresses partent telles que vous les avez saisies, et aucun bouton n’apparaît.

Le raccourcissement est tout ou rien : si le service refuse une adresse (voir plus bas), votre texte reste intact. Meshloom laisse tels quels les liens courts eux-mêmes, les adresses déjà assez courtes et les messages `g:`, `m:` et `r:` (GIF, positions et autres messages spéciaux). Il ne raccourcit pas un message qui ne part pas du champ de saisie, comme celui d’une extension. Quand le partage est désactivé, aucun bouton n’apparaît et rien n’est demandé, quel que soit le mode.

Meshloom vérifie l’adresse avant de l’envoyer et **n’envoie jamais** une adresse que le service refuserait de toute façon : une adresse avec nom d’utilisateur ou mot de passe, une adresse IP, `localhost`, un nom sans point ou finissant par `.local`, `.lan`, `.internal` ou `.home.arpa`, tout ce qui n’est pas `http` ou `https`, ou plus long que 2048 caractères. Le bouton ignore une telle adresse et l’API la refuse (`share_invalid_url`) : un lien vers votre réseau domestique, ou qui contient des identifiants, ne le quitte donc jamais.

## Envoyer une photo

Ouvrez le bouton de pièce jointe à côté de la zone de texte, choisissez **Envoyer une photo**, puis déposez une photo sur le panneau, collez-en une dans le champ de message ou choisissez un fichier. Ajoutez une légende si vous le souhaitez et appuyez sur **Envoyer la photo**. Le message envoyé est `📷` et le lien (`🔒` et le lien pour une photo chiffrée), suivis de la légende. C’est un message MeshCore ordinaire de 156 octets au plus : le panneau compte les octets et vous demande de raccourcir une légende qui ne tient pas. L’envoi demande le rôle **membre** ou plus.

Avant que quoi que ce soit quitte votre navigateur, Meshloom prépare la photo :

- **Elle est réduite.** Le plus grand côté passe à 1600 pixels au plus (une photo plus petite n’est jamais agrandie) et la photo est réenregistrée, en JPEG par défaut. Cette nouvelle copie n’a ni position, ni données de l’appareil, ni heure de prise de vue, et le panneau montre la taille avant et après. Un PNG avec transparence reste PNG, et un WebP reste WebP si votre navigateur sait en écrire. Un **GIF part tel quel**, car le réencoder aplatirait l’animation : il n’est ni réduit ni nettoyé.
- **Seules les images sont acceptées : JPEG, PNG, WebP et GIF.** Meshloom et le service refusent tout autre type, et vérifient le vrai contenu, pas le nom du fichier. D’autres formats peuvent exécuter du code dans un navigateur ou cacher un programme, ce qu’un service dont tout le monde ouvre les liens ne doit pas héberger. Les photos HEIC des iPhone ne sont pas lisibles par un navigateur : partagez-les en JPEG (Réglages > Appareil photo > Formats > Le plus compatible) ou choisissez-les de nouveau par le sélecteur de photos, qui les convertit.
- **Tailles :** un fichier jusqu’à 40 Mio peut être choisi. Une fois préparée, la photo doit peser **10 Mio au plus**, ou **5 Mio pour un GIF**, et compter **40 millions de pixels au plus**. Meshloom revérifie le type, la taille et les dimensions sur le serveur avant l’envoi, dans son dossier `share-tmp`, qu’il vide aussitôt après (voir [Autres chemins d’installation](/docs/deep/install-paths/#base-de-données-et-mises-à-jour)). Deux photos au plus sont reçues en même temps.
- **Le service limite ce que chaque Meshloom peut envoyer.** Par défaut, les 7 premiers jours d’un Meshloom permettent 5 fichiers et 10 Mio par jour. Ensuite c’est 50 fichiers et 200 Mio par jour, et une adresse réseau partagée par plusieurs Meshloom a le double. Le service a aussi un budget quotidien global. Ce sont les valeurs par défaut du service, qu’il peut modifier : Meshloom n’affiche jamais ce qu’il reste (voir [Quand quelque chose ne va pas](#quand-quelque-chose-ne-va-pas)).

Là où la conversation le permet, un choix **Qui peut ouvrir la photo** propose **Public** et **Chiffré** (Chiffré est présélectionné, puis votre dernier choix est mémorisé). Ailleurs, le panneau indique que la photo est publiée en clair.

## Lire les photos

Dans Meshloom, une photo partagée s’affiche sous le texte de son message, sur 240 pixels de large ou de haut au plus. Un clic l’ouvre en taille réelle dans un nouvel onglet. Au-delà de 8 Mio, une carte **Télécharger l’image** s’affiche à la place. Une photo expirée ou retirée l’indique. C’est ce serveur qui va chercher la photo, jamais votre navigateur, et il en garde une copie dans un cache local pendant une heure au plus (voir [Autres chemins d’installation](/docs/deep/install-paths/#base-de-données-et-mises-à-jour)). Une photo qui n’apparaît pas n’est pas une erreur de votre part : le service peut être injoignable, ou le partage désactivé.

**Réglages > Partage : liens et fichiers > Aperçus des images et liens partagés** décide quand une photo partagée dans un message se charge d’elle-même : toujours, dans les conversations privées seulement (messages directs et canaux à clé secrète propre ; ni Public, ni les canaux dont la clé vient de leur nom ; c’est la valeur par défaut), ou jamais. Sinon la photo affiche un bouton **Toucher pour charger l’image**, et **rien n’est demandé avant que vous touchiez** : ni la photo, ni même une question à son sujet. Les photos des expéditeurs bloqués, et celles de l’écran d’affichage, attendent toujours un toucher. Les aperçus de liens ne sont pas encore récupérés.

Hors de Meshloom, les autres applications MeshCore montrent l’adresse comme du texte. Ouverte dans un navigateur, l’adresse d’une photo publique affiche une page avec la photo, sa taille, sa date d’expiration et un lien **Signaler**. Ajoutez `+` à la fin de l’adresse pour voir seulement son type, sa taille et ses dates, sans afficher la photo. Les moteurs de recherche reçoivent l’instruction de ne pas indexer ces pages.

**Signaler depuis la carte.** Les membres et les administrateurs ont un bouton **Signaler** sous une photo partagée. Choisissez un motif parmi huit (contenu illégal, abus sexuel sur mineur, hameçonnage ou arnaque, logiciel malveillant, spam, droit d’auteur, atteinte à la vie privée, autre), ajoutez des précisions si vous le souhaitez, et le signalement part aux modérateurs du service, signé avec la clé de ce Meshloom. Un Meshloom peut envoyer 5 signalements par jour. Un signalement ne retire pas la photo de votre écran : pour ne plus voir un expéditeur, bloquez-le.

## Lire les liens courts

Un lien court dans un message ressemble à n’importe quel autre lien, et c’est une adresse `https://` ordinaire pour les autres applications MeshCore : elles l’ouvrent dans un navigateur, sans Meshloom. Dans Meshloom, le survoler ou lui donner le focus affiche une petite indication, `→ example.org`, avec l’hôte vers lequel il mène, **sans suivre le lien**. L’indication est demandée une seule fois par lien et gardée dix minutes (trente secondes si le service n’a pas pu être joint), et l’hôte est nettoyé de ses caractères de contrôle ; un nom international s’affiche sous sa forme ASCII.

Le service propose aussi, pour chaque lien, une **page d’aperçu** : ajoutez `+` à la fin de l’adresse (`https://meshl.ink/Ab3dE9x+`) pour voir la destination en texte brut, jamais sous forme de lien, avec ses dates et un bouton **Signaler**, avant de décider de l’ouvrir. Tout le monde peut s’en servir, dans un navigateur, depuis n’importe quel appareil.

L’indication demande que le partage soit activé : quand il est désactivé, un lien court n’est qu’un lien et rien n’est demandé.

Dans les notifications push et les e-mails, une image ou un lien partagé n’affiche jamais son adresse : on lit `[image]`, `[fichier chiffré]` ou `[lien]`, écrit à partir du texte seul, sans aucune requête au service (voir [Notifications](/docs/deep/notifications/#confidentialité-et-ce-qui-quitte-votre-machine)). Un script peut créer des liens et des images par l’API : voir [Fanout](/docs/deep/fanout/#partager-un-lien-ou-une-image-depuis-un-script).

## Fichiers chiffrés

Une photo chiffrée est scellée pour que seules les personnes d’une conversation puissent l’ouvrir. Elle n’existe que là où la clé de la conversation est réellement secrète :

| Conversation | Photo chiffrée ? | Pourquoi |
|---|---|---|
| Le canal Public | Non, toujours publique | Sa clé est connue de tout le monde. |
| Un canal dont la clé vient de son nom (hashtag, avec ou sans `#`) | Non, toujours publique | Quiconque connaît ou devine le nom calcule la clé. |
| Un canal privé (une clé donnée à ses membres) | Oui | Seules les personnes qui ont la clé peuvent ouvrir la photo. |
| Un message direct | Oui | La clé vient de la clé de votre radio et de celle de votre contact. |
| Un serveur de salon | Non, toujours publique | Le serveur lit chaque message. |

Là où le chiffré n’est pas proposé, le panneau le dit : la photo est publiée en clair, et **quiconque a le lien peut la voir**. Rien de ce qui est envoyé sur le canal Public ou dans un canal hashtag n’est privé, avec ou sans cette fonction. Meshloom n’y montre pas de choix parce qu’il ne ferait que donner l’illusion d’être privé.

Comment ça marche. Votre navigateur envoie la photo à votre Meshloom, qui la scelle sur le serveur avec AES-256-GCM et une clé dérivée du secret de la conversation et de l’identifiant du fichier, puis envoie les octets scellés au service. La clé du canal ne retourne jamais dans votre navigateur (le navigateur ne désigne le canal que par un identifiant opaque) et ne va jamais au service. Le nom de votre fichier est lui aussi scellé à l’intérieur.

Ce que voit le service : qu’un fichier existe, sa taille exacte (celle des octets scellés, un peu supérieure à la photo), ses dates, la clé de partage pseudonyme de votre Meshloom et une empreinte des octets scellés. Il ne voit ni le contenu, ni le nom, ni le type, ni qui est dans la conversation. Comme pour toute requête, il voit aussi l’adresse IP de ce Meshloom. La page d’un fichier chiffré (son adresse dans un navigateur) dit la même chose et propose de télécharger les octets scellés, illisibles sans la clé.

Les limites, sans détour :

- **Pas de confidentialité persistante (forward secrecy).** La clé d’une conversation ne change ni d’un fichier à l’autre ni avec le temps. Quiconque obtient plus tard la clé d’un canal privé (une personne que vous invitez, un membre qui l’a copiée, un ancien membre qui l’a gardée) peut lire **tous les fichiers chiffrés encore en ligne**. Et quiconque a enregistré les octets scellés pendant qu’ils étaient en ligne peut les lire dès qu’il obtient la clé, même après l’expiration du fichier. Pour un message direct, quiconque obtient la clé privée de l’une des deux radios est dans la même situation.
- **Quiconque voit le lien peut télécharger le fichier scellé** et voir sa taille et ses dates, mais pas son contenu. Le lien voyage dans le message lui-même, sur les ondes.
- **Meshloom lit lui-même les fichiers.** Votre serveur détient les clés et déchiffre pour tout compte autorisé à lire la conversation (un compte `kiosk` ne lit jamais les messages directs). La photo déchiffrée est envoyée au navigateur avec une consigne de ne pas la mettre en cache, et le cache local ne garde que les octets scellés. Cela protège contre le service et contre les personnes qui n’ont que le lien, pas contre quelqu’un qui contrôle votre Meshloom.
- **Les fichiers expirent au bout de 30 jours**, comme les publics. **Mes envois** les supprime plus tôt.
- **Un lien ne voyage pas.** Meshloom n’ouvre un fichier chiffré que pour la conversation dans laquelle il a été envoyé. Copiez le lien dans une autre conversation et Meshloom affiche « Chiffré pour une autre conversation » et le lien brut. Les autres applications MeshCore ne peuvent pas l’ouvrir non plus : elles montrent un lien vers la page décrite plus haut.
- **Les messages directs ont besoin de la clé de votre radio.** Chaque côté ouvre le fichier avec la clé partagée entre sa radio et celle du contact. Meshloom lit la clé de la radio dès que la radio est connectée ; tant qu’elle n’a pas été lue (la radio est déconnectée, par exemple), les fichiers chiffrés des messages directs ne peuvent ni être envoyés ni être ouverts (« ne peut pas être lu pour le moment »). Les photos publiques, les liens et l’identité de partage n’en dépendent pas. Une fois la radio de retour, les fichiers s’ouvrent de nouveau.
- **Les signalements sont traités sans regarder.** Le service ne peut pas ouvrir un fichier chiffré, donc un signalement doit être précis : le canal ou la conversation, qui l’a partagé et le contexte (servez-vous de la zone **Précisions** du signalement). Sur un tel signalement, le fichier est bloqué sans être consulté. Un second signalement distinct et précis concernant un fichier du même Meshloom entraîne le bannissement de ce Meshloom, c’est-à-dire de tous ceux qui l’utilisent (voir plus bas).

## Ouvrir un fichier chiffré dans un navigateur

Le service a aussi une page qui ouvre les fichiers chiffrés hors de Meshloom. Une image chiffrée partagée dans un **canal privé** peut être ouverte par les membres du canal, depuis n’importe quel appareil, à l’adresse `https://meshl.ink/<id>/decrypt` (la page du fichier y renvoie) : collez la clé du canal, ses 32 caractères hexadécimaux ou le lien `meshcore://` du canal. Le navigateur déchiffre le fichier : la clé n’est ni envoyée au service, ni mise dans l’adresse, ni enregistrée, et la seule requête est le téléchargement du fichier chiffré. Une mauvaise clé affiche une erreur et rien d’autre.

Les fichiers partagés en **message direct** ne peuvent pas y être ouverts : leur clé ne quitte jamais la base radio Meshloom. Ouvrez-les dans Meshloom.

La page déchiffre avec le code que `meshl.ink` envoie à chaque ouverture : s’en servir revient à faire confiance au code servi lors de cette visite. Sinon, ouvrez le fichier dans Meshloom.

## Qui peut faire quoi

Activer ou désactiver le partage et régler les modes demande le rôle **administrateur**. Raccourcir un lien, envoyer une photo, signaler un objet et la liste **Mes envois** demandent le rôle **membre** ou plus. Les lecteurs et les bornes peuvent lire les indications et voir les photos des conversations qu’ils ont le droit de lire (une borne ne voit jamais les messages directs ; voir [Comptes et accès](/docs/deep/access/) pour les rôles). Le serveur le vérifie à chaque requête, quoi que l’interface affiche, et refuse une requête que le navigateur signale comme venant d’un autre site web.

Un Meshloom a **une seule** identité de partage, un seul consentement, un seul quota et un seul bannissement, quelle que soit la personne qui l’utilise. Sur un Meshloom partagé par plusieurs personnes, chaque membre voit dans **Mes envois** tout ce que ce Meshloom a partagé, peut le supprimer, et partage le même quota quotidien. Un bannissement pour l’abus d’une personne arrête tout le monde.

## Quand quelque chose ne va pas

Les messages et erreurs que vous pouvez voir, et leur sens :

- **Le partage est désactivé sur ce Meshloom.** Un administrateur doit l’activer.
- **Le service de partage est injoignable pour le moment.** Le service est en panne, lent ou répond quelque chose d’inattendu. Après trois échecs de suite, Meshloom cesse de l’interroger pendant cinq minutes (puis essaie une fois), pour ne pas le harceler. Tant qu’il est injoignable, le partage ne peut pas être activé.
- **Ce Meshloom a atteint son quota de partage**, **Ce Meshloom a atteint son quota de partage quotidien**, **Trop de partages depuis ce réseau aujourd’hui**, **Le service de partage a atteint sa limite quotidienne**, ou **Trop de demandes de partage** : le service limite ce qu’une instance peut créer. Le message indique la limite atteinte : cette instance, l’adresse réseau depuis laquelle elle partage, ou le service entier pour la journée. Le service ne dit jamais ce qu’il reste. Réessayez plus tard. Meshloom se limite aussi à 60 indications et 20 téléchargements par minute ; au-delà, il refuse localement, sans appeler le service.
- **Le service de partage refuse les liens vers cette adresse** (`share_target_blocked`) : la destination figure sur la liste de blocage du service.
- **Ce fichier est trop volumineux pour être partagé** ou **Ce type de fichier ne peut pas être partagé** (`share_too_large`, `share_type_refused`) : voir les tailles et les types dans [Envoyer une photo](#envoyer-une-photo). Le panneau explique aussi le cas des photos HEIC, qu’un navigateur ne sait pas lire.
- **Les fichiers chiffrés ne sont pas possibles dans cette conversation** (`share_mode_not_allowed`) : le canal Public, un canal hashtag et un serveur de salon n’acceptent que des photos publiques. Voir le tableau de [Fichiers chiffrés](#fichiers-chiffrés).
- **La clé de la radio n’a pas encore été lue** (`share_radio_key_unavailable`) ou **Ce fichier chiffré ne peut pas être déchiffré dans cette conversation** (`share_undecryptable`) : voir les limites des messages directs et des liens dans [Fichiers chiffrés](#fichiers-chiffrés).
- **Le service de partage a banni ce Meshloom.** Le service peut bannir une instance pour abus. Un bannissement concerne **cette instance** : un seul consentement, un seul quota et un seul bannissement pour tous ceux qui l’utilisent, comme pour un club radio qui partage un Meshloom. Tant qu’il est banni, Meshloom cesse d’écrire pendant 12 heures sans interroger le service (le bannissement local prend aussi fin si vous changez un réglage de partage ou redémarrez Meshloom) ; les liens déjà partagés continuent de marcher, et vous pouvez toujours lire des liens. Réglages > Partage affiche un bandeau tant que le bannissement dure, et le bouton **Raccourcir** l’indique. Si vous pensez à une erreur, écrivez à ban@meshloom.app. Community n’est pas touché, puisque la clé de partage n’est pas liée à votre radio.

## Conservation, retrait et contact

Un lien court reste valable 365 jours, puis cesse de fonctionner. Une photo, publique ou chiffrée, reste en ligne 30 jours, puis elle est retirée. Pour supprimer un lien ou un fichier que vous avez partagé plus tôt, utilisez **Réglages > Partage : liens et fichiers > Mes envois** (membres et administrateurs ; la liste montre tout ce que ce Meshloom a partagé, avec le type, la taille, l’expiration et l’état de chaque objet). Un objet bloqué par le service affiche sa catégorie et ne peut pas être supprimé : la page renvoie vers les contacts du service. Supprimer un objet, ou vider le cache de ce Meshloom, ne retire pas une copie déjà présente dans un navigateur : une photo publique peut rester jusqu’à une heure dans le cache du navigateur de la personne qui l’a vue (une photo déchiffrée n’est pas conservée). Sous une photo partagée, le bouton **Signaler** la signale au service ; pour un lien court, utilisez le bouton **Signaler** de sa page d’aperçu (ajoutez `+` à l’adresse) ou la page de signalement du service. Pour une demande de retrait ou toute question sur vos données, utilisez les contacts des pages légales du service, `https://meshl.ink/legal` et `https://meshl.ink/privacy`, qui nomment aussi son éditeur.

Pour le côté technique (ce que contient chaque requête, la clé et les jetons), voir [Sécurité](/docs/deep/security/#ce-qui-sort-de-votre-machine).
