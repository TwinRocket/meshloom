"""Migration 129: encrypt the stored third-party secrets (MSHL-133).

Encrypts in place, with ``app.secret_store`` (AES-256-GCM, the ``secrets.key``
file next to the database, created at startup before the migrations):

- every non-empty value of ``notification_channels.secrets`` (string tokens,
  AAD ``secret_store.aad("notification_channels", id, field)``, as
  ``repository.notification_channels.encode_secrets`` writes them);
- the secret keys of each fanout type inside ``fanout_configs.config`` (sealed
  JSON values, AAD ``secret_store.aad("fanout_configs", id, key)``, as
  ``repository.fanout.seal_config`` writes them). The key list is frozen here.

Idempotent: a value that already has the shape of a ``v1:`` token is left as it
is. Without a usable key the migration logs a warning and changes nothing: the
reads are tolerant of plain values, and ``services.secrets_backfill``, run by
``Database.connect`` at every start after the migrations, encrypts them as soon as
a key is available.
"""

import logging

import aiosqlite

from app import secret_store
from app.services.secrets_backfill import encrypt_plain_secrets

logger = logging.getLogger(__name__)

# ``secret_fields.FANOUT_SECRET_KEYS`` when this migration was written.
FANOUT_SECRET_KEYS: dict[str, tuple[str, ...]] = {
    "apprise": ("urls",),
    "webhook": ("url", "headers", "hmac_secret"),
    "mqtt_private": ("password",),
    "mqtt_ha": ("password",),
    "mqtt_community": ("password",),
    "sqs": ("access_key_id", "secret_access_key", "session_token"),
}


async def migrate(conn: aiosqlite.Connection) -> None:
    if not secret_store.ensure_key():
        logger.warning(
            "No secrets key: the stored secrets stay unencrypted until a key is available"
        )
        return
    await encrypt_plain_secrets(conn, FANOUT_SECRET_KEYS)
