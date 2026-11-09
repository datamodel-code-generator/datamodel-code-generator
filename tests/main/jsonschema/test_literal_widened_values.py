"""Warn about the enum and const values a Literal of the output model cannot hold, which their types stand in for."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.conftest import assert_output
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

OUTPUT_MODEL_TYPES = ("pydantic_v2.BaseModel", "msgspec.Struct", "dataclasses.dataclass", "typing.TypedDict")
OPTION_SETS = {
    "default": (),
    "literal_all": ("--enum-field-as-literal", "all"),
    "literal_one_as_const": ("--enum-field-as-literal", "one", "--use-single-value-enum-as-const"),
}


@pytest.mark.parametrize("option_set", OPTION_SETS)
@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
def test_literal_widened_values(
    output_file: Path, recwarn: pytest.WarningsRecorder, output_model_type: str, option_set: str
) -> None:
    """Name each field written with the types of enum or const values in place of the values, and no other field.

    A Literal holds str and int values, and bool values where the output writes bool literals (not msgspec); a float,
    a list or an object value is written as its type, which admits any other value of that type, and so is a bool
    beside msgspec, unless both bools are values. A one-value enum read as a const is written as the const is.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "literal_widened_values.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--output-model-type", output_model_type, *OPTION_SETS[option_set]],
    )
    assert_output(
        "".join(
            f"{message}\n"
            for message in dict.fromkeys(
                str(warning.message)
                for warning in recwarn
                if warning.category is UserWarning and "a Literal of the output model" in str(warning.message)
            )
        ),
        name="warnings",
    )
