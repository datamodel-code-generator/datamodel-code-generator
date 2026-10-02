"""Verify the public API scope surface and the ordinary/capture engine contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import datamodel_code_generator as dcg
from datamodel_code_generator.parser.openapi_scope import ApiOpenAPIParser
from tests.conftest import assert_output

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/api_scope"
EXPECTED = DATA / "expected/main/generation_platform/api_scope"


@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
def test_api_cli_backends(backend: str, tmp_path: Path) -> None:
    """Select API scope through real CLI auto-detection for every builtin backend."""
    from tests.main.conftest import run_main_and_assert

    source = tmp_path / "api.json"
    source.write_bytes((SOURCE / "declarations.json").read_bytes())
    run_main_and_assert(
        input_path=source,
        output_path=tmp_path / "models.py",
        extra_args=[
            "--openapi-scopes",
            "api",
            "--use-operation-id-as-name",
            "--disable-timestamp",
            "--output-model-type",
            backend,
            "--formatters",
            "black",
            "isort",
        ],
        expected_output=(EXPECTED / f"{backend.replace('.', '_')}.py").read_text() + "\n",
    )


def test_api_public_surface_annotations() -> None:
    """Resolve the authorized additive surface without altering old API baselines."""
    import inspect
    from typing import get_type_hints

    from datamodel_code_generator.parser.openapi_scope import ApiDeclarationFrame
    from tests.data.generation_platform.api_scope.typing.annotations import identity

    assert_output(
        json.dumps(
            {
                "scopes": [scope.value for scope in dcg.OpenAPIScope],
                "module": ApiOpenAPIParser.__module__,
                "constructor": str(inspect.signature(ApiOpenAPIParser)),
                "constructor_hint_keys": sorted(get_type_hints(ApiOpenAPIParser.__init__, include_extras=True)),
                "frame_hint_keys": sorted(get_type_hints(ApiDeclarationFrame, include_extras=True)),
                "private_alias_resolved": get_type_hints(identity, include_extras=True)["return"]
                == ApiOpenAPIParser | None,
                "top_level_export": hasattr(dcg, "ApiOpenAPIParser"),
            },
            indent=2,
        )
        + "\n",
        EXPECTED / "public-surface.txt",
    )
