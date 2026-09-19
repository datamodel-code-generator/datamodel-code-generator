"""Verify the public API scope surface and the ordinary/capture engine contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import datamodel_code_generator as dcg
from datamodel_code_generator.parser.openapi_scope import ApiOpenAPIParser
from tests.conftest import assert_output
from tests.data.python.generation_session_inputs import compare_api_session

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


@pytest.mark.parametrize(
    "case",
    [
        "declarations",
        "external",
        "media-30",
        "media-32",
        "items-primitive",
        "discriminator",
        "headers",
        "resources",
        "local-roots",
        "traversal",
    ],
)
def test_api_capture_engine_parity(case: str) -> None:
    """Compare real output and engine call counts with the bounded capture consumer."""
    observation = compare_api_session(
        SOURCE / f"{case}.json",
        dcg.GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[dcg.OpenAPIScope.Api],
            disable_timestamp=True,
            formatters=[],
        ),
    )
    assert_output(json.dumps(observation, indent=2) + "\n", EXPECTED / "capture-parity.txt")


@pytest.mark.parametrize("tags", [False, True])
def test_api_declaration_frame_origins(tags: bool) -> None:
    """Observe original indices and inherited security without replacing engine hooks."""
    import sys

    from tests.conftest import assert_inputs_not_mutated
    from tests.data.python.api_declaration_observer import DeclarationObserver

    source = json.loads((SOURCE / "traversal.json").read_text())
    observer = DeclarationObserver()
    previous = sys.getprofile()
    try:
        sys.setprofile(observer.record)
        with assert_inputs_not_mutated({"source": source}):
            result = dcg.generate(
                source,
                input_file_type=dcg.InputFileType.OpenAPI,
                input_filename="traversal.json",
                openapi_scopes=[dcg.OpenAPIScope.Api, dcg.OpenAPIScope.Tags] if tags else [dcg.OpenAPIScope.Api],
                use_title_as_name=True,
                include_path_parameters=False,
                disable_timestamp=True,
                formatters=["black", "isort"],
            )
    finally:
        sys.setprofile(previous)
    assert_output(result, EXPECTED / "traversal.py")
    assert_output(
        json.dumps({"operation": observer.operation, "tags": observer.tags}, indent=2) + "\n",
        EXPECTED / f"frame-origins-{tags}.txt",
    )
