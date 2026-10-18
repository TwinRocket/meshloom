"""Typed contracts of the local accounts tables (``app/repository/auth.py``).

Secrets never sit on the general models: ``User`` has no password hash or TOTP
secret, ``AuthSession`` no token hash, ``AuthSettings`` no setup code hash. The
one model that carries a hash, ``UserCredentials``, wraps it in ``SecretStr``
and hides inputs from validation errors (``app/AGENTS.md``, "Secrets").
"""

import ipaddress
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.middleware.proxy_headers import normalize_proxies

Role = Literal["admin", "member", "viewer", "kiosk"]
Provider = Literal["local", "ingress", "system"]
HostGuardMode = Literal["observe", "enforce"]

ROLES: tuple[str, ...] = get_args(Role)
PROVIDERS: tuple[str, ...] = get_args(Provider)

USERNAME_MAX_LENGTH = 64

# ``auth_settings.audit_retention_days`` bounds (366 by default, Q-A14).
MIN_AUDIT_RETENTION_DAYS = 1
MAX_AUDIT_RETENTION_DAYS = 36500


class User(BaseModel):
    id: int
    username: str
    display_name: str = ""
    role: Role
    provider: Provider = "local"
    external_id: str | None = None
    disabled: bool = False
    must_change_password: bool = False
    has_password: bool = False
    totp_enabled_at: int | None = None
    created_at: int
    updated_at: int


class UserCredentials(BaseModel):
    """A user with the stored argon2id hash, for the login check only."""

    model_config = ConfigDict(hide_input_in_errors=True)

    user: User
    password_hash: SecretStr | None = None


class AuthSession(BaseModel):
    id: str
    user_id: int
    created_at: int
    last_seen_at: int
    expires_at: int
    ip: str | None = None
    user_agent: str | None = None
    via: str | None = None
    revoked_at: int | None = None


class AuthAuditEntry(BaseModel):
    id: int
    ts: int
    actor_user_id: int | None = None
    actor_token_id: str | None = None
    action: str
    target: str | None = None
    ip: str | None = None
    via: str | None = None
    outcome: str
    details: dict[str, Any] = Field(default_factory=dict)


class TrustedNetworkRule(BaseModel):
    """``cidr`` -> ``user_id``: requests from that network act as that user."""

    cidr: str
    user_id: int

    @field_validator("cidr")
    @classmethod
    def _canonical_cidr(cls, value: str) -> str:
        return str(ipaddress.ip_network(value.strip(), strict=False))


class AuthSettings(BaseModel):
    trusted_networks: list[TrustedNetworkRule] = Field(default_factory=list)
    trusted_proxies: list[str] = Field(default_factory=list)
    public_urls: list[str] = Field(default_factory=list)
    host_guard_mode: HostGuardMode = "observe"
    ingress_default_role: Role = "member"
    session_lifetime_days: int = Field(default=30, ge=1, le=3650)
    audit_retention_days: int = Field(
        default=366, ge=MIN_AUDIT_RETENTION_DAYS, le=MAX_AUDIT_RETENTION_DAYS
    )
    basic_imported: bool = False
    setup_code_set: bool = False

    @field_validator("trusted_proxies")
    @classmethod
    def _canonical_proxies(cls, value: list[str]) -> list[str]:
        return normalize_proxies(value)
