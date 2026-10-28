"""An integer minimum beyond the float range in a pattern property does not stop generation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.main.conftest import JSON_SCHEMA_DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path


def test_pattern_properties_huge_minimum(output_file: Path) -> None:
    """Generate schema validators for a pattern whose integer minimum is too large for a float."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "pattern_properties_huge_minimum.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--generate-schema-validators"],
    )
