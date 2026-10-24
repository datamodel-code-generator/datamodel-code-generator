"""Type aliases of components that only reference each other, which Pydantic cannot build when a class uses them.

Generation stops when a class of output that Pydantic builds reaches such aliases (see ``test_error_messages.py``);
output where no class reaches them keeps its 0.86.0 bytes, and so does a BaseModel, whose root models Pydantic builds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import (
    DATA_PATH,
    OPEN_API_DATA_PATH,
    assert_generated_model_json_validation,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "extra_args",
    [
        ["--output-model-type", "pydantic_v2.dataclass"],
        ["--output-model-type", "pydantic_v2.BaseModel", "--use-type-alias"],
    ],
    ids=["pydantic-dataclass", "base-model-type-alias"],
)
def test_ref_alias_cycle_no_class_uses(output_file: Path, extra_args: list[str]) -> None:
    """Keep the aliases of a cycle that no class reaches beside a class that uses other aliases and a class."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "ref_alias_cycle_unused.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=extra_args,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"ref_alias_cycle_unused_{extra_args[1].replace('.', '_')}",
        model_name="Holder",
        valid_json='{"first": "a", "second": "b", "label": "c", "size": {"value": 1}, "count": 2}',
        invalid_json='{"size": {"value": "x"}}',
        expected_error_type="int_parsing",
        expected_attribute_path=("size", "value"),
        expected_attribute_value=1,
    )


def test_ref_alias_cycle_base_model(output_file: Path) -> None:
    """Keep the root models of a BaseModel, whose field that uses the cycle takes null."""
    run_main_and_assert(
        input_path=DATA_PATH / "malformed" / "ref_alias_cycle.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="ref_alias_cycle_base_model",
        model_name="Holder",
        valid_json='{"a": null}',
        invalid_json="[]",
        expected_error_type="model_type",
        expected_attribute_path=("a",),
        expected_attribute_value=None,
    )
