---
title: Sécurité
description: Ce que Meshloom ne protège pas, et les quelques verrous qui existent vraiment.
level: deep
order: 20
---

Meshloom est conçu pour un réseau de confiance. Ce n’est pas une formule prudente : plusieurs choix d’architecture ne sont défendables que sous cette hypothèse. Cette page les nomme, puis décrit les verrous réellement disponibles.

Les décisions listées ci-dessous sont **délibérées**. Elles ne sont pas des oublis à corriger.

## Ce qui n’existe pas

**Pas de comptes utilisateurs.** Aucune session, aucun modèle d’autorisation, aucune permission par fonctionnalité. Quiconque atteint l’interface peut tout faire : lire l’historique, envoyer des messages, modifier la configuration radio, éditer des bots.

**Pas de restriction d’origine.** Le backend autorise toutes les origines (`allow_origins=["*"]`, avec `allow_credentials=True`). C’est ce qui permet de consulter sa radio depuis n’importe quel appareil du réseau local sans configuration. C’est aussi ce qui fait qu’une page web tierce ouverte dans le même navigateur peut parler à l’API.

**Exécution de code arbitraire par les bots.** Le système de bots exécute du Python fourni par l’utilisateur via `exec()`, avec l’ensemble des `__builtins__`. C’est assumé : les bots sont une fonctionnalité de puissance pour l’automatisation. La conséquence directe est que **toute personne sur le réseau peut exécuter du code arbitraire** sur la machine qui héberge Meshloom.

Ces trois points ne changent rien à la sécurité du mesh lui-même. Le chiffrement des messages MeshCore, les clés de salon et la clé privée du nœud fonctionnent comme prévu. C’est l’accès **à l’application** qui n’est pas cloisonné.

## Authentification HTTP Basic

Une porte grossière existe :

```text
MESHCORE_BASIC_AUTH_USERNAME=...
MESHCORE_BASIC_AUTH_PASSWORD=...
```

Les deux variables doivent être définies ensemble ; l’une sans l’autre provoque une erreur de validation au démarrage. L’authentification s’applique à toute l’application, y compris au point d’entrée WebSocket.

Deux limites à garder en tête. Ce n’est pas un modèle d’utilisateurs : c’est un couple d’identifiants unique, sans rôles ni périmètres. Et HTTP Basic transmet les identifiants en clair — il faut donc HTTPS, plus une posture réseau saine. Voir [HTTPS](/docs/deep/https/).

L’installeur Linux propose de configurer ces identifiants, et peut être rejoué plus tard pour les changer.

## Désactiver les bots

Deux niveaux, selon la durée souhaitée.

`MESHCORE_DISABLE_BOTS=true` désactive le système entièrement au démarrage. Aucune exécution de bot, `403` sur les modifications de configuration de bots, et un message d’indisponibilité dans l’interface. C’est le réglage à poser si l’instance n’a pas besoin de bots, et l’installeur Linux le propose.

`POST /api/fanout/bots/disable-until-restart` arrête les modules bots et les maintient désactivés jusqu’au redémarrage du processus. Utile pour couper court sans toucher à l’environnement ni redémarrer immédiatement.

Détails du modèle d’exécution dans [Fanout](/docs/deep/fanout/).

## Clé privée du nœud

À la connexion, Meshloom exporte la clé privée de la radio et la garde **en mémoire uniquement**. Elle n’est jamais écrite sur disque. C’est ce qui permet le déchiffrement des messages directs côté serveur, y compris pour des contacts absents de la mémoire de la radio.

L’export par l’API est **désactivé par défaut** :

```text
MESHCORE_ENABLE_LOCAL_PRIVATE_KEY_EXPORT=false
```

Mis à `true`, il active `GET /api/radio/private-key`, qui renvoie la clé en hexadécimal, pour une sauvegarde ou une migration. À n’activer que sur un réseau de confiance, et seulement quand vous avez besoin de récupérer la clé. Comme il n’y a pas de comptes utilisateurs, cet endpoint est aussi accessible que le reste de l’API.

L’**import** par `PUT /api/radio/private-key` est toujours disponible, quel que soit ce réglage. Cette asymétrie est volontaire : l’import est en écriture seule et n’expose aucun matériel de clé.

La fonction d’export/import de configuration radio de l’interface s’appuie sur ces deux endpoints. Quand l’export est désactivé, la configuration exportée omet la clé privée et affiche un avertissement.

## Instantané de débogage

Le bloc `/api/debug` est fait pour être collé dans un rapport de bug, donc il faut savoir ce qu’il contient.

Les informations hors logs ne divulguent aucune clé, aucun nom de salon, ni aucune autre information privilégiée, au-delà des **noms de vos bots**. Les logs récents inclus dans le bloc, en revanche, peuvent contenir des noms de salons ou des clés de salon. Ils ne contiennent jamais votre clé privée.

Pour partager sans les logs, copiez seulement jusqu’au marqueur `STOP COPYING HERE`.

## Ce que le fanout laisse sortir

Chaque intégration a une portée, et cette portée décide ce qui quitte la machine. Deux cas valent une vérification explicite.

Le MQTT **communautaire** est verrouillé sur les paquets bruts uniquement, sans messages décodés. Le contenu de vos conversations ne part pas par ce canal.

Le MQTT **privé**, les webhooks, Apprise et SQS peuvent en revanche transporter le texte intégral des messages, selon la portée choisie. Ce sont vos destinations, donc c’est à vous de savoir où elles pointent.

Une sortie Internet est exigée pour les [notifications push](/docs/deep/push/) et, une fois rejoint, pour [Meshloom Community](/docs/deep/community/) (publication de paquets bruts, annuaire, partage des noms hashtag). Même une install qui refuse les deux vérifie encore les mises à jour (GitHub, puis le miroir de versions de Community) toutes les cinq minutes ; elle fonctionne sans cet accès. Les payloads push transitent par le service du navigateur.

## Une posture raisonnable

Rien de tout cela n’a besoin d’être compliqué :

- Garder l’instance sur un réseau où vous connaissez tout le monde.
- Ne pas l’exposer directement sur Internet. Si un accès distant est nécessaire, passer par un VPN ou un tunnel, pas par une redirection de port.
- Mettre `MESHCORE_DISABLE_BOTS=true` si vous n’utilisez pas de bots.
- Laisser l’export de clé privée désactivé sauf le temps d’une sauvegarde.
- Si Basic auth est activée, la faire précéder de HTTPS.

Le raisonnement complet, en langage plus simple, est sur [Un réseau de confiance](/docs/trust/).
