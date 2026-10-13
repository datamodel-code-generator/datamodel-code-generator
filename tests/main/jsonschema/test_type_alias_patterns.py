"""Validate generated type alias patterns through public entrypoints and generated runtime code."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ConfigDict, TypeAdapter, ValidationError

from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    TARGET_PYDANTIC_VERSION_CASES,
    _generated_model,
    installed_pydantic_runs_target,
    run_main_and_assert,
    target_pydantic_args,
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


MODEL_NAME = "Holder"
STRING_PATTERN_TARGET = "2"
"""The target that predates compiled patterns (Pydantic 2.8) and keeps string patterns in type aliases."""


def _expected(variant: str, target_pydantic_version: str | None) -> str:
    """Name the expected output; an explicit newer target shares the output of an unset target."""
    directory = "target_2/" if target_pydantic_version == STRING_PATTERN_TARGET else ""
    return f"type_alias_lookaround/{directory}{variant}"


def _validation_results(namespace: dict[str, Any], fixture: str, target_pydantic_version: str | None) -> str:
    """Run each payload through ``TypeAdapter`` and record its error types.

    A string pattern takes its engine from the config that builds it: the model that uses the alias, or the
    caller's config when the alias is validated on its own.
    """
    alias_config = ConfigDict(regex_engine="python-re") if target_pydantic_version == STRING_PATTERN_TARGET else None
    results: dict[str, list[dict[str, Any]]] = {}
    for name, payloads in LOOKAROUND_PAYLOADS[fixture].items():
        adapter = TypeAdapter(namespace[name], config=None if name == MODEL_NAME else alias_config)
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
@pytest.mark.parametrize("target_pydantic_version", TARGET_PYDANTIC_VERSION_CASES)
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
def test_type_alias_lookaround(
    output_file: Path, fixture: str, variant: str, extra_args: list[str], target_pydantic_version: str | None
) -> None:
    """Aliases compile lookaround patterns, so they validate without a model config.

    Collapsed BaseModel fields keep plain patterns and the model's ``regex_engine`` config. Compiled patterns
    need Pydantic 2.8, so ``--target-pydantic-version 2`` keeps string patterns in the aliases and selects the
    engine in every model that uses them; each target validates the same payloads.
    """
    run_main_and_assert(
        input_path=LOOKAROUND_SCHEMAS / f"{fixture}.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=f"{_expected(variant, target_pydantic_version)}.py",
        extra_args=[
            "--use-type-alias",
            "--disable-timestamp",
            *extra_args,
            *target_pydantic_args(target_pydantic_version),
        ],
        force_exec_validation=True,
    )
    module_name = f"type_alias_lookaround_{variant}"
    with _generated_model(output_file, module_name, MODEL_NAME):
        results = _validation_results(vars(sys.modules[module_name]), fixture, target_pydantic_version)
    assert_output(results, LOOKAROUND_EXPECTED / f"{fixture}_runtime.txt")


@COMPILED_ALIAS_PATTERNS
@pytest.mark.parametrize("target_pydantic_version", TARGET_PYDANTIC_VERSION_CASES)
def test_type_alias_lookaround_reuse_scope_tree(output_dir: Path, target_pydantic_version: str | None) -> None:
    """A lookaround alias moved to the shared module validates and its users import."""
    run_main_and_assert(
        input_path=LOOKAROUND_SCHEMAS / "tree",
        output_path=output_dir,
        input_file_type="jsonschema",
        expected_directory=EXPECTED_JSON_SCHEMA_PATH / _expected("tree", target_pydantic_version),
        extra_args=[
            "--use-type-alias",
            "--reuse-model",
            "--reuse-scope",
            "tree",
            "--disable-timestamp",
            *target_pydantic_args(target_pydantic_version),
        ],
        runtime_validation_module="schema_a",
        runtime_validation_model_name="Model",
        runtime_validation_data={"code": "abc"},
    )
    with _generated_model(output_dir / "shared.py", "type_alias_lookaround_tree_shared", "Code"):
        results = _validation_results(
            vars(sys.modules["type_alias_lookaround_tree_shared"]), "tree", target_pydantic_version
        )
    assert_output(results, LOOKAROUND_EXPECTED / "tree_runtime.txt")
