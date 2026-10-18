"""Typed validation errors shared by the manifest, config schema and package validators."""

from dataclasses import dataclass

from pydantic import ValidationError


@dataclass(frozen=True)
class PluginIssue:
    """One validation failure.

    `code` is stable and machine-readable (a snake_case pydantic error type such as
    `missing`, or one of ours such as `url_not_https`); `path` locates it (dotted
    manifest path, or an archive entry name); `message` is for humans and never
    carries the rejected value.
    """

    code: str
    path: str
    message: str


class PluginValidationError(ValueError):
    def __init__(self, issues: list[PluginIssue]):
        self.issues = issues
        super().__init__(
            "; ".join(f"{i.code} at {i.path or '<root>'}: {i.message}" for i in issues)
        )

    @property
    def codes(self) -> list[str]:
        return [i.code for i in self.issues]


def issues_from_pydantic(exc: ValidationError, prefix: str = "") -> list[PluginIssue]:
    out: list[PluginIssue] = []
    for err in exc.errors(include_input=False, include_url=False):
        loc = ".".join(str(p) for p in err["loc"])
        out.append(PluginIssue(err["type"], f"{prefix}{loc}", err["msg"]))
    return out
