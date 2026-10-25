---
title: Notifications
description: Canaux, événements et règles : être prévenu quand quelque chose se passe, sur votre téléphone, par e-mail, sur ntfy, Telegram, Discord, Home Assistant et d’autres.
level: deep
order: 17
---

Meshloom peut vous prévenir de ce qu’il entend et de son propre état sans que vous regardiez l’interface : un message privé, une mention, un répéteur qui se tait, la radio qui décroche. Chaque endroit où il envoie est un **canal**. Ce qui va vers quel canal est décidé par un seul tableau de **règles**, commun à toute l’installation.

Tout se règle dans **Réglages → Notifications** (administrateurs seulement). Rien ici ne demande de variable d’environnement. Ce qui est propre aux navigateurs (HTTPS, l’adresse de contact VAPID, l’abonnement d’un navigateur) est sur [Notifications push](/docs/deep/push/) ; cette page couvre tout le reste.

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
| Apprise | Tout autre service que [Apprise](https://appriseit.com/) gère, à partir de son URL | Toujours |

Les **types natifs** (Web Push, e-mail, webhook, ntfy, Discord) sont écrits pour le service. Les **préréglages** (Telegram, Gotify, Home Assistant) sont des formulaires prêts à l’emploi pour des services que Meshloom atteint par Apprise : vous remplissez des champs nommés, Meshloom construit l’adresse Apprise au moment d’envoyer et ne la stocke jamais. Le type générique **Apprise** est la porte de sortie pour tout le reste. Le type d’un canal ne change pas après sa création : créez-en un nouveau.

Tout ce qui concerne un canal, ses secrets compris, est expliqué dans [Ajouter un canal](#ajouter-un-canal) et dans les sections de chaque type ci-dessous.

### Ajouter un canal

**Réglages → Notifications → Canaux** (administrateurs) liste en cartes tous les endroits où Meshloom envoie des notifications : son type, un voyant d’état (fonctionne, erreur, en pause, désactivé), la dernière erreur, un interrupteur, **Tester**, **Modifier** et **Supprimer**. La carte **Web Push** est toujours là et ne se supprime pas ; ouvrez-y **Appareils et réglages Web Push** pour l’abonnement de ce navigateur, les autres appareils, le sujet VAPID et l’interrupteur des points d’accès privés. En HTTP simple, la carte indique que Web Push est indisponible.

**Ajouter un canal** ouvre un dialogue en trois étapes. D’abord choisir un type ou un préréglage (E-mail, Webhook, ntfy, Discord, Telegram, Gotify, Home Assistant ou Apprise). Ensuite remplir son formulaire, que le serveur décrit lui-même : les champs obligatoires sont marqués d’une étoile, les erreurs de validation du serveur apparaissent sous le champ, et un secret (mot de passe, jeton, adresse de webhook) n’est jamais affiché, même après l’enregistrement : le formulaire indique qu’une valeur est définie et propose **Remplacer** et **Effacer** (n’y touchez pas pour la conserver). Un service tiers (« cloud ») affiche une note de confidentialité et permet de choisir si le texte des messages est inclus (canaux publics seulement par défaut). **Envoyer un test et continuer** envoie un test avant tout enregistrement ; s’il échoue, l’erreur est expliquée et vous pouvez corriger le formulaire, ou continuer sans test réussi. Enfin, **Que recevoir ici ?** pré-remplit les événements : **Comme Push** (les événements que reçoit Web Push, par défaut), **Alertes seulement** (radio hors ligne ou revenue, alertes de télémétrie, mentions, changements de canal) ou **Tout**. Vous pourrez le modifier ensuite dans le tableau ci-dessous.

Les canaux `legacy:email`, `legacy:webhook` et `legacy:apprise` (vos destinations 4.18.x) gardent leur adresse et leurs options dans le réglage 4.18.x, que leur expéditeur lit encore à chaque envoi ; les règles décident de ce qu’elles reçoivent comme pour tout canal. **Modifier** sur leur carte ouvre le même formulaire qu’en 4.18.x (il enregistre quand vous quittez un champ), et **Supprimer cette destination** la vide ; leur type n’est plus proposé dans **Ajouter un canal**.

### Canaux ntfy

**Ajouter un canal → ntfy** publie vers un sujet [ntfy](https://ntfy.sh) : gratuit, auto-hébergeable, avec des applications Android, iOS et bureau, et utilisable avec un Meshloom servi en HTTP simple sur votre réseau local. Abonnez-vous au même sujet dans l'application ntfy.

- **Serveur** : `https://ntfy.sh` par défaut, ou le vôtre (`http://192.168.1.20:8080`; ni nom d'utilisateur ni mot de passe dans l'adresse, utilisez les champs ci-dessous). Un serveur de votre réseau privé, ou de cette machine, demande **Autoriser une destination sur un réseau privé**.
- **Sujet** (obligatoire, en écriture seule) : 1 à 64 lettres, chiffres, `-` ou `_`. Sur ntfy.sh le sujet est le seul secret : qui le connaît peut lire et publier, les sujets sont donc « publics par obscurité ». Générez-en un long et aléatoire vous-même, par exemple `openssl rand -base64 18 | tr '+/' '-_'` (24 caractères sûrs pour une URL). Meshloom publie le message en JSON à l'adresse du serveur : le sujet ne figure jamais dans une URL ni dans un journal, et il n'est plus jamais affiché après l'enregistrement.
- **Jeton d'accès**, ou **nom d'utilisateur et mot de passe** (en écriture seule), pour un serveur à accès contrôlé. Le jeton l'emporte si les deux sont renseignés. Changer de serveur demande de ressaisir les secrets : ils ne suivent jamais une nouvelle destination.
- **Priorités** : les événements ordinaires utilisent la priorité par défaut (3), les urgents (*radio hors ligne*, alertes de télémétrie, changements de canal) la priorité urgente (5, qui sonne sur la plupart des téléphones). Les **étiquettes** sont des étiquettes ntfy (les codes emoji s'affichent en icônes).
- **Adresse de ce Meshloom** (`https://meshloom.example.net/`) : toucher la notification ouvre la conversation ou le nœud (pas de partie `#` dans l'adresse).
- **Markdown** met en forme le message dans l'application web ntfy.
- **Vérification du certificat** : elle ne peut être désactivée que pour un serveur dont toutes les adresses sont privées (un ntfy auto-signé chez vous), jamais pour un hôte public.

Erreurs affichées par un test ou la carte du canal : *identifiants refusés* (401/403), *quota atteint* (le quota journalier de ntfy.sh est épuisé : auto-hébergez ntfy ou abonnez-vous ; Meshloom ne réessaie pas), *trop de requêtes* (le `Retry-After` de ntfy est respecté), *erreur de certificat*, *erreur du service* (erreur serveur ou message trop long).

ntfy.sh est un tiers, et conserve les messages 12 heures par défaut. Un canal ntfy.sh n'envoie donc que le texte des canaux Public et `#` (titre seul pour les messages privés et les canaux privés) tant que vous ne choisissez pas **Toujours**, et affiche une limite par défaut de 10 messages par minute, 30 par heure et **200 par jour**, sous les 250 par jour de l'offre gratuite (voir [ntfy.sh/pricing](https://ntfy.sh/pricing)). Meshloom les applique (voir [Limites et regroupement](#limites-et-regroupement)) : un canal très actif ne peut pas épuiser le quota de ntfy.sh, et une fois les 200 utilisés plus rien n’est envoyé au canal jusqu’au jour UTC suivant. Un serveur auto-hébergé n'a pas de limite journalière par défaut.

### Destinations e-mail et webhook

Dans **Réglages → Notifications → Canaux**, choisissez **Ajouter un canal → E-mail** et saisissez un serveur SMTP (hôte, port, chiffrement, utilisateur, mot de passe, expéditeur et destinataires), ou **Ajouter un canal → Webhook** et saisissez une adresse avec un secret HMAC facultatif. Les secrets ne sont plus affichés après l’enregistrement. Laissez un champ secret vide pour le conserver. L’adresse du webhook est elle aussi un secret, car elle contient souvent un jeton : une fois enregistrée, son champ ne montre que le schéma et l’hôte ; saisissez une nouvelle adresse pour la remplacer, ou appuyez sur **Effacer** pour la supprimer avec son secret HMAC. Le téléchargement de la sauvegarde JSON les laisse de côté (voir [Sécurité](/docs/deep/security/#secrets-des-intégrations-et-des-notifications)). Chaque destination a un bouton de test. Ce webhook sert aux notifications de Meshloom. Ce n’est pas le webhook de messages du fanout.

Meshloom refuse un webhook sur un réseau privé (votre réseau local, cette machine, `localhost`, le NAT d’opérateur, les adresses lien-local) sauf si vous cochez **Autoriser une destination sur un réseau privé (réseau local, localhost)** sous l’adresse : Home Assistant, Node-RED ou un script sur votre réseau en ont besoin. La vérification a lieu à l’enregistrement de l’adresse puis avant chaque envoi, et Meshloom se connecte ensuite uniquement à l’adresse vérifiée : une réponse DNS qui change entre-temps ne peut pas détourner l’appel. Les adresses de métadonnées cloud (`169.254.169.254` et ses équivalents) sont toujours refusées. Si le nom d’hôte est introuvable, saisissez plutôt l’adresse IP. Un webhook enregistré avant cette vérification continue de fonctionner : la case est cochée pour lui, et un avis unique le signale.

**Plusieurs destinataires et une langue (e-mail).** Une destination e-mail accepte plusieurs destinataires, un par ligne (les virgules marchent aussi), et un Cc facultatif (chaque adresse en Cc reçoit son propre message, une seule fois) ; 20 adresses au plus en tout, le reste est ignoré. Meshloom les confie au serveur de messagerie en une fois : si le serveur refuse un destinataire, toute la notification compte comme échouée, même si les autres l’ont peut-être reçue, et elle n’est pas renvoyée. Le message est rédigé dans la langue du canal (Automatique suit la langue des notifications). Quand le texte d’un message est retenu (le réglage qui garde le texte des messages hors des services tiers), le sujet est « Nouveau message » et le corps montre l’expéditeur et la conversation mais pas le texte.

**En-têtes du webhook.** Chaque appel porte `X-Webhook-Event`, `X-Webhook-Id` et `X-Webhook-Timestamp` (secondes depuis l’epoch de cette tentative), et `X-Webhook-Signature` (`sha256=` et le HMAC-SHA256 du corps) quand un secret est défini. L’identifiant est le même à chaque nouvelle tentative d’une notification : un récepteur comme Node-RED peut ignorer un identifiant déjà vu. La signature ne couvre que le corps, elle n’a donc pas changé. L’identifiant et l’horodatage ne sont pas signés : ils aident un récepteur à écarter une nouvelle tentative honnête, pas une requête rejouée (une version signée est prévue). Désactiver la vérification du certificat (pour un certificat auto-signé sur votre réseau) n’est accepté que pour une destination sur un réseau privé ; une adresse publique garde toujours sa vérification. Un identifiant et un mot de passe e-mail ne sont jamais envoyés sans chiffrement (mode **Aucun**) à un serveur de messagerie hors de votre réseau privé.

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

### Canaux Telegram, Gotify et Home Assistant

Ces trois canaux sont des **préréglages** : des formulaires prêts à l’emploi pour des services que Meshloom joint par Apprise. Vous remplissez des champs nommés, Meshloom construit lui-même l’adresse Apprise au moment de l’envoi et la vérifie comme toute autre destination. Les secrets (jeton du bot, jeton Gotify, jeton Home Assistant) sont en écriture seule : ils ne sont jamais réaffichés, et Meshloom ne conserve jamais l’adresse générée.

**Telegram** (un tiers : le texte des messages privés et des salons privés reste chez vous par défaut). Créez un bot avec `@BotFather` (envoyez `/newbot`) et copiez son jeton (`123456789:AAH…`). Trouvez l’**identifiant de conversation** : écrivez à votre bot, ouvrez `https://api.telegram.org/bot<jeton>/getUpdates` dans un navigateur et lisez `chat` → `id` (ou demandez à `@userinfobot`) ; l’identifiant d’un groupe est négatif (`-100…`), un canal public peut être son `@nom`. Pour un groupe ou un canal, ajoutez d’abord le bot. L’**identifiant de sujet** ne sert que pour un groupe à sujets. L’identifiant de conversation est obligatoire : sans lui le canal est refusé. Limite par défaut : 15 messages par minute, 200 par heure ; quand Telegram répond *trop de requêtes*, Meshloom attend le délai demandé.

**Gotify** (votre propre serveur). Dans Gotify, créez une *application* et copiez son jeton. Saisissez l’adresse du serveur (`https://gotify.exemple.org`, un préfixe de chemin est conservé ; `..` et `%` y sont refusés) et le jeton ; la priorité s’applique à toutes les notifications. Un serveur sur votre réseau local demande de cocher **Autoriser une destination sur réseau privé**, et changer le serveur oblige à ressaisir le jeton. Limite par défaut : 60 messages par minute.

**Home Assistant** (l’application Companion de votre téléphone, ou tout service `notify`). Dans votre profil Home Assistant, créez un **jeton d’accès longue durée**. Saisissez l’adresse de Home Assistant (`http://homeassistant.local:8123` ; uniquement l’adresse, un chemin comme `/ha` est refusé), le jeton et le **service notify** : `mobile_app_votre_telephone` (le nom exact est dans Home Assistant, Outils de développement, Actions ; `notify.alertes` fonctionne aussi). Home Assistant est presque toujours sur votre réseau : cochez **Autoriser une destination sur réseau privé** ; l’erreur le dit quand c’est nécessaire. Limite par défaut : 60 messages par minute.

Chaque formulaire a un bouton **Tester** qui envoie un message avant tout enregistrement.

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

Le texte venu du mesh n’est pas authentifié : quiconque est à portée peut écrire n’importe quoi. Le moteur de rendu commun casse donc `@everyone`, `@here`, `@channel`, `@all`, `@room`, `<@`, `<!` et `<#` avec un caractère invisible dans toute notification envoyée par les canaux ntfy, Discord, Apprise et préréglages (Telegram, Gotify, Home Assistant), en texte brut comme en Markdown. En Markdown (Discord, ntfy avec Markdown activé, Apprise au format Markdown) il échappe aussi la ponctuation Markdown, de sorte qu’un message ne peut pas injecter un lien ou un titre. L’e-mail, Web Push et le webhook transportent le texte tel quel : aucun service de discussion n’y interprète une mention (l’e-mail est échappé en HTML). Le canal Discord envoie en plus `allowed_mentions: {"parse": []}` : il ne mentionne personne.

La liste des secrets est plus bas, dans [Sauvegardes](#sauvegardes). En bref : les secrets s’écrivent mais ne se relisent pas, l’API ne les renvoie jamais et aucune sauvegarde ne les contient pour les canaux (la sauvegarde complète contient en revanche la clé privée Web Push et les abonnements, voir [Sauvegardes](#sauvegardes)).

## Destinations sur votre réseau

Par défaut, Meshloom refuse d’envoyer une notification vers une adresse de votre réseau local, vers cette machine (boucle locale, `localhost` compris) ou vers une adresse de métadonnées cloud. Ainsi, un réglage modifié ne peut pas transformer le serveur en sonde de votre réseau. Un serveur Gotify, ntfy, Home Assistant, Node-RED ou de messagerie chez vous, ou sur la même machine que Meshloom, est donc d’abord refusé, avec le message *cette adresse est sur un réseau privé*.

Si la destination est la vôtre, cochez **Autoriser une destination sur réseau privé** dans le formulaire du canal (décoché pour chaque nouveau canal). Cela couvre aussi la boucle locale. L’adresse est vérifiée à l’enregistrement du canal et de nouveau avant chaque envoi, et Meshloom ne se connecte qu’à l’adresse vérifiée. Les adresses de métadonnées cloud (`169.254.169.254` et équivalents) restent refusées même alors. Si un nom d’hôte est introuvable, saisissez plutôt son adresse IP. Désactiver la vérification du certificat n’est accepté que pour une destination dont toutes les adresses sont privées. Les services push des navigateurs ont un interrupteur à part, voir [Services push sur un réseau privé](/docs/deep/push/#services-push-sur-un-réseau-privé).

Les destinations que vous aviez avant l’existence de cette vérification (4.18.x) restent autorisées là où elles sont : la mise à jour a coché la case pour elles.

## Le tableau des règles

**Réglages → Notifications → Notifications par défaut** est un tableau : une ligne par événement, regroupée (Messages, Nœuds, Radio, Télémétrie, Meshloom), une colonne par canal (Web Push et les canaux ajoutés ; un canal désactivé est grisé mais garde ses coches) et une colonne **Toujours livrer** qui laisse passer un événement malgré la pause. Cochez une case pour envoyer cet événement à ce canal. La première colonne reste visible quand le tableau défile sur un téléphone. Chaque case a une étiquette : le tableau se parcourt au clavier et au lecteur d’écran. **Nouveau nœud vu** n’a pas de case propre, seulement une sous-ligne par type de nœud (compagnons, répéteurs, capteurs). **Canal modifié** va toujours vers Web Push (verrouillé). **Mentions** a une case **Mots entiers uniquement**. Web Push est activé pour les événements usuels au départ.

Un nouveau canal est rempli pour vous par la dernière étape du dialogue d’ajout, **Que recevoir ici ?** : **Comme Push** (les événements que reçoit Web Push, par défaut), **Alertes seulement** ou **Tout**. Les événements urgents (radio hors ligne, alertes de télémétrie, canal modifié) sont toujours livrés, même pendant **Ne pas déranger**.

## Les événements

| Événement | Quand il se déclenche |
|-----------|-----------------------|
| Messages privés | Un message direct arrive, y compris les messages de serveurs de salon |
| Messages de canal | Un message arrive sur le salon Public ou sur un salon `#`. Un salon à clé privée reste muet sans exception qui l’active |
| Mentions | Un message de salon vous nomme, voir [Les mentions](#les-mentions) |
| Nouveau nœud vu | Un nœud est entendu pour la première fois, avec une sous-ligne par type : **Compagnons** (la radio d’une personne), **Répéteurs** et **Capteurs**. Seul compte un nouveau contact ajouté une fois le démarrage de la radio terminé : un nœud déjà connu, un nœud inconnu et un serveur de salon ne le déclenchent jamais |
| Canal trouvé | Meshloom trouve un nouveau salon hashtag |
| Alerte de télémétrie | Un répéteur ou un contact suivi franchit un seuil d’alerte. Les seuils et les canaux qui les reçoivent (une pastille par canal) se règlent sur la page **Alertes** |
| Mise à jour disponible | Une nouvelle version de Meshloom est disponible |
| Radio hors ligne | La radio est injoignable depuis plus longtemps que le délai réglé sous le tableau (60 s par défaut). Envoyé comme urgent (le sujet de l’e-mail commence par `[URGENT]`), une fois par panne |
| Radio de retour | La radio est de nouveau joignable après une notification **Radio hors ligne**, avec la durée de la panne |
| Meshloom démarré | Meshloom vient de démarrer : sa version, si la radio s’est connectée et en combien de temps. Activé par défaut pour le push |
| Canal modifié | Un canal de notification est créé, supprimé, activé ou désactivé, ou est modifié dans ses réglages (nouvelle adresse, nouveau serveur ou webhook, autres options ; pas un renommage ni un nouveau mot de passe). Il va toujours vers Web Push et ne peut pas en être retiré ; les autres canaux suivent le tableau, et le canal qui a changé n’en entend jamais parler |
| Destination modifiée | Une destination e-mail, webhook ou Apprise est changée par les réglages 4.18.x. Web Push seulement |

**Radio hors ligne** et **Radio de retour** ne se déclenchent jamais pour un redémarrage de la radio lancé par Meshloom (correction de l’horloge, changement Wi-Fi) ni tant que la connexion est en pause dans **Réglages → Radio** ; une pause termine une panne sans notification. Rien n’est envoyé avant que la radio se soit connectée une fois depuis le démarrage de Meshloom, ni pendant le même délai juste après un démarrage : une mise à jour ou un redémarrage ne déclenche pas de fausse alerte. Le délai se règle sous la ligne **Radio hors ligne** du tableau (`radio_offline_grace_s` dans `/api/settings` ; 0 notifie aussitôt, jusqu’à 3600 s). La radio est vérifiée environ toutes les 5 secondes, et une tentative de reconnexion en cours peut retarder la vérification : la notification peut arriver un peu après le délai. Si Meshloom redémarre pendant une panne, la durée compte toujours depuis le vrai début.

**Radio hors ligne ne peut pas vous prévenir quand Meshloom lui-même est arrêté** (coupure de courant, redémarrage de l’hôte) : plus rien ne tourne pour l’envoyer. **Meshloom a démarré** rend visible un redémarrage inattendu. Pour être prévenu pendant l’arrêt, utilisez le `binary_sensor.<radio>_connected` de Home Assistant avec un délai « pendant » (voir `README_HA.md`) ; un battement de cœur périodique est prévu.

## Les mentions

Un message de canal **vous mentionne** quand il contient `@[` + le nom de votre radio + `]` (par exemple `@[Bob]`), quelle que soit la casse. Avec **Mots entiers uniquement** coché (ligne *Mentions* du tableau), le nom de votre radio écrit comme un mot entier compte aussi : *Al* correspond à « salut al ! » mais pas à « Alpha ». Une mention est envoyée aux canaux cochés sur la ligne **Mentions**, à la place de la notification ordinaire du canal sur ces canaux : un canal ne reçoit donc jamais les deux ; elle s’intitule « Mention dans #paris par Léa » et remplace la notification ordinaire de cette conversation dans la barre de notifications. Les canaux où **Mentions** n’est pas coché continuent de recevoir la notification ordinaire. La sourdine d’un canal, les listes de blocage et Ne pas déranger s’appliquent comme pour tout message, et une mention n’est **jamais urgente**. Elle est activée par défaut pour les canaux qui reçoivent les messages privés.

**N’importe qui à portée peut forger une mention :** les messages de canal ne sont pas authentifiés, donc quiconque écrit `@[votre nom]` fait « mentionner » votre radio. C’est pourquoi une mention n’est jamais urgente, et pourquoi, sur un **canal privé** (qui a sa propre clé), elle n’atteint que les canaux qui ne sont pas un tiers (Web Push, un ntfy auto-hébergé, un relais e-mail sur votre réseau…) ou ceux que vous avez explicitement autorisés pour cette conversation par une exception (**activé**) dans la liste des conversations ; une exception sur **activé** reçoit la mention même si le canal n’est pas coché sur la ligne **Mentions**. Les canaux Apprise et webhook, et les préréglages construits sur Apprise (Telegram, Gotify, Home Assistant), comptent toujours comme un tiers, car le type ne dit pas où ils envoient. Les messages privés ne sont pas des mentions.

## Conversations et exceptions

Sans exception, le tableau décide : un message direct suit la ligne **Messages privés**, le salon Public et les salons `#` (hashtag) suivent la ligne **Messages de canal**, et un salon à clé privée ne notifie personne. Une **exception** est un choix fait pour une conversation et un canal (*activé* ou *désactivé*) ; elle l’emporte sur le tableau, sauf face à la sourdine, aux listes de blocage et à une pause (voir plus bas). Les exceptions se font avec la cloche.

### La cloche, pour une conversation

Dans l’en-tête d’une conversation, la cloche (réservée aux administrateurs ; les autres rôles n’en ont pas) ouvre un menu avec une case par canal de notification activé, avec l’icône de son type, son nom et un point d’état (connecté, erreur, en pause, déconnecté). Une case montre si le canal notifie cette conversation, et le mot gris à côté dit pourquoi : *par défaut* (les lignes du tableau décident), *activé ici* ou *coupé ici* (une **exception** faite pour cette conversation seulement). Cocher une case que le défaut laisse déjà cochée, ou décocher une case que le défaut laisse vide, fait suivre à nouveau le défaut à la conversation et supprime l’exception. Si votre navigateur n’est pas encore abonné, activer **Web Push** l’abonne d’abord. La cloche est pleine tant qu’au moins un canal notifie la conversation. Elle montre la configuration : un salon en sourdine ou un Ne pas déranger actif laisse les cases cochées alors qu’aucune notification n’est envoyée.

Le même menu contient le raccourci **Ne pas déranger** (1 heure, 8 heures, jusqu’à demain 8:00, **Reprendre maintenant** : la même pause générale que dans les réglages) et un lien **Configurer les canaux de notification**. Dans la liste des conversations, le même menu est sous les trois points d’une ligne, **Notifications**.

Les exceptions sont listées dans **Réglages → Notifications → Exceptions par conversation**, une ligne par conversation avec un bouton par canal : cliquez pour passer de *hérite* (la règle de l’événement) à *oui* puis *non*. La croix supprime l’exception.

## Ne pas déranger

En haut des règles dans **Réglages → Notifications**, **Ne pas déranger** suspend toutes les notifications pendant **1 heure**, **8 heures** ou **jusqu’à demain 8 h** (heure de l’horloge du navigateur), et **Reprendre** y met fin. Le temps restant est affiché. Les événements cochés **Toujours livrer** (colonne toujours cochée et verrouillée pour les événements urgents : radio hors ligne, alertes de télémétrie et canal modifié) passent quand même. La pause est commune à tous les navigateurs et à tous les canaux ; elle est inscrite au journal d’audit. Une notification qui arrive pendant une pause n’est pas conservée : elle est abandonnée (le journal des envois la note *paused*) et n’est pas envoyée à la reprise.

## Sourdine et listes de blocage

Sur un salon, le bouton de sourdine fait taire tout pendant la durée choisie (de 15 minutes à 24 heures, ou indéfiniment). Il masque aussi le compteur de non lus. Il l’emporte sur les valeurs par défaut et les exceptions, et ce n’est pas le même contrôle que la cloche.

Les messages d’un contact ou d’un nom bloqué, et ceux d’un salon en sourdine (jusqu’à la fin de la sourdine), ne notifient sur aucun canal : Web Push, e-mail, webhook et Apprise. Seul un événement marqué **Toujours livrer** passe quand même. Rien ne change tant que vous ne bloquez personne et ne mettez aucun salon en sourdine.

## Langue

**Réglages → Notifications → Langue des notifications** fixe la langue de l’instance pour les notifications : **Automatique**, **Français** ou **English**. Automatique suit les navigateurs abonnés au push : anglais seulement si la plupart d’entre eux (une majorité stricte) sont en anglais, français en cas d’égalité. Chaque canal a aussi sa propre **Langue des notifications** dans son formulaire (Automatique reprend la langue de l’instance). Les notifications Web Push gardent toujours la langue du navigateur abonné, et le webhook envoie des données, pas du texte.

## Preuve de modification

Créer, supprimer, activer ou désactiver un canal, ou changer l’endroit où il envoie, écrit une ligne dans le journal d’audit et envoie la notification **Canal modifié** à chaque navigateur abonné et, par défaut, aux autres canaux activés, en disant ce qui a changé. La copie Web Push ne peut pas être désactivée : si ce n’est pas vous, quelqu’un d’autre l’a fait. Changer une destination 4.18.x par les anciens réglages envoie de la même façon **Destination de notification modifiée** à Web Push.

## Comment les notifications partent

Chaque canal a sa propre file et envoie une notification à la fois : un service lent ou en panne ne retarde jamais les autres, ni le push. Les envois sont espacés : au moins 10 secondes pour l’e-mail, 3 pour Telegram, 2 pour Discord et Apprise, 1 pour ntfy. Le nombre de notifications autorisé est fixé par les [limites](#limites-et-regroupement) du canal. Un envoi Web Push ou e-mail n’est jamais retenté, et Apprise et les préréglages retentent chaque URL eux-mêmes (0, 5 et 30 s) sans l’essai supplémentaire ci-dessous. Pour les autres types, un envoi qui échoue parce que le service est injoignable, trop lent, répond une erreur serveur ou demande de ralentir est retenté après 5 s puis 30 s (son `Retry-After` est respecté, jusqu’à 2 minutes), jamais quand il refuse la requête ; Web Push et l’e-mail ne sont pas retentés. Une notification de message qui échoue encore est abandonnée (elle arriverait trop tard) ; une radio hors ligne ou une alerte de télémétrie a droit à un essai de plus 5 minutes plus tard quand le service était injoignable, jamais après un refus. Les notifications encore en attente quand Meshloom s’arrête ne partent pas après le redémarrage.

L’état de chaque destination figure dans `/api/health`, sous `notification_statuses`, par identifiant (`builtin:push`, `legacy:email`, `legacy:webhook`, `legacy:apprise` et l’identifiant de chaque canal que vous ajoutez) : `status` (`connected`, `error`, `paused` ou `disconnected`), la dernière erreur sous forme de code, jamais un mot de passe ni un jeton (administrateurs seulement), et l’heure du dernier envoi réussi. Meshloom garde un court journal des notifications en échec, regroupées, abandonnées et annulées, sans leur texte ; les envois réussis sont seulement comptés.

Le test d’un canal Apprise envoie une notification de test à chaque URL ; un échec indique la ligne de l’URL et le type d’échec, jamais le jeton.

### Limites et regroupement

Chaque canal a des limites par minute, par heure et, pour certains, par jour. Les défauts viennent du type (Telegram 15 par minute et 200 par heure, Discord 30 et 600, e-mail 6 par minute, 60 par heure et 200 par jour, webhook 120 par minute, Web Push 60 par minute, ntfy.sh 10 par minute, 30 par heure et 200 par jour) ; un canal peut avoir les siennes. Rien n’est perdu en silence :

- **Une limite par minute ou par heure atteinte n’abandonne pas les notifications.** Elles attendent une minute, puis partent en **un seul résumé** du genre « +14 messages : 12 dans #paris, 2 MP ». Le résumé ne fait que compter : jamais le texte d’un message, un expéditeur ni le nom d’un canal privé (comptés « dans d’autres canaux »). Si la limite horaire est encore pleine, le résumé attend qu’il y ait de la place.
- **Les évènements urgents** (radio hors ligne, alerte de télémétrie) prennent un jeton comme les autres, mais passent une limite pleine une fois par évènement : un capteur qui oscille sans cesse ne contourne pas la limite, ses répétitions rejoignent le résumé.
- **Une limite par jour est un quota.** Quand l’allocation du jour est utilisée (comptée depuis minuit UTC et conservée d’un redémarrage à l’autre), plus rien n’est envoyé à ce canal jusqu’au jour UTC suivant : pas de résumé non plus, et chaque notification refusée est inscrite *quota dépassé* dans le journal des envois.
- **Plafond global.** Tous canaux confondus, Meshloom envoie au plus 600 notifications par heure (`limits.global_per_hour` dans les règles). Au-delà, rien n’est envoyé, urgents compris, chacune est inscrite *limitée* dans le journal, et `/api/health` indique `notifications_capped: true` jusqu’à ce que l’heure ait de la place.
- **Pas de répétition.** Un nouveau contact notifie une fois par jour, une panne de radio une fois, un message une fois, même après un redémarrage. Une alerte de télémétrie n’est limitée que par sa propre hystérésis.

## Tester un canal

Chaque canal a un bouton **Tester**, et le dialogue d’ajout teste **avant tout enregistrement** (**Envoyer un test et continuer**). Un test passe par l’expéditeur propre au canal et les mêmes vérifications qu’un vrai envoi, sans entrer dans la file, le journal ni les compteurs. Un échec s’affiche sous forme d’un code court, traduit, qui ne contient jamais une adresse, un jeton ni le texte d’erreur du réseau :

| Code | Signification |
|------|---------------|
| `invalid_config`, `config_required` | Un champ est faux ou vide ; le champ est signalé |
| `secret_required` | Vous avez changé l’endroit où le canal envoie, donc le secret enregistré n’est pas réutilisé : saisissez-le de nouveau |
| `egress_scheme`, `egress_private_address`, `egress_unresolvable`, `egress_host_not_allowed` | L’adresse est refusée : mauvais schéma, réseau privé non autorisé (voir [Destinations sur votre réseau](#destinations-sur-votre-réseau)), nom d’hôte inconnu (saisissez l’adresse IP), ou hôte que le type n’accepte pas |
| `verify_tls_requires_private_destination`, `smtp_plaintext_auth_refused`, `tls_error` | La vérification du certificat ne se désactive que pour une destination privée ; un identifiant de messagerie n’est jamais envoyé sans chiffrement hors de votre réseau ; la connexion sécurisée a échoué |
| `auth_failed` | Le service a refusé le jeton, le mot de passe ou la clé (HTTP 401 ou 403) |
| `rate_limited`, `quota_exceeded` | Le service demande de ralentir, ou le quota du jour est épuisé (ntfy.sh) |
| `timeout`, `service_error` | Pas de réponse à temps ; ou le service a répondu par une erreur |
| `apprise_url_invalid`, `apprise_chat_id_required` | Une adresse Apprise est refusée (la ligne est nommée) ou une adresse Telegram n’a pas son identifiant de chat |

Les tests sont limités : un toutes les 5 secondes par canal, un toutes les 2 secondes pour une configuration pas encore enregistrée, et 10 par minute en tout. Au-delà, la réponse est `429 rate_limited`. Le résultat d’un test sur un canal enregistré met aussi à jour son état sur la carte.

## Sauvegardes

La **sauvegarde complète** (Réglages → Base de données) contient une partie *Notifications* : chaque canal (type, nom, options, limites, langue, politique de texte) et les règles (événements, exceptions par conversation et options du tableau de règles), **sans aucun secret**. Les mots de passe, jetons, adresses Discord ou Apprise, sujets ntfy, URL de webhook et leur secret HMAC ne quittent jamais Meshloom. Les destinations e-mail, webhook et Apprise des anciens réglages sont elles aussi écrites sans leurs secrets. Les listes de blocage voyagent avec les réglages. Le journal des envois et les compteurs ne sont pas sauvegardés, ni la pause : *Ne pas déranger* n’est jamais restauré. Les clés et abonnements Web Push forment une autre partie, restaurée seulement sur un Meshloom sans abonnement propre.

**Restauration.** Un canal qui a perdu un secret revient **désactivé** et marqué *Secret manquant après une restauration* ; Meshloom n’envoie jamais avec un secret manquant. Ouvrez-le dans **Réglages → Notifications → Canaux**, saisissez de nouveau chaque secret (ou effacez-le exprès), puis activez-le. Si le même canal existe déjà ici avec la même destination, ses secrets enregistrés sont conservés. La restauration fusionne les canaux (un canal que le fichier ne nomme pas reste) et remplace les règles. Une sauvegarde faite par Meshloom 4.18 se restaure dans les mêmes canaux et règles (Web Push, e-mail, webhook, Apprise, les exceptions de chaque conversation).

Chiffrez quand même la sauvegarde complète avec une phrase secrète : elle contient encore la clé privée Web Push et les abonnements, les secrets de vos intégrations (MQTT, webhooks du fanout, SQS) et, si vous les incluez, les comptes. Voir [Sauvegarder la radio](/docs/backup/) et [Sécurité](/docs/deep/security/#secrets-des-intégrations-et-des-notifications).

## Configurer les canaux depuis un réseau de confiance

Un canal décide où vont vos messages. Qui peut en créer ou en modifier un peut envoyer une copie de vos notifications au service de son choix. C’est pourquoi toutes les routes sous `/api/notifications` demandent un **administrateur** : les autres rôles ne voient ni ne modifient aucun canal (un membre abonne seulement son propre navigateur au push). Ne donnez le rôle d’administrateur qu’aux personnes à qui vous confiez le nœud, connectez-vous en [HTTPS](/docs/deep/https/) hors d’un réseau de confiance, et gardez courte votre liste d’[Origines autorisées](/docs/deep/security/). Tout changement de destination d’un canal envoie une notification Web Push à chaque navigateur abonné (voir [Preuve de modification](#preuve-de-modification)).

Sur un Meshloom antérieur à la 5.0, il n’y avait aucune connexion : quiconque atteignait la page pouvait configurer un canal. Configurez les canaux depuis un réseau de confiance.

## Mise à jour et retour en arrière

**Depuis la 4.18.x.** Au premier démarrage, Meshloom transforme vos destinations en canaux : le canal intégré **Web Push**, et **`legacy:email`**, **`legacy:webhook`** et **`legacy:apprise`** pour celles que vous aviez configurées, et il transforme vos valeurs par défaut du push et vos exceptions par conversation en règles. Ce que vous recevez ne change pas. Ces trois canaux `legacy:` s’éditent toujours avec leur propre formulaire sur leur carte (celui de la 4.18.x, enregistré quand vous quittez un champ), et leur type n’est plus proposé dans **Ajouter un canal**. Vous pouvez les activer et les désactiver, les renommer et changer leurs règles comme tout canal, mais pas les supprimer par l’API des canaux ni y changer leur adresse ou leurs options : l’API répond **`409 legacy_managed`** à un `PATCH` de `config`, `allow_private_network` ou `verify_tls`, et à un `DELETE`, sur un canal `legacy:`. Utilisez plutôt **Supprimer cette destination** sur la carte. Un moyen jamais configuré (pas de serveur SMTP, pas d’URL) ne devient pas un canal. Une exception de conversation qui le concerne est conservée et s’applique dès que vous le configurez.

**Points d’accès dépréciés.** `GET`/`PATCH /api/push/preferences`, `PUT /api/push/preferences/conversations/{key}`, `notification_destinations` dans `PATCH /api/settings` et `POST /api/settings/notification-destinations/test` fonctionnent toujours comme en 4.18.x. Ils répondent un en-tête `Deprecation: @1792368000` (RFC 9745) et un `Link` vers le point d’accès des règles, et **seront retirés en 5.2.0**. Chaque changement qu’ils font est copié dans les règles, et inversement, pour les quatre moyens push, e-mail, webhook et Apprise ; les autres canaux leur sont invisibles. Demander e-mail, webhook ou Apprise sur une conversation quand cette destination n’est pas configurée répond `400 channel_missing`. Faites migrer vos scripts vers `/api/notifications`.

**Apprise dans le fanout.** La sortie Apprise du [fanout](/docs/deep/fanout/#apprise) n’est pas convertie : elle continue de fonctionner et de transmettre chaque message. Créez un canal Apprise (ou un préréglage) pour les notifications ; une conversion en un clic est prévue.

**Retour en arrière.** Revenir à une version antérieure à la 5.0 relit les anciens réglages. **Les canaux ajoutés après la mise à jour ne sont pas visibles après un retour en arrière**, les règles que vous avez changées dans le tableau ne sont pas lues, et un canal qui n’était pas une destination 4.18.x (ntfy, Discord, Telegram, Gotify, Home Assistant, un deuxième e-mail ou webhook) cesse de notifier. Notez les destinations dont vous avez besoin avant de revenir, et restaurez une sauvegarde faite avant la mise à jour si vous voulez retrouver exactement l’ancien état.

## Quand rien n’arrive

1. Regardez la carte du canal dans **Réglages → Notifications → Canaux** : le voyant d’état (fonctionne, erreur, en pause, désactivé) et la dernière erreur. Un canal restauré sans son secret est désactivé et indique *Secret manquant après une restauration*.
2. Appuyez sur **Tester** sur la carte. Voir [Tester un canal](#tester-un-canal) pour les codes. Un réseau privé non autorisé est refusé avant toute connexion.
3. Vérifiez le tableau des règles : l’événement doit être coché pour ce canal. Une sourdine, un blocage, un **Ne pas déranger** en cours et une exception de conversation l’emportent (voir [Sourdine et listes de blocage](#sourdine-et-listes-de-blocage)) ; les salons à clé privée restent muets sans exception qui les active.
4. Le canal est-il activé ? **Ne pas déranger** est-il actif ? Seuls les événements marqués **Toujours livrer** le traversent.
5. Pour Web Push, voir [Quand rien n’arrive](/docs/deep/push/#quand-rien-narrive) sur la page du push : HTTPS, autorisation du navigateur, Apple.
6. Regardez le journal du serveur et `GET /api/notifications/deliveries`, qui liste les échecs, les notifications abandonnées et celles ignorées pendant une pause, sans leur texte. Voir [Dépannage](/docs/deep/troubleshooting/).

## Points d’accès

Les administrateurs gèrent les canaux sous `/api/notifications` :


| Méthode | Point d’accès | Effet |
|---------|---------------|-------|
| GET | `/api/notifications/types` | Les types de canal : schéma JSON (les champs secrets sont `writeOnly`), limites par défaut |
| GET, POST | `/api/notifications/channels` | Liste et crée des canaux (les secrets s’affichent `********`) |
| POST | `/api/notifications/channels/test` | Teste une configuration avant de l’enregistrer (rien n’est stocké) |
| GET, PATCH, DELETE | `/api/notifications/channels/{id}` | Lit, modifie (le type ne peut pas changer) et supprime un canal ; `builtin:push` ne peut pas être supprimé (`403 builtin_channel`) |
| POST | `/api/notifications/channels/{id}/test` | Envoie un test ; un par 5 secondes et par canal, puis `429 rate_limited` |
| GET, PATCH | `/api/notifications/rules` | Canaux par événement, `always_deliver`, pause globale (`paused_until`), délai de radio hors ligne |
| PUT | `/api/notifications/rules/conversations/{key}` | Canaux d’une conversation (`{"channels": {"id": true}}`, ou `null` pour effacer) |
| GET | `/api/notifications/deliveries` | Le journal des envois (échecs et suppressions, jamais le texte) et les compteurs horaires |
| GET | `/api/notifications/events` | Le catalogue des événements |


Pour conserver un secret en modifiant un canal, omettez-le, envoyez `null` ou envoyez `********` ; `""` l’efface. Les erreurs sont `{"code": ..., "params": ...}` et ne contiennent jamais un hôte, une adresse ou un texte d’exception. Créer, supprimer, activer ou désactiver un canal, ou changer sa destination, notifie le Web Push (cela ne peut pas se désactiver) et les autres canaux actifs, et s’inscrit dans le journal d’audit.
