---
title: Notifications push
description: Des notifications dans le navigateur même quand l’onglet est fermé. HTTPS requis.
level: deep
order: 18
---

Web Push peut prévenir votre navigateur d’un message entrant alors que l’onglet de Meshloom est fermé. Cette page couvre ce qui est propre aux navigateurs : HTTPS, l’adresse de contact (VAPID), l’abonnement et la liste des appareils. Tout le reste, c’est-à-dire les canaux, les événements, le tableau des règles, Ne pas déranger, les mentions, la cloche, les sauvegardes et les autres façons d’être prévenu (e-mail, webhook, ntfy, Discord, Telegram, Gotify, Home Assistant, Apprise), est sur [Notifications](/docs/deep/notifications/).

Web Push est un canal parmi d’autres (le canal intégré **Web Push**). Chaque navigateur a son propre abonnement, qui appartient au compte qui l’a abonné, alors que les règles (quoi notifier, et pour quelles conversations) sont communes à toute l’installation et à tous les canaux. C’est distinct du [fanout](/docs/deep/fanout/). Il n’y a pas de fenêtre contextuelle depuis l’onglet ouvert : Web Push est la seule façon dont Meshloom notifie un navigateur. En HTTP simple, ntfy, Gotify ou Home Assistant sur votre réseau sont la réponse.

## Ce qu’il vous faut

**HTTPS.** Les navigateurs n’autorisent le composant d’arrière-plan qui reçoit les notifications (le service worker) que sur une page sécurisée. Un certificat fabriqué par vous peut convenir ; voir [HTTPS](/docs/deep/https/). Pour joindre Meshloom de l’extérieur avec un nom et un certificat que vous contrôlez, voir [Accès à distance par vos propres moyens](/docs/deep/remote-self-hosted/).

**Un accès à Internet depuis le serveur.** Les notifications passent par des services tenus par les éditeurs de navigateurs : Google (FCM), Mozilla ou Apple (APNs).

## L’adresse de contact (VAPID)

Meshloom signe chaque notification avec une paire de clés créée au premier démarrage. La signature porte aussi une adresse de contact, appelée sujet VAPID.

Réglez-la dans **Réglages → Notifications → Canaux**, champ **Sujet VAPID** de la carte Web Push (**Appareils et réglages Web Push**). Elle doit être `mailto:vous@domaine.tld` (recommandé) ou `https://votre-hote` sans chemin. Quand le champ est vide, Meshloom utilise `mailto:noreply@meshcore.local`.

**Apple exige une vraie adresse.** APNs rejette un sujet qui n’est pas un vrai contact, ainsi que le `.local` par défaut, avec `403 BadJwtToken`. Saisissez une vraie adresse dans ce champ.

La variable d’environnement `MESHCORE_VAPID_SUBJECT` n’est plus lue. Si elle était encore définie lors de la mise à jour, Meshloom l’a recopiée une seule fois dans ce champ, au premier démarrage, à condition qu’il soit vide. L’option `vapid_subject` de l’add-on Home Assistant suit le même chemin : elle est recopiée une fois dans ce champ, et elle est dépréciée (elle disparaît à la version suivante).

Voir la [documentation Apple](https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers). D’autres services de push peuvent accepter la valeur par défaut : le problème n’apparaît donc souvent qu’avec le premier appareil Apple.

## Abonner un navigateur

Dans **Réglages → Notifications → Canaux**, ouvrez **Appareils et réglages Web Push** sur la carte Web Push et choisissez **Abonner ce navigateur**. La liste des appareils enregistrés montre alors chaque navigateur, avec un bouton **Tester** et un moyen de le désabonner.

L’abonnement d’un navigateur décide seulement si ce navigateur reçoit les notifications. Les règles sont communes : si vous activez une conversation depuis votre téléphone, elle l’est pour tous les navigateurs abonnés. Ce qu’il faut notifier se choisit dans [Notifications](/docs/deep/notifications/#le-tableau-des-règles), cloche et Ne pas déranger compris.

### À qui sont les appareils

Chaque compte ne voit et ne gère que ses propres appareils : **Vos appareils** liste les navigateurs que vous avez abonnés, et aucun autre compte ne peut les renommer, les tester ni les retirer. Un navigateur partagé par deux comptes appartient au dernier qui l’a abonné.

Un administrateur voit aussi **Appareils des autres comptes** : l’étiquette de chaque appareil, son compte et le service push par lequel il passe (jamais son adresse complète, qui sert de mot de passe pour ce navigateur), avec un bouton pour le retirer, pour un téléphone perdu ou un ancien membre. Renommer ou tester un appareil reste l’affaire de son propriétaire.

Un appareil ne reçoit des notifications que tant que son compte est actif et peut envoyer des messages (membre ou administrateur). Un lecteur ou un kiosque ne peut pas s’abonner ; les appareils d’un compte désactivé restent muets jusqu’à sa réactivation, et supprimer un compte supprime ses appareils. Les appareils enregistrés avant que les comptes les possèdent (avant Meshloom 5.0, ou restaurés depuis une sauvegarde) appartiennent au premier administrateur.

## Service worker et nettoyage

`sw.js` affiche les notifications reçues et, au clic, met au premier plan ou ouvre la bonne conversation. Si un service de push répond `403`, `404` ou `410` pour un abonnement, Meshloom le supprime. Il faut alors réabonner le navigateur.

## Points d’accès

| Méthode | Point d’accès | Effet |
|---------|---------------|-------|
| GET | `/api/push/vapid-public-key` | Clé publique utilisée pour s’abonner |
| POST | `/api/push/subscribe` | Enregistre ou met à jour un abonnement |
| GET | `/api/push/subscriptions` | Liste vos abonnements ; `?scope=all` liste ceux de tous les comptes (administrateurs seulement, l’adresse réduite au service push) |
| PATCH | `/api/push/subscriptions/{id}` | Change l’étiquette ou la langue d’un de vos abonnements |
| DELETE | `/api/push/subscriptions/{id}` | Supprime un de vos abonnements (un administrateur : n’importe lequel) |
| POST | `/api/push/subscriptions/{id}/test` | Envoie une notification de test à un de vos abonnements |
| GET | `/api/push/preferences` | **Déprécié.** Valeurs par défaut, exceptions et sujet VAPID |
| PATCH | `/api/push/preferences` | **Déprécié.** Modifie les valeurs par défaut et/ou le sujet VAPID |
| PUT | `/api/push/preferences/conversations/{key}` | **Déprécié.** Crée ou efface une exception pour une conversation |

Un abonnement est unique par son adresse : enregistrer à nouveau met à jour l’existant et le donne au compte qui l’enregistre. L’abonnement d’un autre compte répond `404`, comme s’il n’existait pas. Les anciennes routes `/api/push/conversations` n’existent plus.

Les trois routes `preferences` sont **dépréciées** : elles répondent un en-tête `Deprecation` et seront retirées en 5.2.0. Utilisez plutôt `/api/notifications` ; le détail, et ce que veut dire la réponse `409 legacy_managed`, sont dans [Mise à jour et retour en arrière](/docs/deep/notifications/#mise-à-jour-et-retour-en-arrière) et [Points d’accès](/docs/deep/notifications/#points-daccès).

## Services push sur un réseau privé

Les services push des navigateurs (Google, Mozilla, Apple) sont sur Internet. `POST /api/push/subscribe` refuse une adresse sur un réseau privé (réponse `400`, code `egress_private_address`), et Meshloom n’y envoie jamais rien, sauf si **Accepter les points de terminaison push sur un réseau privé** est coché dans **Réglages → Notifications** (réglage `push_allow_private_endpoints` de `/api/settings`). Cochez-le uniquement pour un distributeur UnifiedPush auto-hébergé sur votre réseau local ou sur cette machine. Comme pour le webhook, chaque push part uniquement vers l’adresse vérifiée par Meshloom (le certificat du service push reste contrôlé pour son nom), et une redirection n’est jamais suivie.

La vérification a lieu de nouveau avant chaque push. Un appareil dont l’adresse est mal formée, ou pointe désormais vers une adresse refusée quels que soient les réglages (métadonnées cloud comme `169.254.169.254`, plages de documentation), est ignoré et supprimé (quand seule la réponse DNS pointe là, il n’est supprimé que si un push au moins une heure plus tard la retrouve, un résolveur pouvant se tromper une fois) ; le journal du serveur indique `Push endpoint refused for good by the egress guard` avec le code du motif et l’identifiant de l’appareil, jamais son adresse (elle contient un jeton). Un appareil refusé seulement parce que la case est décochée, ou dont le nom est introuvable pour l’instant, est conservé et le journal le signale une fois.

Lors d’une mise à jour, si un appareil enregistré avant cette vérification n’est pas un service push de navigateur (un distributeur UnifiedPush, peut-être sur votre réseau local), la case est cochée d’office pour que l’appareil continue de recevoir, et un avis dans **Réglages → Notifications** le signale jusqu’à ce que vous le fermiez. Décochez la case si aucun de vos appareils n’est sur votre réseau. Avec uniquement des services push de navigateur, ou un distributeur public comme ntfy.sh, elle reste décochée. Seul un appareil dont l’adresse est une IP privée, un nom sans point ou un nom en `.local`, `.lan`, `.home.arpa`, `.internal` ou `.localdomain` compte comme étant sur votre réseau (Meshloom ne fait aucune résolution DNS pour cela).

Un distributeur auto-hébergé joint par un nom d’apparence publique mais qui pointe vers votre réseau (DNS interne, nom Tailscale `*.ts.net`) ne peut pas être reconnu ainsi. Si la mise à jour décoche la case pour un tel appareil, **Réglages → Notifications** affiche un avis avec un bouton **Autoriser les réseaux privés** qui la coche en un clic ; rien n’est supprimé, et un push refusé parce que la case est décochée n’est jamais compté comme un échec : l’appareil reste enregistré. Pour un tel distributeur sur une installation neuve, cochez la case à la main.

Restaurer une sauvegarde d’avant cette vérification suit la même règle : si un appareil restauré est sur votre réseau, la case est cochée et le même avis apparaît.

## Quand rien n’arrive

1. Vérifiez que la page est servie en HTTPS (ou depuis `localhost`).
2. Vérifiez l’autorisation de notifications dans votre navigateur et votre système.
3. Appuyez sur **Test** sur l’appareil dans la liste : un échec dit seulement de quel type il est (injoignable, pas de réponse à temps, échec TLS, ou abonnement que le service push n’accepte plus), jamais un nom d’hôte ni une adresse. Le journal du serveur indique le type d’erreur sous `Test push failed`.
4. Sur Apple, vérifiez le sujet VAPID (voir plus haut) et cherchez `403 BadJwtToken` dans le journal du serveur.
5. Vérifiez que le serveur peut joindre Internet.
6. Pour les règles (l’événement est-il coché, une sourdine, Ne pas déranger, une exception de conversation), voir [Quand rien n’arrive](/docs/deep/notifications/#quand-rien-narrive) sur la page Notifications.

Au niveau de journal `DEBUG`, les réponses des services de push sont écrites dans le journal. Voir [Dépannage](/docs/deep/troubleshooting/).
