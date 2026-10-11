---
title: Radio, contacts et salons
description: Ce que Meshloom charge sur la radio, la portée de flood, et la largeur des sauts.
level: deep
order: 15
---

Meshloom gère les contacts et les salons stockés sur votre radio. Le serveur retient beaucoup plus de choses que la radio ne peut en contenir, et charge sur la radio l’ensemble de travail dont il a besoin.

La base de données est liée à la clé publique de votre radio. Si vous branchez une autre radio, une fenêtre s’ouvre : **Cette radio ne correspond pas à l’identité enregistrée**. **Effacer et continuer** adopte la nouvelle radio et efface les contacts et messages mesh locaux ; les salons et les réglages du serveur restent. **Annuler** laisse vos données intactes et met la connexion à cette radio en pause. Une base créée avant ce lien pose la question une fois, même pour sa radio habituelle : **Cette radio n’est pas liée à cette instance**. Choisissez **Lier sans effacer** s’il s’agit de la même radio, ou **Nouvelle radio** pour repartir de zéro. Voir [Premier lancement](/docs/first-run/).

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

`MESHCORE_LOAD_WITH_AUTOEVICT=true` fait supprimer à la radio son plus ancien contact non favori quand la table est pleine. Ajouter des contacts n’échoue alors jamais, Meshloom peut charger des contacts même s’il n’a pas réussi à lire la table, et il n’a plus besoin d’en retirer d’abord.

Le prix à payer : les contacts chargés par Meshloom ne sont pas marqués comme favoris sur la radio, ils peuvent donc être supprimés quand une nouvelle annonce arrive. Si vous débranchez la radio de Meshloom pour l’utiliser seule, ils ne sont plus protégés.

## Salons et emplacements

La radio a un nombre limité d’emplacements de salon. Meshloom lit ce nombre sur la radio et n’en suppose aucun.

Au démarrage, il vide les emplacements de salon de la radio. Ensuite, un envoi vers un salon réutilise l’emplacement déjà chargé ; un nouveau salon prend un emplacement libre, puis remplace le moins récemment utilisé quand il n’y en a plus.

Deux exceptions à cette réutilisation :

- Avec une **radio réseau (TCP)**, chaque envoi vers un salon réécrit le salon dans la radio, car un autre programme peut utiliser la même radio.
- `MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE=true` fait de même sur tous les types de connexion. À utiliser si les emplacements semblent instables ou si une autre application les modifie. Chaque envoi prend alors environ 500 ms de plus.

## L’audit horaire

Meshloom s’appuie sur les événements de la radio pour recevoir les messages, et fait en plus une vérification lente, une fois par heure. Elle cherche les messages restés sur la radio, et les emplacements de salon qui ne correspondent plus à ce que Meshloom attend.

S’il trouve un écart, il affiche une erreur et oublie ce qu’il croyait savoir des emplacements. Si vous voyez cette erreur, ou si des messages présents sur la radio n’arrivent jamais dans Meshloom, `MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK=true` fait tourner la vérification toutes les 10 secondes.

## Largeur des sauts : `path_hash_mode`

Chaque répéteur d’une route est identifié par quelques octets. Un identifiant plus long limite les confusions entre répéteurs, mais moins de répéteurs tiennent dans un paquet. Le réglage s’appelle **Mode de hachage de chemin**, dans **Réglages → Radio**, parmi les paramètres radio :

| Valeur | Largeur par saut | Route la plus longue |
|--------|------------------|----------------------|
| `0` | 1 octet | 63 sauts |
| `1` | 2 octets (recommandé) | 32 sauts |
| `2` | 3 octets | 21 sauts |

À la connexion, Meshloom fait passer de 1 à 2 octets une radio qui utilise 1 octet, sauf si vous avez choisi de rester à 1 octet. Le réglage n’apparaît que si le firmware de la radio le gère.

`path_len`, dans l’interface comme dans l’API, est **toujours un nombre de sauts**, jamais un nombre d’octets. Un salon peut avoir sa propre largeur (**Définir le forçage de largeur de saut** dans son en-tête), appliquée à cet envoi seulement.

## Portée de flood régionale

Un message envoyé en flood traverse tous les répéteurs à portée. Une **région** le limite : les répéteurs configurés pour cette région le relaient, et ceux configurés pour refuser les autres régions peuvent l’abandonner.

- **Réglages → Radio → Messagerie → Portée flood / région** est la région utilisée pour tous vos envois. Vide, il n’y a pas de région (flood simple).
- Un salon peut utiliser une autre région, ou aucune, avec le bouton en forme de globe dans son en-tête. Le changement s’applique aux envois vers ce salon, puis le réglage habituel est rétabli. Forcer « aucune région » quand la radio a une région par défaut demande un firmware en version 12 ou plus récente.

Pour les messages reçus, la région n’est pas écrite en clair dans le paquet : c’est un code calculé avec la clé de la région. Meshloom essaie chaque nom de **Régions connues** pour trouver celui qui correspond. Une région absente de la liste laisse un message marqué comme régional mais sans nom. Enregistrer à nouveau la liste ré-étiquette les messages dont le paquet est encore stocké.

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

Une annonce dit aux autres que vous existez. **Réglages → Radio → Annonces et découverte** propose :

- **Intervalle d’annonce périodique**, en heures. `0` la désactive. Le minimum est d’une heure (24 ou plus est recommandé), et une valeur plus courte est relevée à une heure.
- **Envoyer une annonce flood**, qui passe par les répéteurs, et **Envoyer une annonce zéro saut**, qui reste locale et consomme moins de temps d’antenne.

La position dans les annonces n’a que deux choix : désactivée, ou **Inclure la position du nœud**. Le firmware compagnon ne distingue pas une position enregistrée d’un relevé GPS en direct.

## Proxy radio

**Réglages → Proxy** peut faire passer Meshloom pour une radio MeshCore sur votre réseau, afin qu’une application mobile ou un autre Meshloom se connecte à travers lui. Il est désactivé par défaut. Une fois activé, il écoute sur le port 5001, sur toutes les adresses réseau, pour 8 clients au plus à la fois. Vous pouvez changer l’adresse, le port et le nombre de clients.

Le protocole MeshCore n’a pas de mot de passe : n’activez le proxy que sur un réseau de confiance. Un second Meshloom doit utiliser une nouvelle base de données.

## La clé privée

À la connexion, Meshloom demande à la radio sa clé privée et la garde **en mémoire uniquement**. Elle n’est jamais écrite sur le disque. Grâce à elle, Meshloom peut déchiffrer lui-même les messages directs, même quand le contact n’est pas chargé sur la radio, et lire d’anciens messages dès qu’une clé devient connue.

L’export de cette clé par l’API est désactivé par défaut. Voir [Sécurité](/docs/deep/security/).
