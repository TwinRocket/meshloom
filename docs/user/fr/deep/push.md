---
title: Notifications push
description: Des notifications même quand l’onglet est fermé. HTTPS requis.
level: deep
order: 18
---

Web Push peut prévenir votre navigateur d’un message entrant alors que l’onglet de Meshloom est fermé. C’est distinct du [fanout](/docs/deep/fanout/) : chaque navigateur a son propre abonnement, alors que les règles (quoi notifier, et pour quelles conversations) sont communes à toute l’installation.

Il n’y a pas de fenêtres contextuelles venant de l’onglet ouvert lui-même. Web Push est la seule manière dont Meshloom notifie un navigateur.

Les mêmes règles peuvent aussi envoyer un **e-mail** ou appeler un **webhook**. Le push est activé par défaut ; l’e-mail et le webhook sont désactivés tant que vous n’avez pas configuré de destination. Voir « Choisir ce qui notifie » plus bas.

## Ce qu’il vous faut

**HTTPS.** Les navigateurs n’autorisent le composant d’arrière-plan qui reçoit les notifications (le service worker) que sur une page sécurisée. Un certificat fabriqué par vous peut convenir ; voir [HTTPS](/docs/deep/https/).

**Un accès à Internet depuis le serveur.** Les notifications passent par des services tenus par les éditeurs de navigateurs : Google (FCM), Mozilla ou Apple (APNs). [Meshloom Community](/docs/deep/community/) a aussi besoin d’un accès quand il est activé.

## L’adresse de contact (VAPID)

Meshloom signe chaque notification avec une paire de clés créée au premier démarrage. La signature porte aussi une adresse de contact, appelée sujet VAPID.

Réglez-la dans **Réglages → Notifications**, champ **Sujet VAPID**. Elle doit être `mailto:vous@domaine.tld` (recommandé) ou `https://votre-hote` sans chemin. Quand le champ est vide, Meshloom utilise la variable d’environnement `MESHCORE_VAPID_SUBJECT`, dont la valeur par défaut est `mailto:noreply@meshcore.local`.

**Apple exige une vraie adresse.** APNs rejette un sujet qui n’est pas un vrai contact, ainsi que le `.local` par défaut, avec `403 BadJwtToken`. Saisissez une vraie adresse dans l’interface, ou en repli dans l’environnement :

```text
MESHCORE_VAPID_SUBJECT=mailto:you@example.com
```

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

## Quand rien n’arrive

1. Vérifiez que la page est servie en HTTPS (ou depuis `localhost`).
2. Vérifiez l’autorisation de notifications dans votre navigateur et votre système.
3. Envoyez un test depuis **Réglages → Notifications**.
4. Vérifiez les valeurs par défaut et les exceptions, visibles aussi sur `/api/push/preferences`. Un salon à clé privée reste muet sans exception qui l’active. Un salon en sourdine reste muet quels que soient les réglages.
5. Sur Apple, vérifiez le sujet VAPID (voir plus haut) et cherchez `403 BadJwtToken` dans le journal du serveur.
6. Vérifiez que le serveur peut joindre Internet.

Au niveau de journal `DEBUG`, les réponses des services de push sont écrites dans le journal. Voir [Dépannage](/docs/deep/troubleshooting/).
