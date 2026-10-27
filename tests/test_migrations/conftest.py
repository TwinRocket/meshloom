# Updated automatically when a new migration is added.  Migration tests that
# run ``run_migrations`` to completion assert ``get_version == LATEST`` and
# ``applied == LATEST - starting_version`` so only this constant needs to
# change, not every individual assertion.
LATEST_SCHEMA_VERSION = 136


def opened_secrets(raw: str, channel_id: str) -> dict:
    """``notification_channels.secrets`` decrypted (migration 129 encrypts every value)."""
    from app.repository.notification_channels import decode_secrets

    readable, unreadable = decode_secrets(raw, channel_id)
    assert not unreadable
    return readable


def opened_config(kind: str, config_id: str, raw: str) -> dict:
    """``fanout_configs.config`` with its secret keys decrypted (migration 129)."""
    import json

    from app.repository.fanout import open_config

    config, unreadable = open_config(kind, config_id, json.loads(raw))
    assert not unreadable
    return config
