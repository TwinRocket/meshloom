---
title: HTTPS
description: TLS local, contexte sécurisé, et Meshloom derrière un reverse-proxy.
level: deep
order: 13
---

Meshloom fonctionne en HTTP simple, et pour un accès local ça suffit. Trois fonctionnalités changent la donne et réclament HTTPS.

## Ce qui exige HTTPS

Le navigateur réserve certaines API à ce qu’il appelle un contexte sécurisé (*secure context*) : une page servie en HTTPS, ou servie depuis `localhost`. Une page servie en HTTP simple sur une adresse IP du réseau local n’est pas un contexte sécurisé.

Conséquences concrètes :

- La recherche de clés de salon par WebGPU ne fonctionne pas hors `localhost` sans HTTPS.
- Les [notifications push](/docs/deep/push/) exigent HTTPS. Le service worker n’est enregistré que sur un contexte sécurisé.
- L’authentification HTTP Basic optionnelle envoie les identifiants en clair sans TLS. Elle n’a de sens que derrière HTTPS. Voir [Sécurité](/docs/deep/security/).

Un certificat auto-signé fait l’affaire dans les trois cas.

## Certificat local et uvicorn

Générez la paire, puis lancez le backend avec les deux fichiers :

```bash
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes -subj '/CN=localhost'
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --ssl-keyfile=key.pem --ssl-certfile=cert.pem
```

Le navigateur affichera un avertissement, puisque le certificat n’est signé par personne de connu. Il faut l’accepter une fois. Pour éviter l’avertissement, [mkcert](https://github.com/FiloSottile/mkcert) génère des certificats localement approuvés.

## Docker Compose

Générez le certificat sur l’hôte, montez-le dans le conteneur, et remplacez la commande de lancement :

```yaml
services:
  meshloom:
    volumes:
      - ./data:/app/data
      - ./cert.pem:/app/cert.pem:ro
      - ./key.pem:/app/key.pem:ro
    command: uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --ssl-keyfile=/app/key.pem --ssl-certfile=/app/cert.pem
```

Les deux montages sont en lecture seule. Le `command` écrase celui de l’image, donc il doit contenir l’intégralité de la ligne, hôte et port compris.

## Reverse-proxy sur un sous-chemin

Meshloom accepte d’être servi sous un préfixe, par exemple `/meshcore/`, y compris via l’ingress Home Assistant. Tous les chemins d’assets frontend et d’API sont relatifs, donc ils se résolvent correctement sous n’importe quel préfixe.

Une exigence côté proxy.

**La barre oblique finale.** L’URL du sous-chemin doit en avoir une. Si un visiteur atteint `/meshcore` sans barre finale, les chemins relatifs cassent. La plupart des proxys s’en occupent seuls ; pour Nginx, un bloc `location /meshcore/ { ... }` — avec la barre — fait ce qu’il faut.

**Pas d’en-tête `X-Forwarded-*` requis.** Le manifeste web est lui aussi relatif (`start_url` et `scope` valent `./`), donc l’installation en PWA fonctionne sous le préfixe sans `X-Forwarded-Prefix`.

## WebSocket

L’interface s’appuie sur un WebSocket permanent (`/api/ws`) pour les mises à jour temps réel : messages, ACK, paquets bruts, état radio. Un proxy qui ne relaie pas les en-têtes de mise à niveau de connexion coupe ce flux. Symptôme typique : l’interface s’affiche, l’historique se charge par REST, mais rien n’arrive en direct et une reconnexion est tentée toutes les trois secondes.

Le client renvoie un ping toutes les trente secondes. Un proxy qui coupe les connexions inactives plus tôt que ça provoquera des reconnexions en boucle.

Si l’authentification HTTP Basic est activée, elle s’applique aussi au point d’entrée WebSocket, pas seulement aux routes HTTP.

## Sur quel hôte servir

`--host 0.0.0.0` expose le serveur sur toutes les interfaces de la machine. C’est ce que veulent la plupart des installations, puisque l’intérêt est de consulter la radio depuis un téléphone ou un autre poste. C’est aussi ce qui rend les avertissements de la page [Sécurité](/docs/deep/security/) pertinents : il n’y a pas de comptes utilisateurs, et l’origine des requêtes n’est pas restreinte.

Si Meshloom ne doit être atteignable que localement, ne changez pas l’hôte par défaut, et vous gardez au passage le statut de contexte sécurisé de `localhost` sans TLS.
