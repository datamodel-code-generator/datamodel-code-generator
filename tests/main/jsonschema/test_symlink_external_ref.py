"""Regression coverage for symlinked JSON Schema input paths."""

from __future__ import annotations

from typing import TYPE_CHECKING

from datamodel_code_generator import InputFileType
from tests.conftest import validate_generated_code
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, run_generate_file_and_assert
from tests.main.jsonschema.conftest import assert_file_content

if TYPE_CHECKING:
    from pathlib import Path


def test_generate_jsonschema_external_ref_through_symlink_base_path(output_file: Path, tmp_path: Path) -> None:
    """Resolve external references from a library input path that traverses a symlink."""
    symlink_path = tmp_path / "schema"
    symlink_path.symlink_to(JSON_SCHEMA_DATA_PATH / "symlink_external_ref", target_is_directory=True)
    run_generate_file_and_assert(
        input_path=symlink_path / "root.json",
        output_path=output_file,
        input_file_type=InputFileType.JsonSchema,
        assert_func=assert_file_content,
        expected_file="symlink_external_ref.py",
        disable_timestamp=True,
    )
    validate_generated_code(output_file.read_text(encoding="utf-8"), str(output_file), do_exec=True)
