"""Validate generated type alias patterns through public entrypoints and generated runtime code."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import TypeAdapter, ValidationError

from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    _generated_model,
    installed_pydantic_runs_target,
    run_main_and_assert,
)
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH, assert_file_content

if TYPE_CHECKING:
    from pathlib import Path

LOOKAROUND_SCHEMAS = JSON_SCHEMA_DATA_PATH / "type_alias_lookaround"
LOOKAROUND_EXPECTED = EXPECTED_JSON_SCHEMA_PATH / "type_alias_lookaround"
LOOKAROUND_PAYLOADS = json.loads((DATA_PATH / "payloads/type_alias_lookaround/cases.json").read_text())
COMPILED_ALIAS_PATTERNS = pytest.mark.skipif(
    not installed_pydantic_runs_target("2.10"),
    reason="Compiled alias patterns need Pydantic 2.8 and referenced aliases keep their names from Pydantic 2.10",
)


def _validation_results(namespace: dict[str, Any], fixture: str) -> str:
    """Run each payload through ``TypeAdapter`` and record its error types."""
    results: dict[str, list[dict[str, Any]]] = {}
    for name, payloads in LOOKAROUND_PAYLOADS[fixture].items():
        adapter = TypeAdapter(namespace[name])
        results[name] = []
        for payload in payloads:
            try:
                adapter.validate_python(payload)
            except ValidationError as error:
                errors = sorted({detail["type"] for detail in error.errors()})
            else:
                errors = []
            results[name].append({"payload": payload, "errors": errors})
    return json.dumps(results, indent=2) + "\n"


@COMPILED_ALIAS_PATTERNS
@pytest.mark.parametrize(
    ("fixture", "variant", "extra_args"),
    [
        ("model", "alias", []),
        ("model", "annotated", ["--use-annotated"]),
        ("model", "field_constraints", ["--field-constraints"]),
        pytest.param(
            "model",
            "type_statement",
            ["--target-python-version", "3.12"],
            marks=pytest.mark.skipif(sys.version_info < (3, 12), reason="type statements need Python 3.12"),
        ),
        ("model", "reuse_model", ["--reuse-model"]),
        ("model", "dataclass", ["--output-model-type", "pydantic_v2.dataclass"]),
        ("collapse", "collapse", ["--collapse-root-models", "--field-constraints"]),
    ],
)
def test_type_alias_lookaround(output_file: Path, fixture: str, variant: str, extra_args: list[str]) -> None:
    """Aliases compile lookaround patterns, so they validate without a model config.

    Collapsed BaseModel fields keep plain patterns and the model's ``regex_engine`` config.
    """
    run_main_and_assert(
        input_path=LOOKAROUND_SCHEMAS / f"{fixture}.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=f"type_alias_lookaround/{variant}.py",
        extra_args=["--use-type-alias", "--disable-timestamp", *extra_args],
        force_exec_validation=True,
    )
    module_name = f"type_alias_lookaround_{variant}"
    with _generated_model(output_file, module_name, "Holder"):
        results = _validation_results(vars(sys.modules[module_name]), fixture)
    assert_output(results, LOOKAROUND_EXPECTED / f"{fixture}_runtime.txt")


@COMPILED_ALIAS_PATTERNS
def test_type_alias_lookaround_reuse_scope_tree(output_dir: Path) -> None:
    """A lookaround alias moved to the shared module validates and its users import."""
    run_main_and_assert(
        input_path=LOOKAROUND_SCHEMAS / "tree",
        output_path=output_dir,
        input_file_type="jsonschema",
        expected_directory=LOOKAROUND_EXPECTED / "tree",
        extra_args=["--use-type-alias", "--reuse-model", "--reuse-scope", "tree", "--disable-timestamp"],
        runtime_validation_module="schema_a",
        runtime_validation_model_name="Model",
        runtime_validation_data={"code": "abc"},
    )
    with _generated_model(output_dir / "shared.py", "type_alias_lookaround_tree_shared", "Code"):
        results = _validation_results(vars(sys.modules["type_alias_lookaround_tree_shared"]), "tree")
    assert_output(results, LOOKAROUND_EXPECTED / "tree_runtime.txt")
