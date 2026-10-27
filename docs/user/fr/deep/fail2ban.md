---
title: Bloquer les échecs de connexion répétés avec fail2ban
description: Activer le jail fail2ban livré avec les paquets Linux, sous Docker, ou sur le reverse-proxy placé devant Meshloom.
level: deep
order: 24.5
---

Meshloom ralentit déjà quelqu’un qui devine un mot de passe ([Les délais de connexion](/docs/deep/access/#les-délais-de-connexion)). [fail2ban](https://github.com/fail2ban/fail2ban) ajoute un blocage au niveau du pare-feu : une adresse qui échoue cinq fois en dix minutes ne peut plus joindre le port 8000 du tout, pendant une heure, puis plus longtemps à chaque retour. C’est facultatif, et désactivé tant que vous ne l’activez pas.

Chaque mot de passe, code, passkey, jeton ou code d’installation refusé écrit une ligne commençant par `AUTH_FAIL` dans le journal de Meshloom, avec l’adresse d’où il vient. fail2ban surveille ces lignes. Rien dedans n’identifie le compte : seulement un code court qui change à chaque redémarrage.

## Ce que les paquets Linux installent

Les paquets `.deb` et `.rpm` apportent deux fichiers, tous deux conservés à la mise à jour (une modification de votre part n’est jamais écrasée) :

- `/etc/fail2ban/filter.d/meshloom.conf` : comment lire la ligne.
- `/etc/fail2ban/jail.d/meshloom.conf` : le jail, **désactivé** (`enabled = false`). Il lit le journal systemd de `meshloom.service`, bannit après 5 échecs en 10 minutes pour 1 heure, double le bannissement à chaque retour de l’adresse (une semaine au plus) et couvre le port 8000.

fail2ban lui-même n’est que suggéré : Meshloom fonctionne sans, et installer le paquet ne l’installe pas.

## L’activer (paquet Linux)

1. Installez fail2ban : `sudo apt install fail2ban` ou `sudo dnf install fail2ban` (sous Fedora et RHEL, il vient d’EPEL).
2. Modifiez `/etc/fail2ban/jail.d/meshloom.conf` et remplacez `enabled = false` par `enabled = true`. Si vous avez changé le port avec `--port` dans le service, changez aussi `port = 8000`.
3. `sudo fail2ban-client reload`, puis `sudo fail2ban-client status meshloom` montre le jail.
4. Pour tester, ratez 5 connexions depuis un autre ordinateur, puis regardez `sudo fail2ban-client status meshloom` : son adresse est dans la liste. `sudo fail2ban-client set meshloom unbanip <adresse>` lève le bannissement.

Pour ne jamais bannir une de vos adresses, ajoutez `ignoreip = 127.0.0.1/8 ::1 192.168.1.0/24` sous `[meshloom]` (votre propre réseau).

## Derrière un reverse-proxy sur la même machine

Listez d’abord le proxy dans les [Proxies de confiance](/docs/deep/https/#proxies-de-confiance). Tant que ce n’est pas fait, chaque visiteur ressemble au proxy : Meshloom marque la ligne `proxied=1` et le filtre **l’ignore**, car bannir votre propre proxy enfermerait tout le monde dehors. Une fois le proxy déclaré, la ligne porte l’adresse du visiteur. Un bannissement par le pare-feu de cette machine ne bloque alors le trafic de ce visiteur que si le proxy transmet l’adresse du visiteur lui-même, ce que ne fait pas un reverse-proxy normal : le pare-feu voit le proxy comme source. Pour bloquer au niveau du proxy, voir la section suivante.

## Docker

Le conteneur écrit sur sa sortie d’erreur, donc le jail change à deux endroits : où il lit et où il bloque.

- **Où il lit.** Le pilote `json-file`, par défaut, enveloppe chaque ligne dans du JSON, que le filtre ne lit pas. Envoyez plutôt les journaux du conteneur au journal systemd. Dans `docker-compose.yml`, sous le service Meshloom :

  ```yaml
  logging:
    driver: journald
    options:
      tag: meshloom
  ```

  Lancez `docker compose up -d`, puis mettez `journalmatch = CONTAINER_NAME=meshloom` dans le jail (le nom de votre conteneur, tel que l’affiche `docker ps`) à la place de `_SYSTEMD_UNIT=meshloom.service`.
- **Où il bloque.** Docker publie un port avant que les chaînes habituelles du pare-feu voient le trafic : le bannissement par défaut n’aurait aucun effet. Ajoutez au jail :

  ```ini
  chain = DOCKER-USER
  port = 8000
  ```

  avec `port` réglé sur le port que Docker publie.

Meshloom doit aussi voir l’adresse propre de chaque visiteur. Dans un conteneur sans `network_mode: host` et sans proxy déclaré, il voit l’adresse de Docker pour tout le monde et marque la ligne `proxied=1` : rien n’est banni. [Les délais de connexion](/docs/deep/access/#les-délais-de-connexion) donne les deux issues (réseau hôte, ou **Les adresses identifient les clients**).

## Un reverse-proxy sur une autre machine

fail2ban sur la machine de Meshloom ne voit que le proxy. Bannissez plutôt sur la machine du proxy, d’après son propre journal d’accès : une connexion ratée répond `401` à `POST /api/auth/login`. Pour nginx avec le journal `combined` par défaut :

```ini
# /etc/fail2ban/filter.d/meshloom-nginx.conf
[Definition]
failregex = ^<ADDR> \S+ \S+ \[[^\]]+\] "POST /api/auth/login HTTP/[\d.]+" 401\b
datepattern = {^LN-BEG}%%d/%%b/%%Y:%%H:%%M:%%S %%z
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
- **Tailscale ou WireGuard seulement.** Seuls vos propres appareils se connectent : rien à bannir.

## À lire aussi

- [Les délais de connexion et la ligne `AUTH_FAIL`](/docs/deep/access/#les-délais-de-connexion)
- [Accès à distance par vos propres moyens](/docs/deep/remote-self-hosted/)
- [Sécurité](/docs/deep/security/)
