---
title: Dépannage
description: Logs DEBUG, /api/debug, et les cas qui reviennent le plus souvent.
level: deep
order: 19
---

Deux outils avant tout le reste : les logs en niveau `DEBUG`, et l’instantané de support.

## Logs DEBUG

Si vous rencontrez un problème ou ouvrez un rapport de bug, démarrez le backend en `DEBUG`. Le mode debug détaille la communication radio, le traitement des paquets et les opérations internes, ce qui change tout pour diagnostiquer.

```bash
MESHCORE_LOG_LEVEL=DEBUG uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Pour une installation par paquet, la variable se met dans `/etc/meshloom/meshloom.env`, puis `sudo systemctl restart meshloom`. En Docker, dans le bloc `environment:` du Compose.

## L’instantané de support

`/api/debug` renvoie un bloc de diagnostic. Le même est accessible par **Réglages > À propos > « Open debug support snapshot »**, en bas de la section.

Il contient l’environnement d’exécution, les attentes sur les clés et les salons, l’état de la radio, un audit de dérive entre contacts/salons de l’application et de la radio, la version et les informations git, et les logs récents.

Ce qu’il révèle, précisément : les informations hors logs ne divulguent aucune clé, aucun nom de salon, ni aucune autre information privilégiée, au-delà des noms de vos bots. **Les logs, en revanche, peuvent contenir des noms de salons ou des clés de salon** — jamais votre clé privée. Si vous ne souhaitez pas les partager, copiez seulement jusqu’au marqueur `STOP COPYING HERE` dans le corps du bloc.

## `ModuleNotFoundError: No module named 'meshcore'`

Cause unique : uvicorn a tourné avec le Python système au lieu du virtualenv du projet. Trois vérifications, depuis la racine du dépôt :

- Lancer `uv sync`. C’est obligatoire avant tout `uv run ...` : c’est cette commande qui crée le `.venv` contenant les dépendances. N’essayez pas de les installer avec `apt` ou `dnf` — la plupart ne sont pas empaquetées, et elles n’ont rien à faire sur le Python système.
- Lancer `uv --version`. Si ça échoue, `uv` n’est pas installé, ou autre chose du même nom traîne dans le `PATH`. Installation : `curl -LsSf https://astral.sh/uv/install.sh | sh`, puis redémarrer le shell.
- Lancer `which uvicorn`. Si la réponse est `/usr/bin/uvicorn`, c’est un uvicorn système qui gagne. Passez toujours par `uv run uvicorn ...` depuis la racine du dépôt.

## Radio en pause, transport non configuré

La barre d’état reste en pause et **Connecter** ouvre **Réglages > Radio**. C’est normal sur une installation neuve : le transport n’est plus choisi à l’install. Configurez USB, TCP ou BLE dans cette page, puis appliquez. Voir [Premier lancement](/docs/first-run/) et [Transports radio](/docs/deep/transports/).

Les anciennes variables `MESHCORE_SERIAL_PORT`, `MESHCORE_TCP_HOST` et `MESHCORE_BLE_ADDRESS` ne sont plus lues au runtime. Une base déjà existante peut les importer une seule fois au premier démarrage après la mise à jour.

## Dialogue « radio non liée » ou « identité différente »

Après une mise à jour, une base qui avait déjà des contacts ou des messages demande une confirmation, même si c’est la même radio. **Lier sans effacer** conserve l’historique. **Nouvelle radio** l’efface. « Clé précédente : inconnue » signifie que le lien n’existait pas encore, pas qu’un autre appareil est branché.

Si deux clés différentes s’affichent, c’est un vrai changement de radio. Continuer sans effacer n’est pas proposé.

## Table de contacts pleine

Symptôme : un avertissement indiquant que l’acquittement automatique des DM ne fonctionnera peut-être pas pour tous les contacts.

Meshloom charge les favoris et les contacts récemment actifs sur la radio pour qu’elle puisse acquitter seule les messages directs entrants. Si la table est déjà pleine — remplie par des publicités reçues ou par un autre client — certains ajouts échouent.

Quatre sorties : vider la table avec un autre client MeshCore puis redémarrer Meshloom, baisser la cible de remplissage dans les paramètres radio, activer `MESHCORE_LOAD_WITH_AUTOEVICT=true`, ou ignorer l’avertissement. **L’envoi et la réception de messages ne sont jamais affectés.** Détails dans [Radio, contacts et salons](/docs/deep/radio/).

## Des messages restent sur la radio

Meshloom fait tourner un audit horaire qui vérifie si des messages sont restés sur la radio sans remonter par abonnement d’événement, et si ses attentes de slots de salon correspondent encore à la radio. Un écart produit une erreur dans l’interface et dans les logs.

Si vous voyez cet avertissement, ou si des messages n’arrivent jamais dans l’application, `MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK=true` transforme cet audit en sondage toutes les 10 secondes.

## Les envois de salon partent sur le mauvais slot

Si les envois semblent utiliser le mauvais slot de salon, ou si un autre client modifie les slots sous les pieds de Meshloom, `MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE=true` force la radio à valider le slot avant chaque envoi. Coût : environ 500 ms de délai par envoi.

## Contention du port série

Le port série n’est pas partageable. Si un autre processus le tient — autre client MeshCore, console série, deuxième instance — la connexion échoue en boucle.

Meshloom détecte le motif : les lignes répétées `Serial Connection started` de la bibliothèque `meshcore` sont regroupées et remontées en `WARNING`, avec la mention d’une possible contention par un autre processus. Cherchez alors qui d’autre tient la radio.

## L’installation post-connexion tourne en boucle

Si la séquence post-connexion échoue — par exemple parce que la radio ne répond pas à l’export de clé — le moniteur la retente toutes les cinq secondes, indéfiniment.

C’est intentionnel. La radio peut être en train de redémarrer, de sortir de veille, ou temporairement muette. L’installation se terminera d’elle-même quand elle redeviendra disponible, sans intervention.

Si la séquence traîne au-delà de son délai maximal, le backend journalise l’échec et pousse un message dans l’interface demandant de redémarrer la radio puis le serveur.

## Horloge radio bloquée dans le futur

Cas rare. Si la RTC du nœud est en avance sur l’heure système et qu’aucun mode rescue ni temps GPS n’est disponible, `__CLOWNTOWN_DO_CLOCK_WRAPAROUND=true` tente de forcer l’horloge à `0xFFFFFFFF`, d’attendre le débordement de l’entier 32 bits, puis de rejouer une synchronisation normale avant de retomber sur un redémarrage.

Le nom de la variable annonce la couleur. Ce comportement dépend de la carte, peut ne pas être sûr ni efficace sur toutes les cibles MeshCore, et doit être traité comme hautement expérimental.

## `Received empty packet: index out of range` sous Windows

Problème de démarrage intermittent observé sous Windows, accompagné d’un échec de synchronisation des contacts. La cause n’est pas identifiée. Il se résout généralement au redémarrage. C’est documenté comme tel dans le dépôt, avec une prime symbolique pour qui trouvera pourquoi.

## Erreur de parsing sur une publicité

Une trame RF malformée ou tronquée peut faire échouer le parsing d’une publicité côté bibliothèque `meshcore_py`, avec un `IndexError: index out of range` en provenance de `meshcore/reader.py`.

Ce n’est ni une corruption de base, ni un bug du stockage des messages. C’est un manque de durcissement du parseur en amont. L’effet pratique est l’échec d’une tâche pour ce paquet, les suivants continuant normalement.

## `/docs` n’est pas ce site

Une fois le serveur démarré, FastAPI sert une documentation d’API interactive sur `/docs`, sur le même hôte et le même port que l’interface — `http://localhost:8000/docs` pour un lancement local par défaut.

C’est du Swagger généré depuis le code, utile pour explorer les endpoints. Ce n’est pas la documentation utilisateur que vous lisez ici.

## L’interface ne s’affiche pas, l’API répond

En production, le backend sert le frontend compilé. Si `frontend/dist` est absent, il cherche `frontend/prebuilt`, présent dans l’archive zip de release. Si aucun des deux n’existe, le démarrage journalise une erreur explicite et continue à servir les routes API **sans** monter les routes statiques.

Depuis un checkout, la correction est de construire : `cd frontend && npm install && npm run build`.

## Rien n’arrive en temps réel

L’interface s’affiche, l’historique se charge, mais les nouveaux messages n’apparaissent pas sans rechargement. C’est le WebSocket `/api/ws` qui ne passe pas. Cause habituelle : un reverse-proxy qui ne relaie pas la mise à niveau de connexion, ou qui coupe les connexions inactives en moins de trente secondes. Voir [HTTPS](/docs/deep/https/).

## Rapporter un bug

Avec les logs en `DEBUG` et le bloc `/api/debug` — tronqué au marqueur si vous préférez — sur le [dépôt GitHub](https://github.com/TwinRocket/meshloom). Une discussion ou une issue avant une pull request est la voie attendue par le projet.
