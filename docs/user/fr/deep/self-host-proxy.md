---
title: Meshloom derrière votre reverse-proxy
description: Caddy, nginx ou Traefik devant Meshloom, avec les deux réglages à déclarer (adresse publique, proxy de confiance), les en-têtes et le WebSocket.
level: deep
order: 24.3
---

Voici la version courte pour un reverse-proxy que vous exploitez déjà. Les autres voies (Cloudflare Tunnel, Tailscale, WireGuard, Home Assistant) et les raisons de chaque réglage sont dans [Accès à distance par vos propres moyens](/docs/deep/remote-self-hosted/).

Depuis la 5.0, chaque page et chaque route d’API demande un compte : le proxy n’ouvre donc jamais Meshloom sans connexion ([Comptes et accès](/docs/deep/access/)). Exposer un proxy sur Internet reste votre décision : gardez-le à jour.

## Ce que le proxy doit faire

- Tout envoyer au port 8000 de Meshloom, **WebSocket compris** (`/api/ws`, la connexion en direct de la page) : transmettre les en-têtes `Upgrade` et `Connection`. Sans eux, la page s’affiche et rien n’arrive en direct.
- Garder le `Host` d’origine (Caddy et Traefik le font ; nginx a besoin de `$http_host`). Un proxy qui le remplace doit envoyer l’original dans `X-Forwarded-Host`, sinon le WebSocket est refusé comme venant d’un autre site.
- Envoyer `X-Forwarded-For` et `X-Forwarded-Proto`. Meshloom ne les croit que d’une adresse listée dans les Proxies de confiance.
- Ne pas fermer les connexions inactives en moins de trente secondes : la page envoie un ping toutes les trente secondes.
- Servir en HTTPS. Web Push, les passkeys et le chercheur de canaux en ont besoin ([HTTPS](/docs/deep/https/)).

## Ce qu’il faut déclarer dans Meshloom

En administrateur :

- **Réglages → Utilisateurs et accès → Adresses publiques** : ajoutez `https://meshloom.example.org` (le nom que l’on tape, avec `:port` s’il n’est pas 443). La [vérification du Host](/docs/deep/https/#les-noms-auxquels-meshloom-répond) n’accepte que les noms déclarés à partir de la 5.1.
- **Réglages → Configuration locale → Proxies de confiance** (`trusted_proxies`) : l’adresse propre du proxy, une par ligne. `127.0.0.1` s’il tourne sur la même machine que Meshloom. Un autre conteneur : donnez-lui une adresse fixe sur un réseau à part et listez cette adresse. Une autre machine : son adresse sur votre réseau. La liste est vide par défaut et `127.0.0.1` n’est pas sous-entendu. **Ne listez jamais un réseau Docker entier ni sa passerelle**, ni un réseau d’où viennent vos visiteurs. Exemple de fichier Compose : [HTTPS, Proxies de confiance](/docs/deep/https/#proxies-de-confiance).
- **Réglages → Utilisateurs et accès → Réseaux de confiance** : laissez **Accès sans connexion sur le réseau local** désactivé et la liste vide. Il est désactivé par défaut. Il doit le rester dès que Meshloom est joint par un proxy, ou par une redirection de port de la box qui réécrit l’adresse source (SNAT), car les visiteurs d’Internet sembleraient alors locaux. Une règle ne s’applique de toute façon jamais à une requête passée par un proxy ([détails](/docs/deep/access/#les-réseaux-de-confiance)).

Le serveur de Meshloom ne lit jamais lui-même les en-têtes transmis ; chaque commande de démarrage porte `--no-proxy-headers`, et celle que vous écrivez vous-même doit le garder.

## Caddy

```text
meshloom.example.org {
    reverse_proxy 127.0.0.1:8000
}
```

Caddy garde le `Host`, pose les deux en-têtes transmis et laisse passer le WebSocket.

## nginx

```nginx
server {
    listen 443 ssl;
    server_name meshloom.example.org;
    # ssl_certificate / ssl_certificate_key : votre certificat

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

Sans les lignes `Upgrade` et `Connection`, le WebSocket reçoit un 404 et rien n’arrive en direct.

## Traefik (v3)

Configuration dynamique (fournisseur de fichiers), avec un point d’entrée `websecure` et un certificat pour le nom déjà définis ; le même routeur et le même service peuvent s’écrire en labels Docker :

```yaml
http:
  routers:
    meshloom:
      rule: Host(`meshloom.example.org`)
      entryPoints: [websecure]
      service: meshloom
      tls: {}
  services:
    meshloom:
      loadBalancer:
        servers:
          - url: http://127.0.0.1:8000
```

Traefik garde le `Host`, pose les deux en-têtes et laisse passer le WebSocket. Laissez `forwardedHeaders.trustedIPs` vide sauf si un autre proxy se trouve devant lui.

## Vérifier

Ouvrez `https://meshloom.example.org` et connectez-vous. La page de connexion ne doit pas avertir que Meshloom a vu du HTTP simple (sinon, les Proxies de confiance manquent ou sont faux), la liste des sessions montre votre propre adresse, et **Noms inconnus vus récemment** reste vide. Sous un chemin comme `/meshcore/`, déclarez aussi le préfixe et lisez [Reverse-proxy sur un sous-chemin](/docs/deep/https/#reverse-proxy-sur-un-sous-chemin).

## Rester prudent

- **Ne publiez ni le port 8000 ni le port du proxy radio.** Dès que le proxy est devant Meshloom, le port 8000 ne doit plus être joignable autrement. Le proxy radio (**Réglages > Proxy**, port 5051 par défaut) écoute sur `0.0.0.0` et n’a pas de connexion : ne le redirigez jamais, ne le placez jamais derrière le proxy ([Sécurité](/docs/deep/security/#proxy-radio)).
- **Échecs de connexion répétés.** Le [bannissement intégré](/docs/deep/access/#les-délais-de-connexion) de Meshloom est actif par défaut et compte chaque visiteur séparément dès que le proxy est listé dans les Proxies de confiance. Pour un blocage au pare-feu, ou dans le journal du proxy, voir [fail2ban](/docs/deep/fail2ban/#derrière-un-reverse-proxy-sur-la-même-machine).
