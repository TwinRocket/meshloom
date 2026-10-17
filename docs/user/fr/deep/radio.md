---
title: Radio, contacts et salons
description: Ce que Meshloom charge sur la radio, la portée de flood, et la largeur des sauts.
level: deep
order: 15
---

Meshloom gère les contacts et les salons stockés sur votre radio. Le serveur retient beaucoup plus de choses que la radio ne peut en contenir, et charge sur la radio l’ensemble de travail dont il a besoin.

La base de données est liée à la clé publique de votre radio. Si vous branchez une autre radio, une fenêtre s’ouvre : **Cette radio ne correspond pas à l’identité enregistrée**. **Effacer et continuer** adopte la nouvelle radio et efface les contacts et messages mesh locaux ; les salons et les réglages du serveur restent. **Annuler** laisse vos données intactes et met la connexion à cette radio en pause. Une base créée avant ce lien pose la question une fois, même pour sa radio habituelle : **Cette radio n’est pas liée à cette instance**. Choisissez **Lier sans effacer** s’il s’agit de la même radio, ou **Nouvelle radio** pour repartir de zéro. Les deux fenêtres proposent aussi **Restaurer depuis une sauvegarde**, qui rend à une radio reflashée son ancienne identité à partir d’une sauvegarde complète (voir [Sauvegarder la radio](/docs/backup/)). Voir [Premier lancement](/docs/first-run/).

## Pourquoi charger des contacts sur la radio

Une radio peut accuser réception toute seule des messages directs entrants, mais seulement si l’expéditeur est dans sa table de contacts. Meshloom lit donc cette table, charge vos favoris en premier, puis la remplit jusqu’à environ 80 % de **Contacts max sur la radio**. Quand la table atteint environ 95 % de remplissage, Meshloom la vide et la recharge entièrement.

Contacts max sur la radio se règle dans **Réglages → Radio → Messagerie**. Le baisser fait charger moins de contacts à Meshloom.

## Quand la table de contacts est pleine

La table peut se remplir à cause des annonces, ou parce qu’une autre application utilise la même radio. En Bluetooth, la lecture d’une grande table peut aussi expirer. Dans les deux cas, Meshloom vous avertit que l’accusé de réception automatique peut ne pas fonctionner pour tous les contacts. Vous pouvez :

- vider la table avec une autre application MeshCore, puis redémarrer Meshloom ;
- baisser le nombre de **Contacts max sur la radio** ;
- activer l’éviction automatique (ci-dessous) ;
- ignorer l’avertissement. **L’envoi et la réception des messages ne sont jamais affectés.**

### Éviction automatique

**Laisser la radio supprimer ses plus anciens contacts**, dans **Réglages → Radio → Avancé**, fait supprimer à la radio son plus ancien contact non favori quand la table est pleine. L’option s’applique à la prochaine connexion. La désactiver ensuite n’efface pas ce réglage dans la radio : effacez-le avec une autre application MeshCore. Ajouter des contacts n’échoue alors jamais, Meshloom peut charger des contacts même s’il n’a pas réussi à lire la table, et il n’a plus besoin d’en retirer d’abord.

Le prix à payer : les contacts chargés par Meshloom ne sont pas marqués comme favoris sur la radio, ils peuvent donc être supprimés quand une nouvelle annonce arrive. Si vous débranchez la radio de Meshloom pour l’utiliser seule, ils ne sont plus protégés.

## Salons et emplacements

La radio a un nombre limité d’emplacements de salon. Meshloom lit ce nombre sur la radio et n’en suppose aucun.

Au démarrage, il vide les emplacements de salon de la radio. Ensuite, un envoi vers un salon réutilise l’emplacement déjà chargé ; un nouveau salon prend un emplacement libre, puis remplace le moins récemment utilisé quand il n’y en a plus.

Deux exceptions à cette réutilisation :

- Avec une **radio réseau (TCP)**, chaque envoi vers un salon réécrit le salon dans la radio, car un autre programme peut utiliser la même radio.
- **Réécrire le salon avant chaque envoi**, dans **Réglages → Radio → Avancé**, fait de même sur tous les types de connexion. À utiliser si les emplacements semblent instables ou si une autre application les modifie. Chaque envoi vers un salon prend alors un peu plus de temps.

## L’audit horaire

Meshloom s’appuie sur les événements de la radio pour recevoir les messages, et fait en plus une vérification lente, une fois par heure. Elle cherche les messages restés sur la radio, et les emplacements de salon qui ne correspondent plus à ce que Meshloom attend.

S’il trouve un écart, il affiche une erreur et oublie ce qu’il croyait savoir des emplacements. Si vous voyez cette erreur, ou si des messages présents sur la radio n’arrivent jamais dans Meshloom, **Vérifier les messages toutes les 10 secondes**, dans **Réglages → Radio → Avancé**, fait tourner la vérification toutes les 10 secondes.

## Largeur des sauts : `path_hash_mode`

Chaque répéteur d’une route est identifié par quelques octets. Un identifiant plus long limite les confusions entre répéteurs, mais moins de répéteurs tiennent dans un paquet. Le réglage s’appelle **Mode de hachage de chemin**, dans l’onglet **Configuration** de **Réglages → Radio** :

| Valeur | Largeur par saut | Route la plus longue |
|--------|------------------|----------------------|
| `0` | 1 octet | 63 sauts |
| `1` | 2 octets (recommandé) | 32 sauts |
| `2` | 3 octets | 21 sauts |

À la connexion, Meshloom fait passer de 1 à 2 octets une radio qui utilise 1 octet, sauf si vous avez choisi de rester à 1 octet. Il faut pour cela que la radio indique son mode pendant la connexion. Sinon (firmware trop ancien pour le gérer, ou radio qui n’a pas répondu), un message remplace le réglage et la radio garde sa largeur actuelle : reconnectez la radio, et si le message reste, mettez à jour son firmware.

`path_len`, dans l’interface comme dans l’API, est **toujours un nombre de sauts**, jamais un nombre d’octets. Un salon peut avoir sa propre largeur (**Définir le forçage de largeur de saut** dans son en-tête), appliquée à cet envoi seulement.

## Portée de flood régionale

Un message envoyé en flood traverse tous les répéteurs à portée. Une **région** le limite : les répéteurs configurés pour cette région le relaient, et ceux configurés pour refuser les autres régions peuvent l’abandonner.

- **Réglages → Radio → Messagerie → Portée flood / région** est la région utilisée pour tous vos envois. Vide, il n’y a pas de région (flood simple).
- Un salon peut utiliser une autre région, ou aucune, avec le bouton en forme de globe dans son en-tête. Le changement s’applique aux envois vers ce salon, puis le réglage habituel est rétabli. Forcer « aucune région » quand la radio a une région par défaut demande un firmware en version 12 ou plus récente.

Pour les messages reçus, la région n’est pas écrite en clair dans le paquet : c’est un code calculé avec la clé de la région. Meshloom essaie chaque nom de **Régions connues (pour le décodage)** pour trouver celui qui correspond. Une région absente de la liste laisse un message marqué comme régional mais sans nom. Quand vous modifiez la liste, Meshloom ré-étiquette les messages dont le paquet est encore stocké.

**Découvrir les régions** demande aux répéteurs proches quelles régions ils relaient, et propose de les ajouter à la liste. Seuls les répéteurs à portée directe répondent, et seules les régions qu’ils autorisent sont signalées.

## Routage des messages directs

Meshloom choisit la route dans cet ordre :

1. une route que vous avez fixée à la main pour ce contact ;
2. la route apprise par la radio ;
3. le flood.

Les routes apprises viennent de la liste de contacts de la radio et de la découverte de chemin. Le chemin d’une annonce est affiché à titre d’information, et n’est pas utilisé pour envoyer. Un accusé de réception dit qu’un message est arrivé, pas par où.

Un message direct part tout de suite. Si un accusé est attendu et ne vient pas, Meshloom réessaie jusqu’à deux fois, en attendant le délai que suggère la radio. Avant le dernier essai, il oublie la route enregistrée : le dernier essai part donc en flood, même si vous aviez fixé une route à la main.

## Renvoyer les messages de salon

**Renvoyer automatiquement les messages de canal non entendus**, dans **Réglages → Radio → Messagerie**, renvoie un message de salon une fois si aucun répéteur ne l’a répété dans les 2 secondes. La copie est identique : les répéteurs qui ont déjà entendu le premier l’ignorent, et aucun doublon n’apparaît.

## Annonces et position

Une annonce dit aux autres que vous existez. L’onglet **Annonces** de **Réglages → Radio** propose :

- **Intervalle d’annonce périodique**, en heures. `0` la désactive. Le minimum est d’une heure (24 ou plus est recommandé), et une valeur plus courte est relevée à une heure.
- **Envoyer une annonce flood**, qui passe par les répéteurs, et **Envoyer une annonce zéro saut**, qui reste locale et consomme moins de temps d’antenne.

La position dans les annonces se règle avec **Source de position des annonces**, dans l’onglet **Configuration**. Elle n’a que deux choix : **Désactivée**, ou **Inclure la position du nœud**. Le firmware compagnon ne distingue pas une position enregistrée d’un relevé GPS en direct. Quand la radio n’indique pas ce réglage, Meshloom affiche **Désactivée**.

Avec [Meshloom Community](/docs/deep/community/) activé, ce choix fixe aussi la position que reçoit Community : exacte avec **Inclure la position du nœud**, arrondie à environ 1 km avec **Désactivée**. Le changer, ou changer la position de la radio, envoie d’elle-même la nouvelle position à Community. Une radio sans position met Community en attente jusqu’à ce qu’elle en ait une (voir [Meshloom Community](/docs/deep/community/)).

## GPS et réglages du firmware

L’onglet **Avancé** de **Réglages → Radio** commence par les **Réglages du firmware** : ceux que le firmware de la radio signale. Ils sont enregistrés sur la radio et s’appliquent tout de suite.

Sur une radio équipée d’un module GPS, la liste contient une case **GPS**. Activée, la radio utilise le GPS pour sa position et règle son horloge dessus. Le firmware ne cherche le GPS qu’au démarrage de la radio : un GPS absent de la liste n’était pas branché à ce moment-là. Éteignez la radio, branchez le GPS, puis rallumez-la. Avec **Inclure la position du nœud** (voir plus haut), les annonces portent alors la position du GPS.

Si la liste est vide, la radio ne signale aucun réglage. Un firmware très ancien n’en signale aucun, et Meshloom le précise.

Le groupe **Contournements** du même onglet rassemble les correctifs cités sur cette page et **Corriger une horloge radio bloquée dans le futur**, le seul activé par défaut (voir [Dépannage](/docs/deep/troubleshooting/#lhorloge-de-la-radio-est-bloquée-dans-le-futur)).

## Capteurs de la radio

Sous les réglages du firmware, **Capteurs de la radio** montre ce que la radio signale d’elle-même : la tension de sa batterie, la température du microcontrôleur si la carte a un capteur, et la position GPS quand le GPS est activé. Le stockage utilisé sur la radio s’affiche aussi, en Ko utilisés / total avec un pourcentage, quand la radio le signale. Les valeurs sont lues à l’ouverture de l’onglet ; **Actualiser** les relit.

## Wi-Fi

Les radios dotées du firmware compagnon 1.18 ou plus récent, compilé avec le Wi-Fi, ont une interface Wi-Fi que Meshloom lit et règle par la ligne de commande compagnon de la radio. Cela couvre les versions officielles Bluetooth + Wi-Fi et les versions Wi-Fi produites par le flasheur Meshloom. **Réglages → Radio → Connexion** montre alors un groupe **Wi-Fi** : le nom du réseau (SSID), si le Wi-Fi est activé, si la radio est connectée et son adresse, et **Identifiants enregistrés dans la radio** (oui ou non). Le nom affiché est celui enregistré, ou celui intégré au firmware quand rien n’est enregistré : la radio ne sait pas dire lequel. Le groupe est masqué quand le firmware n’a pas de ligne de commande (antérieur à 1.18, ou radio jointe par le proxy radio d’un autre Meshloom) ou pas de Wi-Fi.

### La question posée une fois

Après chaque connexion, Meshloom vérifie si la radio contient des identifiants Wi-Fi enregistrés. Une radio Wi-Fi produite par le flasheur rejoint le réseau avec des identifiants compilés dans son firmware et n’a rien d’enregistré. La « mise à jour sans effacement » du flasheur peut produire un firmware sans identifiants et compter sur ceux enregistrés : sans rien d’enregistré, la radio ne démarrerait plus jamais le Wi-Fi.

Quand le Wi-Fi est activé et que rien n’est enregistré, Meshloom demande donc une fois le mot de passe, avec le nom du réseau prérempli (modifiable) et une option **Réseau ouvert**, et dit pourquoi : cela permet au flasheur de mettre à jour cette radio sans retaper les identifiants Wi-Fi.

- **Enregistrer dans la radio** envoie le nom du réseau, puis le mot de passe, les relit et les vérifie. S’ils ne correspondent pas, les valeurs précédentes sont rétablies.
- **Ne plus demander pour cette radio** est mémorisé pour cette radio (par sa clé publique) : Meshloom ne la redemande plus jamais. **Enregistrer les identifiants**, dans le groupe Wi-Fi, reste disponible.

La question n’a pas de bouton de fermeture et attend l’une de ces deux réponses. Un mauvais mot de passe, ou **Réseau ouvert** coché pour un réseau protégé, empêche la radio de rejoindre le Wi-Fi après son prochain redémarrage. Les identifiants que vous saisissez dans Meshloom pour toute autre raison sont enregistrés dans la radio de la même façon.

### Changer de réseau, activer ou désactiver le Wi-Fi

**Changer de réseau Wi-Fi** demande le nom du réseau et deux fois le mot de passe, ou **Réseau ouvert**. **Désactiver le Wi-Fi** et **Activer le Wi-Fi** changent le réglage de la radio. Chaque changement s’applique au redémarrage de la radio. Quand un changement demande un redémarrage, Meshloom demande avant de redémarrer la radio ; **Plus tard** garde le changement, avec **Changement en attente d’un redémarrage** et un bouton **Redémarrer maintenant**. Enregistrer les identifiants du réseau que la radio utilise déjà avec ses identifiants intégrés ne demande pas de redémarrage.

Quand la radio n’a aucun nom de réseau, **Configurer le Wi-Fi** remplace **Activer le Wi-Fi** : la radio ne démarre le Wi-Fi qu’une fois un nom enregistré. Si le Wi-Fi est désactivé, **Configurer le Wi-Fi** l’active aussi, pour qu’un seul redémarrage applique les deux.

Quand Meshloom joint la radio en TCP (donc par son Wi-Fi), les fenêtres préviennent que changer de réseau ou désactiver le Wi-Fi peut rendre la radio injoignable. Reconnectez-la alors en USB ou en Bluetooth. Une fois la radio sur le Wi-Fi, Meshloom propose de l’utiliser en TCP (voir [Transports](/docs/deep/transports/#du-bluetooth-au-wi-fi)).

### Limites

- Un réseau nommé littéralement `(not set)` ne peut pas être utilisé : la radio affiche ce texte quand aucun nom n’est défini.
- Le nom du réseau fait de 1 à 32 octets, le mot de passe jusqu’à 63. Aucun des deux n’est rogné : les espaces comptent. Sous 8 caractères, Meshloom prévient (le WPA en demande 8, les clés WEP en ont 5 ou 13) et enregistre le mot de passe tel quel.
- Sur la radio, un réseau ouvert se lit comme « rien d’enregistré ». Meshloom retient que vous en avez enregistré un pour ce réseau.
- Si l’enregistrement est interrompu (le lien a coupé entre les deux commandes), la radio peut contenir le nouveau nom de réseau avec l’ancien mot de passe ou aucun. Meshloom affiche alors un avertissement, même après une reconnexion, et ne propose aucun redémarrage (pas même avec **Activer le Wi-Fi** ou **Désactiver le Wi-Fi**) : enregistrez de nouveau les identifiants, ou vérifiez-les en USB ou en Bluetooth avant de redémarrer la radio.
- Si la vérification après la connexion échoue (la radio n’a pas répondu à temps), Meshloom réessaie après 30 secondes, puis après 2 minutes. **Réessayer**, dans le groupe Wi-Fi, vérifie de nouveau. Si Meshloom ne peut pas lire du tout l’état du Wi-Fi, le groupe l’indique, avec **Réessayer**.

### Ce que Meshloom garde

Meshloom n’enregistre jamais le mot de passe Wi-Fi, ni dans sa base de données ni dans ses journaux. Pour savoir s’il y en a un d’enregistré, il le relit dans la radio après chaque connexion (et à chaque nouvelle tentative de cette vérification) et quand il enregistre des identifiants, puis l’oublie. En TCP, il traverse alors votre réseau local en clair à chaque fois. Voir [Sécurité](/docs/deep/security/#mot-de-passe-wi-fi).

## Remise à zéro usine

Tout en bas de l’onglet **Avancé**, **Remise à zéro usine** efface la radio. Le bouton demande que la radio soit connectée. Une fenêtre liste ce qui va se passer, et vous devez saisir exactement le nom de la radio pour activer le bouton de confirmation.

**Effacé de la radio :**

- Son identité. Une nouvelle paire de clés est générée : les autres nœuds voient donc un nouveau nœud.
- Ses contacts et ses canaux.
- Ses réglages radio (fréquence, bande passante, SF, CR, puissance d’émission). Ils reviennent aux valeurs par défaut du firmware : saisissez-les de nouveau dans l’onglet **Radio**.
- Les identifiants Wi-Fi qui y sont enregistrés (voir [Wi-Fi](#wi-fi)). Une radio jointe par le Wi-Fi peut devenir injoignable : reconnectez-la en USB ou en Bluetooth. La fenêtre le signale quand des identifiants sont enregistrés.

**Conservé :**

- La base de données de Meshloom : vos messages et vos contacts.
- Les identifiants Wi-Fi intégrés au firmware, s’il y en a.

La radio redémarre et Meshloom se reconnecte tout seul. Il vous demande ensuite s’il faut adopter la nouvelle identité, avec l’invite d’identité habituelle. Cette action est irréversible.

## Proxy radio

**Réglages → Proxy** peut faire passer Meshloom pour une radio MeshCore sur votre réseau, afin qu’une application mobile ou un autre Meshloom se connecte à travers lui. Il est désactivé par défaut. Une fois activé, il écoute sur le port 5001, sur toutes les adresses réseau, pour 8 clients au plus à la fois. Vous pouvez changer l’adresse, le port et le nombre de clients.

Le protocole MeshCore n’a pas de mot de passe : n’activez le proxy que sur un réseau de confiance. Un second Meshloom doit utiliser une nouvelle base de données.

## La clé privée

À la connexion, Meshloom demande à la radio sa clé privée et la garde **en mémoire uniquement**. Elle n’est jamais écrite dans sa base. Grâce à elle, Meshloom peut déchiffrer lui-même les messages directs, même quand le contact n’est pas chargé sur la radio, et lire d’anciens messages dès qu’une clé devient connue.

Quiconque atteint l’interface peut relire la clé, par `GET /api/radio/private-key` et dans la sauvegarde complète de **Réglages → Base de données** : c’est ainsi que l’on garde l’identité de la radio d’un flashage à l’autre (voir [Sauvegarder la radio](/docs/backup/)). Un firmware compilé sans export de clé n’en donne aucune, et ses sauvegardes ne contiennent pas d’identité. Voir [Sécurité](/docs/deep/security/#la-clé-privée-du-nœud).
