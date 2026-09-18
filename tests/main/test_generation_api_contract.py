"""Verify the public API scope surface and the ordinary/capture engine contract."""

from __future__ import annotations

import gc
import json
from pathlib import Path

import pytest

from datamodel_code_generator import GenerateConfig, OpenAPIScope, _prepare_generate_facade_config, _run_generation
from datamodel_code_generator.parser.openapi_scope import ApiOpenAPIParser
from tests.conftest import assert_output
from tests.main.test_generation_api_scope import ApiAttemptConsumer

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

    import datamodel_code_generator
    from datamodel_code_generator.parser.openapi_scope import ApiDeclarationFrame
    from tests.data.generation_platform.api_scope.typing.annotations import identity

    assert_output(
        json.dumps(
            {
                "scopes": [scope.value for scope in OpenAPIScope],
                "module": ApiOpenAPIParser.__module__,
                "constructor": str(inspect.signature(ApiOpenAPIParser)),
                "constructor_hint_keys": sorted(get_type_hints(ApiOpenAPIParser.__init__, include_extras=True)),
                "frame_hint_keys": sorted(get_type_hints(ApiDeclarationFrame, include_extras=True)),
                "private_alias_resolved": get_type_hints(identity, include_extras=True)["return"]
                == ApiOpenAPIParser | None,
                "top_level_export": hasattr(datamodel_code_generator, "ApiOpenAPIParser"),
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
    import sys

    from tests.data.python.generation_observer import GenerationObserver

    consumer = ApiAttemptConsumer("none")
    outputs = []
    calls = []
    for capture in (None, consumer):
        observer = GenerationObserver()
        previous = sys.getprofile()
        config = _prepare_generate_facade_config(
            GenerateConfig(
                input_file_type="openapi",
                openapi_scopes=[OpenAPIScope.Api],
                disable_timestamp=True,
                formatters=[],
            )
        )
        try:
            sys.setprofile(observer.record)
            outputs.append(
                _run_generation(SOURCE / f"{case}.json", config, Path.cwd(), use_output_cwd=False, capture=capture)
            )
        finally:
            sys.setprofile(previous)
        calls.append(observer.calls)
    consumer.close()
    gc.collect()
    assert_output(
        json.dumps(
            {
                "outputs_equal": outputs[0] == outputs[1],
                "engine_calls_equal": calls[0] == calls[1],
                "factory_reads": consumer.factory_reads,
                "retained_parsers": sum(parser() is not None for parser in consumer.parsers),
            },
            indent=2,
        )
        + "\n",
        EXPECTED / "capture-parity.txt",
    )
