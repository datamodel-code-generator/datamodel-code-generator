"""Tests for enum helpers."""

from __future__ import annotations

from pathlib import Path

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
