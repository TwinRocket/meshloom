---
title: Meshloom Remote
description: Ouvrir votre Meshloom de partout par le relais Meshloom Remote : l’activer, autoriser des appareils, ce que voit le relais et ce que veut dire chaque état.
level: deep
order: 24.2
---

Meshloom Remote ouvre votre instance de n’importe où, depuis n’importe quel appareil (téléphone, tablette, ordinateur), sans ouvrir de port sur votre box et sans compte TwinRocket. Il est **désactivé par défaut** : tant qu’il l’est, Meshloom n’envoie rien au relais. Tout se passe dans **Réglages → Accès à distance**.

## L’activer

Un administrateur appuie sur **Activer Meshloom Remote**. L’instance enregistre sa propre clé auprès du relais et reçoit sa propre adresse, comme `https://k3xwq2m7ta.meshloom.link`. Personne ne peut ouvrir cette adresse tant que vous n’avez pas autorisé un appareil.

Si les plugins tournent sans bac à sable sur cette machine (hooks seulement), la fenêtre demande d’abord de confirmer : une fois l’instance joignable depuis internet, quelqu’un qui entre dans un compte administrateur pourrait installer ou régler un plugin qui exécute du code sur la machine. Protégez chaque administrateur par une clé d’accès (passkey) ou la double authentification, et supprimez les plugins inutiles (**Réglages → Extensions**). L’**avis de sécurité des accès**, en haut de **Utilisateurs et accès**, le rappelle tant que Remote est activé.

## Autoriser un appareil

On autorise chaque appareil une seule fois, pour son propre compte :

- **Par QR code**, sur le réseau local : appuyez sur **Afficher le QR code** et scannez-le avec l’appareil. Le code ne sert qu’une fois et expire au bout de 15 minutes. Il donne accès à votre compte : ne le partagez pas et n’en publiez pas de capture d’écran. Par le relais, le QR code ne s’affiche jamais.
- **Par demande d’accès**, pour un appareil sans appareil photo ou loin de chez vous : ouvrez l’adresse de l’instance sur cet appareil et appuyez sur **Demander l’accès**. Il affiche un code ; saisissez-le dans **Demandes d’accès** sur un appareil que vous utilisez déjà. N’approuvez qu’un appareil que vous avez en main et dont l’écran affiche ce code, jamais un code que quelqu’un vous envoie, vous lit ou vous demande d’approuver.

Connectez-vous ensuite sur l’appareil comme d’habitude. Par le relais, il faut **toujours se connecter** : l’accès sans connexion sur le réseau local et les réseaux de confiance ne s’appliquent jamais ([Comptes et accès](/docs/deep/access/#les-réseaux-de-confiance)). Après plusieurs connexions échouées depuis un même réseau, le relais affiche d’abord une courte page de vérification ([Délais de connexion](/docs/deep/access/#les-délais-de-connexion)).

**Appareils autorisés** liste vos appareils (un administrateur voit ceux de tous les comptes). Révoquez un appareil perdu ou inutilisé ; **Révoquer tous les appareils** les coupe tous d’un coup. Un navigateur garde son accès 90 jours, une application ou un script 365 jours.

## Ce que voit le relais

Le relais est exploité par TwinRocket. Il termine la connexion chiffrée avant de transmettre le trafic à votre instance : il pourrait donc techniquement tout lire, y compris votre mot de passe quand vous vous connectez, vos messages et vos réglages. Il bloque tout appareil que vous n’avez pas autorisé.

Pour qu’un mot de passe vu par le relais ne suffise pas à entrer, utilisez une clé d’accès (passkey) ou activez la double authentification (**Réglages → Utilisateurs et accès**, voir [Passkeys](/docs/deep/access/#les-passkeys) et [Double authentification](/docs/deep/access/#la-double-authentification)).

Quand Remote est activé, Meshloom indique aussi au relais sa version, son type d’installation, et s’il fait tourner des plugins avec quel niveau d’isolation. Tant que Remote est désactivé, rien n’est envoyé. Les conditions et la politique de confidentialité du relais disent ce qu’il conserve.

## États

Le badge sous **État** dit où en est la liaison :

| Badge | Sens et quoi faire |
|---|---|
| Désactivé | Remote est désactivé ; l’instance n’est joignable que sur le réseau local. |
| Non enregistrée | Remote est activé mais le relais n’a pas enregistré l’instance. Appuyez sur **Réessayer** ; si l’échec se répète, cette machine n’arrive peut-être pas à joindre le relais. |
| Connexion, Connectée | La liaison s’établit, ou est établie : les appareils autorisés ouvrent l’adresse affichée. |
| Hors ligne | La liaison est coupée. Meshloom se reconnecte seul, en espaçant les essais (jusqu’à 5 minutes). Vérifiez la connexion internet de cette machine. |
| Inconnue du relais | Le relais a oublié l’instance et le nouvel enregistrement a échoué. Désactivez puis réactivez Remote ; si cela ne suffit pas, remplacez la clé. |
| En pause | TwinRocket a mis cette instance en pause. Meshloom réessaie seul après 15 à 20 minutes. |
| Remplacée | Une autre installation utilise la clé de cette instance, souvent une copie restaurée depuis une sauvegarde. Une clé ne sert qu’à une installation : remplacez la clé ici, puis autorisez de nouveau chaque appareil. |
| Bannie | TwinRocket a banni l’instance ; Meshloom n’essaie plus. Contactez TwinRocket si vous pensez que c’est une erreur. |
| Mise à jour requise | Le relais n’accepte plus cette version : mettez Meshloom à jour, Remote se reconnecte ensuite. |
| Relais en pause | Le service est en pause pour toutes les instances (maintenance ou capacité). Meshloom réessaie après 5 à 15 minutes. |

L’empreinte de la clé identifie l’instance auprès du relais ; elle ne change que si vous remplacez la clé.

## Désactiver, quitter, remplacer la clé

- **Désactiver** : plus rien n’est envoyé au relais et vos appareils ne peuvent plus ouvrir l’instance de l’extérieur. L’adresse et les appareils autorisés sont conservés pour la prochaine activation.
- **Quitter Meshloom Remote** : le relais oublie l’adresse de l’instance et Remote se désactive. La clé est conservée : réactivez Remote dans les 30 jours pour retrouver la même adresse.
- **Remplacer la clé** : une nouvelle clé et une nouvelle adresse ; tous les appareils autorisés et les demandes en attente sont oubliés, il faut autoriser de nouveau chaque appareil.

Une sauvegarde restaurée ne ramène jamais Remote : l’instance restaurée repart avec Remote désactivé, sans adresse ni appareil.

Pour faire mener votre propre domaine à l’adresse, voir [Votre propre domaine pour Meshloom Remote](/docs/deep/remote-domain/). Pour joindre Meshloom avec vos propres outils, voir [Accès à distance par vos propres moyens](/docs/deep/remote-self-hosted/).
