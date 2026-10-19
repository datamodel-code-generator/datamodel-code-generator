"""State the Pydantic release that generated models need when it is newer than an explicit target."""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ConfigDict

from datamodel_code_generator import (
    DataModelType,
    Error,
    InputFileType,
    OpenAPIScope,
    PythonVersion,
    TargetPydanticVersion,
    generate,
)
from datamodel_code_generator.format import Formatter
from datamodel_code_generator.model.pydantic_v2 import UnionMode
from tests.conftest import CHECK_GENERATED_CODE_REQUIREMENTS_ENV, assert_output
from tests.main.conftest import OPEN_API_DATA_PATH, XML_SCHEMA_DATA_PATH, run_main_with_args

if TYPE_CHECKING:
    from pathlib import Path

LOOKAROUND: dict[str, Any] = {"type": "string", "pattern": "^(?!x)[a-z]+$"}
LOOKAROUND_MODEL: dict[str, Any] = {"type": "object", "properties": {"name": LOOKAROUND}, "required": ["name"]}
UNION_MODEL: dict[str, Any] = {
    "type": "object",
    "properties": {"value": {"anyOf": [{"type": "integer"}, {"type": "string"}], "description": "A value."}},
    "required": ["value"],
}
BYTES_MODEL: dict[str, Any] = {
    "type": "object",
    "properties": {"blob": {"type": "string", "format": "binary", "pattern": "^a$"}},
}


def _bytes_or_string_model(*members: str) -> dict[str, Any]:
    """Return a model whose field takes a pattern over a union of a bytes and a string type in the given order."""
    return {
        "type": "object",
        "properties": {"blob": {"$ref": "#/$defs/Blob"}},
        "$defs": {
            "Blob": {"pattern": "^a$", "anyOf": [{"$ref": f"#/$defs/{member}"} for member in members]},
            "Binary": {"type": "string", "format": "binary"},
            "Text": {"type": "string"},
        },
    }


ALIASES: dict[str, Any] = {
    "$defs": {
        "Item": {"type": "object", "properties": {"id": {"type": "integer"}}},
        "ItemRef": {"$ref": "#/$defs/Item"},
        "Items": {"type": "array", "items": {"$ref": "#/$defs/ItemRef"}},
    },
    "type": "object",
    "properties": {"items": {"$ref": "#/$defs/Items"}, "item": {"$ref": "#/$defs/ItemRef"}},
}
USER_CONFIG = {
    "Model": {
        "config": {
            "coerce_numbers_to_str": True,
            "validate_by_name": True,
            "validate_by_alias": True,
            "serialize_by_alias": True,
            "use_attribute_docstrings": True,
            "strict": True,
        }
    }
}
MODULES: dict[str, Any] = {
    "openapi": "3.1.0",
    "info": {"title": "Modules", "version": "1"},
    "paths": {},
    "components": {
        "schemas": {
            **{f"a.Code{index}": {"type": "object", "properties": {"code": LOOKAROUND}} for index in range(3)},
            "b.Code": {"type": "object", "properties": {"code": LOOKAROUND}},
            "b.Value": UNION_MODEL,
            "c.Code": {"type": "object", "properties": {"code": LOOKAROUND}},
        }
    },
}
JSON_SCHEMA = InputFileType.JsonSchema
PYDANTIC_DATACLASS = DataModelType.PydanticV2Dataclass
TARGET_2 = TargetPydanticVersion.V2
CASES = [
    pytest.param(UNION_MODEL, JSON_SCHEMA, {"union_mode": UnionMode.left_to_right}, id="union-mode"),
    pytest.param(LOOKAROUND_MODEL, JSON_SCHEMA, {}, id="regex-engine"),
    pytest.param(LOOKAROUND_MODEL, JSON_SCHEMA, {"output_model_type": PYDANTIC_DATACLASS}, id="regex-engine-dataclass"),
    pytest.param(LOOKAROUND, JSON_SCHEMA, {}, id="regex-engine-root-model"),
    pytest.param(
        {"type": "string", "title": "Name"},
        JSON_SCHEMA,
        {"use_attribute_docstrings": True, "enable_faux_immutability": True},
        id="root-model-config-without-docstrings",
    ),
    pytest.param(UNION_MODEL, JSON_SCHEMA, {"use_attribute_docstrings": True}, id="attribute-docstrings"),
    pytest.param(
        UNION_MODEL,
        JSON_SCHEMA,
        {"use_attribute_docstrings": True, "use_generic_base_class": True},
        id="attribute-docstrings-generic-base-class",
    ),
    pytest.param(
        XML_SCHEMA_DATA_PATH / "pattern_alternatives_simple.xsd",
        InputFileType.XMLSchema,
        {"use_type_alias": True},
        id="compiled-pattern",
    ),
    pytest.param(
        ALIASES,
        JSON_SCHEMA,
        {"use_type_alias": True, "target_python_version": PythonVersion.PY_312},
        id="type-statement",
    ),
    pytest.param(
        ALIASES,
        JSON_SCHEMA,
        {"use_type_alias": True, "target_python_version": PythonVersion.PY_311},
        id="type-alias-type-of-model",
    ),
    pytest.param(
        ALIASES,
        JSON_SCHEMA,
        {
            "use_type_alias": True,
            "target_python_version": PythonVersion.PY_311,
            "output_model_type": PYDANTIC_DATACLASS,
        },
        id="type-alias-annotation-of-model-dataclass",
    ),
    pytest.param(UNION_MODEL, JSON_SCHEMA, {"extra_template_data": USER_CONFIG}, id="user-config"),
    pytest.param(
        UNION_MODEL,
        JSON_SCHEMA,
        {"extra_template_data": USER_CONFIG, "output_model_type": PYDANTIC_DATACLASS},
        id="user-config-dataclass",
    ),
    pytest.param(
        UNION_MODEL,
        JSON_SCHEMA,
        {
            "extra_template_data": {"Model": {"config": {"cache_strings": False}}},
            "output_model_type": PYDANTIC_DATACLASS,
        },
        id="user-config-dataclass-cache-strings",
    ),
    pytest.param(
        UNION_MODEL,
        JSON_SCHEMA,
        {
            "extra_template_data": {"Model": {"config": {"cache_strings": False, "key_pydantic_does_not_know": True}}},
            "output_model_type": PYDANTIC_DATACLASS,
        },
        id="user-config-dataclass-unknown-key",
    ),
    pytest.param(
        UNION_MODEL,
        JSON_SCHEMA,
        {"extra_template_data": USER_CONFIG, "target_pydantic_version": TargetPydanticVersion.V2_11},
        id="user-config-target-2.11",
    ),
    pytest.param(BYTES_MODEL, JSON_SCHEMA, {"use_annotated": True}, id="bytes-pattern"),
    pytest.param(BYTES_MODEL, JSON_SCHEMA, {"field_constraints": True}, id="bytes-pattern-field-constraints"),
    pytest.param(
        BYTES_MODEL,
        JSON_SCHEMA,
        {"use_annotated": True, "target_pydantic_version": TargetPydanticVersion.V2_11},
        id="bytes-pattern-target-2.11",
    ),
    pytest.param(
        BYTES_MODEL,
        JSON_SCHEMA,
        {"use_annotated": True, "target_pydantic_version": TargetPydanticVersion.V2_12},
        id="bytes-pattern-target-2.12",
    ),
    pytest.param(
        BYTES_MODEL,
        JSON_SCHEMA,
        {"use_annotated": True, "use_missing_sentinel": True, "target_pydantic_version": None},
        id="bytes-pattern-missing-sentinel",
    ),
    pytest.param(BYTES_MODEL, JSON_SCHEMA, {}, id="bytes-without-pattern"),
    pytest.param(
        _bytes_or_string_model("Binary", "Text"),
        JSON_SCHEMA,
        {"collapse_root_models": True, "field_constraints": True},
        id="bytes-or-string-pattern",
    ),
    pytest.param(
        _bytes_or_string_model("Text", "Binary"),
        JSON_SCHEMA,
        {"collapse_root_models": True, "field_constraints": True},
        id="string-or-bytes-pattern",
    ),
    pytest.param(LOOKAROUND_MODEL, JSON_SCHEMA, {"target_pydantic_version": None}, id="target-unset"),
    pytest.param(
        LOOKAROUND_MODEL, JSON_SCHEMA, {"target_pydantic_version": TargetPydanticVersion.V2_14}, id="target-2.14"
    ),
    pytest.param(
        LOOKAROUND_MODEL, JSON_SCHEMA, {"output_model_type": DataModelType.MsgspecStruct}, id="msgspec-backend"
    ),
    pytest.param(
        MODULES,
        InputFileType.OpenAPI,
        {"use_attribute_docstrings": True, "union_mode": UnionMode.left_to_right},
        id="modules",
    ),
    pytest.param(
        OPEN_API_DATA_PATH / "nullable_map_parameters_lookaround.yaml",
        InputFileType.OpenAPI,
        {"use_typed_nullable_map_values": True, "openapi_scopes": [OpenAPIScope.Paths, OpenAPIScope.Parameters]},
        id="typed-nullable-map-values-kept-untyped",
    ),
]


def _floor_warnings(input_: Path | dict[str, Any], **options: Any) -> str:
    """Generate the models and return every warning that states the Pydantic release they need, one per line."""
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        generate(input_, disable_timestamp=True, formatters=[Formatter.BUILTIN], **options)
    return "".join(
        f"{record.message}\n" for record in recorded if str(record.message).startswith("The generated models need")
    )


@pytest.mark.parametrize(("input_", "input_file_type", "options"), CASES)
def test_pydantic_floor(input_: Path | dict[str, Any], input_file_type: InputFileType, options: dict[str, Any]) -> None:
    """State once, for the output a generation keeps, the newest minimum among the constructs above the target.

    Each construct lists the models and fields that write it, with their module when there are several. An unset
    or newer target, a backend Pydantic does not build and an older form written for the target state nothing.
    """
    options = {
        "input_file_type": input_file_type,
        "output_model_type": DataModelType.PydanticV2BaseModel,
        "target_pydantic_version": TARGET_2,
        **options,
    }
    assert_output(_floor_warnings(input_, **options), name="warnings")


@pytest.mark.parametrize("command", ["check", "invalid-dotted-stdout-repair"])
def test_pydantic_floor_command_line(
    output_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    """The command line states the floor once per run, for the output it keeps.

    A run with ``--check`` states it as the run that wrote the file did, and its status reflects only the files.
    Output to stdout that repairs invalid dotted names states the floor of the repaired parse.
    """
    schema = tmp_path / "schema.json"
    schema.write_text('{"type": "object", "properties": {"name": {"type": "string", "pattern": "^(?!x)[a-z]+$"}}}')
    common = ["--target-pydantic-version", "2", "--disable-timestamp", "--formatters", "builtin"]
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        if command == "check":
            args = ["--input", str(schema), "--output", str(output_file), "--input-file-type", "jsonschema", *common]
            run_main_with_args(args, capsys=capsys)
            run_main_with_args([*args, "--check"], capsys=capsys)
        else:
            input_path = OPEN_API_DATA_PATH / "invalid_dotted_schema_name.yaml"
            args = ["--input", str(input_path), "--input-file-type", "openapi", "--use-attribute-docstrings", *common]
            run_main_with_args(args, capsys=capsys)
    assert_output(
        "".join(f"{record.message}\n" for record in recorded if record.category is UserWarning), name="warnings"
    )


@pytest.mark.abnormal_path("every ConfigDict key has a row, so only a key Pydantic adds later reaches the check")
@pytest.mark.parametrize("flag", ["check", "report", "off"])
def test_pydantic_floor_config_key_without_row(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, flag: str) -> None:
    """The debug check of generated-code requirements reports a Pydantic ``ConfigDict`` key without a row."""
    report_path = tmp_path / "checks.jsonl"
    monkeypatch.setattr(ConfigDict, "__optional_keys__", ConfigDict.__optional_keys__ | {"key_of_a_newer_release"})
    monkeypatch.setenv(
        CHECK_GENERATED_CODE_REQUIREMENTS_ENV, {"check": "1", "report": f"report:{report_path}", "off": "0"}[flag]
    )
    options: dict[str, Any] = {
        "input_file_type": JSON_SCHEMA,
        "output_model_type": DataModelType.PydanticV2BaseModel,
        "formatters": [Formatter.BUILTIN],
    }
    if flag == "check":
        with pytest.raises(Error, match=f"^{CHECK_GENERATED_CODE_REQUIREMENTS_ENV}: config keys without a row"):
            generate(LOOKAROUND_MODEL, **options)
        return
    generate(LOOKAROUND_MODEL, **options)
    if flag == "report":
        assert_output(report_path.read_text(encoding="utf-8"), name="report")
