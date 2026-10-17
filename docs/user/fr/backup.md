---
title: Sauvegarder la radio
description: Sauvegarder l’identité, les réglages, les contacts et les canaux de la radio avant de la reflasher, et les restaurer ensuite.
level: start
order: 5.5
---

Meshloom peut enregistrer dans un seul fichier tout ce qui fait de votre nœud le vôtre, la **sauvegarde complète** : l’identité de la radio (sa clé privée), ses réglages, les identifiants Wi-Fi qu’elle stocke, vos contacts et vos canaux, et les réglages de Meshloom. La restaurer sur une radio fraîchement flashée fait revenir le même nœud : même clé publique, mêmes contacts, mêmes canaux.

## Avant de reflasher

À faire avant une **installation complète** avec le flasheur, ou avant tout flashage qui efface la radio :

1. Gardez la radio connectée à Meshloom, la barre d’état indiquant **Radio OK**.
2. Ouvrez Meshloom, puis **Réglages → Base de données** (voir [Trouver la sauvegarde](#trouver-la-sauvegarde)).
3. Dans **Sauvegarde et restauration**, sous **Sauvegarde complète (radio et Meshloom)**, cliquez sur **Télécharger la sauvegarde complète**.
4. Lisez les remarques que Meshloom affiche après le téléchargement. S’il indique que **cette sauvegarde ne contient pas d’identité**, le firmware de la radio ne donne pas sa clé privée : le fichier contient les réglages, les contacts et les canaux, mais sa restauration ne rendra pas l’identité du nœud.
5. Rangez le fichier en lieu sûr, si possible ailleurs que sur la machine de la radio. Il contient des secrets : voir [Chiffrement](#chiffrement).

Une **mise à jour sans effacement** garde l’identité et les réglages de la radio : elle ne demande aucune restauration. Un firmware compilé sans identifiants Wi-Fi s’appuie alors sur ceux que la radio stocke : vérifiez que la radio les stocke avant une telle mise à jour.

## Trouver la sauvegarde

La sauvegarde se trouve dans **Réglages → Base de données**, groupe **Sauvegarde et restauration**. Vous pouvez l’ouvrir directement avec l’adresse de votre Meshloom suivie de `/#settings/database`, par exemple `http://meshloom.local:8000/#settings/database`. Les versions récentes acceptent aussi `/#settings/database/backup`, qui fait défiler jusqu’au groupe.

Le groupe contient :

- **Sauvegarde complète (radio et Meshloom)** : le fichier décrit sur cette page.
- **Restaurer une sauvegarde** : restaure une sauvegarde complète, ou une ancienne sauvegarde de Meshloom seul.
- **Télécharger meshcore.db** : une copie de toute la base (historique des messages compris). Elle ne contient jamais la clé privée de la radio et ne se restaure pas depuis l’interface.

## Restaurer la radio

1. Flashez la radio, puis connectez-la à Meshloom comme d’habitude (**Réglages → Radio**).
2. Meshloom voit une radio avec une nouvelle identité et ouvre la fenêtre d’identité. Choisissez **Restaurer depuis une sauvegarde**, choisissez le fichier (et saisissez sa phrase secrète si vous en avez mis une).
3. Meshloom montre ce que contient le fichier et ce que sa restauration change. L’identité de la radio connectée, une nouvelle identité aléatoire après une installation complète, est remplacée par celle de la sauvegarde : la fenêtre nomme cette radio et vous demande de confirmer.
4. Cliquez sur **Restaurer**. Meshloom écrit l’identité, puis ses propres données, puis les réglages de la radio, les canaux, les contacts et les identifiants Wi-Fi, en affichant chaque étape. À la fin, il liste ce qui ne s’est pas passé comme prévu : un réglage refusé par la radio, des contacts qui n’ont pas trouvé de place, des bots ignorés, des clés push conservées.
5. Quand il le demande, cliquez sur **Redémarrer la radio maintenant** pour que la radio applique les réglages restaurés (le Wi-Fi en particulier).

Sur un **nouveau Meshloom** (nouvelle machine ou nouvelle base), **configurez d’abord la connexion à la radio**, puis restaurez depuis **Réglages → Base de données → Restaurer une sauvegarde**. Si la radio n’est pas connectée au moment de la restauration, seules les données de Meshloom sont restaurées. Quand la radio se connecte ensuite, Meshloom demande quoi faire de son identité : choisissez de nouveau **Restaurer depuis une sauvegarde** pour appliquer la clé et les étapes radio, ou **Lier sans effacer** pour garder l’identité propre de la radio avec l’historique restauré. Quand ce Meshloom appartient déjà à l’identité de la sauvegarde, il ne demande rien à la connexion de la radio : restaurez de nouveau une fois la radio connectée pour appliquer les étapes radio. Meshloom ne se lie jamais à l’identité de la sauvegarde avant qu’une radio ait montré qu’elle la détient.

Quand l’historique de Meshloom a été enregistré avant qu’il sache à quelle identité il appartient (une ancienne version, ou une base jamais liée à une radio), le résumé propose **Effacer aussi l’historique que ce Meshloom a enregistré avant**, décoché : laissé décoché, cet historique est conservé et attribué à l’identité restaurée.

Certaines étapes peuvent être sautées :

- Une radio dont le firmware refuse l’import de clé (compilé sans) ne peut pas retrouver son identité. Meshloom s’arrête avant d’écrire quoi que ce soit d’autre et le dit.
- Si la radio redémarre pendant la restauration (elle peut le faire pour régler son horloge), les étapes restantes sont sautées : relancez la restauration quand la radio est revenue.
- L’étape du code PIN Bluetooth est désactivée par défaut quand Meshloom joint la radio en Bluetooth et que le code diffère ; l’étape Wi-Fi est désactivée par défaut quand Meshloom joint la radio par le Wi-Fi et que la sauvegarde contient un autre réseau ou désactive le Wi-Fi, car la restaurer couperait Meshloom de la radio.
- Une puissance d’émission inférieure à 0 dBm n’est pas réécrite (la radio garde sa puissance actuelle) : réglez-la de nouveau dans **Réglages → Radio**.

Après une restauration, rejoignez de nouveau **Meshloom Community** si vous le souhaitez : la participation n’est jamais restaurée depuis un fichier. Les notifications push peuvent devoir être réactivées dans chaque navigateur.

## Chiffrement

La sauvegarde complète contient la clé privée de votre radio et, si la radio les stocke, son mot de passe Wi-Fi, ainsi que d’autres secrets conservés par Meshloom : clés de canaux, code PIN Bluetooth, mot de passe SMTP, secrets des webhooks et des intégrations. Quiconque détient le fichier peut se faire passer pour votre nœud.

Cochez **Chiffrer avec une phrase secrète** avant le téléchargement pour le protéger (8 caractères ou plus). Le fichier est alors illisible sans la phrase secrète, et personne ne peut retrouver une phrase secrète perdue. Meshloom la demande à la restauration. Sans chiffrement, traitez le fichier comme la clé elle-même.

Notez que quiconque atteint votre Meshloom peut télécharger cette sauvegarde, tout comme une page web ouverte dans un navigateur sur une machine qui l’atteint : gardez Meshloom sur un réseau de confiance (voir [Sécurité](/docs/deep/security/#la-clé-privée-du-nœud)).

## Contenu de la sauvegarde

**Depuis la radio :**

- Son identité (clé privée), quand le firmware permet de l’exporter.
- Son nom, sa position, sa puissance d’émission et ses paramètres radio (fréquence, bande passante, facteur d’étalement, taux de codage), avec l’indicateur de répétition client.
- Ses autres réglages : partage de télémétrie, politique de position dans les annonces, accusés de réception supplémentaires, ajout manuel des contacts, paramètres de temporisation, mode de hachage des chemins, règles d’ajout automatique, portée de flood par défaut (région), code PIN Bluetooth, variables personnalisées du firmware (le GPS et son intervalle quand la carte en a) et, à partir du firmware 1.18, le fuseau horaire et les réglages radio que seule la ligne de commande atteint.
- L’état activé ou non du Wi-Fi de la radio, et le nom et le mot de passe du réseau Wi-Fi quand la radio stocke un mot de passe. Un réseau ouvert, ou des identifiants seulement compilés dans le firmware, ne sont pas enregistrés (l’état activé ou non l’est). Quand la radio stocke un mot de passe mais pas de nom de réseau, la radio annonce le nom de réseau compilé dans le firmware : la sauvegarde associe les deux, et une restauration les enregistre tous les deux (la radio les utilise alors).

**Depuis Meshloom :**

- Les contacts (favoris, routes), les canaux avec leurs clés et leur portée régionale, les groupes de contacts.
- Les réglages : régions, listes de blocage, suivi de télémétrie, règles d’alerte, destinations de notification, mises à jour, préférences d’interface, préférences push, réglages du proxy radio, groupe **Contournements** de **Réglages → Radio → Avancé** (correction de l’horloge comprise).
- Les intégrations MQTT, webhook, Apprise, SQS, envoi vers la carte et Home Assistant, avec leurs secrets, chacune activée ou désactivée comme dans le fichier : une intégration activée commence à envoyer dès la fin de la restauration. **Les bots reviennent désactivés** : un bot exécute du code sur le serveur, activez donc chacun après l’avoir relu. Sur un serveur où les bots sont désactivés (`MESHCORE_DISABLE_BOTS=true`, le réglage par défaut du paquet Linux et de l’image Raspberry Pi, ou **Désactiver les bots jusqu’au redémarrage du serveur**), les bots ne sont **pas restaurés du tout** : activez d’abord les bots si vous voulez les retrouver. Avant la restauration, le résumé nomme chaque intégration, indique si le fichier active le proxy radio et quels bots sont ignorés.
- Les clés et abonnements Web Push, restaurés seulement sur un Meshloom qui n’a pas d’abonnements à lui.

**Non inclus :**

- Les messages, paquets bruts et historiques de télémétrie : utilisez **Télécharger meshcore.db** pour ceux-là.
- Les réglages de connexion à la radio (port, adresse, appareil Bluetooth) : ils appartiennent à la machine.
- La participation à Meshloom Community et son code de région : la rejoindre est votre choix, à refaire.
- L’horloge de la radio (Meshloom la règle) et quelques réglages qu’aucune commande n’atteint (buzzer et vibration).

La restauration écrit les canaux dans les emplacements de la radio et vide les emplacements suivants. Meshloom gère lui-même ces emplacements à chaque connexion : ils se remplissent de nouveau quand vous utilisez les canaux.
