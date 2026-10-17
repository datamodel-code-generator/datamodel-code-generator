"""Tests for enum helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from datamodel_code_generator.enums import TargetPydanticVersion, _is_pydantic_version_at_least
from tests.conftest import assert_output

EXPECTED_ENUMS_PATH = Path(__file__).parent / "data" / "expected" / "enums"


def test_is_pydantic_version_at_least() -> None:
    """Target Pydantic versions compare by semantic version order."""
    cases = [
        ("2.12 >= 2.12", TargetPydanticVersion.V2_12, TargetPydanticVersion.V2_12),
        ("2.12 >= 2.11", TargetPydanticVersion.V2_12, TargetPydanticVersion.V2_11),
        ("2.11 >= 2.12", TargetPydanticVersion.V2_11, TargetPydanticVersion.V2_12),
        ("2 >= 2.11", TargetPydanticVersion.V2, TargetPydanticVersion.V2_11),
        ("2.13 >= 2.12", "2.13", TargetPydanticVersion.V2_12),
        ("2.10 >= 2.11", "2.10", TargetPydanticVersion.V2_11),
    ]

    assert_output(
        "".join(f"{label}: {_is_pydantic_version_at_least(target, minimum)}\n" for label, target, minimum in cases),
        EXPECTED_ENUMS_PATH / "pydantic_version_at_least.txt",
    )


@pytest.mark.allow_direct_assert("Verify generated module text does not import unused Field")
def test_enum_with_descriptions_does_not_import_unused_field() -> None:
    """An enum whose members have descriptions does not import unused Field without --use-field-description."""
    from datamodel_code_generator import DataModelType, InputFileType, generate

    schema = (
        '{"title": "Status", "type": "string", "enum": ["queued", "running"], '
        '"x-enum-descriptions": ["Waiting.", "Running."]}'
    )
    result = generate(
        schema, input_file_type=InputFileType.JsonSchema, output_model_type=DataModelType.PydanticV2BaseModel
    )
    assert "from pydantic import Field" not in result
    assert "class Status(Enum):" in result
    assert "queued = 'queued'" in result
    assert "running = 'running'" in result


@pytest.mark.allow_direct_assert("Verify generated module text does not import unused Field")
def test_enum_from_oneof_const_descriptions_does_not_import_unused_field() -> None:
    """An enum created from oneOf const branches with descriptions does not import unused Field."""
    from datamodel_code_generator import DataModelType, InputFileType, generate

    schema = (
        '{"title": "Status", "oneOf": ['
        '{"type": "string", "const": "queued", "description": "Waiting."}, '
        '{"type": "string", "const": "running", "description": "Running."}'
        "]}"
    )
    result = generate(
        schema, input_file_type=InputFileType.JsonSchema, output_model_type=DataModelType.PydanticV2BaseModel
    )
    assert "from pydantic import Field" not in result
    assert "class Status(Enum):" in result
    assert "queued = 'queued'" in result
    assert "running = 'running'" in result
