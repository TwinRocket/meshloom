---
title: Sécurité
description: Ce que Meshloom ne protège pas, et les quelques verrous qui existent : accès Basic, désactivation des bots, sauvegarde chiffrée.
level: deep
order: 20
---

Meshloom est conçu pour un réseau dont vous connaissez les utilisateurs. Plusieurs choix de conception n’ont de sens que sous cette hypothèse. Ces choix sont **délibérés**, ce ne sont pas des oublis.

## Ce qui n’existe pas

**Pas de comptes utilisateurs.** Il n’y a ni identifiant, ni session, ni rôle, ni permission par fonction. Quiconque atteint le serveur peut lire l’historique, envoyer des messages au nom de votre nœud, changer les réglages de la radio et modifier les bots.

**Les autres pages web ne sont que partiellement tenues à l’écart.** L’interface fonctionne depuis n’importe quel appareil qui ouvre l’adresse de Meshloom. Un autre site ouvert dans le même navigateur est une autre *origine* : le serveur ne lui accorde aucun accès CORS (même origine uniquement), il ne peut donc ni lire les réponses de l’API ni lui envoyer de requêtes JSON. Il peut encore déclencher les requêtes qu’un navigateur envoie sans demander (un `POST` sans corps, comme un redémarrage) sans en voir la réponse, et ouvrir le WebSocket en direct, qui ne vérifie pas encore l’origine. Si vous utilisez un tableau de bord ou un script servi depuis une autre adresse, ajoutez cette adresse dans **Réglages > Configuration locale > Origines autorisées** (une `schéma://hôte[:port]` par ligne, vide par défaut, sans `*`). Chaque site listé peut faire tout ce que vous pouvez faire, avec vos identifiants.

La connexion en direct (`/api/ws`, le WebSocket) fait exception : le serveur la refuse quand un navigateur l’ouvre depuis une page d’un autre site, si bien qu’une telle page ne peut pas écouter vos messages et paquets à leur arrivée. Son `Origin` doit correspondre à l’adresse demandée par le navigateur (`Host`, ou `X-Forwarded-Host` derrière un proxy qui réécrit `Host`) ; les clients qui n’envoient pas d’`Origin`, comme les scripts, sont acceptés. Voir [HTTPS](/docs/deep/https/#websocket).

**Les bots exécutent du Python quelconque.** Un bot est un petit programme Python écrit dans l’interface. Meshloom l’exécute tel quel, avec un accès complet au langage (`exec()` avec tous les `__builtins__`). Quiconque atteint Meshloom peut donc faire exécuter du code sur la machine hôte. C’est voulu.

Rien de tout cela n’affaiblit le chiffrement propre à MeshCore par radio : les messages directs et les messages de canal restent chiffrés comme d’habitude. Ces points décrivent qui peut utiliser l’application, pas ce qui circule par radio. La clé privée du nœud fait exception côté serveur : quiconque atteint l’interface peut la lire (voir [La clé privée du nœud](#la-clé-privée-du-nœud)).

## La fenêtre d’avertissement

Tant que les bots sont activés et qu’aucun accès Basic n’est défini, Meshloom affiche une fenêtre d’avertissement plein écran, intitulée « L’exécution non protégée de bots est activée ». Elle propose deux issues :

- **Désactiver les bots jusqu’au redémarrage du serveur** coupe le système de bots tout de suite. Il revient au prochain redémarrage, sauf si vous le désactivez définitivement (voir plus bas).
- Cochez la case de reconnaissance, puis **Ne plus m’avertir sur cet appareil**. Ce choix est retenu par ce navigateur seulement : un autre navigateur ou un téléphone verra de nouveau l’avertissement.

## Authentification HTTP Basic

```text
MESHCORE_BASIC_AUTH_USERNAME=...
MESHCORE_BASIC_AUTH_PASSWORD=...
```

Définissez les deux ou aucune : le serveur refuse de démarrer avec une seule. L’accès Basic protège les pages, l’API et la connexion en direct (WebSocket). C’est un identifiant partagé unique, sans rôles. Sans HTTPS, le mot de passe circule en clair : associez-le à [HTTPS](/docs/deep/https/).

## Désactiver les bots

`MESHCORE_DISABLE_BOTS=true` coupe le système de bots au démarrage. Aucun bot ne tourne, le serveur répond `403` à toute tentative de modifier les bots, et l’interface présente la fonction comme indisponible. Le paquet Linux le définit déjà dans `/etc/meshloom/meshloom.env`.

Le bouton **Désactiver les bots jusqu’au redémarrage du serveur** de la fenêtre d’avertissement fait la même chose temporairement, sans toucher à l’environnement. Les bots et les autres sorties sont décrits dans [Fanout](/docs/deep/fanout/).

## La clé privée du nœud

Meshloom lit la clé privée de la radio à chaque connexion à la radio, si le firmware de la radio le permet, et la garde dans la mémoire du serveur uniquement. Meshloom ne l’écrit jamais dans sa base. Si le firmware refuse, Meshloom fonctionne sans elle, mais ne peut plus déchiffrer les messages directs sur le serveur, et une sauvegarde ne contient alors aucune identité.

La clé peut toujours être relue par quiconque atteint l’interface : `GET /api/radio/private-key` la renvoie en hexadécimal, et l’export de configuration de **Réglages > Radio** comme la sauvegarde complète de **Réglages > Base de données** la contiennent (voir [Sauvegarder la radio](/docs/backup/)). Aucun réglage ne permet de le désactiver. Un autre site ouvert dans le même navigateur ne peut plus les lire (voir [Ce qui n’existe pas](#ce-qui-nexiste-pas)), sauf si vous l’avez ajouté aux **Origines autorisées**. Gardez Meshloom sur un réseau de confiance, et traitez le fichier de sauvegarde comme la clé elle-même : chiffrez-le avec une phrase secrète dès que vous le conservez ailleurs que sur ce réseau.

L’import d’une clé (**Définir la clé privée**, en écriture seule) n’affiche jamais la clé. Une clé qui ne correspond pas à la radio à laquelle la base est liée est refusée tant que vous n’avez pas confirmé le changement d’identité (voir [Premier lancement](/docs/first-run/)).

Sous Linux, le serveur se marque aussi comme non « dumpable » au démarrage : un autre programme lancé par le même utilisateur ne peut pas lire sa mémoire ni son environnement (où se trouve l’identifiant d’accès Basic). En contrepartie, le serveur n’écrit aucun core dump, et les outils de débogage comme `py-spy` ou `gdb` demandent `sudo` pour s’y attacher. Cela protège contre les autres programmes non root et ne fait rien contre root ; l’image Docker par défaut et l’add-on Home Assistant tournent en root, donc la protection compte surtout pour les paquets deb/rpm et les installations qui tournent sous un utilisateur dédié.

## Proxy radio

**Réglages > Proxy** permet à Meshloom de se comporter comme une radio sur le réseau, pour qu’une application mobile ou un second Meshloom utilise votre radio à travers lui. Il est désactivé par défaut. Le protocole qu’il parle n’a aucune authentification, et l’accès Basic ne le couvre pas. Ne l’activez que sur un réseau de confiance.

## Mot de passe Wi-Fi

Meshloom n’enregistre jamais le mot de passe Wi-Fi de la radio, ni dans sa base de données ni dans ses journaux, à aucun niveau de journalisation. Pour savoir si des identifiants sont enregistrés, il relit le mot de passe dans la radio par le lien compagnon, puis l’oublie : après chaque connexion, à chaque nouvelle tentative de cette vérification, et lors d’un **Réessayer** manuel tant qu’aucune vérification n’a abouti. Enregistrer des identifiants envoie le mot de passe à la radio et le relit deux fois (une fois avant, une fois pour vérifier). En transport TCP, le lien compagnon n’est pas chiffré : le mot de passe traverse votre réseau local en clair chaque fois que Meshloom le relit ou l’enregistre, et quiconque peut observer ce réseau peut le lire. En USB ou en Bluetooth, il ne traverse pas votre réseau. Voir [Radio, contacts et canaux](/docs/deep/radio/#wi-fi).

## Secrets des intégrations et des notifications

Une fois enregistrés, les secrets de vos intégrations et destinations de notification ne sont plus jamais affichés : les adresses Apprise, les secrets HMAC des webhooks, les mots de passe MQTT, la clé secrète et le jeton de session Amazon SQS, et le mot de passe SMTP. L’API, l’instantané de support et l’état des intégrations affichent `********` à la place, et la liste Apprise ne montre que le service et l’hôte de chaque adresse. L’erreur d’une intégration ne cite jamais son adresse Apprise. Le téléchargement de la sauvegarde JSON (`GET /api/settings/backup/json`) laisse de côté le mot de passe SMTP et le secret du webhook de notification ; sa restauration conserve ceux déjà enregistrés. La sauvegarde complète de **Réglages > Base de données** les contient toujours, avec les intégrations : chiffrez-la avec une phrase de passe.

## Instantané de support

**Réglages > À propos > Ouvrir l’instantané de support de débogage** (`/api/debug`) est fait pour être collé dans un rapport de bug. Hors des logs, il contient versions, réglages et compteurs, jamais la clé privée ; les clés des contacts et des canaux n’apparaissent que sous forme d’empreintes à sens unique. Les logs ne contiennent jamais la clé privée ni un mot de passe Wi-Fi, à aucun niveau de log, `DEBUG` compris. Les logs peuvent contenir des noms et des clés de canal à tous les niveaux ; en `DEBUG`, ils peuvent aussi contenir les mots de passe de connexion aux répéteurs et aux salons, et le code PIN Bluetooth. Arrêtez la copie à la ligne `STOP COPYING HERE` pour laisser les logs de côté.

## Ce qui sort de votre machine

Ce que chaque sortie envoie dépend de la portée que vous lui donnez dans **Réglages > MQTT et automatisation**.

- **Partage communautaire** ne transporte vers un serveur MQTT communautaire que des paquets radio bruts, jamais le texte lisible des conversations.
- **MQTT privé**, **Webhook**, **Apprise** et **Amazon SQS** peuvent transporter le texte complet des messages, selon les canaux et contacts sélectionnés.
- **Envoi vers la carte** transmet les annonces des répéteurs et des serveurs de salon, avec leur position, à un service de carte (map.meshcore.io ou un autre que vous choisissez).
- **Home Assistant** publie vos appareils vers le serveur MQTT (broker) que vous configurez.

Le webhook de notification, le Web Push et les sorties fanout Webhook, Apprise, Amazon SQS et Envoi vers la carte ne joignent que des destinations sur Internet, sauf si vous autorisez un réseau privé pour eux (voir [Notifications](/fr/docs/deep/push/) et [Fanout](/fr/docs/deep/fanout/)) : une page qui modifie ces réglages ne peut pas faire sonder votre réseau par le serveur. Meshloom ne suit jamais de redirection pour eux et refuse toujours les adresses de métadonnées cloud. Les webhooks, le Web Push et l’envoi vers la carte se connectent uniquement à l’adresse vérifiée. Apprise et Amazon SQS se connectent eux-mêmes après la vérification : quelqu’un qui contrôle le DNS d’un nom d’hôte que vous avez saisi pourrait encore le faire pointer ailleurs ; Apprise n’accepte que les services listés dans [Fanout](/fr/docs/deep/fanout/#apprise).

Certaines fonctions ont besoin d’Internet : les [notifications push](/docs/deep/push/) et, une fois rejoint, [Meshloom Community](/docs/deep/community/) (publication de paquets bruts, annuaire, partage des noms de canaux hashtag). Même sans les deux, Meshloom vérifie les nouvelles versions sur GitHub une fois toutes les 6 heures (avec Community activé : toutes les cinq minutes, avec le miroir Community en secours). Il continue de fonctionner si cette vérification échoue. Quand vous quittez Community, Meshloom lui envoie une dernière requête pour enregistrer la désinscription et supprimer la position de votre radio. Ensuite, Community n’est plus contacté que s’il n’a sûrement pas reçu cette requête et que l’état de votre nœud n’a pas pu être effacé du broker à ce moment-là (une reconnexion unique l’efface, en quelques essais au plus sur 24 heures, puis plus rien), ou quand vous appuyez sur **Réessayer** parce que Community n’a pas confirmé la désinscription (voir [Meshloom Community](/docs/deep/community/#quitter-community)). Les e-mails d’alerte contiennent le logo de Meshloom : en ouvrir un ne charge rien depuis Internet.

## Une posture raisonnable

- Gardez Meshloom sur un réseau dont vous connaissez les utilisateurs.
- Ne l’exposez jamais directement à Internet. Pour un accès à distance, utilisez un VPN ou un tunnel qui ajoute sa propre authentification.
- Définissez `MESHCORE_DISABLE_BOTS=true` si vous n’avez pas besoin de bots.
- Chiffrez une sauvegarde complète avec une phrase secrète quand vous la rangez hors de ce réseau.
- Mettez toujours l’accès Basic derrière HTTPS.

La version courte est dans [Un réseau de confiance](/docs/trust/).
