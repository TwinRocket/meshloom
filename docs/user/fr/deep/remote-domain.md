---
title: Votre propre domaine pour Meshloom Remote
description: Faire mener votre propre nom de domaine à votre adresse Meshloom Remote par une redirection d’URL, et le vérifier depuis Meshloom avec le bouton Tester.
level: deep
order: 24.4
---

Meshloom Remote donne à votre instance une adresse comme `https://k3xwq2m7ta.meshloom.link`. Si vous possédez un domaine (`chat.example.org`), vous pouvez le faire mener là sans frais pour vous ni pour TwinRocket, avec une **redirection d’URL** réglée là où vous gérez votre domaine. Cette page explique comment, à quoi s’attendre et comment tester.

## Pourquoi une redirection et pas un CNAME

Vous ne pouvez pas faire pointer votre domaine vers le relais par un enregistrement `CNAME` : le relais est derrière Cloudflare, qui refuse un nom appartenant à un autre compte (erreur 1014, « CNAME Cross-User Banned »). Y parvenir demanderait une option Cloudflare payante, que Meshloom Remote n’utilise pas. Une redirection n’exige rien de notre côté : votre domaine répond « va sur `https://<votre adresse>` » et le navigateur suit.

## Ce que ça donne

- Quelqu’un saisit `chat.example.org`. Son navigateur est envoyé vers votre adresse Meshloom Remote et **la barre d’adresse affiche alors `<nom>.meshloom.link`**, pas votre domaine. C’est la limite de cette méthode.
- Chaque appareil doit toujours être autorisé (par QR code ou demande d’accès), exactement comme avec l’adresse directe ([Comptes et accès](/docs/deep/access/)) : la redirection ne donne aucun accès par elle-même.
- Si vous remplacez la clé Remote dans Réglages → Accès à distance, l’instance reçoit une nouvelle adresse. Mettez la redirection à jour, et préférez une redirection temporaire (HTTP 302 ou 307) à une permanente : les navigateurs retiennent une redirection permanente et continuent d’envoyer vers l’ancienne adresse.
- Si vous voulez que votre domaine reste dans la barre d’adresse, Meshloom Remote ne sait pas le faire. Utilisez l’une des voies de [Accès à distance par vos propres moyens](/docs/deep/remote-self-hosted/) (un Cloudflare Tunnel ou un reverse-proxy que vous exploitez).

## Régler la redirection

Copiez d’abord votre adresse dans **Réglages → Accès à distance → État** (la ligne qui commence par `https://`). Créez ensuite une redirection de votre domaine vers elle. Choisissez le cas qui vous convient.

### Chez votre registrar ou hébergeur DNS

La plupart des registrars parlent de « redirection d’URL », « redirection web » ou « redirection de domaine ».

1. Ouvrez les réglages de redirection du domaine.
2. Source : `chat.example.org` (ou le domaine nu).
3. Destination : votre adresse Meshloom Remote, `https://<nom>.meshloom.link`.
4. Type : temporaire (302 ou 307). Ne choisissez pas « masquée », « frame » ou « cloaking » : cela charge Meshloom dans un cadre, ce qui ne fonctionne pas.
5. Si le registrar le propose, conservez le chemin.

### Dans Cloudflare (votre propre compte)

1. Vérifiez que le nom est dans votre zone et **proxifié** (nuage orange). S’il n’a pas de vraie cible, ajoutez un enregistrement factice, par exemple un `A` vers `192.0.2.1` : la redirection est servie avant qu’il serve.
2. Règles → Règles de redirection → Créer une règle. Quand : le nom d’hôte est égal à `chat.example.org`.
3. Alors : redirection dynamique ou statique vers `https://<nom>.meshloom.link`, statut 302.
4. Enregistrez et patientez une minute.

## Tester depuis Meshloom

En tant qu’administrateur, ouvrez **Réglages → Accès à distance**. Meshloom Remote a besoin de Meshloom Community activé (voir [Fonctions qui ont besoin de Community](/docs/deep/community/#fonctions-qui-ont-besoin-de-community)). Quand Meshloom Remote est activé et enregistré, le groupe **Votre propre domaine** apparaît. Saisissez votre domaine et appuyez sur **Tester**. Votre instance envoie une requête à ce domaine (HTTPS d’abord, puis HTTP si HTTPS échoue) sans suivre la redirection. Elle ne contacte jamais le relais pour cela. Le résultat est l’un de :

| Résultat | Signification | Que faire |
|---|---|---|
| La redirection fonctionne | Le domaine répond par une redirection vers votre adresse. | Rien. Essayez depuis un téléphone, hors de votre réseau. |
| Aucune redirection | Le domaine répond, mais sans redirection (par exemple une page de parking). | Créez la redirection ci-dessus, ou attendez que le registrar l’applique. |
| Mauvaise destination | Le domaine redirige ailleurs, indiqué dans le message. | Corrigez la destination : elle doit être exactement votre `<nom>.meshloom.link`. |
| Ce n’est pas un nom de domaine | Ce que vous avez saisi n’est pas un nom tel que `chat.example.org`. | Retirez le port, le chemin ou l’adresse IP. |
| Votre domaine est injoignable | Le nom ne se résout pas, répond trop lentement, a un mauvais certificat ou pointe vers une adresse privée. | Vérifiez l’enregistrement DNS et le certificat ; la raison est affichée. |

Un domaine qui pointe vers votre propre réseau (adresse privée ou locale) n’est jamais contacté par ce test, comme toute adresse que Meshloom doit appeler. Le test part de votre instance : il voit ce que voit votre instance, et un changement chez le registrar peut mettre du temps à atteindre tous les résolveurs.

Le domaine saisi n’est enregistré nulle part. Les notifications Web Push utilisent donc votre adresse `<nom>.meshloom.link` comme adresse de contact quand aucune n’est saisie, jamais votre propre domaine (voir [Notifications push](/docs/deep/push/)).
