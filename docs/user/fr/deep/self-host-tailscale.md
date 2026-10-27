---
title: Tailscale serve devant Meshloom
description: Joindre Meshloom en HTTPS depuis vos propres appareils avec tailscale serve, et ce qu’il faut déclarer dans Meshloom (adresse publique, proxy de confiance).
level: deep
order: 24.2
---

`tailscale serve` publie Meshloom uniquement pour les appareils de votre propre tailnet, en HTTPS, avec un certificat pour votre nom MagicDNS. Rien n’est ouvert sur votre box. Cette page est la version courte pour un Meshloom qui tourne sur la même machine que Tailscale. Les autres voies (Cloudflare Tunnel, WireGuard) et les raisons de chaque réglage sont dans [Accès à distance par vos propres moyens](/docs/deep/remote-self-hosted/).

*Non testé de bout en bout.*

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

Si Meshloom tourne sous Docker, l’adresse qu’il voit dépend de la façon dont le port 8000 est publié, et vous ne devez jamais lister une passerelle ni un réseau Docker ([HTTPS, Proxies de confiance](/docs/deep/https/#proxies-de-confiance)). Deux options, toutes deux *non testées de bout en bout* : sous Linux, donnez au service Meshloom `network_mode: host`, pour que `tailscale serve` sur l’hôte joigne `127.0.0.1:8000` et que 127.0.0.1 soit la bonne entrée ; ou lancez Tailscale en conteneur annexe qui partage le réseau de Meshloom (`network_mode: service:meshloom` sur le service Tailscale), où `tailscale serve` joint aussi `127.0.0.1:8000` et où 127.0.0.1 est encore l’entrée.

## Rester prudent

- **Laissez Accès sans connexion sur le réseau local désactivé** (**Réglages → Utilisateurs et accès → Réseaux de confiance**) et la liste de règles vide. Il est désactivé par défaut. Derrière `tailscale serve`, une règle ne s’applique jamais (la requête passe par un proxy), mais l’interrupteur doit rester désactivé dès que Meshloom est joint par un proxy, ou par une redirection de port de la box qui réécrit l’adresse source (SNAT) : les visiteurs d’Internet sembleraient alors locaux. Une règle sur `100.64.0.0/10` couvrirait tout votre tailnet. Voir [Les réseaux de confiance](/docs/deep/access/#les-réseaux-de-confiance).
- **N’utilisez pas `tailscale funnel`.** Il publie Meshloom sur tout Internet, et vos comptes deviennent la seule barrière.
- **Le proxy radio n’a pas de connexion.** Si vous activez **Réglages → Proxy**, il écoute sur `0.0.0.0` (port 5051 par défaut) et tout appareil du tailnet qui joint cette machine peut utiliser votre radio, quoi que publie `tailscale serve`. Laissez-le désactivé, ou restreignez le port avec les ACL de Tailscale ([Sécurité](/docs/deep/security/#proxy-radio)).
- **Échecs de connexion répétés : vos propres appareils peuvent être bannis.** Avec `127.0.0.1` dans les Proxies de confiance, Meshloom voit l’adresse tailnet de chaque appareil (`100.64.0.0/10`, ou `fd7a:115c:a1e0::/48` en IPv6), et [le bannissement intégré](/docs/deep/fail2ban/#les-lignes-que-compte-le-blocage-intégré) en bannit un après 10 connexions refusées, plus longtemps à chaque retour, comme n’importe quel visiteur. Sans `127.0.0.1` listé, tous les appareils partagent l’adresse de boucle locale : rien n’est banni et le [délai de connexion](/docs/deep/access/#les-délais-de-connexion) d’un appareil frappe tous les autres. Si un de vos appareils est banni, levez le bannissement avec **Débannir** depuis un autre appareil ou `meshloom-admin unban 100.x.y.z` sur la machine ; pour ne jamais bannir votre tailnet, ajoutez `100.64.0.0/10` et `fd7a:115c:a1e0::/48` à la liste blanche (les délais s’appliquent toujours). [fail2ban](/docs/deep/fail2ban/) n’apporte rien : seuls vos propres appareils se connectent, donnez donc un mot de passe solide à chaque compte.
