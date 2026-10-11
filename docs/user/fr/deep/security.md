---
title: Sécurité
description: Ce que Meshloom ne protège pas, et les quelques verrous qui existent : accès Basic, désactivation des bots, export de clé.
level: deep
order: 20
---

Meshloom est conçu pour un réseau dont vous connaissez les utilisateurs. Plusieurs choix de conception n’ont de sens que sous cette hypothèse. Ces choix sont **délibérés**, ce ne sont pas des oublis.

## Ce qui n’existe pas

**Pas de comptes utilisateurs.** Il n’y a ni identifiant, ni session, ni rôle, ni permission par fonction. Quiconque atteint le serveur peut lire l’historique, envoyer des messages au nom de votre nœud, changer les réglages de la radio et modifier les bots.

**Aucune restriction sur l’origine des requêtes.** Le serveur accepte les requêtes venant de n’importe quelle page web (`allow_origins=["*"]`, avec `allow_credentials=True`). C’est pratique pour ouvrir l’interface depuis n’importe quel appareil, mais une page web ouverte dans le même navigateur peut aussi appeler l’API.

**Les bots exécutent du Python quelconque.** Un bot est un petit programme Python écrit dans l’interface. Meshloom l’exécute tel quel, avec un accès complet au langage (`exec()` avec tous les `__builtins__`). Quiconque atteint Meshloom peut donc faire exécuter du code sur la machine hôte. C’est voulu.

Rien de tout cela n’affaiblit le chiffrement propre à MeshCore : les messages directs, les clés de canal et la clé privée du nœud restent protégés. Ces points décrivent qui peut utiliser l’application, pas ce qui circule par radio.

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

La clé privée de la radio est lue une fois au démarrage et gardée dans la mémoire du serveur uniquement. Meshloom ne l’écrit jamais sur le disque.

La relire par l’API est désactivé par défaut :

```text
MESHCORE_ENABLE_LOCAL_PRIVATE_KEY_EXPORT=false
```

À `true`, `GET /api/radio/private-key` renvoie la clé en hexadécimal, et l’export de configuration de **Réglages > Radio** peut l’inclure. N’activez cela que sur un réseau de confiance, uniquement pour une sauvegarde ou un déménagement vers une autre machine, puis désactivez-le. L’import d’une clé (**Définir la clé privée**, en écriture seule) reste toujours disponible et n’affiche jamais la clé. Une clé qui ne correspond pas à la radio à laquelle la base est liée est refusée tant que vous n’avez pas confirmé le changement d’identité (voir [Premier lancement](/docs/first-run/)).

## Proxy radio

**Réglages > Proxy** permet à Meshloom de se comporter comme une radio sur le réseau, pour qu’une application mobile ou un second Meshloom utilise votre radio à travers lui. Il est désactivé par défaut. Le protocole qu’il parle n’a aucune authentification, et l’accès Basic ne le couvre pas. Ne l’activez que sur un réseau de confiance.

## Instantané de support

**Réglages > À propos > Ouvrir l’instantané de support de débogage** (`/api/debug`) est fait pour être collé dans un rapport de bug. Hors des logs, il contient versions, réglages et compteurs, jamais la clé privée ; les clés des contacts et des canaux n’apparaissent que sous forme d’empreintes à sens unique. Les logs récents peuvent contenir des noms ou des clés de canal, mais jamais la clé privée. Arrêtez la copie à la ligne `STOP COPYING HERE` pour laisser les logs de côté.

## Ce qui sort de votre machine

Ce que chaque sortie envoie dépend de la portée que vous lui donnez dans **Réglages > MQTT et automatisation**.

- **MQTT Community** ne transporte que des paquets radio bruts, jamais le texte lisible des conversations.
- **MQTT privé, webhooks, Apprise et SQS** peuvent transporter le texte complet des messages, selon les canaux et contacts sélectionnés.
- **Envoi vers la carte** transmet la position des répéteurs et des serveurs de salon à un service de carte (map.meshcore.io ou un autre que vous choisissez).
- **MQTT Home Assistant** publie vos appareils vers le broker que vous configurez.

Certaines fonctions ont besoin d’Internet : les [notifications push](/docs/deep/push/) et, une fois rejoint, [Meshloom Community](/docs/deep/community/) (publication de paquets bruts, annuaire, partage des noms de canaux hashtag). Même sans les deux, Meshloom vérifie les nouvelles versions toutes les cinq minutes (GitHub d’abord, puis le miroir Community). Il continue de fonctionner si cette vérification échoue.

## Une posture raisonnable

- Gardez Meshloom sur un réseau dont vous connaissez les utilisateurs.
- Ne l’exposez jamais directement à Internet. Pour un accès à distance, utilisez un VPN ou un tunnel qui ajoute sa propre authentification.
- Définissez `MESHCORE_DISABLE_BOTS=true` si vous n’avez pas besoin de bots.
- Laissez l’export de la clé privée désactivé, sauf pendant une sauvegarde.
- Mettez toujours l’accès Basic derrière HTTPS.

La version courte est dans [Un réseau de confiance](/docs/trust/).
