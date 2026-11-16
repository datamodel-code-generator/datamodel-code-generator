"""AsyncAPI ``schemaFormat: application/schema`` media type support."""

from __future__ import annotations

from typing import TYPE_CHECKING

from datamodel_code_generator import DataModelType, InputFileType, generate

if TYPE_CHECKING:
    from pathlib import Path


def test_asyncapi_accepts_application_schema_format(tmp_path: Path) -> None:
    """Accept ``application/schema;version=draft-07`` as a JSON Schema format."""
    spec = """
asyncapi: 2.6.0
info:
  title: Probe
  version: 1.0.0
channels:
  /things:
    subscribe:
      message:
        schemaFormat: application/schema;version=draft-07
        payload:
          type: object
          properties:
            name:
              type: string
          required:
            - name
"""
    output = tmp_path / "model.py"
    generate(
        spec,
        input_file_type=InputFileType.AsyncAPI,
        output=output,
        output_model_type=DataModelType.PydanticV2BaseModel,
    )
    generated = output.read_text(encoding="utf-8")
    assert "class Payload" in generated or "name: str" in generated
