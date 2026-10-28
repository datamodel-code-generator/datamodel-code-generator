"""A referenced request body, response or parameter types its extra keys like an inline one under the option.

Only with --read-unevaluated-properties; without it the extra keys stay untyped, as in 0.86.0.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import (
    OPEN_API_DATA_PATH,
    assert_generated_model_json_invalid,
    assert_generated_model_json_validation,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

EXTRA_KEY_JSON = '{"name": "a", "count": 1}'


@pytest.mark.parametrize("read", [False, True], ids=["without_read", "read"])
def test_unevaluated_properties_referenced_objects(output_file: Path, *, read: bool) -> None:
    """Type the extra keys of the schemas in referenced parameters, request bodies and responses."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "unevaluated_properties_referenced_objects.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            "--openapi-scopes",
            "paths",
            "parameters",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            *(["--read-unevaluated-properties"] if read else []),
        ],
    )
    for model_name in ("Filter", "ResourcesPostRequest", "ResourcesPostResponse"):
        module_name = f"unevaluated_referenced_{model_name}_{'read' if read else 'without_read'}"
        if read:
            assert_generated_model_json_invalid(
                output_file,
                module_name=module_name,
                model_name=model_name,
                invalid_json=EXTRA_KEY_JSON,
                expected_error_type="string_type",
            )
        else:
            assert_generated_model_json_validation(
                output_file,
                module_name=module_name,
                model_name=model_name,
                valid_json=EXTRA_KEY_JSON,
                invalid_json="[]",
                expected_error_type="model_type",
            )
