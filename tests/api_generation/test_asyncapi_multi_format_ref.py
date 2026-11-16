"""AsyncAPI 3 payload ``$ref`` to a component Multi Format Schema Object."""

from __future__ import annotations

import importlib.util
import sys
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from datamodel_code_generator import DataModelType, InputFileType, generate

if TYPE_CHECKING:
    from pathlib import Path


def test_asyncapi_payload_ref_to_multi_format_schema_object(tmp_path: Path) -> None:
    """Resolve payload ``$ref`` to a Multi Format Schema Object via its ``schema``."""
    spec = """
asyncapi: 3.0.0
info:
  title: Probe
  version: 1.0.0
servers:
  socket:
    host: probe.example.com
    protocol: wss
channels:
  notes:
    address: /notes
    messages:
      note:
        payload:
          $ref: '#/components/schemas/Note'
operations:
  receiveNotes:
    action: receive
    channel:
      $ref: '#/channels/notes'
components:
  schemas:
    Note:
      schemaFormat: application/schema+json;version=draft-07
      schema:
        type: object
        properties:
          text:
            type: string
        required:
          - text
"""
    output = tmp_path / "model.py"
    generate(
        spec,
        input_file_type=InputFileType.AsyncAPI,
        output=output,
        output_model_type=DataModelType.PydanticV2BaseModel,
    )
    generated = output.read_text(encoding="utf-8")
    assert "class Note(BaseModel):" in generated
    assert "class NotesNotePayload(BaseModel):" in generated
    assert "NoteModel" not in generated

    spec_obj = importlib.util.spec_from_file_location("__asyncapi_multi_format_ref__", output)
    assert spec_obj is not None
    assert spec_obj.loader is not None
    mod = importlib.util.module_from_spec(spec_obj)
    sys.modules["__asyncapi_multi_format_ref__"] = mod
    spec_obj.loader.exec_module(mod)
    valid = mod.NotesNotePayload.model_validate({"text": "hello"})
    assert valid.text == "hello"
    with pytest.raises(ValidationError):
        mod.NotesNotePayload.model_validate({"text": 5})
