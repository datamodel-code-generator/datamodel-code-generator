"""Preserve GraphQL container types when constructing msgspec defaults."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import msgspec
import pytest
from pydantic import ValidationError

from datamodel_code_generator import DataModelType, InputFileType, PythonVersion
from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    EXPECTED_GRAPHQL_PATH,
    _generated_model,
    run_generate_file_and_assert,
    run_main_and_assert,
)
from tests.main.graphql.conftest import assert_file_content

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("output_model_type", [DataModelType.MsgspecStruct, DataModelType.PydanticV2BaseModel])
@pytest.mark.parametrize("input_file_type", [InputFileType.GraphQL, InputFileType.JsonSchema])
@pytest.mark.parametrize("empty", [False, True])
def test_msgspec_graphql_list_defaults(
    entrypoint: str, output_model_type: DataModelType, input_file_type: InputFileType, empty: bool, output_file: Path
) -> None:
    """Default and explicit inputs retain the same ordered model elements through CLI/API."""
    name = "msgspec_empty_list_defaults" if empty else "msgspec_object_list_defaults"
    extension = "graphql" if input_file_type == InputFileType.GraphQL else "json"
    input_path = DATA_PATH / input_file_type.value / f"{name}.{extension}"
    expected_name = f"{name}_{input_file_type.value}_{output_model_type.value.replace('.', '_')}"
    if entrypoint == "cli":
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type=input_file_type.value,
            assert_func=assert_file_content,
            expected_file=f"{expected_name}.py",
            extra_args=[
                "--output-model-type",
                output_model_type.value,
                "--target-python-version",
                "3.10",
                "--disable-timestamp",
            ],
        )
    else:
        run_generate_file_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type=input_file_type,
            assert_func=assert_file_content,
            expected_file=f"{expected_name}.py",
            output_model_type=output_model_type,
            target_python_version=PythonVersion.PY_310,
            disable_timestamp=True,
        )
    payloads = json.loads((DATA_PATH / "payloads" / "msgspec_object_list_defaults.json").read_text())
    with _generated_model(output_file, "msgspec_list_default_runtime", "Box") as model:
        first = model()
        second = model()
        if output_model_type == DataModelType.MsgspecStruct:
            explicit = msgspec.convert(payloads["explicit"], type=model)
            with pytest.raises(msgspec.ValidationError):
                msgspec.convert(payloads["invalid"], type=model)
            field_order = list(model.__struct_fields__)
            defaults = msgspec.to_builtins(first)
        else:
            explicit = model.model_validate(payloads["explicit"])
            with pytest.raises(ValidationError):
                model.model_validate(payloads["invalid"])
            field_order = list(model.model_fields)
            defaults = first.model_dump()
        actual = {
            "field_order": field_order,
            "defaults": {
                name: [None if item is None else item["n"] for item in defaults[name]]
                for name in payloads["list_fields"]
            },
            "item_types": {
                name: [type(item).__name__ for item in getattr(first, name)] for name in payloads["list_fields"]
            },
            "explicit_items": [item.n for item in explicit.items],
            "direct_item": first.direct_item.n,
            "count": first.count,
            "numbers": first.numbers,
            "matrix_values": [[item["n"] for item in row] for row in defaults["matrix"]],
            "matrix_types": [[type(item).__name__ for item in row] for row in first.matrix],
            "cube_values": [[[item["n"] for item in row] for row in plane] for plane in defaults["cube"]],
            "cube_types": [[[type(item).__name__ for item in row] for row in plane] for plane in first.cube],
            "scalar_cube": first.scalar_cube,
        }
        first.items.append(first.direct_item)
        first.direct_item.n = 100
        actual["independent_items"] = [item.n for item in second.items]
        actual["independent_direct_item"] = second.direct_item.n
    assert_output(f"{json.dumps(actual, indent=2)}\n", EXPECTED_GRAPHQL_PATH / f"{expected_name}.txt")
