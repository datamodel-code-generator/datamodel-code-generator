"""End-to-end checks for generated RootModel metadata names."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter

from datamodel_code_generator import InputFileType, PythonVersion
from datamodel_code_generator.format import Formatter
from tests.conftest import assert_output, stored_expected
from tests.main.conftest import (
    JSON_DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    _generated_model,
    run_generate_file_and_assert,
    run_main_and_assert,
)
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH, assert_file_content

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("entry_point", ["cli", "api"])
@pytest.mark.parametrize("emit_metadata", [False, True], ids=["python-only", "metadata"])
@pytest.mark.parametrize("mode", ["root_model", "root_model_type_alias", "type_alias"])
def test_root_model_metadata_names(output_file: Path, entry_point: str, emit_metadata: bool, mode: str) -> None:
    """Metadata resolves runtime fields while aliases and Python output stay stable."""
    input_path = JSON_SCHEMA_DATA_PATH / "root_model_metadata.json"
    expected_dir = EXPECTED_JSON_SCHEMA_PATH / "root_model_metadata"
    metadata_path = output_file.with_suffix(".metadata.json")
    expected_metadata = expected_dir / f"{mode}.txt"
    options = {} if mode == "root_model" else {f"use_{mode}": True}
    match entry_point:
        case "cli":
            extra_args = ["--disable-timestamp", "--target-python-version", "3.10", "--formatters", "builtin"]
            extra_args.extend(f"--{option.replace('_', '-')}" for option in options)
            if emit_metadata:
                extra_args.extend(["--emit-model-metadata", str(metadata_path)])
            run_main_and_assert(
                input_path=input_path,
                output_path=output_file,
                input_file_type="jsonschema",
                assert_func=assert_file_content,
                expected_file=expected_dir / f"{mode}.py",
                extra_args=extra_args,
                force_exec_validation=True,
            )
        case _:
            run_generate_file_and_assert(
                input_path=input_path,
                output_path=output_file,
                input_file_type=InputFileType.JsonSchema,
                assert_func=assert_file_content,
                expected_file=expected_dir / f"{mode}.py",
                disable_timestamp=True,
                target_python_version=PythonVersion.PY_310,
                formatters=[Formatter.BUILTIN],
                emit_model_metadata=metadata_path if emit_metadata else None,
                **options,
            )
    if emit_metadata:
        assert_output(metadata_path.read_text(encoding="utf-8"), expected_metadata)
    metadata = json.loads(
        stored_expected(metadata_path if emit_metadata else expected_metadata).read_text(encoding="utf-8")
    )
    payloads = json.loads((JSON_DATA_PATH / "root_model_metadata_payloads.json").read_text(encoding="utf-8"))
    runtime = []
    with _generated_model(output_file, "root_model_metadata", "RootMetadata") as model:
        for model_info in metadata["models"]:
            generated_type = getattr(sys.modules[model.__module__], model_info["class_name"])
            adapter = TypeAdapter(generated_type)
            instance = adapter.validate_python(payloads[model_info["class_name"]])
            fields = []
            if hasattr(generated_type, "model_fields"):
                for field in model_info["fields"]:
                    runtime_field = generated_type.model_fields[field["name"]]
                    value = getattr(instance, field["name"])
                    fields.append({
                        "name": field["name"],
                        "alias": runtime_field.alias if runtime_field.alias is not None else field["name"],
                        "required": runtime_field.is_required(),
                        "value": TypeAdapter(runtime_field.annotation).dump_python(value, mode="json", by_alias=True),
                    })
            runtime.append({
                "class_name": model_info["class_name"],
                "field_order": list(getattr(generated_type, "model_fields", {})),
                "fields": fields,
                "value": adapter.dump_python(instance, mode="json", by_alias=True),
            })
    assert_output(
        f"{json.dumps(runtime, indent=2)}\n",
        expected_dir / ("type_alias_runtime.txt" if mode == "type_alias" else "root_model_runtime.txt"),
    )
