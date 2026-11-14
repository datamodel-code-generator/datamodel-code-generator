"""Open enums of untyped, mixed, formatted and constrained JSON Schema enums (``--open-enums``)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import JSON_SCHEMA_DATA_PATH, assert_generated_model_json_validation, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "output_model_type", ["pydantic_v2.BaseModel", "typing.TypedDict", "msgspec.Struct", "dataclasses.dataclass"]
)
def test_open_enum_value_types(output_file: Path, output_model_type: str) -> None:
    """Join each open enum's Literal with the type of its schema, else with the types of its values (#6818).

    An untyped enum takes the types of its values, a formatted string the type of its format, a number enum, which no
    Literal holds, stays its type, a nullable enum keeps ``None``, and an enum a key ``$ref`` names opens the keys.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "open_enums_values.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--formatters",
            "builtin",
            "--target-python-version",
            "3.11",
            "--output-model-type",
            output_model_type,
            "--open-enums",
            "all",
        ],
    )
    if output_model_type == "pydantic_v2.BaseModel":
        assert_generated_model_json_validation(
            output_file,
            module_name="open_enum_value_types",
            model_name="Reading",
            valid_json=(
                '{"untyped": "mid", "mixed": 7, "uuid": "9a1f2c4e-0000-4000-8000-000000000009", "ratio": 2.5, '
                '"code": "ef", "nullable": 9, "byUnit": {"g": 1.0}, "unit": "oz"}'
            ),
            invalid_json='{"code": "toolong"}',
            expected_error_type="literal_error",
            expected_repr=(
                "Reading(untyped='mid', mixed=7, uuid=UUID('9a1f2c4e-0000-4000-8000-000000000009'), ratio=2.5, "
                "flag=None, code='ef', nullable=9, byUnit={'g': 1.0}, unit=Unit(root='oz'))"
            ),
        )
