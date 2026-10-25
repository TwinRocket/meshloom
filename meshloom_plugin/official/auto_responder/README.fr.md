# Auto-répondeur

Répond aux messages avec des réponses écrites sous forme de règles simples. Sans code.

- **Lit** les messages des canaux publics et vos messages directs.
- **Envoie** uniquement des réponses, dans la conversation du message auquel il répond.
- **Conserve** un petit compteur de ses réponses récentes, jamais le texte des messages. Aucun accès au réseau.

## Règles

Une règle par ligne dans **Règles** :

```
déclencheur[,option=valeur]: texte à chercher => réponse   (une espace de chaque côté de =>)
```

| Déclencheur | La règle s'applique quand... |
|---|---|
| `keyword` | le mot ou la phrase apparaît dans le message (mots entiers) |
| `prefix` | le message commence par le texte |
| `regex` | le texte, un motif de 100 caractères au plus, se trouve dans les 200 premiers caractères du message |

Les mots `keyword`, `prefix` et `regex` s'écrivent tels quels, quelle que soit la langue. Options, après le déclencheur et séparées par des virgules :

- `channel=#nom` limite la règle à un canal (`channel=dm` pour les messages directs seulement) ;
- `cooldown=N` fixe la pause, en secondes (de 30 à 86400), avant que cette règle réponde de nouveau à la même personne. Par défaut, le réglage **Pause**.

La réponse peut contenir `{sender}` (le nom de l'expéditeur), `{channel}` (le nom du canal) et `{text}` (le message). Exemples :

```
keyword: bonjour => Bonjour {sender} !
prefix,channel=#test,cooldown=300: !ping => Pong, {sender} !
regex: ^(salut|coucou)\b => Salut {sender}, bienvenue sur {channel}
```

La première règle qui correspond décide. Une règle illisible est ignorée et écrite dans le journal avec son numéro de ligne. Un motif qui répète un groupe déjà répété, ou qui utilise une référence arrière, est refusé.

## Limites

Le plugin ne répond jamais plus que **Nombre maximal de réponses par heure** (20 par défaut), sous les 30 par heure que Meshloom accorde à chaque copie. En plus, Meshloom lui-même empêche de répondre plus vite qu'un message toutes les 30 secondes à la même personne et plafonne ce que tous les plugins envoient ensemble. Les majuscules et minuscules sont ignorées, sauf si vous activez **Respecter les majuscules et minuscules**. Vos propres messages ne reçoivent jamais de réponse.
