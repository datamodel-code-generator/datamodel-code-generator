"""TypedDict keeps ``| None`` on optional nullable maps with key schemas."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from datamodel_code_generator import DataModelType, InputFileType, generate

if TYPE_CHECKING:
    from pathlib import Path


def test_typed_dict_optional_nullable_map_keeps_none(tmp_path: Path) -> None:
    """Keep ``| None`` inside ``NotRequired[...]`` for optional nullable maps."""
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Root",
        "type": "object",
        "properties": {
            "f": {"type": ["object", "null"], "patternProperties": {"^a": {"type": "integer"}}},
            "g": {"type": ["object", "null"], "propertyNames": {"pattern": "^b"}},
            "h": {"type": "object", "patternProperties": {"^c": {"type": "integer"}}},
        },
    }
    output = tmp_path / "model.py"
    generate(
        json.dumps(schema),
        input_file_type=InputFileType.JsonSchema,
        output=output,
        output_model_type=DataModelType.TypingTypedDict,
    )
    generated = output.read_text(encoding="utf-8")
    assert "f: NotRequired[dict[str, int] | None]" in generated
    assert "g: NotRequired[dict[str, Any] | None]" in generated
    assert "h: NotRequired[dict[str, int]]" in generated
    assert "h: NotRequired[dict[str, int] | None]" not in generated
