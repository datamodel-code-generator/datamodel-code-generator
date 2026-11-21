"""Tests for the warning on a closed model with patternProperties."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.conftest import assert_output
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.recwarn import WarningsRecorder


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param(["--output-model-type", "pydantic_v2.BaseModel"], id="pydantic_v2.BaseModel"),
        pytest.param(["--output-model-type", "pydantic_v2.dataclass"], id="pydantic_v2.dataclass"),
        pytest.param(["--output-model-type", "typing.TypedDict"], id="typing.TypedDict"),
        pytest.param(["--output-model-type", "typing.TypedDict", "--no-use-closed-typed-dict"], id="open_typed_dict"),
        pytest.param(["--output-model-type", "msgspec.Struct"], id="msgspec.Struct"),
        pytest.param(["--output-model-type", "pydantic_v2.BaseModel", "--generate-schema-validators"], id="validators"),
        pytest.param(["--output-model-type", "pydantic_v2.BaseModel", "--extra-fields", "allow"], id="extra_allow"),
    ],
)
def test_closed_pattern_properties_warning(output_file: Path, recwarn: WarningsRecorder, extra_args: list[str]) -> None:
    """Warn that a closed model rejects the keys its patterns admit, and name the option that checks them."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "closed_pattern_properties.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", *extra_args],
    )
    assert_output(
        "".join(f"{warning.message}\n" for warning in recwarn if issubclass(warning.category, UserWarning)),
        name="warnings",
    )


def test_closed_false_pattern_properties_no_warning(output_file: Path, recwarn: WarningsRecorder) -> None:
    """Write no warning where every pattern's schema is false, since the patterns admit no key the model rejects."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "closed_false_pattern_properties.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--output-model-type", "pydantic_v2.BaseModel"],
    )
    assert_output(
        "".join(f"{warning.message}\n" for warning in recwarn if issubclass(warning.category, UserWarning)),
        name="warnings",
    )


def test_closed_pattern_properties_warning_once_per_location(output_file: Path, recwarn: WarningsRecorder) -> None:
    """Warn once for a closed schema parsed twice, as a property and through a ``$ref`` to that property."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "closed_pattern_properties_property_ref.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--output-model-type", "pydantic_v2.BaseModel"],
    )
    assert_output(
        "".join(f"{warning.message}\n" for warning in recwarn if issubclass(warning.category, UserWarning)),
        name="warnings",
    )
