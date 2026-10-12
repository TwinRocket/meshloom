---
title: HTTPS
description: TLS local, contexte sécurisé, et Meshloom derrière un reverse-proxy.
level: deep
order: 13
---

Meshloom fonctionne en HTTP simple, et pour un accès local cela suffit. Trois fonctionnalités demandent plus.

## Ce qui exige HTTPS

Les navigateurs réservent certaines fonctions à un **contexte sécurisé** : une page servie en HTTPS, ou depuis `localhost`. Une page servie en HTTP simple depuis une adresse du réseau local, comme `192.168.1.20`, n’est pas un contexte sécurisé.

Sur une telle page :

- La recherche par force brute du chercheur de canaux ne fonctionne pas, car elle a besoin de WebGPU. La page le signale.
- Les [notifications push](/docs/deep/push/) ne fonctionnent pas, car elles ont besoin d’un service worker, que les navigateurs n’autorisent qu’en contexte sécurisé. **Réglages → Notifications** indique que le push exige HTTPS.
- L’authentification HTTP Basic facultative enverrait le mot de passe en clair. Voir [Sécurité](/docs/deep/security/).

Un certificat auto-signé (que vous avez fabriqué vous-même) suffit à rendre la page sécurisée, ce qui suffit au chercheur de canaux. Pour le push, il faut en plus que votre navigateur accepte le certificat. Meshloom prévient que la livraison peut être peu fiable avec un certificat non approuvé, selon le navigateur. Un certificat que vos appareils approuvent, comme ceux de [mkcert](https://github.com/FiloSottile/mkcert), évite le problème.

## Certificat local et uvicorn

Uvicorn est le programme qui sert Meshloom. Fabriquez un certificat, puis lancez-le avec les deux fichiers :

```bash
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes -subj '/CN=localhost'
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --ssl-keyfile=key.pem --ssl-certfile=cert.pem
```

Le navigateur affiche un avertissement, puisque personne de connu n’a signé le certificat. Acceptez-le une fois. [mkcert](https://github.com/FiloSottile/mkcert) fabrique des certificats que vos propres appareils approuvent, ce qui supprime l’avertissement.

## Docker Compose

Fabriquez le certificat sur l’hôte, montez-le dans le conteneur et remplacez la commande de lancement :

```yaml
services:
  meshloom:
    volumes:
      - ./data:/app/data
      - ./cert.pem:/app/cert.pem:ro
      - ./key.pem:/app/key.pem:ro
    command: uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --ssl-keyfile=/app/key.pem --ssl-certfile=/app/cert.pem
```

Les montages du certificat sont en lecture seule. `command` remplace celle de l’image : elle doit donc contenir toute la ligne, hôte et port compris.

## Reverse-proxy sur un sous-chemin

Meshloom peut être servi sous un préfixe, par exemple `/meshcore/`, y compris via la barre latérale de Home Assistant. Les adresses des ressources, de l’API et du manifeste web sont toutes relatives : il n’y a rien d’autre à configurer, et aucun en-tête `X-Forwarded-*` n’est nécessaire.

Une seule exigence côté proxy : l’adresse doit **se terminer par une barre oblique**. Si un visiteur atteint `/meshcore` sans elle, les chemins relatifs cassent. La plupart des proxys s’en occupent ; avec Nginx, un bloc `location /meshcore/ { ... }`, barre comprise, fait ce qu’il faut.

## WebSocket

La page garde une connexion permanente avec le serveur, sur `/api/ws`, pour recevoir en direct les messages, les accusés de réception, les paquets et l’état de la radio. Un proxy qui ne relaie pas les en-têtes de changement de protocole la coupe. Symptôme typique : la page s’affiche, l’historique se charge, mais rien n’arrive en direct. Le navigateur réessaie en attendant un peu plus longtemps à chaque fois (d’une seconde jusqu’à trente).

La page envoie un ping au serveur toutes les trente secondes. Un proxy qui coupe les connexions inactives plus tôt que cela provoque des reconnexions en boucle.

Quand l’authentification HTTP Basic est activée, elle s’applique aussi à cette connexion, pas seulement aux pages.

## Sur quelle adresse écouter

`--host 0.0.0.0` rend le serveur joignable depuis tous les réseaux auxquels la machine est reliée. C’est ce que font le paquet Linux, l’installation Docker de l’installateur et l’image, parce que l’intérêt est d’utiliser Meshloom depuis un téléphone ou un autre ordinateur. C’est aussi ce qui rend pertinents les avertissements de [Sécurité](/docs/deep/security/) : il n’y a pas de comptes utilisateurs, et l’origine des requêtes n’est pas restreinte.

Si vous le lancez à la main et que vous voulez qu’il ne soit joignable que depuis la machine elle-même, gardez la valeur par défaut d’uvicorn (`127.0.0.1`) et ouvrez `http://localhost:8000`, qui est un contexte sécurisé sans aucun certificat.
