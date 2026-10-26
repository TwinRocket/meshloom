---
title: Notifications
description: Canaux, événements et règles : être prévenu quand quelque chose se passe, sur votre téléphone, par e-mail, sur ntfy, Telegram, Discord, Home Assistant et d’autres.
level: deep
order: 17
---

Meshloom peut vous prévenir de ce qu’il entend et de son propre état sans que vous regardiez l’interface : un message privé, une mention, un répéteur qui se tait, la radio qui décroche. Chaque endroit où il envoie est un **canal**. Ce qui va vers quel canal est décidé par un seul tableau de **règles**, les valeurs par défaut de l’administrateur pour toute l’installation. Chaque compte peut ensuite ajuster, pour ses seuls appareils Web Push, les événements qui l’atteignent : voir [Vos propres règles](#vos-propres-règles).

Tout se règle dans **Réglages → Notifications** (administrateurs seulement, sauf [Mes notifications](#vos-propres-règles)). Rien ici ne demande de variable d’environnement. Ce qui est propre aux navigateurs (HTTPS, l’adresse de contact VAPID, l’abonnement d’un navigateur) est sur [Notifications push](/docs/deep/push/) ; cette page couvre tout le reste.

Les canaux de notification sont distincts du [fanout](/docs/deep/fanout/), qui transmet chaque message des conversations que vous choisissez à des machines (MQTT, webhooks, SQS). Les notifications s’adressent aux personnes et parlent d’événements.

## Canaux

Un canal est une destination, avec son nom, ses réglages, sa politique de texte, sa langue et son interrupteur. Vous pouvez en avoir autant que vous voulez, de tout type, plusieurs du même type compris (deux sujets ntfy, un chat Telegram pour la famille et un pour le club).

| Type | Ce qu’il atteint | Texte des messages au départ |
|------|------------------|------------------------------|
| Web Push | Les navigateurs abonnés (un canal intégré, impossible à supprimer ; demande HTTPS) | Toujours (pas de politique de texte, voir [Confidentialité](#confidentialité-et-ce-qui-quitte-votre-machine)) |
| E-mail | Des destinataires, par votre serveur SMTP | Canaux publics seulement, sauf si le serveur SMTP est sur votre réseau |
| Webhook | Une URL que vous contrôlez, avec un corps JSON et une signature facultative | Toujours |
| ntfy | Un sujet [ntfy](https://ntfy.sh) sur ntfy.sh ou sur votre propre serveur | Canaux publics seulement sur ntfy.sh, toujours sur votre serveur |
| Discord | Un salon Discord, par un webhook | Canaux publics seulement |
| Telegram | Un chat, un groupe ou un canal, par votre propre bot (préréglage) | Canaux publics seulement |
| Gotify | Votre propre serveur Gotify (préréglage) | Toujours |
| Home Assistant | Un service `notify` de Home Assistant, comme l’application Companion (préréglage) | Toujours |
| Pushover | L’application Pushover de votre téléphone (préréglage) | Canaux publics seulement |
| Matrix | Un salon Matrix, comme bot ou compte (préréglage) | Canaux publics seulement |
| Signal | Des messages Signal via votre serveur signal-cli REST (préréglage) | Toujours |
| Slack | Un canal Slack, par un webhook entrant (préréglage) | Canaux publics seulement |
| Mattermost | Un canal Mattermost, par un webhook entrant (préréglage) | Toujours |
| Rocket.Chat | Un salon Rocket.Chat, avec un compte (préréglage) | Toujours |
| SMS Free Mobile | Un SMS sur votre propre ligne Free Mobile (préréglage) | Jamais (titre seul) |
| SMS SFR | Des SMS vers des numéros via la messagerie SFR (préréglage) | Jamais (titre seul) |
| Apprise | Tout autre service que [Apprise](https://appriseit.com/) gère, à partir de son URL | Toujours |

Les **types natifs** (Web Push, e-mail, webhook, ntfy, Discord) sont écrits pour le service. Les **préréglages** (Telegram, Gotify, Home Assistant, Pushover, Matrix, Signal, Slack, Mattermost, Rocket.Chat, SMS Free Mobile, SMS SFR) sont des formulaires prêts à l’emploi pour des services que Meshloom atteint par Apprise : vous remplissez des champs nommés, Meshloom construit l’adresse Apprise au moment d’envoyer et ne la stocke jamais. Le type générique **Apprise** est la porte de sortie pour tout le reste. Le type d’un canal ne change pas après sa création : créez-en un nouveau.

Tout ce qui concerne un canal, ses secrets compris, est expliqué dans [Ajouter un canal](#ajouter-un-canal) et dans les sections de chaque type ci-dessous.

### Ajouter un canal

**Réglages → Notifications → Canaux** (administrateurs) liste en cartes tous les endroits où Meshloom envoie des notifications : son type, un voyant d’état (fonctionne, erreur, en pause, désactivé), la dernière erreur, un interrupteur, **Tester**, **Modifier** et **Supprimer**. La carte **Web Push** est toujours là et ne se supprime pas ; ouvrez-y **Appareils et réglages Web Push** pour l’abonnement de ce navigateur, les autres appareils, le sujet VAPID et l’interrupteur des points d’accès privés. En HTTP simple, la carte indique que Web Push est indisponible.

**Ajouter un canal** ouvre un dialogue en trois étapes. D’abord choisir un type ou un préréglage (E-mail, Webhook, ntfy, Discord, Telegram, Gotify, Home Assistant, Pushover, Matrix, Signal, Slack, Mattermost, Rocket.Chat, SMS Free Mobile, SMS SFR ou Apprise). Ensuite remplir son formulaire, que le serveur décrit lui-même : les champs obligatoires sont marqués d’une étoile, les erreurs de validation du serveur apparaissent sous le champ, et un secret (mot de passe, jeton, adresse de webhook) n’est jamais affiché, même après l’enregistrement : le formulaire indique qu’une valeur est définie et propose **Remplacer** et **Effacer** (n’y touchez pas pour la conserver). Un service tiers (« cloud ») affiche une note de confidentialité et permet de choisir si le texte des messages est inclus (canaux publics seulement par défaut). **Envoyer un test et continuer** envoie un test avant tout enregistrement ; s’il échoue, l’erreur est expliquée et vous pouvez corriger le formulaire, ou continuer sans test réussi. Enfin, **Que recevoir ici ?** pré-remplit les événements : **Comme Push** (les événements que reçoit Web Push, par défaut), **Alertes seulement** (radio hors ligne ou revenue, alertes de télémétrie, mentions, changements de canal) ou **Tout**. Vous pourrez le modifier ensuite dans le tableau ci-dessous.

Si le nouveau canal envoie au même endroit qu’une intégration Apprise active de **MQTT et automatisation**, le dialogue nomme cette intégration (jamais son adresse) avant de se fermer : garder les deux notifierait chaque message deux fois. **La convertir dans ce canal** donne la portée de l’intégration au nouveau canal et la désactive, comme la [conversion](/docs/deep/fanout/#convertir-en-canal-de-notification) du fanout ; **Garder les deux** la laisse telle quelle.

Les canaux `legacy:email`, `legacy:webhook` et `legacy:apprise` (vos destinations 4.18.x) gardent leur adresse et leurs options dans le réglage 4.18.x, que leur expéditeur lit encore à chaque envoi ; les règles décident de ce qu’elles reçoivent comme pour tout canal. **Modifier** sur leur carte ouvre le même formulaire qu’en 4.18.x (il enregistre quand vous quittez un champ), et **Supprimer cette destination** la vide ; leur type n’est plus proposé dans **Ajouter un canal**.

### Canaux ntfy

**Ajouter un canal → ntfy** publie vers un sujet [ntfy](https://ntfy.sh) : gratuit, auto-hébergeable, avec des applications Android, iOS et bureau, et utilisable avec un Meshloom servi en HTTP simple sur votre réseau local. Abonnez-vous au même sujet dans l'application ntfy.

- **Serveur** : `https://ntfy.sh` par défaut, ou le vôtre (`http://192.168.1.20:8080`; ni nom d'utilisateur ni mot de passe dans l'adresse, utilisez les champs ci-dessous). Un serveur de votre réseau privé, ou de cette machine, demande **Autoriser une destination sur un réseau privé**.
- **Sujet** (obligatoire, en écriture seule) : 1 à 64 lettres, chiffres, `-` ou `_`. Sur ntfy.sh le sujet est le seul secret : qui le connaît peut lire et publier, les sujets sont donc « publics par obscurité ». Générez-en un long et aléatoire vous-même, par exemple `openssl rand -base64 18 | tr '+/' '-_'` (24 caractères sûrs pour une URL). Meshloom publie le message en JSON à l'adresse du serveur : le sujet ne figure jamais dans une URL ni dans un journal, et il n'est plus jamais affiché après l'enregistrement.
- **Jeton d'accès**, ou **nom d'utilisateur et mot de passe** (en écriture seule), pour un serveur à accès contrôlé. Le jeton l'emporte si les deux sont renseignés. Changer de serveur demande de ressaisir les secrets : ils ne suivent jamais une nouvelle destination.
- **Priorités** : les événements ordinaires utilisent la priorité par défaut (3), les urgents (*radio hors ligne*, alertes de télémétrie, changements de canal, canal en échec) la priorité urgente (5, qui sonne sur la plupart des téléphones). Les **étiquettes** sont des étiquettes ntfy (les codes emoji s'affichent en icônes).
- **Adresse de ce Meshloom** (`https://meshloom.example.net/`) : toucher la notification ouvre la conversation ou le nœud (pas de partie `#` dans l'adresse).
- **Markdown** met en forme le message dans l'application web ntfy.
- **Vérification du certificat** : elle ne peut être désactivée que pour un serveur dont toutes les adresses sont privées (un ntfy auto-signé chez vous), jamais pour un hôte public.

Erreurs affichées par un test ou la carte du canal : *identifiants refusés* (401/403), *quota atteint* (le quota journalier de ntfy.sh est épuisé : auto-hébergez ntfy ou abonnez-vous ; Meshloom ne réessaie pas), *trop de requêtes* (le `Retry-After` de ntfy est respecté), *erreur de certificat*, *erreur du service* (erreur serveur ou message trop long).

ntfy.sh est un tiers, et conserve les messages 12 heures par défaut. Un canal ntfy.sh n'envoie donc que le texte des canaux Public et `#` (titre seul pour les messages privés et les canaux privés) tant que vous ne choisissez pas **Toujours**, et affiche une limite par défaut de 10 messages par minute, 30 par heure et **200 par jour**, sous les 250 par jour de l'offre gratuite (voir [ntfy.sh/pricing](https://ntfy.sh/pricing)). Meshloom les applique (voir [Limites et regroupement](#limites-et-regroupement)) : un canal très actif ne peut pas épuiser le quota de ntfy.sh, et une fois les 200 utilisés plus rien n’est envoyé au canal jusqu’au jour UTC suivant. Un serveur auto-hébergé n'a pas de limite journalière par défaut.

### Destinations e-mail et webhook

Dans **Réglages → Notifications → Canaux**, choisissez **Ajouter un canal → E-mail** et saisissez un serveur SMTP (hôte, port, chiffrement, utilisateur, mot de passe, expéditeur et destinataires), ou **Ajouter un canal → Webhook** et saisissez une adresse avec un secret HMAC facultatif. Les secrets ne sont plus affichés après l’enregistrement. Laissez un champ secret vide pour le conserver. L’adresse du webhook est elle aussi un secret, car elle contient souvent un jeton : une fois enregistrée, son champ ne montre que le schéma et l’hôte ; saisissez une nouvelle adresse pour la remplacer, ou appuyez sur **Effacer** pour la supprimer avec son secret HMAC. Le téléchargement de la sauvegarde JSON les laisse de côté (voir [Sécurité](/docs/deep/security/#secrets-des-intégrations-et-des-notifications)). Chaque destination a un bouton de test. Ce webhook sert aux notifications de Meshloom. Ce n’est pas le webhook de messages du fanout.

Meshloom refuse un webhook sur un réseau privé (votre réseau local, cette machine, `localhost`, le NAT d’opérateur, les adresses lien-local) sauf si vous cochez **Autoriser une destination sur un réseau privé (réseau local, localhost)** sous l’adresse : Home Assistant, Node-RED ou un script sur votre réseau en ont besoin. La vérification a lieu à l’enregistrement de l’adresse puis avant chaque envoi, et Meshloom se connecte ensuite uniquement à l’adresse vérifiée : une réponse DNS qui change entre-temps ne peut pas détourner l’appel. Les adresses de métadonnées cloud (`169.254.169.254` et ses équivalents) sont toujours refusées. Si le nom d’hôte est introuvable, saisissez plutôt l’adresse IP. Un webhook enregistré avant cette vérification continue de fonctionner : la case est cochée pour lui, et un avis unique le signale.

**Plusieurs destinataires et une langue (e-mail).** Une destination e-mail accepte plusieurs destinataires, un par ligne (les virgules marchent aussi), et un Cc facultatif (chaque adresse en Cc reçoit son propre message, une seule fois) ; 20 adresses au plus en tout, le reste est ignoré. Meshloom les confie au serveur de messagerie en une fois : si le serveur refuse un destinataire, toute la notification compte comme échouée, même si les autres l’ont peut-être reçue, et elle n’est pas renvoyée. Le message est rédigé dans la langue du canal (Automatique suit la langue des notifications). Quand le texte d’un message est retenu (le réglage qui garde le texte des messages hors des services tiers), le sujet est « Nouveau message » et le corps montre l’expéditeur et la conversation mais pas le texte.

**Corps et en-têtes du webhook.** Un canal webhook a un **Format du corps** : **v2** (le défaut d’un canal créé à partir de la 5.0.0) est un corps JSON documenté dont la signature couvre aussi l’horodatage, ce qui permet à un récepteur de refuser une requête rejouée ; **v1** est le corps de la 4.18.x, que gardent tous les canaux webhook qui existaient avant, ainsi que `legacy:webhook`. Le corps, les en-têtes, un exemple par événement et la vérification de la signature en Python et dans Node-RED sont sur [Webhook de notification](/docs/deep/notifications-webhook/). Chaque appel porte `X-Webhook-Event`, `X-Webhook-Id` et `X-Webhook-Timestamp` (secondes depuis l’epoch de cette tentative), et `X-Webhook-Signature` quand un secret est défini. L’identifiant est le même à chaque nouvelle tentative d’une notification : un récepteur comme Node-RED peut ignorer un identifiant déjà vu. En v1, la signature (`sha256=` et le HMAC-SHA256 du corps) ne couvre que le corps, et l’identifiant et l’horodatage ne sont pas signés : ils aident un récepteur à écarter une nouvelle tentative honnête, pas une requête rejouée. Désactiver la vérification du certificat (pour un certificat auto-signé sur votre réseau) n’est accepté que pour une destination sur un réseau privé ; une adresse publique garde toujours sa vérification. Un identifiant et un mot de passe e-mail ne sont jamais envoyés sans chiffrement (mode **Aucun**) à un serveur de messagerie hors de votre réseau privé.

L’e-mail est en HTML, avec une copie en texte brut. Le logo de Meshloom voyage dans le message (image intégrée) : ouvrir l’e-mail ne charge rien depuis Internet, et il ne contient aucun lien.

### Destination Apprise

Le canal **Apprise**, ajouté de la même façon, prend une [URL Apprise](https://appriseit.com/getting-started/universal-syntax/) par ligne. Quelques exemples :

| Service | URL |
|---------|-----|
| ntfy | `ntfys://ntfy.sh/mon-sujet-meshloom` (ou `ntfys://ntfy.example.org/sujet` pour votre propre serveur) |
| Telegram | `tgram://bot_token/chat_id` (l’identifiant de discussion est obligatoire) |
| Discord | `discord://webhook_id/webhook_token` |
| Home Assistant | `hassios://homeassistant.local:8123/jeton_longue_duree` |

Les URL contiennent des jetons : ce sont des secrets. Après l’enregistrement elles s’affichent `********` et ne sont plus jamais montrées ; le téléchargement de la sauvegarde JSON les laisse de côté. Pour les changer, saisissez-en de nouvelles ; videz le champ pour les supprimer. Seuls les services que Meshloom a examinés sont acceptés (la même liste que pour le [fanout Apprise](/docs/deep/fanout/)) ; une URL refusée est désignée par sa ligne à l’enregistrement. Cette destination transporte les notifications système choisies dans le tableau ci-dessus ; ce n’est pas le fanout Apprise, qui transfère tous les messages des salons choisis.

- **Texte des messages.** *Canaux publics et # seulement* (le défaut de cette destination 4.18.x ; un nouveau canal Apprise démarre sur *Toujours*) envoie le texte des messages du canal Public et des canaux `#`, et seulement un titre pour les messages directs et les salons privés (« Message de Léa »). *Toujours* envoie tous les textes ; *Jamais* n’envoie que des titres.
- **Format.** Texte brut, ou Markdown. En Markdown le texte des messages est échappé (un message ne peut pas injecter de lien ni de titre), et dans les deux formats les mentions de tout un salon (`@everyone`, `@here`, `@channel`, `@all`, `@room`, `<@…>`, `<!…>`, `<#…>`) sont neutralisées.
- **Autoriser une destination sur un réseau privé (LAN, localhost).** Désactivé par défaut, comme pour le webhook : cochez-le pour un serveur ntfy, Gotify ou Home Assistant sur votre réseau ou sur cette machine. La vérification a lieu à l’enregistrement puis avant chaque envoi.

Chaque URL est envoyée séparément : un service en panne n’empêche pas les autres. Un envoi raté est retenté après 5 s puis 30 s quand le service est injoignable, répond une erreur serveur ou demande de ralentir (son `Retry-After` est respecté), jamais quand il refuse la requête (un jeton faux, par exemple). Une URL à plusieurs destinataires (plusieurs sujets ntfy, discussions Telegram ou destinataires e-mail) n’est essayée qu’une fois, pour que ceux déjà atteints ne reçoivent pas le message deux fois. Pour protéger vos comptes, Meshloom envoie au plus une notification Apprise toutes les 2 secondes, en garde 20 en attente et remplace le reste par un seul « +N événements » ; les nouveaux contacts et annonces notifient au plus 10 fois par heure, puis un seul « +N nouveaux contacts ». Le dernier échec (son code, la ligne de l’URL, son schéma et son hôte seulement) figure dans `/api/health` sous `notification_statuses["legacy:apprise"]` (voir « Comment les notifications partent » ci-dessous).

### Destination Discord

Le canal **Discord** publie dans un salon Discord par un webhook (dans Discord : paramètres du salon, **Intégrations**, **Webhooks**, **Copier l’URL du webhook**). Collez l’URL dans le formulaire du canal ; elle doit commencer par `https://discord.com/api/webhooks/` (ou `discordapp.com`), toute autre adresse est refusée. L’URL contient le jeton : c’est un secret, jamais réaffiché après l’enregistrement ni dans la sauvegarde JSON. Un **Nom d’utilisateur** facultatif remplace le nom du webhook ; Meshloom ne change jamais son avatar.

Quoi que dise un nœud ou un message, il ne peut mentionner personne : chaque requête porte `allowed_mentions: {"parse": []}`, donc `@everyone`, `@here`, les membres et les rôles ne sont jamais notifiés, le texte est échappé (aucun lien, titre ou mise en forme injecté, `@everyone` est cassé) et les aperçus de liens sont désactivés. Discord est un tiers : par défaut le texte des messages des **canaux Public et #** est envoyé, et les messages privés et les canaux privés n’envoient qu’un titre (« Message de Léa ») ; changez-le avec **Texte du message**. Les vérifications de certificat ne peuvent pas être désactivées (l’adresse est publique). Meshloom attend quand Discord indique que le webhook est limité et réessaie après le délai donné ; un webhook supprimé apparaît comme une erreur d’authentification.

### Canaux préréglés (Telegram, Gotify, Home Assistant, Pushover, Matrix, Signal, Slack, Mattermost, Rocket.Chat, SMS)

Ces canaux sont des **préréglages** : des formulaires prêts à l’emploi pour des services que Meshloom joint par Apprise. Vous remplissez des champs nommés, Meshloom construit lui-même l’adresse Apprise au moment de l’envoi et la vérifie comme toute autre destination. Les secrets (jeton du bot, jeton Gotify, jeton Home Assistant, clés Pushover, identifiant Matrix, clé du webhook Slack, etc.) sont en écriture seule : ils ne sont jamais réaffichés, et Meshloom ne conserve jamais l’adresse générée.

**Telegram** (un tiers : le texte des messages privés et des salons privés reste chez vous par défaut). Créez un bot avec `@BotFather` (envoyez `/newbot`) et copiez son jeton (`123456789:AAH…`). Trouvez l’**identifiant de conversation** : écrivez à votre bot, ouvrez `https://api.telegram.org/bot<jeton>/getUpdates` dans un navigateur et lisez `chat` → `id` (ou demandez à `@userinfobot`) ; l’identifiant d’un groupe est négatif (`-100…`), un canal public peut être son `@nom`. Pour un groupe ou un canal, ajoutez d’abord le bot. L’**identifiant de sujet** ne sert que pour un groupe à sujets. L’identifiant de conversation est obligatoire : sans lui le canal est refusé. Limite par défaut : 15 messages par minute, 200 par heure ; quand Telegram répond *trop de requêtes*, Meshloom attend le délai demandé.

**Gotify** (votre propre serveur). Dans Gotify, créez une *application* et copiez son jeton. Saisissez l’adresse du serveur (`https://gotify.exemple.org`, un préfixe de chemin est conservé ; `..` et `%` y sont refusés) et le jeton ; la priorité s’applique à toutes les notifications. Un serveur sur votre réseau local demande de cocher **Autoriser une destination sur réseau privé**, et changer le serveur oblige à ressaisir le jeton. Limite par défaut : 60 messages par minute.

**Home Assistant** (l’application Companion de votre téléphone, ou tout service `notify`). Dans votre profil Home Assistant, créez un **jeton d’accès longue durée**. Saisissez l’adresse de Home Assistant (`http://homeassistant.local:8123` ; uniquement l’adresse, un chemin comme `/ha` est refusé), le jeton et le **service notify** : `mobile_app_votre_telephone` (le nom exact est dans Home Assistant, Outils de développement, Actions ; `notify.alertes` fonctionne aussi). Home Assistant est presque toujours sur votre réseau : cochez **Autoriser une destination sur réseau privé** ; l’erreur le dit quand c’est nécessaire. Limite par défaut : 60 messages par minute.

**Pushover** (un tiers). Sur pushover.net, copiez votre **clé utilisateur**, créez une *application* et copiez son **jeton d’API** (30 caractères chacun). **Appareils**, facultatif, limite la livraison à ces noms d’appareils (séparés par des virgules, 10 au plus) ; **Priorité** et **Son** s’appliquent à toutes les notifications (la priorité *urgence* se répète jusqu’à acquittement). Une application gratuite peut envoyer 10 000 messages par mois : la limite par défaut (10 par minute, 100 par heure) reste bien en dessous, mais le quota mensuel est à surveiller.

**Matrix** (un tiers ; les messages partent en Markdown, le texte venu du mesh est donc échappé). Utilisez un compte dédié ou un bot qui a rejoint le salon. Saisissez l’adresse du serveur d’accueil (uniquement l’adresse, sans chemin ; `https://matrix.example.org`), puis soit un **jeton d’accès** seul (nom d’utilisateur vide), soit le **nom d’utilisateur** et le **mot de passe** du compte dans le champ d’identifiant (l’un des deux est obligatoire : sans lui, l’enregistrement ou le test est refusé avec `config_required`, avant tout envoi), et le **salon** : son identifiant (`!abc:example.org`, paramètres du salon, Avancé) ou son alias (`#salon:example.org`). Un serveur d’accueil sur votre réseau local demande **Autoriser une destination sur réseau privé** ; changer le serveur, l’utilisateur ou le salon oblige à ressaisir l’identifiant. Limite par défaut : 30 messages par minute.

**Signal** (votre propre serveur ; il faut un serveur [signal-cli REST API](https://bbernhard.github.io/signal-cli-rest-api/) avec un numéro enregistré, le plus souvent sur votre réseau local : cochez **Autoriser une destination sur réseau privé**). Saisissez son adresse, le numéro émetteur et les destinataires (`+33611111111,+33622222222`, 10 au plus, avec l’indicatif pays). Le nom d’utilisateur et le mot de passe ne servent que pour un proxy inverse qui demande une authentification basique. Limite par défaut : 20 messages par minute, 200 par heure.

**Slack** (un tiers). Dans votre application Slack, activez *Incoming Webhooks*, ajoutez-en un à un canal et copiez son adresse `https://hooks.slack.com/services/AAA/BBB/CCC` et collez-la en entier dans le champ **Adresse du webhook** (elle contient le secret, elle n’est donc plus jamais affichée ; seules les adresses `hooks.slack.com` sont acceptées). Limite par défaut : 30 messages par minute, un par seconde.

**Mattermost** (votre propre serveur ; Markdown). Dans Mattermost, *Intégrations*, *Webhooks entrants*, créez un webhook et copiez la clé à la fin de son adresse (`…/hooks/<clé>`). Saisissez l’adresse du serveur (un préfixe de chemin est conservé) et la clé ; un serveur sur votre réseau local demande **Autoriser une destination sur réseau privé**. Limite par défaut : 30 messages par minute.

**Rocket.Chat** (votre propre serveur). Utilisez un compte dédié qui a rejoint le salon. Saisissez l’adresse du serveur (sans chemin), l’**identifiant utilisateur ou le nom d’utilisateur** et le mot de passe (avec un jeton d’accès personnel, réglez le type d’identifiant sur *jeton* et saisissez l’identifiant utilisateur (user ID) que Rocket.Chat affiche à côté du jeton, pas le nom d’utilisateur) et le salon : `#canal`, `@utilisateur` ou un identifiant de salon. Un serveur sur votre réseau local demande **Autoriser une destination sur réseau privé**. Limite par défaut : 30 messages par minute.

**SMS Free Mobile** et **SMS SFR** (des tiers ; politique de texte verrouillée sur **Jamais**). Un SMS tient environ 160 caractères : ces canaux sont donc fixés sur un titre seul (par exemple « Message de Léa ») : **Texte du message** est verrouillé sur **Jamais**, et le serveur refuse une autre valeur (`text_policy_locked`), car le service couperait le texte. Free Mobile n’envoie qu’à la ligne du compte qui a activé **Notifications par SMS** (votre identifiant à 8 chiffres et la clé que l’option affiche) ; SFR demande un compte de son service de messagerie avec l’API SMS (nom du compte, mot de passe, identifiant d’espace) et prend les numéros des destinataires. Les deux démarrent à 2 messages par minute, 20 par heure et 100 par jour, espacés de 10 secondes, pour éviter les refus anti-inondation des services.

Chaque formulaire a un bouton **Tester** qui envoie un message avant tout enregistrement.

## Modèles de message

Un canal Apprise (et ses préréglages : Telegram, Gotify, Home Assistant, Pushover, Matrix, Signal, Slack, Mattermost, Rocket.Chat, SMS), un canal ntfy et un canal Discord peuvent présenter une notification de message avec vos propres mots. Ouvrez le canal, puis **Modèles de message**, et écrivez un modèle pour les **Messages privés** et un pour les **Messages de canal**. Laissez un champ vide pour garder le corps par défaut. Le titre ne change pas. Les canaux e-mail, webhook et Web Push ont leur propre format et n’acceptent pas de modèle.

Un modèle est du texte brut avec des variables entre accolades, par exemple `{sender_name} : {text} (via {hops})` :

| Variable | Valeur |
|----------|--------|
| `{type}` | `PRIV` pour un message privé, `CHAN` pour un message de canal |
| `{text}` | Le texte du message, tel qu’une notification le montre (un lien de partage devient `[image]`, `[lien]`…) |
| `{sender_name}`, `{sender_key}` | Le nom et la clé publique de l’expéditeur |
| `{channel_name}`, `{conversation_key}` | Le nom du canal et la clé de la conversation |
| `{hops}`, `{hops_backticked}`, `{hop_count}` | Le chemin du message (`a1, b2, c3`, la même liste avec chaque saut entre apostrophes inversées, et leur nombre), `direct` s’il est venu droit de l’expéditeur |
| `{rssi}`, `{snr}` | La force du signal et le rapport signal/bruit, vides quand la radio ne les a pas fournis |
| `{title}` | Le titre de la notification |

Ce sont les variables de la [sortie Apprise](/docs/deep/fanout/) (plus `{title}`). Trois règles rendent un modèle prévisible. Les variables sont remplacées en un seul passage : un message qui contient lui-même `{sender_name}` s’affiche tel quel et n’est jamais développé. Un nom absent de la liste reste tel que vous l’avez écrit. Et les règles de confidentialité s’appliquent toujours : quand la politique **Texte du message** du canal masque le texte d’une conversation, `{text}` est vide (un modèle qui donne un résultat vide revient au corps par défaut), `@everyone` et les mentions du même genre sont toujours neutralisées, et sur un canal qui envoie du Markdown (Discord, Matrix, Mattermost, un canal Apprise en Markdown, ntfy en Markdown) le texte venu du mesh est échappé alors que le Markdown que vous avez écrit dans le modèle, comme `**{sender_name}**`, est conservé.

Un modèle compte au plus 500 caractères. Meshloom le vérifie à l’enregistrement et refuse un caractère de contrôle ou un modèle trop long. Sous chaque champ, un aperçu montre un message privé et un message de canal d’exemple, rendus par Meshloom lui-même avec la langue et la politique de texte du canal ; l’aperçu n’applique l’échappement Markdown que pour un canal Discord et montre tous les autres canaux sans échappement, même s’ils envoient du Markdown, car le formulaire ne connaît pas ce réglage ; un envoi réel échappe toujours le texte venu du mesh sur un canal Markdown. Les modèles sont enregistrés avec le canal, y compris dans la sauvegarde JSON (ce ne sont pas des secrets).

## Confidentialité et ce qui quitte votre machine

Les notifications quittent votre Meshloom pour le service derrière chaque canal. Trois choses décident de leur contenu.

**Le genre de conversation.** Les messages du salon Public et des salons `#` (hashtag) sont lisibles par quiconque en connaît le nom : la clé en découle. Les messages privés et les salons à clé privée sont confidentiels.

**La politique de texte du canal.** Chaque canal a **Texte des messages** (ou **Inclure le texte des messages**) avec trois valeurs :

- *Canaux publics seulement* : le texte des salons Public et `#` est envoyé ; les messages privés et les salons privés donnent un titre seul (« Message de Léa »).
- *Toujours* : tous les textes sont envoyés.
- *Jamais* : un titre seul, pour tout.

Le tableau des [Canaux](#canaux) donne la valeur de départ de chaque type. Discord, Telegram et ntfy.sh montrent ce choix dans leur formulaire, avec une note de confidentialité ; les autres types ne le montrent pas et démarrent sur *Toujours* (ou *Canaux publics seulement* pour un relais e-mail hors de votre réseau). La destination Apprise de la 4.18.x garde son propre défaut, *Canaux publics seulement*, dans son formulaire. **Web Push n’a pas de politique de texte** : sa charge est chiffrée pour le navigateur et porte toujours le texte. Pour un service tiers placé derrière un canal Apprise générique, réglez sa politique de texte par l’API (`include_message_text`), car le formulaire ne la propose pas.

**La mémoire du service.** Les services publics conservent ce qu’ils reçoivent : ntfy.sh garde les messages en cache (12 heures par défaut) et quiconque connaît le sujet peut les lire ; sur ntfy.sh, le sujet est donc le seul secret et doit être long et aléatoire. Telegram et Discord gardent leur historique tant que la conversation existe. Supprimer une notification sur votre téléphone ne la supprime pas là-bas.

Quand vous envoyez les messages d’autres personnes à un tel service, **vous**, qui exploitez ce Meshloom, êtes responsable de ces données (dans l’UE, le responsable de traitement) ; Meshloom est un logiciel que vous avez installé, pas un sous-traitant. Rien ne passe par TwinRocket.

Le texte venu du mesh n’est pas authentifié : quiconque est à portée peut écrire n’importe quoi. Le moteur de rendu commun casse donc `@everyone`, `@here`, `@channel`, `@all`, `@room`, `<@`, `<!` et `<#` avec un caractère invisible dans toute notification envoyée par les canaux ntfy, Discord, Apprise et préréglages (Telegram, Gotify, Home Assistant, Pushover, Matrix, Signal, Slack, Mattermost, Rocket.Chat, SMS), en texte brut comme en Markdown. En Markdown (Discord, Matrix, Mattermost, ntfy avec Markdown activé, Apprise au format Markdown) il échappe aussi la ponctuation Markdown, de sorte qu’un message ne peut pas injecter un lien ou un titre. L’e-mail, Web Push et le webhook transportent le texte tel quel : aucun service de discussion n’y interprète une mention (l’e-mail est échappé en HTML). Le canal Discord envoie en plus `allowed_mentions: {"parse": []}` : il ne mentionne personne.

**Images et liens partagés.** Un message qui n’est qu’un lien court ou une image partagée (`📷 https://meshl.ink/…`, `🔒 …`, voir [Liens courts et photos](/docs/deep/share/)) n’arrive jamais comme une adresse dans une notification push, un e-mail, un message Apprise, ntfy.sh ou Discord : il se lit `[image]`, `[fichier chiffré]` ou `[lien]` (avec la légende, s’il y en a une). Cela vient du texte seul : Meshloom ne récupère rien pour l’écrire, et tout `#fragment` après un lien est écarté, jamais affiché. Dans un canal, l’expéditeur reste : `Léa : [image]`. Le canal webhook envoie le texte brut : toujours en v1, et en v2 quand son réglage **Texte des messages** le laisse passer.

La liste des secrets est plus bas, dans [Sauvegardes](#sauvegardes). En bref : les secrets s’écrivent mais ne se relisent pas, l’API ne les renvoie jamais et aucune sauvegarde ne les contient pour les canaux (la sauvegarde complète contient en revanche la clé privée Web Push et les abonnements, voir [Sauvegardes](#sauvegardes)).

## Destinations sur votre réseau

Par défaut, Meshloom refuse d’envoyer une notification vers une adresse de votre réseau local, vers cette machine (boucle locale, `localhost` compris) ou vers une adresse de métadonnées cloud. Ainsi, un réglage modifié ne peut pas transformer le serveur en sonde de votre réseau. Un serveur Gotify, ntfy, Home Assistant, Node-RED ou de messagerie chez vous, ou sur la même machine que Meshloom, est donc d’abord refusé, avec le message *cette adresse est sur un réseau privé*.

Si la destination est la vôtre, cochez **Autoriser une destination sur réseau privé** dans le formulaire du canal (décoché pour chaque nouveau canal). Cela couvre aussi la boucle locale. L’adresse est vérifiée à l’enregistrement du canal et de nouveau avant chaque envoi, et Meshloom ne se connecte qu’à l’adresse vérifiée. Les adresses de métadonnées cloud (`169.254.169.254` et équivalents) restent refusées même alors. Si un nom d’hôte est introuvable, saisissez plutôt son adresse IP. Désactiver la vérification du certificat n’est accepté que pour une destination dont toutes les adresses sont privées. Les services push des navigateurs ont un interrupteur à part, voir [Services push sur un réseau privé](/docs/deep/push/#services-push-sur-un-réseau-privé).

Les destinations que vous aviez avant l’existence de cette vérification (4.18.x) restent autorisées là où elles sont : la mise à jour a coché la case pour elles.

## Le tableau des règles

**Réglages → Notifications → Valeurs par défaut pour tous les comptes** est un tableau : une ligne par événement, regroupée (Messages, Nœuds, Radio, Télémétrie, Meshloom), une colonne par canal (Web Push et les canaux ajoutés ; un canal désactivé est grisé mais garde ses coches) et une colonne **Toujours livrer** qui laisse passer un événement malgré la pause. Cochez une case pour envoyer cet événement à ce canal. La première colonne reste visible quand le tableau défile sur un téléphone. Chaque case a une étiquette : le tableau se parcourt au clavier et au lecteur d’écran. **Nouveau nœud vu** n’a pas de case propre, seulement une sous-ligne par type de nœud (compagnons, répéteurs, capteurs). **Canal modifié** va toujours vers Web Push (verrouillé). **Mentions** a une case **Mots entiers uniquement**. Web Push est activé pour les événements usuels au départ.

Un nouveau canal est rempli pour vous par la dernière étape du dialogue d’ajout, **Que recevoir ici ?** : **Comme Push** (les événements que reçoit Web Push, par défaut), **Alertes seulement** ou **Tout**. Les événements urgents (radio hors ligne, alertes de télémétrie, canal modifié, canal en échec) sont toujours livrés, même pendant **Ne pas déranger**.

## Vos propres règles

Le tableau ci-dessus porte les **valeurs par défaut**. Web Push est le seul canal dont les destinataires sont des personnes (chaque appareil appartient à un compte) : sous **Réglages → Notifications → Mes notifications**, chaque compte (membre ou administrateur) voit une ligne par événement, et une par type de nœud pour *Nouveau nœud vu*. Chaque ligne suit la valeur par défaut de l’administrateur (la liste indique si c’est *notifier* ou *ne pas notifier*) tant que vous ne choisissez pas **Me notifier toujours** ou **Ne jamais me notifier** ; **Revenir aux valeurs par défaut** efface tous vos choix. Vos choix ne valent que pour les appareils de votre compte, et personne d’autre ne les voit ni ne les modifie.

- *Ne jamais me notifier* l’emporte sur tout pour vos appareils, exceptions de conversation de l’administrateur comprises.
- *Me notifier toujours* respecte quand même ce qui relève de l’installation : les exceptions de l’administrateur pour une conversation, un canal en sourdine, les listes de blocage et **Ne pas déranger**.
- L’e-mail, les webhooks, ntfy, Telegram et les autres canaux sont des destinations partagées que l’administrateur gère : ils continuent de suivre le tableau, quels que soient vos choix.
- Une mention atteint vos appareils comme une mention quand vos règles vous donnent *Mentions*, sinon comme un message de canal ordinaire, jamais les deux.
- Les résumés envoyés quand un canal dépasse sa limite (voir [Limites et regroupement](#limites-et-regroupement)) suivent aussi vos règles : vous n’en recevez un que si au moins une des notifications qu’il remplace vous était destinée, et ses totaux ne comptent que celles-là. Les avis sur la configuration des notifications (**Canal modifié**, **Canal en échec**, **Destination modifiée**) n’atteignent que les appareils des administrateurs, quels que soient leurs choix.
- Tant que vous ne changez rien, rien ne diffère du tableau, et la mise à jour vers cette version ne change rien pour personne.

### Vos exceptions de conversation

Sous **Mes notifications → Conversations**, choisissez un canal ou un contact, puis **Couper pour moi** ou **Me notifier toujours**. Cela ne vaut que pour cette conversation, sur vos appareils seulement, et l’emporte sur vos lignes d’événement ci-dessus (un canal coupé pour vous reste silencieux même si *Messages de canal* est activé ; un contact dont vous voulez toujours des nouvelles notifie même si *Messages privés* est désactivé). *Couper pour moi* fait aussi taire les mentions de ce canal. *Me notifier toujours* ne passe pas outre ce qui relève de l’installation : une conversation en sourdine, un expéditeur bloqué et la pause de l’administrateur la maintiennent silencieuse. Changez le choix dans la liste, ou retirez la ligne pour suivre de nouveau vos lignes d’événement. Jusqu’à 256 conversations.

### Mettre en pause vos appareils

Sous **Mes notifications → Pause**, choisissez **1 heure**, **8 heures**, **Jusqu’à demain 8 h** ou **Jusqu’à la reprise**. Vos appareils ne reçoivent alors plus rien, sauf les alertes urgentes (radio hors ligne, alertes de télémétrie) et les événements que l’administrateur a marqués *Toujours livrer*, comme avec **Ne pas déranger**. Les résumés sont eux aussi suspendus. **Reprendre** y met fin plus tôt ; une pause avec une heure de fin s’arrête toute seule. Personne d’autre n’est touché, ni les destinations partagées (e-mail, webhooks...) : le **Ne pas déranger** de l’administrateur est distinct. **Revenir aux valeurs par défaut** efface vos règles et vos exceptions de conversation, mais pas une pause en cours.

Les mêmes choix sont disponibles par `GET`, `PATCH` et `DELETE /api/push/my-rules`, et `PUT` et `DELETE /api/push/my-pause` (voir [Web Push](/docs/deep/push/)).

## Les événements

| Événement | Quand il se déclenche |
|-----------|-----------------------|
| Messages privés | Un message direct arrive, y compris les messages de serveurs de salon |
| Messages de canal | Un message arrive sur le salon Public ou sur un salon `#`. Un salon à clé privée reste muet sans exception qui l’active |
| Mentions | Un message de salon vous nomme, voir [Les mentions](#les-mentions) |
| Mot-clé | Un message contient le texte d’une règle de mot-clé, voir [Les mots-clés](#les-mots-clés). Réglé dans sa propre carte, pas dans ce tableau |
| Nœud surveillé entendu / de retour | Un nœud que vous surveillez est entendu (son annonce ou un message privé), ou revient après un long silence, voir [Les nœuds surveillés](#les-nœuds-surveillés). Réglé dans sa propre carte, pas dans ce tableau |
| Nouveau nœud vu | Un nœud est entendu pour la première fois, avec une sous-ligne par type : **Compagnons** (la radio d’une personne), **Répéteurs** et **Capteurs**. Seul compte un nouveau contact ajouté une fois le démarrage de la radio terminé : un nœud déjà connu, un nœud inconnu et un serveur de salon ne le déclenchent jamais |
| Canal trouvé | Meshloom trouve un nouveau salon hashtag |
| Alerte de télémétrie | Un répéteur ou un contact suivi franchit un seuil d’alerte. Les seuils et les canaux qui les reçoivent (une pastille par canal) se règlent sur la page **Alertes** |
| Mise à jour disponible | Une nouvelle version de Meshloom est disponible |
| Radio hors ligne | La radio est injoignable depuis plus longtemps que le délai réglé sous le tableau (60 s par défaut). Envoyé comme urgent (le sujet de l’e-mail commence par `[URGENT]`), une fois par panne |
| Radio de retour | La radio est de nouveau joignable après une notification **Radio hors ligne**, avec la durée de la panne |
| Meshloom démarré | Meshloom vient de démarrer : sa version, si la radio s’est connectée et en combien de temps. Activé par défaut pour le push |
| Canal modifié | Un canal de notification est créé, supprimé, activé ou désactivé, ou est modifié dans ses réglages (nouvelle adresse, nouveau serveur ou webhook, autres options ; pas un renommage ni un nouveau mot de passe). Il va toujours vers Web Push (appareils des administrateurs seulement) et ne peut pas en être retiré ; les autres canaux suivent le tableau, et le canal qui a changé n’en entend jamais parler |
| Canal en échec | Un canal a échoué 3 fois de suite, ou un navigateur a été retiré : désabonné par son service push, en échec 15 fois de suite, ou avec une adresse push que la protection réseau refuse définitivement. Il va vers Web Push (appareils des administrateurs seulement) et tous les autres canaux actifs et ne peut pas en être retiré ; le canal en échec n’en entend jamais parler. Au plus une fois par jour et par canal. Envoyé comme urgent : les heures calmes et **Ne pas déranger** ne le retiennent jamais. L’erreur est un code, jamais un mot de passe, un jeton ou une adresse |
| Destination modifiée | Une destination e-mail, webhook ou Apprise est changée par les réglages 4.18.x. Web Push seulement, appareils des administrateurs seulement |

**Radio hors ligne** et **Radio de retour** ne se déclenchent jamais pour un redémarrage de la radio lancé par Meshloom (correction de l’horloge, changement Wi-Fi) ni tant que la connexion est en pause dans **Réglages → Radio** ; une pause termine une panne sans notification. Rien n’est envoyé avant que la radio se soit connectée une fois depuis le démarrage de Meshloom, ni pendant le même délai juste après un démarrage : une mise à jour ou un redémarrage ne déclenche pas de fausse alerte. Le délai se règle sous la ligne **Radio hors ligne** du tableau (`radio_offline_grace_s` dans `/api/settings` ; 0 notifie aussitôt, jusqu’à 3600 s). La radio est vérifiée environ toutes les 5 secondes, et une tentative de reconnexion en cours peut retarder la vérification : la notification peut arriver un peu après le délai. Si Meshloom redémarre pendant une panne, la durée compte toujours depuis le vrai début.

**Radio hors ligne ne peut pas vous prévenir quand Meshloom lui-même est arrêté** (coupure de courant, redémarrage de l’hôte) : plus rien ne tourne pour l’envoyer. **Meshloom a démarré** rend visible un redémarrage inattendu. Pour être prévenu pendant l’arrêt, utilisez le `binary_sensor.<radio>_connected` de Home Assistant avec un délai « pendant » (voir `README_HA.md`), ou un battement de cœur de canal (ci-dessous).

### Savoir quand Meshloom lui-même est arrêté

Un canal ntfy, webhook, Apprise, Gotify ou Home Assistant peut envoyer un **battement de cœur** : dans les réglages **Avancé** du canal, choisissez un intervalle (5 min, 15 min, 1 h, 6 h ou 24 h ; désactivé par défaut ; l’API accepte 0 ou 300 à 86400 s dans `heartbeat_interval_s`). Deux modes :

- **Un message** (`notify.heartbeat` : nom de l’instance, durée de fonctionnement, version), envoyé pour la première fois un intervalle après le démarrage de Meshloom. Il ignore les heures calmes, *Ne pas déranger* et les limites ; il est compté mais jamais écrit dans le journal d’envoi. Un canal désactivé, auquel il manque un secret ou mis en pause après des échecs n’en envoie pas, et un battement de cœur en échec compte comme un échec, comme tout autre envoi.
- **Une URL de ping** (`heartbeat_url`, facultative) : au lieu d’un message, Meshloom appelle une URL de type healthchecks (healthchecks.io, une sonde push d’Uptime Kuma…) en `GET` ou `POST` (`heartbeat_method`), au démarrage puis à chaque intervalle. L’URL est un secret : chiffrée, affichée `********`, exclue des sauvegardes. Elle doit être en `https`/`http` et passer les mêmes contrôles que les autres destinations (voir [Destinations sur votre réseau](#destinations-sur-votre-réseau)), à l’enregistrement et à chaque ping ; les redirections sont refusées, l’appel expire au bout de quelques secondes et la réponse n’est jamais lue. Le ping continue pendant la pause après échecs, et un ping en échec n’est journalisé que par un code.

**Qui donne l’alerte.** Un battement de cœur ne sert que si quelque chose remarque qu’il s’est arrêté. ntfy et Gotify ne savent pas alerter sur un message manquant : utilisez une URL de ping avec un service qui alerte sur le silence, ou dans Home Assistant l’automatisation sur `binary_sensor.<radio>_connected` (voir `README_HA.md`).

## Les mentions

Un message de canal **vous mentionne** quand il contient `@[` + le nom de votre radio + `]` (par exemple `@[Bob]`), quelle que soit la casse. Avec **Mots entiers uniquement** coché (ligne *Mentions* du tableau), le nom de votre radio écrit comme un mot entier compte aussi : *Al* correspond à « salut al ! » mais pas à « Alpha ». Une mention est envoyée aux canaux cochés sur la ligne **Mentions**, à la place de la notification ordinaire du canal sur ces canaux : un canal ne reçoit donc jamais les deux ; elle s’intitule « Mention dans #paris par Léa » et remplace la notification ordinaire de cette conversation dans la barre de notifications. Les canaux où **Mentions** n’est pas coché continuent de recevoir la notification ordinaire. La sourdine d’un canal, les listes de blocage et Ne pas déranger s’appliquent comme pour tout message, et une mention n’est **jamais urgente**. Elle est activée par défaut pour les canaux qui reçoivent les messages privés.

**N’importe qui à portée peut forger une mention :** les messages de canal ne sont pas authentifiés, donc quiconque écrit `@[votre nom]` fait « mentionner » votre radio. C’est pourquoi une mention n’est jamais urgente, et pourquoi, sur un **canal privé** (qui a sa propre clé), elle n’atteint que les canaux qui ne sont pas un tiers (Web Push, un ntfy auto-hébergé, un relais e-mail sur votre réseau…) ou ceux que vous avez explicitement autorisés pour cette conversation par une exception (**activé**) dans la liste des conversations ; une exception sur **activé** reçoit la mention même si le canal n’est pas coché sur la ligne **Mentions**. Les canaux Apprise et webhook, et les préréglages construits sur Apprise (Telegram, Gotify, Home Assistant, Pushover et les autres), comptent toujours comme un tiers, car le type ne dit pas où ils envoient. Les messages privés ne sont pas des mentions.

## Les mots-clés

Une **règle de mot-clé** notifie un canal quand un message contient un mot ou une expression, **en plus** des notifications habituelles : « secours » ou le nom d’un lieu peut atteindre un canal dédié. On les ajoute dans **Réglages → Notifications → Mots-clés** (administrateurs) : le texte (64 caractères au plus), le mode de correspondance, où, quels canaux et un délai de récupération.

- **Correspondance.** *Contient le texte* trouve le texte n’importe où (« secours » dans « au secours ! » comme dans « secoursx »). *Mot entier* demande autre chose qu’une lettre, un chiffre ou `_` de chaque côté : « secours » correspond à « au secours ! » mais pas à « secoursx ». Un bord qui n’est ni une lettre, ni un chiffre, ni `_` (comme dans `#paris` ou un émoji) n’exige rien autour de ce côté : `🆘` correspond dans « aide🆘vite ». La casse ne compte pas, et les lettres accentuées écrites sous l’une ou l’autre forme Unicode sont égales (« é » en un caractère ou « e » plus un accent). Les accents comptent encore : « cafe » ne correspond pas à « café ». Pas d’expressions régulières, volontairement : rien de ce qu’un inconnu écrit ne peut ralentir Meshloom.
- **Où.** Toutes les conversations, les messages privés seulement, ou les conversations que vous cochez. Le nom de l’expéditeur qui commence un message de canal n’est pas cherché.
- **Canaux.** Choisissez les canaux de la règle. **Web Push n’est pas proposé** : il se décide compte par compte, et une règle de mot-clé est un réglage d’instance. Un canal qui **reçoit déjà ce message** (en message privé, en message de canal ou en mention) ne reçoit pas en plus la notification de mot-clé : une notification par canal. Le titre est « Mot-clé « secours » dans #paris ».
- **Délai de récupération.** Après une correspondance, la règle reste silencieuse pendant le délai choisi (5 minutes par défaut, de 0 à 24 heures), quel que soit l’expéditeur. En plus, un même expéditeur ne peut pas redéclencher la même règle avant 15 minutes, ou avant le délai s’il est plus long. Un message ne déclenche une règle qu’une fois.
- **Limites.** 20 règles au plus ; le serveur refuse une 21ᵉ règle et un texte de plus de 64 caractères.

Une conversation en sourdine, un expéditeur bloqué, un canal en attente, *Ne pas déranger* et la pause d’un canal l’emportent toujours : un mot-clé ne les traverse pas, et une notification de mot-clé n’est **jamais urgente**. La politique de texte de chaque canal s’applique comme pour tout message.

**Un mot-clé peut être forgé par n’importe qui à portée :** les messages de canal ne sont pas authentifiés, donc quiconque écrit le mot déclenche la règle. C’est pourquoi un mot-clé n’est jamais urgent et a des délais de récupération. Ne vous fiez pas à un mot-clé seul pour quoi que ce soit de critique.

Les règles voyagent dans la sauvegarde complète (partie *Notifications*), vérifiées comme le fait l’API ; une règle qui serait refusée est écartée avec un avertissement.

## Les nœuds surveillés

Un **nœud surveillé** notifie un canal quand un nœud précis est de nouveau entendu, ou revient après un long silence : un répéteur sur une colline, la radio d’un ami. Activez **Notifier quand il est entendu** dans la fiche contact du nœud (administrateurs ; les canaux atteignables sont proposés sous forme de pastilles, tous sauf Web Push sont cochés au départ), puis ajustez chaque nœud dans **Réglages → Notifications → Nœuds surveillés**.

- **Entendu.** Un nœud est *entendu* quand son annonce arrive ou qu’un message privé de sa part est déchiffré. La notification est « Hilltop entendu » (`contact.node_seen`).
- **De retour.** Quand l’observation précédente remonte à plus que le **seuil de silence** (24 heures par défaut, de 1 minute à 365 jours), la notification est « Hilltop est de retour après 2 j » (`contact.node_back`).
- **Délai de récupération.** Un nœud notifie au plus une fois par délai (1 heure par défaut, de 0 à 30 jours), quelle que soit la notification, si bien qu’un nœud qui s’annonce toutes les quelques minutes n’inonde jamais un canal. Une observation pendant le délai n’est pas notifiée mais compte quand même comme la dernière observation pour le silence.
- **Canaux.** Choisissez les canaux de chaque nœud. **Web Push n’est pas proposé** : il se décide compte par compte, et un nœud surveillé est un réglage d’instance. Un nœud sans canal n’envoie rien.
- **Limites.** 50 nœuds au plus, imposés par le serveur (`watched_nodes` dans `PATCH /api/notifications/rules`) et à la restauration d’une sauvegarde. Seuls les nœuds surveillés sont retenus (dernière observation et délai, conservés après un redémarrage) : l’annonce d’un autre nœud ne coûte rien.

*Ne pas déranger*, la pause d’un canal et les politiques de texte s’appliquent comme pour toute notification. Les annonces sont signées mais n’importe qui à portée peut les rejouer, une raison de plus pour le délai de récupération. Les nœuds voyagent dans la sauvegarde complète (partie *Notifications*), vérifiés comme le fait l’API.

## Conversations et exceptions

Sans exception, le tableau décide : un message direct suit la ligne **Messages privés**, le salon Public et les salons `#` (hashtag) suivent la ligne **Messages de canal**, et un salon à clé privée ne notifie personne. Une **exception** est un choix fait pour une conversation et un canal (*activé* ou *désactivé*) ; elle l’emporte sur le tableau, sauf face à la sourdine, aux listes de blocage et à une pause (voir plus bas). Les exceptions se font avec la cloche.

### La cloche, pour une conversation

Dans l’en-tête d’une conversation, la cloche (réservée aux administrateurs ; les autres rôles n’en ont pas) ouvre un menu avec une case par canal de notification activé, avec l’icône de son type, son nom et un point d’état (connecté, erreur, en pause, déconnecté). Une case montre si le canal notifie cette conversation, et le mot gris à côté dit pourquoi : *par défaut* (les lignes du tableau décident), *activé ici* ou *coupé ici* (une **exception** faite pour cette conversation seulement). Cocher une case que le défaut laisse déjà cochée, ou décocher une case que le défaut laisse vide, fait suivre à nouveau le défaut à la conversation et supprime l’exception. Si votre navigateur n’est pas encore abonné, activer **Web Push** l’abonne d’abord. La cloche est pleine tant qu’au moins un canal notifie la conversation. Elle montre la configuration : un salon en sourdine ou un Ne pas déranger actif laisse les cases cochées alors qu’aucune notification n’est envoyée.

Le même menu contient le raccourci **Ne pas déranger** (1 heure, 8 heures, jusqu’à demain 8:00, **Reprendre maintenant** : la même pause générale que dans les réglages) et un lien **Configurer les canaux de notification**. Dans la liste des conversations, le même menu est sous les trois points d’une ligne, **Notifications**.

Les exceptions sont listées dans **Réglages → Notifications → Exceptions par conversation**, une ligne par conversation avec un bouton par canal : cliquez pour passer de *hérite* (la règle de l’événement) à *oui* puis *non*. La croix supprime l’exception.

## Ne pas déranger

En haut des règles dans **Réglages → Notifications**, **Ne pas déranger** suspend toutes les notifications pendant **1 heure**, **8 heures** ou **jusqu’à demain 8 h** (heure de l’horloge du navigateur), et **Reprendre** y met fin. Le temps restant est affiché. Les événements cochés **Toujours livrer** (colonne toujours cochée et verrouillée pour les événements urgents : radio hors ligne, alertes de télémétrie et canal modifié) passent quand même. La pause est commune à tous les navigateurs et à tous les canaux ; elle est inscrite au journal d’audit. Une notification qui arrive pendant une pause n’est pas envoyée plus tard : le journal des envois la note *paused*. Elle est seulement comptée, et à la fin de la pause chaque canal reçoit un message de résumé (« Pendant la pause : 3 messages dans 2 conversations… », le même genre de résumé que les heures calmes, jamais le texte d'un message). Le résumé n’est envoyé que si l’option de résumé des heures calmes est active (elle l’est par défaut). La pause propre d’un canal ne produit pas de résumé.

## Heures calmes

Les heures calmes sont une plage récurrente (par exemple de 22:00 à 07:00) pendant laquelle les notifications sont retenues au lieu d'être envoyées, puis résumées en un seul message à la fin de la plage. Elles se règlent dans **Réglages → Notifications**, dans le groupe **Heures calmes** sous *Ne pas déranger* : l’interrupteur, les heures de début et de fin, les jours et l’option de résumé. (Les mêmes champs existent dans l’API des règles : `quiet_hours` dans `PATCH /api/notifications/rules`, avec `enabled`, `start` et `end` au format `HH:MM`, `weekdays` de 0 = lundi à 6 = dimanche, `digest_at_end`.) Les heures sont lues dans le fuseau de la machine qui fait tourner Meshloom, renvoyé par `GET /api/notifications/rules` sous `host_timezone`. Une plage qui passe minuit appartient au jour où elle commence, et un début égal à la fin couvre la journée entière choisie.

- **Ce qui passe.** Les événements urgents (radio hors ligne, alertes de télémétrie, canal modifié, canal en échec), les événements cochés **Toujours livrer**, et tout canal dont la case **Exempté des heures calmes** est cochée (sur sa carte dans la liste des canaux ; cochée par défaut pour un webhook créé désormais, décochée pour les autres types). Le webhook importé des réglages 4.18.x (`legacy:webhook`) est décoché, car les heures calmes n’existaient pas alors et sont désactivées tant que vous ne les activez pas : cochez sa case s’il doit continuer à recevoir. Le reste est retenu sur les autres canaux, et le journal des envois le note *suppressed_quiet*.
- **Le résumé.** Par canal, un message une fois la plage terminée (vérifié chaque minute) : « Pendant les heures calmes : 14 messages dans 3 conversations (#paris 9, Léa 3, #test 2), 1 nouveau contact ». Il ne contient que des nombres et des noms, jamais le texte d'un message. Les canaux publics et `#` sont toujours nommés ; un message privé ou un canal privé n'est nommé que sur un canal dont la politique de texte est *toujours*, sinon il est seulement compté. Les compteurs sont gardés en base : un redémarrage pendant la plage ne perd rien. Avec l’option de résumé désactivée, rien n'est compté et aucun résumé n'est envoyé (la même option gouverne le résumé après *Ne pas déranger*). Le même événement reçu deux fois pendant la plage (même identité) n’est compté qu’une fois.
- **Avec les autres pauses.** L'ordre est : vos choix et les règles de l'administrateur décident qui veut l'événement (une conversation en sourdine, un événement qu'un compte a désactivé ou un compte en pause pour lui-même n'est jamais compté) ; puis **Ne pas déranger** et la pause propre d'un canal le retiennent (*paused* ; seule la pause globale est comptée pour son résumé) ; puis les heures calmes. **Toujours livrer** et les événements urgents passent les deux. *Toujours me notifier* sur une conversation ne contourne pas les heures calmes. Si **Ne pas déranger** est actif à la fin de la plage, le résumé attend sa fin. Un résumé qui n’a pas pu être mis en file (limite de débit, canal en pause) est conservé et retenté chaque minute : dix minutes au plus s’il a été refusé (limite de débit), sans limite tant que le canal est en pause pour lui-même (le résumé part une minute après la fin de la pause) ; un résumé mis en file qui échoue sur une erreur temporaire a une nouvelle tentative cinq minutes plus tard. Sur Web Push, le résumé n'atteint que les comptes qui auraient reçu au moins un des événements retenus.

## Mode résumé

Un canal très actif peut devenir un résumé périodique : dans les réglages **Avancé** du canal, **Mode résumé** prend un intervalle (5 min, 15 min, 30 min, 1 h, 6 h ou 24 h ; désactivé par défaut ; l’API accepte 0 ou 300 à 86400 s dans `digest_interval_s`, sur tous les types de canal). Au lieu d’une notification par message, le canal reçoit alors un résumé par intervalle, par exemple « Depuis le dernier résumé : 14 messages dans 3 conversations (#paris 9, Léa 3, #test 2), 1 nouveau contact ». C’est le résumé des heures calmes (mêmes comptes, même confidentialité : des nombres et des noms, jamais le texte d’un message ; un message privé ou un canal privé n’est nommé que si la politique de texte du canal est *toujours*), envoyé selon une minuterie plutôt qu’à la fin d’une plage.

- **Ce qui est additionné.** Les messages privés, les messages de canal et les nouveaux contacts (`message.dm`, `message.channel`, `contact.first_seen`). Le même événement reçu deux fois n’est compté qu’une fois.
- **Ce qui arrive toujours immédiatement.** Les événements urgents (radio hors ligne, alertes de télémétrie, canal modifié), les événements cochés **Toujours livrer**, et ceux que vous avez demandés un par un : les **mentions**, les alertes de **mot-clé** et les **nœuds surveillés** (entendu, de retour). Un résumé qui les retarderait irait contre leur but. Un message qui correspond à une règle de mot-clé du canal est à la fois annoncé aussitôt par l’alerte de mot-clé et compté une fois dans le résumé suivant. Les autres canaux et le Fil de l’application ne sont pas touchés : le Fil reçoit toujours chaque événement au moment où il se produit.
- **La fenêtre.** Elle s’ouvre au premier événement retenu et le résumé part un intervalle plus tard (vérifié chaque minute, avec donc jusqu’à une minute de retard). L’événement suivant ouvre une nouvelle fenêtre. Une fenêtre sans événement n’envoie rien. Si vous raccourcissez l’intervalle pendant qu’une fenêtre est ouverte, elle se termine un nouvel intervalle après la prochaine vérification.
- **Avec les autres pauses.** Le même ordre que pour les heures calmes : les règles et vos choix décident d’abord, puis **Ne pas déranger** et la pause propre du canal retiennent l’événement, puis les heures calmes (un événement pendant les heures calmes est compté dans le résumé des heures calmes, pas ici ; un résumé dû pendant elles attend la fin, sauf sur un canal exempté des heures calmes), puis le mode résumé. Les limites du canal s’appliquent au résumé comme à tout message.
- **Redémarrage.** Les comptes et la fin de la fenêtre sont conservés dans la base. Après un redémarrage la fenêtre continue ; celle qui s’est terminée pendant que Meshloom était arrêté part à la première vérification après le démarrage. Désactiver le mode résumé envoie aussitôt ce qui était en attente ; désactiver ou supprimer le canal l’abandonne.

## Sourdine et listes de blocage

Sur un salon, le bouton de sourdine fait taire tout pendant la durée choisie (de 15 minutes à 24 heures, ou indéfiniment). Il masque aussi le compteur de non lus. Il l’emporte sur les valeurs par défaut et les exceptions, et ce n’est pas le même contrôle que la cloche.

Les messages d’un contact ou d’un nom bloqué, et ceux d’un salon en sourdine (jusqu’à la fin de la sourdine), ne notifient sur aucun canal : Web Push, e-mail, webhook et Apprise. Seul un événement marqué **Toujours livrer** passe quand même. Rien ne change tant que vous ne bloquez personne et ne mettez aucun salon en sourdine.

## Langue

**Réglages → Notifications → Langue des notifications** fixe la langue de l’instance pour les notifications : **Automatique**, **Français** ou **English**. Automatique suit les navigateurs abonnés au push : anglais seulement si la plupart d’entre eux (une majorité stricte) sont en anglais, français en cas d’égalité. Chaque canal a aussi sa propre **Langue des notifications** dans son formulaire (Automatique reprend la langue de l’instance). Les notifications Web Push gardent toujours la langue du navigateur abonné. Un webhook v1 envoie des données, pas du texte ; un webhook v2 y ajoute la notification rédigée (`rendered`) dans la langue du canal.

## Preuve de modification

Créer, supprimer, activer ou désactiver un canal, ou changer l’endroit où il envoie, écrit une ligne dans le journal d’audit et envoie la notification **Canal modifié** à chaque navigateur abonné par un administrateur et, par défaut, aux autres canaux activés, en disant ce qui a changé. La copie Web Push ne peut pas être désactivée : si ce n’est pas vous, quelqu’un d’autre l’a fait. Changer une destination 4.18.x par les anciens réglages envoie de la même façon **Destination de notification modifiée** à Web Push. Les appareils des autres comptes ne reçoivent jamais ces avis, ni **Canal en échec** : ils nomment des canaux et des appareils, que seuls les administrateurs gèrent.

## Comment les notifications partent

Chaque canal a sa propre file et envoie une notification à la fois : un service lent ou en panne ne retarde jamais les autres, ni le push. Les envois sont espacés : au moins 10 secondes pour l’e-mail, 3 pour Telegram, 10 pour les préréglages SMS, 2 pour Discord et Apprise, 1 pour ntfy, Pushover et Slack. Le nombre de notifications autorisé est fixé par les [limites](#limites-et-regroupement) du canal. Un envoi Web Push ou e-mail n’est jamais retenté, et Apprise et les préréglages retentent chaque URL eux-mêmes (0, 5 et 30 s) sans l’essai supplémentaire ci-dessous. Pour les autres types, un envoi qui échoue parce que le service est injoignable, trop lent, répond une erreur serveur ou demande de ralentir est retenté après 5 s puis 30 s (son `Retry-After` est respecté, jusqu’à 2 minutes), jamais quand il refuse la requête ; Web Push et l’e-mail ne sont pas retentés. Une notification de message qui échoue encore est abandonnée (elle arriverait trop tard) ; une radio hors ligne ou une alerte de télémétrie a droit à un essai de plus 5 minutes plus tard quand le service était injoignable, jamais après un refus. Les notifications encore en attente quand Meshloom s’arrête ne partent pas après le redémarrage.

L’état de chaque destination figure dans `/api/health`, sous `notification_statuses`, par identifiant (`builtin:push`, `legacy:email`, `legacy:webhook`, `legacy:apprise` et l’identifiant de chaque canal que vous ajoutez) : `status` (`connected`, `error`, `paused` ou `disconnected`), la dernière erreur sous forme de code, jamais un mot de passe ni un jeton (administrateurs seulement), et l’heure du dernier envoi réussi. `notifications_failing` vaut `true` tant qu’un canal a échoué 3 fois de suite, est en pause après des échecs ou a un secret indéchiffrable. Meshloom garde un court journal des notifications en échec, regroupées, abandonnées et annulées, sans leur texte ; les envois réussis sont seulement comptés.

Le test d’un canal Apprise envoie une notification de test à chaque URL ; un échec indique la ligne de l’URL et le type d’échec, jamais le jeton.

### Limites et regroupement

Chaque canal a des limites par minute, par heure et, pour certains, par jour. Les défauts viennent du type (Telegram 15 par minute et 200 par heure, Pushover 10 et 100, SMS 2 par minute, 20 par heure et 100 par jour, Discord 30 et 600, e-mail 6 par minute, 60 par heure et 200 par jour, webhook 120 par minute, Web Push 60 par minute, ntfy.sh 10 par minute, 30 par heure et 200 par jour) ; un canal peut avoir les siennes. Rien n’est perdu en silence :

- **Une limite par minute ou par heure atteinte n’abandonne pas les notifications.** Elles attendent une minute, puis partent en **un seul résumé** du genre « +14 messages : 12 dans #paris, 2 MP ». Le résumé ne fait que compter : jamais le texte d’un message, un expéditeur ni le nom d’un canal privé (comptés « dans d’autres canaux »). Si la limite horaire est encore pleine, le résumé attend qu’il y ait de la place.
- **Les évènements urgents** (radio hors ligne, alerte de télémétrie) prennent un jeton comme les autres, mais passent une limite pleine une fois par évènement : un capteur qui oscille sans cesse ne contourne pas la limite, ses répétitions rejoignent le résumé.
- **Une limite par jour est un quota.** Quand l’allocation du jour est utilisée (comptée depuis minuit UTC et conservée d’un redémarrage à l’autre), plus rien n’est envoyé à ce canal jusqu’au jour UTC suivant : pas de résumé non plus, et chaque notification refusée est inscrite *quota dépassé* dans le journal des envois.
- **Plafond global.** Tous canaux confondus, Meshloom envoie au plus 600 notifications par heure (`limits.global_per_hour` dans les règles). Au-delà, rien n’est envoyé, urgents compris, chacune est inscrite *limitée* dans le journal, et `/api/health` indique `notifications_capped: true` jusqu’à ce que l’heure ait de la place.
- **Pas de répétition.** Un nouveau contact notifie une fois par jour, une panne de radio une fois, un message une fois, même après un redémarrage. Une alerte de télémétrie n’est limitée que par sa propre hystérésis.

### Réglages avancés et journal d’envoi

Dans la fenêtre de modification d’un canal, **Avancé** ouvre les limites du canal par minute, par heure et par jour, avec la valeur par défaut du type sous chaque champ (un champ vide la garde), la case **Exempté des heures calmes** (la même que sur la carte), le **Mode résumé** (voir [Mode résumé](#mode-résumé)) et, sur les types qui en ont un, le **Battement de cœur** (voir [Savoir quand Meshloom lui-même est arrêté](#savoir-quand-meshloom-lui-même-est-arrêté)). Le groupe des heures calmes, avec le fuseau horaire de la machine, est dans les règles ; l’en-tête de la colonne **Toujours livrer** du tableau y renvoie.

Chaque carte a un bouton **Journal d’envoi**. Il ouvre un tiroir avec les 50 dernières entrées du journal du canal (heure, nom de l’événement, pastille d’état comme *Échec*, *Groupée*, *Heures calmes*, *Limitée*, *Limite quotidienne*, et l’erreur sous forme de texte court traduit, jamais un texte de message ; un code sans traduction s’affiche « Échec de l’envoi (code) »), le nombre de notifications envoyées sur les dernières 24 heures et un bouton **Actualiser**. Sur la carte, une ligne indique, quand il y en a, combien de notifications ont été retenues pendant les heures calmes et combien ont été regroupées en un résumé sur les dernières 24 heures. Ces deux chiffres sont exacts (le journal lui-même ne garde que les 200 dernières entrées par canal). Quand Meshloom atteint son plafond horaire global (voir plus haut), un bandeau en haut de la liste des canaux l’indique (`notifications_capped` dans `/api/health`).

## Tester un canal

Chaque canal a un bouton **Tester**, et le dialogue d’ajout teste **avant tout enregistrement** (**Envoyer un test et continuer**). Un test passe par l’expéditeur propre au canal et les mêmes vérifications qu’un vrai envoi, sans entrer dans la file, le journal ni les compteurs. Sur **Web Push**, le test n’atteint que les appareils du compte qui a appuyé sur **Tester** ; un battement de cœur, comme les autres avis sur les canaux, n’atteint que les appareils des administrateurs. Un échec s’affiche sous forme d’un code court, traduit, qui ne contient jamais une adresse, un jeton ni le texte d’erreur du réseau :

| Code | Signification |
|------|---------------|
| `invalid_config`, `config_required` | Un champ est faux ou vide ; le champ est signalé |
| `text_policy_locked` | La politique de texte d’un canal SMS ne peut pas être changée (elle reste sur Jamais) |
| `secret_required` | Vous avez changé l’endroit où le canal envoie, donc le secret enregistré n’est pas réutilisé : saisissez-le de nouveau |
| `egress_scheme`, `egress_private_address`, `egress_unresolvable`, `egress_host_not_allowed` | L’adresse est refusée : mauvais schéma, réseau privé non autorisé (voir [Destinations sur votre réseau](#destinations-sur-votre-réseau)), nom d’hôte inconnu (saisissez l’adresse IP), ou hôte que le type n’accepte pas |
| `verify_tls_requires_private_destination`, `smtp_plaintext_auth_refused`, `tls_error` | La vérification du certificat ne se désactive que pour une destination privée ; un identifiant de messagerie n’est jamais envoyé sans chiffrement hors de votre réseau ; la connexion sécurisée a échoué |
| `auth_failed` | Le service a refusé le jeton, le mot de passe ou la clé (HTTP 401 ou 403) |
| `rate_limited`, `quota_exceeded` | Le service demande de ralentir, ou le quota du jour est épuisé (ntfy.sh) |
| `timeout`, `service_error` | Pas de réponse à temps ; ou le service a répondu par une erreur |
| `apprise_url_invalid`, `apprise_chat_id_required` | Une adresse Apprise est refusée (la ligne est nommée) ou une adresse Telegram n’a pas son identifiant de chat |
| `no_push_device` | Web Push seulement : rien n’a été envoyé, car aucun de vos appareils (ou, pour le nouvel essai automatique d’un canal en pause, d’un administrateur) n’est inscrit. L’état du canal reste tel quel |

Les tests sont limités : un toutes les 5 secondes par canal, un toutes les 2 secondes pour une configuration pas encore enregistrée, et 10 par minute en tout. Au-delà, la réponse est `429 rate_limited`. Le résultat d’un test sur un canal enregistré met aussi à jour son état sur la carte.

## Sauvegardes

La **sauvegarde complète** (Réglages → Base de données) contient une partie *Notifications* : chaque canal (type, nom, options, limites, langue, politique de texte, modèles de message) et les règles (événements, exceptions par conversation, règles de mots-clés, nœuds surveillés, options du tableau de règles et heures calmes), **sans aucun secret**. Les choix propres à chaque compte (voir [Vos propres règles](#vos-propres-règles)) voyagent avec les comptes : ils reviennent quand **Inclure les comptes** est coché. Les mots de passe, jetons, adresses Discord ou Apprise, sujets ntfy, URL de webhook et leur secret HMAC, et l’URL de ping du battement de cœur ne quittent jamais Meshloom. L’intervalle et la méthode du battement de cœur, ainsi que l’intervalle du mode résumé, voyagent avec le canal (un intervalle hors de 300 à 86400 s est restauré désactivé, avec un avertissement). Les destinations e-mail, webhook et Apprise des anciens réglages sont elles aussi écrites sans leurs secrets. Les listes de blocage voyagent avec les réglages. Le journal des envois et les compteurs ne sont pas sauvegardés, ni la pause : *Ne pas déranger* n’est jamais restauré. Les clés et abonnements Web Push forment une autre partie, restaurée seulement sur un Meshloom sans abonnement propre.

**Restauration.** Un canal qui a perdu un secret revient **désactivé** et marqué *Secret manquant après une restauration* ; Meshloom n’envoie jamais avec un secret manquant. Ouvrez-le dans **Réglages → Notifications → Canaux**, saisissez de nouveau chaque secret (ou effacez-le exprès), puis activez-le. Si le même canal existe déjà ici avec la même destination, ses secrets enregistrés sont conservés. La restauration fusionne les canaux (un canal que le fichier ne nomme pas reste) et remplace les règles. Une sauvegarde faite par Meshloom 4.18 se restaure dans les mêmes canaux et règles (Web Push, e-mail, webhook, Apprise, les exceptions de chaque conversation).

**Déplacée sans sa clé.** Dans la base, les secrets des canaux sont chiffrés avec le fichier `secrets.key` posé à côté d’elle. Une base copiée ou restaurée sans ce fichier (ou avec un autre) ne peut pas les ouvrir : Meshloom démarre quand même, et chaque canal concerné reste désactivé avec un message qui dit que le secret ne peut pas être déchiffré. Remettez le `secrets.key` d’origine à côté de la base et redémarrez, ou saisissez le secret à nouveau. Voir [Autres chemins d’installation](/docs/deep/install-paths/#base-de-données-et-mises-à-jour).

Chiffrez quand même la sauvegarde complète avec une phrase secrète : elle contient encore la clé privée Web Push et les abonnements, les secrets de vos intégrations (MQTT, webhooks du fanout, SQS) et, si vous les incluez, les comptes. Voir [Sauvegarder la radio](/docs/backup/) et [Sécurité](/docs/deep/security/#secrets-des-intégrations-et-des-notifications).

## Configurer les canaux depuis un réseau de confiance

Un canal décide où vont vos messages. Qui peut en créer ou en modifier un peut envoyer une copie de vos notifications au service de son choix. C’est pourquoi toutes les routes sous `/api/notifications` demandent un **administrateur** : les autres rôles ne voient ni ne modifient aucun canal (un membre abonne seulement son propre navigateur au push). Ne donnez le rôle d’administrateur qu’aux personnes à qui vous confiez le nœud, connectez-vous en [HTTPS](/docs/deep/https/) hors d’un réseau de confiance, et gardez courte votre liste d’[Origines autorisées](/docs/deep/security/). Tout changement de destination d’un canal envoie une notification Web Push à chaque navigateur abonné par un administrateur (voir [Preuve de modification](#preuve-de-modification)).

Sur un Meshloom antérieur à la 5.0, il n’y avait aucune connexion : quiconque atteignait la page pouvait configurer un canal. Configurez les canaux depuis un réseau de confiance.

## Mise à jour et retour en arrière

**Depuis la 4.18.x.** Au premier démarrage, Meshloom transforme vos destinations en canaux : le canal intégré **Web Push**, et **`legacy:email`**, **`legacy:webhook`** et **`legacy:apprise`** pour celles que vous aviez configurées, et il transforme vos valeurs par défaut du push et vos exceptions par conversation en règles. Ce que vous recevez ne change pas. Ces trois canaux `legacy:` s’éditent toujours avec leur propre formulaire sur leur carte (celui de la 4.18.x, enregistré quand vous quittez un champ), et leur type n’est plus proposé dans **Ajouter un canal**. Vous pouvez les activer et les désactiver, les renommer et changer leurs règles comme tout canal, mais pas les supprimer par l’API des canaux ni y changer leur adresse ou leurs options : l’API répond **`409 legacy_managed`** à un `PATCH` de `config`, `allow_private_network` ou `verify_tls`, et à un `DELETE`, sur un canal `legacy:`. Utilisez plutôt **Supprimer cette destination** sur la carte. Un moyen jamais configuré (pas de serveur SMTP, pas d’URL) ne devient pas un canal. Une exception de conversation qui le concerne est conservée et s’applique dès que vous le configurez.

**Points d’accès dépréciés.** `GET`/`PATCH /api/push/preferences`, `PUT /api/push/preferences/conversations/{key}`, `notification_destinations` dans `PATCH /api/settings` et `POST /api/settings/notification-destinations/test` fonctionnent toujours comme en 4.18.x. Ils répondent un en-tête `Deprecation: @1792368000` (RFC 9745) et un `Link` vers le point d’accès des règles, et **seront retirés en 5.2.0**. Chaque changement qu’ils font est copié dans les règles, et inversement, pour les quatre moyens push, e-mail, webhook et Apprise ; les autres canaux leur sont invisibles. Demander e-mail, webhook ou Apprise sur une conversation quand cette destination n’est pas configurée répond `400 channel_missing`. Faites migrer vos scripts vers `/api/notifications`.

**Apprise dans le fanout.** La sortie Apprise du [fanout](/docs/deep/fanout/#apprise) n’est pas convertie automatiquement : elle continue de fonctionner et de transmettre chaque message. Elle est dépréciée (retrait en 5.2.0) : [convertissez-la](/docs/deep/fanout/#convertir-en-canal-de-notification) en canal de notification en un clic.

**Retour en arrière.** Revenir à une version antérieure à la 5.0 relit les anciens réglages. **Les canaux ajoutés après la mise à jour ne sont pas visibles après un retour en arrière**, les règles que vous avez changées dans le tableau ne sont pas lues, et un canal qui n’était pas une destination 4.18.x (ntfy, Discord, Telegram, Gotify, Home Assistant, un deuxième e-mail ou webhook) cesse de notifier. Notez les destinations dont vous avez besoin avant de revenir, et restaurez une sauvegarde faite avant la mise à jour si vous voulez retrouver exactement l’ancien état.

## Quand rien n’arrive

1. Regardez la carte du canal dans **Réglages → Notifications → Canaux** : le voyant d’état (fonctionne, erreur, en pause, désactivé) et la dernière erreur. Un canal restauré sans son secret est désactivé et indique *Secret manquant après une restauration*.
2. Un canal qui a échoué 3 fois de suite apparaît dans un bandeau en haut de **Canaux** et porte un badge, et les autres canaux reçoivent **Canal en échec** (une fois par jour). Après 20 échecs de suite, il est mis en pause et n’envoie plus rien, pas même les événements urgents : Meshloom lui envoie un test après 5 min, 15 min, 1 h, puis toutes les 6 h, et le premier succès le relance. **Relancer** dans le bandeau le relance aussitôt et remet le compteur à zéro. Un canal dont le secret enregistré ne peut pas être déchiffré y apparaît aussi : saisissez de nouveau le secret dans ses réglages.
3. Appuyez sur **Tester** sur la carte. Voir [Tester un canal](#tester-un-canal) pour les codes. Un réseau privé non autorisé est refusé avant toute connexion.
4. Vérifiez le tableau des règles : l’événement doit être coché pour ce canal. Une sourdine, un blocage, un **Ne pas déranger** en cours et une exception de conversation l’emportent (voir [Sourdine et listes de blocage](#sourdine-et-listes-de-blocage)) ; les salons à clé privée restent muets sans exception qui les active.
5. Le canal est-il activé ? **Ne pas déranger** est-il actif ? Seuls les événements marqués **Toujours livrer** le traversent.
6. Pour Web Push, voir [Quand rien n’arrive](/docs/deep/push/#quand-rien-narrive) sur la page du push : HTTPS, autorisation du navigateur, Apple.
7. Regardez le journal du serveur et `GET /api/notifications/deliveries`, qui liste les échecs, les notifications abandonnées et celles ignorées pendant une pause, sans leur texte. Voir [Dépannage](/docs/deep/troubleshooting/).

## Points d’accès

Les administrateurs gèrent les canaux sous `/api/notifications` :


| Méthode | Point d’accès | Effet |
|---------|---------------|-------|
| GET | `/api/notifications/types` | Les types de canal : schéma JSON (les champs secrets sont `writeOnly`), limites par défaut |
| GET, POST | `/api/notifications/channels` | Liste et crée des canaux (les secrets s’affichent `********` ; une création renvoie aussi `fanout_duplicates`, les intégrations Apprise du fanout qui envoient au même endroit, par id et nom) |
| POST | `/api/notifications/channels/test` | Teste une configuration avant de l’enregistrer (rien n’est stocké) |
| POST | `/api/notifications/channels/preview-template` | Rend `template_dm` et `template_channel` sur un message privé et un message de canal d’exemple (`markdown`, `include_message_text` et `language` sont facultatifs) ; `400 template_too_long` ou `template_invalid` pour un modèle refusé |
| GET, PATCH, DELETE | `/api/notifications/channels/{id}` | Lit, modifie (le type ne peut pas changer) et supprime un canal ; `builtin:push` ne peut pas être supprimé (`403 builtin_channel`) |
| POST | `/api/notifications/channels/{id}/test` | Envoie un test ; un par 5 secondes et par canal, puis `429 rate_limited` |
| POST | `/api/notifications/channels/{id}/resume` | Relance un canal mis en pause après des échecs et remet son compteur d’échecs à zéro |
| GET, PATCH | `/api/notifications/rules` | Canaux par événement, `always_deliver`, pause globale (`paused_until`), délai de radio hors ligne, règles de mots-clés (`keywords`), nœuds surveillés (`watched_nodes`) |
| PUT | `/api/notifications/rules/conversations/{key}` | Canaux d’une conversation (`{"channels": {"id": true}}`, ou `null` pour effacer) |
| GET | `/api/notifications/deliveries` | Le journal des envois (échecs et suppressions, jamais le texte) et les compteurs horaires, plus les `totals` exacts sur 24 h (`sent`, `suppressed_quiet`, `aggregated`) |
| GET | `/api/notifications/events` | Le catalogue des événements |


Pour conserver un secret en modifiant un canal, omettez-le, envoyez `null` ou envoyez `********` ; `""` l’efface. Les erreurs sont `{"code": ..., "params": ...}` et ne contiennent jamais un hôte, une adresse ou un texte d’exception. Créer, supprimer, activer ou désactiver un canal, ou changer sa destination, notifie le Web Push (cela ne peut pas se désactiver) et les autres canaux actifs, et s’inscrit dans le journal d’audit.
