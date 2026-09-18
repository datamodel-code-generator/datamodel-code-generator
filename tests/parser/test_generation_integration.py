"""Exercise parser extension callables through real model generation."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from datamodel_code_generator import snooper_to_methods
from datamodel_code_generator.parser.generation import GenerationStore
from datamodel_code_generator.parser.openapi import OpenAPIParser
from datamodel_code_generator.reference import ModelResolver
from tests.conftest import assert_output

DATA = Path(__file__).parents[1] / "data"
EXPECTED = DATA / "expected/main/generation_platform"


class RecordingStore(GenerationStore):
    """A real store selected before any constructor registrations."""


class RecordingResolver(ModelResolver):
    """A real resolver selected at the parser's original construction site."""


class RecordingParser(OpenAPIParser):
    """Bounded consumer of factories and actual nonrecursive copy returns."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize recording before store and resolver construction."""
        self.events: list[str] = []
        super().__init__(*args, **kwargs)

    def _generation_store_factory(self) -> tuple[GenerationStore, list[Any]]:
        self.events.append("store")
        return RecordingStore.create_with_results()

    def _model_resolver_factory(self, *args: Any, **kwargs: Any) -> ModelResolver:
        self.events.append("resolver")
        return RecordingResolver(*args, **kwargs)

    def _copy_model_field(self, *args: Any, **kwargs: Any) -> Any:
        copied = super()._copy_model_field(*args, **kwargs)
        self.events.append(f"field:{copied is not args[0]}:{kwargs.get('register_references', True)}")
        return copied

    def _copy_model_type(self, *args: Any, **kwargs: Any) -> Any:
        copied = super()._copy_model_type(*args, **kwargs)
        self.events.append(f"type:{copied is not args[0]}:{kwargs.get('register_references', True)}")
        return copied

    def _copy_inherited_field(self, *args: Any, **kwargs: Any) -> Any:
        copied = super()._copy_inherited_field(*args, **kwargs)
        self.events.append(f"inherited:{copied is not args[0]}:{kwargs.get('register_references', True)}")
        return copied


@pytest.mark.parametrize("decorate", [False, True])
def test_parser_factory_consumer(decorate: bool) -> None:
    """Observe real copies while preserving the same engine output and guards."""

    class Consumer(RecordingParser):
        """Keep two-stage decoration local to this generation."""

    if decorate:
        Consumer = snooper_to_methods()(snooper_to_methods()(Consumer))
    options = {
        "formatters": [],
        "openapi_scopes": ["schemas", "paths"],
        "read_only_write_only_model_type": "all",
        "collapse_root_models": True,
        "reuse_model": True,
    }
    source = DATA / "generation_platform/observation.json"
    ordinary = OpenAPIParser(source, **options)
    consumer = Consumer(source, **options)
    try:
        assert_output(ordinary.parse(), EXPECTED / "parser_output.py")
        assert_output(consumer.parse(), EXPECTED / "parser_output.py")
        observation = {
            "factories": consumer.events[:2],
            "copies": dict(sorted(Counter(consumer.events[2:]).items())),
            "store": type(consumer.generation_store).__name__,
            "resolver": type(consumer.model_resolver).__name__,
            "results_are_store_models": consumer.results is consumer.generation_store.models,
            "guarded_methods": {
                name: getattr(Consumer, name) is getattr(OpenAPIParser, name)
                for name in ("_validate_schema_object", "_get_ref_raw_schema", "_load_ref_schema_object")
            }
            if not decorate
            else {},
        }
        assert_output(json.dumps(observation, indent=2) + "\n", EXPECTED / f"factories_{decorate}.txt")
    finally:
        consumer.dispose()
        ordinary.dispose()


def test_legacy_result_postprocessor_override() -> None:
    """Lookup the existing name-mangled hook after ordinary rendering."""

    class Consumer(OpenAPIParser):
        calls = 0

        @classmethod
        def _Parser__postprocess_result_modules(cls, results: Any, *, empty_init: bool = False) -> Any:  # noqa: N802
            cls.calls += 1
            return super()._Parser__postprocess_result_modules(results, empty_init=empty_init)

    parser = Consumer(DATA / "openapi/modular.yaml", treat_dot_as_module=True, formatters=[])
    try:
        results = parser.parse()
        assert_output(
            json.dumps({"calls": Consumer.calls, "keys": list(results)}, indent=2) + "\n",
            EXPECTED / "postprocessor.txt",
        )
    finally:
        parser.dispose()


def test_copy_consumer_across_modules() -> None:
    """Capture cross-module root copies made by the real renderer."""
    from datamodel_code_generator import ModuleSplitMode
    from tests.conftest import assert_parser_modules

    parser = RecordingParser(
        DATA / "generation_platform/observation.json",
        formatters=[],
        openapi_scopes=["schemas", "paths"],
        read_only_write_only_model_type="all",
        collapse_root_models=True,
        reuse_model=True,
    )
    try:
        results = parser.parse(module_split_mode=ModuleSplitMode.Single)
        assert_parser_modules(results, EXPECTED / "split")
        assert_output(
            json.dumps(dict(sorted(Counter(parser.events).items())), indent=2) + "\n", EXPECTED / "split_calls.txt"
        )
    finally:
        parser.dispose()


def test_operation_lookup_remains_dynamic() -> None:
    """A parser can replace its operation hook during the same traversal."""

    class Consumer(OpenAPIParser):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            """Prepare operation observations before parsing."""
            self.events: list[str] = []
            super().__init__(*args, **kwargs)

        def parse_operation(self, *args: Any, **kwargs: Any) -> Any:
            self.events.append("first")
            self.parse_operation = self.later_operation
            return super().parse_operation(*args, **kwargs)

        def later_operation(self, *args: Any, **kwargs: Any) -> Any:
            """Process subsequent operations through the original implementation."""
            self.events.append("later")
            return super().parse_operation(*args, **kwargs)

    parser = Consumer(DATA / "openapi/api.yaml", openapi_scopes=["schemas", "paths"], formatters=[])
    try:
        parser.parse()
        assert_output(json.dumps(parser.events) + "\n", EXPECTED / "dynamic_operations.txt")
    finally:
        parser.dispose()
