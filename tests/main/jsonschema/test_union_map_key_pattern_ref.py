"""Union map key with a pattern string ``$ref`` member stays a hashable string constraint."""

from __future__ import annotations

import importlib.util
import json
import sys
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from datamodel_code_generator import DataModelType, InputFileType, generate

if TYPE_CHECKING:
    from pathlib import Path


def test_union_map_key_with_pattern_string_ref_validates(tmp_path: Path) -> None:
    """Unwrap constrained-string ``$ref`` inside union ``propertyNames`` so dict keys are hashable."""
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Root",
        "type": "object",
        "properties": {
            "limits": {
                "type": "object",
                "propertyNames": {"anyOf": [{"$ref": "#/$defs/Unit"}, {"$ref": "#/$defs/Ext"}]},
                "additionalProperties": {"type": "integer"},
            }
        },
        "$defs": {
            "Unit": {"type": "string", "enum": ["kg", "lb"]},
            "Ext": {"type": "string", "pattern": "^x-"},
        },
    }
    output = tmp_path / "model.py"
    generate(
        json.dumps(schema),
        input_file_type=InputFileType.JsonSchema,
        output=output,
        output_model_type=DataModelType.PydanticV2BaseModel,
    )

    spec = importlib.util.spec_from_file_location("__union_map_key_pattern__", output)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["__union_map_key_pattern__"] = mod
    spec.loader.exec_module(mod)

    valid_enum = mod.Root.model_validate({"limits": {"kg": 1}})
    assert valid_enum.limits[mod.Unit.kg] == 1

    valid_ext = mod.Root.model_validate({"limits": {"x-a": 2}})
    assert valid_ext.limits["x-a"] == 2

    with pytest.raises(ValidationError):
        mod.Root.model_validate({"limits": {"bad": 3}})
