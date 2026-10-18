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
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-proxy-headers --ssl-keyfile=key.pem --ssl-certfile=cert.pem
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
    command: uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-proxy-headers --ssl-keyfile=/app/key.pem --ssl-certfile=/app/cert.pem
```

Les montages du certificat sont en lecture seule. `command` remplace celle de l’image : elle doit donc contenir toute la ligne, hôte et port compris.

## Les noms auxquels Meshloom répond

Une page web d’un autre domaine peut être redirigée vers votre réseau local (rebinding DNS) puis parler à Meshloom sous le nom de ce domaine. Meshloom regarde donc le nom saisi dans le navigateur (le `Host`) et reconnaît comme siens : une adresse IP, `localhost`, les noms finissant par `.local`, `.home.arpa`, `.lan` ou `.internal`, le nom de cette machine, une adresse publique que vous avez déclarée et le nom de Meshloom Remote.

En 5.0.0 c’est de l’**observation seulement** : tout autre nom (par exemple le nom DNS de votre reverse-proxy) fonctionne encore, Meshloom le note simplement dans son journal et garde la liste des noms vus. À partir de la 5.1, un nom inconnu sera refusé avec une erreur qui le nomme : déclarez l’adresse publique de votre reverse-proxy d’ici là. L’exception est déjà stricte : un [réseau de confiance](/docs/deep/access/#les-réseaux-de-confiance) ne connecte personne quand le navigateur utilise un nom inconnu.

### Déclarer les adresses publiques

Un administrateur les gère dans **Réglages → Utilisateurs et accès → Adresses publiques** ; il n’existe aucune variable d’environnement pour cela.

- Les **adresses publiques** s’écrivent `https://nom[:port][/préfixe]` (lettres, chiffres, points et tirets ; ni identifiant, ni paramètres, ni fragment ; 16 au plus). Meshloom les enregistre sous forme canonique (minuscules, `:443` retiré). Seul le nom compte pour le contrôle du Host.
- **Noms inconnus vus récemment** liste les noms utilisés pour atteindre Meshloom et non déclarés (depuis le dernier redémarrage, le plus récent d’abord, avec un compteur). **Autoriser** en déclare un comme adresse publique et le retire de la liste.
- **Refuser les noms inconnus (strict)** fait passer le contrôle de l’observation au mode strict : une requête sous un nom non déclaré reçoit une erreur (421) qui le nomme. Meshloom refuse de l’activer s’il rejetterait l’adresse que vous utilisez en ce moment, et de retirer cette adresse tant que le mode strict est actif. Si un nom cesse malgré tout de fonctionner, ouvrez Meshloom par son adresse IP (toujours acceptée) et désactivez l’option.

Seuls les noms simples (lettres, chiffres, points, tirets) sont listés et journalisés ; les autres valeurs de `Host` ne sont jamais enregistrées. Le journal garde une seule ligne par nouveau nom, et une ligne de synthèse quand beaucoup de noms différents arrivent en une minute.

## Proxies de confiance

Derrière un reverse-proxy, chaque requête arrive à Meshloom depuis l’adresse du proxy. L’adresse du visiteur et le fait qu’il soit passé en HTTPS n’arrivent que dans les en-têtes `X-Forwarded-For` et `X-Forwarded-Proto`, que n’importe qui peut envoyer. Meshloom ne les croit que depuis les adresses listées dans **Réglages → Configuration locale → Proxies de confiance** (administrateurs seulement). La liste est vide par défaut, et `127.0.0.1` n’est pas de confiance tant que vous ne l’ajoutez pas.

Indiquez l’adresse du proxy lui-même, telle que Meshloom la voit, une adresse par ligne (un `/32`, ou un `/128` en IPv6) :

- le proxy tourne sur la même machine qu’une installation par paquet ou depuis les sources : `127.0.0.1` (et `::1` s’il se connecte en IPv6) ;
- le proxy est un autre conteneur : donnez-lui une adresse fixe sur un réseau dédié et indiquez cette adresse, par exemple `172.20.0.10` (voir plus bas) ;
- le proxy est une autre machine : son adresse sur votre réseau.

**N’indiquez jamais un réseau Docker entier ni sa passerelle** (`172.18.0.0/16`, `172.17.0.1`, le `192.168.65.1` de Docker Desktop). Quand le port 8000 de Meshloom est aussi publié sur l’hôte, certains clients l’atteignent par cette passerelle (connexions depuis l’hôte lui-même, IPv6 via `docker-proxy`, Docker Desktop) : avec la passerelle de confiance, ils pourraient écrire n’importe quelle adresse dans `X-Forwarded-For` et contourner les délais de connexion ou fausser le journal d’audit. La page des réglages signale une telle entrée. Une fois qu’un proxy est devant Meshloom, ne publiez plus le port 8000 si vous le pouvez.

Avec Docker Compose, une adresse fixe pour le proxy ressemble à ceci :

```yaml
services:
  meshloom:
    networks: [proxy]
    # pas de « ports: » une fois le proxy devant Meshloom
  caddy:
    image: caddy:2
    ports: ["443:443", "80:80"]
    networks:
      proxy:
        ipv4_address: 172.20.0.10   # l’adresse à indiquer dans Proxies de confiance
networks:
  proxy:
    ipam:
      config:
        - subnet: 172.20.0.0/24
```

Une fois le proxy listé :

- les délais de connexion, le journal d’audit et la liste des sessions montrent l’adresse du visiteur au lieu de celle du proxy. Meshloom prend l’adresse la plus à droite de `X-Forwarded-For` qui n’est pas elle-même un proxy de confiance, si bien qu’une valeur écrite par le visiteur ne peut pas être retenue ;
- quand le proxy indique `X-Forwarded-Proto: https`, le cookie de session est un cookie sécurisé (préfixe `__Host-`, `Secure`).

Sans la liste, Meshloom fonctionne quand même derrière un proxy, mais tous les visiteurs partagent l’adresse du proxy et, sur un site HTTPS, le cookie de session est le cookie simple. La page de connexion et le bloc Proxies de confiance le signalent quand votre navigateur est en HTTPS mais que Meshloom a vu du HTTP simple. Ne listez jamais un réseau d’où viennent vos visiteurs : une adresse listée peut se faire passer pour n’importe qui. Le serveur de Meshloom (uvicorn) ne lit lui-même aucun de ces en-têtes : chaque commande de démarrage porte `--no-proxy-headers`, et une commande que vous écrivez vous-même doit le garder.

Caddy garde `Host` et pose les deux en-têtes de lui-même :

```text
meshloom.example.org {
    reverse_proxy 127.0.0.1:8000
}
```

Nginx ne pose rien sans qu’on le lui demande :

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
}
```

Traefik garde `Host` et pose les deux en-têtes ; il ne transmet ceux envoyés par un client que depuis ses propres `forwardedHeaders.trustedIPs`, laissez donc ce réglage vide sauf si un autre proxy se trouve devant lui. Avec des labels Docker :

```yaml
labels:
  - traefik.http.routers.meshloom.rule=Host(`meshloom.example.org`)
  - traefik.http.services.meshloom.loadbalancer.server.port=8000
```

La barre latérale de Home Assistant n’a besoin d’aucune entrée : Meshloom reconnaît le Superviseur à sa propre adresse.

## En-têtes de sécurité

Quand le navigateur atteint Meshloom directement en HTTPS, Meshloom ajoute `Strict-Transport-Security` (un an, sans sous-domaines) pour que le navigateur reste en HTTPS sur ce nom d'hôte, **sur tous les ports** : un autre service portant le même nom (par exemple `http://homeassistant.local:8123`) est alors aussi forcé en HTTPS pendant un an. Les navigateurs l'ignorent pour une simple adresse IP. Il ne l'envoie jamais en HTTP simple : une adresse locale comme `http://192.168.1.20:8000` n'est donc pas figée en HTTPS. Il ne l'envoie jamais non plus via la barre latérale de Home Assistant ni via le relais, où l'adresse appartient à quelqu'un d'autre. Derrière un reverse-proxy, l'en-tête n'est envoyé que si le proxy figure dans les [Proxies de confiance](#proxies-de-confiance) et indique `X-Forwarded-Proto: https` ; sinon, laissez le proxy envoyer le sien.

Les réponses de connexion (`/api/auth/`) et celles qui portent des secrets (clé privée de la radio, sauvegardes, clés des canaux) portent `Cache-Control: no-store` : ni navigateur ni proxy ne les conserve. Une `Permissions-Policy` coupe la caméra et le micro pour la page et laisse au bouton de position son accès à la localisation.

## Reverse-proxy sur un sous-chemin

Meshloom peut être servi sous un préfixe, par exemple `/meshcore/`, y compris via la barre latérale de Home Assistant. Les adresses des ressources, de l’API et du manifeste web sont toutes relatives : il n’y a rien d’autre à configurer, et aucun en-tête `X-Forwarded-*` n’est nécessaire, sauf `X-Forwarded-Host` pour la connexion en direct quand le proxy réécrit `Host` (voir [WebSocket](#websocket)).

Une seule exigence côté proxy : l’adresse doit **se terminer par une barre oblique**. Si un visiteur atteint `/meshcore` sans elle, les chemins relatifs cassent. La plupart des proxys s’en occupent ; avec Nginx, un bloc `location /meshcore/ { ... }`, barre comprise, fait ce qu’il faut.

## WebSocket

La page garde une connexion permanente avec le serveur, sur `/api/ws`, pour recevoir en direct les messages, les accusés de réception, les paquets et l’état de la radio. Un proxy qui ne relaie pas les en-têtes de changement de protocole la coupe. Symptôme typique : la page s’affiche, l’historique se charge, mais rien n’arrive en direct. Le navigateur réessaie en attendant un peu plus longtemps à chaque fois (d’une seconde jusqu’à trente).

La page envoie un ping au serveur toutes les trente secondes. Un proxy qui coupe les connexions inactives plus tôt que cela provoque des reconnexions en boucle.

Quand l’authentification HTTP Basic est activée, elle s’applique aussi à cette connexion, pas seulement aux pages.

Le serveur refuse cette connexion quand elle vient d’une page d’un autre site : le navigateur indique à quel site appartient la page (l’en-tête `Origin`), qui doit correspondre à l’adresse demandée par le navigateur (l’en-tête `Host`), sauf si ce site fait partie des **Origines autorisées** (voir [Sécurité](/docs/deep/security/)). Les scripts et les applications qui n’envoient pas d’`Origin` ne sont pas concernés. Un proxy qui garde le `Host` d’origine, comme Caddy et Traefik, n’a besoin de rien de plus. Un proxy qui le remplace doit transmettre l’original dans `X-Forwarded-Host` (avec Nginx : `proxy_set_header Host $http_host;` ou `proxy_set_header X-Forwarded-Host $http_host;` ; `$host` perdrait le port) ; la barre latérale de Home Assistant le fait déjà. Sinon la page s’affiche mais rien n’arrive en direct, et le journal du serveur indique `Refused a WebSocket upgrade from a foreign origin`.

## Sur quelle adresse écouter

`--host 0.0.0.0` rend le serveur joignable depuis tous les réseaux auxquels la machine est reliée. C’est ce que font le paquet Linux, l’installation Docker de l’installateur et l’image, parce que l’intérêt est d’utiliser Meshloom depuis un téléphone ou un autre ordinateur. C’est aussi ce qui rend pertinents les avertissements de [Sécurité](/docs/deep/security/) : un compte est la seule barrière, et le serveur ne restreint pas les adresses qui peuvent l’atteindre.

Si vous le lancez à la main et que vous voulez qu’il ne soit joignable que depuis la machine elle-même, gardez la valeur par défaut d’uvicorn (`127.0.0.1`) et ouvrez `http://localhost:8000`, qui est un contexte sécurisé sans aucun certificat.
