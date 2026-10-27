---
title: Accès à distance par vos propres moyens
description: Joindre Meshloom depuis l’extérieur avec votre propre Cloudflare Tunnel, Tailscale ou WireGuard, ou un reverse-proxy (Caddy, nginx, Traefik), et ce qu’il faut déclarer dans Meshloom.
level: deep
order: 24
---

Meshloom écoute sur le port 8000 de la machine qui l’exécute, et rien de plus. Pour le joindre depuis l’extérieur de chez vous ou de votre bureau, ne redirigez pas ce port sur votre box : placez devant lui un élément que vous contrôlez. Cette page présente trois voies, toutes sur votre propre compte ou vos propres machines, sans rien demander au projet Meshloom. L’add-on Home Assistant a [sa propre note](#add-on-home-assistant).

Depuis la 5.0, chaque page et chaque route d’API demande un compte, quelle que soit la voie : un tunnel ou un proxy n’ouvre donc jamais Meshloom sans connexion. Lisez d’abord [Comptes et accès](/docs/deep/access/), et donnez à chaque administrateur un mot de passe solide, la [double authentification](/docs/deep/access/#la-double-authentification) ou une [passkey](/docs/deep/access/#les-passkeys).

> **Ce qui a été testé.** Le côté Meshloom de chaque voie (quel réglage changer, ce que Meshloom fait des en-têtes reçus) a été vérifié dans le code de cette version. Les configurations Cloudflare, Tailscale, Caddy, nginx et Traefik ci-dessous sont les méthodes documentées par ces produits et n’ont **pas** été exécutées de bout en bout par l’auteur de cette page : elles sont marquées *non testé* là où elles apparaissent. Dites-nous ce que vous constatez.

## Ce que Meshloom doit savoir

Quelle que soit la voie, quatre éléments décident du bon comportement de Meshloom derrière elle.

| Réglage | Où | À quoi il sert |
|---|---|---|
| **Adresses publiques** | **Réglages → Utilisateurs et accès → Adresses publiques** | Le nom que l’on tape pour joindre Meshloom, écrit `https://nom[:port][/préfixe]`. Nécessaire à la vérification du Host ([plus bas](#noms-dhôte)) et aux passkeys. |
| **Proxies de confiance** | **Réglages → Configuration locale → Proxies de confiance** | L’adresse du programme qui transmet vers Meshloom (tunnel, proxy). Permet à Meshloom de voir l’adresse du visiteur et que la page est en HTTPS. |
| **Refuser les noms inconnus (strict)** | Même page que les adresses publiques | Passe la vérification du Host de l’observation (défaut de la 5.0.0) au mode strict. Facultatif en 5.0.0. |
| **Réseaux de confiance** | **Réglages → Utilisateurs et accès → Réseaux de confiance** | Gardez cette liste **vide** pour tout ce qui passe par un tunnel ou un proxy ([plus bas](#réseaux-de-confiance-désactivés)). |

### Noms d’hôte

Meshloom vérifie le nom présent dans l’adresse du navigateur (le `Host`) pour résister au DNS rebinding ([Les noms auxquels Meshloom répond](/docs/deep/https/#les-noms-auxquels-meshloom-répond)). En 5.0.0, il se contente de noter un nom inconnu dans son journal et dans **Noms inconnus vus récemment** ; à partir de la 5.1, il le refusera avec une erreur `421` qui le nomme. Déclarez dès maintenant votre nom public : **Réglages → Utilisateurs et accès → Adresses publiques**, ajoutez `https://meshloom.example.org`. Seul le nom compte pour la vérification ; le schéma est toujours `https` dans ce champ, même si vous joignez Meshloom en HTTP simple. Activer **Refuser les noms inconnus** est facultatif ; Meshloom refuse de l’activer s’il vous enfermait dehors avec l’adresse que vous utilisez.

Si vous vous enfermez dehors avec un nom désormais refusé, ouvrez Meshloom par son adresse IP (toujours acceptée), ou lancez `meshloom-admin host-guard-observe` sur la machine ([Comptes et accès](/docs/deep/access/#les-commandes)).

### Proxies de confiance

Derrière un tunnel ou un proxy, chaque requête arrive à Meshloom depuis l’adresse de celui qui transmet. Meshloom ne croit `X-Forwarded-For` et `X-Forwarded-Proto` que depuis les adresses listées dans **Proxies de confiance**, liste vide par défaut (`127.0.0.1` n’est pas sous-entendu). Listez l’adresse propre de celui qui transmet, une par ligne :

- il tourne sur la même machine que Meshloom (installation par paquet ou depuis les sources, ou `cloudflared` / Caddy / `tailscale serve` en service sur l’hôte) : `127.0.0.1`, et `::1` s’il se connecte en IPv6 ;
- c’est un autre conteneur : donnez-lui une adresse fixe sur un réseau dédié et listez cette adresse (un exemple de fichier Compose figure dans [HTTPS, Proxies de confiance](/docs/deep/https/#proxies-de-confiance)) ;
- c’est une autre machine : son adresse sur votre réseau.

**Ne listez jamais un réseau Docker entier ni sa passerelle.** Ne listez jamais un réseau d’où viennent vos visiteurs. Ne publiez plus du tout le port 8000 dès qu’un proxy est devant Meshloom. Détails et raisons dans [HTTPS](/docs/deep/https/#proxies-de-confiance).

Une fois celui qui transmet listé, les délais de connexion, le journal d’audit et la liste des sessions montrent l’adresse du visiteur, et le cookie de session devient sécurisé (préfixe `__Host-`, `Secure`) quand il indique `X-Forwarded-Proto: https`. La page de connexion et le bloc Proxies de confiance le signalent quand votre navigateur est en HTTPS mais que Meshloom a vu du HTTP simple : ce message est le moyen le plus rapide de vérifier que cette étape a fonctionné.

### Réseaux de confiance désactivés

Une règle de réseau de confiance connecte un appareil sans mot de passe. Elle est **désactivée** pour toute requête passée par un tunnel ou un proxy, parce que chaque visiteur, d’où qu’il vienne, arrive depuis l’adresse de celui qui transmet : une requête portant `X-Forwarded-For`, `Forwarded`, `X-Real-IP`, `CF-Connecting-IP` ou équivalent, ou venant d’un proxy de confiance listé, n’est jamais connectée par une règle, et une règle ne couvre jamais la boucle locale de cette machine hors de l’add-on Home Assistant. Meshloom refuse aussi d’enregistrer une règle qui recouvre un proxy de confiance. Derrière un proxy, tout le monde se connecte donc avec un compte, vous compris sur votre téléphone ; gardez vos règles de réseau de confiance, s’il y en a, pour les appareils du réseau local qui joignent Meshloom par son adresse IP ([détails](/docs/deep/access/#les-réseaux-de-confiance)).

### HTTPS, HSTS et passkeys

- **Laissez celui qui transmet faire le HTTPS.** Un tunnel ou un proxy qui sert votre nom en HTTPS fait de la page un contexte sécurisé, dont ont besoin le Web Push, le chercheur de canaux et les passkeys ([HTTPS](/docs/deep/https/)).
- **HSTS.** Derrière un proxy, Meshloom n’envoie `Strict-Transport-Security` que si le proxy est listé dans les proxies de confiance et indique `X-Forwarded-Proto: https` ; sinon il n’en envoie pas et le proxy doit envoyer le sien. L’en-tête épingle tout le nom d’hôte, sur tous les ports, pendant un an : ne servez donc pas Meshloom sous un nom qui porte aussi un service en HTTP simple que vous utilisez encore ([En-têtes de sécurité](/docs/deep/https/#en-têtes-de-sécurité)).
- **Les passkeys** ne sont proposées qu’en https sur un nom déclaré dans **Adresses publiques** (et, derrière un proxy, seulement si Meshloom a vu le https, donc si le proxy est un proxy de confiance). L’identifiant de partie de confiance (RP id) de la passkey est ce nom d’hôte : une passkey ne fonctionne que sur le nom où elle a été créée, et changer plus tard de nom public oblige à en enregistrer de nouvelles. Elles ne sont pas proposées sur une adresse IP nue, ni en `http://` simple (sauf `http://localhost`), ni via l’ingress de Home Assistant.
- **WebSocket.** La connexion en direct de la page est `/api/ws`. Le proxy doit laisser passer les en-têtes de changement de protocole, et s’il réécrit `Host` il doit envoyer l’original dans `X-Forwarded-Host` ([WebSocket](/docs/deep/https/#websocket)). Caddy et Traefik n’ont besoin de rien ; l’exemple nginx ci-dessous s’en charge.
- **Web Push.** Le push exige du HTTPS sur un nom stable : il fonctionne donc via un tunnel ou un proxy sur votre propre nom, pas via une adresse IP nue ([Notifications push](/docs/deep/push/#ce-quil-vous-faut)). Meshloom lui-même doit aussi joindre les services push des éditeurs de navigateurs (Google, Mozilla, Apple) depuis l’endroit où il tourne ; le tunnel ne transporte pas ce trafic.

## Cloudflare Tunnel depuis votre compte

*Non testé de bout en bout.* Avec un Cloudflare Tunnel, `cloudflared`, un petit programme de votre côté, ouvre une connexion sortante vers Cloudflare ; Cloudflare sert votre nom en HTTPS et envoie les requêtes par ce canal. Rien n’est ouvert sur votre box. Il faut un compte Cloudflare et un domaine dessus.

1. Dans Cloudflare, créez un tunnel (Zero Trust → Networks → Tunnels) et installez le connecteur sur la machine qui exécute Meshloom (ou dans un conteneur à côté).
2. Ajoutez un **nom d’hôte public**, par exemple `meshloom.example.org`, dont le service est `http://localhost:8000` (`http://meshloom:8000` si `cloudflared` est un conteneur sur le réseau Docker de Meshloom, port 8000 non publié).
3. Dans Meshloom, ajoutez `https://meshloom.example.org` aux **Adresses publiques**.
4. Dans **Proxies de confiance**, listez l’adresse depuis laquelle `cloudflared` se connecte : `127.0.0.1` s’il tourne sur l’hôte ; une adresse fixe de conteneur s’il en est un.
5. Ouvrez `https://meshloom.example.org` et connectez-vous. La page de connexion ne doit pas dire que Meshloom a vu du HTTP simple.

Cloudflare envoie `X-Forwarded-For`, `X-Forwarded-Proto` et `CF-Connecting-IP`. Meshloom lit les deux premiers depuis un proxy listé et jamais `CF-Connecting-IP`, qui sert seulement à garder les réseaux de confiance désactivés.

**Cloudflare Access devant** (facultatif, conseillé pour une famille ou une équipe) : dans Zero Trust, ajoutez une application Access sur le même nom d’hôte avec une règle du type « e-mails se terminant par ». Les visiteurs passent alors la connexion de Cloudflare avant celle de Meshloom. C’est une porte de plus ; elle ne remplace pas les comptes de Meshloom (Meshloom ne lit pas l’identité d’Access). N’appliquez pas Access séparément au chemin WebSocket : protégez tout le nom d’hôte, sinon la connexion en direct échoue. Un jeton d’API (en-tête `Authorization`) utilisé par un script ne passe pas la connexion Access, sauf si vous créez pour lui un jeton de service Cloudflare et envoyez aussi ses deux en-têtes.

Sous Home Assistant, l’add-on **Cloudflared** fait le même travail : voir [la note sur l’add-on](#add-on-home-assistant).

## Tailscale ou WireGuard

Ils placent votre téléphone ou votre portable sur un réseau privé avec la machine qui exécute Meshloom. Rien n’est publié sur Internet : c’est l’exposition la plus faible des trois voies, au prix d’installer le VPN sur chaque appareil qui doit joindre Meshloom.

### Tailscale

*Non testé de bout en bout.*

1. Installez Tailscale sur la machine de Meshloom et sur vos appareils, connectés au même tailnet.
2. **Joignez Meshloom par son adresse du tailnet** : `http://100.x.y.z:8000` (une adresse IP, toujours acceptée) ou `http://nom-machine.nom-tailnet.ts.net:8000` avec MagicDNS. Déclarez le nom MagicDNS dans **Adresses publiques** sous la forme `https://nom-machine.nom-tailnet.ts.net` : la vérification du Host l’exigera à partir de la 5.1, et **Noms inconnus vus récemment** le listera avec **Autoriser** si vous l’oubliez. Ne déclarez jamais un nom que vous n’avez pas créé vous-même.
3. Cela fonctionne en HTTP simple : Tailscale chiffre le trafic entre vos appareils, mais le navigateur considère quand même la page comme non sécurisée ; aucune fonction réservée au HTTPS (push, passkeys, chercheur de canaux) ne marche et le cookie de session est le cookie simple. **Pour le Web Push, les passkeys et le chercheur de canaux, il faut du HTTPS** : utilisez `tailscale serve --bg --https=443 http://127.0.0.1:8000` (et activez les certificats HTTPS de votre tailnet dans la console d’administration Tailscale), puis ouvrez `https://nom-machine.nom-tailnet.ts.net`. Ajoutez `127.0.0.1` aux **Proxies de confiance** pour que Meshloom voie le HTTPS et le vrai appareil, et déclarez ce nom dans **Adresses publiques**.
4. `tailscale funnel` publierait Meshloom sur tout Internet. Il n’est pas nécessaire ici ; les comptes de Meshloom seraient alors la seule porte : préférez `serve`.

Le push vers un nom `*.ts.net` pour les points d’arrivée des notifications est un autre sujet : voir [Services push sur un réseau privé](/docs/deep/push/#services-push-sur-un-réseau-privé).

### WireGuard

*Non testé de bout en bout.* Faites tourner un serveur WireGuard sur votre box ou sur la machine de Meshloom, avec un pair par appareil, et ouvrez Meshloom à son adresse dans le tunnel (par exemple `http://10.8.0.1:8000`). Une adresse IP est toujours acceptée par la vérification du Host : aucune déclaration n’est nécessaire. Comme avec Tailscale, le HTTP simple veut dire ni push ni passkeys : ajoutez un reverse-proxy avec certificat (section suivante) qui écoute sur l’adresse du tunnel, ou utilisez un nom pour lequel vous avez un certificat.

Dans les deux cas, le visiteur joint Meshloom directement (aucun intermédiaire) : laissez donc les Proxies de confiance tels quels, et notez qu’une [règle de réseau de confiance](/docs/deep/access/#les-réseaux-de-confiance) qui couvrirait la plage du VPN connecterait tous ceux qui sont sur ce VPN : n’en ajoutez pas.

## Votre propre reverse-proxy

*Non testé de bout en bout* pour les extraits exacts ; les en-têtes qu’ils posent sont ceux listés dans [HTTPS](/docs/deep/https/#proxies-de-confiance). Il faut un nom qui pointe vers le proxy et un certificat pour ce nom (Caddy en obtient un tout seul). Laisser le port 80 ou 443 joignable depuis Internet chez vous est votre décision : le proxy est alors exposé, gardez-le à jour.

**Caddy.** Conserve `Host`, pose les deux en-têtes de transfert, laisse passer le WebSocket :

```text
meshloom.example.org {
    reverse_proxy 127.0.0.1:8000
}
```

**nginx.** Ne pose rien sans instruction (gardez les lignes `Upgrade` et `Connection` pour la connexion en direct, et `$http_host` pour conserver le port) :

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

**Traefik** (v3, fournisseur Docker). Conserve `Host`, pose les deux en-têtes et laisse passer le WebSocket. Laissez `forwardedHeaders.trustedIPs` vide sauf si un autre proxy est devant lui. Labels sur le conteneur Meshloom, avec un point d’entrée `websecure` sur `:443` et un résolveur de certificats nommé `letsencrypt` déjà défini dans la configuration statique de Traefik (changez les deux noms pour les vôtres) :

```yaml
labels:
  - traefik.enable=true
  - traefik.http.routers.meshloom.rule=Host(`meshloom.example.org`)
  - traefik.http.routers.meshloom.entrypoints=websecure
  - traefik.http.routers.meshloom.tls.certresolver=letsencrypt
  - traefik.http.services.meshloom.loadbalancer.server.port=8000
```

Traefik et Meshloom doivent partager un réseau Docker ; listez l’adresse fixe de Traefik sur ce réseau dans les Proxies de confiance ([exemple](/docs/deep/https/#proxies-de-confiance)).

Ensuite, dans Meshloom : déclarez `https://meshloom.example.org` dans **Adresses publiques** et listez le proxy dans **Proxies de confiance** (`127.0.0.1` s’il est sur l’hôte ; sinon une adresse fixe de conteneur). Si le proxy sert Meshloom sous un chemin comme `/meshcore/`, déclarez `https://meshloom.example.org/meshcore` et lisez [Reverse-proxy sur un sous-chemin](/docs/deep/https/#reverse-proxy-sur-un-sous-chemin).

## Add-on Home Assistant

Ouvert depuis la barre latérale de Home Assistant, Meshloom connecte l’utilisateur Home Assistant et n’a besoin de rien de ce qui précède ; l’ingress n’est jamais refusé pour son nom et a sa propre adresse. La barre latérale n’a pas d’adresse publique stable : passkeys, réseaux de confiance et Web Push ne fonctionnent donc pas par elle. Pour joindre Meshloom de l’extérieur avec l’add-on **Cloudflared** (ou une des voies ci-dessus) : dans le panneau **Réseau** de l’add-on Meshloom, donnez un port d’hôte à `8000/tcp`, routez le nom d’hôte vers `http://<ip-home-assistant>:<ce port>`, déclarez le nom d’hôte dans **Adresses publiques**, puis regardez la page de connexion à travers le tunnel : si elle avertit que Meshloom a vu du HTTP simple, il manque l’entrée dans Proxies de confiance. **L’adresse à lister dépend d’où vient la connexion.** Si le tunnel est un conteneur à part sur le réseau des add-ons de Home Assistant (l’add-on Cloudflared), listez l’adresse de ce conteneur, telle que Meshloom la voit (le journal d’audit et **Mes sessions** la montrent). Si la connexion arrive par le port d’hôte publié, Meshloom voit en général la passerelle Docker de ce réseau : **ne la listez pas** (règle ci-dessus), laissez les Proxies de confiance vides et acceptez que les visiteurs partagent une adresse et que le cookie de session soit le cookie simple, ou faites pointer le tunnel directement vers l’adresse du conteneur de l’add-on plutôt que vers le port publié. L’adresse n’a pas été vérifiée par l’auteur sur du matériel Home Assistant. Cette adresse demande un compte Meshloom, pas une connexion Home Assistant. Voir [Home Assistant](/docs/deep/access/#home-assistant) et la note d’installation dans [Installation](/docs/install/).

## Bloquer ceux qui devinent avec fail2ban

Avec un proxy sur votre propre machine, ou Meshloom exposé sur Internet, [fail2ban](/docs/deep/fail2ban/) peut bannir une adresse qui rate sans cesse sa connexion. Cela n’aide pas derrière un Cloudflare Tunnel, où chaque visiteur arrive depuis Cloudflare.

## Liste de contrôle

- Les comptes existent, les administrateurs ont un mot de passe solide et un second facteur ou une passkey.
- La page est servie en HTTPS par celui qui transmet, et la page de connexion n’avertit pas d’un HTTP simple.
- Le nom public est dans **Adresses publiques** ; l’adresse propre de celui qui transmet, et rien de plus large, est dans **Proxies de confiance**.
- **Réseaux de confiance** est vide, ou ne contient que des appareils locaux joints par adresse IP.
- Le port 8000 et le port du proxy radio (5051) ne sont pas redirigés sur votre box. Le proxy radio n’a pas d’authentification : voir [Sécurité](/docs/deep/security/).
