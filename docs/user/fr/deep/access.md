---
title: Comptes et accès
description: Comptes, rôles, sessions, réseaux de confiance, jetons, double authentification et passkeys, Home Assistant et l’écran kiosque, la connexion Basic de la 4.x, le journal d’audit, et meshloom-admin, la commande qui permet de rentrer depuis la machine, pour chaque mode d’installation de Meshloom.
level: deep
order: 21
---

À partir de Meshloom 5.0, rien ne s’affiche avant la connexion : chaque page et chaque route de l’API demande un compte, et le serveur vérifie le rôle du compte à chaque requête. Cette page donne le tableau complet : qui peut quoi, comment fonctionnent les sessions, les jetons, les seconds facteurs et les réseaux de confiance, comment se connectent Home Assistant et un écran mural, ce que devient une connexion 4.x, le journal d’audit, et comment rentrer quand on est bloqué dehors. Le modèle de confiance en bref est dans [Un réseau de confiance](/docs/trust/) et [Sécurité](/docs/deep/security/).

Si vous perdez le mot de passe de l’administrateur, l’interface web n’est pas nécessaire pour revenir : `meshloom-admin` travaille sur la machine qui détient la base de données. Quiconque peut le lancer à cet endroit contrôle déjà les données de Meshloom, il ne demande donc aucune connexion. Il modifie directement le fichier de la base. Un Meshloom en cours d’exécution voit le changement à sa requête suivante : pas de redémarrage.

## Première connexion

La première fois, Meshloom demande le compte administrateur (voir [Premier lancement](/docs/first-run/)). Un Meshloom qui contenait déjà des données avant de demander des comptes demande aussi un [code d’installation](#le-code-dinstallation), pour que personne d’autre sur le réseau ne puisse le prendre en premier. Sous Home Assistant, le premier utilisateur Home Assistant qui ouvre Meshloom en devient l’administrateur et rien n’est demandé ([Home Assistant](#home-assistant)). Un écran kiosque a sa propre entrée ([Écran kiosque](#écran-kiosque)).

## Les rôles

Chaque compte a l’un des quatre rôles. Le serveur vérifie le rôle à chaque requête, quoi qu’affiche l’interface : une requête hors du rôle est refusée (`auth_forbidden`).

| Rôle | Peut |
|---|---|
| `admin` | Tout : réglages, radio, répéteurs et salons, intégrations et bots, sauvegarde et restauration, clé privée, mises à jour, Community, notifications, rapport de débogage. |
| `member` | Tout ce que peut un `viewer`, plus envoyer des messages, ajouter et modifier contacts, canaux et groupes de contacts, favoris, épingles et canaux en sourdine, abonner son propre navigateur aux notifications push, et créer des jetons d’API pour ses propres appareils et scripts. |
| `viewer` | Lire les canaux et les messages directs, les marquer comme lus, gérer son propre profil et ses sessions. |
| `kiosk` | Lecture seule, pour un écran au mur : messages des canaux (canaux en sourdine et en attente compris), contacts, canaux, paquets, statistiques et carte. Jamais un message direct, aucun réglage, aucun profil. |

Ce que chaque rôle voit dans l’interface suit ce tableau : les écrans qu’un rôle ne peut pas utiliser sont masqués ou grisés au lieu d’échouer au clic.

| | `admin` | `member` | `viewer` | `kiosk` |
|---|---|---|---|---|
| Canaux et paquets | oui | oui | oui | oui (canaux seulement, aucun message direct) |
| Messages directs | oui | oui | oui | non |
| Zone de saisie, nouvelle conversation, épingle, favori, sourdine, suppression | oui | oui | non | non |
| Volets répéteurs et salons, test du maillage | oui | non | non | non |
| Réglages | toutes les sections | Configuration locale, Utilisateurs et accès (son propre compte) | Configuration locale, Utilisateurs et accès (son propre compte) | aucun |
| Langue et thème de ce navigateur | oui | oui | oui | oui, depuis le bouton de compte |

Chaque rôle reçoit le thème de l’instance, ses contacts et noms bloqués et les noms de régions, quoi qu’il puisse modifier : un contact bloqué reste masqué aussi pour un lecteur ou un kiosque. Seuls les administrateurs lisent ou modifient les autres réglages.

Une action que le serveur refuse malgré tout (par exemple depuis une page restée ouverte avant un changement de rôle) affiche un seul message : « Ce compte n’a pas le droit de faire cela. »

Le premier administrateur, et le compte que Meshloom crée à partir de l’identifiant Basic d’une installation 4.x ([plus bas](#la-connexion-basic-de-la-4x)), sont `admin` : une installation avec un seul administrateur fonctionne comme avant.

## Gérer les comptes

Les administrateurs gèrent les comptes dans **Réglages → Utilisateurs et accès**, ou par l’API. La page liste chaque compte avec son rôle et son mode de connexion (local ou Home Assistant), et propose, par compte : changer le rôle, désactiver ou réactiver, réinitialiser le mot de passe (l’utilisateur doit en choisir un nouveau à sa prochaine connexion, sauf si vous décochez), déconnecter partout, et supprimer. Vous ne pouvez pas modifier, désactiver ni supprimer votre propre compte depuis cette page, ce qui garantit qu’il reste un administrateur. Un kiosque n’a pas de mot de passe. Le rôle donné à un utilisateur Home Assistant vu pour la première fois se règle aussi là ; les proxys de confiance restent dans **Configuration locale**.

Chaque utilisateur (sauf le kiosque) voit **Mon compte** et **Mes sessions** sur la même page : modifier son nom affiché et son mot de passe, voir les appareils connectés (navigateur, système, adresse, dernière activité), en révoquer un, ou se déconnecter partout ailleurs.

| Requête | Ce qu’elle fait |
|---|---|
| `GET /api/auth/users` | Liste tous les comptes avec leur rôle, leur origine (`local`, ou `ingress` pour un utilisateur Home Assistant), s’ils sont désactivés et s’ils ont un mot de passe. |
| `POST /api/auth/users` | Crée un compte : `{"username", "role", "password", "display_name"}`. Tout rôle sauf `kiosk` demande un mot de passe initial, que la personne change à sa première connexion (envoyez `"must_change_password": false` pour s’en passer). Un `kiosk` n’a pas de mot de passe. |
| `PATCH /api/auth/users/<id>` | Change le rôle, le nom affiché, `disabled` (`true` bloque le compte) ou `must_change_password`. |
| `POST /api/auth/users/<id>/password` | Fixe un nouveau mot de passe, à changer à la prochaine connexion sauf avec `"must_change_password": false`. |
| `DELETE /api/auth/users/<id>/sessions` | Déconnecte le compte partout. |
| `DELETE /api/auth/users/<id>` | Supprime le compte. |

- **Il reste toujours un administrateur.** Le dernier administrateur actif ne peut être ni rétrogradé, ni désactivé, ni supprimé : la requête est refusée (`auth_last_admin`). Promouvez ou réactivez d’abord un autre administrateur.
- **Les changements s’appliquent aussitôt.** Un nouveau rôle, une désactivation, une suppression ou un mot de passe fixé par un administrateur déconnecte le compte partout, pages ouvertes comprises, et oublie les navigateurs où il était mémorisé. Passer un compte en `kiosk` lui retire son mot de passe.
- **Un mot de passe choisi par un administrateur est provisoire.** Tant que la personne n’a pas choisi le sien, Meshloom ne la laisse rien faire d’autre que le changer (ou se déconnecter) ; la requête est `PATCH /api/auth/me` avec `{"current_password", "new_password"}`. Changer son propre mot de passe déconnecte ses autres navigateurs. Un mot de passe actuel erroné compte comme une connexion ratée, avec les mêmes délais (plus bas).
- **Les utilisateurs Home Assistant** (voir l’add-on plus bas) prennent n’importe quel rôle, `kiosk` compris, et peuvent être désactivés, mais n’ont jamais de mot de passe : c’est Home Assistant qui les connecte. Le rôle donné aux prochains nouveaux est `ingress_default_role` (`member` par défaut), réglé avec `PATCH /api/auth/settings`. Un utilisateur Home Assistant supprimé revient avec ce rôle à sa visite suivante, même celui qui était devenu administrateur en premier (seul le tout premier utilisateur Home Assistant devient administrateur) : désactivez-le plutôt pour le tenir à l’écart.

### Quand un administrateur définit votre mot de passe

Si un administrateur définit votre mot de passe (ou `meshloom-admin reset-password --must-change`), Meshloom affiche **Changez votre mot de passe** à la place de l’application : saisissez le mot de passe actuel, puis un nouveau (12 caractères minimum, avec les règles rappelées en cas de refus). Rien d’autre ne fonctionne avant le changement, puis l’application s’ouvre là où vous étiez. Les autres appareils de ce compte sont déconnectés. Un écran kiosque n’a pas de mot de passe et n’a jamais cette page ; si elle s’affichait, demandez à un administrateur.

## Les sessions

Se connecter ouvre une **session** : Meshloom pose un cookie sur ce navigateur et ne garde qu’une empreinte de son secret. Le cookie dure 30 jours et glisse : chaque visite repousse la fin, donc un navigateur que vous utilisez n’est jamais déconnecté, ni une page qui garde seulement sa connexion en direct ouverte, comme un écran mural. Après 30 jours sans aucune visite, vous vous reconnectez. En HTTPS le cookie s’appelle `__Host-meshloom_session` (sécurisé, propre à l’hôte) ; en `http://` simple c’est `meshloom_session`, que le navigateur envoie en clair : utilisez [HTTPS](/docs/deep/https/) au-delà d’un réseau de confiance.

- **Mes sessions** (**Réglages → Utilisateurs et accès**) liste les navigateurs connectés à votre compte, avec système, adresse et dernière visite. **Révoquer** en déconnecte un, **Déconnecter partout ailleurs** tous sauf celui-ci. La connexion en direct d’une session révoquée se ferme aussitôt.
- **Se déconnecter** révoque la session et efface le cookie.
- Un compte garde au plus 50 sessions : une connexion de plus déconnecte la plus ancienne.
- Un compte désactivé ou supprimé, un rôle ou un mot de passe changé, ou `meshloom-admin revoke-sessions` met fin à ses sessions ; une page déjà ouverte perd sa connexion en direct au plus tard en 30 secondes.
- Les autres sites web ne peuvent pas agir avec votre session : le cookie est `SameSite=Lax` et Meshloom refuse les modifications venues d’un autre site. Sous Home Assistant il n’y a aucun cookie Meshloom (la session de Home Assistant est utilisée).

## Les délais de connexion

Les mauvais mots de passe ralentissent les tentatives suivantes ; ils ne verrouillent jamais un compte.

- **Depuis une adresse, pour un compte :** les 5 premiers mauvais mots de passe ne coûtent rien. Ensuite, la tentative suivante attend 1 seconde, puis 2, 4, 8 et ainsi de suite, jusqu’à 1 minute. Pendant ce temps, la page de connexion indique combien de secondes attendre. Une fois l’attente passée, le bon mot de passe fonctionne et efface le délai.
- **Pour un compte, depuis n’importe où :** après 5 mauvais mots de passe, le compte accepte au plus une tentative par seconde, qui que ce soit qui l’envoie. Quelqu’un qui inonde `admin` n’a qu’un essai par seconde, et un navigateur que Meshloom ne connaît pas est en concurrence avec lui pour cette seconde (voir plus bas).
- **Depuis une adresse, pour tous les comptes :** au-delà de 100 mauvais mots de passe en 15 minutes, cette adresse est refusée jusqu’à ce que son compteur redescende (une tentative de plus toutes les 9 secondes).

Une adresse IPv6 compte pour son bloc /64 : un foyer ou un serveur dispose en général d’un bloc entier et pourrait sinon changer d’adresse à chaque tentative.

**Votre propre navigateur entre toujours.** Après une connexion, Meshloom reconnaît le navigateur grâce à un cookie dédié (pendant 180 jours, pour le dernier compte connecté dessus). Avec ce cookie, le bon mot de passe passe aussitôt, quoi que d’autres envoient et d’où qu’ils l’envoient. Un navigateur qui se trompe 5 fois de mot de passe perd ce passe-droit et suit les règles ci-dessus ; ses erreurs sont oubliées après 15 minutes sans nouvelle erreur, ou à sa prochaine connexion réussie. Changer le mot de passe, le rôle ou le nom du compte, le désactiver, restaurer une copie de la base ou lancer `meshloom-admin rotate-device-key` oublie les navigateurs : la connexion réussie suivante le fait reconnaître de nouveau. Un redémarrage ou une mise à jour les garde.

Depuis un navigateur que Meshloom ne connaît pas encore (nouvel appareil, cookie effacé, après un changement de clé) :

- **Sur une installation Docker (Compose ou un simple conteneur) sans proxy de confiance**, tous les visiteurs peuvent arriver par l’adresse de Docker lui-même : la première et la dernière règle sont coupées et seule la règle par compte s’applique. Quelqu’un sur la même adresse est en concurrence avec vous pour l’unique tentative par seconde : s’il en envoie beaucoup par seconde, il peut vous tenir dehors tant que vous n’utilisez pas un navigateur que Meshloom connaît ou ne listez pas votre reverse-proxy dans **Proxies de confiance**.
- **Derrière un reverse-proxy non listé dans les Proxies de confiance**, tous les visiteurs comptent pour l’adresse du proxy : les délais d’un seul visiteur malveillant s’appliquent à tous. Listez le proxy ; Meshloom journalise un avertissement qui le nomme.
- **Sinon**, chaque visiteur a sa propre adresse et quelqu’un d’ailleurs ne vous fait attendre qu’une seconde au plus.

Les délais sont gardés en mémoire et repartent de zéro au redémarrage de Meshloom. Un délai est écrit une fois dans le journal d’audit, pas à chaque tentative refusée.

## Derrière un reverse-proxy

Les délais de connexion, le journal d’audit et la liste des sessions notent l’adresse d’où vient une requête. Derrière un reverse-proxy, c’est celle du proxy, pour tous les visiteurs, tant que vous ne listez pas le proxy dans **Réglages → Configuration locale → Proxies de confiance** ; Meshloom utilise alors l’adresse du visiteur que le proxy transmet. N’indiquez que l’adresse du proxy lui-même, jamais un réseau Docker ni sa passerelle. Voir [HTTPS, proxies de confiance](/docs/deep/https/#proxies-de-confiance). Meshloom regarde aussi le nom saisi dans le navigateur : en 5.0.0, un nom inconnu (le nom DNS de votre proxy, par exemple) est seulement noté ; à partir de la 5.1 il sera refusé s’il n’est pas déclaré (**Réglages → Utilisateurs et accès → Adresses publiques**). Voir [Les noms auxquels Meshloom répond](/docs/deep/https/#les-noms-auxquels-meshloom-répond). Une mise en place pas à pas pour un tunnel, un VPN ou un proxy : [Accès à distance par vos propres moyens](/docs/deep/remote-self-hosted/).

## Les réseaux de confiance

Un administrateur peut laisser entrer sans identification les appareils d’un réseau choisi, dans **Réglages → Utilisateurs et accès → Réseaux de confiance** : chaque règle est un réseau (`192.168.1.50`, `192.168.1.0/24`) et le compte avec lequel ces appareils agissent. La liste est vide par défaut. Réservez-la à un appareil que vous maîtrisez, comme une tablette murale, avec le plus petit réseau (sa propre adresse) et le rôle le plus faible qui suffit (kiosque ou lecteur). Une connexion par une règle est notée dans le journal d’audit une fois par appareil et par jour.

Une règle est **coupée dès qu’un proxy est dans la boucle**, car derrière un proxy ou un tunnel chaque visiteur, d’où qu’il vienne, arrive avec l’adresse du proxy :

- une requête passée par un reverse-proxy ou un tunnel (elle porte `X-Forwarded-For`, `Forwarded`, `X-Real-IP`, `CF-Connecting-IP` ou un en-tête du même genre), ou venant d’une adresse listée dans les proxies de confiance, n’est jamais connectée par une règle ;
- de même pour les requêtes passées par Meshloom Remote ou l’ingress de Home Assistant (Home Assistant connecte celles-ci lui-même) ;
- une règle ne couvre jamais le loopback de cette machine (`127.0.0.1`, `::1`), sauf dans l’add-on Home Assistant : un proxy local ou un tunnel `ssh -L` y ferait arriver chaque visiteur ;
- l’adresse dans le navigateur doit être une adresse IP, `localhost`, un nom local (`.local`, `.home.arpa`, `.lan`, `.internal`), le nom de cette machine ou une adresse publique que vous avez déclarée, pour qu’une page web ne puisse pas utiliser un nom à elle pour atteindre Meshloom avec ce compte.

Meshloom refuse d’enregistrer une règle qui recoupe un proxy de confiance, une règle qui couvre le loopback hors de l’add-on et, tant qu’une adresse publique est configurée, une règle qui couvre la passerelle Docker. Les réglages avertissent en rouge quand une règle connecte un **administrateur** : toute personne sur ce réseau peut alors changer chaque réglage, compte et plugin. L’**avertissement de sécurité de l’accès**, en haut d’**Utilisateurs et accès**, signale une règle qui connecte un administrateur alors que les plugins tournent avec une isolation réduite, ou qui couvre le loopback ou la passerelle Docker alors qu’une adresse publique est configurée.

## Les jetons d’API et d’appareil

Un script, un capteur Home Assistant ou une application mobile parle à Meshloom avec un **jeton** au lieu d’un mot de passe : un par appareil, pour pouvoir en révoquer un sans toucher aux autres. Les membres et les administrateurs créent les leurs ; un lecteur ou un kiosque ne le peut pas. L’écran du mur de l’image kiosque en a un, que Meshloom écrit sur le Pi lui-même (voir [Raspberry Pi](/docs/rpi/)).

Dans **Réglages → Utilisateurs et accès → Jetons d’API et d’appareil**, choisissez **Créer un jeton** : donnez-lui un nom, cochez ses portées (seules celles que permet votre rôle sont proposées) et choisissez une expiration, ou aucune. Meshloom affiche alors le jeton **une seule fois**, avec un bouton **Copier** : il n’en garde qu’une empreinte et ne peut plus le montrer, copiez-le donc avant de fermer la fenêtre. Si vous le perdez, révoquez-le et créez-en un autre. La liste montre pour chaque jeton ses portées, sa création, sa dernière utilisation et son expiration (un administrateur voit aussi à qui il est) ; **Révoquer** demande une confirmation, puis le jeton cesse de fonctionner aussitôt. Connecté par Home Assistant, la page vous dit que les jetons ne fonctionnent pas à travers l’ingress de l’add-on ; vous pouvez quand même y créer les jetons d’appareils qui joignent Meshloom directement. Les mêmes actions sont disponibles par l’API :

| Requête | Ce qu’elle fait |
|---|---|
| `POST /api/auth/tokens` | Crée un jeton : `{"name": "capteur-ha", "scopes": ["read"]}`, avec en option `"expires_at"` (une heure Unix). La réponse contient le jeton (`mlt_…`) **une seule fois** : copiez-le à ce moment-là, Meshloom n’en garde qu’une empreinte et ne peut plus l’afficher. |
| `GET /api/auth/tokens` | Liste vos jetons (un administrateur voit ceux de tout le monde) : nom, portées, création, dernière utilisation, expiration. Jamais le jeton lui-même. |
| `DELETE /api/auth/tokens/<id>` | Révoque un jeton. Il cesse de fonctionner aussitôt, et une connexion ouverte avec lui est fermée. |

L’appareil envoie le jeton dans un en-tête, `Authorization: Bearer mlt_…`, à chaque requête et à l’ouverture de la connexion en direct (`/api/ws`). Un jeton placé dans l’adresse (`?token=`) est ignoré. Il ne fonctionne pas à travers l’ingress de Home Assistant : faites pointer l’appareil vers l’adresse propre de Meshloom.

Les **portées** disent ce qu’un jeton peut faire : `read` (canaux, contacts, paquets, carte), `read_private` (messages directs), `send` (envoyer, modifier contacts et canaux), `repeater_admin` (répéteurs et rooms) et `admin` (tout). Un jeton n’obtient jamais plus que le rôle de son compte : un membre ne peut pas demander `repeater_admin` ni `admin` (`auth_token_scope`), et si un compte perd un rôle plus tard, ses jetons le perdent aussi. Un compte désactivé ou supprimé emporte ses jetons. Donnez à chaque jeton le moins de portées possible : un tableau de bord n’a besoin que de `read`.

### Appairer un appareil

Une application mobile comme meshenger peut obtenir son jeton en scannant un QR code plutôt qu’en en copiant un. Dans **Réglages → Utilisateurs et accès → Appairer un appareil** (réservé aux administrateurs), choisissez **Appairer un appareil**, nommez l’appareil, cochez les portées dont il a besoin et choisissez la durée de validité du code (de 1 à 10 minutes). Meshloom affiche alors un QR code, dessiné dans votre navigateur, avec un compte à rebours ; scannez-le avec meshenger (ou une autre application qui s’enrôle auprès de Meshloom) sur l’appareil. **Ne partagez pas ce QR code et n’en publiez pas de capture d’écran** : c’est un mot de passe à usage unique pour un nouvel appareil, et quiconque le scanne avant son expiration accède à votre compte avec ces portées. Quand le compte à rebours s’arrête, le QR code disparaît et vous pouvez en créer un nouveau ; **Annuler le code** l’arrête aussitôt, et fermer la fenêtre annule un code encore en attente. Dès que l’appareil a utilisé le code, la fenêtre l’indique. Le panneau liste les codes encore en attente (nom, portées, créateur, expiration), chacun avec un bouton d’annulation. Les mêmes étapes par l’API : un administrateur crée un **code d’appairage** avec `POST /api/auth/pairings` : `{"label": "Téléphone de Nadia", "scopes": ["read", "send"]}`, avec en option `"ttl"` (en secondes, de 30 à 600, 5 minutes par défaut). La réponse contient le code (`mlp_…`) et le texte de son QR code (`meshloom://pair?d=…`), **une seule fois**. Le QR porte le code et les adresses où l’appareil peut joindre Meshloom : vos adresses publiques (**Réglages → Utilisateurs et accès → Adresses publiques**), puis l’adresse locale que vous avez utilisée pour ouvrir Meshloom, seulement si c’est une adresse du réseau local (une IP privée comme `192.168.x.x`, ou un nom en `.local` ; jamais `localhost` ni une adresse publique, que l’application ne joint que par vos adresses publiques en https). L’application envoie le code à `POST /api/auth/pair` avec un nom pour l’appareil et reçoit son propre jeton d’appareil, avec exactement les portées choisies, rattaché à votre compte. Il apparaît ensuite dans votre liste de jetons, où vous le révoquez comme n’importe quel autre.

Un code ne sert **qu’une fois**, et seulement pendant sa durée de vie : un second essai, un code expiré ou révoqué répondent `auth_pairing_expired`, et vous en affichez un nouveau. `GET /api/auth/pairings` liste les codes en attente, `DELETE /api/auth/pairings/<id>` en révoque un aussitôt. Un code ne donne jamais plus que ce que permet votre rôle, même si votre rôle a changé entre-temps. Les mauvais codes ralentissent l’appareil ou l’adresse qui les envoie, comme de mauvais mots de passe, sans ralentir personne d’autre. L’appairage ne fonctionne pas à travers l’ingress de Home Assistant : l’application doit utiliser l’adresse propre de Meshloom. Le contrat technique pour les développeurs d’applications est `docs/dev/device-pairing.md` dans le dépôt Meshloom.

## La double authentification

Tout compte qui a un mot de passe (sauf le kiosque et les utilisateurs Home Assistant) peut ajouter un second facteur : un code à 6 chiffres donné par une application d’authentification (TOTP, RFC 6238 : Aegis, 2FAS, Google Authenticator, un gestionnaire de mots de passe…). La connexion demande alors le mot de passe **et** le code du moment. C’est facultatif ; un administrateur peut l’exiger pour les administrateurs (plus bas). 

### Dans l’interface

Allez dans **Réglages → Utilisateurs et accès → Authentification à deux facteurs**.

1. **Configurer.** Choisissez **Configurer l’authentification à deux facteurs** et confirmez votre mot de passe. Meshloom affiche un QR code et la même clé en texte : scannez le code avec votre application d’authentification, ou saisissez la clé. Le QR code est dessiné dans votre navigateur ; la clé ne part vers aucun autre service. Saisissez le code à 6 chiffres affiché par l’application, puis **Activer**.
2. **Codes de secours.** Meshloom affiche alors dix codes de secours, **une seule fois**. Copiez-les, ou téléchargez le fichier, et gardez-les en lieu sûr (un gestionnaire de mots de passe). Chacun ne sert qu’une fois si vous perdez votre application. Cochez que vous les avez enregistrés pour fermer la liste. Vos autres navigateurs sont déconnectés.
3. **Se connecter.** Après le mot de passe, un second écran demande le code de l’application. **Utiliser un code de secours** bascule vers un code de secours. Un mauvais code est expliqué et vous pouvez réessayer (trop d’essais vous ralentissent). Si vous attendez trop longtemps (5 minutes), ou après cinq mauvais codes, Meshloom vous ramène au mot de passe.
4. **Nouveaux codes de secours, désactivation.** Les deux demandent votre mot de passe et un code actuel (la désactivation accepte aussi un code de secours). De nouveaux codes de secours remplacent tous les anciens.
5. **L’exiger pour les administrateurs.** Les administrateurs voient **Exiger l’authentification à deux facteurs pour les administrateurs** dans la même section. Activez d’abord votre propre second facteur : un administrateur qui ne l’a pas configuré ne voit que la page de configuration tant que ce n’est pas fait.

Connecté par Home Assistant, ou avec un compte sans mot de passe Meshloom (un kiosque), la section indique que l’authentification à deux facteurs ne s’applique pas : Home Assistant vous a déjà authentifié. Les appels d’API ci-dessous font les mêmes choses.

| Requête | Ce qu’elle fait |
|---|---|
| `POST /api/auth/2fa/enrol` | Commence la configuration : `{"password"}`. La réponse contient le secret et un lien `otpauth://` à scanner ou coller dans l’application, **cette seule fois** : aucune autre requête ne réaffiche le secret. |
| `POST /api/auth/2fa/confirm` | L’active avec le premier code affiché par l’application : `{"code": "123456"}`. La réponse contient **dix codes de secours**, affichés une seule fois : imprimez-les ou gardez-les dans un gestionnaire de mots de passe. Vos autres navigateurs sont déconnectés. |
| `GET /api/auth/2fa` | Indique si elle est active et combien de codes de secours restent. |
| `POST /api/auth/2fa/recovery` | Dix nouveaux codes de secours (les anciens cessent de fonctionner) : `{"password", "code"}` avec un code de l’application. |
| `POST /api/auth/2fa/disable` | La désactive : `{"password", "code"}`, un code de l’application ou un code de secours. |

- **Se connecter.** Après un mot de passe correct, `POST /api/auth/login` répond `"authenticated": false` avec un `second_factor.pending_token` ; envoyez-le avec le code à `POST /api/auth/login/2fa` dans les 5 minutes pour obtenir la session. Un code de secours remplace le code de l’application, une fois chacun. Cinq mauvais codes, ou une modification du compte, obligent à recommencer avec le mot de passe.
- **Un code ne sert qu’une fois.** Un code déjà utilisé, ou plus ancien, est refusé même s’il est encore affiché dans l’application : attendez le suivant.
- **Les mauvais codes comptent comme de mauvais mots de passe**, avec les mêmes délais (plus haut).
- **Scripts.** `Authorization: Basic` ([la fenêtre 4.x](#la-connexion-basic-de-la-4x)) cesse de fonctionner pour un compte qui a un second facteur : utilisez un [jeton d’API](#les-jetons-dapi-et-dappareil).
- **L’exiger pour les administrateurs.** `PATCH /api/auth/settings` avec `{"require_2fa_for_admins": true}` (désactivé par défaut). Un administrateur sans second facteur ne peut alors que le configurer (et se déconnecter) tant que ce n’est pas fait ; membres, lecteurs, kiosque et utilisateurs Home Assistant ne sont pas concernés. Recommandé quand Meshloom est joignable depuis Internet.
- **Téléphone perdu.** Utilisez un code de secours. Sans code, un administrateur lance `meshloom-admin disable-2fa <utilisateur>` sur la machine (plus bas) ; l’utilisateur se connecte alors avec son seul mot de passe et la configure de nouveau.

Le secret est gardé dans la base (chiffré au repos dans une version ultérieure). La sauvegarde complète le contient, avec les codes de secours (seulement leurs hachés), uniquement quand **Inclure les comptes** est coché ; le téléchargement de la base n’en garde aucun.

## Les passkeys

Une passkey vous connecte avec votre téléphone, votre ordinateur ou une clé de sécurité (empreinte, visage ou code de l’appareil) au lieu de saisir votre mot de passe. Elle est facultative, et votre mot de passe continue de fonctionner.

Les passkeys demandent une connexion sécurisée avec un nom : Meshloom ne les propose qu’en **https** par une adresse déclarée dans **Réglages → Utilisateurs et accès → Adresses publiques** (derrière un reverse-proxy, listez-le aussi dans **Proxies de confiance** pour que Meshloom voie le https : [Derrière un reverse-proxy](#derrière-un-reverse-proxy)), par Meshloom Remote, ou sur `http://localhost`. En simple `http://` sur le réseau local (une adresse IP ou un nom `.local`), et à travers Home Assistant, elles ne sont pas disponibles : les navigateurs les y refusent. Une passkey ne fonctionne que sur l’adresse où elle a été créée.

### Dans l’interface

- **En ajouter une.** Allez dans **Réglages → Utilisateurs et accès → Passkeys** et choisissez **Ajouter une passkey**. Donnez-lui un nom (par exemple *Portable* ou *Téléphone*), saisissez votre mot de passe et, si la double authentification est activée, un code de votre application d’authentification ou un code de secours, puis suivez la fenêtre de votre navigateur (empreinte, visage, code de l’appareil ou clé de sécurité). La section liste chaque passkey avec son nom, sa date d’ajout et sa dernière utilisation. Un code de secours est consommé dès que vous validez le formulaire, même si vous annulez ensuite la fenêtre du navigateur : il en faudrait un autre pour réessayer.
- **Se connecter avec.** Quand l’adresse accepte les passkeys et que votre navigateur aussi, la page de connexion affiche **Se connecter avec une passkey** sous le formulaire du mot de passe. Sur une adresse en simple `http://`, le bouton est masqué et la page en donne la raison.
- **Comme second facteur.** Après le mot de passe, un compte qui a la double authentification et au moins une passkey voit **Utiliser une passkey** à côté du champ du code.
- **En retirer une.** Choisissez **Supprimer** à côté d’elle et saisissez votre mot de passe. Votre mot de passe continue de fonctionner.
- Si vous annulez la fenêtre du navigateur, la laissez expirer, ou essayez d’ajouter un appareil qui a déjà une passkey pour votre compte, Meshloom le dit et rien ne change. La section n’apparaît pas sous Home Assistant, ni pour un compte sans mot de passe, et en donne la raison.

### API

Les mêmes choses par l’API (comptes avec mot de passe, pas le kiosque ni les utilisateurs Home Assistant) :

| Requête | Ce qu’elle fait |
|---|---|
| `POST /api/auth/webauthn/register/options` | `{"password"}`, plus `"code"` (de l’application, ou un code de secours) quand la double authentification est activée : les options à passer à `navigator.credentials.create()`. |
| `POST /api/auth/webauthn/register/verify` | `{"credential", "name"}` : la réponse du navigateur et un nom pour la passkey. Valable 5 minutes, une fois, depuis la même session. |
| `GET /api/auth/webauthn/credentials` | Vos passkeys : nom, création, dernière utilisation. |
| `DELETE /api/auth/webauthn/credentials/<id>` | `{"password"}` : en retire une. Votre mot de passe vous connecte toujours. |
| `POST /api/auth/webauthn/login/options` | `{}` : les options de `navigator.credentials.get()` pour se connecter sans mot de passe (l’appareil doit vérifier que c’est vous). |
| `POST /api/auth/webauthn/login/verify` | `{"credential"}` : la réponse du navigateur ; ouvre la session. |

- **Comme second facteur.** Avec la [double authentification](#la-double-authentification) activée, une passkey peut remplacer le code après le mot de passe : `POST /api/auth/login` indique `passkey` dans `second_factor.methods`, `POST /api/auth/webauthn/login/options` avec `{"pending_token"}` donne les options, et `POST /api/auth/login/2fa` accepte `{"pending_token", "passkey"}`. Sans double authentification, une passkey ne change rien à la connexion par mot de passe.
- **Une passkey remplace à la fois le mot de passe et le code.** Se connecter avec une passkey seule suffit, même avec la double authentification activée : l’appareil vérifie que c’est vous (empreinte, visage ou code), ce qui vaut pour les deux facteurs. C’est pourquoi ajouter une passkey demande votre mot de passe **et** un code quand la double authentification est activée. Si vous pensez que quelqu’un a utilisé votre compte, passez vos passkeys en revue, retirez celles que vous ne reconnaissez pas, puis changez votre mot de passe.
- **Les échecs comptent comme de mauvais mots de passe**, avec les mêmes délais. Chaque défi ne sert qu’une fois et expire au bout de 5 minutes. Une passkey dont le compteur recule (signe d’une clé de sécurité copiée) est refusée.
- **Appareil perdu.** Connectez-vous avec votre mot de passe et retirez sa passkey. Un administrateur peut retirer toutes les passkeys d’un utilisateur sur la machine avec `meshloom-admin remove-passkeys <utilisateur>` (plus bas).

Meshloom ne garde que la partie publique de chaque passkey. La sauvegarde complète ne les contient que quand **Inclure les comptes** est coché ; le téléchargement de la base les garde.

## Home Assistant

Sur l’add-on Home Assistant, c’est Home Assistant qui vous connecte. Ouvert depuis la barre latérale, Meshloom accepte l’utilisateur Home Assistant qui l’ouvre, sans mot de passe et sans cookie Meshloom. Meshloom ne fait confiance à l’identité que si la requête vient du Supervisor de Home Assistant lui-même ; les mêmes en-têtes envoyés à un port 8000 publié sont ignorés, et cette adresse demande un compte Meshloom comme toute autre installation.

- Le premier utilisateur Home Assistant à ouvrir Meshloom devient son administrateur, même si un administrateur Meshloom existe déjà, pour que la barre latérale mène toujours à un administrateur. Les suivants reçoivent `ingress_default_role` (`member` par défaut, modifiable dans **Utilisateurs et accès**). Seuls les administrateurs Home Assistant voient le panneau : ce sont donc des personnes qui administrent déjà votre Home Assistant.
- Un compte Meshloom par utilisateur Home Assistant ; son nom affiché suit celui de Home Assistant. Un tel compte n’a ni mot de passe, ni second facteur, ni passkey. Vous changez son rôle, le désactivez ou le supprimez dans **Utilisateurs et accès** (un compte supprimé revient à sa prochaine visite : désactivez-le plutôt).
- Home Assistant et ses add-ons se font mutuellement confiance : un add-on à qui vous ne confieriez pas votre Home Assistant ne doit pas tourner à côté de Meshloom.
- Par la barre latérale, les réseaux de confiance, les passkeys et les jetons d’API ne s’appliquent pas (voir chaque section). Pour joindre Meshloom depuis une application mobile ou un script, donnez un port hôte à `8000/tcp` dans le panneau **Réseau** de l’add-on ; cette adresse demande un compte Meshloom. Si un reverse-proxy ou un tunnel la sert sous un nom, déclarez ce nom dans **Adresses publiques** ([HTTPS](/docs/deep/https/#les-noms-auxquels-meshloom-répond)).

## Écran kiosque

Le rôle `kiosk` est pour un écran au mur (un club, un atelier) : il lit les canaux, les contacts, les paquets, les statistiques et la carte, jamais un message direct, et n’a ni mot de passe ni réglages. Deux façons de l’utiliser :

- **L’image kiosque Raspberry Pi** crée elle-même le compte `kiosk` et connecte l’écran avec un **jeton d’appareil** qu’elle écrit sur le Pi, lisible seulement par l’utilisateur du bureau, valable seulement depuis le Pi lui-même et jamais visible dans une adresse (voir [Raspberry Pi](/docs/rpi/#image-kiosque-écran-au-mur)). Un administrateur le voit sous le nom **Kiosk screen** dans **Jetons d’API et d’appareil** ; **Révoquer** le remplace, et désactiver le compte `kiosk` garde l’écran déconnecté.
- **Tout autre écran** : créez un compte `kiosk` dans **Utilisateurs et accès**, puis ajoutez une règle de réseau de confiance qui associe l’adresse de l’écran à ce compte ([plus bas](#les-réseaux-de-confiance)). Un compte `kiosk` n’a pas de mot de passe : il ne peut se connecter autrement.

## La connexion Basic de la 4.x

Jusqu’à la 4.18, `MESHCORE_BASIC_AUTH_USERNAME` et `MESHCORE_BASIC_AUTH_PASSWORD` (les options `basic_auth_username` et `basic_auth_password` de l’add-on) formaient un identifiant partagé. À partir de la 5.0 :

- Au **premier démarrage sans compte**, quand les deux sont définies, elles deviennent le compte administrateur (même nom, même mot de passe, sans code d’installation). Meshloom ne les relit jamais ; il écrit un avertissement à chaque démarrage tant qu’elles sont définies : retirez-les. Une seule définie n’importe rien.
- **Fenêtre de compatibilité.** Jusqu’à la 5.2, les scripts qui envoient ce nom et ce mot de passe avec `Authorization: Basic` continuent de fonctionner, pour tout compte qui a un mot de passe et pas de second facteur, avec une courte pause après un échec. Un avertissement d’obsolescence est écrit dans le journal. En 5.2 l’en-tête est retiré : passez dès maintenant vos scripts à un [jeton d’API](#les-jetons-dapi-et-dappareil).
- Un nom d’utilisateur 4.x de plus de 64 caractères ne peut pas devenir un compte : l’import est ignoré, avec une erreur dans le journal, et la première connexion normale s’applique.

## Le journal d’audit

Meshloom tient un journal des actions sensibles : connexions (réussies ou non), déconnexions et sessions déconnectées, comptes créés, renommés, changés de rôle, désactivés, réactivés ou supprimés (refus compris, comme une tentative de rétrograder le dernier administrateur), changements de mot de passe, double authentification activée ou désactivée, nouveaux codes de secours et connexions en attente de leur code, passkeys ajoutées ou retirées, création du premier administrateur, changements des réglages d’accès, jetons créés et révoqués et utilisations d’un jeton révoqué ou expiré, robots créés, modifiés, supprimés ou arrêtés, restaurations de sauvegarde, imports de clé privée, réinitialisations d’usine de la radio et mises à jour lancées. Chaque commande `meshloom-admin` ci-dessous écrit aussi une entrée, y compris quand elle est refusée.

Chaque entrée garde l’heure, le compte qui a agi (aucun pour une connexion ratée ou la ligne de commande), l’adresse d’où il venait, la voie d’accès (réseau local, Home Assistant, ligne de commande, ou Meshloom lui-même pour une mise à jour automatique), ce qui a été visé et l’issue. Elle ne contient jamais de mot de passe, de code d’installation, de session, de clé ni le code d’un robot. Une connexion ratée ne nomme le compte que si le nom saisi correspond à un compte ; tout autre nom saisi est noté comme inconnu, si bien qu’un mot de passe tapé dans le champ du nom n’est pas gardé.

Les administrateurs le lisent dans **Réglages → Utilisateurs et accès → Journal d’audit** (masqué aux autres), ou par `GET /api/auth/audit` : du plus récent au plus ancien, par pages, avec un bouton pour charger les entrées plus anciennes. Les filtres (famille d’action, action, résultat, compte, dates) restreignent les entrées déjà chargées ; chargez des entrées plus anciennes pour remonter plus loin. Les entrées sont gardées **366 jours**, puis supprimées par un nettoyage horaire ; un administrateur change cette durée avec `PATCH /api/auth/settings` et `{"audit_retention_days": <1 à 36500>}`. Le journal n’est pas dans les sauvegardes JSON ; le téléchargement de la base (**Réglages → Base de données**) le conserve.

## Sauvegarde et restauration

La sauvegarde complète (**Réglages → Base de données**) contient les comptes quand **Inclure les comptes** est coché, ce qui est le cas par défaut : leurs rôles, hachés de mots de passe, secrets de double authentification et passkeys, et les jetons d’API (seulement leurs empreintes, si bien que les appareils continuent de fonctionner après une restauration), jamais les sessions ni le journal d’audit. La restaurer sur une nouvelle machine rend les mêmes comptes et mots de passe. Une restauration remplace les comptes et déconnecte tout le monde ; elle ne laisse jamais Meshloom sans administrateur actif qui peut se connecter ici (un compte avec mot de passe, ou un utilisateur Home Assistant sur l’add-on) : un fichier qui n’en a pas, ou sans comptes du tout, conserve les comptes actuels. Détails sur la [page de sauvegarde](/docs/backup/#contenu-de-la-sauvegarde). Le téléchargement de la base garde les comptes mais pas leurs mots de passe : après l’avoir remis en place, réinitialisez un mot de passe avec `meshloom-admin` (ci-dessous).

## Les commandes

| Commande | Effet |
|---|---|
| `meshloom-admin reset-password <utilisateur>` | Définit un nouveau mot de passe (demandé deux fois, ou une ligne sur l’entrée standard avec `--stdin`) et déconnecte l’utilisateur partout. `--must-change` en demande un autre à la prochaine connexion. Refusé pour un compte `kiosk` ou Home Assistant, qui n’a pas de mot de passe. |
| `meshloom-admin create-admin <utilisateur>` | Crée un administrateur. Mêmes options de mot de passe, plus `--display-name`. |
| `meshloom-admin setup-code` | Émet un nouveau code d’installation et l’affiche (voir plus bas). |
| `meshloom-admin list-users` | Liste les comptes : identifiant, rôle, fournisseur, actif ou désactivé, mot de passe défini ou non. `--json` pour les scripts. |
| `meshloom-admin disable-user <utilisateur>` | Bloque un utilisateur et le déconnecte. Refuse de désactiver le dernier administrateur actif sauf avec `--force`. |
| `meshloom-admin enable-user <utilisateur>` | Autorise de nouveau un utilisateur désactivé à se connecter. |
| `meshloom-admin disable-2fa <utilisateur>` | Retire la double authentification et les codes de secours d’un utilisateur (téléphone perdu). Il se connecte ensuite avec son seul mot de passe. |
| `meshloom-admin remove-passkeys <utilisateur>` | Retire toutes les passkeys d’un utilisateur (appareil perdu ou volé). Le mot de passe continue de fonctionner. |
| `meshloom-admin revoke-sessions <utilisateur>` ou `--all` | Déconnecte un utilisateur, ou tout le monde. |
| `meshloom-admin host-guard-observe` | Remet le [contrôle du Host](/docs/deep/https/#les-noms-auxquels-meshloom-répond) en mode observation, quand le mode strict refuse le seul nom par lequel vous atteignez Meshloom. |
| `meshloom-admin rotate-device-key` | Oublie tous les navigateurs reconnus (voir [Votre propre navigateur entre toujours](#les-délais-de-connexion)) : chacun suit les délais ordinaires jusqu’à sa prochaine connexion réussie. |

Un Meshloom en cours d’exécution voit ces changements dès sa requête suivante ; un navigateur resté ouvert sur une page déconnectée de cette façon perd sa connexion en direct en moins de 30 secondes.

Un mot de passe compte au moins 12 caractères et n’est jamais lu sur la ligne de commande, il reste donc hors de l’historique de votre shell. `meshloom-admin --help` et `meshloom-admin <commande> --help` listent toutes les options. Une commande se termine par le code 0 si elle a réussi, et par 1 avec un message sinon, par exemple quand le fichier de la base est absent, illisible ou pas encore mis à niveau (lancez d’abord Meshloom une fois dans la nouvelle version).

### Le code d’installation

Quand Meshloom est mis à jour depuis une version plus ancienne, le premier administrateur doit prouver qu’il a accès à la machine en saisissant un code d’installation. Meshloom ne garde qu’une empreinte du code, qui ne peut donc pas être réaffiché : `meshloom-admin setup-code` en fabrique un **nouveau** et le précédent cesse de fonctionner. La commande ne marche que tant qu’aucun compte n’existe ; ensuite, utilisez `create-admin` ou `reset-password`.

Le code a 8 chiffres ; Meshloom en imprime aussi un nouveau dans son journal à chaque démarrage tant qu’aucun compte n’existe. Les espaces ou tirets saisis au milieu sont ignorés. Une installation neuve ne demande pas de code. Si Meshloom n’a pas pu préparer le premier démarrage (le journal le dit), la création de l’administrateur est refusée jusqu’à ce qu’il y arrive ; redémarrez Meshloom si cela dure. Un code imprimé entre-temps par `meshloom-admin setup-code` reste valable : Meshloom ne le remplace pas quand il réessaie.

## La commande pour chaque installation

Remplacez `alice` par le nom du compte.

### Paquet Linux (apt ou dnf)

Lancez-la avec `sudo` :

```bash
sudo meshloom-admin reset-password alice
```

La commande refuse de s’exécuter sans les droits root. En root, elle lit le chemin de la base dans `/etc/meshloom/meshloom.env` (un fichier que seul root peut lire), puis passe à l’utilisateur `meshloom`, qui possède la base : SQLite ne laisse ainsi aucun fichier que le service ne peut pas écrire. La même commande existe sous le nom `/opt/meshloom/.venv/bin/meshloom-admin`.

### Docker et Docker Compose

Lancez-la dans le conteneur, depuis le dossier qui contient `docker-compose.yml` :

```bash
docker compose exec meshloom meshloom-admin reset-password alice
```

Avec `docker run`, utilisez `docker exec -it <conteneur> meshloom-admin reset-password alice`. Si le conteneur tourne sous l’utilisateur 10001 (`MESHLOOM_RUN_AS_USER`), ajoutez `--user 10001` à `exec`, pour que les fichiers de la base gardent leur propriétaire. Pour alimenter un script, lisez le mot de passe sans l’afficher et passez-le sur l’entrée standard, il n’apparaît ainsi jamais dans l’historique de votre shell : `read -rs pw; printf '%s\n' "$pw" | docker compose exec -T meshloom meshloom-admin reset-password alice --stdin`.

### Add-on Home Assistant

Par la barre latérale, c’est Home Assistant qui vous connecte : Meshloom accepte l’utilisateur Home Assistant qui l’ouvre, sans mot de passe propre. Le premier utilisateur Home Assistant qui ouvre Meshloom en devient l’administrateur, même si un administrateur Meshloom existe déjà (issu des options `basic_auth_*` de la 4.18 ou de `meshloom-admin`), pour que la barre latérale mène toujours à un administrateur ; les suivants reçoivent le rôle **Membre** (un administrateur peut le changer, ainsi que le rôle de chacun : voir « Gérer les comptes » plus haut). Seuls les administrateurs de Home Assistant voient le panneau Meshloom : ce sont donc des personnes qui administrent déjà votre Home Assistant. Si vous ne pouvez pas ouvrir Meshloom, rétablissez votre accès dans Home Assistant lui-même. Si vous avez donné un port d’hôte au port 8000 de l’add-on, cette adresse demande un compte Meshloom comme toute autre installation. La commande existe quand même pour le cas rare où elle serait utile. Elle s’exécute dans le conteneur de l’add-on et demande donc d’atteindre le Docker de l’hôte : l’add-on *Advanced SSH & Web Terminal* avec le **Mode protégé** désactivé.

```bash
docker ps --format '{{.Names}}' | grep meshloom
docker exec -it addon_xxxxxxxx_meshloom meshloom-admin list-users
```

Utilisez le nom affiché par la première commande. La base de l’add-on est `/config/meshcore.db` dans le conteneur, que la commande trouve toute seule.

### Image Raspberry Pi

L’image installe le paquet Linux, la commande est donc la même. Ouvrez un shell sur le Pi en SSH (l’utilisateur et la clé choisis dans Raspberry Pi Imager), puis :

```bash
sudo meshloom-admin reset-password alice
```

Si vous avez besoin du code d’installation et ne pouvez pas lire le journal, `meshloom-admin setup-code` en donne un nouveau en SSH.

### Copie du code

Depuis le dossier de Meshloom, avec l’utilisateur qui le lance :

```bash
cd ~/meshloom
uv run python -m app.cli reset-password alice
```

La base est `data/meshcore.db` dans ce dossier.

## Si la commande échoue

- **`database not found`** : la commande a regardé le mauvais fichier. Donnez-lui le chemin avec `--db /chemin/vers/meshcore.db`, ou définissez `MESHCORE_DATABASE_PATH`.
- **`run it as root`** (paquet) : ajoutez `sudo` devant.
- **`cannot read /etc/meshloom/meshloom.env`** : donnez la base avec `--db /chemin/vers/meshcore.db` ou définissez `MESHCORE_DATABASE_PATH`.
- **`cannot read database` ou `cannot open database for writing`** : vous n’êtes pas l’utilisateur propriétaire du fichier. Utilisez `sudo meshloom-admin` (paquet) ou `docker compose exec` (Docker).
- **`start Meshloom once`** : la base est plus ancienne que le programme. Lancez Meshloom pour qu’il la mette à niveau, puis relancez la commande.
