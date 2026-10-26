---
title: Liens courts
description: Raccourcir de longues adresses web avec le service de partage Meshloom : consentement, ce qui sort de votre machine, modes, bannissement et limites.
level: deep
order: 23
---

Un message MeshCore contient 156 octets, et une adresse web en prend souvent davantage. Le **service de partage** transforme une longue adresse en un lien court comme `https://meshl.ink/Ab3dE9x` (25 octets), qui tient dans un message et que tout le monde peut ouvrir. Il est exploité par le projet Meshloom sur son propre domaine, `meshl.ink`, et il est **optionnel et désactivé par défaut** : tant qu’un administrateur ne l’active pas, ce Meshloom ne le contacte jamais, pas même pour résoudre son nom.

Cette version raccourcit des **adresses web** et envoie des **photos** depuis le panneau **Envoyer une photo** de la zone de saisie. Une photo publique peut être ouverte par toute personne qui a son lien. Dans un canal privé ou un message direct, le panneau peut aussi envoyer la photo chiffrée pour cette conversation, que ses membres ouvrent dans Meshloom ; sur le canal Public, un canal dont la clé vient de son nom (avec ou sans `#`, même renommé localement sans son `#`) et un serveur de salon, les photos sont toujours publiques. Meshloom ne peut pas savoir qu’un canal a été renommé avec une autre casse ou sous un nom sans rapport : sa clé peut rester publique. Les sections ci-dessous ne décrivent que ce qui existe.

## L’activer

Ouvrez **Réglages > Partage : liens et fichiers** (administrateurs ; un membre n’y voit que **Mes envois**, voir plus bas). La section indique si le partage est activé, si l’identité de partage de ce Meshloom est prête et si les fichiers chiffrés pour les messages directs sont disponibles.

Cocher **Activer le partage de liens et de fichiers** ouvre toujours une fenêtre de consentement. Elle liste ce qui quitte ce Meshloom, qui exploite le service et combien de temps les données sont conservées, renvoie vers les conditions d’utilisation et la politique de confidentialité du service, et demande de cocher que vous acceptez les conditions. Annuler, fermer la fenêtre ou laisser la case décochée ne change rien. Votre acceptation est enregistrée sur ce Meshloom, avec la version des conditions et la date : si les conditions changent dans une version ultérieure, le partage reste désactivé et la section vous le signale, jusqu’à ce qu’un administrateur lise et accepte les nouvelles conditions en le réactivant. Désactiver le partage ne demande aucun consentement et prend effet aussitôt : les requêtes déjà en route sont interrompues.

Si le service ne répond pas, ses conditions ne peuvent pas être lues : le partage ne peut alors pas être activé avant qu’il réponde de nouveau (le désactiver reste toujours possible).

## Ce qui sort de votre machine

Seul ce serveur contacte le service, jamais votre navigateur, et toujours en HTTPS vers `meshl.ink` uniquement : Meshloom refuse toute redirection vers un autre hôte. Il n’envoie jamais vos clés de canal ni la clé de votre radio.

- **Quand vous raccourcissez un lien** (un message que vous écrivez, ou l’API) : l’adresse à raccourcir, la moitié **publique** de la clé de partage de ce Meshloom dans un jeton signé valable moins de deux minutes et pour cette seule requête, et l’adresse IP de ce Meshloom, que le service voit à chaque requête.
- **Quand vous envoyez une photo :** l’image, réduite dans votre navigateur et sans sa position ni les données de l’appareil, sa taille et son empreinte, le même type de jeton signé, et l’adresse IP de ce Meshloom. La photo est publique : quiconque a son lien peut la voir.
- **Quand vous lisez :** rien à l’affichage d’un message, sauf une photo partagée, que ce serveur télécharge quand le réglage des aperçus plus bas la laisse se charger ou quand vous la touchez (une seule fois : elle est ensuite gardée dans un cache local). Quand vous survolez un lien court ou lui donnez le focus, Meshloom demande au service ce que c’est (sa nature, l’hôte de sa destination et son expiration) pour afficher l’indication décrite plus bas. Ces requêtes portent l’identifiant de l’objet et l’adresse IP de ce Meshloom, sans jeton.

Le service conserve les liens courts 365 jours et les fichiers 30 jours. Ses conditions d’utilisation et sa politique de confidentialité, accessibles depuis la fenêtre de consentement et publiées sur le domaine du service, disent ce qu’il conserve et pour qui.

Meshloom n’utilise jamais la clé de la radio pour cela. Voir [La clé de partage](/docs/deep/security/#la-clé-de-partage) : une clé aléatoire qui lui est propre, créée la première fois que vous raccourcissez un lien, et que le service ne voit que comme un pseudonyme. Les sauvegardes ne contiennent jamais le consentement, l’acceptation des conditions enregistrée, ni cette clé (voir [Sauvegarder la radio](/docs/backup/)).

## Raccourcir depuis le champ de saisie

**Réglages > Partage : liens et fichiers > Raccourcir les liens à l’écriture d’un message** propose trois modes :

- **Automatique** (par défaut) : quand un message est trop long à cause d’une longue adresse, un bouton **Raccourcir** apparaît à côté du compteur d’octets. Il remplace chaque longue adresse de la zone de texte par un lien court, indique combien d’octets il a gagnés et propose **Annuler** (Annuler ne marche que tant que vous n’avez pas modifié le texte depuis). Rien n’est envoyé par ce geste : vous relisez le résultat, puis vous appuyez sur Envoyer.
- **Toujours** : Envoyer raccourcit d’abord les longues adresses, vérifie ensuite la taille, puis envoie. Si le message reste trop long, il reste dans la zone de texte et rien n’est envoyé.
- **Jamais** : les adresses partent telles que vous les avez saisies, et aucun bouton n’apparaît.

Le raccourcissement est tout ou rien : si le service refuse une adresse (voir plus bas), votre texte reste intact. Meshloom laisse tels quels les liens courts eux-mêmes, les adresses déjà assez courtes et les messages `g:`, `m:` et `r:` (GIF, positions et autres messages spéciaux). Il ne raccourcit pas un message qui ne part pas du champ de saisie, comme celui d’une extension. Quand le partage est désactivé, aucun bouton n’apparaît et rien n’est demandé, quel que soit le mode.

Meshloom vérifie l’adresse avant de l’envoyer et **n’envoie jamais** une adresse que le service refuserait de toute façon : une adresse avec nom d’utilisateur ou mot de passe, une adresse IP, `localhost`, un nom sans point ou finissant par `.local`, `.lan`, `.internal` ou `.home.arpa`, tout ce qui n’est pas `http` ou `https`, ou plus long que 2048 caractères. Le bouton ignore une telle adresse et l’API la refuse (`share_invalid_url`) : un lien vers votre réseau domestique, ou qui contient des identifiants, ne le quitte donc jamais.

**Réglages > Partage : liens et fichiers > Aperçus des images et liens partagés** décide quand une image partagée dans un message se charge d’elle-même : toujours, dans les conversations privées seulement (messages directs et canaux à clé secrète propre ; ni Public, ni les canaux dont la clé vient de leur nom), ou jamais. Sinon l’image affiche un bouton **Toucher pour charger**, et rien n’est demandé avant que vous touchiez. Les images des expéditeurs bloqués, et celles de l’écran d’affichage, attendent toujours un toucher. Les aperçus de liens ne sont pas encore récupérés.

## Lire les liens courts

Un lien court dans un message ressemble à n’importe quel autre lien, et c’est une adresse `https://` ordinaire pour les autres applications MeshCore : elles l’ouvrent dans un navigateur, sans Meshloom. Dans Meshloom, le survoler ou lui donner le focus affiche une petite indication, `→ example.org`, avec l’hôte vers lequel il mène, **sans suivre le lien**. L’indication est demandée une seule fois par lien et gardée dix minutes, et l’hôte est nettoyé de ses caractères de contrôle ; un nom international s’affiche sous sa forme ASCII.

Le service propose aussi, pour chaque lien, une **page d’aperçu** : ajoutez `+` à la fin de l’adresse (`https://meshl.ink/Ab3dE9x+`) pour voir la destination en texte brut, jamais sous forme de lien, avec ses dates et un bouton **Signaler**, avant de décider de l’ouvrir. Tout le monde peut s’en servir, dans un navigateur, depuis n’importe quel appareil.

L’indication demande que le partage soit activé : quand il est désactivé, un lien court n’est qu’un lien et rien n’est demandé.

## Ouvrir un fichier chiffré dans un navigateur

Le service a aussi une page qui ouvre les fichiers chiffrés hors de Meshloom. Une image chiffrée partagée dans un **canal privé** peut être ouverte par les membres du canal, depuis n’importe quel appareil, à l’adresse `https://meshl.ink/<id>/decrypt` (la page du fichier y renvoie) : collez la clé du canal, ses 32 caractères hexadécimaux ou le lien `meshcore://` du canal. Le navigateur déchiffre le fichier : la clé n’est ni envoyée au service, ni mise dans l’adresse, ni enregistrée, et la seule requête est le téléchargement du fichier chiffré. Une mauvaise clé affiche une erreur et rien d’autre.

Les fichiers partagés en **message direct** ne peuvent pas y être ouverts : leur clé ne quitte jamais la base radio Meshloom. Ouvrez-les dans Meshloom.

La page déchiffre avec le code que `meshl.ink` envoie à chaque ouverture : s’en servir revient à faire confiance au code servi lors de cette visite. Sinon, ouvrez le fichier dans Meshloom.

## Qui peut faire quoi

Activer ou désactiver le partage et régler les modes demande le rôle **administrateur**. Raccourcir un lien ou envoyer une photo demande le rôle **membre** ou plus. Les lecteurs et les bornes peuvent lire les indications (voir [Comptes et accès](/docs/deep/access/) pour les rôles). Le serveur le vérifie à chaque requête, quoi que l’interface affiche, et refuse une requête que le navigateur signale comme venant d’un autre site web.

## Quand quelque chose ne va pas

Les messages et erreurs que vous pouvez voir, et leur sens :

- **Le partage est désactivé sur ce Meshloom.** Un administrateur doit l’activer.
- **Le service de partage est injoignable pour le moment.** Le service est en panne, lent ou répond quelque chose d’inattendu. Après trois échecs de suite, Meshloom cesse de l’interroger pendant cinq minutes (puis essaie une fois), pour ne pas le harceler. Tant qu’il est injoignable, le partage ne peut pas être activé.
- **Ce Meshloom a atteint son quota de partage**, **Ce Meshloom a atteint son quota de partage quotidien**, **Trop de partages depuis ce réseau aujourd’hui**, **Le service de partage a atteint sa limite quotidienne**, ou **Trop de demandes de partage** : le service limite ce qu’une instance peut créer. Le message indique la limite atteinte : cette instance, l’adresse réseau depuis laquelle elle partage, ou le service entier pour la journée. Le service ne dit jamais ce qu’il reste. Réessayez plus tard. Meshloom se limite aussi à 60 indications et 20 téléchargements par minute ; au-delà, il refuse localement, sans appeler le service.
- **Le service de partage refuse les liens vers cette adresse** (`share_target_blocked`) : la destination figure sur la liste de blocage du service.
- **Le service de partage a banni ce Meshloom.** Le service peut bannir une instance pour abus. Un bannissement concerne **cette instance** : un seul consentement, un seul quota et un seul bannissement pour tous ceux qui l’utilisent, comme pour un club radio qui partage un Meshloom. Tant qu’il est banni, Meshloom cesse d’écrire pendant 12 heures sans interroger le service (le bannissement local prend aussi fin si vous changez un réglage de partage ou redémarrez Meshloom) ; les liens déjà partagés continuent de marcher, et vous pouvez toujours lire des liens. Réglages > Partage affiche un bandeau tant que le bannissement dure, et le bouton **Raccourcir** l’indique. Si vous pensez à une erreur, écrivez à ban@meshloom.app. Community n’est pas touché, puisque la clé de partage n’est pas liée à votre radio.

## Conservation, retrait et contact

Un lien court reste valable 365 jours, puis cesse de fonctionner. Pour supprimer un lien ou un fichier que vous avez partagé, utilisez **Réglages > Partage : liens et fichiers > Mes envois** (membres et administrateurs ; un objet bloqué par le service ne peut pas être supprimé). Supprimer un objet, ou vider le cache de ce Meshloom, ne retire pas une copie déjà présente dans un navigateur : une image publique peut rester jusqu’à une heure dans le cache du navigateur de la personne qui l’a vue. Cette version de Meshloom n’a pas de bouton pour signaler un lien : pour en signaler un, utilisez le bouton **Signaler** de sa page d’aperçu (ajoutez `+` à l’adresse) ou la page de signalement du service. Pour une demande de retrait ou toute question sur vos données, utilisez les contacts des pages légales du service, `https://meshl.ink/legal` et `https://meshl.ink/privacy`, qui nomment aussi son éditeur.

Pour le côté technique (ce que contient chaque requête, la clé et les jetons), voir [Sécurité](/docs/deep/security/#ce-qui-sort-de-votre-machine).
