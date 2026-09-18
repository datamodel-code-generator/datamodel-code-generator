"""Exercise API factory capability and shared attempt lifetime end to end."""

from __future__ import annotations

import gc
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import (
    Error,
    GenerateConfig,
    OpenAPIScope,
    _prepare_generate_facade_config,
    _run_generation,
)
from datamodel_code_generator.parser.openapi_scope import ApiOpenAPIParser
from tests.conftest import assert_output
from tests.main.test_generation_attempts import AttemptConsumer, AttemptParser

if TYPE_CHECKING:
    from datamodel_code_generator import _ParserSource
    from datamodel_code_generator.config import OpenAPIParserConfig

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/api_scope"
EXPECTED = DATA / "expected/main/generation_platform/api_scope"


class ApiAttemptParser(AttemptParser, ApiOpenAPIParser):
    """Reuse the existing exceptional-attempt injector with the actual API walker."""


class ApiAttemptFactory:
    """Expose explicit capability on the actual callable selected by the driver."""

    _supports_api_scope = True

    def __init__(self, session: ApiAttemptConsumer) -> None:
        """Bind the existing attempt observer to this factory."""
        self.session = session

    def __call__(self, *, source: _ParserSource, config: OpenAPIParserConfig) -> ApiAttemptParser:
        """Construct the real API parser with the shared observer."""
        return ApiAttemptParser(self.session, source=source, config=config)


class ApiAttemptConsumer(AttemptConsumer):
    """Count actual factory selection while preserving S01 attempt observations."""

    factory_reads = 0

    @property
    def parser_factory(self) -> ApiAttemptFactory:
        """Observe factory reads without changing parser construction."""
        self.factory_reads += 1
        return ApiAttemptFactory(self)


@pytest.mark.parametrize("failure", ["none", "collapse_once"])
@pytest.mark.parametrize("empty", [False, True])
def test_api_capture_factory_attempts(failure: str, empty: bool) -> None:
    """Use one selected API factory for ordinary generation and collapse retries."""
    source = SOURCE / ("contentless.json" if empty else "declarations.json")
    consumer = ApiAttemptConsumer(failure)
    config = _prepare_generate_facade_config(
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            collapse_root_models=failure == "collapse_once",
            input_filename="api.json",
            use_operation_id_as_name=not empty,
            disable_timestamp=True,
            formatters=["black", "isort"],
        )
    )
    try:
        result = _run_generation(source, config, Path.cwd(), use_output_cwd=False, capture=consumer)
        if not empty:
            assert_output(result, EXPECTED / "pydantic_v2_BaseModel.py")
    finally:
        consumer.close()
    gc.collect()
    assert_output(
        json.dumps(
            {
                "empty_result": result if empty else None,
                "factory_reads": consumer.factory_reads,
                "accepted_attempt": consumer.accepted[0] if consumer.accepted else None,
                "retained_parsers": sum(parser() is not None for parser in consumer.parsers),
            },
            indent=2,
        )
        + "\n",
        EXPECTED / f"capture-{failure}.txt",
    )


def test_api_scope_does_not_allow_empty_jsonschema() -> None:
    """Keep legacy no-model failures for an actually detected JSON Schema input."""
    source = SOURCE / "contentless.json"
    config = _prepare_generate_facade_config(
        GenerateConfig(
            input_file_type="jsonschema",
            openapi_scopes=[OpenAPIScope.Api],
            skip_root_model=True,
            disable_timestamp=True,
            formatters=[],
        )
    )
    with pytest.raises(Error, match="Models not found in the input data"):
        _run_generation(source, config, Path.cwd(), use_output_cwd=False)


def test_api_empty_factory_requires_capability() -> None:
    """Keep empty-result rejection when the selected callable does not advertise API support."""

    class UnadvertisedFactory(ApiAttemptFactory):
        _supports_api_scope = False

    class UnadvertisedConsumer(ApiAttemptConsumer):
        @property
        def parser_factory(self) -> ApiAttemptFactory:
            return UnadvertisedFactory(self)

    consumer = UnadvertisedConsumer("none")
    config = _prepare_generate_facade_config(
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            formatters=[],
        )
    )
    with pytest.raises(Error, match="Models not found in the input data"):
        _run_generation(SOURCE / "contentless.json", config, Path.cwd(), use_output_cwd=False, capture=consumer)
