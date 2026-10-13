"""Validate generated type alias patterns through public entrypoints and generated runtime code."""

from __future__ import annotations

import inspect
import json
import sys
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ConfigDict, TypeAdapter, ValidationError

from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
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
MODEL_NAME = "Holder"
STRING_PATTERN_TARGET = "2"
"""The target that predates compiled patterns (Pydantic 2.8) and keeps string patterns in type aliases."""
ALIAS_PATTERN_TARGETS = (
    pytest.param(
        None,
        id="target-unset",
        marks=pytest.mark.skipif(
            not installed_pydantic_runs_target("2.10"),
            reason="Compiled alias patterns need Pydantic 2.8, and referenced aliases keep their names from 2.10",
        ),
    ),
    pytest.param(
        STRING_PATTERN_TARGET,
        id="target-2",
        marks=pytest.mark.skipif(
            not installed_pydantic_runs_target("2.5"),
            reason="String alias patterns take the engine from a model config, which needs Pydantic 2.5",
        ),
    ),
    pytest.param(
        "2.12",
        id="target-2.12",
        marks=pytest.mark.skipif(not installed_pydantic_runs_target("2.12"), reason="The target needs Pydantic 2.12"),
    ),
)
"""Each target with the oldest Pydantic that runs the alias patterns it writes."""
TYPE_ALIAS = ["--use-type-alias"]
DATACLASS = ["--output-model-type", "pydantic_v2.dataclass"]
ALIAS_OF_ALIAS_RUNTIME = pytest.mark.skipif(
    not installed_pydantic_runs_target("2.10"),
    reason="Pydantic before 2.10 cannot fill a TypeAliasType that only names another one",
)


def _expected(variant: str, target_pydantic_version: str | None) -> str:
    """Name the expected output; an explicit newer target shares the output of an unset target."""
    directory = "target_2/" if target_pydantic_version == STRING_PATTERN_TARGET else ""
    return f"type_alias_lookaround/{directory}{variant}"


def _validation_results(namespace: dict[str, Any], fixture: str, target_pydantic_version: str | None) -> str:
    """Run each payload through ``TypeAdapter`` and record its error types.

    A string pattern takes its engine from the config that builds it: a model that reaches the alias, or the
    caller's config when the alias is validated on its own.
    """
    alias_config = ConfigDict(regex_engine="python-re") if target_pydantic_version == STRING_PATTERN_TARGET else None
    results: dict[str, list[dict[str, Any]]] = {}
    for name, payloads in LOOKAROUND_PAYLOADS[fixture].items():
        adapter = TypeAdapter(generated := namespace[name], config=None if inspect.isclass(generated) else alias_config)
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


@pytest.mark.parametrize("target_pydantic_version", ALIAS_PATTERN_TARGETS)
@pytest.mark.parametrize(
    ("fixture", "variant", "args"),
    [
        ("model", "alias", TYPE_ALIAS),
        ("model", "annotated", [*TYPE_ALIAS, "--use-annotated"]),
        ("model", "field_constraints", [*TYPE_ALIAS, "--field-constraints"]),
        pytest.param(
            "model",
            "type_statement",
            [*TYPE_ALIAS, "--target-python-version", "3.12"],
            marks=[
                pytest.mark.skipif(sys.version_info < (3, 12), reason="type statements need Python 3.12"),
                pytest.mark.skipif(
                    not installed_pydantic_runs_target("2.10"),
                    reason="Pydantic builds models that use type statements from 2.10",
                ),
            ],
        ),
        pytest.param("model", "reuse_model", [*TYPE_ALIAS, "--reuse-model"], marks=ALIAS_OF_ALIAS_RUNTIME),
        ("model", "dataclass", [*TYPE_ALIAS, *DATACLASS]),
        ("collapse", "collapse", [*TYPE_ALIAS, "--collapse-root-models", "--field-constraints"]),
        ("nested", "nested", TYPE_ALIAS),
        ("nested", "nested_dataclass", DATACLASS),
        ("siblings", "siblings", TYPE_ALIAS),
        ("siblings", "siblings_dataclass", DATACLASS),
        ("inherited", "inherited", TYPE_ALIAS),
        ("inherited", "inherited_dataclass", DATACLASS),
        ("multiple_bases", "multiple_bases", TYPE_ALIAS),
        ("multiple_bases", "multiple_bases_dataclass", DATACLASS),
        ("annotated_root", "annotated_root", ["--use-annotated"]),
    ],
)
def test_type_alias_lookaround(
    output_file: Path, fixture: str, variant: str, args: list[str], target_pydantic_version: str | None
) -> None:
    """Aliases compile lookaround patterns, so they validate without a model config.

    Collapsed BaseModel fields keep plain patterns and the model's ``regex_engine`` config. Compiled patterns
    need Pydantic 2.8, so ``--target-pydantic-version 2`` keeps string patterns in the aliases and selects the
    engine in every model that reaches them: through a nested model, through the bases of a model it uses,
    as a dataclass whose first base does not carry the config, or as a ``RootModel``, which then names no
    alias in its generic. Each target validates the same payloads.
    """
    run_main_and_assert(
        input_path=LOOKAROUND_SCHEMAS / f"{fixture}.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=f"{_expected(variant, target_pydantic_version)}.py",
        extra_args=["--disable-timestamp", *args, *target_pydantic_args(target_pydantic_version)],
        force_exec_validation=True,
    )
    module_name = f"type_alias_lookaround_{variant}"
    with _generated_model(output_file, module_name, MODEL_NAME):
        results = _validation_results(vars(sys.modules[module_name]), fixture, target_pydantic_version)
    assert_output(results, LOOKAROUND_EXPECTED / f"{fixture}_runtime.txt")


@ALIAS_OF_ALIAS_RUNTIME
@pytest.mark.parametrize("target_pydantic_version", ALIAS_PATTERN_TARGETS)
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
