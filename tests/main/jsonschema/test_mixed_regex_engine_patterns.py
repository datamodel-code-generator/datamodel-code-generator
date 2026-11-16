"""Mixed Python-only and Rust-only regex patterns in one Pydantic v2 model."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import DataModelType, InputFileType, generate

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "model_type",
    [
        DataModelType.PydanticV2BaseModel,
        DataModelType.PydanticV2Dataclass,
    ],
)
def test_mixed_python_re_and_rust_only_patterns_importable(
    tmp_path: Path,
    model_type: DataModelType,
) -> None:
    """Drop Rust-only patterns from ``constr`` fields when the model selects ``python-re``."""
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "Model",
        "type": "object",
        "properties": {
            "password": {"type": "string", "pattern": "^(?=.*[A-Z]).{8,}$"},
            "name": {"type": "string", "pattern": r"^\p{L}+$"},
        },
    }
    output = tmp_path / "model.py"
    with pytest.warns(UserWarning, match=r"does not compile pattern="):
        generate(
            json.dumps(schema),
            input_file_type=InputFileType.JsonSchema,
            output=output,
            output_model_type=model_type,
        )

    code = output.read_text(encoding="utf-8")
    assert 'regex_engine="python-re"' in code
    assert r"\p{L}" not in code

    import importlib.util

    module_name = f"__mixed_regex_test_{model_type.name}__"
    spec = importlib.util.spec_from_file_location(module_name, output)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    model_cls = module.Model
    instance = model_cls(password="Secret123", name="Alice")
    assert instance.password == "Secret123"
    assert instance.name == "Alice"
