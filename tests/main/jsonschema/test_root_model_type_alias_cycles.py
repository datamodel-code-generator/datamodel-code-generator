"""End-to-end coverage for cycles of RootModel aliases that hold constrained root models."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import (
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
    assert_generated_model_json_validation,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("target_python_version", ["3.10", "3.13"])
def test_main_jsonschema_root_model_type_alias_constrained_cycle(
    output_file: Path,
    target_python_version: str,
) -> None:
    """Order and quote the root classes of a cycle whose constrained members cannot be aliases."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "root_model_type_alias_constrained_cycle.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-root-model-type-alias",
            "--target-python-version",
            target_python_version,
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ],
        force_exec_validation=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"root_model_type_alias_constrained_cycle_{target_python_version.replace('.', '_')}",
        model_name="Model",
        valid_json='{"a":[[[[[[]]]]]]}',
        invalid_json='{"a":[[[[],[],[]]]]}',
        expected_error_type="too_long",
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"root_model_type_alias_constrained_cycle_empty_{target_python_version.replace('.', '_')}",
        model_name="Model",
        valid_json='{"a":[[]]}',
        invalid_json='{"a":[]}',
        expected_error_type="too_short",
    )


def test_main_openapi_root_model_type_alias_constrained_cycle_modular(output_dir: Path) -> None:
    """Order and quote the root classes of each module of a modular output."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "root_model_type_alias_constrained_cycle_modular.json",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-root-model-type-alias",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ],
        force_exec_validation=True,
    )
