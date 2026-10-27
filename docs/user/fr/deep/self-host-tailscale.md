---
title: Tailscale serve devant Meshloom
description: Joindre Meshloom en HTTPS depuis vos propres appareils avec tailscale serve, et ce qu’il faut déclarer dans Meshloom (adresse publique, proxy de confiance).
level: deep
order: 24.2
---

`tailscale serve` publie Meshloom uniquement pour les appareils de votre propre tailnet, en HTTPS, avec un certificat pour votre nom MagicDNS. Rien n’est ouvert sur votre box. Cette page est la version courte pour un Meshloom qui tourne sur la même machine que Tailscale. Les autres voies (Cloudflare Tunnel, WireGuard) et les raisons de chaque réglage sont dans [Accès à distance par vos propres moyens](/docs/deep/remote-self-hosted/).

Depuis la 5.0, chaque page et chaque route d’API demande un compte : cela n’ouvre donc jamais Meshloom sans connexion ([Comptes et accès](/docs/deep/access/)).

## Les étapes

1. Dans la console d’administration de Tailscale, activez MagicDNS et les certificats HTTPS pour votre tailnet.
2. Sur la machine de Meshloom, publiez le port 8000 :

   ```bash
   tailscale serve --bg --https=443 http://127.0.0.1:8000
   ```

   Votre nom est `nom-de-la-machine.nom-du-tailnet.ts.net` (`tailscale serve status` l’affiche).
3. Dans Meshloom, en administrateur :
   - **Réglages → Utilisateurs et accès → Adresses publiques** : ajoutez `https://nom-de-la-machine.nom-du-tailnet.ts.net`. Cela déclare le nom MagicDNS comme le vôtre, pour que la [vérification du Host](/docs/deep/https/#les-noms-auxquels-meshloom-répond) l’accepte (à partir de la 5.1, elle refuse les noms non déclarés). N’ajoutez qu’un nom que vous avez créé.
   - **Réglages → Configuration locale → Proxies de confiance** : ajoutez `127.0.0.1`. `tailscale serve` se connecte depuis la machine elle-même et n’indique l’adresse du visiteur et le HTTPS que dans `X-Forwarded-For` et `X-Forwarded-Proto`, que Meshloom ne croit que d’une adresse listée. Sans cela, tous les appareils ressemblent à `127.0.0.1` et le cookie de session est le cookie simple.
4. Ouvrez `https://nom-de-la-machine.nom-du-tailnet.ts.net` depuis un autre appareil du tailnet et connectez-vous. La page de connexion ne doit pas avertir que Meshloom a vu du HTTP simple. Si elle le fait, les Proxies de confiance manquent ou sont faux.

Si Meshloom tourne sous Docker, l’adresse qu’il voit dépend de la façon dont le port 8000 est publié : lisez [HTTPS, Proxies de confiance](/docs/deep/https/#proxies-de-confiance) avant de lister quoi que ce soit, et ne listez jamais une passerelle ni un réseau Docker.

## Rester prudent

- **Laissez Accès sans connexion sur le réseau local désactivé** (**Réglages → Utilisateurs et accès → Réseaux de confiance**) et la liste de règles vide. Il est désactivé par défaut. Derrière `tailscale serve`, une règle ne s’applique jamais (la requête passe par un proxy), mais l’interrupteur doit rester désactivé dès que Meshloom est joint par un proxy, ou par une redirection de port de la box qui réécrit l’adresse source (SNAT) : les visiteurs d’Internet sembleraient alors locaux. Une règle sur `100.64.0.0/10` couvrirait tout votre tailnet. Voir [Les réseaux de confiance](/docs/deep/access/#les-réseaux-de-confiance).
- **N’utilisez pas `tailscale funnel`.** Il publie Meshloom sur tout Internet, et vos comptes deviennent la seule barrière.
- **Le proxy radio n’a pas de connexion.** Si vous activez **Réglages > Proxy**, il écoute sur `0.0.0.0` (port 5051 par défaut) et tout appareil du tailnet qui joint cette machine peut utiliser votre radio, quoi que publie `tailscale serve`. Laissez-le désactivé, ou restreignez le port avec les ACL de Tailscale ([Sécurité](/docs/deep/security/#proxy-radio)).
- **Échecs de connexion répétés.** Le [bannissement intégré](/docs/deep/access/#les-délais-de-connexion) de Meshloom est actif par défaut. [fail2ban](/docs/deep/fail2ban/) n’apporte rien ici : seuls vos propres appareils se connectent.
