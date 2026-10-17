---
title: Notifications push
description: Des notifications même quand l’onglet est fermé. HTTPS requis.
level: deep
order: 18
---

Web Push peut prévenir votre navigateur d’un message entrant alors que l’onglet de Meshloom est fermé. C’est distinct du [fanout](/docs/deep/fanout/) : chaque navigateur a son propre abonnement, alors que les règles (quoi notifier, et pour quelles conversations) sont communes à toute l’installation.

Il n’y a pas de fenêtres contextuelles venant de l’onglet ouvert lui-même. Web Push est la seule manière dont Meshloom notifie un navigateur.

Les mêmes règles peuvent aussi envoyer un **e-mail** ou appeler un **webhook**. Le push est activé par défaut. L’e-mail et le webhook sont désactivés par défaut, et ne fonctionnent qu’une fois une destination configurée. Voir « Choisir ce qui notifie » plus bas.

## Ce qu’il vous faut

**HTTPS.** Les navigateurs n’autorisent le composant d’arrière-plan qui reçoit les notifications (le service worker) que sur une page sécurisée. Un certificat fabriqué par vous peut convenir ; voir [HTTPS](/docs/deep/https/).

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

**Réglages → Notifications → Notifications par défaut** a une ligne par événement, avec trois colonnes : **Push**, **E-mail** et **Webhook**. Le push est activé pour chaque événement au départ.

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

Pour un compagnon, **Nouveaux contacts** ou **Publicités compagnon** suffit.

Les alertes « vu pour la première fois » ne se déclenchent que lorsqu’un **nouveau contact** est ajouté, après la fin du démarrage de la radio. Un nœud déjà connu, un nœud de type inconnu ou un serveur de salon ne les déclenchent jamais.

### Les conversations

Pour les messages de salon, sans exception :

- les salons publics et les salons `#` (hashtag) notifient par push ;
- les salons à clé privée ne notifient pas.

Les messages directs suivent la ligne **Messages directs**. Les notifications par e-mail ou webhook pour un salon n’existent que si vous les activez pour ce salon.

### La cloche, pour une conversation

Dans l’en-tête d’une conversation, la cloche ouvre un menu avec trois cases : **Push**, **E-mail** et **Webhook**. En cocher une crée une **exception** aux valeurs par défaut, pour cette conversation seulement. Si votre navigateur n’est pas encore abonné, cocher **Push** l’abonne. **E-mail** et **Webhook** sont grisés tant qu’aucune destination n’existe ; un lien mène aux réglages.

Les exceptions sont listées dans **Réglages → Notifications → Exceptions par conversation**, où on peut les retirer.

### Mettre un salon en sourdine

Sur un salon, le bouton de sourdine fait taire tout pendant la durée choisie (de 15 minutes à 24 heures, ou indéfiniment). Il masque aussi le compteur de non lus. Il l’emporte sur les valeurs par défaut et les exceptions, et ce n’est pas le même contrôle que la cloche.

### Destinations e-mail et webhook

Dans **Réglages → Notifications → Destinations de livraison**, saisissez un serveur SMTP (hôte, port, chiffrement, utilisateur, mot de passe, expéditeur et destinataire) et/ou l’adresse d’un webhook avec un secret HMAC facultatif. Les secrets ne sont plus affichés après l’enregistrement. Chaque destination a un bouton de test. Ce webhook sert aux notifications de Meshloom. Ce n’est pas le webhook de messages du fanout.

Meshloom refuse un webhook sur un réseau privé (votre réseau local, cette machine, `localhost`, le NAT d’opérateur, les adresses lien-local) sauf si vous cochez **Autoriser une destination sur un réseau privé (réseau local, localhost)** sous l’adresse : Home Assistant, Node-RED ou un script sur votre réseau en ont besoin. La vérification a lieu à l’enregistrement de l’adresse puis avant chaque envoi, et Meshloom se connecte ensuite uniquement à l’adresse vérifiée : une réponse DNS qui change entre-temps ne peut pas détourner l’appel. Les adresses de métadonnées cloud (`169.254.169.254` et ses équivalents) sont toujours refusées. Si le nom d’hôte est introuvable, saisissez plutôt l’adresse IP. Un webhook enregistré avant cette vérification continue de fonctionner : la case est cochée pour lui, et un avis unique le signale.

L’e-mail est en HTML, avec une copie en texte brut. Le logo de Meshloom voyage dans le message (image intégrée) : ouvrir l’e-mail ne charge rien depuis Internet, et il ne contient aucun lien.

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

Les services push des navigateurs (Google, Mozilla, Apple) sont sur Internet. `POST /api/push/subscribe` refuse une adresse sur un réseau privé (réponse `400`, code `egress_private_address`), et Meshloom n’y envoie jamais rien, sauf si **Accepter les points de terminaison push sur un réseau privé** est coché dans **Réglages → Notifications** (réglage `push_allow_private_endpoints` de `/api/settings`). Cochez-le uniquement pour un distributeur UnifiedPush auto-hébergé sur votre réseau local ou sur cette machine. Comme pour le webhook, chaque push part uniquement vers l’adresse vérifiée par Meshloom, et une redirection n’est jamais suivie.

## Quand rien n’arrive

1. Vérifiez que la page est servie en HTTPS (ou depuis `localhost`).
2. Vérifiez l’autorisation de notifications dans votre navigateur et votre système.
3. Envoyez un test depuis **Réglages → Notifications**. Une destination sur un réseau privé non autorisée est refusée tout de suite, avant toute connexion. Une fois cette vérification passée, quand un test échoue, Meshloom indique seulement le type d’échec (injoignable, pas de réponse à temps, échec TLS, statut HTTP renvoyé par le webhook, ou abonnement que le service push n’accepte plus) ; il n’affiche jamais l’erreur réseau elle-même, un nom d’hôte ni une adresse (une page ne peut donc pas se servir du test pour sonder votre réseau). Le journal du serveur indique le type d’erreur, ainsi que l’hôte et le statut HTTP du webhook, sous `Test push failed` ou `Notification test (webhook) failed` ; il n’enregistre jamais l’adresse du webhook ni son jeton.
4. Vérifiez les valeurs par défaut et les exceptions, visibles aussi sur `/api/push/preferences`. Un salon à clé privée reste muet sans exception qui l’active. Un salon en sourdine reste muet quels que soient les réglages.
5. Sur Apple, vérifiez le sujet VAPID (voir plus haut) et cherchez `403 BadJwtToken` dans le journal du serveur.
6. Vérifiez que le serveur peut joindre Internet.

Au niveau de journal `DEBUG`, les réponses des services de push sont écrites dans le journal. Voir [Dépannage](/docs/deep/troubleshooting/).
