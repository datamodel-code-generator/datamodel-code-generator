"""Local fragment ``$ref`` inside an embedded ``$id`` resource in ``$defs``."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from datamodel_code_generator import DataModelType, InputFileType, generate

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "status_ref",
    [
        "https://example.com/wrap.json#/$defs/Status",
        "#/$defs/Wrap/$defs/Status",
    ],
)
def test_local_ref_in_embedded_defs_resource_resolves_against_resource_id(
    tmp_path: Path,
    status_ref: str,
) -> None:
    """Resolve ``#/$defs/Code`` inside an embedded ``$id`` resource against that resource."""
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Main",
        "type": "object",
        "properties": {"status": {"$ref": status_ref}},
        "$defs": {
            "Code": {"type": "string", "enum": ["x", "y"]},
            "Wrap": {
                "$id": "https://example.com/wrap.json",
                "$defs": {
                    "Status": {"type": "object", "properties": {"code": {"$ref": "#/$defs/Code"}}},
                    "Code": {"type": "string", "enum": ["a", "b"]},
                },
            },
        },
    }
    output = tmp_path / "model.py"
    generate(
        json.dumps(schema),
        input_file_type=InputFileType.JsonSchema,
        output=output,
        output_model_type=DataModelType.PydanticV2BaseModel,
    )
    namespace: dict[str, object] = {}
    exec(output.read_text(encoding="utf-8"), namespace)
    main_cls = namespace["Main"]
    main_cls.model_rebuild(_types_namespace=namespace)
    sys.modules.setdefault("__embedded_resource_test__", type(sys)("__embedded_resource_test__"))

    valid = main_cls.model_validate({"status": {"code": "a"}})
    assert valid.status.code.value == "a"
    with pytest.raises(ValidationError):
        main_cls.model_validate({"status": {"code": "x"}})


def test_local_ref_in_embedded_defs_resource_allof(tmp_path: Path) -> None:
    """Resolve ``allOf`` ``#/$defs/Code`` inside an embedded ``$id`` resource against that resource."""
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Main",
        "type": "object",
        "properties": {"status": {"$ref": "https://example.com/wrap.json#/$defs/Status"}},
        "$defs": {
            "Code": {"type": "string", "enum": ["x", "y"]},
            "Wrap": {
                "$id": "https://example.com/wrap.json",
                "$defs": {
                    "Status": {"allOf": [{"$ref": "#/$defs/Code"}]},
                    "Code": {"type": "string", "enum": ["a", "b"]},
                },
            },
        },
    }
    output = tmp_path / "model.py"
    generate(
        json.dumps(schema),
        input_file_type=InputFileType.JsonSchema,
        output=output,
        output_model_type=DataModelType.PydanticV2BaseModel,
    )
    namespace: dict[str, object] = {}
    exec(output.read_text(encoding="utf-8"), namespace)
    main_cls = namespace["Main"]
    main_cls.model_rebuild(_types_namespace=namespace)

    valid = main_cls.model_validate({"status": "a"})
    assert valid.status.root.value == "a"
    with pytest.raises(ValidationError):
        main_cls.model_validate({"status": "x"})
