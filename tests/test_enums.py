"""Tests for enum helpers."""

from __future__ import annotations

from pathlib import Path

from datamodel_code_generator import MAX_VERSION, MIN_VERSION
from datamodel_code_generator.enums import TargetPydanticVersion, _is_pydantic_version_at_least
from datamodel_code_generator.format import PythonVersion
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


def test_version_bounds_are_the_lowest_and_highest_target_python_version() -> None:
    """MIN_VERSION and MAX_VERSION are the minors of the lowest and the highest PythonVersion target (#5547)."""
    lowest, *_, highest = (version.value for version in PythonVersion)
    matches = (f"3.{MIN_VERSION}", f"3.{MAX_VERSION}") == (lowest, highest)

    assert_output(f"MIN_VERSION and MAX_VERSION name the lowest and the highest target: {matches}\n")
