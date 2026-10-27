---
title: Bloquer les échecs de connexion répétés avec fail2ban
description: Activer le jail fail2ban livré avec les paquets Linux, sous Docker, ou sur le reverse-proxy placé devant Meshloom.
level: deep
order: 24.5
---

Meshloom ralentit déjà quelqu’un qui devine un mot de passe ([Les délais de connexion](/docs/deep/access/#les-délais-de-connexion)). [fail2ban](https://github.com/fail2ban/fail2ban) ajoute un blocage au niveau du pare-feu : une adresse qui échoue cinq fois en dix minutes ne peut plus joindre le port 8000 du tout, pendant une heure, puis plus longtemps à chaque retour. C’est facultatif, et désactivé tant que vous ne l’activez pas.

Chaque mot de passe, code, passkey, jeton ou code d’installation refusé écrit une ligne commençant par `AUTH_FAIL` dans le journal de Meshloom, avec l’adresse d’où il vient. fail2ban surveille ces lignes. Rien dedans n’identifie le compte : seulement un code court qui change à chaque redémarrage.

## Le blocage intégré et fail2ban

Meshloom a aussi un [blocage intégré](/docs/deep/access/#les-délais-de-connexion), activé par défaut : 10 connexions refusées en 10 minutes depuis une même adresse la bloquent pendant 60 minutes. Il compte les mêmes lignes `AUTH_FAIL`, fonctionne partout où Meshloom tourne, Docker compris (les connexions par Home Assistant ne sont jamais comptées) et ne demande rien à installer. Un administrateur le règle, lève les blocages et tient une liste blanche dans **Réglages → Utilisateurs et accès → Bannissement d’adresses**, ou avec `meshloom-admin unban`.

La différence tient à l’endroit du blocage. Le blocage intégré répond à une adresse bloquée par une erreur avant de vérifier le moindre mot de passe : elle joint encore Meshloom, ce qui coûte peu. fail2ban la bloque au pare-feu : elle ne joint plus du tout le port. Les deux bloquent plus longtemps à chaque retour de l’adresse.

- **Le blocage intégré seul** suffit à la plupart des installations, et c’est le seul choix sous Docker sans journald, ou sous macOS ou Windows.
- **Les deux** sur une machine Linux ouverte sur internet : le blocage intégré agit tout de suite, fail2ban tient une adresse obstinée loin de la machine. Ils ne se gênent pas.

### Les lignes que compte le blocage intégré

Une ligne `AUTH_FAIL` compte pour le blocage intégré seulement si toutes ces conditions sont réunies :

- elle porte `proxied=0` (une ligne `proxied=1` désigne un proxy de votre réseau partagé par tous ses visiteurs) ;
- elle porte une adresse, pas `ip=-` (via Meshloom Remote, seul un visiteur à adresse publique est nommé) ;
- cette adresse n’est ni la boucle locale (`127.0.0.0/8`, `::1`), ni privée (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), ni de lien local (`169.254.0.0/16`, `fe80::/10`), ni une adresse IPv6 locale (`fc00::/7`, sauf la plage Tailscale `fd7a:115c:a1e0::/48`) ;
- sous Linux avec le paquet ou l’image Raspberry Pi, elle n’est pas dans un bloc IPv6 du réseau de cette machine : un `/64` ou plus étroit, lu sur les interfaces réseau, ou sur les routes directes quand l’adresse vient de DHCPv6 sous forme d’un seul `/128`. Un bloc plus large (`/48`, `/56`) est partagé avec d’autres réseaux et ne compte pas comme local ;
- elle n’est pas dans la liste blanche.

Les connexions par Home Assistant n’écrivent aucune ligne. Tout le reste compte : une adresse publique, et une adresse Tailscale ou d’un autre tailnet (`100.64.0.0/10`, et la plage IPv6 de Tailscale `fd7a:115c:a1e0::/48`), qu’elle joigne Meshloom directement ou par un proxy listé dans les Proxies de confiance (`tailscale serve` avec `127.0.0.1` listé). Un visiteur qui ajoute un faux en-tête `X-Forwarded-For` sans être un proxy de confiance est compté, et bloqué, sous sa propre adresse, jamais sous celle qu’il a écrite.

**Un reverse-proxy ailleurs sur votre tailnet doit être déclaré.** Un proxy qui joint Meshloom depuis un autre appareil du tailnet (une adresse `100.x.y.z` ou `fd7a:115c:a1e0::`) et qui n’est pas dans les [Proxies de confiance](/docs/deep/https/#proxies-de-confiance) compte comme un visiteur : ses connexions échouées sont comptées sous sa propre adresse, il finit donc bloqué, et tous les visiteurs derrière lui sont coupés avec lui. Listez son adresse tailnet dans les Proxies de confiance : le blocage tombe alors sur chaque visiteur.

Un nouveau blocage qui commence moins de 7 jours après la fin du précédent dure deux fois plus longtemps, jusqu’au **Bannissement le plus long** (24 heures par défaut, 30 jours au plus), dans les mêmes réglages. Lever un blocage l’oublie : le suivant repart à la durée normale.

**Vos propres appareils du tailnet peuvent être bloqués.** Un de vos téléphones qui tape 10 fois un mauvais mot de passe par Tailscale est bloqué comme n’importe quel visiteur. Pour lever le blocage : **Débannir** depuis un autre appareil, `meshloom-admin unban 100.x.y.z` (ou l’adresse `fd7a:115c:a1e0::` de l’appareil) depuis un terminal sur la machine, ou un ordinateur de votre réseau local. Pour ne jamais bloquer votre tailnet, ajoutez `100.64.0.0/10` et `fd7a:115c:a1e0::/48` (ou seulement les adresses de vos appareils) à la liste blanche ; les [délais de connexion](/docs/deep/access/#les-délais-de-connexion) s’appliquent toujours.

Le filtre fail2ban fourni est plus large : il lit toutes les lignes `proxied=0`, adresses privées comprises. Gardez vos propres réseaux dans son `ignoreip`.

## Ce que les paquets Linux installent

Les paquets `.deb` et `.rpm` apportent deux fichiers, tous deux conservés à la mise à jour (une modification de votre part n’est jamais écrasée) :

- `/etc/fail2ban/filter.d/meshloom.conf` : comment lire la ligne.
- `/etc/fail2ban/jail.d/meshloom.conf` : le jail, **désactivé** (`enabled = false`). Il lit le journal systemd de `meshloom.service`, bannit après 5 échecs en 10 minutes pour 1 heure, double le bannissement à chaque retour de l’adresse (une semaine au plus) et couvre le port 8000.

fail2ban lui-même n’est que suggéré : Meshloom fonctionne sans, et installer le paquet ne l’installe pas.

## L’activer (paquet Linux)

1. Installez fail2ban : `sudo apt install fail2ban` ou `sudo dnf install fail2ban` (sous RHEL, AlmaLinux et Rocky, il vient d’EPEL ; Fedora l’a dans ses dépôts).
2. Créez `/etc/fail2ban/jail.d/meshloom.local` avec ce contenu (le `.conf` livré reste intact, donc une mise à jour ne vous pose aucune question à son sujet) :

   ```ini
   [meshloom]
   enabled = true
   ```

   Si vous avez changé le port avec `--port` dans le service, ajoutez `port = ` suivi de votre port.
3. `sudo fail2ban-client reload`, puis `sudo fail2ban-client status meshloom` montre le jail.
4. Pour tester, ratez 5 connexions depuis un autre ordinateur, puis regardez `sudo fail2ban-client status meshloom` : son adresse est dans la liste. `sudo fail2ban-client set meshloom unbanip <adresse>` lève le bannissement.

Pour ne jamais bannir une de vos adresses, ajoutez `ignoreip = 127.0.0.1/8 ::1 192.168.1.0/24` sous `[meshloom]` dans ce même fichier `.local` (votre propre réseau).

## Derrière un reverse-proxy sur la même machine

Listez d’abord le proxy dans les [Proxies de confiance](/docs/deep/https/#proxies-de-confiance). Tant que ce n’est pas fait, chaque visiteur ressemble au proxy : quand le proxy envoie des en-têtes de transmission (tous le font), Meshloom marque la ligne `proxied=1` et le filtre **l’ignore**, car bannir votre propre proxy enfermerait tout le monde dehors. Une fois le proxy déclaré, la ligne porte l’adresse du visiteur. Un bannissement par le pare-feu de cette machine ne bloque alors le trafic de ce visiteur que si le proxy transmet l’adresse du visiteur lui-même, ce que ne fait pas un reverse-proxy normal : le pare-feu voit le proxy comme source. Pour bloquer au niveau du proxy, voir la section suivante.

## Docker

Le conteneur écrit sur sa sortie d’erreur, donc le jail change à deux endroits : où il lit et où il bloque. Mettez les deux dans `/etc/fail2ban/jail.d/meshloom.local`.

- **Où il lit.** Le pilote `json-file`, par défaut, enveloppe chaque ligne dans du JSON, que le filtre ne lit pas. Envoyez plutôt les journaux du conteneur au journal systemd. Dans `docker-compose.yml`, sous le service Meshloom :

  ```yaml
  logging:
    driver: journald
    options:
      tag: meshloom
  ```

  Lancez `docker compose up -d`. Le jail lit alors les entrées du journal que Docker écrit pour votre conteneur (son nom, tel que l’affiche `docker ps`). Nommer aussi l’unité de Docker compte : le nom du conteneur seul pourrait être imité par un autre programme qui écrit dans le journal.

  ```ini
  journalmatch = _SYSTEMD_UNIT=docker.service CONTAINER_NAME=meshloom
  ```
- **Où il bloque.** Le trafic vers un port publié est transmis au conteneur, pas remis à l’hôte : le bannissement par défaut (le point d’accroche `input`) n’a aucun effet. Choisissez l’un des deux :

  ```ini
  # hôtes nftables (le défaut des Debian et Fedora actuelles)
  banaction = nftables[chain_hook=forward]
  ```

  ```ini
  # ou iptables (demande la commande iptables ; le jail bloque alors dans la chaîne propre à Docker)
  banaction = iptables-multiport
  chain = DOCKER-USER
  ```

  Dans les deux cas, réglez `port` sur le port **du conteneur**, 8000, pas celui que Docker publie sur l’hôte : quand le pare-feu voit le paquet, Docker a déjà réécrit la destination. Avec `ports: "8080:8000"`, vous joignez Meshloom sur 8080 et écrivez `port = 8000`.

Deux autres points pour Docker :

- Meshloom doit voir l’adresse propre de chaque visiteur. Dans un conteneur sans `network_mode: host` et sans proxy déclaré, il voit l’adresse de la passerelle de Docker pour tout le monde (`172.x.x.x`) et la consigne comme n’importe quelle adresse : le jail bannirait la passerelle, donc tous les visiteurs. Ajoutez toujours les réseaux Docker à `ignoreip` : `ignoreip = 127.0.0.1/8 ::1 172.16.0.0/12` (ajoutez `192.168.0.0/16` si vos réseaux Docker l’utilisent). [Les délais de connexion](/docs/deep/access/#les-délais-de-connexion) donne les deux moyens de faire voir à Meshloom les vraies adresses (réseau hôte, ou **Les adresses identifient les clients**) ; tant que l’un n’est pas en place, le jail ne peut pas aider.
- Les lignes marquées `proxied=1` sont ignorées par le filtre ; elles désignent un reverse-proxy d’un réseau local absent des **Proxies de confiance**.

## Un reverse-proxy sur une autre machine

fail2ban sur la machine de Meshloom ne voit que le proxy. Bannissez plutôt sur la machine du proxy, d’après son propre journal d’accès : une connexion ratée répond `401` à `POST /api/auth/login`. Pour nginx avec le journal `combined` par défaut :

```ini
# /etc/fail2ban/filter.d/meshloom-nginx.conf
[Definition]
failregex = ^<ADDR> \S+ \S+ \[\] "POST /api/auth/login HTTP/[\d.]+" 401\b
```

```ini
# /etc/fail2ban/jail.d/meshloom-nginx.conf
[meshloom-nginx]
enabled = true
filter = meshloom-nginx
logpath = /var/log/nginx/access.log
port = http,https
maxretry = 5
findtime = 10m
bantime = 1h
```

*Non testé de bout en bout.* Cela ne voit que le formulaire du mot de passe ; un mauvais jeton ou une passkey refusée sont consignés par Meshloom, pas par le proxy. Si le proxy est lui-même derrière un autre (un CDN), l’adresse de son journal est celle de cet autre : configurez d’abord l’adresse réelle du client dans le proxy. Caddy écrit des journaux JSON : écrivez un filtre sur ses champs `remote_ip`, `uri` et `status` de la même façon.

## Quand cela n’aide pas

- **Cloudflare Tunnel.** La connexion vient de Cloudflare, pas du visiteur : un bannissement par le pare-feu bloquerait Cloudflare pour tout le monde. Utilisez la limitation de débit et Access de Cloudflare, et laissez ce jail désactivé.
- **Add-on Home Assistant.** fail2ban ne peut pas s’installer dans l’add-on, et les connexions par Home Assistant n’écrivent aucune ligne. Home Assistant a son propre bannissement d’IP.
- **Tailscale ou WireGuard seulement.** Seuls vos propres appareils se connectent : un bannissement au pare-feu apporte peu. Le blocage intégré compte quand même les adresses du tailnet (voir plus haut).

## À lire aussi

- [Les délais de connexion, le blocage intégré et la ligne `AUTH_FAIL`](/docs/deep/access/#les-délais-de-connexion)
- [Accès à distance par vos propres moyens](/docs/deep/remote-self-hosted/), avec les pages courtes [Tailscale serve](/docs/deep/self-host-tailscale/) et [Meshloom derrière votre reverse-proxy](/docs/deep/self-host-proxy/)
- [Sécurité](/docs/deep/security/)
