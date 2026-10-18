---
title: Notifications push
description: Des notifications même quand l’onglet est fermé. HTTPS requis.
level: deep
order: 18
---

Web Push peut prévenir votre navigateur d’un message entrant alors que l’onglet de Meshloom est fermé. C’est distinct du [fanout](/docs/deep/fanout/) : chaque navigateur a son propre abonnement, alors que les règles (quoi notifier, et pour quelles conversations) sont communes à toute l’installation.

Il n’y a pas de fenêtres contextuelles venant de l’onglet ouvert lui-même. Web Push est la seule manière dont Meshloom notifie un navigateur.

Les mêmes règles peuvent aussi envoyer un **e-mail**, appeler un **webhook** ou notifier par **Apprise** (ntfy, Telegram, Discord, Home Assistant et bien d’autres). Le push est activé par défaut. L’e-mail, le webhook et Apprise sont désactivés par défaut, et ne fonctionnent qu’une fois une destination configurée. Voir « Choisir ce qui notifie » plus bas.

## Ce qu’il vous faut

**HTTPS.** Les navigateurs n’autorisent le composant d’arrière-plan qui reçoit les notifications (le service worker) que sur une page sécurisée. Un certificat fabriqué par vous peut convenir ; voir [HTTPS](/docs/deep/https/). Pour joindre Meshloom de l’extérieur avec un nom et un certificat que vous contrôlez, voir [Accès à distance par vos propres moyens](/docs/deep/remote-self-hosted/).

**Un accès à Internet depuis le serveur.** Les notifications passent par des services tenus par les éditeurs de navigateurs : Google (FCM), Mozilla ou Apple (APNs).

## L’adresse de contact (VAPID)

Meshloom signe chaque notification avec une paire de clés créée au premier démarrage. La signature porte aussi une adresse de contact, appelée sujet VAPID.

Réglez-la dans **Réglages → Notifications**, champ **Sujet VAPID**. Elle doit être `mailto:vous@domaine.tld` (recommandé) ou `https://votre-hote` sans chemin. Quand le champ est vide, Meshloom utilise `mailto:noreply@meshcore.local`.

**Apple exige une vraie adresse.** APNs rejette un sujet qui n’est pas un vrai contact, ainsi que le `.local` par défaut, avec `403 BadJwtToken`. Saisissez une vraie adresse dans ce champ.

La variable d’environnement `MESHCORE_VAPID_SUBJECT` n’est plus lue. Si elle était encore définie lors de la mise à jour, Meshloom l’a recopiée une seule fois dans ce champ, au premier démarrage, à condition qu’il soit vide. L’option `vapid_subject` de l’add-on Home Assistant suit le même chemin : elle est recopiée une fois dans ce champ, et elle est dépréciée (elle disparaît à la version suivante).

Voir la [documentation Apple](https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers). D’autres services de push peuvent accepter la valeur par défaut : le problème n’apparaît donc souvent qu’avec le premier appareil Apple.

## Abonner un navigateur

Dans **Réglages → Notifications**, choisissez **Abonner ce navigateur**. La liste des appareils enregistrés montre alors chaque navigateur, avec un bouton **Tester** et un moyen de le désabonner.

L’abonnement d’un navigateur décide seulement si ce navigateur reçoit les notifications. Les règles sont communes : si vous activez une conversation depuis votre téléphone, elle l’est pour tous les navigateurs abonnés.

## Choisir ce qui notifie

### Les événements

**Réglages → Notifications → Notifications par défaut** a une ligne par événement, avec quatre colonnes : **Push**, **E-mail**, **Webhook** et **Apprise**. Le push est activé pour chaque événement au départ.

| Événement | Quand il se déclenche |
|-----------|-----------------------|
| Nouveaux contacts | Un compagnon (la radio d’une personne) est vu pour la première fois |
| Messages directs | Un message direct arrive, y compris les messages de serveurs de salon |
| Publicités répéteur | Un répéteur est vu pour la première fois |
| Publicités compagnon | Un compagnon est vu pour la première fois |
| Publicités capteur | Un capteur est vu pour la première fois |
| Canaux trouvés | Meshloom trouve un nouveau canal hashtag |
| Alertes télémétrie | Un répéteur ou un contact suivi franchit un seuil d’alerte. Les seuils se règlent sur la page **Alertes** |
| Mises à jour Meshloom | Une nouvelle version de Meshloom est disponible |
| Radio hors ligne | La radio est injoignable depuis plus longtemps que le délai réglé sous le tableau (60 s par défaut). Envoyé comme urgent (le sujet de l’e-mail commence par `[URGENT]`), une fois par panne |
| Radio de nouveau en ligne | La radio est de nouveau joignable après une notification **Radio hors ligne**, avec la durée de la panne |
| Meshloom a démarré | Meshloom vient de démarrer : sa version, si la radio s’est connectée et en combien de temps. Activé par défaut pour le push |

**Radio hors ligne** et **Radio de nouveau en ligne** ne se déclenchent jamais pour un redémarrage de la radio lancé par Meshloom (correction de l’horloge, changement Wi-Fi) ni tant que la connexion est en pause dans **Réglages → Radio** ; une pause termine une panne sans notification. Rien n’est envoyé avant que la radio se soit connectée une fois depuis le démarrage de Meshloom, ni pendant le même délai juste après un démarrage : une mise à jour ou un redémarrage ne déclenche pas de fausse alerte. Le délai se règle dans **Réglages → Notifications → Délai « radio hors ligne »** (`radio_offline_grace_s` dans `/api/settings` ; 0 notifie aussitôt, jusqu’à 3600 s). La radio est vérifiée environ toutes les 5 secondes, et une tentative de reconnexion en cours peut retarder la vérification : la notification peut arriver un peu après le délai. Si Meshloom redémarre pendant une panne, la durée compte toujours depuis le vrai début.

**Radio hors ligne ne peut pas vous prévenir quand Meshloom lui-même est arrêté** (coupure de courant, redémarrage de l’hôte) : plus rien ne tourne pour l’envoyer. **Meshloom a démarré** rend visible un redémarrage inattendu. Pour être prévenu pendant l’arrêt, utilisez le `binary_sensor.<radio>_connected` de Home Assistant avec un délai « pendant » (voir `README_HA.md`) ; un battement de cœur périodique est prévu.

Pour un compagnon, **Nouveaux contacts** ou **Publicités compagnon** suffit.

Les alertes « vu pour la première fois » ne se déclenchent que lorsqu’un **nouveau contact** est ajouté, après la fin du démarrage de la radio. Un nœud déjà connu, un nœud de type inconnu ou un serveur de salon ne les déclenchent jamais.

### Les conversations

Pour les messages de salon, sans exception :

- les salons publics et les salons `#` (hashtag) notifient par push ;
- les salons à clé privée ne notifient pas.

Les messages directs suivent la ligne **Messages directs**. Les notifications par e-mail, webhook ou Apprise pour un salon n’existent que si vous les activez pour ce salon (la cloche, ci-dessous).

Les messages d’un contact ou d’un nom bloqué, et ceux d’un salon en sourdine, ne partent jamais vers Apprise.

### La cloche, pour une conversation

Dans l’en-tête d’une conversation, la cloche ouvre un menu avec quatre cases : **Push**, **E-mail**, **Webhook** et **Apprise**. En cocher une crée une **exception** aux valeurs par défaut, pour cette conversation seulement. Si votre navigateur n’est pas encore abonné, cocher **Push** l’abonne. **E-mail**, **Webhook** et **Apprise** sont grisés tant qu’aucune destination n’existe ; un lien mène aux réglages.

Les exceptions sont listées dans **Réglages → Notifications → Exceptions par conversation**, où on peut les retirer.

### Mettre un salon en sourdine

Sur un salon, le bouton de sourdine fait taire tout pendant la durée choisie (de 15 minutes à 24 heures, ou indéfiniment). Il masque aussi le compteur de non lus. Il l’emporte sur les valeurs par défaut et les exceptions, et ce n’est pas le même contrôle que la cloche.

### Destinations e-mail et webhook

Dans **Réglages → Notifications → Destinations de livraison**, saisissez un serveur SMTP (hôte, port, chiffrement, utilisateur, mot de passe, expéditeur et destinataire) et/ou l’adresse d’un webhook avec un secret HMAC facultatif. Les secrets ne sont plus affichés après l’enregistrement. Laissez un champ secret vide pour le conserver. L’adresse du webhook est elle aussi un secret, car elle contient souvent un jeton : une fois enregistrée, son champ ne montre que le schéma et l’hôte ; saisissez une nouvelle adresse pour la remplacer, ou appuyez sur **Effacer** pour la supprimer avec son secret HMAC. Le téléchargement de la sauvegarde JSON les laisse de côté (voir [Sécurité](/docs/deep/security/#secrets-des-intégrations-et-des-notifications)). Chaque destination a un bouton de test. Ce webhook sert aux notifications de Meshloom. Ce n’est pas le webhook de messages du fanout.

Meshloom refuse un webhook sur un réseau privé (votre réseau local, cette machine, `localhost`, le NAT d’opérateur, les adresses lien-local) sauf si vous cochez **Autoriser une destination sur un réseau privé (réseau local, localhost)** sous l’adresse : Home Assistant, Node-RED ou un script sur votre réseau en ont besoin. La vérification a lieu à l’enregistrement de l’adresse puis avant chaque envoi, et Meshloom se connecte ensuite uniquement à l’adresse vérifiée : une réponse DNS qui change entre-temps ne peut pas détourner l’appel. Les adresses de métadonnées cloud (`169.254.169.254` et ses équivalents) sont toujours refusées. Si le nom d’hôte est introuvable, saisissez plutôt l’adresse IP. Un webhook enregistré avant cette vérification continue de fonctionner : la case est cochée pour lui, et un avis unique le signale.

**Plusieurs destinataires et une langue (e-mail).** Une destination e-mail accepte plusieurs destinataires, un par ligne (les virgules marchent aussi), et un Cc facultatif. Meshloom les confie au serveur de messagerie en une fois : si le serveur refuse un destinataire, toute la notification compte comme échouée, même si les autres l’ont peut-être reçue, et elle n’est pas renvoyée. Le message est rédigé dans la langue du canal (Automatique suit la langue des notifications). Quand le texte d’un message est retenu (le réglage qui garde le texte des messages hors des services tiers), le sujet est « Nouveau message » et le corps montre l’expéditeur et la conversation mais pas le texte.

**En-têtes du webhook.** Chaque appel porte `X-Webhook-Event`, `X-Webhook-Id` et `X-Webhook-Timestamp` (secondes depuis l’epoch de cette tentative), et `X-Webhook-Signature` (`sha256=` et le HMAC-SHA256 du corps) quand un secret est défini. L’identifiant est le même à chaque nouvelle tentative d’une notification : un récepteur comme Node-RED peut ignorer un identifiant déjà vu. La signature ne couvre que le corps, elle n’a donc pas changé. Désactiver la vérification du certificat (pour un certificat auto-signé sur votre réseau) n’est accepté qu’avec **Autoriser une destination sur un réseau privé**.

L’e-mail est en HTML, avec une copie en texte brut. Le logo de Meshloom voyage dans le message (image intégrée) : ouvrir l’e-mail ne charge rien depuis Internet, et il ne contient aucun lien.

### Destination Apprise

La destination **Apprise**, au même endroit, prend une [URL Apprise](https://appriseit.com/getting-started/universal-syntax/) par ligne. Quelques exemples :

| Service | URL |
|---------|-----|
| ntfy | `ntfys://ntfy.sh/mon-sujet-meshloom` (ou `ntfys://ntfy.example.org/sujet` pour votre propre serveur) |
| Telegram | `tgram://bot_token/chat_id` (l’identifiant de discussion est obligatoire) |
| Discord | `discord://webhook_id/webhook_token` |
| Home Assistant | `hassios://homeassistant.local:8123/jeton_longue_duree` |

Les URL contiennent des jetons : ce sont des secrets. Après l’enregistrement elles s’affichent `********` et ne sont plus jamais montrées ; le téléchargement de la sauvegarde JSON les laisse de côté. Pour les changer, saisissez-en de nouvelles ; videz le champ pour les supprimer. Seuls les services que Meshloom a examinés sont acceptés (la même liste que pour le [fanout Apprise](/docs/deep/fanout/)) ; une URL refusée est désignée par sa ligne à l’enregistrement. Cette destination transporte les notifications système choisies dans le tableau ci-dessus ; ce n’est pas le fanout Apprise, qui transfère tous les messages des salons choisis.

- **Texte des messages.** *Canaux publics et # seulement* (par défaut) envoie le texte des messages du canal Public et des canaux `#`, et seulement un titre pour les messages directs et les salons privés (« Message de Léa »). *Toujours* envoie tous les textes ; *Jamais* n’envoie que des titres.
- **Format.** Texte brut, ou Markdown. En Markdown le texte des messages est échappé (un message ne peut pas injecter de lien ni de titre), et dans les deux formats les mentions de tout un salon (`@everyone`, `@here`, `@channel`, `@all`, `@room`, `<@…>`, `<!…>`) sont neutralisées.
- **Autoriser une destination sur un réseau privé (LAN, localhost).** Désactivé par défaut, comme pour le webhook : cochez-le pour un serveur ntfy, Gotify ou Home Assistant sur votre réseau ou sur cette machine. La vérification a lieu à l’enregistrement puis avant chaque envoi.

Chaque URL est envoyée séparément : un service en panne n’empêche pas les autres. Un envoi raté est retenté après 5 s puis 30 s quand le service est injoignable, répond une erreur serveur ou demande de ralentir (son `Retry-After` est respecté), jamais quand il refuse la requête (un jeton faux, par exemple). Une URL à plusieurs destinataires (plusieurs sujets ntfy, discussions Telegram ou destinataires e-mail) n’est essayée qu’une fois, pour que ceux déjà atteints ne reçoivent pas le message deux fois. Pour protéger vos comptes, Meshloom envoie au plus une notification Apprise toutes les 2 secondes, en garde 20 en attente et remplace le reste par un seul « +N évènements » ; les nouveaux contacts et annonces notifient au plus 10 fois par heure, puis un seul « +N nouveaux contacts ». Le dernier échec (son code, la ligne de l’URL, son schéma et son hôte seulement) figure dans `/api/health` sous `notification_statuses["legacy:apprise"]` (voir « Comment les notifications partent » ci-dessous).

### Comment les notifications partent

Chaque destination (push, e-mail, webhook, Apprise) a sa propre file et envoie une notification à la fois : un service lent ou en panne ne retarde jamais les autres, ni le push. L’e-mail envoie au plus une notification toutes les 10 secondes et Apprise une toutes les 2 secondes. Quand plus de 20 notifications attendent pour une destination, les suivantes sont remplacées par un seul « +N évènements » ; les urgentes (radio hors ligne, alerte de télémétrie) ne sont jamais regroupées. Les nouveaux contacts et annonces notifient au plus 10 fois par heure et par destination, puis un seul « +N nouveaux contacts ». Ce regroupement et ce plafond valent pour toutes les destinations, push, e-mail et webhook compris, pas seulement Apprise. Un webhook injoignable, trop lent, qui répond une erreur serveur ou demande de ralentir est retenté après 5 s puis 30 s (son `Retry-After` est respecté, jusqu’à 2 minutes), jamais quand il refuse la requête. Une notification de message qui échoue encore est abandonnée (elle arriverait trop tard) ; une radio hors ligne ou une alerte de télémétrie a droit à un essai de plus 5 minutes plus tard quand le service était injoignable, jamais après un refus. Les notifications encore en attente quand Meshloom s’arrête ne partent pas après le redémarrage.

L’état de chaque destination figure dans `/api/health`, sous `notification_statuses`, par identifiant (`builtin:push`, `legacy:email`, `legacy:webhook`, `legacy:apprise`) : `status` (`connected`, `error`, `paused` ou `disconnected`), la dernière erreur sous forme de code, jamais un mot de passe ni un jeton (administrateurs seulement), et l’heure du dernier envoi réussi. Meshloom garde un court journal des notifications en échec, regroupées, abandonnées et annulées, sans leur texte ; les envois réussis sont seulement comptés.

Le bouton de test envoie une notification de test à chaque URL ; un échec indique la ligne de l’URL et le type d’échec, jamais le jeton.

### Langue

**Réglages → Notifications → Langue des notifications** fixe la langue des notifications par e-mail et Apprise : **Automatique**, **Français** ou **English**. Automatique suit les navigateurs abonnés au push : anglais seulement si la plupart d’entre eux (une majorité stricte) sont en anglais, français en cas d’égalité. Les notifications push gardent toujours la langue du navigateur abonné, et le webhook envoie des données, pas du texte.

### Preuve de modification

Toute modification d’une destination (e-mail, webhook ou Apprise) envoie à chaque navigateur abonné une notification push **Destination de notification modifiée**, qui dit ce qui a changé. Elle ne peut pas être désactivée : si ce n’est pas vous, quelqu’un d’autre l’a fait.

## En coulisses

### Service worker et nettoyage

`sw.js` affiche les notifications reçues et, au clic, met au premier plan ou ouvre la bonne conversation. Si un service de push répond `403`, `404` ou `410` pour un abonnement, Meshloom le supprime. Il faut alors réabonner le navigateur.

### Points d’accès

| Méthode | Point d’accès | Effet |
|---------|---------------|-------|
| GET | `/api/push/vapid-public-key` | Clé publique utilisée pour s’abonner |
| POST | `/api/push/subscribe` | Enregistre ou met à jour un abonnement |
| GET | `/api/push/subscriptions` | Liste les abonnements |
| PATCH | `/api/push/subscriptions/{id}` | Change l’étiquette ou la langue |
| DELETE | `/api/push/subscriptions/{id}` | Supprime un abonnement |
| POST | `/api/push/subscriptions/{id}/test` | Envoie une notification de test |
| GET | `/api/push/preferences` | Valeurs par défaut, exceptions et sujet VAPID |
| PATCH | `/api/push/preferences` | Modifie les valeurs par défaut et/ou le sujet VAPID |
| PUT | `/api/push/preferences/conversations/{key}` | Crée ou efface une exception pour une conversation |

Un abonnement est unique par son adresse : enregistrer à nouveau met à jour l’existant. Les anciennes routes `/api/push/conversations` n’existent plus.

### Services push sur un réseau privé

Les services push des navigateurs (Google, Mozilla, Apple) sont sur Internet. `POST /api/push/subscribe` refuse une adresse sur un réseau privé (réponse `400`, code `egress_private_address`), et Meshloom n’y envoie jamais rien, sauf si **Accepter les points de terminaison push sur un réseau privé** est coché dans **Réglages → Notifications** (réglage `push_allow_private_endpoints` de `/api/settings`). Cochez-le uniquement pour un distributeur UnifiedPush auto-hébergé sur votre réseau local ou sur cette machine. Comme pour le webhook, chaque push part uniquement vers l’adresse vérifiée par Meshloom (le certificat du service push reste contrôlé pour son nom), et une redirection n’est jamais suivie.

La vérification a lieu de nouveau avant chaque push. Un appareil dont l’adresse est mal formée, ou pointe désormais vers une adresse refusée quels que soient les réglages (métadonnées cloud comme `169.254.169.254`, plages de documentation), est ignoré et supprimé (quand seule la réponse DNS pointe là, il n’est supprimé que si un push au moins une heure plus tard la retrouve, un résolveur pouvant se tromper une fois) ; le journal du serveur indique `Push endpoint refused for good by the egress guard` avec le code du motif et l’identifiant de l’appareil, jamais son adresse (elle contient un jeton). Un appareil refusé seulement parce que la case est décochée, ou dont le nom est introuvable pour l’instant, est conservé et le journal le signale une fois.

Lors d’une mise à jour, si un appareil enregistré avant cette vérification n’est pas un service push de navigateur (un distributeur UnifiedPush, peut-être sur votre réseau local), la case est cochée d’office pour que l’appareil continue de recevoir, et un avis dans **Réglages → Notifications** le signale jusqu’à ce que vous le fermiez. Décochez la case si aucun de vos appareils n’est sur votre réseau. Avec uniquement des services push de navigateur, ou un distributeur public comme ntfy.sh, elle reste décochée. Seul un appareil dont l’adresse est une IP privée, un nom sans point ou un nom en `.local`, `.lan`, `.home.arpa`, `.internal` ou `.localdomain` compte comme étant sur votre réseau (Meshloom ne fait aucune résolution DNS pour cela).

Un distributeur auto-hébergé joint par un nom d’apparence publique mais qui pointe vers votre réseau (DNS interne, nom Tailscale `*.ts.net`) ne peut pas être reconnu ainsi. Si la mise à jour décoche la case pour un tel appareil, **Réglages → Notifications** affiche un avis avec un bouton **Autoriser les réseaux privés** qui la coche en un clic ; rien n’est supprimé, et un push refusé parce que la case est décochée n’est jamais compté comme un échec : l’appareil reste enregistré. Pour un tel distributeur sur une installation neuve, cochez la case à la main.

Restaurer une sauvegarde d’avant cette vérification suit la même règle : si un appareil restauré est sur votre réseau, la case est cochée et le même avis apparaît.

## Quand rien n’arrive

1. Vérifiez que la page est servie en HTTPS (ou depuis `localhost`).
2. Vérifiez l’autorisation de notifications dans votre navigateur et votre système.
3. Envoyez un test depuis **Réglages → Notifications**. Une destination sur un réseau privé non autorisée est refusée tout de suite, avant toute connexion. Une fois cette vérification passée, quand un test échoue, Meshloom indique seulement le type d’échec (injoignable, pas de réponse à temps, échec TLS, statut HTTP renvoyé par le webhook, ou abonnement que le service push n’accepte plus) ; il n’affiche jamais l’erreur réseau elle-même, un nom d’hôte ni une adresse (une page ne peut donc pas se servir du test pour sonder votre réseau). Le journal du serveur indique le type d’erreur, ainsi que l’hôte et le statut HTTP du webhook, sous `Test push failed` ou `Notification test (webhook) failed` ; il n’enregistre jamais l’adresse du webhook ni son jeton.
4. Vérifiez les valeurs par défaut et les exceptions, visibles aussi sur `/api/push/preferences`. Un salon à clé privée reste muet sans exception qui l’active. Un salon en sourdine reste muet quels que soient les réglages.
5. Sur Apple, vérifiez le sujet VAPID (voir plus haut) et cherchez `403 BadJwtToken` dans le journal du serveur.
6. Vérifiez que le serveur peut joindre Internet.

Au niveau de journal `DEBUG`, les réponses des services de push sont écrites dans le journal. Voir [Dépannage](/docs/deep/troubleshooting/).
