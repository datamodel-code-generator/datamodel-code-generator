"""Refuse a msgspec Struct whose field name msgspec cannot take, instead of writing a module that cannot be imported."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator.__main__ import Exit
from tests.main.conftest import run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

NAMES = {
    "backslash": ("a\\b", "its field 'a_b' named 'a\\\\b'"),
    "double_quote": ('c"d', "its field 'c_d' named 'c\"d'"),
    "control_character": ("e\x01f", "its field 'e_f' named 'e\\x01f'"),
}


@pytest.mark.parametrize("output_model_type", ["msgspec.Struct", "pydantic_v2.BaseModel"])
@pytest.mark.parametrize("name", NAMES)
def test_msgspec_refused_field_names(
    tmp_path: Path, output_file: Path, capsys: pytest.CaptureFixture[str], name: str, output_model_type: str
) -> None:
    """Fail for a property that renames to a name containing a backslash, a double quote or a control character.

    msgspec refuses such a renamed field name, so ``field(name=...)`` raised ``ValueError`` when the generated module
    was imported (#5557). The error names the model, the field and the name. Another output model type writes the
    property as it did.
    """
    wire_name, message = NAMES[name]
    input_path = tmp_path / "refused_field_names.json"
    properties = {"ok": {"type": "string"}, wire_name: {"type": "integer"}}
    input_path.write_text(json.dumps({"title": "M", "type": "object", "properties": properties}))
    refused = output_model_type == "msgspec.Struct"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", output_model_type, "--formatters", "builtin", "--disable-timestamp"],
        expected_exit=Exit.ERROR if refused else Exit.OK,
        capsys=capsys,
        expected_stderr_contains=f"msgspec.Struct model 'M' cannot express {message}" if refused else None,
        output_should_not_exist=refused,
    )
