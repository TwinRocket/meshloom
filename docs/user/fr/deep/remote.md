---
title: Meshloom Remote
description: "Ouvrir votre Meshloom de partout par le relais Meshloom Remote : l’activer, autoriser des appareils, ce que voit le relais et ce que veut dire chaque état."
level: deep
order: 24.2
---

Meshloom Remote ouvre votre instance de n’importe où, depuis n’importe quel appareil (téléphone, tablette, ordinateur), sans ouvrir de port sur votre box et sans compte TwinRocket. Il est **désactivé par défaut** : tant qu’il l’est, Meshloom n’envoie rien au relais. Tout se passe dans **Réglages → Accès à distance**.

## L’activer

Un administrateur appuie sur **Activer Meshloom Remote**. L’instance enregistre sa propre clé auprès du relais, avec une preuve de Meshloom Community qu’une autre station a entendu votre radio récemment (voir [Preuve pour Remote et le partage](/docs/deep/community/#preuve-pour-remote-et-le-partage)), et reçoit sa propre adresse, comme `https://k3xwq2m7ta.meshloom.link`. Personne ne peut ouvrir cette adresse tant que vous n’avez pas autorisé un appareil.

Si les plugins tournent sans bac à sable sur cette machine (hooks seulement), la fenêtre demande d’abord de confirmer : une fois l’instance joignable depuis internet, quelqu’un qui entre dans un compte administrateur pourrait installer ou régler un plugin qui exécute du code sur la machine. Protégez chaque administrateur par la double authentification, et supprimez les plugins inutiles (**Réglages → Extensions**). L’**avis de sécurité des accès**, en haut de **Utilisateurs et accès**, le rappelle tant que Remote est activé.

## Autoriser un appareil

On autorise chaque appareil une seule fois, pour son propre compte :

- **Par QR code**, sur le réseau local : appuyez sur **Afficher le QR code** et scannez-le avec l’appareil. Le code ne sert qu’une fois et expire au bout de 15 minutes. Il donne accès à votre compte : ne le partagez pas et n’en publiez pas de capture d’écran. Par le relais, le QR code ne s’affiche jamais.
- **Par demande d’accès**, pour un appareil sans appareil photo ou loin de chez vous : ouvrez l’adresse de l’instance sur cet appareil et appuyez sur **Demander l’accès**. Il affiche un code ; saisissez-le dans **Demandes d’accès** sur un appareil que vous utilisez déjà. N’approuvez qu’un appareil que vous avez en main et dont l’écran affiche ce code, jamais un code que quelqu’un vous envoie, vous lit ou vous demande d’approuver.

Connectez-vous ensuite sur l’appareil comme d’habitude. Par le relais, il faut **toujours se connecter** : l’accès sans connexion sur le réseau local et les réseaux de confiance ne s’appliquent jamais ([Comptes et accès](/docs/deep/access/#les-réseaux-de-confiance)). Après plusieurs connexions échouées depuis un même réseau, le relais affiche d’abord une courte page de vérification ([Délais de connexion](/docs/deep/access/#les-délais-de-connexion)).

**Appareils autorisés** liste vos appareils (un administrateur voit ceux de tous les comptes). Révoquez un appareil perdu ou inutilisé ; **Révoquer tous les appareils** les coupe tous d’un coup. Un navigateur garde son accès tant que vous vous y connectez et l’utilisez au moins une fois tous les 90 jours ; passé ce délai, il faut l’autoriser de nouveau. Une application ou un script garde son accès 365 jours.

Un appareil appartient au compte qui l’a autorisé. Supprimer un compte coupe tous ses appareils, y compris ceux qu’il a approuvés pour quelqu’un d’autre : cette personne redemande alors l’accès.

## Ce que voit le relais

Le relais est exploité par TwinRocket. Il termine la connexion chiffrée avant de transmettre le trafic à votre instance : il pourrait donc techniquement tout lire, y compris votre mot de passe quand vous vous connectez, vos messages et vos réglages. Il bloque tout appareil que vous n’avez pas autorisé.

Seule la double authentification empêche qu’un mot de passe vu par le relais suffise à entrer (**Réglages → Utilisateurs et accès**, voir [Double authentification](/docs/deep/access/#la-double-authentification)). Une [clé d’accès (passkey)](/docs/deep/access/#les-passkeys) évite de saisir le mot de passe, mais celui-ci reste valable.

Quand Remote est activé, Meshloom indique aussi au relais sa version, son type d’installation, et s’il fait tourner des plugins avec quel niveau d’isolation. Tant que Remote est désactivé, rien n’est envoyé. Il n’y a pas de compte sur le relais. Pour chaque instance, il conserve :

- son enregistrement : la clé, l’adresse, la version de Meshloom, le type d’installation, si des plugins tournent et avec quelle isolation ;
- l’état des appareils autorisés (pas leurs noms) ;
- des chiffres d’usage par instance et par mois : requêtes, trafic, durée de connexion de l’instance, erreurs. Ils ne contiennent aucun contenu, et le relais s’en sert pour appliquer la limite mensuelle ci-dessous et maîtriser ses propres coûts ;
- une trace des actions de l’opérateur sur une instance (une pause, un bannissement, une clé retirée), avec la date ;
- l’adresse de l’[e-mail d’alerte](#e-mail-dalerte), si vous en donnez une.

Une demande d’accès vit 10 minutes et une connexion échouée est retenue 15 minutes, chacune sous une étiquette brouillée de l’adresse réseau, jamais l’adresse elle-même. Les journaux du relais ne contiennent ni message, ni adresse, ni adresse web, ni clé ; les chiffres d’usage sont gardés quelques mois. **Quitter Meshloom Remote** supprime du relais l’enregistrement et l’état des appareils ; la clé et l’adresse vous restent réservées 30 jours (voir plus bas), et les chiffres d’usage expirent d’eux-mêmes.

## Ce que Remote isole, et ce qu’il n’isole pas

L’adresse de chaque instance se termine par `meshloom.link`, et les navigateurs traitent toutes ces adresses comme un seul site. Le relais compense l’essentiel.

Ce qui est isolé :

- **L’accès à votre instance.** Une page d’une autre instance peut faire un lien vers la vôtre, rien de plus : tout ce qu’elle tente d’autre depuis le navigateur est refusé.
- **Les cookies.** Votre session et l’accès de votre appareil utilisent des cookies liés à l’adresse exacte. Une page d’une autre instance peut quand même laisser dans votre navigateur des cookies pour tout le domaine, mais le relais ne transmet que ceux liés à l’adresse : ils n’atteignent donc jamais votre instance. Le relais retire aussi des réponses d’une instance tout cookie destiné aux autres.
- **Les passkeys.** Une clé d’accès créée par Remote ne fonctionne qu’à cette adresse. Après **Remplacer la clé**, l’adresse change : créez-en une nouvelle.

Ce qui ne l’est pas :

- **Les gestionnaires de mots de passe.** Certains traitent tout `meshloom.link` comme un seul site et peuvent donc proposer votre mot de passe sur la page d’une autre instance. Vérifiez l’adresse avant de le remplir et, si votre gestionnaire le permet, faites correspondre l’entrée enregistrée à l’adresse exacte. Seule la double authentification empêche qu’un mot de passe seul suffise ; une clé d’accès évite de le saisir, mais il reste valable.
- **La place pour les cookies dans le navigateur.** Une page d’une autre instance peut remplir la place que votre navigateur accorde à `meshloom.link`. Au pire, vous êtes déconnecté, le navigateur perd son accès, ou les pages refusent de se charger parce que la requête est trop volumineuse. Cela ne donne à cette page aucun accès à votre instance.

Pour remettre les cookies à zéro, supprimez ceux de `meshloom.link` et de ses sous-domaines dans les réglages du navigateur, puis autorisez de nouveau l’appareil et révoquez l’ancienne entrée dans **Appareils autorisés**. Cela vaut pour toutes les instances Meshloom Remote ouvertes dans ce navigateur. Pour une seule instance, la page affichée à un navigateur sans accès a un bouton **Oublier cet appareil**. Il retire seulement à ce navigateur son accès à l’instance, pas votre session Meshloom.

## États

Le badge sous **État** dit où en est la liaison :

| Badge | Sens et quoi faire |
|---|---|
| Désactivé | Remote est désactivé ; l’instance n’est joignable que sur le réseau local. |
| Non enregistrée | Remote est activé mais le relais n’a pas enregistré l’instance. Appuyez sur **Réessayer** ; si l’échec se répète, cette machine n’arrive peut-être pas à joindre le relais. Quand Community n’a pas pu donner sa preuve, la raison s’affiche sous le badge : pour **Aucune autre station n’a entendu cette radio récemment**, envoyez une annonce flood, puis réessayez quelques minutes plus tard ; pour **Meshloom Community ne peut pas garantir cette radio pour l’instant**, réessayez plus tard, et si ce Meshloom atteint la radio par un [proxy radio](/docs/deep/radio/#proxy-radio), branchez la radio directement : Community a besoin de la clé de la radio sur cette instance. |
| Connexion, Connectée | La liaison s’établit, ou est établie : les appareils autorisés ouvrent l’adresse affichée. |
| Hors ligne | La liaison est coupée. Meshloom se reconnecte seul, en espaçant les essais (jusqu’à 5 minutes). Vérifiez la connexion internet de cette machine. |
| Inconnue du relais | Le relais a oublié l’instance et le nouvel enregistrement a échoué. Désactivez puis réactivez Remote ; si cela ne suffit pas, remplacez la clé. |
| Quota mensuel atteint | L’instance a dépassé son [quota mensuel](#limites). Remote revient le 1er du mois suivant (UTC) : Meshloom réessaie seul toutes les 15 à 20 minutes. Le réseau local n’est pas touché : Meshloom continue de fonctionner chez vous. |
| En pause | TwinRocket a mis l’instance en pause. Meshloom réessaie seul après 15 à 20 minutes. Le réseau local n’est pas touché. |
| Remplacée | Une autre installation utilise la clé de cette instance, souvent une [copie du dossier de données](#les-sauvegardes-et-la-clé-remote). Une clé ne sert qu’à une installation : remplacez la clé ici, puis autorisez de nouveau chaque appareil. |
| Bannie | TwinRocket a banni l’instance ; Meshloom n’essaie plus. [Écrivez à TwinRocket](#aide-et-signalements) si vous pensez que c’est une erreur. |
| Mise à jour requise | Le relais n’accepte plus cette version : mettez Meshloom à jour, Remote se reconnecte ensuite. |
| Relais en pause | Le service est en pause pour toutes les instances (maintenance ou capacité). Meshloom réessaie après 5 à 15 minutes. |

L’empreinte de la clé identifie l’instance auprès du relais ; elle ne change que si vous remplacez la clé.

## Limites

| Limite | Valeur |
|---|---|
| Trafic par instance et par mois | 5 Go, compté dans les deux sens (requêtes, réponses et connexions en direct) |
| Requêtes par instance et par mois | 500 000 (la connexion en direct d’un navigateur compte pour une) |
| Remise à zéro | Le 1er de chaque mois, en UTC |
| Au-delà | L’instance est en pause jusqu’au mois suivant : toute requête est refusée, quel que soit l’appareil. Meshloom affiche **Quota mensuel atteint**, et un [e-mail d’alerte](#e-mail-dalerte) confirmé reçoit un avertissement à 80 % et un avis à la mise en pause. Le réseau local continue de fonctionner |
| Un envoi | 100 Mio ; au-delà, le relais répond « requête trop volumineuse » |
| Requêtes par instance | 1 200 par minute ; au-delà, le relais répond « trop de requêtes » un instant |
| Connexions échouées | Après quelques-unes depuis un même réseau, une page de vérification d’abord (voir plus haut) |
| Demande d’accès | Valable 10 minutes ; 5 au plus en attente à la fois pour une instance |
| QR code | Un seul usage, 15 minutes |
| Appareil autorisé | 90 jours après sa dernière utilisation pour un navigateur, 365 jours pour une application ou un script |
| Adresse après **Quitter** | Conservée 30 jours pour la même clé |

Ce sont les limites de l’offre gratuite, les mêmes pour toutes les instances. L’usage normal de l’interface reste très en dessous ; un script qui interroge toutes les quelques secondes, ou de gros médias, sont ce qui les atteint.

## E-mail d’alerte

Un administrateur peut donner une adresse dans **Réglages → Accès à distance → E-mail d’alerte**. Elle reçoit au plus, chaque mois, un avertissement à 80 % du quota mensuel et un avis quand l’instance est mise en pause pour dépassement.

- **Confirmer** : le relais envoie un e-mail avec un lien pour confirmer et un lien pour arrêter. Rien d’autre n’est envoyé tant que l’adresse n’est pas confirmée ; le lien est valable 24 heures.
- **Changer ou retirer** : saisissez une autre adresse pour la remplacer (elle doit être confirmée à nouveau), appuyez sur **Retirer l’adresse**, ou suivez le lien d’arrêt de n’importe quel e-mail. Une adresse peut être définie 3 fois par jour au plus.
- **Ce qui est conservé** : l’adresse reste sur le relais et ne sert qu’à ces e-mails. Meshloom ne la garde pas et le relais ne la réaffiche jamais : la page dit seulement si une adresse attend sa confirmation ou est confirmée. **Quitter Meshloom Remote** la supprime. Remplacer la clé la supprime aussi tant que Remote et Community sont activés et que le relais l’accepte, donc pas si TwinRocket a gelé ou banni l’instance. Sinon elle reste sur le relais, et seul un lien d’arrêt encore valable d’un e-mail reçu peut la retirer (24 heures dans l’e-mail de confirmation, 30 jours dans un e-mail d’alerte). Remote ou Community désactivé, activez-les et retirez l’adresse avant de remplacer la clé.

Sans adresse confirmée, la pause a lieu quand même, sans e-mail. Aucun e-mail n’est envoyé quand une instance passe hors ligne.

## Désactiver, quitter, remplacer la clé

- **Désactiver** : plus rien n’est envoyé au relais et vos appareils ne peuvent plus ouvrir l’instance de l’extérieur. L’adresse et les appareils autorisés sont conservés pour la prochaine activation.
- **Quitter Meshloom Remote** (administrateurs) : le relais supprime l’enregistrement de l’instance et Remote se désactive. La clé reste sur cette machine et l’adresse vous reste réservée 30 jours : réactivez Remote dans ce délai pour retrouver la même adresse, passé ce délai une nouvelle est attribuée. Remote et Community doivent être activés, car Meshloom doit joindre le relais. Le relais refuse tant que l’instance est en pause (quota mensuel compris), gelée ou bannie : désactivez plutôt Remote, et quittez une fois la pause terminée.
- **Remplacer la clé** : une nouvelle clé et une nouvelle adresse ; tous les appareils autorisés et les demandes en attente sont oubliés, il faut autoriser de nouveau chaque appareil.

Pour faire mener votre propre domaine à l’adresse, créez de votre côté une redirection d’URL vers `https://<nom>.meshloom.link` : un vrai `CNAME` ne peut pas fonctionner (erreur 1014). Voir [Votre propre domaine pour Meshloom Remote](/docs/deep/remote-domain/). Pour joindre Meshloom avec vos propres outils, voir [Accès à distance par vos propres moyens](/docs/deep/remote-self-hosted/).

## Les sauvegardes et la clé Remote

La clé Remote est conservée dans la base de données de l’instance.

- **Les sauvegardes de Meshloom** (la sauvegarde complète, **Télécharger meshcore.db**) ne la contiennent pas. Après en avoir restauré une, l’instance a une nouvelle clé et Remote est désactivé, sans adresse ni appareil : réactivez-le et autorisez de nouveau chaque appareil.
- **Toute autre copie du dossier de données** (une sauvegarde Home Assistant, une sauvegarde de volume ou de disque, un fichier copié à la main) contient la clé : gardez-la aussi privée que l’instance. La restaurer ramène la même clé et la même adresse. Si deux copies tournent en même temps, elles s’éjectent du relais l’une l’autre et Meshloom affiche **Remplacée**. Si une telle copie a pu sortir, appuyez sur **Remplacer la clé**.
- **Restaurez depuis le réseau local.** Restaurer une sauvegarde désactive normalement Remote, ce qui coupe la connexion que vous utiliseriez. Une sauvegarde complète est limitée à 64 Mio et le relais refuse tout envoi de plus de 100 Mio. Une copie de la base, qui peut être plus grosse, se remet en place sur la machine elle-même ([Base de données et mises à jour](/docs/deep/install-paths/#base-de-données-et-mises-à-jour)). La télécharger par le relais compte dans le trafic mensuel.

## Sur l’add-on Home Assistant

Remote fonctionne de la même façon sur l’add-on. La liaison part de l’add-on, donc `8000/tcp` peut rester non publié. Ce qui change :

- **La connexion.** Un compte Home Assistant n’a ni mot de passe, ni second facteur, ni clé d’accès : il ne peut pas se connecter par le relais. Créez un compte Meshloom avec mot de passe dans **Réglages → Utilisateurs et accès**, et connectez-vous avec lui sur vos appareils à distance. Vous pouvez autoriser un appareil depuis la barre latérale, puis vous y connecter avec ce compte.
- **Les extensions.** Remote rend Meshloom joignable depuis l’extérieur de Home Assistant. Si vous n’utilisez pas d’extensions, activez `disable_plugins` dans la configuration de l’add-on.
- **Les mises à jour.** Pour **Mise à jour requise**, mettez à jour depuis la page de l’add-on dans Home Assistant.
- **Les sauvegardes.** Une sauvegarde Home Assistant contient la base de données, donc la clé (voir plus haut).

## Aide et signalements

- **Une question, un problème, ou une pause que vous pensez être une erreur :** écrivez à [contact@meshloom.app](mailto:contact@meshloom.app).
- **Un bannissement que vous pensez être une erreur :** écrivez à [ban@meshloom.app](mailto:ban@meshloom.app).
- **Une adresse qui héberge du contenu illégal, du hameçonnage ou un logiciel malveillant :** écrivez à [abuse@meshloom.app](mailto:abuse@meshloom.app), indiquée aussi sur [my.meshloom.app/abuse](https://my.meshloom.app/abuse).
- **Le service fonctionne-t-il ?** Voir [my.meshloom.app/status](https://my.meshloom.app/status).
