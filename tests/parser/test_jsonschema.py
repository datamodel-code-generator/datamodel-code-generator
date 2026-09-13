"""Tests for JSON Schema parser."""

from __future__ import annotations

import gc
import json
import socket
import sys
import weakref
from collections import Counter, defaultdict
from copy import deepcopy
from functools import partial
from ipaddress import ip_address
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, ClassVar, Optional, Union, cast

import pydantic
import pytest
import yaml

import datamodel_code_generator._builtin_formatter as builtin_formatter
from datamodel_code_generator import (
    AliasGenerator,
    AllOfMergeMode,
    DataModelType,
    Error,
    Formatter,
    InputFileType,
    PythonVersion,
    ReadOnlyWriteOnlyModelType,
    YamlValue,
    generate,
)
from datamodel_code_generator._python_type_annotation import PythonTypeRuntimeSymbol
from datamodel_code_generator.config import JSONSchemaParserConfig
from datamodel_code_generator.http import _get_http_stack, _get_httpx
from datamodel_code_generator.imports import Import
from datamodel_code_generator.model import DataModelFieldBase, get_data_model_types
from datamodel_code_generator.model.dataclass import DataClass
from datamodel_code_generator.model.pydantic_v2.base_model import BaseModel
from datamodel_code_generator.model.pydantic_v2.root_model import RootModel
from datamodel_code_generator.model.runtime_validation import (
    UNIQUE_ITEMS_ARRAY_TAIL_PATH_STEP,
    UNIQUE_ITEMS_MAPPING_ADDITIONAL_VALUES_PATH_STEP,
    UNIQUE_ITEMS_MAPPING_VALUES_PATH_STEP,
    ConditionalRequiredRule,
    PatternPropertiesRule,
    UniqueItemsRule,
    _make_internal_schema_runtime_validation,
)
from datamodel_code_generator.model.type_alias import TypeAlias
from datamodel_code_generator.parser.base import (
    _DEFERRED_INHERITED_CLASS_KEY,
    _DEFERRED_INHERITED_TYPE_KEY,
    _RAW_SCHEMA_DEFAULT_KEY,
    _SOURCE_REFERENCE_PATH_KEY,
    SPECIAL_PATH_FORMAT,
    Parser,
    Source,
    _get_inherited_type_modifiers,
    dump_templates,
)
from datamodel_code_generator.parser.jsonschema import (
    _INHERITED_MATERIALIZED_TYPE_SHAPE_KEY,
    JsonSchemaObject,
    JsonSchemaParser,
    Types,
    _get_json_value_type,
    _get_union_variant_name,
    _get_unique_rw_model_variant_source_path,
    _json_literal_may_accept_container,
    _json_literal_values_equal,
    _json_schema_type_may_accept_container,
    _validate_schema_python_import_path,
    get_model_by_path,
    split_json_pointer,
)
from datamodel_code_generator.reference import SPECIAL_PATH_MARKER, Reference
from datamodel_code_generator.types import ANY, DataType
from tests.conftest import assert_inputs_not_mutated, assert_output, create_assert_file_content, validate_generated_code
from tests.main.conftest import assert_generated_model_json_validation, run_generate_file_and_assert

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from pytest_mock import MockerFixture

DATA_PATH: Path = Path(__file__).parents[1] / "data" / "jsonschema"
EXPECTED_PARSER_PATH: Path = Path(__file__).parents[1] / "data" / "expected" / "parser"
EXPECTED_MAIN_JSONSCHEMA_PATH: Path = Path(__file__).parents[1] / "data" / "expected" / "main" / "jsonschema"
assert_file_content = create_assert_file_content(EXPECTED_PARSER_PATH)


def _json_schema_object(data: dict[str, Any]) -> JsonSchemaObject:
    return JsonSchemaObject.model_validate(data)


@pytest.mark.parametrize("entrypoint", ["config", "options", "api"])
@pytest.mark.parametrize("custom_factory", [False, True])
def test_parser_template_data_reuse(output_file: Path, entrypoint: str, *, custom_factory: bool) -> None:
    """Keep caller template data reusable while applying each parser's extra policy."""
    case = json.loads((DATA_PATH.parent / "payloads/parser_template_data_reuse.json").read_text())
    config = JSONSchemaParserConfig(
        extra_template_data=defaultdict(partial(dict) if custom_factory else dict, case["template_data"]),
        formatters=[Formatter.BUILTIN],
    )
    with assert_inputs_not_mutated({"template_data": config.extra_template_data}):
        for extra_fields in ("allow", "forbid"):
            match entrypoint:
                case "api":
                    run_generate_file_and_assert(
                        input_path=DATA_PATH / "person.json",
                        input_file_type=InputFileType.JsonSchema,
                        output_path=output_file,
                        output_model_type=DataModelType.PydanticV2BaseModel,
                        extra_template_data=config.extra_template_data,
                        extra_fields=extra_fields,
                        formatters=[Formatter.BUILTIN],
                        disable_timestamp=True,
                        use_standard_collections=False,
                        use_union_operator=False,
                        settings_path=Path(__file__).parents[2],
                        assert_func=assert_file_content,
                        transform=lambda content: content.partition("\n\n")[2],
                        expected_file=EXPECTED_PARSER_PATH / f"template_data_{extra_fields}.py",
                    )
                    continue
                case "config":
                    parser = JsonSchemaParser(
                        DATA_PATH / "person.json", config=config.model_copy(update={"extra_fields": extra_fields})
                    )
                case _:
                    parser = JsonSchemaParser(
                        DATA_PATH / "person.json",
                        extra_template_data=config.extra_template_data,
                        extra_fields=extra_fields,
                        formatters=[Formatter.BUILTIN],
                    )
            generated = parser.parse()
            assert_output(generated, EXPECTED_PARSER_PATH / f"template_data_{extra_fields}.py")
            output_file.write_text(cast("str", generated), encoding="utf-8")
    assert_generated_model_json_validation(
        output_file,
        module_name="parser_template_data_reuse",
        model_name="Person",
        valid_json=json.dumps(case["valid"]),
        invalid_json=json.dumps(case["invalid"]),
        expected_error_type="extra_forbidden",
    )


@pytest.mark.parametrize(
    "output_model_type",
    [
        pytest.param(DataModelType.DataclassesDataclass, id="dataclass"),
        pytest.param(DataModelType.TypingTypedDict, id="typed-dict"),
        pytest.param(DataModelType.MsgspecStruct, id="msgspec"),
        pytest.param(DataModelType.PydanticV2BaseModel, id="pydantic-v2"),
    ],
)
def test_schema_validator_root_alias_uses_output_model_capability(
    output_model_type: DataModelType,
    output_file: Path,
) -> None:
    """Keep direct-parser root aliases in their output family while preserving Pydantic validators."""
    input_path = DATA_PATH / "unique_items_const_draft6.json"
    model_types = get_data_model_types(
        output_model_type,
        target_python_version=PythonVersion.PY_310,
        use_type_alias=True,
    )
    parser = JsonSchemaParser(
        input_path,
        base_path=input_path.parent,
        data_model_type=model_types.data_model,
        data_model_root_type=model_types.root_model,
        data_model_field_type=model_types.field_model,
        data_type_manager_type=model_types.data_type_manager,
        dump_resolve_reference_action=model_types.dump_resolve_reference_action,
        formatters=[Formatter.BUILTIN],
        generate_schema_validators=True,
        target_python_version=PythonVersion.PY_310,
        use_standard_collections=True,
        use_union_operator=True,
    )
    generated = parser.parse()

    match output_model_type:
        case DataModelType.PydanticV2BaseModel:
            assert_output(
                "# generated by datamodel-codegen:\n"
                "#   filename:  unique_items_const_draft6.json\n\n"
                f"{generated.rstrip()}",
                EXPECTED_MAIN_JSONSCHEMA_PATH / "unique_items_const_draft6.py",
            )
            output_file.write_text(generated, encoding="utf-8")
            assert_generated_model_json_validation(
                output_file,
                module_name="direct_parser_runtime_validation_root_alias",
                model_name="UniqueItemsConstDraft6",
                valid_json="[1,2]",
                invalid_json="[1,1]",
                expected_error_type="value_error",
            )
        case _:
            assert_output(generated, EXPECTED_PARSER_PATH / "runtime_validation_non_pydantic_root_alias.py")
            validate_generated_code(generated, str(output_file), do_exec=True)


def test_schema_validator_retains_unique_items_for_collapsed_referenced_root_model(output_file: Path) -> None:
    """Keep uniqueItems validation when a referenced array root model is collapsed."""
    generate(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Payload",
            "type": "object",
            "properties": {"unique-values": {"$ref": "#/$defs/UniqueValues"}},
            "required": ["unique-values"],
            "$defs": {
                "UniqueValues": {
                    "type": "array",
                    "uniqueItems": True,
                    "items": {"type": "integer"},
                }
            },
        },
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        output_model_type=DataModelType.PydanticV2BaseModel,
        collapse_root_models=True,
        disable_timestamp=True,
        formatters=[],
        generate_schema_validators=True,
    )

    assert_generated_model_json_validation(
        output_file,
        module_name="collapsed_unique_items",
        model_name="Payload",
        valid_json='{"unique-values":[1,2]}',
        invalid_json='{"unique-values":[1,1]}',
        expected_error_type="value_error",
        expected_attribute_path=("unique_values",),
        expected_attribute_value=[1, 2],
    )


def test_generated_formatter_mode_only_enabled_for_builtin() -> None:
    """Keep no-formatter and external-formatter output outside generated fast-path dispatch."""
    model_types = get_data_model_types(
        DataModelType.PydanticV2BaseModel,
        target_python_version=PythonVersion.PY_310,
    )
    input_path = DATA_PATH / "user.json"
    formatted_cases: list[str] = []
    for name, formatters in (
        ("none", []),
        ("black", [Formatter.BLACK]),
        ("builtin", [Formatter.BUILTIN]),
    ):
        parser = JsonSchemaParser(
            input_path,
            base_path=input_path.parent,
            data_model_type=model_types.data_model,
            data_model_root_type=model_types.root_model,
            data_model_field_type=model_types.field_model,
            data_type_manager_type=model_types.data_type_manager,
            dump_resolve_reference_action=model_types.dump_resolve_reference_action,
            formatters=formatters,
            target_python_version=PythonVersion.PY_310,
        )
        output = parser.parse()
        fast_path = vars(parser)["_uses_standard_generation_templates"]
        formatted_cases.append(f"[{name} fast_path={fast_path}]\n{output}")

    assert_output(
        "\n".join(formatted_cases),
        DATA_PATH / "generated_formatter_modes.snapshot",
    )


def test_builtin_formatter_falls_back_for_custom_class_name_generator(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep invalid custom names on the full formatter's syntax-error path."""

    def invalid_class_name(_: str) -> str:
        return "Bad-Name"

    model_types = get_data_model_types(
        DataModelType.PydanticV2BaseModel,
        target_python_version=PythonVersion.PY_311,
    )
    input_path = DATA_PATH / "user.json"
    parser = JsonSchemaParser(
        input_path,
        base_path=input_path.parent,
        data_model_type=model_types.data_model,
        data_model_root_type=model_types.root_model,
        data_model_field_type=model_types.field_model,
        data_type_manager_type=model_types.data_type_manager,
        dump_resolve_reference_action=model_types.dump_resolve_reference_action,
        custom_class_name_generator=invalid_class_name,
        formatters=[Formatter.BUILTIN],
        target_python_version=PythonVersion.PY_311,
    )

    monkeypatch.setattr(
        builtin_formatter,
        "_apply_builtin_generated_formatter",
        lambda *_args, **_kwargs: pytest.fail("custom class-name hook reached generated formatter fast path"),
    )
    output = parser.parse()
    assert_output(output, DATA_PATH / "builtin_formatter_custom_class_name.snapshot")


def test_builtin_formatter_falls_back_for_custom_resolve_reference_action(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep custom trailing source on the full formatter's syntax-error path."""

    def invalid_resolve_reference_action(_: Iterable[str]) -> str:
        return "BROKEN = ("

    model_types = get_data_model_types(
        DataModelType.PydanticV2BaseModel,
        target_python_version=PythonVersion.PY_311,
    )
    input_path = DATA_PATH / "self_reference.json"
    parser = JsonSchemaParser(
        input_path,
        base_path=input_path.parent,
        data_model_type=model_types.data_model,
        data_model_root_type=model_types.root_model,
        data_model_field_type=model_types.field_model,
        data_type_manager_type=model_types.data_type_manager,
        dump_resolve_reference_action=invalid_resolve_reference_action,
        formatters=[Formatter.BUILTIN],
        target_python_version=PythonVersion.PY_311,
    )

    monkeypatch.setattr(
        builtin_formatter,
        "_apply_builtin_generated_formatter",
        lambda *_args, **_kwargs: pytest.fail("custom resolve hook reached generated formatter fast path"),
    )
    output = parser.parse()
    assert_output(output, DATA_PATH / "builtin_formatter_custom_resolve_action.snapshot")


@pytest.fixture(autouse=True)
def block_dns_by_default(mocker: MockerFixture) -> None:
    """Keep tests that mock HTTP requests independent from external DNS."""
    mocker.patch("socket.getaddrinfo", side_effect=OSError)


def test_schema_validator_required_only_schema_filters() -> None:
    """Test detection of required-only combined-schema branches."""
    parser = JsonSchemaParser("", generate_schema_validators=True)
    required_only_schema = JsonSchemaObject.model_validate({
        "type": "object",
        "required": ["a"],
        "$comment": "metadata is ignored",
        "x-vendor": {"metadata": True},
    })

    assert parser._is_required_only_schema(required_only_schema)
    assert parser._get_required_groups([required_only_schema]) == (("a",),)
    assert parser._is_required_only_schema(JsonSchemaObject.model_validate({"required": ["a"], "type": ["object"]}))
    assert not parser._is_required_only_schema(True)
    assert not parser._is_required_only_schema(JsonSchemaObject.model_validate({}))
    assert not parser._is_required_only_schema(JsonSchemaObject.model_validate({"required": []}))
    assert not parser._is_required_only_schema(JsonSchemaObject.model_validate({"$ref": "#/$defs/Model"}))
    assert not parser._is_required_only_schema(JsonSchemaObject.model_validate({"items": {"type": "string"}}))
    assert not parser._is_required_only_schema(JsonSchemaObject.model_validate({"anyOf": [{"required": ["a"]}]}))
    assert not parser._is_required_only_schema(JsonSchemaObject.model_validate({"enum": ["a"]}))
    assert not parser._is_required_only_schema(
        JsonSchemaObject.model_validate({"required": ["a"], "additionalProperties": False})
    )
    assert not parser._is_required_only_schema(
        JsonSchemaObject.model_validate({"required": ["a"], "unevaluatedProperties": False})
    )
    assert not parser._is_required_only_schema(JsonSchemaObject.model_validate({"required": ["a"], "minProperties": 1}))
    assert not parser._is_required_only_schema(JsonSchemaObject.model_validate({"required": ["a"], "maxProperties": 1}))
    assert not parser._is_required_only_schema(
        JsonSchemaObject.model_validate({"required": ["a"], "dependentRequired": {"a": ["b"]}})
    )
    assert not parser._is_required_only_schema(
        JsonSchemaObject.model_validate({"required": ["a"], "dependentSchemas": {"a": {"required": ["b"]}}})
    )
    assert not parser._is_required_only_schema(
        JsonSchemaObject.model_validate({"required": ["a"], "dependencies": {"a": ["b"]}})
    )
    assert not parser._is_required_only_schema(JsonSchemaObject.model_validate({"required": ["a"], "contains": {}}))
    assert not parser._is_required_only_schema(JsonSchemaObject.model_validate({"required": ["a"], "not": {}}))
    assert not parser._is_required_only_schema(JsonSchemaObject.model_validate({"required": ["a"], "type": ["null"]}))
    assert (
        parser._get_required_groups([JsonSchemaObject.model_validate({"properties": {"a": {"type": "string"}}})]) == ()
    )


def test_schema_validator_helpers_disabled() -> None:
    """Test schema validator helpers are inert when disabled."""
    parser = JsonSchemaParser("", generate_schema_validators=False)
    obj = JsonSchemaObject.model_validate({
        "type": "object",
        "minProperties": 1,
        "maxProperties": 2,
        "properties": {"a": {"type": "string"}},
        "oneOf": [{"required": ["a"]}],
    })
    parser.extra_template_data["#/Model"] = {}

    assert not parser._should_parse_object_with_schema_validators(obj)
    assert parser._get_property_count_rule(obj) is None
    assert parser._merge_conditional_properties(obj) is obj

    parser._add_schema_validators("#/Model", "Model", obj, ["#"], [], [])

    assert parser.extra_template_data["#/Model"] == {}


def test_schema_validator_property_count_helper_caches_effective_allof_rule() -> None:
    """Reuse one immutable property-count rule across validator selection and attachment."""
    parser = JsonSchemaParser("", generate_schema_validators=True)
    obj = JsonSchemaObject.model_validate({
        "type": "object",
        "allOf": [
            True,
            {"minProperties": 2},
            {"maxProperties": 4},
        ],
    })
    parser.extra_template_data["#/Model"] = {}

    assert parser._has_property_count_validator(obj)
    rule = parser._get_property_count_rule(obj)
    assert rule is not None
    assert (rule.min_properties, rule.max_properties) == (2, 4)
    parser._add_property_count_validator("#/Model", obj)

    assert parser.extra_template_data["#/Model"]["schema_runtime_validation"].property_count is rule


def test_schema_validator_property_count_helper_stops_at_visited_ref() -> None:
    """Keep $ref sibling counts while avoiding recursive allOf walks."""
    parser = JsonSchemaParser("", generate_schema_validators=True)
    parser.raw_obj = {"$defs": {"Loop": {"allOf": [{"$ref": "#/$defs/Loop", "maxProperties": 4}]}}}
    obj = JsonSchemaObject.model_validate({"allOf": [{"$ref": "#/$defs/Loop", "minProperties": 2}]})

    sources = tuple(parser._iter_property_count_validation_sources(obj))

    assert [source.minProperties for source in sources] == [None, 2, None, None]
    assert [source.maxProperties for source in sources] == [None, None, None, 4]


def test_clear_inherited_field_caches_releases_property_count_rules() -> None:
    """Release opt-in property-count aggregation cache with parser-owned temporary state."""
    parser = JsonSchemaParser("", generate_schema_validators=True)
    parser._get_property_count_rule(JsonSchemaObject.model_validate({"minProperties": 2}))

    assert parser._property_count_rule_cache

    parser._clear_inherited_field_caches()

    assert not parser._property_count_rule_cache


def test_schema_runtime_validation_module_preparation_skips_empty_modules() -> None:
    """Avoid planning work for modules without opt-in runtime metadata."""
    parser = JsonSchemaParser("", generate_schema_validators=True)

    parser._prepare_schema_runtime_validation_module_code([SimpleNamespace(models=[])])


def test_schema_validator_merge_conditional_properties_skips_missing_and_duplicate_properties() -> None:
    """Test conditional property merging skips missing and duplicate branch properties."""
    parser = JsonSchemaParser("", generate_schema_validators=True)
    obj = JsonSchemaObject.model_validate({
        "type": "object",
        "properties": {"metric": {"type": "integer"}},
        "if": {"required": ["kind"], "properties": {"kind": {"const": "metric"}}},
        "then": {"required": ["metric"]},
        "else": {"required": ["metric"], "properties": {"metric": {"type": "integer"}}},
    })

    assert parser._merge_conditional_properties(obj) is obj


def test_schema_validator_input_names_include_validation_aliases_and_schema_base_properties() -> None:
    """Test validator input names include aliases and inherited schema properties."""
    parser = JsonSchemaParser("", generate_schema_validators=True)
    field = DataModelFieldBase(
        name="field_name",
        original_name="field",
        alias="fieldAlias",
        validation_aliases=["fieldAlias", "field-alt"],
        data_type=DataType(type="str"),
    )
    empty_field = DataModelFieldBase(name="field_", original_name="", alias="", data_type=DataType(type="str"))
    nameless_field = DataModelFieldBase(data_type=DataType(type="str"))
    parser.raw_obj = {
        "$defs": {
            "Empty": {"type": "object"},
            "Base": {
                "type": "object",
                "properties": {"base": {"type": "string"}},
            },
        }
    }

    assert parser._field_input_names(field) == ("fieldAlias", "field-alt")
    assert parser._get_input_names_by_property(
        [empty_field, nameless_field],
        [Reference(path="#/$defs/Empty", name="Empty"), Reference(path="#/$defs/Base", name="Base")],
    ) == {"": ("",), "base": ("base",)}


def test_schema_validator_input_names_include_empty_datamodel_base_fields() -> None:
    """Test inherited generated model fields retain empty source names."""
    parser = JsonSchemaParser("", generate_schema_validators=True)
    base_field = DataModelFieldBase(
        name="base_field",
        original_name="base",
        alias="baseAlias",
        validation_aliases=["baseAlias", "base-alt"],
        data_type=DataType(type="str"),
    )
    empty_field = DataModelFieldBase(name="field_", original_name="", alias="", data_type=DataType(type="str"))
    nameless_field = DataModelFieldBase(data_type=DataType(type="str"))
    typed_extra_field = DataModelFieldBase(
        name=BaseModel.TYPED_EXTRA_FIELD_NAME,
        original_name=BaseModel.TYPED_EXTRA_FIELD_NAME,
        data_type=DataType(type="str"),
    )
    base_ref = Reference(path="#/$defs/Base", name="Base")
    BaseModel(reference=base_ref, fields=[base_field, empty_field, nameless_field, typed_extra_field])

    assert parser._get_input_names_by_property([], [base_ref]) == {
        "": ("",),
        "base": ("baseAlias", "base-alt"),
    }
    assert parser._field_input_names(base_field) == ("baseAlias", "base-alt")
    assert JsonSchemaParser("", allow_population_by_field_name=True)._field_input_names(base_field) == (
        "baseAlias",
        "base-alt",
        "base_field",
    )
    assert parser._get_input_names_by_property([], [base_ref]) == {
        "": ("",),
        "base": ("baseAlias", "base-alt"),
    }
    assert parser._required_group_input_names((("base",),), {"base": ("baseAlias",)}) == ((("baseAlias",),),)


def test_schema_validator_input_names_match_pydantic_validation_priority() -> None:
    """Use Pydantic's validation-alias priority for raw schema paths."""
    serialization_field = DataModelFieldBase(
        name="known_value",
        original_name="known-value",
        alias="known-value",
        use_serialization_alias=True,
        data_type=DataType(type="str"),
    )
    validation_field = DataModelFieldBase(
        name="input_value",
        original_name="input-value",
        alias="output-value",
        validation_aliases=["input-value", "inputValue"],
        use_serialization_alias=True,
        data_type=DataType(type="str"),
    )
    nameless_field = DataModelFieldBase(data_type=DataType(type="str"))

    assert JsonSchemaParser("")._field_input_names(serialization_field) == ("known_value",)
    assert JsonSchemaParser("", alias_generator=AliasGenerator.ToCamel)._field_input_names(serialization_field) == (
        "knownValue",
        "known_value",
    )
    assert JsonSchemaParser("", alias_generator=AliasGenerator.ToPascal)._field_input_names(serialization_field) == (
        "KnownValue",
        "known_value",
    )
    assert JsonSchemaParser("", alias_generator=AliasGenerator.ToSnake)._field_input_names(serialization_field) == (
        "known_value",
    )
    assert JsonSchemaParser("", allow_population_by_field_name=True)._field_input_names(validation_field) == (
        "input-value",
        "inputValue",
        "input_value",
    )
    assert JsonSchemaParser("")._field_input_names(nameless_field) == ()


def test_build_missing_required_field_uses_shared_alias_policy() -> None:
    """Required-only schema fields use the common alias normalization."""
    parser = JsonSchemaParser("")

    field = parser._build_missing_required_field("missing", set(), [], "Model")

    assert field.name == "missing"
    assert field.required is True
    assert field.alias is None
    assert field.validation_aliases is None


def test_object_field_merges_property_count_constraints() -> None:
    """Dictionary fields preserve schema property-count constraints."""
    parser = JsonSchemaParser("", field_constraints=True)

    field = parser.get_object_field(
        field_name="values",
        field=JsonSchemaObject.model_validate({"type": "object", "minProperties": 1}),
        required=False,
        field_type=DataType(is_dict=True),
        alias=None,
        original_field_name="values",
    )

    assert field.constraints is not None
    assert field.constraints.min_length == 1


@pytest.mark.parametrize(
    "schema",
    [
        {
            "title": "Model",
            "type": "object",
            "properties": {"": {"type": "string"}, "field_": {"type": "integer"}},
            "allOf": [{"required": [""]}],
        },
        {
            "title": "Model",
            "required": [""],
            "allOf": [
                {
                    "type": "object",
                    "properties": {"": {"type": "string"}, "field_": {"type": "integer"}},
                }
            ],
        },
        {
            "title": "Model",
            "allOf": [
                {
                    "type": "object",
                    "properties": {"": {"type": "string"}, "field_": {"type": "integer"}},
                    "required": [""],
                }
            ],
        },
    ],
    ids=["allof-required", "root-required", "inline-required"],
)
def test_allof_required_preserves_empty_property_name(schema: dict[str, Any]) -> None:
    """Test allOf required handling neither misses nor duplicates an empty property name."""
    parser = JsonSchemaParser(json.dumps(schema))

    parser.parse(format_=False)

    model = next(result for result in parser.results if result.class_name == "Model")
    fields = {field.original_name: field for field in model.fields}
    assert len(model.fields) == len(fields) == 2
    assert fields[""].required
    assert not fields["field_"].required


def test_allof_inheritance_uses_empty_original_name() -> None:
    """Test an empty property override inherits its own type, not the generated-name collision."""
    parser = JsonSchemaParser(
        json.dumps({
            "title": "Child",
            "type": "object",
            "properties": {"": {}},
            "allOf": [{"$ref": "#/$defs/Base"}],
            "$defs": {
                "Base": {
                    "type": "object",
                    "properties": {"": {"type": "string"}, "field_": {"type": "integer"}},
                }
            },
        })
    )

    parser.parse(format_=False)

    child = next(result for result in parser.results if result.class_name == "Child")
    assert len(child.fields) == 1
    assert child.fields[0].original_name is not None
    assert not child.fields[0].original_name
    assert child.fields[0].data_type.type == "str"


def test_read_write_variants_keep_empty_and_generated_name_collision() -> None:
    """Test request/response deduplication distinguishes empty and generated-looking source names."""
    parser = JsonSchemaParser(
        json.dumps({
            "title": "Model",
            "type": "object",
            "properties": {
                "": {"type": "string", "readOnly": True},
                "field_": {"type": "integer", "writeOnly": True},
                "shared": {"type": "boolean"},
            },
            "required": ["", "field_"],
        }),
        read_only_write_only_model_type=ReadOnlyWriteOnlyModelType.All,
    )

    parser.parse(format_=False)

    models = {result.class_name: result for result in parser.results}
    assert [field.original_name for field in models["ModelRequest"].fields] == ["field_", "shared"]
    assert [field.original_name for field in models["ModelResponse"].fields] == ["", "shared"]
    assert [field.original_name for field in models["Model"].fields] == ["", "field_", "shared"]


def test_empty_original_name_supports_explicit_serialization_alias() -> None:
    """Test serialization alias lookup treats an empty original name as present."""
    parser = JsonSchemaParser(
        json.dumps({"title": "Model", "type": "object", "properties": {"": {"type": "string"}}}),
        serialization_aliases={"": "serialized"},
    )

    parser.parse(format_=False)

    model = next(result for result in parser.results if result.class_name == "Model")
    assert len(model.fields) == 1
    assert model.fields[0].serialization_alias == "serialized"


def test_schema_runtime_validation_reuses_existing_instance() -> None:
    """Test schema runtime validation config is reused for a reference."""
    parser = JsonSchemaParser("", generate_schema_validators=True)

    runtime_validation = parser._schema_runtime_validation("#/Model")

    assert parser._schema_runtime_validation("#/Model") is runtime_validation


def test_schema_runtime_validation_copy_replans_unique_items_paths() -> None:
    """Replan uniqueItems rules from the variant schema and visible fields."""
    parser = JsonSchemaParser("")
    source_path = "#/UniqueItemsSource"
    target_path = "#/UniqueItemsTarget"
    parser.extra_template_data[source_path]["schema_runtime_validation"] = _make_internal_schema_runtime_validation(
        unique_items=[UniqueItemsRule(path=())]
    )

    parser._copy_schema_runtime_validation_for_variant(
        source_path,
        target_path,
        [DataModelFieldBase(name="kept", original_name="kept", data_type=DataType(is_list=True))],
        "Request",
        obj=_json_schema_object({
            "type": "object",
            "properties": {
                "kept": {"type": "array", "uniqueItems": True},
                "removed": {"type": "array", "uniqueItems": True},
            },
        }),
    )

    runtime_validation = parser.extra_template_data[target_path]["schema_runtime_validation"]
    assert_output(
        "\n".join(repr(rule.path) for rule in runtime_validation.unique_items) + "\n",
        DATA_PATH / "schema_runtime_unique_items_variant.snapshot",
    )


def test_schema_validator_unique_items_paths_cover_composed_input_shapes() -> None:
    """Collect uniqueItems paths for every inline JSON Schema container shape."""
    parser = JsonSchemaParser("", collapse_root_models=True, generate_schema_validators=True)
    root_schema = _json_schema_object({
        "allOf": [{"type": "array", "uniqueItems": True}],
        "items": [False, {"type": "array", "uniqueItems": True}],
        "prefixItems": [False, {"type": "array", "uniqueItems": True}],
        "properties": {
            "ignored": False,
            "child": {"type": "array", "uniqueItems": True},
        },
    })
    mapping_schema = _json_schema_object({
        "additionalProperties": {"type": "array", "uniqueItems": True},
    })
    prefix_items_tail_schema = _json_schema_object({
        "items": {"type": "array", "uniqueItems": True},
        "prefixItems": [{"type": "array"}, {"type": "string"}],
    })
    direct_schema = _json_schema_object({"type": "array", "uniqueItems": True})
    property_schema = _json_schema_object({
        "allOf": [{"type": "object"}],
        "properties": {
            "unmapped": {"type": "array", "uniqueItems": True},
            "kept": {"type": "array", "uniqueItems": True},
            "ignored": True,
        },
    })
    visited_reference = _json_schema_object({"$ref": "#/$defs/UniqueValues", "uniqueItems": True})
    visited_reference_paths = list(
        parser._iter_unique_items_paths(
            visited_reference,
            (),
            frozenset({parser.model_resolver.resolve_ref(visited_reference.ref)}),
        )
    )
    parser.raw_obj = {
        "$defs": {
            "Loop": {"$ref": "#/$defs/Loop"},
            "UniqueMapping": {
                "additionalProperties": {"type": "array", "uniqueItems": True},
            },
        },
    }
    loop_ref = parser.model_resolver.resolve_ref("#/$defs/Loop")
    visited_all_of_paths = list(
        parser._iter_unique_items_paths(
            _json_schema_object({"allOf": [{"$ref": "#/$defs/Loop"}]}),
            (),
            frozenset({loop_ref}),
        )
    )
    union_paths = tuple(
        (
            reference_path,
            list(parser._iter_unique_items_paths(schema, ())),
        )
        for reference_path, schema in (
            (
                "#/NullableAnyOf",
                _json_schema_object({"anyOf": [{"type": "array", "uniqueItems": True}, {"type": "null"}]}),
            ),
            (
                "#/NullableOneOf",
                _json_schema_object({"oneOf": [{"type": "array", "uniqueItems": True}, {"type": "null"}]}),
            ),
            (
                "#/UnconstrainedArrayAnyOf",
                _json_schema_object({
                    "anyOf": [
                        {"type": "array", "uniqueItems": True},
                        {"type": "array"},
                    ],
                }),
            ),
            (
                "#/UnknownArrayAnyOf",
                _json_schema_object({"anyOf": [{"type": "array", "uniqueItems": True}, {}]}),
            ),
            (
                "#/TrueArrayAnyOf",
                _json_schema_object({"anyOf": [{"type": "array", "uniqueItems": True}, True]}),
            ),
            (
                "#/FalseArrayAnyOf",
                _json_schema_object({"anyOf": [{"type": "array", "uniqueItems": True}, False]}),
            ),
            (
                "#/StringArrayAnyOf",
                _json_schema_object({"anyOf": [{"type": "array", "uniqueItems": True}, {"type": "string"}]}),
            ),
            (
                "#/RecursiveArrayAnyOf",
                _json_schema_object({
                    "anyOf": [
                        {"type": "array", "uniqueItems": True},
                        {"$ref": "#/$defs/Loop"},
                    ],
                }),
            ),
            (
                "#/RefMappingAnyOf",
                _json_schema_object({
                    "anyOf": [
                        {"$ref": "#/$defs/UniqueMapping"},
                        {"additionalProperties": {"type": "array", "uniqueItems": True}},
                    ],
                }),
            ),
            (
                "#/RefMappingOneOf",
                _json_schema_object({
                    "oneOf": [
                        {"$ref": "#/$defs/UniqueMapping"},
                        {"additionalProperties": {"type": "array", "uniqueItems": True}},
                    ],
                }),
            ),
            (
                "#/MappingFallbackAnyOf",
                _json_schema_object({
                    "anyOf": [
                        {"additionalProperties": {"type": "array", "uniqueItems": True}},
                        {"type": "object"},
                    ],
                }),
            ),
            (
                "#/MappingFallbackOneOf",
                _json_schema_object({
                    "oneOf": [
                        {"additionalProperties": {"type": "array", "uniqueItems": True}},
                        {"type": "object"},
                    ],
                }),
            ),
            (
                "#/ImpossibleArrayAnyOf",
                _json_schema_object({
                    "anyOf": [
                        {"type": "array", "anyOf": [{"type": "object"}]},
                        {"type": "array", "uniqueItems": True},
                    ],
                }),
            ),
            (
                "#/ImpossibleMappingAnyOf",
                _json_schema_object({
                    "anyOf": [
                        {"type": "object", "anyOf": [{"type": "array"}]},
                        {"additionalProperties": {"type": "array", "uniqueItems": True}},
                    ],
                }),
            ),
            (
                "#/ConstScalarArrayAnyOf",
                _json_schema_object({
                    "anyOf": [
                        {"type": "array", "uniqueItems": True},
                        {"const": "scalar"},
                    ],
                }),
            ),
            (
                "#/EnumScalarMappingOneOf",
                _json_schema_object({
                    "oneOf": [
                        {"additionalProperties": {"type": "array", "uniqueItems": True}},
                        {"enum": ["scalar", 0]},
                    ],
                }),
            ),
            (
                "#/ConstArrayLiteralAnyOf",
                _json_schema_object({
                    "anyOf": [
                        {"type": "array", "uniqueItems": True},
                        {"const": [1, 1]},
                    ],
                }),
            ),
            (
                "#/EnumObjectLiteralOneOf",
                _json_schema_object({
                    "oneOf": [
                        {"additionalProperties": {"type": "array", "uniqueItems": True}},
                        {"enum": [{"x": [1, 1]}]},
                    ],
                }),
            ),
        )
    )
    literal_values = parser._schema_literal_values(
        _json_schema_object({"const": [{"nested": [True]}], "enum": [[{"nested": [1]}]]})
    )
    skipped_rule_paths = list(
        parser._iter_unique_items_rules(
            _json_schema_object({
                "allOf": [True],
                "properties": {
                    "ignored": False,
                    "kept": {"type": "array", "uniqueItems": True},
                },
            }),
            (),
            include_properties=True,
        )
    )

    parser._add_unique_items_validator("#/Root", root_schema, [], [], is_root_model=True)
    parser._add_unique_items_validator("#/Mapping", mapping_schema, [], [], is_root_model=True)
    parser._add_unique_items_validator("#/Tail", prefix_items_tail_schema, [], [], is_root_model=True)
    parser._add_unique_items_validator("#/Direct", direct_schema, [], [], is_root_model=False)
    parser._add_unique_items_validator(
        "#/Properties",
        property_schema,
        [DataModelFieldBase(name="kept", original_name="kept", data_type=DataType(type="str"))],
        [],
        is_root_model=False,
    )

    assert_output(
        "\n".join(
            f"{reference_path}: {runtime_validation.unique_items!r}"
            for reference_path in ("#/Root", "#/Mapping", "#/Tail", "#/Direct", "#/Properties")
            if (runtime_validation := parser.extra_template_data[reference_path]["schema_runtime_validation"])
        )
        + f"\n#/VisitedReference: {visited_reference_paths!r}"
        + f"\n#/VisitedAllOfReference: {visited_all_of_paths!r}"
        + f"\n#/LiteralIntersection: {literal_values!r}"
        + f"\n#/LiteralFallback: {_json_literal_values_equal(object(), object())}"
        + "\n#/LiteralEqualities: "
        + repr((
            _json_literal_values_equal(None, None),
            _json_literal_values_equal(1, 1.0),
            _json_literal_values_equal("value", "value"),
        ))
        + f"\n#/SkippedRules: {skipped_rule_paths!r}"
        + "".join(f"\n{reference_path}: {paths!r}" for reference_path, paths in union_paths)
        + "\n",
        DATA_PATH / "schema_runtime_unique_items_paths.snapshot",
    )


def test_schema_validator_unique_items_array_capability_branches() -> None:
    """Conservatively classify union branches that can receive array input."""
    parser = JsonSchemaParser("", collapse_root_models=True, generate_schema_validators=True)
    parser.raw_obj = {"$defs": {"String": {"type": "string"}}}
    visited_refs = frozenset[str]()
    cases: tuple[tuple[str, JsonSchemaObject | bool], ...] = (
        ("false", False),
        ("true", True),
        ("array", _json_schema_object({"type": "array"})),
        ("array-type-list", _json_schema_object({"type": ["array", "null"]})),
        ("string-type-list", _json_schema_object({"type": ["string", "null"]})),
        ("items", _json_schema_object({"items": {}})),
        ("prefix-items", _json_schema_object({"prefixItems": []})),
        ("false-object", JsonSchemaObject(is_boolean_schema_false=True)),
        ("string-ref", _json_schema_object({"$ref": "#/$defs/String"})),
        ("false-all-of", _json_schema_object({"allOf": [False]})),
        ("true-all-of", _json_schema_object({"allOf": [True]})),
        ("array-any-of", _json_schema_object({"anyOf": [{"type": "string"}, {"type": "array"}]})),
        ("string-one-of", _json_schema_object({"oneOf": [{"type": "string"}]})),
    )
    capability_lines = [
        f"{name}={parser._schema_item_may_accept_container(item, 'array', visited_refs)}" for name, item in cases
    ]
    unique_array = _json_schema_object({"type": "array", "uniqueItems": True})
    identical_union_paths = list(parser._iter_union_unique_items_paths((unique_array, unique_array), (), visited_refs))
    unknown_path_union_paths = list(
        parser._iter_union_unique_items_paths((unique_array, unique_array), ("unknown",), visited_refs)
    )
    non_array_union_paths = list(
        parser._iter_union_unique_items_paths((False, _json_schema_object({"type": "string"})), (), visited_refs)
    )
    path_container_type_lines = [
        f"{path!r}={parser._unique_items_path_container_type(path)!r}"
        for path in (
            (),
            (None,),
            (1,),
            ((UNIQUE_ITEMS_ARRAY_TAIL_PATH_STEP, 1),),
            (UNIQUE_ITEMS_MAPPING_VALUES_PATH_STEP,),
            (("__json_schema_mapping_pattern_values__", "^x", None),),
            ((UNIQUE_ITEMS_MAPPING_ADDITIONAL_VALUES_PATH_STEP, ((),), ()),),
            (("field",),),
            ("unknown",),
        )
    ]

    assert_output(
        "\n".join((
            *capability_lines,
            *path_container_type_lines,
            f"identical-union={identical_union_paths!r}",
            f"unknown-path-union={unknown_path_union_paths!r}",
            f"non-array-union={non_array_union_paths!r}",
        ))
        + "\n",
        DATA_PATH / "schema_runtime_unique_items_array_capability.snapshot",
    )


def test_schema_validator_unique_items_branch_coverage() -> None:
    """Exercise deduplication and loop fallthroughs in the uniqueItems planner."""
    parser = JsonSchemaParser("", collapse_root_models=False, generate_schema_validators=True)
    parser.raw_obj = {"$defs": {"Array": {"type": "array"}}}
    visited_refs = frozenset({parser.model_resolver.resolve_ref("#/$defs/Array")})
    reference = _json_schema_object({"$ref": "#/$defs/Array"})
    branch_rules, candidates = parser._get_union_unique_items_branch_rules((True, reference), (), visited_refs, None)
    owner = RootModel(
        reference=Reference(path="#/$defs/Owner", name="Owner"),
        fields=[DataModelFieldBase(name="root", data_type=DataType(is_list=True))],
    )
    owner._set_internal_template_data(
        "schema_runtime_validation",
        _make_internal_schema_runtime_validation(unique_items=[UniqueItemsRule(path=())]),
    )
    owner_types = tuple(DataType(reference=owner.reference) for _ in range(3))
    common_paths = parser._get_common_executable_unique_items_paths(owner_types, (), set())
    pattern_rules = tuple(
        parser._iter_unique_items_rules(
            _json_schema_object({
                "patternProperties": {
                    "^ignored": False,
                    "^value": {"type": "array", "uniqueItems": True},
                }
            }),
            (),
        )
    )

    parser._add_required_groups_validator("#/Model", "anyOf", (("value",),), {})
    parser._add_required_groups_validator("#/Model", "anyOf", (("value",),), {})
    conditional = _json_schema_object({
        "if": {"required": ["kind"], "properties": {"kind": {"const": "metric"}}},
        "then": {"required": ["value"]},
    })
    parser._add_conditional_validator("#/Model", conditional, {})
    parser._add_conditional_validator("#/Model", conditional, {})
    runtime_validation = parser.extra_template_data["#/Model"]["schema_runtime_validation"]

    assert_output(
        "\n".join((
            f"literal-object={_json_literal_may_accept_container({}, 'object')}",
            f"type-list={_json_schema_type_may_accept_container(['array', 'null'], 'array')}",
            f"schema-object={parser._schema_item_may_accept_container(reference, 'array', visited_refs)}",
            f"branch-rule-counts={tuple(len(rules) for _, rules in branch_rules)!r}",
            f"candidate-paths={tuple(rule.path for rule in candidates)!r}",
            f"common-paths={tuple(sorted(common_paths, key=repr))!r}",
            f"pattern-paths={tuple(rule.path for rule in pattern_rules)!r}",
            f"required-group-count={len(runtime_validation.required_groups)}",
            f"conditional-required-count={len(runtime_validation.conditional_required)}",
        ))
        + "\n",
        DATA_PATH / "schema_runtime_unique_items_branch_coverage.snapshot",
    )


def test_schema_validator_unique_items_executable_paths_cover_all_data_type_branches() -> None:
    """Collect executable child paths without recursing through data-type cycles."""
    parser = JsonSchemaParser("", generate_schema_validators=True)
    owner_reference = Reference(path="#/$defs/Owner", name="Owner")
    owner = RootModel(
        reference=owner_reference,
        fields=[DataModelFieldBase(name="root", data_type=DataType(is_list=True))],
    )
    owner._set_internal_template_data(
        "schema_runtime_validation",
        _make_internal_schema_runtime_validation(unique_items=[UniqueItemsRule(path=())]),
    )
    owner_type = DataType(reference=owner_reference)
    fallback_reference = Reference(path="#/$defs/Fallback", name="Fallback")
    BaseModel(reference=fallback_reference, fields=[])
    parser.extra_template_data[fallback_reference.path]["schema_runtime_validation"] = (
        _make_internal_schema_runtime_validation(unique_items=[UniqueItemsRule(path=())])
    )
    cyclic_type = DataType()
    outer_root_type = DataType(is_list=True, data_types=[owner_type])
    parser._add_unique_items_validator(
        "#/Outer",
        _json_schema_object({"items": {"type": "array", "uniqueItems": True}}),
        [DataModelFieldBase(name="root", data_type=outer_root_type)],
        [],
        is_root_model=True,
    )
    child_reference = Reference(path="#/$defs/Child", name="Child")
    child = BaseModel(
        reference=child_reference,
        fields=[DataModelFieldBase(name="my_field", original_name="my_field", data_type=DataType(type="str"))],
    )
    child._set_internal_template_data(
        "schema_runtime_validation",
        _make_internal_schema_runtime_validation(
            unique_items=[
                UniqueItemsRule(
                    path=(
                        (
                            UNIQUE_ITEMS_MAPPING_ADDITIONAL_VALUES_PATH_STEP,
                            (("my_field", "my-field", "myField"),),
                            (),
                        ),
                    )
                )
            ]
        ),
    )
    parser._add_unique_items_validator(
        "#/Parent",
        _json_schema_object({
            "type": "object",
            "properties": {
                "child": {
                    "type": "object",
                    "properties": {"my-field": {"type": "array"}},
                    "additionalProperties": {"type": "array", "uniqueItems": True},
                }
            },
        }),
        [DataModelFieldBase(name="child", original_name="child", data_type=DataType(reference=child_reference))],
        [],
        is_root_model=False,
    )
    unowned_reference = Reference(path="#/$defs/Unowned", name="Unowned")
    unowned = BaseModel(
        reference=unowned_reference,
        fields=[DataModelFieldBase(name="my_field", original_name="my_field", data_type=DataType(type="str"))],
    )
    unowned._set_internal_template_data(
        "schema_runtime_validation",
        _make_internal_schema_runtime_validation(
            unique_items=[
                UniqueItemsRule(path=(("other",),)),
                UniqueItemsRule(path=(("long",), ("tail",))),
            ]
        ),
    )
    parser._add_unique_items_validator(
        "#/UnownedParent",
        _json_schema_object({
            "type": "object",
            "properties": {
                "child": {
                    "type": "object",
                    "properties": {"my-field": {"type": "array"}},
                    "additionalProperties": {"type": "array", "uniqueItems": True},
                }
            },
        }),
        [DataModelFieldBase(name="child", original_name="child", data_type=DataType(reference=unowned_reference))],
        [],
        is_root_model=False,
    )
    fixed_paths = parser._get_executable_unique_items_paths(
        DataType(is_tuple=True, tuple_item_count=2, data_types=[owner_type, owner_type]),
        (),
    )
    variable_paths = parser._get_executable_unique_items_paths(
        DataType(is_tuple=True, data_types=[owner_type]),
        (),
    )
    mapping_paths = parser._get_executable_unique_items_paths(
        DataType(is_mapping=True, data_types=[owner_type]),
        (),
    )
    list_child_paths = parser._get_executable_unique_items_paths(
        DataType(is_list=True, data_types=[DataType(reference=child_reference)]),
        (),
    )
    parser.collapse_root_models = True

    assert_output(
        "\n".join((
            f"visited-array={parser._data_type_may_accept_array(cyclic_type, frozenset({id(cyclic_type)}))}",
            f"unknown-array={parser._data_type_may_accept_array(DataType())}",
            f"visited-paths={parser._get_executable_unique_items_paths(cyclic_type, (), {id(cyclic_type)})!r}",
            f"fallback={parser._get_executable_unique_items_paths(DataType(reference=fallback_reference), ())!r}",
            f"fixed={fixed_paths!r}",
            f"variable={variable_paths!r}",
            f"mapping={mapping_paths!r}",
            f"list-child={sorted(list_child_paths, key=repr)!r}",
            f"collapsed-root={parser._get_executable_unique_items_paths(DataType(reference=owner_reference), ())!r}",
            f"root-owned={parser.extra_template_data['#/Outer'].get('schema_runtime_validation')!r}",
            f"selector-owned={parser.extra_template_data['#/Parent'].get('schema_runtime_validation')!r}",
            f"selector-unowned={bool(parser.extra_template_data['#/UnownedParent']['schema_runtime_validation'].unique_items)}",
        ))
        + "\n",
        DATA_PATH / "schema_runtime_unique_items_executable_paths.snapshot",
    )


def test_schema_validator_adds_all_of_missing_required_field() -> None:
    """Materialize a required field supplied only by an allOf branch."""
    parser = JsonSchemaParser(
        json.dumps({
            "title": "MissingRequired",
            "type": "object",
            "properties": {"known": {"type": "string"}},
            "allOf": [{"required": ["missing"]}],
        })
    )

    parser.parse(format_=False)

    model = next(result for result in parser.results if result.class_name == "MissingRequired")
    assert_output(
        "\n".join(f"{field.original_name}={field.required}" for field in model.fields) + "\n",
        DATA_PATH / "schema_runtime_missing_required.snapshot",
    )


def test_schema_validator_pattern_property_helpers_collect_inherited_sources() -> None:
    """Test patternProperties helper walks usable inherited schema sources."""
    parser = JsonSchemaParser("", generate_schema_validators=True)
    parser.raw_obj = {"$defs": {"Loop": {"allOf": [{"$ref": "#/$defs/Loop"}]}}}
    obj = JsonSchemaObject.model_validate({
        "allOf": [
            True,
            {"$ref": "#/$defs/Loop"},
            {"type": "object", "patternProperties": {"^x": True}},
        ]
    })

    sources = list(parser._iter_schema_validation_sources(obj))

    assert len(sources) == 3
    assert sources[-1].patternProperties == {"^x": True}


def test_schema_validator_pattern_property_helpers_collect_value_types() -> None:
    """Test patternProperties helper collects generated value data types."""
    parser = JsonSchemaParser("", generate_schema_validators=True)
    obj = JsonSchemaObject.model_validate({
        "type": "object",
        "patternProperties": {"^reject": False, "^any": True},
        "additionalProperties": {"type": "integer"},
    })
    parser.extra_template_data["#/Model"] = {}

    pattern_value_types, rejected_patterns, additional_property_type, allow_unmatched = (
        parser._collect_pattern_property_validators("Model", obj, ["#"])
    )

    assert pattern_value_types[0][0] == "^any"
    assert rejected_patterns == ["^reject"]
    assert additional_property_type is not None
    assert allow_unmatched

    parser._add_pattern_properties_validator("#/Model", "Model", obj, ["#"], [], [])

    runtime_validation = parser.extra_template_data["#/Model"]["schema_runtime_validation"]
    assert runtime_validation.data_types


def test_schema_validator_pattern_property_helpers_ignore_unexpected_value_types() -> None:
    """Test patternProperties collection ignores unexpected runtime values defensively."""
    parser = JsonSchemaParser("", generate_schema_validators=True)
    obj = JsonSchemaObject.model_validate({"type": "object"})
    obj.patternProperties = {"^ignored": object()}  # type: ignore[dict-item]

    pattern_value_types, rejected_patterns, additional_property_type, allow_unmatched = (
        parser._collect_pattern_property_validators("Model", obj, ["#"])
    )

    assert pattern_value_types == []
    assert rejected_patterns == []
    assert additional_property_type is None
    assert allow_unmatched


def test_schema_validator_conditional_predicate_helpers() -> None:
    """Test conditional predicate extraction accepts only mechanical cases."""
    parser = JsonSchemaParser("", generate_schema_validators=True)

    assert (
        parser._get_conditional_predicate(
            JsonSchemaObject.model_validate({"if": {"required": ["kind"], "properties": {"kind": True}}})
        )
        is None
    )
    assert parser._get_conditional_predicate(
        JsonSchemaObject.model_validate({"if": {"required": ["kind"], "properties": {"kind": {"enum": ["a", "b"]}}}})
    ) == (("kind", ("a", "b")),)
    assert (
        parser._get_conditional_predicate(
            JsonSchemaObject.model_validate({"if": {"required": ["kind"], "properties": {"kind": {"type": "string"}}}})
        )
        is None
    )

    parser.extra_template_data["#/Model"] = {}
    parser._add_conditional_validator(
        "#/Model",
        JsonSchemaObject.model_validate({
            "if": {"required": ["kind"], "properties": {"kind": {"const": "metric"}}},
            "then": {},
        }),
        {"kind": ("kind",)},
    )

    assert parser.extra_template_data["#/Model"] == {}


def test_parse_id_traverses_property_names_schema() -> None:
    """Test $id collection traverses propertyNames schemas."""
    parser = JsonSchemaParser("")
    obj = JsonSchemaObject.model_validate({"propertyNames": {"$id": "urn:property-name"}})

    parser.parse_id(obj, ["#"])

    assert parser.model_resolver.ids[""]["urn:property-name"] == "#/propertyNames"


def test_traverse_schema_objects_preserves_builtin_callback_order() -> None:
    """Visit built-in child schema fields in their established order."""
    parser = JsonSchemaParser("", generate_schema_validators=True)
    obj = JsonSchemaObject.model_validate({
        "items": {"type": "string"},
        "prefixItems": [{"type": "integer"}],
        "additionalProperties": {"type": "number"},
        "unevaluatedProperties": {"type": "boolean"},
        "unevaluatedItems": {"type": "null"},
        "patternProperties": {"^value": {"type": "string"}},
        "propertyNames": {"type": "string"},
        "anyOf": [{"type": "string"}],
        "allOf": [{"type": "integer"}],
        "oneOf": [{"type": "number"}],
        "if": {"type": "object"},
        "then": {"type": "array"},
        "else": {"type": "null"},
        "properties": {"property": {"type": "boolean"}},
    })
    visited: list[str] = []

    parser._traverse_schema_objects(obj, ["#"], lambda _, path: visited.append("/".join(path)))

    assert_output(
        "\n".join(visited) + "\n",
        DATA_PATH / "schema_traversal_all_child_fields.snapshot",
    )


def test_traverse_schema_objects_handles_conditional_schemas_without_child_fields() -> None:
    """Traverse conditional extras only when schema validators are enabled."""
    obj = JsonSchemaObject.model_validate({
        "if": {"type": "object"},
        "then": {"type": "array"},
        "else": {"type": "null"},
    })
    validators_visited: list[str] = []
    JsonSchemaParser("", generate_schema_validators=True)._traverse_schema_objects(
        obj,
        ["#"],
        lambda _, path: validators_visited.append("/".join(path)),
    )
    no_validators_visited: list[str] = []
    JsonSchemaParser("")._traverse_schema_objects(
        obj,
        ["#"],
        lambda _, path: no_validators_visited.append("/".join(path)),
    )

    assert_output(
        "[schema-validators]\n"
        + "\n".join(validators_visited)
        + "\n[without-schema-validators]\n"
        + "\n".join(no_validators_visited)
        + "\n",
        DATA_PATH / "schema_traversal_conditional_fields.snapshot",
    )


def test_traverse_schema_objects_keeps_explicit_empty_child_fields_on_regular_path() -> None:
    """Do not classify explicitly empty or null child fields as unprovided leaves."""
    parser = JsonSchemaParser("")
    obj = JsonSchemaObject.model_validate({
        "items": None,
        "prefixItems": [],
        "additionalProperties": None,
        "unevaluatedProperties": None,
        "unevaluatedItems": None,
        "patternProperties": {},
        "propertyNames": None,
        "anyOf": [],
        "allOf": [],
        "oneOf": [],
        "properties": {},
    })
    visited: list[str] = []

    parser._traverse_schema_objects(obj, ["#"], lambda _, path: visited.append("/".join(path)))

    assert_output(
        "\n".join(visited) + "\n",
        DATA_PATH / "schema_traversal_explicit_empty_child_fields.snapshot",
    )


def test_traverse_schema_objects_skips_builtin_leaf_children() -> None:
    """Stop after the callback for a built-in primitive leaf schema."""
    parser = JsonSchemaParser("")
    visited: list[str] = []

    parser._traverse_schema_objects(
        JsonSchemaObject.model_validate({"type": "string"}),
        ["#"],
        lambda _, path: visited.append("/".join(path)),
    )

    assert_output(
        "\n".join(visited) + "\n",
        DATA_PATH / "schema_traversal_explicit_empty_child_fields.snapshot",
    )


def test_traverse_schema_objects_falls_back_for_parser_subclass_children() -> None:
    """Keep subclass-defined child traversal reachable for schemas with no built-in children."""

    class ExtensionSchema(JsonSchemaObject):
        children: list[JsonSchemaObject] = pydantic.Field(default_factory=list)

    class ExtensionParser(JsonSchemaParser):
        SCHEMA_OBJECT_TYPE = ExtensionSchema

        def _traverse_schema_objects(
            self,
            obj: JsonSchemaObject,
            path: list[str],
            callback: Callable[[JsonSchemaObject, list[str]], None],
            *,
            include_one_of: bool = True,
        ) -> None:
            super()._traverse_schema_objects(obj, path, callback, include_one_of=include_one_of)
            if isinstance(obj, ExtensionSchema):
                for index, child in enumerate(obj.children):
                    self._traverse_schema_objects(
                        child,
                        [*path, "children", str(index)],
                        callback,
                        include_one_of=include_one_of,
                    )

    parser = ExtensionParser("")
    obj = ExtensionSchema.model_validate({"children": [{"type": "string"}]})
    visited: list[str] = []

    parser._traverse_schema_objects(obj, ["#"], lambda _, path: visited.append("/".join(path)))

    builtin_parser_visited: list[str] = []
    JsonSchemaParser("")._traverse_schema_objects(
        ExtensionSchema.model_validate({"type": "string"}),
        ["#"],
        lambda _, path: builtin_parser_visited.append("/".join(path)),
    )

    assert_output(
        "[parser-subclass-child]\n"
        + "\n".join(visited)
        + "\n[exact-parser-schema-subclass]\n"
        + "\n".join(builtin_parser_visited)
        + "\n",
        DATA_PATH / "schema_traversal_parser_subclass_child.snapshot",
    )


def test_parse_obj_returns_when_merged_ref_still_has_ref(mocker: MockerFixture) -> None:
    """Test parse_obj stops after parsing a schema-keyword ref that still resolves as a ref."""
    parser = JsonSchemaParser("")
    obj = JsonSchemaObject.model_validate({"$ref": "#/$defs/Target", "minLength": 1})
    mocker.patch.object(parser, "_merge_ref_with_schema", return_value=obj)
    parse_ref = mocker.patch.object(parser, "parse_ref")

    parser.parse_obj("Target", obj, ["#", "$defs", "Target"])

    parse_ref.assert_called_once_with(obj, ["#", "$defs", "Target"])


@pytest.mark.parametrize(
    ("schema", "path", "model"),
    [
        ({"foo": "bar"}, None, {"foo": "bar"}),
        ({"a": {"foo": "bar"}}, "a", {"foo": "bar"}),
        ({"a": {"b": {"foo": "bar"}}}, "a/b", {"foo": "bar"}),
        ({"a": {"b": {"c": {"foo": "bar"}}}}, "a/b", {"c": {"foo": "bar"}}),
        ({"a": {"b": {"c": {"foo": "bar"}}}}, "a/b/c", {"foo": "bar"}),
        ({"a": [{"x": 1}, {"y": 2}]}, "a/0", {"x": 1}),
        ({"a": [{"x": 1}, {"y": 2}]}, "a/1", {"y": 2}),
    ],
)
def test_get_model_by_path(schema: dict, path: str, model: dict) -> None:
    """Test model retrieval by path."""
    assert get_model_by_path(schema, path.split("/") if path else []) == model


@pytest.mark.parametrize("path", [["0"], [0]])
def test_get_model_by_path_preserves_integer_keys(path: list[str] | list[int]) -> None:
    """Keep both supported key types when decoding raw lookup paths."""
    assert get_model_by_path([{"type": "integer"}], path) == {"type": "integer"}
    assert get_model_by_path({"0": {"type": "string"}}, path) == {"type": "string"}


def test_ref_raw_schema_decodes_json_pointer_segments_once() -> None:
    """Keep decoded pointer tokens from being decoded again during lookup."""
    once_value = {"type": "string"}
    schema = {
        "$defs": {
            "a/b~1c": once_value,
            "a/b~0c": {"type": "integer"},
            "a/b": {"type": "number"},
        }
    }
    parser = JsonSchemaParser("")
    parser.raw_obj = schema

    assert split_json_pointer(schema, "$defs/a~1b~01c") == ["$defs", "a/b~1c"]
    assert parser._get_ref_raw_schema("#/$defs/a~1b~01c") is once_value
    assert parser._get_ref_raw_schema("#/$defs/a~1b~00c") == {"type": "integer"}
    assert parser._get_ref_raw_schema("#/$defs/a%7E1b") == {"type": "number"}
    assert parser._ref_schema_exists("#/$defs/a~1b~01c")


@pytest.mark.parametrize(
    ("path", "match"),
    [
        ("a/foo", "Invalid JSON pointer array index 'foo'"),
        ("a/-1", "Invalid JSON pointer array index '-1'"),
        ("a/01", "Invalid JSON pointer array index '01'"),
        ("a/99999", "JSON pointer array index 99999 is out of range"),
        ("a/0x1", "Invalid JSON pointer array index '0x1'"),
        ("a/0o1", "Invalid JSON pointer array index '0o1'"),
        ("a/0b1", "Invalid JSON pointer array index '0b1'"),
        ("a/017", "Invalid JSON pointer array index '017'"),
        ("a/1_0", "Invalid JSON pointer array index '1_0'"),
        ("a/+1", r"Invalid JSON pointer array index '\+1'"),
    ],
)
def test_get_model_by_path_rejects_invalid_list_index(path: str, match: str) -> None:
    """Test list-index pointer segments are validated, not fed to raw list indexing."""
    schema = {"a": [{"x": 1}, {"y": 2}]}
    with pytest.raises(Error, match=match):
        get_model_by_path(schema, path.split("/"))


@pytest.mark.skipif(
    not hasattr(sys, "set_int_max_str_digits"),
    reason="int string-conversion length limit requires Python 3.11+",
)
def test_get_model_by_path_rejects_overlong_list_index() -> None:
    """Test a digit string above the int conversion limit raises Error, not ValueError."""
    schema = {"a": [{"x": 1}]}
    overlong = "9" * (sys.get_int_max_str_digits() + 1)
    with pytest.raises(Error, match="integer string is too long to parse"):
        get_model_by_path(schema, ["a", overlong])


def test_split_json_pointer_slow_path_rejects_invalid_list_index() -> None:
    """Test the slow-path pointer traversal applies the same list-index guard."""
    # "~1" forces the slow path; the escaped key resolves to a list, then the next
    # segment is an invalid index.
    schema = {"weird/key": ["x", "y"]}
    with pytest.raises(Error, match="Invalid JSON pointer array index 'foo'"):
        split_json_pointer(schema, "weird~1key/foo")


def test_split_json_pointer_slow_path_preserves_out_of_range_index() -> None:
    """Let deferred pointer resolution diagnose a valid but unavailable list index."""
    schema = {"weird/key": ["x", "y"]}
    assert split_json_pointer(schema, "weird~1key/1") == ["weird/key", "1"]
    assert split_json_pointer(schema, "weird~1key/9") == ["weird/key", "9"]
    assert split_json_pointer(schema, "weird~1key/9/nested") == ["weird/key", "9", "nested"]


@pytest.mark.parametrize(
    ("ref", "match"),
    [
        ("#/items/foo", "Invalid JSON pointer array index 'foo'"),
        ("#/items/01", "Invalid JSON pointer array index '01'"),
    ],
)
def test_parse_deferred_json_pointer_rejects_invalid_array_index(ref: str, match: str) -> None:
    """Do not classify syntactically invalid array indices as dangling references."""
    parser = JsonSchemaParser("")
    with pytest.raises(Error, match=match):
        parser.parse_json_pointer({"items": [{}]}, ref, [])


@pytest.mark.skipif(
    not hasattr(sys, "set_int_max_str_digits") or sys.get_int_max_str_digits() == 0,
    reason="int string-conversion length limit requires Python 3.11+",
)
def test_parse_deferred_json_pointer_rejects_overlong_array_index() -> None:
    """Keep integer-conversion failures outside dangling-reference diagnostics."""
    parser = JsonSchemaParser("")
    overlong = "9" * (sys.get_int_max_str_digits() + 1)
    with pytest.raises(Error, match="integer string is too long to parse"):
        parser.parse_json_pointer({"items": [{}]}, f"#/items/{overlong}", [])


def test_validate_schema_python_import_path_rejects_non_string() -> None:
    """Test schema import path validation rejects non-string values."""
    with pytest.raises(Error, match="customTypePath must be a dotted Python identifier path: 1"):
        _validate_schema_python_import_path(1, "customTypePath")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("custom.Base", "custom.Base"),
        (["custom.Base", "mixins.Other"], ["custom.Base", "mixins.Other"]),
    ],
)
def test_json_schema_object_validates_custom_base_path(
    value: object,
    expected: str | list[str] | None,
) -> None:
    """Test schema custom base paths preserve valid scalar, list, and null values."""
    assert JsonSchemaObject.model_validate({"customBasePath": value}).custom_base_path == expected


def test_json_schema_object_rejects_invalid_custom_base_path_list_item() -> None:
    """Test invalid list members report the schema extension name."""
    with pytest.raises(Error, match="customBasePath must be a dotted Python identifier path: 1"):
        JsonSchemaObject.model_validate({"customBasePath": ["custom.Base", 1]})


def test_get_x_python_import_path_handles_empty_and_incomplete_metadata() -> None:
    """Test x-python-import accepts an empty object but rejects partial metadata."""
    parser = JsonSchemaParser("")

    assert parser._get_x_python_import_path({}) is None
    for metadata in ({"module": "os"}, {"name": "PathLike"}):
        with pytest.raises(Error, match="x-python-import requires both module and name"):
            parser._get_x_python_import_path(metadata)
    assert parser._get_x_python_import_path({"module": "os", "name": "PathLike"}) == "os.PathLike"


def test_get_ref_data_type_uses_cached_validated_definition_facts(mocker: MockerFixture) -> None:
    """Use facts from the already validated definition instead of validating the ref target again."""
    parser = JsonSchemaParser(
        json.dumps({
            "type": "object",
            "properties": {"user": {"$ref": "#/$defs/User"}},
            "required": ["user"],
            "$defs": {
                "User": {
                    "type": "object",
                    "nullable": True,
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
        }),
        strict_nullable=True,
    )
    load_ref_schema_object = mocker.spy(parser, "_load_ref_schema_object")

    parser.parse(format_=False)

    load_ref_schema_object.assert_not_called()
    assert parser._ref_data_type_facts["#/$defs/User"] == (None, True)
    assert parser._false_schema_refs is None
    assert "user: Optional[User]" in dump_templates(list(parser.results))


def test_get_ref_data_type_falls_back_when_facts_are_not_cached(mocker: MockerFixture) -> None:
    """Keep the validation fallback for refs that were not parsed and cached first."""
    parser = JsonSchemaParser("")
    parser.raw_obj = {"$defs": {"User": {"type": "object"}}}
    load_ref_schema_object = mocker.spy(parser, "_load_ref_schema_object")

    parser.get_ref_data_type("#/$defs/User")

    load_ref_schema_object.assert_called_once_with("#/$defs/User")


def test_local_ref_false_schema_reuses_validated_facts() -> None:
    """Reuse local false facts only after the standard schema validation completed."""
    parser = JsonSchemaParser("")
    parser.raw_obj = {
        "$defs": {
            "Never": False,
            "Allowed": True,
            "Text": {"type": "string"},
        }
    }

    assert parser._uses_builtin_false_ref_facts()
    assert parser._is_local_ref_false_schema("#/$defs/Never", use_builtin_facts=True)
    for ref in ("#/$defs/Never", "#/$defs/Allowed", "#/$defs/Text"):
        resolved_ref = parser.model_resolver.resolve_ref(ref)
        parser._cache_ref_data_type_facts(resolved_ref, parser._load_ref_schema_object(ref))
    assert parser._false_schema_refs == {"#/$defs/Never"}
    assert parser._false_schema_refs <= parser._ref_data_type_facts.keys()

    parser.raw_obj["$defs"] = {"Never": True, "Allowed": False, "Text": False}
    assert parser._is_local_ref_false_schema("#/$defs/Never", use_builtin_facts=True)
    assert not parser._is_local_ref_false_schema("#/$defs/Allowed", use_builtin_facts=True)
    assert not parser._is_local_ref_false_schema("#/$defs/Text", use_builtin_facts=True)
    assert parser._false_schema_refs is not None
    parser._false_schema_refs.remove("#/$defs/Never")
    assert not parser._is_local_ref_false_schema("#/$defs/Never", use_builtin_facts=True)


def test_local_ref_false_schema_falls_back_when_facts_are_missing() -> None:
    """Keep the validation path when a local ref was not already parsed."""

    class LoaderTrackingParser(JsonSchemaParser):
        loaded_refs: ClassVar[list[str]] = []

        def _load_ref_schema_object(self, ref: str) -> JsonSchemaObject:
            type(self).loaded_refs.append(ref)
            return super()._load_ref_schema_object(ref)

    LoaderTrackingParser.loaded_refs = []
    parser = LoaderTrackingParser("")
    parser.raw_obj = {"$defs": {"Never": False}}

    assert not parser._uses_builtin_false_ref_facts()
    assert parser._is_local_ref_false_schema("#/$defs/Never", use_builtin_facts=False)
    assert LoaderTrackingParser.loaded_refs == ["#/$defs/Never"]


def test_local_ref_false_schema_facts_fall_back_for_custom_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep custom loader, validator, and schema construction hooks observable."""

    class LoaderOverrideParser(JsonSchemaParser):
        loaded_refs: ClassVar[list[str]] = []

        def _load_ref_schema_object(self, ref: str) -> JsonSchemaObject:
            type(self).loaded_refs.append(ref)
            return super()._load_ref_schema_object(ref)

    class ValidatorOverrideParser(JsonSchemaParser):
        validated_paths: ClassVar[list[list[str]]] = []

        def _validate_schema_object(
            self,
            raw: dict[str, YamlValue] | YamlValue,
            path: list[str],
        ) -> JsonSchemaObject:
            type(self).validated_paths.append(path)
            return super()._validate_schema_object(raw, path)

    class RawLoaderOverrideParser(JsonSchemaParser):
        loaded_refs: ClassVar[list[str]] = []

        def _get_ref_raw_schema(self, resolved_ref: str) -> dict[str, YamlValue] | YamlValue:
            type(self).loaded_refs.append(resolved_ref)
            return super()._get_ref_raw_schema(resolved_ref)

    class CustomSchema(JsonSchemaObject):
        constructed: ClassVar[int] = 0

        def model_post_init(self, context: Any, /) -> None:
            type(self).constructed += 1
            super().model_post_init(context)

    class SchemaOverrideParser(JsonSchemaParser):
        SCHEMA_OBJECT_TYPE = CustomSchema

    LoaderOverrideParser.loaded_refs = []
    ValidatorOverrideParser.validated_paths = []
    RawLoaderOverrideParser.loaded_refs = []
    CustomSchema.constructed = 0
    for parser_type in (
        LoaderOverrideParser,
        ValidatorOverrideParser,
        RawLoaderOverrideParser,
        SchemaOverrideParser,
    ):
        parser = parser_type("")
        parser.raw_obj = {"$defs": {"Never": False}}

        assert not parser._uses_builtin_false_ref_facts()
        assert parser._is_local_ref_false_schema("#/$defs/Never", use_builtin_facts=False)

    assert LoaderOverrideParser.loaded_refs == ["#/$defs/Never"]
    assert ValidatorOverrideParser.validated_paths == [["#/$defs/Never"]]
    assert RawLoaderOverrideParser.loaded_refs == ["#/$defs/Never"]
    assert CustomSchema.constructed == 1

    instance_calls: list[str] = []
    parser = JsonSchemaParser("")
    parser.raw_obj = {"$defs": {"Never": False}}

    def instance_loader(ref: str) -> JsonSchemaObject:
        instance_calls.append(ref)
        return JsonSchemaParser._load_ref_schema_object(parser, ref)

    monkeypatch.setattr(parser, "_load_ref_schema_object", instance_loader)

    assert not parser._uses_builtin_false_ref_facts()
    assert parser._is_local_ref_false_schema("#/$defs/Never", use_builtin_facts=False)
    assert instance_calls == ["#/$defs/Never"]


def test_local_ref_false_schema_preserves_custom_fact_cache_output() -> None:
    """Do not let a custom cache transform change false-ref branch selection."""

    class FactCacheOverrideParser(JsonSchemaParser):
        cached_refs: ClassVar[list[str]] = []

        def _cache_ref_data_type_facts(self, resolved_ref: str, obj: JsonSchemaObject) -> None:
            type(self).cached_refs.append(resolved_ref)
            super()._cache_ref_data_type_facts(resolved_ref, obj)
            if self._false_schema_refs is not None:
                self._false_schema_refs.discard(resolved_ref)

    FactCacheOverrideParser.cached_refs = []
    parser = FactCacheOverrideParser(
        json.dumps({
            "title": "Payload",
            "type": "object",
            "properties": {
                "value": {
                    "anyOf": [
                        {"$ref": "#/$defs/Never"},
                        {"type": "string"},
                    ]
                }
            },
            "$defs": {"Never": False},
        })
    )

    parser.parse(format_=False)

    assert not parser._uses_builtin_false_ref_facts()
    assert "value: Optional[str] = None" in dump_templates(list(parser.results))
    assert FactCacheOverrideParser.cached_refs == ["#", "#/$defs/Never", "#/$defs/Never"]


def test_local_ref_false_schema_fast_path_matches_validation_path() -> None:
    """Keep generated output identical to repeated reference validation."""

    class ValidationPathParser(JsonSchemaParser):
        def _uses_builtin_false_ref_facts(self) -> bool:
            return False

    source = (DATA_PATH / "false_reference_fast_path.json").read_text()
    outputs: list[str] = []
    for parser_type in (ValidationPathParser, JsonSchemaParser):
        parser = parser_type(source)
        parser.parse(format_=False)
        outputs.append(dump_templates(list(parser.results)))

    assert outputs[0] == outputs[1]


def test_array_items_constraints_fast_path_falls_back_for_subclasses() -> None:
    """Keep parser and schema subclasses on the conventional constraint path."""

    class ConstraintParser(JsonSchemaParser):
        @classmethod
        def _get_array_max_items_constraints(cls, _obj: JsonSchemaObject) -> list[int]:
            return [2]

    class ConstraintSchema(JsonSchemaObject):
        def model_post_init(self, _context: Any, /) -> None:
            self.items = False

    assert ConstraintParser._get_array_items_constraints(JsonSchemaObject.model_validate({})) == {"maxItems": 2}
    assert JsonSchemaParser._get_array_items_constraints(ConstraintSchema.model_validate({})) == {"maxItems": 0}


def test_array_items_constraints_fast_path_matches_conventional_large_schema() -> None:
    """Keep large-schema output byte-identical to the conventional path."""

    class ConventionalArrayConstraintParser(JsonSchemaParser):
        pass

    source = (DATA_PATH.parent / "performance" / "large_models.json").read_text()
    outputs: list[str] = []
    for parser_type in (ConventionalArrayConstraintParser, JsonSchemaParser):
        parser = parser_type(source)
        parser.parse(format_=False)
        outputs.append(dump_templates(list(parser.results)))

    assert outputs[0] == outputs[1]


def test_resolve_local_ref_path_caches_safe_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid resolving the same local ref path repeatedly after it has passed safety checks."""
    parser = JsonSchemaParser(tmp_path / "schema.json")
    target = tmp_path / "schema.json"
    original_resolve = Path.resolve
    calls: list[Path] = []

    def resolve(path: Path, *args: Any, **kwargs: Any) -> Path:
        calls.append(path)
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)

    parser._resolve_local_ref_path(target, "schema.json")
    first_call_count = len(calls)
    assert first_call_count > 0
    parser._resolve_local_ref_path(target, "schema.json")

    assert len(calls) == first_call_count


def test_json_schema_directory_input_reads_each_source_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test directory input source text is materialized once per parse."""
    user_path, pet_path = _write_simple_json_schemas(tmp_path)
    read_counts = _track_reads(tmp_path, monkeypatch)

    JsonSchemaParser(source=tmp_path).parse(format_=False)

    assert read_counts == {
        pet_path: 1,
        user_path: 1,
    }


def test_json_schema_path_list_input_reads_each_source_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test list input source text is materialized once per parse."""
    user_path, pet_path = _write_simple_json_schemas(tmp_path)
    read_counts = _track_reads(tmp_path, monkeypatch)

    JsonSchemaParser(source=[user_path, pet_path], base_path=tmp_path).parse(format_=False)

    assert read_counts == {
        pet_path: 1,
        user_path: 1,
    }


def test_json_schema_parser_warns_for_non_dict_text_source() -> None:
    """Test non-dict text sources are skipped with a warning."""
    with pytest.warns(UserWarning, match=r"\. is empty or not a dict\. Skipping this file"):
        JsonSchemaParser("[1]").parse(format_=False)


def test_json_schema_parser_load_source_dict_rejects_non_dict_text_source() -> None:
    """Reject non-dict text data before parsing a JSON Schema source."""
    parser = JsonSchemaParser("")

    with pytest.raises(TypeError, match="Expected dict, got list"):
        parser._load_source_dict(Source(path=Path(), text="[1]"))


def test_json_schema_parser_load_source_dict_rejects_non_dict_cached_source() -> None:
    """Reject non-dict parsed data before parsing a JSON Schema source."""
    parser = JsonSchemaParser("")

    with pytest.raises(TypeError, match="Expected dict, got list"):
        parser._load_source_dict(Source(path=Path(), raw_data=[]))


def test_json_schema_iter_local_source_paths_ignores_non_local_source() -> None:
    """Test local source path iteration is empty for non-local source input."""
    assert list(JsonSchemaParser("{}")._iter_local_source_paths()) == []


def test_track_reads_ignores_paths_outside_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test read tracking ignores files outside the temporary schema directory."""
    read_counts = _track_reads(tmp_path, monkeypatch)

    assert Path(__file__).read_text(encoding="utf-8")
    assert read_counts == {}


def _write_simple_json_schemas(tmp_path: Path) -> tuple[Path, Path]:
    user_path = tmp_path / "user.json"
    user_path.write_text(
        json.dumps({
            "title": "User",
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }),
        encoding="utf-8",
    )
    pet_path = tmp_path / "pet.json"
    pet_path.write_text(
        json.dumps({
            "title": "Pet",
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }),
        encoding="utf-8",
    )
    return user_path, pet_path


def _track_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Counter[Path]:
    read_counts: Counter[Path] = Counter()
    original_read_text = Path.read_text

    def read_text(path: Path, *args: Any, **kwargs: Any) -> str:
        if path.parent == tmp_path:
            read_counts[path] += 1
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    return read_counts


def test_json_schema_object_ref_url_json(mocker: MockerFixture) -> None:
    """Test JSON schema object reference with JSON URL."""
    parser = JsonSchemaParser("", allow_remote_refs=True)
    obj = JsonSchemaObject.model_validate({"$ref": "https://example.com/person.schema.json#/definitions/User"})
    mocker.patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))],
    )
    mock_fetch = mocker.patch("datamodel_code_generator.http._HTTPFetchSession.get_response")
    mock_fetch.return_value.status_code = 200
    mock_fetch.return_value.headers = {}
    mock_fetch.return_value.text = json.dumps(
        {
            "$id": "https://example.com/person.schema.json",
            "$schema": "http://json-schema.org/draft-07/schema#",
            "definitions": {
                "User": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                        }
                    },
                }
            },
        },
    )

    parser.parse_ref(obj, ["Model"])
    assert (
        dump_templates(list(parser.results))
        == """class User(BaseModel):
    name: Optional[str] = None"""
    )
    parser.parse_ref(obj, ["Model"])
    mock_fetch.assert_called_once_with(
        _get_http_stack(),
        "https://example.com/person.schema.json",
        headers=None,
        verify=True,
        follow_redirects=False,
        query_parameters=None,
        timeout=30.0,
        pinned_host="example.com",
        pinned_ips=(ip_address("93.184.216.34"),),
    )


def test_json_schema_object_ref_url_yaml(mocker: MockerFixture) -> None:
    """Test JSON schema object reference with YAML URL."""
    parser = JsonSchemaParser("", allow_remote_refs=True)
    obj = JsonSchemaObject.model_validate({"$ref": "https://example.org/schema.yaml#/definitions/User"})
    mocker.patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))],
    )
    mock_fetch = mocker.patch("datamodel_code_generator.http._HTTPFetchSession.get_response")
    mock_fetch.return_value.status_code = 200
    mock_fetch.return_value.headers = {}
    mock_fetch.return_value.text = yaml.safe_dump(json.load((DATA_PATH / "user.json").open()))

    parser.parse_ref(obj, ["User"])
    assert (
        dump_templates(list(parser.results))
        == """class User(BaseModel):
    name: Optional[str] = Field(None, examples=['ken'])
    pets: List[User] = Field(default_factory=list)


class Pet(BaseModel):
    name: Optional[str] = Field(None, examples=['dog', 'cat'])"""
    )
    parser.parse_ref(obj, [])
    mock_fetch.assert_called_once_with(
        _get_http_stack(),
        "https://example.org/schema.yaml",
        headers=None,
        verify=True,
        follow_redirects=False,
        query_parameters=None,
        timeout=30.0,
        pinned_host="example.org",
        pinned_ips=(ip_address("93.184.216.34"),),
    )


def test_json_schema_object_cached_ref_url_yaml(mocker: MockerFixture) -> None:
    """Test JSON schema object cached reference with YAML URL."""
    parser = JsonSchemaParser("", allow_remote_refs=True)

    obj = JsonSchemaObject.model_validate(
        {
            "type": "object",
            "properties": {
                "pet": {"$ref": "https://example.org/schema.yaml#/definitions/Pet"},
                "user": {"$ref": "https://example.org/schema.yaml#/definitions/User"},
            },
        },
    )
    mocker.patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))],
    )
    mock_fetch = mocker.patch("datamodel_code_generator.http._HTTPFetchSession.get_response")
    mock_fetch.return_value.status_code = 200
    mock_fetch.return_value.headers = {}
    mock_fetch.return_value.text = yaml.safe_dump(json.load((DATA_PATH / "user.json").open()))

    parser.parse_ref(obj, [])
    assert (
        dump_templates(list(parser.results))
        == """class Pet(BaseModel):
    name: Optional[str] = Field(None, examples=['dog', 'cat'])


class User(BaseModel):
    name: Optional[str] = Field(None, examples=['ken'])
    pets: List[User] = Field(default_factory=list)"""
    )
    mock_fetch.assert_called_once_with(
        _get_http_stack(),
        "https://example.org/schema.yaml",
        headers=None,
        verify=True,
        follow_redirects=False,
        query_parameters=None,
        timeout=30.0,
        pinned_host="example.org",
        pinned_ips=(ip_address("93.184.216.34"),),
    )


def test_json_schema_ref_url_json(mocker: MockerFixture) -> None:
    """Test JSON schema reference with JSON URL."""
    parser = JsonSchemaParser("", allow_remote_refs=True)
    obj = {
        "type": "object",
        "properties": {"user": {"$ref": "https://example.org/schema.json#/definitions/User"}},
    }
    mocker.patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))],
    )
    mock_fetch = mocker.patch("datamodel_code_generator.http._HTTPFetchSession.get_response")
    mock_fetch.return_value.status_code = 200
    mock_fetch.return_value.headers = {}
    mock_fetch.return_value.text = json.dumps(json.load((DATA_PATH / "user.json").open()))

    parser.parse_raw_obj("Model", obj, ["Model"])
    assert (
        dump_templates(list(parser.results))
        == """class Model(BaseModel):
    user: Optional[User] = None


class User(BaseModel):
    name: Optional[str] = Field(None, examples=['ken'])
    pets: List[User] = Field(default_factory=list)


class Pet(BaseModel):
    name: Optional[str] = Field(None, examples=['dog', 'cat'])"""
    )
    mock_fetch.assert_called_once_with(
        _get_http_stack(),
        "https://example.org/schema.json",
        headers=None,
        verify=True,
        follow_redirects=False,
        query_parameters=None,
        timeout=30.0,
        pinned_host="example.org",
        pinned_ips=(ip_address("93.184.216.34"),),
    )


def test_json_schema_ref_url_from_local_http_path(tmp_path: Path, mocker: MockerFixture) -> None:
    """Test HTTP JSON schema references resolved from a local schema store."""
    schema_store = tmp_path / "schemas"
    local_schema = schema_store / "example.com" / "application" / "package" / "element" / "sub-element.json"
    local_schema.parent.mkdir(parents=True)
    local_schema.write_text(
        json.dumps(
            {
                "$id": "http://example.com/application/package/element/sub-element",
                "title": "SubElement",
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                    },
                },
            },
        ),
        encoding="utf-8",
    )

    parser = JsonSchemaParser("", allow_remote_refs=False, http_local_ref_path=schema_store)
    mock_get = mocker.patch.object(_get_httpx(), "get")

    parser.parse_raw_obj(
        "Model",
        {
            "type": "object",
            "properties": {
                "sub_element": {
                    "$ref": "http://example.com/application/package/element/sub-element",
                },
            },
        },
        ["Model"],
    )

    assert (
        dump_templates(list(parser.results))
        == """class Model(BaseModel):
    sub_element: Optional[SubElement] = None


class SubElement(BaseModel):
    name: Optional[str] = None"""
    )
    mock_get.assert_not_called()


def test_json_schema_ref_url_from_local_http_path_with_extension(tmp_path: Path, mocker: MockerFixture) -> None:
    """Test HTTP JSON schema references with an extension resolved from a local schema store."""
    schema_store = tmp_path / "schemas"
    local_schema = schema_store / "example.com" / "application" / "package" / "element" / "sub-element.json"
    local_schema.parent.mkdir(parents=True)
    local_schema.write_text(
        json.dumps(
            {
                "title": "SubElement",
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                    },
                },
            },
        ),
        encoding="utf-8",
    )

    parser = JsonSchemaParser("", allow_remote_refs=False, http_local_ref_path=schema_store)
    mock_get = mocker.patch.object(_get_httpx(), "get")

    assert parser._get_ref_body_from_url("http://example.com/application/package/element/sub-element.json") == {
        "title": "SubElement",
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
            },
        },
    }
    mock_get.assert_not_called()


@pytest.mark.parametrize(
    "ref",
    [
        "http:///application/package/element/sub-element",
        "http://example.com/application/package/../sub-element",
        "http://example.com/..%5C..%5CWindows%5Cwin.ini",
        "http://example.com/path%2Fwith-slash",
    ],
)
def test_json_schema_ref_url_from_local_http_path_invalid_path(tmp_path: Path, ref: str) -> None:
    """Test invalid local HTTP JSON schema reference paths are rejected."""
    parser = JsonSchemaParser("", allow_remote_refs=False, http_local_ref_path=tmp_path)

    with pytest.raises(Error, match="Unsupported local HTTP \\$ref URL path"):
        parser._get_ref_body_from_url(ref)


def test_json_schema_ref_url_from_local_http_path_missing_file(tmp_path: Path) -> None:
    """Test missing local HTTP JSON schema references show the attempted local paths."""
    parser = JsonSchemaParser("", allow_remote_refs=False, http_local_ref_path=tmp_path)

    with pytest.raises(Error, match=r"\$ref local file not found for http://example.com/schema"):
        parser._get_ref_body_from_url("http://example.com/schema")


def test_json_schema_ref_url_from_local_http_path_ignores_non_http_scheme(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    """Test local HTTP path resolution does not handle non-HTTP URL schemes."""
    parser = JsonSchemaParser("", http_local_ref_path=tmp_path)
    mocker.patch.object(parser, "_get_text_from_url", return_value='{"type": "object"}')
    local_http_path = mocker.patch.object(parser, "_get_ref_body_from_local_http_path")

    assert parser._get_ref_body_from_url("ftp://example.com/schema.json") == {"type": "object"}
    local_http_path.assert_not_called()


def test_json_schema_ref_url_from_local_http_path_symlink_escape(tmp_path: Path) -> None:
    """Test local HTTP JSON schema references cannot escape the schema store through symlinks."""
    schema_store = tmp_path / "schemas"
    local_schema = schema_store / "example.com" / "schema.json"
    local_schema.parent.mkdir(parents=True)
    outside_schema = tmp_path / "outside.json"
    outside_schema.write_text('{"type": "object"}', encoding="utf-8")
    try:
        local_schema.symlink_to(outside_schema)
    except OSError as exc:  # pragma: no cover
        pytest.skip(f"symlink creation is not supported: {exc}")

    parser = JsonSchemaParser("", allow_remote_refs=False, http_local_ref_path=schema_store)

    with pytest.raises(Error, match="Unsupported local HTTP \\$ref URL path"):
        parser._get_ref_body_from_url("http://example.com/schema.json")


@pytest.mark.parametrize(
    ("source_obj", "generated_classes"),
    [
        (
            {
                "$id": "https://example.com/person.schema.json",
                "$schema": "http://json-schema.org/draft-07/schema#",
                "title": "Person",
                "type": "object",
                "properties": {
                    "firstName": {
                        "type": "string",
                        "description": "The person's first name.",
                    },
                    "lastName": {
                        "type": "string",
                        "description": "The person's last name.",
                    },
                    "age": {
                        "description": "Age in years which must be equal to or greater than zero.",
                        "type": "integer",
                        "minimum": 0,
                    },
                },
            },
            """class Person(BaseModel):
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    age: Optional[conint(ge=0)] = None""",
        ),
        (
            {
                "$id": "https://example.com/person.schema.json",
                "$schema": "http://json-schema.org/draft-07/schema#",
                "title": "person-object",
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The person's name.",
                    },
                    "home-address": {
                        "$ref": "#/definitions/home-address",
                        "description": "The person's home address.",
                    },
                },
                "definitions": {
                    "home-address": {
                        "type": "object",
                        "properties": {
                            "street-address": {"type": "string"},
                            "city": {"type": "string"},
                            "state": {"type": "string"},
                        },
                        "required": ["street_address", "city", "state"],
                    }
                },
            },
            """class Person(BaseModel):
    name: Optional[str] = None
    home_address: Optional[HomeAddress] = None""",
        ),
    ],
)
def test_parse_object(source_obj: dict[str, Any], generated_classes: str) -> None:
    """Test parsing JSON schema objects."""
    parser = JsonSchemaParser(
        data_model_field_type=DataModelFieldBase,
        source="",
    )
    parser.parse_object("Person", JsonSchemaObject.model_validate(source_obj), [])
    assert dump_templates(list(parser.results)) == generated_classes


@pytest.mark.parametrize(
    ("source_obj", "generated_classes"),
    [
        (
            {
                "$id": "https://example.com/person.schema.json",
                "$schema": "http://json-schema.org/draft-07/schema#",
                "title": "AnyJson",
                "description": "This field accepts any object",
                "discriminator": "type",
            },
            """class AnyObject(RootModel[Any]):
    root: Any = Field(..., description='This field accepts any object', discriminator='type', title='AnyJson')""",
        )
    ],
)
def test_parse_any_root_object(source_obj: dict[str, Any], generated_classes: str) -> None:
    """Test parsing any root object."""
    parser = JsonSchemaParser("")
    parser.parse_root_type("AnyObject", JsonSchemaObject.model_validate(source_obj), [])
    assert dump_templates(list(parser.results)) == generated_classes


def _root_model_sequence_field(
    data_type: DataType,
    *,
    required: bool = True,
    nullable: bool | None = None,
) -> DataModelFieldBase:
    return DataModelFieldBase(name="root", data_type=data_type, required=required, nullable=nullable)


def _root_model_sequence_type(item_type: DataType | None = None) -> DataType:
    return DataType(is_list=True, data_types=[] if item_type is None else [item_type])


def _root_model(fields: list[DataModelFieldBase] | None = None) -> RootModel:
    return RootModel(fields=fields or [], reference=Reference(name="Pets", path="pets"))


@pytest.mark.parametrize(
    ("parser", "data_model_root", "fields"),
    [
        (
            JsonSchemaParser("", use_root_model_sequence_interface=False),
            _root_model([_root_model_sequence_field(_root_model_sequence_type(DataType(type="str")))]),
            [_root_model_sequence_field(_root_model_sequence_type(DataType(type="str")))],
        ),
        (
            JsonSchemaParser("", use_root_model_sequence_interface=True),
            TypeAlias(
                fields=[_root_model_sequence_field(_root_model_sequence_type(DataType(type="str")))],
                reference=Reference(name="Pets", path="pets"),
            ),
            [_root_model_sequence_field(_root_model_sequence_type(DataType(type="str")))],
        ),
        (
            JsonSchemaParser("", use_root_model_sequence_interface=True),
            _root_model(),
            [],
        ),
    ],
)
def test_apply_root_model_sequence_interface_skips_disabled_alias_and_empty_fields(
    parser: JsonSchemaParser,
    data_model_root: RootModel | TypeAlias,
    fields: list[DataModelFieldBase],
) -> None:
    """Sequence interface helpers are skipped unless the opt-in target is a concrete RootModel with a field."""
    parser._apply_root_model_sequence_interface(data_model_root, fields)

    assert data_model_root.methods == []


def test_apply_root_model_sequence_interface_skips_non_sequence_root() -> None:
    """Sequence interface helpers are skipped for scalar RootModel classes."""
    parser = JsonSchemaParser("", use_root_model_sequence_interface=True)
    root_model = _root_model([_root_model_sequence_field(DataType(type="str"))])

    parser._apply_root_model_sequence_interface(root_model, root_model.fields)

    assert root_model.methods == []


@pytest.mark.parametrize(
    ("required", "nullable"),
    [
        (False, None),
        (True, True),
    ],
)
def test_apply_root_model_sequence_interface_skips_optional_root_field(
    required: bool,
    nullable: bool | None,
) -> None:
    """Sequence interface helpers are skipped when the root field can be None."""
    parser = JsonSchemaParser("", use_root_model_sequence_interface=True)
    root_model = _root_model([
        _root_model_sequence_field(
            _root_model_sequence_type(DataType(type="str")),
            required=required,
            nullable=nullable,
        )
    ])

    parser._apply_root_model_sequence_interface(root_model, root_model.fields)

    assert root_model.methods == []


def test_apply_root_model_sequence_interface_skips_models_without_sequence_method() -> None:
    """The parser only applies helpers to RootModel implementations that expose the method."""
    parser = JsonSchemaParser("", use_root_model_sequence_interface=True)
    base_model = BaseModel(
        fields=[_root_model_sequence_field(_root_model_sequence_type(DataType(type="str")))],
        reference=Reference(name="Pets", path="pets"),
    )

    parser._apply_root_model_sequence_interface(base_model, base_model.fields)

    assert base_model.methods == []


@pytest.mark.parametrize(
    ("data_type", "expected_item_hint", "expected_slice_hint"),
    [
        (_root_model_sequence_type(DataType(type="str")), "str", "List[str]"),
        (_root_model_sequence_type(), "Any", "List[Any]"),
        (_root_model_sequence_type(DataType()), "Any", "List[Any]"),
        (
            DataType(is_list=True, data_types=[DataType(type="str"), DataType(type="int")]),
            "Union[str, int]",
            "List[Union[str, int]]",
        ),
        (DataType(is_sequence=True, data_types=[DataType(type="str")]), "str", "Sequence[str]"),
    ],
)
def test_apply_root_model_sequence_interface_adds_sequence_helpers(
    data_type: DataType,
    expected_item_hint: str,
    expected_slice_hint: str,
) -> None:
    """Sequence interface helpers use the wrapped item hint, falling back to Any when needed."""
    parser = JsonSchemaParser("", use_root_model_sequence_interface=True)
    root_model = _root_model([_root_model_sequence_field(data_type)])

    parser._apply_root_model_sequence_interface(root_model, root_model.fields)

    rendered = root_model.render()
    assert root_model.methods == []
    assert root_model._internal_template_data["sequence_base_class"] == f"Sequence[{expected_item_hint}]"
    assert root_model._internal_template_data["sequence_item_type"] == expected_item_hint
    assert root_model._internal_template_data["sequence_slice_type"] == expected_slice_hint
    assert f", Sequence[{expected_item_hint}]):" in rendered.splitlines()[0]
    assert f"def __iter__(self) -> Iterator[{expected_item_hint}]" in rendered
    assert f"def __getitem__(self, index: SupportsIndex) -> {expected_item_hint}" in rendered
    assert f"def __getitem__(self, index: slice) -> {expected_slice_hint}" in rendered
    assert "def __getitem__(self, index: SupportsIndex | slice)" in rendered
    assert "def __len__(self) -> int" in rendered


@pytest.mark.parametrize(
    ("data_type", "expected_type"),
    [
        (DataType(is_optional=True), None),
        (DataType(type="str"), None),
        (DataType(data_types=[DataType(type="str", is_optional=True)]), None),
        (DataType(data_types=[DataType(type="str")]), None),
        (_root_model_sequence_type(DataType(type="str")), "List[str]"),
        (DataType(is_sequence=True, data_types=[DataType(type="str")]), "Sequence[str]"),
        (DataType(data_types=[_root_model_sequence_type(DataType(type="str"))]), "List[str]"),
    ],
)
def test_get_root_model_sequence_type(data_type: DataType, expected_type: str | None) -> None:
    """The parser recognizes only non-optional list and sequence RootModel types."""
    parser = JsonSchemaParser("")

    result = parser._get_root_model_sequence_type(data_type)

    assert (result.type_hint if result is not None else None) == expected_type


def test_infer_union_variant_names_uses_discriminator_literals() -> None:
    """Infer variant names from discriminator literals without mutating schemas."""
    parser = JsonSchemaParser("", infer_union_variant_names=True)
    parent = _json_schema_object({"discriminator": {"propertyName": "kind"}})
    variants = [
        _json_schema_object({"properties": {"kind": {"const": ""}}}),
        _json_schema_object({"properties": {"kind": {"enum": ["ready"]}}}),
    ]

    assert parser._infer_union_variant_names("pkg.Event", parent, variants) == [None, "pkg.Event_ready"]


def test_infer_union_variant_names_distinguishes_literal_types() -> None:
    """Use type-aware names for non-string literal tags."""
    parser = JsonSchemaParser("", infer_union_variant_names=True)
    parent = _json_schema_object({"discriminator": {"propertyName": "kind"}})
    variants = [
        _json_schema_object({"properties": {"kind": {"const": 1}}}),
        _json_schema_object({"properties": {"kind": {"const": "1"}}}),
        _json_schema_object({"properties": {"kind": {"const": True}}}),
    ]

    assert parser._infer_union_variant_names("Event", parent, variants) == [
        "Event_int_1",
        "Event__1",
        "Event_bool_true",
    ]
    assert _get_union_variant_name("Event", "") is None


def test_infer_union_variant_names_skips_generated_name_collisions() -> None:
    """Try the next literal field when generated variant names collide."""
    parser = JsonSchemaParser("", infer_union_variant_names=True)
    parent = _json_schema_object({"discriminator": {"propertyName": "kind"}})
    variants = [
        _json_schema_object({"properties": {"kind": {"const": 1}, "fallback": {"const": "created"}}}),
        _json_schema_object({"properties": {"kind": {"const": "int_1"}, "fallback": {"const": "updated"}}}),
    ]

    assert parser._infer_union_variant_names("Event", parent, variants) == ["Event_created", "Event_updated"]


def test_union_variant_literal_helpers_handle_refs_and_invalid_fields(tmp_path: Path, mocker: MockerFixture) -> None:
    """Literal collection rejects ambiguous branches and resolves simple refs."""
    parser = JsonSchemaParser("", infer_union_variant_names=True)
    ref = "#/$defs/Kind"
    parser.raw_obj = {"$defs": {"Kind": {"const": "from_ref"}}}
    external_schema = tmp_path / "external.json"
    external_parser = JsonSchemaParser(
        "",
        external_ref_mapping={str(external_schema): "external.models"},
        infer_union_variant_names=True,
    )
    load_ref = mocker.patch.object(external_parser, "_load_ref_schema_object")

    assert parser._get_single_literal_value(_json_schema_object({"$ref": ref})) == "from_ref"
    assert (
        external_parser._get_single_literal_value(_json_schema_object({"$ref": f"{external_schema}#/External"})) is None
    )
    load_ref.assert_not_called()
    assert (
        parser._get_single_literal_value(
            _json_schema_object({"$ref": ref}),
            {parser.model_resolver.resolve_ref(ref)},
        )
        is None
    )
    assert parser._get_single_literal_value(_json_schema_object({"type": "string"})) is None
    assert (
        parser._get_union_variant_literal_values(
            [
                _json_schema_object({}),
                _json_schema_object({"properties": {"kind": {"const": "only"}}}),
            ],
            "kind",
        )
        is None
    )
    assert (
        parser._get_union_variant_literal_values(
            [
                _json_schema_object({"properties": {"kind": True}}),
                _json_schema_object({"properties": {"kind": {"const": "ready"}}}),
            ],
            "kind",
        )
        is None
    )
    assert (
        parser._get_union_variant_literal_values(
            [
                _json_schema_object({"properties": {"kind": {"type": "string"}}}),
                _json_schema_object({"properties": {"kind": {"const": "ready"}}}),
            ],
            "kind",
        )
        is None
    )


def test_iter_union_variant_literal_field_names_skips_duplicates() -> None:
    """Field name scanning prefers discriminator names and keeps fallbacks stable."""
    parser = JsonSchemaParser("", infer_union_variant_names=True)

    assert list(parser._iter_union_variant_literal_field_names(_json_schema_object({"discriminator": "kind"}), [])) == [
        "kind"
    ]
    assert list(
        parser._iter_union_variant_literal_field_names(
            _json_schema_object({}),
            [
                _json_schema_object({}),
                _json_schema_object({"properties": {"kind": {"const": "a"}, "reason": {"const": "x"}}}),
                _json_schema_object({"properties": {"kind": {"const": "b"}}}),
            ],
        )
    ) == ["kind", "reason"]


def test_infer_union_variant_names_returns_none_when_no_literal_field_matches() -> None:
    """Keep default generated names when no field has unique literal values."""
    parser = JsonSchemaParser("", infer_union_variant_names=True)
    variants = [
        _json_schema_object({"properties": {"kind": {"type": "string"}}}),
        _json_schema_object({"properties": {"kind": {"const": "ready"}}}),
    ]

    assert parser._infer_union_variant_names("Event", _json_schema_object({}), variants) is None


def test_infer_union_variant_names_disabled() -> None:
    """Leave default variant naming unchanged unless explicitly enabled."""
    parser = JsonSchemaParser("")
    variants = [
        _json_schema_object({"properties": {"kind": {"const": "created"}}}),
        _json_schema_object({"properties": {"kind": {"const": "deleted"}}}),
    ]

    assert parser._get_inferred_union_variant_names("Event", _json_schema_object({}), variants) is None


@pytest.mark.parametrize(
    ("source_obj", "generated_classes"),
    [
        (
            yaml.safe_load((DATA_PATH / "oneof.json").read_text()),
            (DATA_PATH / "oneof.json.snapshot").read_text(),
        )
    ],
)
def test_parse_one_of_object(source_obj: dict[str, Any], generated_classes: str) -> None:
    """Test parsing oneOf schema objects."""
    parser = JsonSchemaParser("")
    parser.parse_raw_obj("onOfObject", source_obj, [])
    assert dump_templates(list(parser.results)) == generated_classes


@pytest.mark.parametrize(
    ("source_obj", "generated_classes"),
    [
        (
            {
                "$id": "https://example.com/person.schema.json",
                "$schema": "http://json-schema.org/draft-07/schema#",
                "title": "defaults",
                "type": "object",
                "properties": {
                    "string": {
                        "type": "string",
                        "default": "default string",
                    },
                    "string_on_field": {
                        "type": "string",
                        "default": "default string",
                        "description": "description",
                    },
                    "number": {"type": "number", "default": 123},
                    "number_on_field": {
                        "type": "number",
                        "default": 123,
                        "description": "description",
                    },
                    "number_array": {"type": "array", "default": [1, 2, 3]},
                    "string_array": {"type": "array", "default": ["a", "b", "c"]},
                    "object": {"type": "object", "default": {"key": "value"}},
                },
            },
            """class Defaults(BaseModel):
    string: Optional[str] = 'default string'
    string_on_field: Optional[str] = Field('default string', description='description')
    number: Optional[float] = 123
    number_on_field: Optional[float] = Field(123, description='description')
    number_array: Optional[List[Any]] = [1, 2, 3]
    string_array: Optional[List[Any]] = ['a', 'b', 'c']
    object: Optional[Dict[str, Any]] = {'key': 'value'}""",
        )
    ],
)
def test_parse_default(source_obj: dict[str, Any], generated_classes: str) -> None:
    """Test parsing default values in schemas."""
    parser = JsonSchemaParser("")
    parser.parse_raw_obj("Defaults", source_obj, [])
    assert dump_templates(list(parser.results)) == generated_classes


def test_parse_array_schema() -> None:
    """Test parsing array schemas."""
    parser = JsonSchemaParser("")
    parser.parse_raw_obj("schema", {"type": "object", "properties": {"name": True}}, [])
    assert (
        dump_templates(list(parser.results))
        == """class Schema(BaseModel):
    name: Optional[Any] = None"""
    )


def test_parse_nested_array(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test parsing nested array schemas."""
    monkeypatch.chdir(tmp_path)
    parser = JsonSchemaParser(
        DATA_PATH / "nested_array.json",
        data_model_field_type=DataModelFieldBase,
    )
    parser.parse()
    assert_output(dump_templates(list(parser.results)), DATA_PATH / "nested_array.json.snapshot")


@pytest.mark.parametrize(
    ("schema_type", "schema_format", "result_type", "from_", "import_", "use_pendulum"),
    [
        ("integer", "int32", "int", None, None, False),
        ("integer", "int64", "int", None, None, False),
        ("integer", "date-time", "AwareDatetime", "pydantic", "AwareDatetime", False),
        ("integer", "date-time", "AwareDatetime", "pydantic", "AwareDatetime", True),
        ("integer", "unix-time", "int", None, None, False),
        ("number", "float", "float", None, None, False),
        ("number", "double", "float", None, None, False),
        ("number", "time", "time", "datetime", "time", False),
        ("number", "time", "Time", "pendulum", "Time", True),
        ("number", "date-time", "AwareDatetime", "pydantic", "AwareDatetime", False),
        ("number", "date-time", "AwareDatetime", "pydantic", "AwareDatetime", True),
        ("string", None, "str", None, None, False),
        ("string", "byte", "Base64Str", "pydantic", "Base64Str", False),
        ("string", "binary", "bytes", None, None, False),
        ("boolean", None, "bool", None, None, False),
        ("string", "date", "date", "datetime", "date", False),
        ("string", "date", "Date", "pendulum", "Date", True),
        ("string", "date-time", "AwareDatetime", "pydantic", "AwareDatetime", False),
        ("string", "date-time", "AwareDatetime", "pydantic", "AwareDatetime", True),
        ("string", "duration", "timedelta", "datetime", "timedelta", False),
        ("string", "duration", "Duration", "pendulum", "Duration", True),
        ("number", "time-delta", "timedelta", "datetime", "timedelta", False),
        ("number", "time-delta", "Duration", "pendulum", "Duration", True),
        ("string", "path", "Path", "pathlib", "Path", False),
        ("string", "password", "SecretStr", "pydantic", "SecretStr", False),
        ("string", "email", "EmailStr", "pydantic", "EmailStr", False),
        ("string", "uri", "AnyUrl", "pydantic", "AnyUrl", False),
        ("string", "uri-reference", "str", None, None, False),
        ("string", "uuid", "UUID", "uuid", "UUID", False),
        ("string", "uuid1", "UUID1", "pydantic", "UUID1", False),
        ("string", "uuid2", "UUID", "uuid", "UUID", False),
        ("string", "uuid3", "UUID3", "pydantic", "UUID3", False),
        ("string", "uuid4", "UUID4", "pydantic", "UUID4", False),
        ("string", "uuid5", "UUID5", "pydantic", "UUID5", False),
        ("string", "ulid", "ULID", "ulid", "ULID", False),
        ("string", "ipv4", "IPv4Address", "ipaddress", "IPv4Address", False),
        ("string", "ipv6", "IPv6Address", "ipaddress", "IPv6Address", False),
        ("string", "unknown-type", "str", None, None, False),
    ],
)
def test_get_data_type(
    schema_type: str,
    schema_format: str,
    result_type: str,
    from_: str | None,
    import_: str | None,
    use_pendulum: bool,
) -> None:
    """Test data type resolution from schema type and format."""
    if from_ and import_:
        import_: Import | None = Import(from_=from_, import_=import_)
    else:
        import_ = None

    parser = JsonSchemaParser("", use_pendulum=use_pendulum)
    assert (
        parser.get_data_type(JsonSchemaObject(type=schema_type, format=schema_format)).model_dump()
        == DataType(type=result_type, import_=import_).model_dump()
    )


@pytest.mark.parametrize(
    ("schema_types", "result_types"),
    [
        (["integer", "number"], ["int", "float"]),
        (["integer", "null"], ["int"]),
    ],
)
def test_get_data_type_array(schema_types: list[str], result_types: list[str]) -> None:
    """Test data type resolution for array of types."""
    parser = JsonSchemaParser("")
    assert (
        parser.get_data_type(JsonSchemaObject(type=schema_types)).model_dump()
        == parser.data_type(
            data_types=[
                parser.data_type(
                    type=r,
                )
                for r in result_types
            ],
            is_optional="null" in schema_types,
        ).model_dump()
    )


@pytest.mark.allow_direct_assert
def test_array_union_constraint_alias_uses_custom_get_data_type() -> None:
    """Keep custom data-type parser hooks active while rendering internal aliases."""
    model_types = get_data_model_types(
        DataModelType.PydanticV2BaseModel,
        target_python_version=PythonVersion.PY_310,
    )
    observed_schemas: list[JsonSchemaObject] = []

    class CustomParser(JsonSchemaParser):
        def get_data_type(self, obj: JsonSchemaObject) -> DataType:
            observed_schemas.append(obj)
            return super().get_data_type(obj)

    parser = CustomParser(
        "{}",
        data_model_type=model_types.data_model,
        data_model_root_type=model_types.root_model,
        data_model_field_type=model_types.field_model,
        data_type_manager_type=model_types.data_type_manager,
        target_python_version=PythonVersion.PY_310,
    )
    schema = JsonSchemaObject.model_validate({
        "type": ["array", "string"],
        "minItems": 1,
        "minLength": 2,
        "items": {"type": "string"},
    })

    parser.parse_array_fields("Value", schema, ["value"])

    assert any(item.type == "string" and item.minLength == 2 for item in observed_schemas)
    assert parser.field_constraints is False


def test_additional_imports() -> None:
    """Test that additional imports are inside imports container."""
    new_parser = JsonSchemaParser(source="", additional_imports=["collections.deque"])
    assert len(new_parser.imports) == 1
    assert new_parser.imports["collections"] == {"deque"}


def test_additional_imports_reject_invalid_import_path() -> None:
    """Reject direct parser configuration that could inject generated statements."""
    with pytest.raises(Error, match="additional_imports must be a Python import path composed of identifiers"):
        JsonSchemaParser(source="", additional_imports=["collections.deque\nINJECTION_MARKER = 1"])


def test_additional_imports_reject_non_string_after_config_mutation() -> None:
    """Keep the package error contract when a validated parser config is mutated."""
    config = JSONSchemaParserConfig()
    config.additional_imports = [1]  # ty: ignore[invalid-assignment]

    with pytest.raises(
        Error,
        match=r"additional_imports must be a Python import path composed of identifiers: 1",
    ):
        JsonSchemaParser(source="", config=config)


def test_no_additional_imports() -> None:
    """Test that not additional imports are not affecting imports container."""
    new_parser = JsonSchemaParser(
        source="",
    )
    assert len(new_parser.imports) == 0


def test_class_decorators() -> None:
    """Test that class decorators are stored in parser."""
    new_parser = JsonSchemaParser(source="", class_decorators=["@dataclass_json"])
    assert new_parser.class_decorators == ["@dataclass_json"]


def test_class_decorators_multiple() -> None:
    """Test that multiple class decorators are stored in parser."""
    new_parser = JsonSchemaParser(source="", class_decorators=["@dataclass_json", "@my_decorator"])
    assert new_parser.class_decorators == ["@dataclass_json", "@my_decorator"]


def test_no_class_decorators() -> None:
    """Test that no class decorators results in empty list."""
    new_parser = JsonSchemaParser(source="")
    assert new_parser.class_decorators == []


@pytest.mark.parametrize(
    ("source_obj", "generated_classes"),
    [
        (
            {
                "$id": "https://example.com/person.schema.json",
                "$schema": "http://json-schema.org/draft-07/schema#",
                "title": "Person",
                "type": "object",
                "properties": {
                    "firstName": {
                        "type": "string",
                        "description": "The person's first name.",
                        "alt_type": "integer",
                    },
                    "lastName": {
                        "type": "string",
                        "description": "The person's last name.",
                        "alt_type": "integer",
                    },
                    "age": {
                        "description": "Age in years which must be equal to or greater than zero.",
                        "type": "integer",
                        "minimum": 0,
                        "alt_type": "number",
                    },
                    "real_age": {
                        "description": "Age in years which must be equal to or greater than zero.",
                        "type": "integer",
                        "minimum": 0,
                    },
                },
            },
            """class Person(BaseModel):
    firstName: Optional[int] = None
    lastName: Optional[int] = None
    age: Optional[confloat(ge=0.0)] = None
    real_age: Optional[conint(ge=0)] = None""",
        ),
    ],
)
@pytest.mark.skipif(pydantic.VERSION < "2.0.0", reason="Require Pydantic version 2.0.0 or later ")
def test_json_schema_parser_extension(source_obj: dict[str, Any], generated_classes: str) -> None:
    """Test JSON schema parser extension with alt_type support."""

    class AltJsonSchemaObject(JsonSchemaObject):
        properties: Optional[dict[str, Union[AltJsonSchemaObject, bool]]] = None  # noqa: UP007, UP045
        alt_type: Optional[str] = None  # noqa: UP045

        def model_post_init(self, context: Any) -> None:  # noqa: ARG002
            if self.alt_type:
                self.type = self.alt_type

    class AltJsonSchemaParser(JsonSchemaParser):
        SCHEMA_OBJECT_TYPE = AltJsonSchemaObject

    parser = AltJsonSchemaParser(
        data_model_field_type=DataModelFieldBase,
        source="",
    )
    parser.parse_object("Person", AltJsonSchemaObject.model_validate(source_obj), [])
    assert dump_templates(list(parser.results)) == generated_classes


def test_json_schema_parser_schema_raw_key_cache_is_schema_object_specific() -> None:
    """Test schema raw key caches are keyed by the exact schema object type."""

    class AltJsonSchemaObject(JsonSchemaObject):
        properties: Optional[dict[str, Union[AltJsonSchemaObject, bool]]] = None  # noqa: UP007, UP045
        alt_type: Optional[str] = pydantic.Field(default=None, alias="altType")  # noqa: UP045

    class AltJsonSchemaParser(JsonSchemaParser):
        SCHEMA_OBJECT_TYPE = AltJsonSchemaObject

    base_parser = JsonSchemaParser("")
    alt_parser = AltJsonSchemaParser("")

    assert "altType" not in base_parser._known_schema_object_raw_keys()
    assert "altType" in alt_parser._known_schema_object_raw_keys()
    assert base_parser._has_schema_affecting_keywords({"altType": "string"}) is False
    assert alt_parser._has_schema_affecting_keywords({"altType": "string"}) is True
    assert alt_parser._known_schema_object_raw_keys() is alt_parser._known_schema_object_raw_keys()


def test_create_data_model_with_frozen_dataclasses() -> None:
    """Test _create_data_model when frozen_dataclasses attribute exists."""
    parser = JsonSchemaParser(
        "",
        data_model_type=DataClass,
        data_model_root_type=DataClass,
    )
    parser.frozen_dataclasses = True

    field = DataModelFieldBase(name="test_field", data_type=DataType(type="str"), required=True)

    result = parser._create_data_model(
        reference=Reference(name="TestModel", path="test_model"),
        fields=[field],
    )

    assert isinstance(result, DataClass)
    assert result.name == "TestModel"


def test_create_data_model_with_keyword_only() -> None:
    """Test _create_data_model when keyword_only attribute exists."""
    parser = JsonSchemaParser(
        "",
        data_model_type=DataClass,
        data_model_root_type=DataClass,
    )
    parser.keyword_only = True

    field = DataModelFieldBase(name="test_field", data_type=DataType(type="str"), required=True)

    result = parser._create_data_model(
        reference=Reference(name="TestModel", path="test_model"),
        fields=[field],
    )

    assert isinstance(result, DataClass)
    assert result.name == "TestModel"


def test_create_data_model_with_both_frozen_and_keyword_only() -> None:
    """Test _create_data_model when both frozen_dataclasses and keyword_only exist."""
    parser = JsonSchemaParser(
        "",
        data_model_type=DataClass,
        data_model_root_type=DataClass,
    )
    parser.frozen_dataclasses = True
    parser.keyword_only = True

    field = DataModelFieldBase(name="test_field", data_type=DataType(type="str"), required=True)

    result = parser._create_data_model(
        reference=Reference(name="TestModel", path="test_model"),
        fields=[field],
    )

    assert isinstance(result, DataClass)
    assert result.name == "TestModel"


def test_create_data_model_with_existing_dataclass_arguments() -> None:
    """Test _create_data_model when existing dataclass_arguments are provided in kwargs."""
    parser = JsonSchemaParser(
        "",
        data_model_type=DataClass,
        data_model_root_type=DataClass,
    )
    parser.frozen_dataclasses = True
    parser.keyword_only = True

    field = DataModelFieldBase(name="test_field", data_type=DataType(type="str"), required=True)

    result = parser._create_data_model(
        reference=Reference(name="TestModel", path="test_model"),
        fields=[field],
        dataclass_arguments={"slots": True, "order": True},
    )

    assert isinstance(result, DataClass)
    assert result.name == "TestModel"


def test_create_data_model_without_existing_dataclass_arguments() -> None:
    """Test _create_data_model when no existing dataclass_arguments (else branch)."""
    parser = JsonSchemaParser(
        "",
        data_model_type=DataClass,
        data_model_root_type=DataClass,
    )
    parser.frozen_dataclasses = False
    parser.keyword_only = False

    field = DataModelFieldBase(name="test_field", data_type=DataType(type="str"), required=True)

    result = parser._create_data_model(
        reference=Reference(name="TestModel", path="test_model"),
        fields=[field],
    )

    assert isinstance(result, DataClass)
    assert result.name == "TestModel"


def test_create_data_model_frozen_and_keyword_only_cleanup() -> None:
    """Test that frozen and keyword_only are popped from kwargs when existing args present."""
    parser = JsonSchemaParser(
        "",
        data_model_type=DataClass,
        data_model_root_type=DataClass,
    )
    parser.frozen_dataclasses = True
    parser.keyword_only = True

    field = DataModelFieldBase(name="test_field", data_type=DataType(type="str"), required=True)

    result = parser._create_data_model(
        reference=Reference(name="TestModel", path="test_model"),
        fields=[field],
        dataclass_arguments={"slots": True},
        frozen=False,
        keyword_only=False,
    )

    assert isinstance(result, DataClass)
    assert result.name == "TestModel"


def test_create_data_model_with_complex_existing_arguments() -> None:
    """Test _create_data_model with complex existing dataclass_arguments that get merged."""
    parser = JsonSchemaParser(
        "",
        data_model_type=DataClass,
        data_model_root_type=DataClass,
    )
    parser.frozen_dataclasses = True
    parser.keyword_only = True

    field = DataModelFieldBase(name="test_field", data_type=DataType(type="str"), required=True)

    result = parser._create_data_model(
        reference=Reference(name="TestModel", path="test_model"),
        fields=[field],
        dataclass_arguments={
            "slots": True,
            "order": True,
            "unsafe_hash": False,
            "match_args": True,
        },
    )

    assert isinstance(result, DataClass)
    assert result.name == "TestModel"


def test_create_data_model_none_dataclass_arguments() -> None:
    """Test _create_data_model when dataclass_arguments is explicitly None."""
    parser = JsonSchemaParser(
        "",
        data_model_type=DataClass,
        data_model_root_type=DataClass,
    )
    parser.frozen_dataclasses = True
    parser.keyword_only = True

    field = DataModelFieldBase(name="test_field", data_type=DataType(type="str"), required=True)

    result = parser._create_data_model(
        reference=Reference(name="TestModel", path="test_model"),
        fields=[field],
        dataclass_arguments=None,
    )

    assert isinstance(result, DataClass)
    assert result.name == "TestModel"


def test_create_data_model_non_dataclass_with_dataclass_arguments() -> None:
    """Test _create_data_model removes dataclass_arguments for non-DataClass models."""
    parser = JsonSchemaParser(
        "",
        data_model_type=BaseModel,
        data_model_root_type=BaseModel,
    )

    field = DataModelFieldBase(name="test_field", data_type=DataType(type="str"), required=True)

    # Pass dataclass_arguments even though model is not DataClass - should be removed
    result = parser._create_data_model(
        reference=Reference(name="TestModel", path="test_model"),
        fields=[field],
        dataclass_arguments={"frozen": True},
    )

    assert isinstance(result, BaseModel)
    assert result.name == "TestModel"


def test_parse_type_mappings_invalid_format() -> None:
    """Test _parse_type_mappings raises ValueError for invalid format."""
    with pytest.raises(ValueError, match="Invalid type mapping format"):
        Parser._parse_type_mappings(["invalid_without_equals"])


def test_parse_type_mappings_valid_formats() -> None:
    """Test _parse_type_mappings with valid formats."""
    result = Parser._parse_type_mappings(["binary=string", "string+date=string"])
    assert result == {
        ("string", "binary"): "string",
        ("string", "date"): "string",
    }


def test_get_type_with_mappings_to_format() -> None:
    """Test _get_type_with_mappings mapping to a format within type_formats."""
    parser = JsonSchemaParser(
        source="",
        type_mappings=["binary=byte"],
    )
    result = parser._get_type_with_mappings("string", "binary")
    assert result == Types.byte


def test_get_type_with_mappings_to_type_default() -> None:
    """Test _get_type_with_mappings mapping to a top-level type's default."""
    parser = JsonSchemaParser(
        source="",
        type_mappings=["binary=boolean"],
    )
    result = parser._get_type_with_mappings("string", "binary")
    assert result == Types.boolean


def test_get_type_with_mappings_unknown_target_fallback() -> None:
    """Test _get_type_with_mappings falls back to _get_type for unknown target."""
    parser = JsonSchemaParser(
        source="",
        type_mappings=["binary=unknown_format"],
    )
    result = parser._get_type_with_mappings("string", "binary")
    assert result == Types.binary


@pytest.mark.parametrize(
    ("frozen_dataclasses", "keyword_only", "parser_dataclass_args", "kwargs_dataclass_args", "expected"),
    [
        (False, False, None, None, {}),
        (True, False, None, None, {"frozen": True}),
        (False, True, None, None, {"kw_only": True}),
        (True, True, None, None, {"frozen": True, "kw_only": True}),
        (False, False, {"slots": True}, None, {"slots": True}),
        (True, True, {"slots": True}, None, {"slots": True}),
        (True, True, {"slots": True}, {"order": True}, {"order": True}),
    ],
)
def test_create_data_model_dataclass_arguments(
    frozen_dataclasses: bool,
    keyword_only: bool,
    parser_dataclass_args: dict | None,
    kwargs_dataclass_args: dict | None,
    expected: dict,
) -> None:
    """Test _create_data_model handles dataclass_arguments correctly."""
    parser = JsonSchemaParser(
        source="",
        data_model_type=DataClass,
        frozen_dataclasses=frozen_dataclasses,
        keyword_only=keyword_only,
    )
    parser.dataclass_arguments = parser_dataclass_args

    reference = Reference(path="test", original_name="Test", name="Test")
    kwargs: dict[str, Any] = {"reference": reference, "fields": []}
    if kwargs_dataclass_args is not None:
        kwargs["dataclass_arguments"] = kwargs_dataclass_args
    result = parser._create_data_model(**kwargs)
    assert isinstance(result, DataClass)
    assert result.dataclass_arguments == expected


def test_get_ref_body_from_url_file_unc_path(mocker: MockerFixture) -> None:
    """Test _get_ref_body_from_url handles UNC file:// URLs correctly."""
    parser = JsonSchemaParser("", allow_remote_refs=True)
    mock_load = mocker.patch(
        "datamodel_code_generator.parser.jsonschema.load_data_from_path",
        return_value={"type": "object"},
    )

    result = parser._get_ref_body_from_url("file://server/share/schemas/pet.json")

    assert result == {"type": "object"}
    mock_load.assert_called_once()
    called_path = mock_load.call_args[0][0]
    # On Windows, UNC paths have \\server\share\ as a single "drive" part
    # On POSIX, they're separate: /, server, share, schemas, pet.json
    path_str = str(called_path)
    assert "server" in path_str
    assert "share" in path_str
    assert called_path.parts[-2:] == ("schemas", "pet.json")


def test_get_ref_body_from_url_file_local_path(mocker: MockerFixture) -> None:
    """Test _get_ref_body_from_url handles local file:// URLs (no netloc)."""
    parser = JsonSchemaParser("", allow_remote_refs=True)
    mock_load = mocker.patch(
        "datamodel_code_generator.parser.jsonschema.load_data_from_path",
        return_value={"type": "string"},
    )

    result = parser._get_ref_body_from_url("file:///home/user/schemas/pet.json")

    assert result == {"type": "string"}
    mock_load.assert_called_once()
    called_path = mock_load.call_args[0][0]
    assert called_path.parts[-4:] == ("home", "user", "schemas", "pet.json")


def test_merge_ref_with_schema_no_ref() -> None:
    """Test _merge_ref_with_schema returns object unchanged when no $ref is present."""
    parser = JsonSchemaParser("")
    obj = JsonSchemaObject.model_validate({"type": "string", "minLength": 5})
    result = parser._merge_ref_with_schema(obj)
    assert result is obj


def test_has_ref_with_schema_keywords_extras_with_schema_affecting_keys() -> None:
    """Test has_ref_with_schema_keywords when extras contains schema-affecting keys."""
    # const is stored in extras and is schema-affecting
    obj = JsonSchemaObject.model_validate(
        {
            "$ref": "#/$defs/Base",
            "const": "active",
        },
    )
    # Verify extras contains schema-affecting key
    assert obj.extras
    assert "const" in obj.extras
    assert obj.has_ref_with_schema_keywords is True


def test_has_ref_with_schema_keywords_extras_with_metadata_only_keys() -> None:
    """Test has_ref_with_schema_keywords when extras contains only metadata keys."""
    # $comment is metadata-only, should not trigger merge
    obj = JsonSchemaObject.model_validate(
        {
            "$ref": "#/$defs/Base",
            "$comment": "this is a comment",
        },
    )
    # Verify extras contains only metadata key
    assert obj.extras
    assert "$comment" in obj.extras
    assert obj.has_ref_with_schema_keywords is False


def test_has_ref_with_schema_keywords_extras_with_extension_keys() -> None:
    """Test has_ref_with_schema_keywords when extras contains only x-* extension keys.

    OpenAPI/JSON Schema extension fields (x-*) should be treated as metadata
    and not trigger schema merging, which prevents infinite recursion with
    self-referencing schemas.
    """
    # x-* extensions are vendor extensions, should not trigger merge
    obj = JsonSchemaObject.model_validate(
        {
            "$ref": "#/$defs/Base",
            "deprecated": False,  # metadata-only field
            "x-internalAPI": False,  # extension field
            "x-custom-field": "value",  # another extension field
        },
    )
    # Verify extras contains extension keys
    assert obj.extras
    assert "x-internalAPI" in obj.extras
    assert "x-custom-field" in obj.extras
    # Extension fields should NOT trigger schema merge
    assert obj.has_ref_with_schema_keywords is False


def test_has_ref_with_schema_keywords_no_extras() -> None:
    """Test has_ref_with_schema_keywords when extras is empty."""
    # Only $ref and a schema-affecting field, no extras
    obj = JsonSchemaObject.model_validate(
        {
            "$ref": "#/$defs/Base",
            "minLength": 10,
        },
    )
    # Verify extras is empty but minLength triggers merge
    assert not obj.extras
    assert obj.has_ref_with_schema_keywords is True


def test_parse_combined_schema_anyof_with_ref_and_schema_keywords() -> None:
    """Test parse_combined_schema merges $ref with schema-affecting keywords in anyOf."""
    parser = JsonSchemaParser("")
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {
            "value": {
                "anyOf": [
                    {
                        "$ref": "#/$defs/BaseString",
                        "minLength": 10,
                    },
                    {
                        "type": "integer",
                    },
                ]
            }
        },
        "$defs": {
            "BaseString": {
                "type": "string",
                "maxLength": 100,
            }
        },
    }
    parser.parse_raw_obj("Model", schema, [])
    results = list(parser.results)
    assert len(results) >= 1


def test_parse_enum_empty_enum_not_nullable() -> None:
    """Test parse_enum returns null type when enum_fields is empty and not nullable."""
    parser = JsonSchemaParser("")
    obj = JsonSchemaObject.model_validate({"type": "integer", "enum": []})
    result = parser.parse_enum("EmptyEnum", obj, ["EmptyEnum"])
    assert result.type == "None"


def test_parse_enum_preserves_explicit_null_member() -> None:
    """Keep a native JSON null member distinct from an unset field default."""
    from datamodel_code_generator.model.enum import NULL_ENUM_MEMBER_VALUE, Enum

    parser = JsonSchemaParser("")
    parser.parse_enum("Mixed", JsonSchemaObject.model_validate({"enum": [None, "None"]}), ["Mixed"])

    enum = next(model for model in parser.results if isinstance(model, Enum))
    assert enum.fields[0].default is NULL_ENUM_MEMBER_VALUE
    assert enum.find_member(None).field is enum.fields[0]  # ty: ignore[union-attr]
    assert enum.find_member("None").field is enum.fields[1]  # ty: ignore[union-attr]


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "array", "items": {"type": "string"}}, False),
        ({"allOf": [{"type": "string"}]}, False),
        ({"oneOf": [{"type": "string"}]}, False),
        ({"anyOf": [{"type": "string"}]}, False),
        ({"properties": {"name": {"type": "string"}}}, False),
        ({"patternProperties": {".*": {"type": "string"}}}, False),
        ({"type": "object"}, False),
        ({"enum": ["a", "b"]}, False),
        ({"type": "string"}, True),
        ({"type": "string", "minLength": 1}, True),
    ],
)
def test_is_root_model_schema(schema: dict[str, Any], expected: bool) -> None:
    """Test _is_root_model_schema returns correct value for various schema types."""
    parser = JsonSchemaParser("")
    obj = JsonSchemaObject.model_validate(schema)
    assert parser._is_root_model_schema(obj) is expected


def test_merge_primitive_schemas_for_allof_single_item() -> None:
    """Test _merge_primitive_schemas_for_allof returns unchanged item when single."""
    parser = JsonSchemaParser("")
    item = JsonSchemaObject.model_validate({"type": "string", "minLength": 1})
    result = parser._merge_primitive_schemas_for_allof([item])
    assert result == item


def test_merge_primitive_schemas_for_allof_nomerge_mode() -> None:
    """Test _merge_primitive_schemas_for_allof overwrites constraints in NoMerge mode."""
    parser = JsonSchemaParser("")
    parser.allof_merge_mode = AllOfMergeMode.NoMerge
    items = [
        JsonSchemaObject.model_validate({"type": "string", "pattern": "^a.*"}),
        JsonSchemaObject.model_validate({"minLength": 5}),
    ]
    result = parser._merge_primitive_schemas_for_allof(items)
    assert result.pattern == "^a.*"
    assert result.minLength == 5


def test_merge_primitive_schemas_for_allof_nomerge_mode_with_format() -> None:
    """Test _merge_primitive_schemas_for_allof handles format in NoMerge mode."""
    parser = JsonSchemaParser("")
    parser.allof_merge_mode = AllOfMergeMode.NoMerge
    items = [
        JsonSchemaObject.model_validate({"type": "string"}),
        JsonSchemaObject.model_validate({"format": "email"}),
    ]
    result = parser._merge_primitive_schemas_for_allof(items)
    assert result.format == "email"


def test_merge_primitive_schemas_for_allof_constraints_mode_with_format() -> None:
    """Test _merge_primitive_schemas_for_allof handles format in Constraints mode."""
    parser = JsonSchemaParser("")
    parser.allof_merge_mode = AllOfMergeMode.Constraints
    items = [
        JsonSchemaObject.model_validate({"type": "string", "pattern": "^a.*"}),
        JsonSchemaObject.model_validate({"format": "email"}),
    ]
    result = parser._merge_primitive_schemas_for_allof(items)
    assert result.format == "email"


def test_handle_allof_root_model_special_path_marker() -> None:
    """Test _handle_allof_root_model_with_constraints returns None for special path."""
    parser = JsonSchemaParser("")
    obj = JsonSchemaObject.model_validate(
        {
            "allOf": [
                {"$ref": "#/definitions/Base"},
                {"minLength": 1},
            ]
        },
    )
    path = [f"test{SPECIAL_PATH_MARKER}inline"]
    result = parser._handle_allof_root_model_with_constraints("Test", obj, path)
    assert result is None


def test_handle_allof_root_model_multiple_refs() -> None:
    """Test _handle_allof_root_model_with_constraints returns None for multiple refs."""
    parser = JsonSchemaParser("")
    obj = JsonSchemaObject.model_validate(
        {
            "allOf": [
                {"$ref": "#/definitions/Base1"},
                {"$ref": "#/definitions/Base2"},
            ]
        },
    )
    result = parser._handle_allof_root_model_with_constraints("Test", obj, ["test"])
    assert result is None


def test_handle_allof_root_model_no_refs() -> None:
    """Test _handle_allof_root_model_with_constraints returns None when no refs."""
    parser = JsonSchemaParser("")
    obj = JsonSchemaObject.model_validate(
        {
            "allOf": [
                {"type": "string"},
                {"minLength": 1},
            ]
        },
    )
    result = parser._handle_allof_root_model_with_constraints("Test", obj, ["test"])
    assert result is None


def test_handle_allof_root_model_no_constraint_items() -> None:
    """Test _handle_allof_root_model_with_constraints returns None when no constraints."""
    parser = JsonSchemaParser("")
    parser._load_ref_schema_object = lambda _ref: JsonSchemaObject.model_validate({"type": "string"})
    obj = JsonSchemaObject.model_validate(
        {
            "allOf": [
                {"$ref": "#/definitions/Base"},
            ]
        },
    )
    result = parser._handle_allof_root_model_with_constraints("Test", obj, ["test"])
    assert result is None


def test_handle_allof_root_model_constraint_with_properties() -> None:
    """Test _handle_allof_root_model_with_constraints returns None when constraint has properties."""
    parser = JsonSchemaParser("")
    parser._load_ref_schema_object = lambda _ref: JsonSchemaObject.model_validate({"type": "string"})
    obj = JsonSchemaObject.model_validate(
        {
            "allOf": [
                {"$ref": "#/definitions/Base"},
                {"properties": {"name": {"type": "string"}}},
            ]
        },
    )
    result = parser._handle_allof_root_model_with_constraints("Test", obj, ["test"])
    assert result is None


def test_handle_allof_root_model_constraint_with_items() -> None:
    """Test _handle_allof_root_model_with_constraints returns None when constraint has items."""
    parser = JsonSchemaParser("")
    parser._load_ref_schema_object = lambda _ref: JsonSchemaObject.model_validate({"type": "string"})
    obj = JsonSchemaObject.model_validate(
        {
            "allOf": [
                {"$ref": "#/definitions/Base"},
                {"items": {"type": "string"}},
            ]
        },
    )
    result = parser._handle_allof_root_model_with_constraints("Test", obj, ["test"])
    assert result is None


def test_handle_allof_root_model_incompatible_types() -> None:
    """Test _handle_allof_root_model_with_constraints returns None for incompatible types."""
    parser = JsonSchemaParser("")
    parser._load_ref_schema_object = lambda _ref: JsonSchemaObject.model_validate({"type": "string"})
    obj = JsonSchemaObject.model_validate(
        {
            "allOf": [
                {"$ref": "#/definitions/Base"},
                {"type": "boolean"},
            ]
        },
    )
    result = parser._handle_allof_root_model_with_constraints("Test", obj, ["test"])
    assert result is None


def test_handle_allof_root_model_ref_to_non_root() -> None:
    """Test _handle_allof_root_model_with_constraints returns None when ref is not root model."""
    parser = JsonSchemaParser("")
    parser._load_ref_schema_object = lambda _ref: JsonSchemaObject.model_validate(
        {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
        },
    )
    obj = JsonSchemaObject.model_validate(
        {
            "allOf": [
                {"$ref": "#/definitions/Base"},
                {"minLength": 1},
            ]
        },
    )
    result = parser._handle_allof_root_model_with_constraints("Test", obj, ["test"])
    assert result is None


def test_timestamp_with_time_zone_format() -> None:
    """Test that PostgreSQL timestamp with time zone format maps to datetime."""
    from datamodel_code_generator.parser.jsonschema import json_schema_data_formats

    # Verify the format is mapped correctly
    assert json_schema_data_formats["string"]["timestamp with time zone"] == Types.date_time


@pytest.mark.parametrize(
    "format_",
    [
        "idn-hostname",
        "iri",
        "iri-reference",
        "uri-template",
        "json-pointer",
        "relative-json-pointer",
        "regex",
    ],
)
def test_json_schema_standard_string_formats_map_to_string(format_: str) -> None:
    """Test standard JSON Schema string formats without dedicated Python types."""
    from datamodel_code_generator.parser.jsonschema import json_schema_data_formats

    assert json_schema_data_formats["string"][format_] == Types.string


@pytest.mark.parametrize(
    ("x_python_type", "expected"),
    [
        # Direct matches for special container types
        ("Set[str]", {"is_set": True}),
        ("set[int]", {"is_set": True}),
        ("FrozenSet[int]", {"is_frozen_set": True}),
        ("frozenset[str]", {"is_frozen_set": True}),
        ("Sequence[str]", {"is_sequence": True}),
        ("MutableSequence[int]", {"is_sequence": True}),
        ("Mapping[str, int]", {"is_mapping": True}),
        ("MutableMapping[str, int]", {"is_mapping": True}),
        ("AbstractSet[str]", {"is_frozen_set": True}),
        ("MutableSet[int]", {"is_set": True}),
        # Union with special container type
        ("Union[Set[str], None]", {"is_set": True}),
        ("Optional[FrozenSet[int]]", {"is_frozen_set": True}),
        ("Set[int] | None", {"is_set": True}),
        ("Set[int]|None", {"is_set": True}),
        ("None|Set[int]", {"is_set": True}),
        ("Sequence[str] | int", {"is_sequence": True}),
        # Union without special container type (loop completes without match)
        ("Union[str, int]", {}),
        ("str | int", {}),
        ("Optional[str]", {}),
        ("Union[str, int, float]", {}),
        ("Union[List[str], None]", {}),  # List is not a special container
        ("Optional[Dict[str, int]]", {}),  # Dict is not a special container
        # Non-special container types
        ("List[str]", {}),
        ("Dict[str, int]", {}),
        ("Literal[-1]", {}),
        ("str", {}),
        ("int", {}),
        ("CustomType", {}),
    ],
)
def test_get_python_type_flags(x_python_type: str, expected: dict[str, bool]) -> None:
    """Test _get_python_type_flags extracts collection flags correctly."""
    parser = JsonSchemaParser("")
    obj = JsonSchemaObject.model_validate({"x-python-type": x_python_type})
    result = parser._get_python_type_flags(obj)
    assert result == expected


def test_bind_python_type_carries_shared_structure_and_ordered_imports() -> None:
    """One parsed expression drives rendering, imports, and copied DataTypes."""
    parser = JsonSchemaParser("")
    obj = JsonSchemaObject.model_validate({
        "type": "string",
        "x-python-type": "Optional[Callable[[foo.Bar, foo.Bar, Literal['foo.Bar']], baz.Qux]]",
    })

    data_type = parser._get_python_type_override(obj)

    assert data_type is not None
    assert data_type.type == "Optional[Callable[[Bar, Bar, Literal['foo.Bar']], Qux]]"
    assert data_type.python_type is not None
    assert not hasattr(data_type.python_type, "rendered")
    assert tuple(data_type.python_type.imports) == (
        Import.from_full_path("typing.Optional"),
        Import.from_full_path("collections.abc.Callable"),
        Import.from_full_path("foo.Bar"),
        Import.from_full_path("typing.Literal"),
        Import.from_full_path("baz.Qux"),
    )
    assert deepcopy(data_type).python_type is data_type.python_type
    assert DataModelFieldBase._has_explicit_typing_import_requirements(data_type)

    aliased_type = DataType(type=data_type.type, alias="alias.Model", python_type=data_type.python_type)
    model = BaseModel(
        reference=Reference(path="structured", name="Structured"),
        fields=[
            DataModelFieldBase(name="value", data_type=data_type),
            DataModelFieldBase(name="aliased", data_type=aliased_type),
        ],
    )
    assert {"Callable", "Bar", "Literal", "Qux", "alias"} <= Parser._collect_used_names_from_models([model])


def test_bind_python_type_preserves_direct_runtime_symbol_identity() -> None:
    """Runtime symbols bind their exact dotted module without a text round trip."""
    bound_type = JsonSchemaParser("")._bind_python_type(PythonTypeRuntimeSymbol("pkg.models", ("Outer", "Inner")))

    assert bound_type.expression == PythonTypeRuntimeSymbol("pkg.models", ("Outer", "Inner"))
    assert bound_type.imports == (Import(import_="pkg.models"),)


def test_inherited_request_response_helpers_keep_unnamed_root_fields() -> None:
    """Temporary root fields stay ordered while nameless override entries are ignored."""
    parser = JsonSchemaParser("")
    reference = Reference(path="#/$defs/MappingBase", name="MappingBase")
    root_field = DataModelFieldBase(data_type=DataType(type="str"))
    parser._inherited_schema_cache[reference.path] = JsonSchemaObject.model_validate({})
    parser._raw_inherited_fields_cache[reference.path] = (root_field,)
    parser._raw_inherited_own_names_cache[reference.path] = frozenset()

    inherited_fields = parser._collect_inherited_fields_for_request_response([reference])

    assert len(inherited_fields) == 1
    assert inherited_fields[0].name is None
    assert parser._merge_inherited_field_overrides(inherited_fields, [root_field]) == []


@pytest.mark.parametrize("gc_initially_enabled", [True, False])
def test_clear_inherited_field_caches_releases_deferred_list_wrapper_cycles(
    *,
    gc_initially_enabled: bool,
) -> None:
    """Temporary deferred container trees are released immediately without cyclic GC."""
    gc_state_setters = (gc.disable, gc.enable)
    restore_gc = gc_state_setters[gc.isenabled()]
    gc_state_setters[gc_initially_enabled]()
    parser = JsonSchemaParser("")
    field = DataModelFieldBase(
        name="items",
        original_name="items",
        data_type=DataType(
            data_types=[
                DataType(
                    data_types=[DataType(type="str")],
                    is_list=True,
                )
            ]
        ),
    )
    modifiers = _get_inherited_type_modifiers(field.data_type)
    assert modifiers.list_wrapper is not None
    field.__dict__[_DEFERRED_INHERITED_TYPE_KEY] = modifiers
    parser._raw_inherited_fields_cache["#/$defs/Cycle"] = (field,)
    schema = JsonSchemaObject.model_validate({"type": "object"})
    parser._inherited_schema_cache["#/$defs/Cycle"] = schema
    parser._inherited_schema_ancestor_cache["#/$defs/Cycle"] = frozenset({"#/$defs/Base"})
    parser._inherited_schema_linearization_cache["#/$defs/Cycle",] = (
        "#/$defs/Cycle",
        "#/$defs/Base",
    )
    parser._inherited_required_cache["#/$defs/Cycle",] = frozenset({"items"})
    resolution = (schema, "#/$defs/Cycle", frozenset(), False)
    parser._inherited_parent_property_cache[id(schema), "#/$defs/Cycle", frozenset(), False] = (schema, resolution)
    tracked = [
        field,
        *field.data_type.all_data_types,
        modifiers.list_wrapper,
        *modifiers.list_wrapper.all_data_types,
        schema,
    ]
    weak_references = [weakref.ref(value) for value in tracked]
    gc.disable()
    try:
        parser._clear_inherited_field_caches()
        assert not parser._raw_inherited_fields_cache
        assert not parser._inherited_schema_cache
        assert not parser._inherited_schema_ancestor_cache
        assert not parser._inherited_schema_linearization_cache
        assert not parser._inherited_required_cache
        assert not parser._inherited_parent_property_cache
        del field, modifiers, resolution, schema, tracked

        assert all(reference() is None for reference in weak_references)
    finally:
        restore_gc()


def test_inherited_parent_property_cache_is_context_scoped() -> None:
    """Pure references reuse only identical recursive and relative-ref contexts."""
    parser = JsonSchemaParser("")
    parser.raw_obj = {
        "$defs": {
            "Target": {"type": "string"},
        }
    }
    pure_ref = JsonSchemaObject.model_validate({"$ref": "#/$defs/Target"})

    first = parser._resolve_inherited_parent_property(
        pure_ref,
        "#/$defs/Parent",
    )
    repeated = parser._resolve_inherited_parent_property(
        JsonSchemaObject.model_validate({"$ref": "#/$defs/Target"}),
        "#/$defs/Parent",
    )
    recursive = parser._resolve_inherited_parent_property(
        pure_ref,
        "#/$defs/Parent",
        frozenset({"#/$defs/Target"}),
    )
    resolved_context = parser._resolve_inherited_parent_property(
        pure_ref,
        "#/$defs/Parent",
        refs_resolved=True,
    )
    relative_ref = JsonSchemaObject.model_validate({
        "$ref": "./types.json#/$defs/Target",
    })
    first_parent_ref = f"{DATA_PATH / 'first' / 'parent.json'}#/$defs/Parent"
    second_parent_ref = f"{DATA_PATH / 'second' / 'parent.json'}#/$defs/Parent"
    with parser._inherited_ref_context(first_parent_ref):
        first_resolved_ref = parser.model_resolver.resolve_ref(relative_ref.ref)
    with parser._inherited_ref_context(second_parent_ref):
        second_resolved_ref = parser.model_resolver.resolve_ref(relative_ref.ref)
    first_relative = parser._resolve_inherited_parent_property(
        relative_ref,
        first_parent_ref,
        frozenset({first_resolved_ref}),
    )
    second_relative = parser._resolve_inherited_parent_property(
        relative_ref,
        second_parent_ref,
        frozenset({second_resolved_ref}),
    )

    assert repeated is first
    assert recursive is not first
    assert resolved_context is not first
    assert first_relative is not second_relative
    assert len(parser._inherited_parent_property_cache) == 5
    assert all(
        cached_source is pure_ref or cached_source is relative_ref
        for cached_source, _ in parser._inherited_parent_property_cache.values()
    )


def test_inherited_parent_property_cache_bypasses_mutable_sibling_refs() -> None:
    """Identity entries anchor inline schemas while changed sibling refs bypass stale values."""
    parser = JsonSchemaParser("")
    parser.raw_obj = {
        "$defs": {
            "Target": {"type": "string"},
        }
    }
    pure_ref = JsonSchemaObject.model_validate({"$ref": "#/$defs/Target"})
    first = parser._resolve_inherited_parent_property(
        pure_ref,
        "#/$defs/Parent",
    )
    sibling_ref = JsonSchemaObject.model_validate({
        "$ref": "#/$defs/Target",
        "minLength": 2,
    })
    sibling = parser._resolve_inherited_parent_property(
        sibling_ref,
        "#/$defs/Parent",
    )
    resolved_sibling = parser._resolve_inherited_parent_property(
        sibling_ref,
        "#/$defs/Parent",
        refs_resolved=True,
    )
    inline_schema = JsonSchemaObject.model_validate({"type": "integer"})
    inline = parser._resolve_inherited_parent_property(
        inline_schema,
        "#/$defs/Parent",
    )
    repeated_inline = parser._resolve_inherited_parent_property(
        inline_schema,
        "#/$defs/Parent",
    )
    inline_cache_key = (id(inline_schema), "#/$defs/Parent", frozenset(), False)
    inline_schema.ref = "#/$defs/Target"
    mutated_inline = parser._resolve_inherited_parent_property(
        inline_schema,
        "#/$defs/Parent",
    )

    assert sibling is not first
    assert sibling[0].minLength == 2
    assert resolved_sibling[0].minLength == 2
    assert resolved_sibling[3]
    assert repeated_inline is inline
    assert mutated_inline is not inline
    assert parser._inherited_parent_property_cache[inline_cache_key][0] is inline_schema
    assert len(parser._inherited_parent_property_cache) == 2


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, "boolean"),
        (1, "integer"),
        ("value", "string"),
        (1.5, "number"),
        (["value"], "array"),
        ({"value": 1}, "object"),
        (None, "null"),
        (object(), ""),
    ],
)
def test_inherited_json_value_type_matrix(value: object, expected: str) -> None:
    """Concrete enum and const values retain every JSON instance type."""
    assert _get_json_value_type(value) == expected


def test_inherited_schema_type_inference_matrix() -> None:
    """Untyped structural, enum, const, and allOf parents expose their effective types."""
    parser = JsonSchemaParser("")
    inferred_types = {
        "implicit_object": parser._get_inherited_schema_types(
            JsonSchemaObject.model_validate({
                "properties": {"value": {"type": "string"}},
            }),
            "#/$defs/ImplicitObject",
        ),
        "const": parser._get_inherited_schema_types(
            JsonSchemaObject.model_validate({"const": True}),
            "#/$defs/Const",
        ),
        "float_enum": parser._get_inherited_schema_types(
            JsonSchemaObject.model_validate({"enum": [1.5]}),
            "#/$defs/FloatEnum",
        ),
        "array_enum": parser._get_inherited_schema_types(
            JsonSchemaObject.model_validate({"enum": [["value"]]}),
            "#/$defs/ArrayEnum",
        ),
        "object_enum": parser._get_inherited_schema_types(
            JsonSchemaObject.model_validate({"enum": [{"value": 1}]}),
            "#/$defs/ObjectEnum",
        ),
        "null_enum": parser._get_inherited_schema_types(
            JsonSchemaObject.model_validate({"enum": [None]}),
            "#/$defs/NullEnum",
        ),
        "unsupported_enum": parser._get_inherited_schema_types(
            JsonSchemaObject.model_validate({"enum": [object()]}),
            "#/$defs/UnsupportedEnum",
        ),
        "all_of": parser._get_inherited_schema_types(
            JsonSchemaObject.model_validate({
                "allOf": [
                    True,
                    {},
                    {"type": "string"},
                    {"type": "string"},
                ]
            }),
            "#/$defs/AllOf",
        ),
    }

    assert inferred_types == {
        "implicit_object": frozenset({"object"}),
        "const": frozenset({"boolean"}),
        "float_enum": frozenset({"number"}),
        "array_enum": frozenset({"array"}),
        "object_enum": frozenset({"object"}),
        "null_enum": frozenset({"null"}),
        "unsupported_enum": frozenset(),
        "all_of": frozenset({"string"}),
    }


def test_inherited_nested_schema_merge_matrix() -> None:
    """Boolean, positional, mapping, and scalar nested schemas use intersection semantics."""
    parser = JsonSchemaParser("")

    assert parser._is_list_with_any_item_type(
        DataType(
            is_list=True,
            data_types=[
                DataType(
                    data_types=[
                        DataType(
                            is_list=True,
                            data_types=[DataType(type=ANY)],
                        )
                    ]
                )
            ],
        )
    )
    assert parser._is_list_with_any_item_type(
        DataType(
            data_types=[
                DataType(
                    is_list=True,
                    data_types=[DataType(type=ANY)],
                )
            ]
        )
    )
    assert not parser._is_list_with_any_item_type(
        DataType(
            is_list=True,
            data_types=[
                DataType(
                    data_types=[DataType(type="str")],
                )
            ],
        )
    )
    assert parser._merge_inherited_nested_schemas(False, {"type": "string"}) is False
    assert parser._merge_inherited_nested_schemas({"type": "string"}, False) is False
    assert parser._merge_inherited_nested_schemas(True, {"type": "string"}) == {"type": "string"}
    assert parser._merge_inherited_nested_schemas({"type": "string"}, True) == {"type": "string"}
    assert parser._merge_inherited_nested_schemas(
        {"type": "string", "minLength": 1},
        {"maxLength": 3},
    ) == {
        "type": "string",
        "minLength": 1,
        "maxLength": 3,
    }
    assert parser._merge_inherited_nested_schemas("parent", "child") == "child"
    assert parser._merge_inherited_schema_keyword(
        "prefixItems",
        [{"type": "string"}],
        [{"maxLength": 3}, {"minimum": 0}],
        {"items": {"type": "integer"}},
    ) == [
        {"type": "string", "maxLength": 3},
        {"type": "integer", "minimum": 0},
    ]
    assert parser._merge_property_schemas(
        {"nested": {"type": "string"}},
        {"nested": {"$ref": "#/$defs/Nested"}},
    ) == {"nested": {"$ref": "#/$defs/Nested"}}
    assert parser._merge_property_schemas(
        {"nested": {"minimum": 1}},
        {"nested": {"maximum": 2}},
    ) == {"nested": {"minimum": 1, "maximum": 2}}


def test_inherited_distributed_constraint_matrix() -> None:
    """Container extension constraints are selected, retained, or dropped without leaking metadata."""
    parser = JsonSchemaParser("")
    extra_key = JsonSchemaObject.__extra_key__

    assert (
        parser._select_inherited_distributed_shape(
            {extra_key: {"x-metadata": "keep"}},
            frozenset({extra_key}),
            frozenset({"array"}),
        )
        == {}
    )
    assert parser._remove_inherited_distributed_shape(
        {
            "minItems": 1,
            extra_key: {
                "contains": {"type": "string"},
                "x-metadata": "keep",
            },
        },
        frozenset({"minItems", extra_key}),
    ) == {extra_key: {"x-metadata": "keep"}}
    assert (
        parser._drop_incompatible_inherited_constraints(
            {"minLength": 1},
            frozenset({"integer"}),
        )
        == {}
    )
    assert parser._drop_incompatible_inherited_constraints(
        {
            extra_key: {
                "contains": {"type": "string"},
                "x-metadata": "keep",
            }
        },
        frozenset({"string"}),
    ) == {extra_key: {"x-metadata": "keep"}}
    assert (
        parser._drop_incompatible_inherited_constraints(
            {extra_key: {"contains": {"type": "string"}}},
            frozenset(),
        )
        == {}
    )
    assert parser._drop_incompatible_inherited_constraints(
        {
            "minLength": 1,
            "maxLength": 2,
            extra_key: {
                "contains": {"type": "string"},
                "x-metadata": "keep",
            },
        },
        frozenset({"integer"}),
    ) == {extra_key: {"x-metadata": "keep"}}


def test_inherited_constraint_composition_normalizes_nested_boolean_values() -> None:
    """Nested list and mapping schemas retain booleans while flattening constraint-only branches."""
    parser = JsonSchemaParser("")
    source = JsonSchemaObject.model_validate({
        "prefixItems": [
            True,
            {"allOf": [{"minLength": 2}]},
        ],
        "properties": {
            "neutral": True,
            "constrained": {"allOf": [{"maxLength": 4}]},
        },
    })

    normalized = parser._normalize_inherited_constraint_compositions(source)

    assert normalized is not source
    assert normalized.model_dump(exclude_unset=True, by_alias=True) == {
        "prefixItems": [
            True,
            {"minLength": 2},
        ],
        "properties": {
            "neutral": True,
            "constrained": {"maxLength": 4},
        },
    }
    assert (
        parser._get_flattenable_inherited_constraint_items(
            "anyOf",
            [{"type": "string"}],
        )
        is None
    )


def test_inherited_type_shape_branch_matrix() -> None:
    """No-merge materialization handles booleans, null unions, scalars, and incompatible replacements."""
    parser = JsonSchemaParser("", allof_merge_mode=AllOfMergeMode.NoMerge)

    assert parser._is_inherited_type_narrowing(
        frozenset({"string"}),
        frozenset(),
    )
    assert not parser._is_partial_inherited_nested_value(
        1,
        JsonSchemaObject.model_validate({"type": "string"}),
    )
    assert (
        parser._merge_inherited_type_shape_value(
            False,
            {"type": "string"},
            "#/$defs/Parent",
            frozenset(),
        )
        is False
    )
    assert (
        parser._merge_inherited_type_shape_value(
            {"type": "string"},
            False,
            "#/$defs/Parent",
            frozenset(),
        )
        is False
    )
    assert parser._merge_inherited_type_shape_value(
        {"type": "string"},
        True,
        "#/$defs/Parent",
        frozenset(),
    ) == {"type": "string"}
    assert (
        parser._merge_inherited_type_shape_value(
            "parent",
            "child",
            "#/$defs/Parent",
            frozenset(),
        )
        == "child"
    )
    assert parser._merge_inherited_union_branch(
        {"type": ["string", "null"]},
        {"minLength": 1},
        frozenset({"minLength"}),
        ("#/$defs/Parent", frozenset()),
        excludes_null=True,
    ) == {
        "type": "string",
        "minLength": 1,
        "nullable": False,
    }
    assert parser._merge_inherited_type_shape_dict(
        {"allOf": [{"type": "string"}]},
        {"minLength": 1},
        "#/$defs/Parent",
        parent_refs_resolved=True,
    ) == {
        "type": "string",
        "minLength": 1,
    }
    assert parser._merge_inherited_type_shape_dict(
        {"enum": ["value"]},
        {"minLength": 1},
        "#/$defs/Parent",
        parent_refs_resolved=True,
    ) == {
        "type": "string",
        "minLength": 1,
    }
    assert parser._merge_inherited_type_shape_dict(
        {"type": ["null"]},
        {"nullable": False},
        "#/$defs/Parent",
        parent_refs_resolved=True,
    ) == {
        "type": ["null"],
        "nullable": False,
    }
    inherited_union = {
        "anyOf": [
            {"type": "string"},
            {"type": "integer"},
        ]
    }
    assert (
        parser._merge_inherited_type_shape_dict(
            inherited_union,
            {},
            "#/$defs/Parent",
            parent_refs_resolved=True,
        )
        == inherited_union
    )
    materialized = JsonSchemaObject.model_validate({
        "properties": {
            "typed": {"type": "string"},
            "boolean": True,
        }
    })
    parser._mark_inherited_materialized_type_shapes(materialized)
    assert materialized.__dict__[_INHERITED_MATERIALIZED_TYPE_SHAPE_KEY]
    assert materialized.properties is not None
    typed_property = materialized.properties["typed"]
    assert isinstance(typed_property, JsonSchemaObject)
    assert typed_property.__dict__[_INHERITED_MATERIALIZED_TYPE_SHAPE_KEY]
    assert materialized.properties["boolean"] is True
    incompatible_child = JsonSchemaObject.model_validate({"type": "integer"})
    assert (
        parser._merge_no_merge_inherited_property(
            JsonSchemaObject.model_validate({"type": "string"}),
            incompatible_child,
            "#/$defs/Parent",
        )
        is incompatible_child
    )


def test_inherited_deferred_and_boolean_sanitization_fallbacks() -> None:
    """Deferred boolean properties and already-shaped schemas bypass destructive normalization."""
    parser = JsonSchemaParser("", allof_merge_mode=AllOfMergeMode.NoMerge)
    source = JsonSchemaObject.model_validate({
        "type": "object",
        "properties": {"value": True},
    })

    assert parser._get_deferred_inherited_parse_object(source, frozenset({"value"})) is source
    assert (
        parser._sanitize_untyped_boolean_inherited_property(
            JsonSchemaObject.model_validate({
                "anyOf": [{"type": "string"}],
                "minLength": 1,
            })
        )
        is None
    )
    assert (
        parser._sanitize_untyped_boolean_inherited_property(
            JsonSchemaObject.model_validate({"description": "metadata only"})
        )
        is None
    )


def test_inherited_field_type_uses_typed_grandparent_fallback() -> None:
    """An untyped direct declaration falls back to the first typed ancestor."""
    parser = JsonSchemaParser("")
    parser.raw_obj = {
        "$defs": {
            "Grandparent": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
            },
            "Parent": {
                "allOf": [{"$ref": "#/$defs/Grandparent"}],
                "type": "object",
                "properties": {"value": {}},
            },
        }
    }
    parent = parser.model_resolver.add_ref("#/$defs/Parent", resolved=True)

    inherited_type = parser._get_inherited_field_type("value", [parent])

    assert inherited_type is not None
    assert inherited_type.type == "str"


def test_inherited_request_response_override_resolves_derived_default() -> None:
    """Request/response field copies resolve mutable defaults in derived class scope."""
    parser = JsonSchemaParser(
        "",
        allof_merge_mode=AllOfMergeMode.All,
        default_value_overrides={"Derived.value": {"source": ["derived"]}},
    )
    inherited_field = DataModelFieldBase(
        name="value",
        original_name="value",
        data_type=DataType(type="str"),
        default="schema",
        has_default=True,
    )
    inherited_field.__dict__[_RAW_SCHEMA_DEFAULT_KEY] = "schema"
    child_field = DataModelFieldBase(
        name="value",
        original_name="value",
        data_type=DataType(),
    )
    child_field.__dict__[_DEFERRED_INHERITED_TYPE_KEY] = child_field.data_type
    child_field.__dict__[_DEFERRED_INHERITED_CLASS_KEY] = "Derived"

    fields = parser._merge_inherited_field_overrides([inherited_field], [child_field])
    fields[0].default["source"].append("copy")

    assert fields[0].default == {"source": ["derived", "copy"]}
    assert parser.model_resolver.default_value_overrides["Derived.value"] == {
        "source": ["derived"],
    }


def test_inherited_partial_schema_shape_helpers() -> None:
    """Partial allOf detection handles boolean, object, array, and composition shapes."""
    parser = JsonSchemaParser("")
    parent_object = JsonSchemaObject.model_validate({"type": "object"})
    parent_array = JsonSchemaObject.model_validate({"type": "array", "items": {"type": "string"}})

    assert not parser._is_unconstrained_inherited_schema(False)
    assert not parser._is_unconstrained_inherited_schema({"minLength": 3})
    assert parser._remove_unconstrained_compositions(
        {"allOf": [{}, {"type": "string"}]},
    ) == {"allOf": [{"type": "string"}]}
    assert parser._remove_unconstrained_compositions({"oneOf": [{}]}) == {}
    assert parser._remove_unconstrained_compositions({"allOf": [{}, True]}) == {}
    assert parser._is_partial_inherited_property(
        JsonSchemaObject.model_validate({"additionalProperties": True}),
        parent_object,
    )
    assert parser._is_partial_inherited_property(
        JsonSchemaObject.model_validate({"items": None}),
        parent_array,
    )
    deferred_names = parser._get_deferred_inherited_property_names(
        JsonSchemaObject.model_validate({
            "properties": {
                "boolean_parent": True,
                "object_parent": True,
                "false_parent": False,
            }
        }),
        {
            "boolean_parent": (True, "#/$defs/BooleanParent"),
            "object_parent": (
                JsonSchemaObject.model_validate({"type": "string"}),
                "#/$defs/ObjectParent",
            ),
            "false_parent": (
                JsonSchemaObject.model_validate({"type": "string"}),
                "#/$defs/FalseParent",
            ),
        },
    )

    assert deferred_names == frozenset({"object_parent"})
    assert parser._get_flattenable_inherited_constraint_items("allOf", [None]) is None


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        pytest.param(
            {"allOf": [{"minLength": 3}]},
            {"minLength": 3},
            id="all-of-singleton",
        ),
        pytest.param(
            {"anyOf": [{"minLength": 3}]},
            {"minLength": 3},
            id="any-of-singleton",
        ),
        pytest.param(
            {"anyOf": [False, {"minLength": 3}]},
            {"minLength": 3},
            id="any-of-false-and-constraint",
        ),
        pytest.param(
            {"oneOf": [False, {"minLength": 3}]},
            {"minLength": 3},
            id="one-of-false-and-constraint",
        ),
        pytest.param(
            {"allOf": [{"allOf": [{"minLength": 3}]}]},
            {"minLength": 3},
            id="nested-all-of",
        ),
        pytest.param(
            {"type": "array", "items": {"allOf": [True]}},
            {"type": "array", "items": None},
            id="array-neutral-item",
        ),
        pytest.param(
            {"type": "object", "additionalProperties": {"allOf": [True]}},
            {"type": "object", "additionalProperties": {}},
            id="mapping-neutral-value",
        ),
        pytest.param(
            {"type": "array", "items": {"allOf": [{"minLength": 3}]}},
            {"type": "array", "items": {"minLength": 3}},
            id="array-constrained-item",
        ),
        pytest.param(
            {
                "type": "array",
                "items": {
                    "type": "array",
                    "items": {"allOf": [{"minLength": 3}]},
                },
            },
            {
                "type": "array",
                "items": {
                    "type": "array",
                    "items": {"minLength": 3},
                },
            },
            id="deep-array-constrained-item",
        ),
    ],
)
def test_normalize_inherited_constraint_compositions(
    schema: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    """Constraint-only compositions are materialized without inventing a wrapper type."""
    parser = JsonSchemaParser("")

    normalized = parser._normalize_inherited_constraint_compositions(JsonSchemaObject.model_validate(schema))

    assert normalized.model_dump(exclude_unset=True, by_alias=True) == expected


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param(
            {"anyOf": [{"minLength": 2}, {"maxLength": 5}]},
            id="multi-any-of",
        ),
        pytest.param(
            {"oneOf": [True, {"minLength": 3}]},
            id="one-of-true-and-constraint",
        ),
        pytest.param(
            {"allOf": [{"type": "string"}, {"minLength": 3}]},
            id="typed-all-of",
        ),
        pytest.param(
            {"allOf": [False, {"minLength": 3}]},
            id="all-of-false-and-constraint",
        ),
        pytest.param(
            {"type": "array", "items": {"allOf": [False, {"minLength": 3}]}},
            id="nested-all-of-false-and-constraint",
        ),
    ],
)
def test_normalize_inherited_constraint_compositions_keeps_non_equivalent_shapes(
    schema: dict[str, Any],
) -> None:
    """Composition branches are retained when flattening would change their meaning."""
    parser = JsonSchemaParser("")
    obj = JsonSchemaObject.model_validate(schema)

    assert parser._normalize_inherited_constraint_compositions(obj) is obj


def test_normalize_inherited_constraint_compositions_fast_path() -> None:
    """Schemas without compositions avoid model serialization and validation."""
    parser = JsonSchemaParser("")
    obj = JsonSchemaObject.model_validate({"minLength": 3})

    assert parser._normalize_inherited_constraint_compositions(obj) is obj


@pytest.mark.parametrize(
    ("child", "parent_ref", "expected"),
    [
        pytest.param({"type": "string", "minLength": 2}, "#/$defs/Scalar", True, id="scalar"),
        pytest.param(
            {"type": "array", "items": {"minLength": 2}},
            "#/$defs/Array",
            True,
            id="array",
        ),
        pytest.param(
            {"type": "object", "properties": {"name": {"minLength": 2}}},
            "#/$defs/Object",
            True,
            id="object",
        ),
        pytest.param({"nullable": False, "minLength": 2}, "#/$defs/Scalar", True, id="nullable-false"),
        pytest.param(
            {"type": "object", "properties": {"id": {"type": "integer"}}},
            "#/$defs/Scalar",
            False,
            id="incompatible",
        ),
        pytest.param({"type": "integer", "minimum": 1}, "#/$defs/Number", True, id="integer-narrows-number"),
        pytest.param({"type": "number", "minimum": 1}, "#/$defs/Integer", False, id="number-widens-integer"),
        pytest.param({"type": "integer", "minimum": 1}, "#/$defs/Union", True, id="type-list-narrowing"),
    ],
)
def test_partial_inherited_nested_nullable_ref_type_compatibility(
    child: dict[str, Any],
    parent_ref: str,
    *,
    expected: bool,
) -> None:
    """Null exclusion keeps compatible ref types and rejects incompatible replacements."""
    parser = JsonSchemaParser("")
    parser.raw_obj = {
        "$defs": {
            "Scalar": {"type": "string", "nullable": True},
            "Array": {"type": ["array", "null"], "items": {"type": "string"}},
            "Number": {"type": "number"},
            "Integer": {"type": "integer"},
            "Union": {"type": ["string", "integer", "null"]},
            "Object": {
                "anyOf": [
                    {
                        "type": "object",
                        "required": ["name"],
                        "properties": {"name": {"type": "string"}},
                    },
                    {"type": "null"},
                ]
            },
        }
    }

    assert (
        parser._is_partial_inherited_nested_value(
            child,
            JsonSchemaObject.model_validate({"$ref": parent_ref}),
            context=("#/$defs/Owner", frozenset(), False),
        )
        is expected
    )


def test_merge_property_schemas_and_parent_constraint_paths() -> None:
    """Property merging preserves child precedence and handles boolean and no-merge parents."""
    parser = JsonSchemaParser("")
    assert parser._merge_property_schemas(
        {"required": ["base"]},
        {"required": ["child", "base"]},
    ) == {"required": ["child", "base"]}

    parser.raw_obj = {
        "$defs": {
            "Base": {
                "type": "object",
                "properties": {
                    "boolean_parent": True,
                    "text": {"type": "string", "minLength": 2},
                },
            }
        }
    }
    base_reference = parser.model_resolver.add_ref("#/$defs/Base", resolved=True)
    child = JsonSchemaObject.model_validate({
        "type": "object",
        "properties": {
            "boolean_parent": {"type": "integer"},
            "text": {"type": "string", "maxLength": 5},
            "own": {"allOf": [{"minLength": 3}]},
        },
    })
    merged = parser._merge_properties_with_parent_constraints(child, [base_reference])

    assert merged.properties is not None
    assert merged.properties["boolean_parent"] == JsonSchemaObject.model_validate({"type": "integer"})
    assert merged.properties["own"] == JsonSchemaObject.model_validate({"allOf": [{"minLength": 3}]})

    no_merge_parser = JsonSchemaParser("", allof_merge_mode=AllOfMergeMode.NoMerge)
    no_merge_parser.raw_obj = parser.raw_obj
    no_merge_reference = no_merge_parser.model_resolver.add_ref("#/$defs/Base", resolved=True)
    no_merge_child = JsonSchemaObject.model_validate({
        "type": "object",
        "properties": {"text": {"type": "string", "maxLength": 5}},
    })

    assert (
        no_merge_parser._merge_properties_with_parent_constraints(
            no_merge_child,
            [no_merge_reference],
        )
        is no_merge_child
    )
    no_merge_property = {"maxLength": 5}
    copied_no_merge_property = no_merge_parser._merge_property_schemas(
        {"type": "string", "minLength": 2},
        no_merge_property,
    )
    assert copied_no_merge_property == no_merge_property
    assert copied_no_merge_property is not no_merge_property


def test_nested_inherited_constraint_detection() -> None:
    """Nested container constraints are distinct from neutral partial schemas."""
    parser = JsonSchemaParser("")

    assert parser._has_inherited_constraints(
        JsonSchemaObject.model_validate({
            "type": "array",
            "items": {
                "type": "array",
                "items": {"minLength": 3},
            },
        })
    )
    assert not parser._has_inherited_constraints(
        JsonSchemaObject.model_validate({
            "type": "array",
            "items": {
                "type": "array",
                "items": {},
            },
        })
    )


def test_parse_nested_inline_inherited_schema_fields() -> None:
    """Nested inline allOf declarations are parsed in their inherited owner scope."""
    parser = JsonSchemaParser("")
    reference = parser.model_resolver.add_ref("#/$defs/Derived", resolved=True)
    schema = JsonSchemaObject.model_validate({
        "allOf": [
            {
                "allOf": [
                    {
                        "type": "object",
                        "properties": {"nested": {"type": "string"}},
                    }
                ]
            }
        ]
    })

    fields = parser._parse_inherited_schema_fields(reference, schema, [], [])

    assert [field.original_name for field in fields] == ["nested"]


def test_inherited_field_lookup_uses_generated_and_raw_types() -> None:
    """Inherited lookup copies generated fields and builds raw property types."""
    parser = JsonSchemaParser("")
    generated_reference = Reference(path="#/$defs/Generated", name="Generated")
    BaseModel(
        reference=generated_reference,
        fields=[
            DataModelFieldBase(
                name="generated",
                original_name="generated",
                data_type=DataType(type="str"),
            )
        ],
    )
    generated_type = parser._get_inherited_field_type("generated", [generated_reference])

    parser.raw_obj = {
        "$defs": {
            "Raw": {
                "type": "object",
                "properties": {"raw": {"type": "integer"}},
            }
        }
    }
    raw_reference = parser.model_resolver.add_ref("#/$defs/Raw", resolved=True)
    raw_type = parser._get_inherited_field_type("raw", [raw_reference])

    assert generated_type is not None
    assert generated_type.type == "str"
    assert raw_type is not None
    assert raw_type.type == "int"


def test_inherited_field_map_replaces_only_deferred_generated_fields() -> None:
    """Canonical raw lookup leaves resolved siblings untouched."""
    parser = JsonSchemaParser("")
    reference = Reference(path="#/$defs/Mixed", name="Mixed")
    stable_field = DataModelFieldBase(
        name="stable",
        original_name="stable",
        data_type=DataType(type="str"),
    )
    deferred_field = DataModelFieldBase(
        name="pending",
        original_name="pending",
        data_type=DataType(),
    )
    deferred_field.__dict__[_DEFERRED_INHERITED_TYPE_KEY] = deferred_field.data_type
    BaseModel(
        reference=reference,
        fields=[stable_field, deferred_field],
    )
    canonical_stable = DataModelFieldBase(
        name="stable",
        original_name="stable",
        data_type=DataType(type="str"),
    )
    canonical_pending = DataModelFieldBase(
        name="pending",
        original_name="pending",
        data_type=DataType(type="int"),
    )
    parser._inherited_schema_cache[reference.path] = JsonSchemaObject.model_validate({
        "type": "object",
        "properties": {
            "stable": {"type": "string"},
            "pending": {"type": "integer"},
        },
    })
    parser._raw_inherited_fields_cache[reference.path] = (
        canonical_stable,
        canonical_pending,
    )
    parser._raw_inherited_own_names_cache[reference.path] = frozenset({
        "stable",
        "pending",
    })

    inherited_fields = parser._get_inherited_field_map([reference])

    assert inherited_fields["stable"] is stable_field
    assert inherited_fields["pending"].data_type.type == "int"


def test_inherited_field_schema_cycle_and_mapping_fallbacks() -> None:
    """Raw field lookup and mapping allOf detection terminate on neutral cycles."""
    parser = JsonSchemaParser("")
    parser.raw_obj = {
        "$defs": {
            "CycleA": {"allOf": [{"$ref": "#/$defs/CycleB"}]},
            "CycleB": {"allOf": [{"$ref": "#/$defs/CycleA"}]},
            "NestedA": {"allOf": [{"$ref": "#/$defs/NestedB"}]},
            "NestedB": {
                "type": "object",
                "properties": {"target": {"type": "string"}},
            },
        }
    }
    cycle_reference = parser.model_resolver.add_ref("#/$defs/CycleA", resolved=True)
    nested_reference = parser.model_resolver.add_ref("#/$defs/NestedA", resolved=True)

    assert parser._get_inherited_field_schema("missing", [cycle_reference]) is None
    assert parser._get_inherited_field_schema("target", [nested_reference]) == JsonSchemaObject.model_validate({
        "type": "string"
    })
    assert (
        parser._get_inherited_field_schema(
            "target",
            [nested_reference],
            frozenset({"#/$defs/NestedB"}),
        )
        is None
    )
    assert parser._merge_all_of_root_schema(JsonSchemaObject.model_validate({"allOf": [True]})) is None
    assert parser._merge_all_of_root_schema(JsonSchemaObject.model_validate({})) is None


def test_merge_all_of_mapping_accepts_only_object_type_lists() -> None:
    """Merge singleton object type lists without accepting mixed types."""
    parser = JsonSchemaParser("")
    object_only = JsonSchemaObject.model_validate({
        "allOf": [{"type": ["object"], "additionalProperties": {"type": "integer"}}]
    })
    mixed = JsonSchemaObject.model_validate({
        "allOf": [{"type": ["object", "string"], "additionalProperties": {"type": "integer"}}]
    })

    merged = parser._merge_all_of_root_schema(object_only)

    assert merged is not None
    assert merged.type == "object"
    assert parser._merge_all_of_root_schema(mixed) is None


def test_resolve_type_import_from_defs() -> None:
    """Test _resolve_type_import_from_defs resolves imports from $defs with x-python-import."""
    schema_dict: dict[str, Any] = {
        "type": "object",
        "properties": {"status": {"$ref": "#/$defs/Status"}},
        "$defs": {
            "Status": {
                "type": "string",
                "enum": ["active", "inactive"],
                "x-python-import": {"module": "myapp.enums", "name": "Status"},
            }
        },
    }
    parser = JsonSchemaParser(json.dumps(schema_dict))
    parser.raw_obj = schema_dict  # Set raw_obj for _load_ref_schema_object to work

    # Call _resolve_type_import_from_defs directly
    result = parser._resolve_type_import_from_defs("Status")
    assert result is not None
    assert result.from_ == "myapp.enums"
    assert result.import_ == "Status"


def test_resolve_type_import_from_defs_not_found() -> None:
    """Test _resolve_type_import_from_defs returns None when type not in $defs."""
    schema_dict: dict[str, Any] = {"type": "object", "properties": {"name": {"type": "string"}}}
    parser = JsonSchemaParser(json.dumps(schema_dict))
    parser.raw_obj = schema_dict

    result = parser._resolve_type_import_from_defs("NonExistentType")
    assert result is None


def test_resolve_type_import_from_defs_no_x_python_import() -> None:
    """Test _resolve_type_import_from_defs returns None when $defs entry has no x-python-import."""
    schema_dict: dict[str, Any] = {
        "type": "object",
        "properties": {"status": {"$ref": "#/$defs/Status"}},
        "$defs": {"Status": {"type": "string", "enum": ["active", "inactive"]}},
    }
    parser = JsonSchemaParser(json.dumps(schema_dict))
    parser.raw_obj = schema_dict

    result = parser._resolve_type_import_from_defs("Status")
    assert result is None


def test_resolve_type_import_from_defs_exception_handling() -> None:
    """Test _resolve_type_import_from_defs handles exceptions gracefully.

    When raw_obj is None or invalid, _load_ref_schema_object will raise an exception,
    and _resolve_type_import_from_defs should catch it and return None.
    """
    schema_dict: dict[str, Any] = {"type": "object", "properties": {"name": {"type": "string"}}}
    parser = JsonSchemaParser(json.dumps(schema_dict))
    # Set raw_obj to None to trigger exception in _load_ref_schema_object
    parser.raw_obj = None  # pyright: ignore[reportAttributeAccessIssue]

    result = parser._resolve_type_import_from_defs("SomeType")
    assert result is None


def test_jsonschema_parser_edge_case_helpers() -> None:
    """Cover helper branches for boolean schemas and complex JSON values."""
    parser = JsonSchemaParser("", use_tuple_for_fixed_items=True)

    assert not parser._is_fixed_length_tuple(
        JsonSchemaObject.model_validate({
            "type": "array",
            "items": [{"type": "string"}, False],
            "minItems": 2,
            "maxItems": 2,
        })
    )
    assert JsonSchemaParser._property_names_forbids_all_keys(JsonSchemaObject.model_validate({"type": ["integer"]}))
    assert JsonSchemaParser._get_contains_count_constraints(JsonSchemaObject.model_validate({})) == (None, None)
    assert JsonSchemaParser._get_array_items_constraints(
        JsonSchemaObject.model_validate({"contains": True, "minContains": 1, "minItems": 2})
    ) == {"minItems": 2}
    assert parser._get_data_type_from_json_value(object()).type_hint == "Any"


def test_anchor_ref_path_escapes_json_pointer_segments() -> None:
    """Test anchor ref paths escape JSON Pointer segments."""
    parser = JsonSchemaParser("")

    assert parser._anchor_ref_path((), ["#", "$defs", "foo/bar", "tilde~key"]) == "#/$defs/foo~1bar/tilde~0key"

    parser.model_resolver.set_current_root([])
    parser._recursive_anchor_index[()] = ["#/$defs/foo~1bar"]
    recursive_ref = JsonSchemaObject.model_validate({"$recursiveRef": "#"})
    assert parser._resolve_recursive_ref(recursive_ref, ["#", "$defs", "foo/bar", "child"]) == "#/$defs/foo~1bar"


def test_preload_property_refs_skips_external_ref_mapping(tmp_path: Path, mocker: MockerFixture) -> None:
    """Test read/write preload does not load refs handled by external mapping."""
    external_schema = tmp_path / "external.json"
    parser = JsonSchemaParser(
        "",
        external_ref_mapping={str(external_schema): "external.models"},
        read_only_write_only_model_type=ReadOnlyWriteOnlyModelType.RequestResponse,
    )
    load_ref = mocker.patch.object(parser, "_load_ref_schema_object")

    parser._preload_property_refs_for_rw_models(
        JsonSchemaObject.model_validate({
            "properties": {
                "mapped": {"$ref": f"{external_schema}#/External"},
                "local": {"$ref": "#/$defs/Local"},
            },
        })
    )

    load_ref.assert_called_once_with("#/$defs/Local")


def test_read_write_variant_facts_include_inherited_and_one_sided_fields() -> None:
    """Request/response refs use both variants while all mode retains its compact model set."""
    schema: dict[str, Any] = {
        "$defs": {
            "OnlyRead": {
                "type": "object",
                "properties": {"id": {"type": "integer", "readOnly": True}},
            },
            "OnlyWrite": {
                "type": "object",
                "properties": {"secret": {"type": "string", "writeOnly": True}},
            },
            "ReadDerived": {
                "allOf": [{"$ref": "#/$defs/OnlyRead"}],
                "properties": {"name": {"type": "string"}},
            },
            "WriteDerived": {
                "allOf": [{"$ref": "#/$defs/OnlyWrite"}],
                "properties": {"name": {"type": "string"}},
            },
        },
    }
    request_response_parser = JsonSchemaParser(
        json.dumps(schema),
        read_only_write_only_model_type=ReadOnlyWriteOnlyModelType.RequestResponse,
    )
    request_response_parser.raw_obj = schema

    assert request_response_parser._get_ref_schema_rw_model_field_facts("#/$defs/OnlyRead") == (
        True,
        False,
        False,
        True,
    )
    assert request_response_parser._get_ref_schema_rw_model_field_facts("#/$defs/OnlyWrite") == (
        False,
        True,
        True,
        False,
    )
    assert request_response_parser._get_ref_schema_rw_model_field_facts("#/$defs/ReadDerived") == (
        True,
        False,
        True,
        True,
    )
    for ref_path in ("#/$defs/OnlyRead", "#/$defs/OnlyWrite", "#/$defs/ReadDerived", "#/$defs/WriteDerived"):
        assert request_response_parser._ref_schema_generates_variant(ref_path, "Request")
        assert request_response_parser._ref_schema_generates_variant(ref_path, "Response")

    request_type = DataType(reference=request_response_parser.model_resolver.add_ref("#/$defs/OnlyRead"))
    response_type = DataType(reference=request_response_parser.model_resolver.add_ref("#/$defs/OnlyRead"))
    request_response_parser._update_data_type_ref_for_variant(request_type, "Request")
    request_response_parser._update_data_type_ref_for_variant(response_type, "Response")
    assert request_type.reference
    assert request_type.reference.name == "OnlyReadRequest"
    assert response_type.reference
    assert response_type.reference.name == "OnlyReadResponse"

    all_parser = JsonSchemaParser(
        json.dumps(schema),
        read_only_write_only_model_type=ReadOnlyWriteOnlyModelType.All,
    )
    all_parser.raw_obj = schema
    assert not all_parser._ref_schema_generates_variant("#/$defs/OnlyRead", "Request")
    assert not all_parser._ref_schema_generates_variant("#/$defs/OnlyRead", "Response")
    assert not all_parser._ref_schema_generates_variant("#/$defs/OnlyWrite", "Request")
    assert not all_parser._ref_schema_generates_variant("#/$defs/OnlyWrite", "Response")
    assert all_parser._ref_schema_generates_variant("#/$defs/ReadDerived", "Request")
    assert not all_parser._ref_schema_generates_variant("#/$defs/ReadDerived", "Response")
    assert not all_parser._ref_schema_generates_variant("#/$defs/WriteDerived", "Request")
    assert all_parser._ref_schema_generates_variant("#/$defs/WriteDerived", "Response")


@pytest.mark.parametrize("node_count", [100, 200, 400])
def test_request_response_negative_ring_caches_each_graph_node_once(node_count: int) -> None:
    """A negative reference ring is traversed once and cached for both variants."""

    class CountingJsonSchemaParser(JsonSchemaParser):
        graph_node_calls: Counter[str]

        def _get_rw_model_variant_graph_node(self, resolved_ref: str) -> tuple[bool, tuple[str, ...]]:
            self.graph_node_calls[resolved_ref] += 1
            return super()._get_rw_model_variant_graph_node(resolved_ref)

    schema: dict[str, Any] = {
        "$defs": {
            f"Node{index}": {
                "type": "object",
                "properties": {
                    "next": {
                        "$ref": f"#/$defs/Node{(index + 1) % node_count}",
                    },
                },
            }
            for index in range(node_count)
        },
    }
    parser = CountingJsonSchemaParser(
        json.dumps(schema),
        read_only_write_only_model_type=ReadOnlyWriteOnlyModelType.RequestResponse,
    )
    parser.raw_obj = schema
    parser.graph_node_calls = Counter()

    assert not parser._ref_schema_generates_variant("#/$defs/Node0", "Request")
    assert parser.graph_node_calls == Counter({f"#/$defs/Node{index}": 1 for index in range(node_count)})
    assert parser._rw_model_variant_requirement_cache == {
        (f"#/$defs/Node{index}", suffix): False for index in range(node_count) for suffix in ("Request", "Response")
    }

    for index in range(node_count):
        assert not parser._ref_schema_generates_variant(f"#/$defs/Node{index}", "Request")
        assert not parser._ref_schema_generates_variant(f"#/$defs/Node{index}", "Response")

    assert sum(parser.graph_node_calls.values()) == node_count


def test_request_response_property_names_follows_only_type_contributing_refs() -> None:
    """PropertyNames follows a bare ref but ignores constraints and nested non-key shapes."""
    schema: dict[str, Any] = {
        "$defs": {
            "Child": {
                "type": "object",
                "properties": {"id": {"type": "integer", "readOnly": True}},
            },
            "Bare": {
                "type": "object",
                "propertyNames": {"$ref": "#/$defs/Child"},
            },
            "RefWithPattern": {
                "type": "object",
                "propertyNames": {
                    "$ref": "#/$defs/Child",
                    "pattern": "^x",
                },
            },
            "NestedShape": {
                "type": "object",
                "propertyNames": {
                    "type": "object",
                    "properties": {"nested": {"$ref": "#/$defs/Child"}},
                },
            },
        },
    }
    parser = JsonSchemaParser(
        json.dumps(schema),
        read_only_write_only_model_type=ReadOnlyWriteOnlyModelType.RequestResponse,
    )
    parser.raw_obj = schema

    assert parser._get_ref_schema_rw_model_reference_facts("#/$defs/Bare") == (
        False,
        ("#/$defs/Child",),
    )
    assert parser._ref_schema_generates_variant("#/$defs/Bare", "Request")
    for name in ("RefWithPattern", "NestedShape"):
        assert parser._get_ref_schema_rw_model_reference_facts(f"#/$defs/{name}") == (False, ())
        assert not parser._ref_schema_generates_variant(f"#/$defs/{name}", "Request")


def test_request_response_variant_graph_handles_cached_internal_and_missing_refs() -> None:
    """Variant reachability stops at cached negatives, internal variants, and missing schemas."""
    schema: dict[str, Any] = {
        "$defs": {
            "Leaf": {"type": "object", "properties": {"value": {"type": "string"}}},
            "Root": {
                "type": "object",
                "properties": {"leaf": {"$ref": "#/$defs/Leaf"}},
            },
            "Cached": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
            },
        },
    }
    parser = JsonSchemaParser(
        json.dumps(schema),
        read_only_write_only_model_type=ReadOnlyWriteOnlyModelType.RequestResponse,
    )
    parser.raw_obj = schema
    parser._rw_model_variant_requirement_cache["#/$defs/Leaf", "Request"] = False

    assert not parser._request_response_ref_schema_generates_variant("#/$defs/Root")

    internal_request_path = f"#/$defs/Leaf/{SPECIAL_PATH_FORMAT.format('read-write-request')}"
    internal_response_path = f"#/$defs/Leaf/{SPECIAL_PATH_FORMAT.format('read-write-response')}"
    assert not parser._ref_schema_generates_variant(internal_request_path, "Response")
    assert not parser._request_response_ref_schema_generates_variant(internal_response_path)

    cached_reference = parser.model_resolver.add_ref("#/$defs/Cached", resolved=True)
    parser._request_response_fields[cached_reference.path] = ()
    assert parser._get_raw_inherited_fields(cached_reference, frozenset()) == []
    assert parser._raw_inherited_own_names_cache[cached_reference.path] == frozenset({"value"})

    all_parser = JsonSchemaParser(
        "",
        read_only_write_only_model_type=ReadOnlyWriteOnlyModelType.All,
    )
    all_parser.raw_obj = None  # type: ignore[assignment]  # exercise an unresolved external document
    assert not all_parser._ref_schema_generates_variant("#/$defs/Missing", "Request")


def test_request_response_schema_graph_helper_edges(tmp_path: Path) -> None:
    """Schema graph helpers cover tuple items, combined key schemas, and mapped leaves."""
    parser = JsonSchemaParser("")
    tuple_schema = JsonSchemaObject.model_validate({
        "items": [{"type": "string"}, False],
    })
    children = list(parser._iter_rw_model_schema_children(tuple_schema, detect_inline_variant=True))

    assert len(children) == 1
    assert children[0][0].type == "string"
    assert children[0][1] is True

    property_names = JsonSchemaObject.model_validate({
        "anyOf": [{"$ref": "#/$defs/First"}, {"$ref": "#/$defs/Second"}],
    })
    refs, combined = parser._get_rw_property_name_sources(
        property_names=property_names,
        defining_ref="#/$defs/Mapping",
    )
    assert refs == ()
    assert [item.ref for item in combined] == ["#/$defs/First", "#/$defs/Second"]
    assert parser._ref_schema_exists("")

    external_schema = tmp_path / "external.json"
    mapped_schema = {
        "$defs": {
            "MappedProperty": {
                "type": "object",
                "properties": {"value": {"$ref": f"{external_schema}#/$defs/External"}},
            },
            "MappedPropertyName": {
                "type": "object",
                "propertyNames": {"$ref": f"{external_schema}#/$defs/External"},
            },
        }
    }
    mapped_parser = JsonSchemaParser(
        json.dumps(mapped_schema),
        external_ref_mapping={str(external_schema): "external.models"},
    )
    mapped_parser.raw_obj = mapped_schema
    assert (
        mapped_parser._resolve_rw_model_reference(
            f"{external_schema}#/$defs/External",
            "#/$defs/Local",
        )
        is None
    )
    assert mapped_parser._get_ref_schema_rw_model_reference_facts("#/$defs/MappedProperty") == (False, ())
    assert mapped_parser._get_ref_schema_rw_model_reference_facts("#/$defs/MappedPropertyName") == (False, ())

    inline_properties = JsonSchemaObject.model_validate({
        "properties": {"direct": {"type": "string"}},
        "allOf": [
            True,
            {
                "type": "object",
                "properties": {"nested": {"type": "integer"}},
            },
        ],
    })
    assert parser._get_inline_property_names(inline_properties) == frozenset({"direct", "nested"})


def test_request_response_variant_source_path_collision_fallbacks() -> None:
    """Variant metadata paths remain unique for unrelated names and repeated source collisions."""
    assert (
        _get_unique_rw_model_variant_source_path(
            "#/$defs/ChildRequest",
            "ChildRequest",
            "RenamedRequest",
            collides_with_source=True,
        )
        == "#/$defs/RenamedRequest"
    )

    parser = JsonSchemaParser(
        "",
        read_only_write_only_model_type=ReadOnlyWriteOnlyModelType.RequestResponse,
    )
    parser.raw_obj = {
        "$defs": {
            "Child": {},
            "ChildRequest": {},
            "ChildRequest1": {},
        },
    }
    base_reference = parser.model_resolver.add(
        ["#", "$defs", "Child"],
        "Child",
        class_name=True,
        loaded=True,
    )

    request_reference = parser._get_rw_model_variant_reference(base_reference, "Request")

    assert request_reference.__dict__[_SOURCE_REFERENCE_PATH_KEY] == "#/$defs/ChildRequest11"


def test_request_response_variant_affix_keeps_numeric_suffix_and_unique_source_path() -> None:
    """Variant affixes precede collision indexes and never reuse a source schema path."""
    parser = JsonSchemaParser(
        "",
        class_name_prefix="Api",
        class_name_suffix="Model",
        read_only_write_only_model_type=ReadOnlyWriteOnlyModelType.RequestResponse,
    )
    parser.raw_obj = {
        "$defs": {
            "ChildRequest": {"type": "string"},
            "ChildResponse": {"type": "string"},
        },
    }
    parser.model_resolver.add(["#", "$defs", "Other"], "Child", class_name=True, loaded=True)
    base_reference = parser.model_resolver.add(
        ["#", "$defs", "Child"],
        "Child",
        class_name=True,
        loaded=True,
    )

    request_reference = parser._get_rw_model_variant_reference(base_reference, "Request")
    response_reference = parser._get_rw_model_variant_reference(base_reference, "Response")

    assert base_reference.name == "ApiChildModel1"
    assert request_reference.name == "ApiChildRequestModel1"
    assert response_reference.name == "ApiChildResponseModel1"
    source_paths = {
        request_reference.__dict__[_SOURCE_REFERENCE_PATH_KEY],
        response_reference.__dict__[_SOURCE_REFERENCE_PATH_KEY],
    }
    assert len(source_paths | {"#/$defs/ChildRequest", "#/$defs/ChildResponse"}) == 4


def test_request_response_typed_dict_additional_properties_metadata_uses_variants() -> None:
    """TypedDict extra-item hints and dependency metadata point at matching variants."""
    model_types = get_data_model_types(
        DataModelType.TypingTypedDict,
        target_python_version=PythonVersion.PY_312,
    )
    schema: dict[str, Any] = {
        "$defs": {
            "Child": {
                "type": "object",
                "properties": {"id": {"type": "integer", "readOnly": True}},
            },
            "Mapping": {
                "type": "object",
                "additionalProperties": {"$ref": "#/$defs/Child"},
            },
        },
        "$ref": "#/$defs/Mapping",
    }
    parser = JsonSchemaParser(
        json.dumps(schema),
        data_model_type=model_types.data_model,
        data_model_root_type=model_types.root_model,
        data_model_field_type=model_types.field_model,
        data_type_manager_type=model_types.data_type_manager,
        target_python_version=PythonVersion.PY_312,
        use_closed_typed_dict=True,
        read_only_write_only_model_type=ReadOnlyWriteOnlyModelType.RequestResponse,
    )

    parser.parse(format_=False)

    models = {model.class_name: model for model in parser.results}
    for suffix in ("Request", "Response"):
        mapping = models[f"Mapping{suffix}"]
        child = models[f"Child{suffix}"]
        assert mapping.extra_template_data["additionalPropertiesType"] == f"Child{suffix}"
        assert mapping.extra_template_data["additionalPropertiesReferenceClasses"] == {
            child.reference.path,
        }


def test_request_response_runtime_validation_is_copied_filtered_and_retargeted() -> None:
    """Runtime rules are independent per variant and use only matching fields and refs."""
    schema: dict[str, Any] = {
        "$defs": {
            "Child": {
                "type": "object",
                "properties": {"id": {"type": "integer", "readOnly": True}},
            },
            "Container": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "requestOnly": {"type": "string", "writeOnly": True},
                    "responseOnly": {"type": "string", "readOnly": True},
                },
                "patternProperties": {"^child": {"$ref": "#/$defs/Child"}},
                "additionalProperties": {"$ref": "#/$defs/Child"},
                "oneOf": [
                    {"required": ["requestOnly"]},
                    {"required": ["responseOnly"]},
                ],
                "anyOf": [{"required": ["kind"]}],
                "if": {
                    "required": ["kind"],
                    "properties": {"kind": {"const": "metric"}},
                },
                "then": {"required": ["requestOnly"]},
                "else": {"required": ["responseOnly"]},
            },
        },
        "$ref": "#/$defs/Container",
    }
    parser = JsonSchemaParser(
        json.dumps(schema),
        generate_schema_validators=True,
        read_only_write_only_model_type=ReadOnlyWriteOnlyModelType.RequestResponse,
    )

    parser.parse(format_=False)

    models = {model.class_name: model for model in parser.results}
    source_runtime = parser.extra_template_data["#/$defs/Container"]["schema_runtime_validation"]
    for suffix, included_name, excluded_name, expected_one_of, expected_then, expected_else in (
        (
            "Request",
            "requestOnly",
            "responseOnly",
            ((("requestOnly",),), ()),
            ((("requestOnly",),),),
            ((),),
        ),
        (
            "Response",
            "responseOnly",
            "requestOnly",
            ((), (("responseOnly",),)),
            ((),),
            ((("responseOnly",),),),
        ),
    ):
        model = models[f"Container{suffix}"]
        child = models[f"Child{suffix}"]
        runtime = model._internal_template_data["schema_runtime_validation"]
        pattern_rule = runtime.pattern_properties[0]
        pattern_type = pattern_rule.pattern_properties[0][1]
        additional_type = pattern_rule.additional_property_type

        assert runtime is not source_runtime
        assert pattern_type is not source_runtime.pattern_properties[0].pattern_properties[0][1]
        assert pattern_type.reference is child.reference
        assert additional_type is not None
        assert additional_type.reference is child.reference
        assert included_name in pattern_rule.declared_properties
        assert excluded_name not in pattern_rule.declared_properties

        required_groups = {rule.keyword: rule.groups for rule in runtime.required_groups}
        assert required_groups["anyOf"] == ((("kind",),),)
        assert required_groups["oneOf"] == expected_one_of

        conditional_rule = runtime.conditional_required[0]
        assert conditional_rule.condition == ((("kind",), ("metric",)),)
        assert conditional_rule.then_groups == expected_then
        assert conditional_rule.else_groups == expected_else

    assert {data_type.type_hint for data_type in source_runtime.data_types} == {"Child"}


def test_request_response_runtime_validation_copy_handles_empty_optional_parts() -> None:
    """Copy pattern rules without an additional type and discard fully filtered conditional rules."""
    parser = JsonSchemaParser(
        "",
        read_only_write_only_model_type=ReadOnlyWriteOnlyModelType.RequestResponse,
    )
    field = DataModelFieldBase(
        name="kept",
        original_name="kept",
        data_type=DataType(type="str"),
    )
    pattern_source_path = "#/PatternSource"
    pattern_target_path = "#/PatternTarget"
    parser.extra_template_data[pattern_source_path]["schema_runtime_validation"] = (
        _make_internal_schema_runtime_validation(
            pattern_properties=[
                PatternPropertiesRule(
                    declared_properties=("kept", "removed"),
                    pattern_properties=(("^x", DataType(type="str")),),
                )
            ]
        )
    )

    parser._copy_schema_runtime_validation_for_variant(
        pattern_source_path,
        pattern_target_path,
        [field],
        "Request",
        obj=_json_schema_object({"type": "object"}),
    )

    pattern_target = parser.extra_template_data[pattern_target_path]["schema_runtime_validation"]
    assert pattern_target.pattern_properties[0].declared_properties == ("kept",)
    assert pattern_target.pattern_properties[0].additional_property_type is None

    conditional_source_path = "#/ConditionalSource"
    conditional_target_path = "#/ConditionalTarget"
    parser.extra_template_data[conditional_source_path]["schema_runtime_validation"] = (
        _make_internal_schema_runtime_validation(
            conditional_required=[
                ConditionalRequiredRule(
                    condition=((("removed",), ("value",)),),
                    then_groups=((("kept",),),),
                    else_groups=(),
                )
            ]
        )
    )

    parser._copy_schema_runtime_validation_for_variant(
        conditional_source_path,
        conditional_target_path,
        [field],
        "Response",
        obj=_json_schema_object({"type": "object"}),
    )

    assert "schema_runtime_validation" not in parser.extra_template_data[conditional_target_path]


def test_all_mode_skips_empty_variant_model() -> None:
    """Do not register an empty variant in all-model mode."""
    parser = JsonSchemaParser(
        "",
        read_only_write_only_model_type=ReadOnlyWriteOnlyModelType.All,
    )
    reference = parser.model_resolver.add(
        ["#", "$defs", "Empty"],
        "Empty",
        class_name=True,
        loaded=True,
    )

    parser._create_variant_model(
        reference,
        "Request",
        [],
        JsonSchemaObject.model_validate({"type": "object"}),
        BaseModel,
    )

    assert not parser.results


def test_json_schema_object_x_property_names_dict() -> None:
    """Test OpenAPI x-propertyNames dict is normalized to propertyNames."""
    obj = JsonSchemaObject.model_validate({"x-propertyNames": {"type": "string", "pattern": "^x-"}})
    ignored = JsonSchemaObject.model_validate({"x-propertyNames": "ignored"})

    assert isinstance(obj.propertyNames, JsonSchemaObject)
    assert obj.propertyNames.pattern == "^x-"
    assert "x-propertyNames" not in obj.extras
    assert ignored.propertyNames is None
    assert "x-propertyNames" not in ignored.extras


def test_set_additional_properties_schema_allows_extra_without_typed_runtime() -> None:
    """Test schema-valued additionalProperties allows extras without typed extra validation."""
    parser = JsonSchemaParser("", use_closed_typed_dict=False)
    parser.extra_template_data["#/Model"] = {}
    parser.set_additional_properties(
        "#/Model",
        JsonSchemaObject.model_validate({"additionalProperties": {"type": "string"}}),
    )
    assert parser.extra_template_data["#/Model"] == {"additionalProperties": True}


def test_set_additional_properties_schema_keeps_typed_dict_extra_items_metadata() -> None:
    """Test schema-valued additionalProperties still feeds PEP 728 TypedDict metadata."""
    parser = JsonSchemaParser("", use_closed_typed_dict=True)
    parser.extra_template_data["#/Model"] = {}

    parser.set_additional_properties(
        "#/Model",
        JsonSchemaObject.model_validate({"additionalProperties": {"type": "string"}}),
    )

    assert parser.extra_template_data["#/Model"] == {
        "additionalProperties": True,
        "additionalPropertiesType": "str",
        "use_typeddict_backport": True,
    }


def test_set_unevaluated_properties_schema_allows_extra_without_typed_runtime() -> None:
    """Test schema-valued unevaluatedProperties allows extras without typed extra validation."""
    parser = JsonSchemaParser("")
    parser.extra_template_data["#/Model"] = {}

    parser.set_unevaluated_properties(
        "#/Model",
        JsonSchemaObject.model_validate({"unevaluatedProperties": {"type": "integer"}}),
    )

    assert parser.extra_template_data["#/Model"] == {"unevaluatedProperties": True}


def test_standard_schema_metadata_is_included_in_field_extras() -> None:
    """Test standard metadata keys are preserved as field extras by default."""
    parser = JsonSchemaParser("")
    obj = JsonSchemaObject.model_validate({
        "type": "string",
        "contentEncoding": "base64",
        "contentMediaType": "application/json",
        "contentSchema": {"type": "object"},
        "externalDocs": {"url": "https://example.com/field"},
        "xml": {"name": "field"},
    })

    assert parser.get_field_extras(obj) == {
        "contentEncoding": "base64",
        "contentMediaType": "application/json",
        "contentSchema": {"type": "object"},
        "externalDocs": {"url": "https://example.com/field"},
        "xml": {"name": "field"},
    }


def test_field_extra_keys_without_x_prefix_removes_exact_prefix() -> None:
    """Test x-prefixed field extras remove only the exact extension prefix."""
    parser = JsonSchemaParser("", field_extra_keys_without_x_prefix={"x-xml"})
    obj = JsonSchemaObject.model_validate({"type": "string", "x-xml": {"name": "field"}})

    assert parser.get_field_extras(obj) == {"xml": {"name": "field"}}


def test_standard_schema_metadata_is_included_in_model_extras() -> None:
    """Test standard metadata keys are preserved as model extras by default."""
    parser = JsonSchemaParser("")
    parser.extra_template_data["#/Model"] = {}
    obj = JsonSchemaObject.model_validate({
        "type": "object",
        "externalDocs": {"url": "https://example.com/model"},
        "xml": {"name": "model"},
    })

    parser.set_schema_extensions("#/Model", obj)

    assert parser.extra_template_data["#/Model"] == {
        "model_extras": {
            "externalDocs": {"url": "https://example.com/model"},
            "xml": {"name": "model"},
        }
    }


@pytest.mark.parametrize(
    ("schema", "type_hint"),
    [
        ({"allOf": [True]}, "Any"),
        ({"enum": ["x"]}, "Literal['x']"),
        ({"allOf": [True, {"type": "string"}]}, "str"),
        ({"allOf": [{"$ref": "#/$defs/A"}, {"$ref": "#/$defs/B"}]}, "A"),
        (
            {
                "allOf": [
                    {"type": "object"},
                    {"type": "object", "properties": {"value": {"type": "string"}}},
                ]
            },
            "Dict[str, Any]",
        ),
        (
            {
                "allOf": [
                    {"type": "object", "additionalProperties": {"type": "string"}},
                    {"type": "object", "additionalProperties": {"type": "integer"}},
                ]
            },
            "Dict[str, str]",
        ),
        ({"allOf": [{"type": "object", "additionalProperties": {}}]}, "Dict[str, Any]"),
        (
            {
                "allOf": [
                    {"type": "object", "additionalProperties": {}},
                    {"type": "object", "additionalProperties": {"type": "integer"}},
                ]
            },
            "Dict[str, int]",
        ),
        ({"type": "array", "items": {"$ref": "#/$defs/Item"}}, "List[Item]"),
        ({"type": ["string", "null"]}, "Optional[str]"),
        ({"type": "array", "prefixItems": [{"type": "string"}], "items": True}, "List[Union[str, Any]]"),
        ({"type": "array", "prefixItems": [{"type": "string"}, False], "items": {"type": "integer"}}, "List[str]"),
        ({"type": "array", "prefixItems": [{"type": "string"}]}, "List[str]"),
        (
            {"type": "array", "prefixItems": [{"type": "string"}], "items": {"type": "integer"}},
            "List[Union[str, int]]",
        ),
        (
            {"type": "array", "prefixItems": [{"type": "string"}], "unevaluatedItems": {"type": "integer"}},
            "List[Union[str, int]]",
        ),
        ({"type": "array", "prefixItems": [{"type": "string"}], "unevaluatedItems": True}, "List[Union[str, Any]]"),
        ({"type": "array", "items": [{"type": "string"}], "additionalItems": True}, "List[Union[str, Any]]"),
        ({"type": "array"}, "List[Any]"),
        ({"type": "array", "unevaluatedItems": {"type": "integer"}}, "List[int]"),
        ({"type": "array", "unevaluatedItems": True}, "List[Any]"),
        ({"enum": ["x", {"a": 1}, None]}, "Optional[Union[Literal['x'], Dict[str, int]]]"),
        ({"anyOf": [False, True]}, "Any"),
    ],
)
def test_build_lightweight_type_edge_cases(schema: dict[str, Any], type_hint: str) -> None:
    """Test lightweight type inference for boolean and complex schemas."""
    parser = JsonSchemaParser("")
    data_type = parser._build_lightweight_type(JsonSchemaObject.model_validate(schema))
    assert data_type is not None
    assert data_type.type_hint == type_hint


def test_build_lightweight_type_allof_false() -> None:
    """Test allOf false produces no lightweight type."""
    parser = JsonSchemaParser("")
    assert parser._build_lightweight_type(JsonSchemaObject.model_validate({"allOf": [False]})) is None
    assert parser._build_lightweight_type(JsonSchemaObject.model_validate({"anyOf": [False]})) is None


def test_parse_array_fields_with_prefix_and_unevaluated_schema() -> None:
    """Test array field parsing combines prefix items with unevaluatedItems schema."""
    parser = JsonSchemaParser("")
    field = parser.parse_array_fields(
        "Model",
        JsonSchemaObject.model_validate({
            "type": "array",
            "prefixItems": [{"type": "string"}],
            "unevaluatedItems": {"type": "integer"},
        }),
        ["#"],
    )
    assert field.data_type.type_hint == "List[Union[str, int]]"


def test_parse_enum_as_literal_with_literal_and_complex_values() -> None:
    """Test literal enum parsing keeps scalar literals and infers complex value types."""
    parser = JsonSchemaParser("")
    data_type = parser.parse_enum_as_literal(JsonSchemaObject.model_validate({"enum": ["x", {"a": 1}, None]}))
    assert data_type.type_hint == "Optional[Union[Literal['x'], Dict[str, int]]]"
