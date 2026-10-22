"""The patterns of a property that several allOf members define all apply to the merged property.

Only with --intersect-allof-keywords: without it the later pattern replaces the earlier one, as in 0.86.0.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import (
    JSON_SCHEMA_DATA_PATH,
    assert_generated_model_json_invalid,
    assert_generated_model_json_validation,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

REJECTED_BY_FIRST_PATTERN = '{"code": "A1"}'


@pytest.mark.parametrize("intersect", [False, True], ids=["without_option", "intersect"])
def test_allof_shared_property_patterns(output_file: Path, *, intersect: bool) -> None:
    """Conjoin the patterns of a shared property with --intersect-allof-keywords."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "allof_shared_property_patterns.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--formatters",
            "builtin",
            "--field-constraints",
            *(["--intersect-allof-keywords"] if intersect else []),
        ],
    )
    module_name = f"allof_shared_property_patterns_{'intersect' if intersect else 'without_option'}"
    if intersect:
        assert_generated_model_json_invalid(
            output_file,
            module_name=module_name,
            model_name="Model",
            invalid_json=REJECTED_BY_FIRST_PATTERN,
            expected_error_type="string_pattern_mismatch",
        )
    else:
        assert_generated_model_json_validation(
            output_file,
            module_name=module_name,
            model_name="Model",
            valid_json=REJECTED_BY_FIRST_PATTERN,
            invalid_json='{"code": "ab"}',
            expected_error_type="string_pattern_mismatch",
        )
