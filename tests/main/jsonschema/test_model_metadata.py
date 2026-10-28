"""End-to-end checks for generated model metadata: RootModel names and schema locations."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any, cast

import pytest
from pydantic import Field, TypeAdapter, model_validator

from datamodel_code_generator import DataModelType, Error, InputFileType, PythonVersion, generate
from datamodel_code_generator.format import Formatter
from datamodel_code_generator.model_metadata import dump_model_metadata
from datamodel_code_generator.parser.jsonschema import JsonSchemaObject, JsonSchemaParser
from datamodel_code_generator.parser.openapi import OpenAPIParser
from tests.conftest import CHECK_SCHEMA_LOCATIONS_ENV, assert_inputs_not_mutated, assert_output, stored_expected
from tests.main.conftest import (
    ASYNC_API_DATA_PATH,
    GRAPHQL_DATA_PATH,
    JSON_DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
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


@pytest.mark.parametrize(
    ("input_path", "input_file_type", "options"),
    [
        pytest.param(JSON_SCHEMA_DATA_PATH / "schema_location.json", "jsonschema", [], id="jsonschema"),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "all_of_any_of_base_class_ref.json", "jsonschema", [], id="colliding-parse-paths"
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "object_maps" / "strict_pattern_maps.json",
            "jsonschema",
            ["--generate-schema-validators", "--use-type-alias"],
            id="schema-validator-alias-class",
        ),
        pytest.param(
            OPEN_API_DATA_PATH / "schema_location.yaml",
            "openapi",
            ["--openapi-scopes", "schemas", "paths", "parameters", "webhooks"],
            id="openapi",
        ),
        pytest.param(OPEN_API_DATA_PATH / "schema_location.yaml", "openapi", ["--openapi-scopes", "api"], id="api"),
        pytest.param(
            OPEN_API_DATA_PATH / "schema_location.yaml",
            "openapi",
            ["--openapi-scopes", "schemas", "paths", "--read-only-write-only-model-type", "request-response"],
            id="request-response",
        ),
        pytest.param(OPEN_API_DATA_PATH / "schema_location_alias.yaml", "openapi", [], id="yaml-aliases"),
        pytest.param(ASYNC_API_DATA_PATH / "schema_location.yaml", "asyncapi", [], id="asyncapi"),
        pytest.param(GRAPHQL_DATA_PATH / "simple-star-wars.graphql", "graphql", [], id="graphql"),
    ],
)
def test_schema_location_metadata(
    output_file: Path, input_path: Path, input_file_type: str, options: list[str]
) -> None:
    """Locate each model at the schema object it is built from with an RFC 6901 JSON Pointer (#4843).

    Read and write variants share the location of their schema, a YAML alias is located where it is used whatever the
    YAML loader, and GraphQL, which has no JSON document, keeps its reference paths. A model whose parse path collides
    with another schema object's (the anyOf branches of the allOf members in ``all_of_any_of_base_class_ref.json``)
    keeps its reference path instead of a pointer that could disagree with its content. A type alias written as a class
    for its schema validator rules (``labels``) keeps the location of its schema.
    """
    metadata_path = output_file.with_suffix(".metadata.json")
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type=input_file_type,
        extra_args=[
            "--disable-timestamp",
            "--formatters",
            "builtin",
            *options,
            "--emit-model-metadata",
            str(metadata_path),
        ],
    )
    assert_output(metadata_path.read_text(encoding="utf-8"), name="metadata")


def test_schema_location_metadata_of_shared_dict_input(tmp_path: Path) -> None:
    """Locate the models of a dict input where it shares objects and holds a cycle, without changing the input."""
    inner: dict[str, Any] = {"type": "object", "properties": {"y": {"type": "integer"}}}
    shared: dict[str, Any] = {"type": "object", "properties": {"inner": inner, "kind": {"type": "string"}}}
    cycle: dict[str, Any] = {}
    cycle["self"] = cycle["again"] = cycle
    definitions: dict[str, Any] = {
        "A": shared,
        "B": shared,
        "C": {"type": "object", "properties": {"z": inner}},
    }
    schema: dict[str, Any] = {
        "$defs": definitions,
        "type": "object",
        "x-cycle": cycle,
        "properties": {"a": {"$ref": "#/$defs/A"}, "b": {"$ref": "#/$defs/B"}, "c": {"$ref": "#/$defs/C"}},
    }
    metadata_path = tmp_path / "metadata.json"
    with assert_inputs_not_mutated({"definitions": definitions}):
        output = generate(
            schema,
            input_file_type=InputFileType.JsonSchema,
            output_model_type=DataModelType.PydanticV2BaseModel,
            emit_model_metadata=metadata_path,
            disable_timestamp=True,
            formatters=[Formatter.BUILTIN],
        )
    assert_output(str(output))
    assert_output(metadata_path.read_text(encoding="utf-8"), name="metadata")


@pytest.mark.parametrize("flag", ["check", "report", "off", "fallback", "invalid"])
def test_schema_location_of_schemas_a_custom_parser_rewrites(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, flag: str
) -> None:
    """A custom parser that parses copies of the raw schemas leaves models without a location.

    The check (`1`, as the test suite sets it) raises for them at the end of parsing; `report:<path>` appends them to
    the file as a JSON line instead; with the check off (`0`) or unset, they keep their reference paths. Other values
    are rejected.
    """

    class RewritingParser(OpenAPIParser):
        def parse_raw_obj(self, name: str, raw: Any, path: list[str]) -> None:
            super().parse_raw_obj(name, json.loads(json.dumps(raw)), path)

    report_path = tmp_path / "checks.jsonl"
    values = {"check": "1", "report": f"report:{report_path}", "off": "0", "invalid": "report:"}
    if (value := values.get(flag)) is None:
        monkeypatch.delenv(CHECK_SCHEMA_LOCATIONS_ENV, raising=False)
    else:
        monkeypatch.setenv(CHECK_SCHEMA_LOCATIONS_ENV, value)
    parser = RewritingParser(OPEN_API_DATA_PATH / "schema_location_alias.yaml", formatters=[Formatter.BUILTIN])
    if flag == "check":
        with pytest.raises(Error, match=f"^{CHECK_SCHEMA_LOCATIONS_ENV}: models without a schema location: Owner "):
            parser.parse(collect_model_metadata=True)
        return
    if flag == "invalid":
        with pytest.raises(ValueError, match=r"='report:': expected 0, 1 or report:<path>$"):
            parser.parse(collect_model_metadata=True)
        return
    parser.parse(collect_model_metadata=True)
    assert_output(dump_model_metadata(parser.model_metadata), name="metadata")
    if flag == "report":
        assert_output(report_path.read_text(encoding="utf-8"), name="report")


def test_schema_location_of_fields_a_schema_object_subclass_adds(tmp_path: Path) -> None:
    """Schema objects in a field a ``SCHEMA_OBJECT_TYPE`` subclass adds take their locations, also beside its validator.

    The test suite runs with the location check on, so a model built from such a field without a location fails here.
    """

    class ExtensionSchema(JsonSchemaObject):
        children: list[JsonSchemaObject] = Field(default_factory=list)

        @model_validator(mode="after")
        def _keep(self) -> ExtensionSchema:
            return self

    class ExtensionParser(JsonSchemaParser):
        SCHEMA_OBJECT_TYPE = ExtensionSchema

        def parse_object(self, name: str, obj: JsonSchemaObject, path: list[str], *args: Any, **kwargs: Any) -> Any:
            for index, child in enumerate(cast("ExtensionSchema", obj).children):
                super().parse_object(f"{name}Child{index}", child, [*path, "children", str(index)])
            return super().parse_object(name, obj, path, *args, **kwargs)

    (input_path := tmp_path / "extension.json").write_text(
        json.dumps({
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "children": [{"type": "object", "properties": {"count": {"type": "integer"}}}],
        }),
        encoding="utf-8",
    )
    parser = ExtensionParser(input_path, base_path=tmp_path, formatters=[Formatter.BUILTIN])
    parser.parse(collect_model_metadata=True)
    assert_output(dump_model_metadata(parser.model_metadata), name="metadata")
