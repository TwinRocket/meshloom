"""Plugin manifest model (`manifest.json`, version 1).

The JSON schema exported from `Manifest` (`docs/dev/plugins/manifest.schema.json`,
written by `scripts/build/export_plugin_contract.py`) is a contract shared with
meshloom-plugins: change it in a `CONTRACTS:` commit. Rules that cannot be
expressed in JSON Schema (the id/origin rules, the anti-impersonation name rule,
the `*.meshloom.app` host rule, SPDX membership) are enforced here and covered by
the golden vectors in `docs/dev/plugins/vectors/`.
"""

import json
import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    model_validator,
)
from pydantic.config import JsonDict
from pydantic_core import PydanticCustomError

from app.plugins.errors import PluginIssue, PluginValidationError, issues_from_pydantic
from app.plugins.licenses import ACCEPTED_LICENSES
from app.plugins.permissions import Host, Permissions, is_meshloom_domain

MANIFEST_MAX_BYTES = 64 * 1024
RESERVED_NAMESPACES = ("meshloom", "local")
MISLEADING_NAME_WORDS = ("meshloom", "official", "officiel")

# Who is submitting the manifest. Third-party plugins may not use the reserved
# namespaces; bundled plugins are `meshloom.*`, local ones `local.*`.
Origin = Literal["third_party", "bundled", "local"]

_ICONS_PATH = Path(__file__).resolve().parent.parent / "data" / "plugin_icons.json"
_ID_RE = r"^[a-z0-9]+(-[a-z0-9]+)*\.[a-z0-9]+(-[a-z0-9]+)*$"
# Official semver.org regular expression.
_SEMVER_RE = (
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)
_LANG_PATTERN = r"^[a-z]{2}(-[A-Z]{2})?$"
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
_ENTRY_RE = re.compile(
    r"^(?P<path>[A-Za-z0-9_][A-Za-z0-9_./-]*)(?::(?P<attr>[A-Za-z_][A-Za-z0-9_]*))?$"
)


def _enum(values: Iterable[str]) -> JsonDict:
    return {"enum": [*values]}


@lru_cache(maxsize=1)
def plugin_icons() -> tuple[str, ...]:
    """The closed list of icon names (lucide names the frontend ships)."""
    return tuple(json.loads(_ICONS_PATH.read_text(encoding="utf-8")))


def safe_relative_path(value: str) -> str:
    """A normalised relative POSIX path inside the archive: no `..`, no absolute path."""
    if (
        not value
        or "\\" in value
        or "\x00" in value
        or value.startswith("/")
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise PydanticCustomError("path_unsafe", "must be a relative POSIX path inside the package")
    parts = PurePosixPath(value).parts
    if any(p in ("..", ".") for p in value.split("/")) or "" in value.split("/"):
        raise PydanticCustomError("path_unsafe", "must not contain '.', '..' or empty segments")
    if len(parts) > 8 or len(value) > 200:
        raise PydanticCustomError("path_unsafe", "path is too long or too deep")
    return value


def _https_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        _ = parts.port
    except ValueError:
        raise PydanticCustomError("url_invalid", "not a valid URL") from None
    if parts.scheme != "https" or not parts.hostname:
        raise PydanticCustomError("url_not_https", "URL must be absolute and use https:")
    if parts.username or parts.password:
        raise PydanticCustomError("url_not_https", "URL must not carry credentials")
    if _CONTROL_RE.search(value) or " " in value:
        raise PydanticCustomError("url_invalid", "not a valid URL")
    return value


def _text(value: str) -> str:
    if _CONTROL_RE.search(value) or value != value.strip():
        raise PydanticCustomError("text_invalid", "no control characters or surrounding blanks")
    return value


def _semver_comparable(value: str) -> str:
    try:
        Version(value)
    except InvalidVersion:
        raise PydanticCustomError(
            "version_not_comparable",
            "version has a pre-release form that cannot be ordered (use e.g. 1.0.0-rc.1)",
        ) from None
    return value


def _specifier(value: str) -> str:
    try:
        SpecifierSet(value)
    except InvalidSpecifier:
        raise PydanticCustomError("specifier_invalid", "not a valid version range") from None
    return value


def _license(value: str) -> str:
    if value not in ACCEPTED_LICENSES:
        raise PydanticCustomError(
            "license_not_accepted", "licence is not in the accepted SPDX list"
        )
    return value


def _icon(value: str) -> str:
    if value not in plugin_icons():
        raise PydanticCustomError("icon_unknown", "icon is not in the allowed icon list")
    return value


def _i18n_texts(max_len: int):
    def check(value: dict[str, str]) -> dict[str, str]:
        if "en" not in value:
            raise PydanticCustomError("i18n_en_required", "the 'en' entry is required")
        for lang, text in value.items():
            if not re.match(_LANG_PATTERN, lang):
                raise PydanticCustomError("i18n_language_invalid", "invalid language code")
            if not text or len(text) > max_len:
                raise PydanticCustomError(
                    "text_invalid", "text must be 1 to {max_len} characters", {"max_len": max_len}
                )
            _text(text)
        return value

    return check


def _lang_paths(suffix: str, require_en: bool):
    def check(value: dict[str, str]) -> dict[str, str]:
        if require_en and "en" not in value:
            raise PydanticCustomError("i18n_en_required", "the 'en' entry is required")
        for lang, path in value.items():
            if not re.match(_LANG_PATTERN, lang):
                raise PydanticCustomError("i18n_language_invalid", "invalid language code")
            safe_relative_path(path)
            if not path.endswith(suffix):
                raise PydanticCustomError(
                    "path_extension", "path must end with {suffix}", {"suffix": suffix}
                )
        return value

    return check


HttpsUrl = Annotated[str, Field(max_length=2048), AfterValidator(_https_url)]
I18nName = Annotated[
    dict[str, str], Field(min_length=1, max_length=32), AfterValidator(_i18n_texts(60))
]
I18nSummary = Annotated[
    dict[str, str], Field(min_length=1, max_length=32), AfterValidator(_i18n_texts(200))
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Author(_Model):
    name: Annotated[str, Field(min_length=1, max_length=80), AfterValidator(_text)]
    url: HttpsUrl | None = None


class Runtime(_Model):
    type: Literal["python"]
    api: int = Field(ge=1, le=1000)
    python: Annotated[str, Field(max_length=100), AfterValidator(_specifier)]


class Compat(_Model):
    meshloom: Annotated[str, Field(max_length=100), AfterValidator(_specifier)]


class Privacy(_Model):
    sends_to_third_parties: list[Host] = Field(max_length=20)
    stores_message_content: bool


class Manifest(_Model):
    manifest: Literal[1]
    id: Annotated[str, Field(max_length=64, pattern=_ID_RE)]
    version: Annotated[
        str, Field(max_length=64, pattern=_SEMVER_RE), AfterValidator(_semver_comparable)
    ]
    name: I18nName
    summary: I18nSummary
    authors: list[Author] = Field(min_length=1, max_length=10)
    license: Annotated[
        str, Field(json_schema_extra=_enum(sorted(ACCEPTED_LICENSES))), AfterValidator(_license)
    ]
    homepage: HttpsUrl | None = None
    icon: (
        Annotated[str, Field(json_schema_extra=_enum(plugin_icons())), AfterValidator(_icon)] | None
    ) = None
    categories: list[Literal["bot", "utility", "monitoring", "integration", "fun"]] = Field(
        min_length=1, max_length=3
    )
    runtime: Runtime
    compat: Compat
    entry: Annotated[str, Field(max_length=200)]
    hooks: list[Literal["message", "raw_packet", "contact", "telemetry", "health", "timer"]] = (
        Field(max_length=6)
    )
    permissions: Permissions
    privacy: Privacy
    config_schema: Annotated[str, AfterValidator(safe_relative_path)] | None = Field(
        default=None,
        max_length=200,
        description="Path of the config schema file, e.g. config.schema.json",
    )
    i18n: Annotated[
        dict[str, str], Field(max_length=32), AfterValidator(_lang_paths(".json", False))
    ] = Field(default_factory=dict)
    readme: Annotated[
        dict[str, str], Field(min_length=1, max_length=32), AfterValidator(_lang_paths(".md", True))
    ]

    @model_validator(mode="after")
    def _cross_rules(self, info: ValidationInfo) -> "Manifest":
        origin: Origin = (info.context or {}).get("origin", "third_party")
        namespace = self.id.split(".", 1)[0]
        expected = {"bundled": "meshloom", "local": "local"}.get(origin)
        if expected is not None and namespace != expected:
            raise PydanticCustomError(
                "id_namespace_mismatch",
                "a {origin} plugin id starts with '{ns}.'",
                {"origin": origin, "ns": expected},
            )
        if expected is None and namespace in RESERVED_NAMESPACES:
            raise PydanticCustomError(
                "id_namespace_reserved",
                "the '{ns}.' id namespace is reserved",
                {"ns": namespace},
            )
        if namespace != "meshloom":
            for text in self.name.values():
                folded = text.casefold()
                if any(word in folded for word in MISLEADING_NAME_WORDS):
                    raise PydanticCustomError(
                        "name_misleading",
                        "the name must not contain 'meshloom', 'official' or 'officiel'",
                    )
            if self.permissions.http is not None and any(
                is_meshloom_domain(h) for h in self.permissions.http.hosts
            ):
                raise PydanticCustomError(
                    "http_host_reserved", "meshloom.app hosts are reserved to meshloom.* plugins"
                )
        m = _ENTRY_RE.match(self.entry)
        if m is None:
            raise PydanticCustomError("entry_invalid", "entry is 'path[:Class]' inside the package")
        safe_relative_path(m.group("path"))
        if len(set(self.hooks)) != len(self.hooks):
            raise PydanticCustomError("hooks_duplicate", "hooks must be unique")
        if len(set(self.categories)) != len(self.categories):
            raise PydanticCustomError("categories_duplicate", "categories must be unique")
        return self

    @property
    def entry_path(self) -> str:
        """Package-relative path of the entry module or package (without `:Class`)."""
        return self.entry.split(":", 1)[0]

    @property
    def entry_attr(self) -> str | None:
        return self.entry.split(":", 1)[1] if ":" in self.entry else None


def parse_manifest(data: Any, *, origin: Origin = "third_party") -> Manifest:
    """Validate manifest data; raise PluginValidationError with every issue found."""
    try:
        return Manifest.model_validate(data, context={"origin": origin})
    except ValidationError as exc:
        raise PluginValidationError(issues_from_pydantic(exc)) from None


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out


def load_manifest_json(raw: bytes, *, origin: Origin = "third_party") -> Manifest:
    """Parse the raw bytes of a `manifest.json` (size-capped, duplicate keys refused)."""
    if len(raw) > MANIFEST_MAX_BYTES:
        raise PluginValidationError(
            [PluginIssue("manifest_too_large", "manifest.json", "manifest.json is over 64 KiB")]
        )
    try:
        data = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)),
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise PluginValidationError(
            [PluginIssue("manifest_not_json", "manifest.json", "manifest.json is not valid JSON")]
        ) from None
    return parse_manifest(data, origin=origin)


def manifest_json_schema() -> dict[str, Any]:
    """The exported contract (see `scripts/build/export_plugin_contract.py`)."""
    schema = Manifest.model_json_schema(by_alias=True, mode="validation")
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **schema}
