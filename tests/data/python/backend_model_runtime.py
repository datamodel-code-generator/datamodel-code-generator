"""Read public native metadata from a generated fixture, outside binding capture."""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
from pathlib import Path

backend, filename = sys.argv[1:]
spec = importlib.util.spec_from_file_location("binding_runtime_fixture", Path(filename))
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
model = module.Record
match backend:
    case "DataclassesDataclass" | "PydanticV2Dataclass":
        result = {
            "init": model.__dataclass_params__.init,
            "frozen": model.__dataclass_params__.frozen,
            "slots": hasattr(model, "__slots__"),
            "fields": [
                {"name": field.name, "init": field.init, "kw_only": field.kw_only}
                for field in dataclasses.fields(model)
            ],
        }
        if backend == "PydanticV2Dataclass":
            result["config"] = {
                key: model.__pydantic_config__.get(key) for key in ("extra", "strict", "populate_by_name")
            }
    case "PydanticV2BaseModel":
        result = {"config": {key: model.model_config.get(key) for key in ("extra", "strict", "populate_by_name")}}
    case "TypingTypedDict":
        result = {"total": model.__total__, "closed": model.__closed__}
    case "MsgspecStruct":
        import msgspec

        result = {
            "config": {
                key: getattr(model.__struct_config__, key)
                for key in ("tag", "tag_field", "array_like", "forbid_unknown_fields", "omit_defaults", "frozen")
            },
            "fields": [
                {"name": field.name, "encode_name": field.encode_name, "required": field.required}
                for field in msgspec.structs.fields(model)
            ],
        }
sys.stdout.write(json.dumps(result, indent=2) + "\n")
