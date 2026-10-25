"""The pydantic-generated schema the frontend's `SchemaForm` tests render.

`frontend/src/test/fixtures/schemaForm.pydantic.json` is what `model_json_schema()` emits
for this model (a `SecretStr` -> `writeOnly`, an `Optional` -> `anyOf` with null, an enum
-> a local `$ref` into `$defs`). The test fails when pydantic's output drifts, so the
fixture never claims a shape pydantic does not produce.
"""

import json
from enum import StrEnum
from pathlib import Path

from pydantic import AnyHttpUrl, BaseModel, Field, SecretStr

FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "frontend"
    / "src"
    / "test"
    / "fixtures"
    / "schemaForm.pydantic.json"
)


class Level(StrEnum):
    info = "info"
    warning = "warning"


class ExampleChannel(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    token: SecretStr
    link: AnyHttpUrl | None = None
    note: str | None = Field(default=None, max_length=200)
    level: Level = Level.info
    retries: int = Field(default=3, ge=0, le=10)
    ratio: float = Field(default=0.5, ge=0, le=1)
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)


def test_fixture_matches_pydantic_output():
    generated = ExampleChannel.model_json_schema()
    assert json.loads(FIXTURE.read_text("utf-8")) == generated, (
        'regenerate with: uv run python -c "import json,tests.test_schema_form_fixture as t; '
        'print(json.dumps(t.ExampleChannel.model_json_schema(), indent=2))"'
    )
