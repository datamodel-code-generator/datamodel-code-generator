"""Exercise API factory capability and shared attempt lifetime end to end."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datamodel_code_generator import Error, GenerateConfig, InputFileType, OpenAPIScope, generate
from datamodel_code_generator._openapi_generation import OpenAPIGenerationSession
from tests.conftest import assert_output
from tests.data.python.generation_session_inputs import observe_api_session

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/api_scope"
EXPECTED = DATA / "expected/main/generation_platform/api_scope"


@pytest.mark.parametrize("empty", [False, True])
def test_api_capture_factory_attempts(empty: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    """Use one selected API factory for a collapse retry injected into the first attempt."""
    source = SOURCE / ("contentless.json" if empty else "declarations.json")
    result, observation = observe_api_session(
        source,
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            collapse_root_models=True,
            input_filename="api.json",
            use_operation_id_as_name=not empty,
            disable_timestamp=True,
            formatters=["black", "isort"],
        ),
        failure="collapse_once",
        monkeypatch=monkeypatch,
    )
    if not empty:
        assert_output(result, EXPECTED / "pydantic_v2_BaseModel.py")
    else:
        observation["empty_result"] = result
    assert_output(json.dumps(observation, indent=2) + "\n", EXPECTED / "capture-collapse_once.txt")


def test_api_scope_does_not_allow_empty_jsonschema() -> None:
    """Keep legacy no-model failures for an actually detected JSON Schema input."""
    with pytest.raises(Error, match="Models not found in the input data"):
        generate(
            SOURCE / "contentless.json",
            input_file_type=InputFileType.JsonSchema,
            openapi_scopes=[OpenAPIScope.Api],
            skip_root_model=True,
            disable_timestamp=True,
            formatters=[],
        )


def test_api_empty_factory_requires_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject the empty result if the actual capture factory loses its capability."""
    monkeypatch.setattr(OpenAPIGenerationSession, "_supports_api_scope", False)
    with pytest.raises(Error, match="Models not found in the input data"):
        observe_api_session(
            SOURCE / "contentless.json",
            GenerateConfig(input_file_type="openapi", openapi_scopes=[OpenAPIScope.Api], formatters=[]),
        )
