"""Convert msgspec dict defaults using the full dict container type."""

from __future__ import annotations

from typing import TYPE_CHECKING

import msgspec

from datamodel_code_generator import DataModelType, InputFileType, generate
from tests.main.conftest import _generated_model

if TYPE_CHECKING:
    from pathlib import Path


def test_msgspec_dict_struct_default(tmp_path: Path) -> None:
    """A dict[str, Struct] field converts its object default using the dict type."""
    schema = """{
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "Model",
      "type": "object",
      "properties": {
        "dm": {
          "type": "object",
          "additionalProperties": {"$ref": "#/$defs/PodSpec"},
          "default": {"a": {"name": "x"}}
        }
      },
      "$defs": {
        "PodSpec": {
          "type": "object",
          "properties": {"name": {"type": "string"}}
        }
      }
    }"""
    output_file = tmp_path / "model.py"
    code = generate(
        schema,
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.MsgspecStruct,
    )
    assert isinstance(code, str)
    output_file.write_text(code, encoding="utf-8")
    assert "type=dict[str, PodSpec]" in code

    with _generated_model(output_file, "msgspec_dict_default_model", "Model") as model_cls:
        instance = model_cls()
        assert msgspec.to_builtins(instance.dm) == {"a": {"name": "x"}}
        assert type(instance.dm["a"]).__name__ == "PodSpec"
        assert instance.dm["a"].name == "x"
