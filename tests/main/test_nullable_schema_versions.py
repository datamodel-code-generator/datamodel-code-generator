"""Keep nullable JSON Schema definitions valid across supported drafts."""

from __future__ import annotations

import json
import warnings
from typing import TYPE_CHECKING

import pytest
from jsonschema.validators import validator_for

from datamodel_code_generator import InputFileType
from datamodel_code_generator.enums import JsonSchemaVersion, VersionMode
from tests.conftest import assert_warnings_contain, assert_warnings_do_not_contain
from tests.main.conftest import (
    EXPECTED_MAIN_PATH,
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
    run_generate_file_and_assert,
    run_main_and_assert,
)
from tests.main.jsonschema.conftest import assert_file_content

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "schema_version", [version for version in JsonSchemaVersion if version is not JsonSchemaVersion.Auto]
)
def test_null_type_array_definitions_across_jsonschema_versions(
    schema_version: JsonSchemaVersion, output_file: Path
) -> None:
    """Generate valid nullable definitions through the CLI and API without false warnings."""
    input_path = JSON_SCHEMA_DATA_PATH / "null_type_array_definition.json"
    expected_file = EXPECTED_MAIN_PATH / "schema_versions" / "nullable_definition.py"
    schema = json.loads(input_path.read_text(encoding="utf-8"))
    schema_uri = (
        f"http://json-schema.org/{schema_version.value}/schema#"
        if schema_version.value.startswith("draft-")
        else f"https://json-schema.org/draft/{schema_version.value}/schema"
    )
    validator_for({"$schema": schema_uri}).check_schema(schema)
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", UserWarning)
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type="jsonschema",
            assert_func=assert_file_content,
            expected_file=expected_file,
            extra_args=[
                "--schema-version",
                schema_version.value,
                "--schema-version-mode",
                "strict",
                "--target-python-version",
                "3.10",
                "--disable-timestamp",
                "--formatters",
                "builtin",
            ],
            force_exec_validation=True,
        )
        run_generate_file_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type=InputFileType.JsonSchema,
            schema_version=schema_version.value,
            schema_version_mode=VersionMode.Strict,
            target_python_version="3.10",
            disable_timestamp=True,
            formatters=["builtin"],
            assert_func=assert_file_content,
            expected_file=expected_file,
        )
    assert_warnings_do_not_contain(recorded, "null in type array")


def test_null_type_array_openapi_30_keeps_strict_warning(output_file: Path) -> None:
    """Retain the warning for OpenAPI 3.0, where nullable uses a separate keyword."""
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", UserWarning)
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "null_type_array_definition.json",
            output_path=output_file,
            input_file_type="openapi",
            assert_func=assert_file_content,
            expected_file=EXPECTED_MAIN_PATH / "schema_versions" / "nullable_definition.py",
            extra_args=[
                "--schema-version-mode",
                "strict",
                "--target-python-version",
                "3.10",
                "--disable-timestamp",
                "--formatters",
                "builtin",
            ],
            force_exec_validation=True,
        )
    assert_warnings_contain(recorded, "null in type array")
