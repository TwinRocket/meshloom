# Écho

Répond `Plong!` à `!pling`, dans la conversation d'où vient le message (un canal public
ou un message direct). C'est le plugin de référence : tout son code tient dans
`plugin.py`, quelques lignes à copier pour écrire le vôtre.

- **Lit** les messages des canaux publics et vos messages directs.
- **Envoie** uniquement des réponses, dans la conversation du message auquel il répond.
- Aucun réglage, aucune donnée conservée, aucun accès au réseau.

Les limites de Meshloom s'appliquent : au plus une réponse toutes les 30 secondes au même
expéditeur, 30 messages par heure pour cette copie.
