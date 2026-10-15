"""Tests for generate_dynamic_models function."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pydantic
import pytest
from inline_snapshot import external_file

from datamodel_code_generator import (
    AllExportsCollisionStrategy,
    AllExportsScope,
    DataModelType,
    Error,
    InputFileType,
    clear_dynamic_models_cache,
    generate,
    generate_dynamic_models,
    load_yaml_dict_from_path,
)
from datamodel_code_generator.config import GenerateConfig
from datamodel_code_generator.enums import ModuleSplitMode
from datamodel_code_generator.format import Formatter
from datamodel_code_generator.model.pydantic_v2 import UnionMode
from datamodel_code_generator.types import StrictTypes
from tests.conftest import (
    assert_directory_content,
    assert_generated_modules_output,
    assert_inputs_not_mutated,
    assert_output,
)
from tests.main.conftest import (
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
    assert_generated_model_json_validation,
    run_main_with_args,
)

if TYPE_CHECKING:
    from typing import Any


pytestmark = pytest.mark.skipif(pydantic.VERSION < "2.0.0", reason="generate_dynamic_models requires Pydantic v2")

DATA_PATH = Path(__file__).parent.parent / "data" / "dynamic_models"
EXPECTED_PATH = Path(__file__).parent.parent / "data" / "expected" / "dynamic_models"


def make_object_schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    """Create a simple object schema."""
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def make_config(
    class_name: str | None = None,
    module_split_mode: ModuleSplitMode | None = None,
) -> GenerateConfig:
    """Create a GenerateConfig with common defaults."""
    return GenerateConfig(
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        class_name=class_name,
        module_split_mode=module_split_mode,
    )


def assert_dynamic_models(
    schema: dict[str, Any],
    validations: dict[str, dict[str, Any]],
    expected_path: Path,
    *,
    config: GenerateConfig | None = None,
    include_model_names: bool = False,
) -> None:
    """Generate dynamic models, validate data, and assert with external file."""
    models = generate_dynamic_models(schema, config=config)
    actual: dict[str, Any] = {}
    for name, data in validations.items():
        model = models[name]
        model_dump = model.model_validate(data).model_dump(mode="json")
        if include_model_names:
            actual[name] = {"__name__": model.__name__, "data": model_dump}
            continue
        actual[name] = model_dump
    assert actual == external_file(expected_path)


@pytest.fixture(autouse=True)
def _setup_and_clear_cache() -> None:
    """Rebuild GenerateConfig and clear cache before each test."""
    GenerateConfig.model_rebuild(_types_namespace={"StrictTypes": StrictTypes, "UnionMode": UnionMode})
    clear_dynamic_models_cache()


def test_simple_model() -> None:
    """Test generating a simple model and validating data."""
    schema = make_object_schema({"name": {"type": "string"}, "age": {"type": "integer"}}, required=["name"])
    assert_dynamic_models(schema, {"Model": {"name": "John", "age": 30}}, EXPECTED_PATH / "simple_model.json")
    assert_dynamic_models(schema, {"Model": {"name": "Jane"}}, EXPECTED_PATH / "simple_model_optional.json")


def test_nested_models() -> None:
    """Test generating nested models and validating nested data."""
    schema = make_object_schema({"user": {"type": "object", "properties": {"name": {"type": "string"}}}})
    assert_dynamic_models(schema, {"Model": {"user": {"name": "Alice"}}}, EXPECTED_PATH / "nested_models.json")


@pytest.mark.parametrize(
    "fixture_name",
    ["unsafe_custom_base_path_scalar.json", "unsafe_custom_base_path_list_nested.json"],
)
def test_generate_apis_reject_unsafe_custom_base_path(fixture_name: str) -> None:
    """Reject unsafe scalar and nested list customBasePath values before dynamic execution."""
    schema = json.loads((JSON_SCHEMA_DATA_PATH / fixture_name).read_text(encoding="utf-8"))
    config = make_config()

    with pytest.raises(Error, match="customBasePath must be a dotted Python identifier path"):
        generate(input_=schema, config=config)
    with pytest.raises(Error, match="customBasePath must be a dotted Python identifier path"):
        generate_dynamic_models(schema, config=config)


def test_asyncapi_dynamic_models() -> None:
    """Test auto-detecting AsyncAPI input for dynamic models."""
    schema: dict[str, Any] = {
        "asyncapi": "2.6.0",
        "info": {"title": "User Events", "version": "1.0.0"},
        "channels": {
            "users": {
                "publish": {
                    "message": {
                        "payload": {"$ref": "#/components/schemas/User"},
                    },
                },
            },
        },
        "components": {
            "schemas": {
                "User": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
        },
    }

    models = generate_dynamic_models(schema)

    assert models["User"].model_validate({"name": "Alice"}).model_dump(mode="json") == {"name": "Alice"}


def test_enum_model() -> None:
    """Test generating model with enum and validating enum values."""
    schema = make_object_schema({"status": {"type": "string", "enum": ["active", "inactive"]}})
    assert_dynamic_models(schema, {"Model": {"status": "active"}}, EXPECTED_PATH / "enum_model.json")

    models = generate_dynamic_models(schema)
    with pytest.raises(pydantic.ValidationError):
        models["Model"].model_validate({"status": "invalid"})


def test_imported_model_classes_are_not_returned() -> None:
    """Return only classes defined by the generated module."""
    schema = json.loads((DATA_PATH / "imported_model_classes.json").read_text(encoding="utf-8"))
    config = GenerateConfig(
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        additional_imports=[
            "pydantic.RootModel",
            "datamodel_code_generator.enums.DataModelType",
        ],
    )

    models = generate_dynamic_models(schema, config=config, cache_size=0)
    actual = {
        "models": sorted(models),
        "validated": models["GeneratedUser"].model_validate({"name": "Alice"}).model_dump(mode="json"),
    }
    assert_output(
        f"{json.dumps(actual, indent=2, sort_keys=True)}\n",
        EXPECTED_PATH / "imported_model_classes.txt",
    )


@pytest.mark.parametrize("cache_size", [0, 128])
@pytest.mark.parametrize("module_name", [None, "dynamic_root_aliases", "tests.data.dynamic_models"])
@pytest.mark.parametrize("module_split_mode", [None, ModuleSplitMode.Single])
@pytest.mark.parametrize("use_aliases", [False, True], ids=["classes", "aliases"])
def test_generated_root_model_aliases(
    cache_size: int, module_name: str | None, module_split_mode: ModuleSplitMode | None, use_aliases: bool
) -> None:
    """Return declared RootModel aliases in order without publishing imported dependencies."""
    from tests.data.dynamic_models.root_model_alias_imports import ImportedIntegerRoot

    schema = json.loads((DATA_PATH / "root_model_aliases.json").read_text(encoding="utf-8"))
    payloads = json.loads((DATA_PATH / "root_model_alias_payloads.json").read_text(encoding="utf-8"))
    expected = EXPECTED_PATH / "root_model_aliases" / ("aliases" if use_aliases else "classes")
    config = GenerateConfig(
        input_file_type=InputFileType.JsonSchema,
        use_root_model_type_alias=use_aliases,
        module_split_mode=module_split_mode,
        additional_imports=[
            "pydantic.RootModel",
            "datamodel_code_generator.enums.DataModelType",
            "tests.data.dynamic_models.root_model_alias_imports.ImportedIntegerRoot",
            "tests.data.dynamic_models.root_model_alias_imports.ImportedObject",
        ],
        disable_timestamp=True,
        formatters=[Formatter.BUILTIN],
    )
    generated = generate(schema, config=config)
    if module_split_mode is None:
        assert_output(generated, expected / "code.py")
    else:
        assert_generated_modules_output(generated, expected / "modules")

    original_module = ImportedIntegerRoot.__module__
    registered_module = sys.modules.get(module_name or "")
    with assert_inputs_not_mutated({"schema": schema, "config": config}):
        models = generate_dynamic_models(schema, config=config, cache_size=cache_size, module_name=module_name)
        repeated = generate_dynamic_models(schema, config=config, cache_size=cache_size, module_name=module_name)
        targeted = generate_dynamic_models(
            schema,
            config=config,
            cache_size=cache_size,
            module_name=module_name,
            target_model_names=["TwinNumber", "ZNumber"],
        )
    values = {
        name: model.model_validate(payloads[name]).model_dump(mode="json")
        if issubclass(model, pydantic.BaseModel)
        else model(payloads[name]).value
        for name, model in models.items()
    }
    with pytest.raises(pydantic.ValidationError):
        models["ZNumber"].model_validate("not an integer")
    with pytest.raises(Error) as error:
        generate_dynamic_models(
            schema,
            config=config,
            cache_size=cache_size,
            module_name=module_name,
            target_model_names=["ImportedIntegerRoot"],
        )
    actual = {
        "values": values,
        "cache_identity_matches": (models is repeated) == (cache_size > 0),
        "target_order": list(targeted),
        "shared_root_identity_matches": (models["ZNumber"] is ImportedIntegerRoot) == use_aliases,
        "shared_root_module_unchanged": ImportedIntegerRoot.__module__ == original_module,
        "regular_module_matches": models["PlainObject"].__module__
        == (f"{module_name}.plain_object" if module_split_mode and module_name else module_name or "builtins")
        if module_split_mode is None or module_name
        else models["PlainObject"].__module__.startswith("_dcg_dynamic_"),
        "imported_target_error": str(error.value),
        "registered_module_restored": sys.modules.get(module_name or "") is registered_module,
    }
    assert_output(
        f"{json.dumps(actual, indent=2)}\n",
        expected / ("runtime_single.txt" if module_split_mode is None else "runtime_modules.txt"),
    )


@pytest.mark.parametrize("include_private", [False, True])
def test_private_root_model_aliases(include_private: bool) -> None:
    """Keep private alias naming consistent with ordinary dynamic models."""
    schema = json.loads((DATA_PATH / "private_root_model_alias.json").read_text(encoding="utf-8"))
    models = generate_dynamic_models(
        schema,
        config=GenerateConfig(
            use_root_model_type_alias=True,
            allow_leading_underscore_class_name=include_private,
            class_name="__ParsedModel",
            formatters=[Formatter.BUILTIN],
            disable_timestamp=True,
        ),
        cache_size=0,
    )
    assert_output(
        f"{json.dumps({name: model.model_validate(3).root for name, model in models.items()}, indent=2)}\n",
        EXPECTED_PATH / "root_model_aliases" / ("private.txt" if include_private else "normalized_private.txt"),
    )


@pytest.mark.parametrize("include_private", [False, True])
def test_root_model_alias_assignments_ignore_nested_and_attribute_targets(include_private: bool) -> None:
    """Extract top-level aliases while preserving private and non-model exclusions."""
    schema = json.loads((DATA_PATH / "private_root_model_alias.json").read_text(encoding="utf-8"))
    config = GenerateConfig(
        input_file_type=InputFileType.JsonSchema,
        use_root_model_type_alias=True,
        class_name="GeneratedRoot",
        allow_leading_underscore_class_name=include_private,
        disable_future_imports=True,
        custom_file_header_path=DATA_PATH / "root_model_alias_header.py",
        additional_imports=["tests.data.dynamic_models.root_model_alias_imports.ImportedIntegerRoot"],
        formatters=[Formatter.BUILTIN],
        disable_timestamp=True,
    )
    assert_output(generate(schema, config=config), EXPECTED_PATH / "root_model_aliases" / "header_code.py")
    models = generate_dynamic_models(schema, config=config, cache_size=0)
    actual = {
        "models": list(models),
        "header_root": models["HeaderRoot"].model_validate("header").root,
        "generated_root": models["GeneratedRoot"].model_validate(3).root,
    }
    assert_output(
        f"{json.dumps(actual, indent=2)}\n",
        EXPECTED_PATH / "root_model_aliases" / ("header_private.txt" if include_private else "header_public.txt"),
    )


def test_ordinary_type_alias_extraction_is_unchanged() -> None:
    """Plain typing aliases remain outside the dynamic model-class result."""
    schema = json.loads((DATA_PATH / "root_model_aliases.json").read_text(encoding="utf-8"))
    payloads = json.loads((DATA_PATH / "root_model_alias_payloads.json").read_text(encoding="utf-8"))
    config = GenerateConfig(
        input_file_type=InputFileType.JsonSchema,
        use_type_alias=True,
        formatters=[Formatter.BUILTIN],
        disable_timestamp=True,
    )
    assert_output(generate(schema, config=config), EXPECTED_PATH / "root_model_aliases" / "type_alias_code.py")
    models = generate_dynamic_models(schema, config=config, cache_size=0)
    actual = {
        "models": list(models),
        "validated": models["AliasModels"].model_validate(payloads["AliasModels"]).model_dump(mode="json"),
    }
    assert_output(f"{json.dumps(actual, indent=2)}\n", EXPECTED_PATH / "root_model_aliases" / "type_alias.txt")


@pytest.mark.parametrize("use_aliases", [False, True], ids=["classes", "aliases"])
@pytest.mark.parametrize("split_modules", [False, True])
def test_root_model_alias_cli_output_is_unchanged(tmp_path: Path, use_aliases: bool, split_modules: bool) -> None:
    """CLI code matches baseline Python bytes and validates through the same RootModel fields."""
    expected = EXPECTED_PATH / "root_model_aliases" / ("aliases" if use_aliases else "classes")
    output = tmp_path / ("models" if split_modules else "models.py")
    args = [
        "--input",
        str(DATA_PATH / "root_model_aliases.json"),
        "--input-file-type",
        "jsonschema",
        "--output",
        str(output),
        "--disable-timestamp",
        "--formatters",
        "builtin",
        "--additional-imports",
        (
            "pydantic.RootModel,datamodel_code_generator.enums.DataModelType,"
            "tests.data.dynamic_models.root_model_alias_imports.ImportedIntegerRoot,"
            "tests.data.dynamic_models.root_model_alias_imports.ImportedObject"
        ),
    ]
    if use_aliases:
        args.append("--use-root-model-type-alias")
    if split_modules:
        args.extend(["--module-split-mode", "single"])
    run_main_with_args(args)
    if split_modules:
        assert_directory_content(output, expected / "cli_modules")
    else:
        assert_output(output.read_text(encoding="utf-8"), expected / "cli_code.py")
        payloads = json.loads((DATA_PATH / "root_model_alias_payloads.json").read_text(encoding="utf-8"))
        assert_generated_model_json_validation(
            output,
            module_name="cli_root_model_aliases",
            model_name="AliasModels",
            valid_json=json.dumps(payloads["AliasModels"]),
            invalid_json="{}",
            expected_error_type="missing",
            expected_attribute_path=("number", "root"),
            expected_attribute_value=7,
        )


def test_circular_reference() -> None:
    """Test generating models with circular references."""
    schema: dict[str, Any] = {
        "$defs": {
            "Node": {
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                    "children": {"type": "array", "items": {"$ref": "#/$defs/Node"}},
                },
            },
        },
        "$ref": "#/$defs/Node",
    }
    assert_dynamic_models(
        schema,
        {
            "Node": {
                "value": "root",
                "children": [{"value": "child1", "children": []}, {"value": "child2", "children": []}],
            }
        },
        EXPECTED_PATH / "circular_reference.json",
    )


def test_allof_inheritance() -> None:
    """Test generating models with allOf inheritance."""
    schema: dict[str, Any] = {
        "$defs": {
            "Base": {"type": "object", "properties": {"id": {"type": "integer"}}},
            "Extended": {
                "allOf": [{"$ref": "#/$defs/Base"}, {"type": "object", "properties": {"name": {"type": "string"}}}]
            },
        },
        "$ref": "#/$defs/Extended",
    }
    assert_dynamic_models(schema, {"Extended": {"id": 42, "name": "test"}}, EXPECTED_PATH / "allof_inheritance.json")


def test_validation_error() -> None:
    """Test that validation errors are raised for invalid data."""
    schema = make_object_schema({"count": {"type": "integer"}}, required=["count"])
    models = generate_dynamic_models(schema)
    Model = models["Model"]

    with pytest.raises(pydantic.ValidationError):
        Model.model_validate({"count": "not_an_integer"})

    with pytest.raises(pydantic.ValidationError):
        Model.model_validate({})


def test_cache_hit() -> None:
    """Test that cached models are returned."""
    schema = make_object_schema({"name": {"type": "string"}})
    models1 = generate_dynamic_models(schema)
    models2 = generate_dynamic_models(schema)
    assert models1 is models2


def test_cache_miss_different_schema() -> None:
    """Test that different schemas create different models."""
    models1 = generate_dynamic_models(make_object_schema({"name": {"type": "string"}}))
    models2 = generate_dynamic_models(make_object_schema({"age": {"type": "integer"}}))
    assert models1 is not models2


def test_cache_miss_different_config() -> None:
    """Test that different configs create different cache entries."""
    schema = make_object_schema({"name": {"type": "string"}})
    models1 = generate_dynamic_models(schema, config=make_config(class_name="User"))
    models2 = generate_dynamic_models(schema, config=make_config(class_name="Person"))
    assert models1 is not models2
    assert sorted(models1.keys()) == ["User"]
    assert sorted(models2.keys()) == ["Person"]


@pytest.mark.parametrize(
    ("fixture_prefix", "payload_name", "expected_name"),
    [
        ("cache_field_order", "field", "cache_field_order.txt"),
        ("cache_nested_field_order", "nested", "cache_nested_field_order.txt"),
        ("cache_defs_model_order", "defs", "cache_defs_model_order.txt"),
        ("cache_openapi_components_order", "openapi", "cache_openapi_components_order.txt"),
    ],
)
def test_cache_distinguishes_schema_order(
    fixture_prefix: str,
    payload_name: str,
    expected_name: str,
) -> None:
    """Keep cached models aligned with both orders of order-sensitive schemas."""
    first_schema = json.loads((DATA_PATH / f"{fixture_prefix}_first.json").read_text(encoding="utf-8"))
    second_schema = json.loads((DATA_PATH / f"{fixture_prefix}_second.json").read_text(encoding="utf-8"))
    payloads = json.loads((DATA_PATH / "cache_order_payloads.json").read_text(encoding="utf-8"))[payload_name]
    config = GenerateConfig(
        disable_timestamp=True,
        formatters=[],
    )

    actual: dict[str, Any] = {}
    for order_name, initial_schema, followup_schema in (
        ("first_then_second", first_schema, second_schema),
        ("second_then_first", second_schema, first_schema),
    ):
        clear_dynamic_models_cache()
        initial_input = types.MappingProxyType(initial_schema)
        followup_input = types.MappingProxyType(followup_schema)
        initial_schema_json = json.dumps(initial_schema, separators=(",", ":"))
        followup_schema_json = json.dumps(followup_schema, separators=(",", ":"))
        with assert_inputs_not_mutated({"initial_schema": initial_schema, "followup_schema": followup_schema}):
            initial_models = generate_dynamic_models(initial_input, config=config)
            cached_followup_models = generate_dynamic_models(followup_input, config=config)
            cached_initial_models = generate_dynamic_models(initial_input, config=config)
            uncached_followup_models = generate_dynamic_models(followup_input, config=config, cache_size=0)
        actual[order_name] = {
            "input_mapping_types": [type(initial_input).__name__, type(followup_input).__name__],
            "input_mappings_unchanged": [
                json.dumps(initial_schema, separators=(",", ":")) == initial_schema_json,
                json.dumps(followup_schema, separators=(",", ":")) == followup_schema_json,
            ],
            "initial_cache_hit": initial_models is cached_initial_models,
            "followup_cache_miss": cached_followup_models is not initial_models,
            "initial_classes_reused": {
                name: initial_models[name] is cached_initial_models[name] for name in initial_models
            },
            "cached_followup_model_order": list(cached_followup_models),
            "uncached_followup_model_order": list(uncached_followup_models),
            "cached_followup_fields": {
                name: list(model.model_fields) for name, model in cached_followup_models.items()
            },
            "uncached_followup_fields": {
                name: list(model.model_fields) for name, model in uncached_followup_models.items()
            },
            "cached_followup_json": {
                name: cached_followup_models[name].model_validate(payload).model_dump_json()
                for name, payload in payloads.items()
            },
            "uncached_followup_json": {
                name: uncached_followup_models[name].model_validate(payload).model_dump_json()
                for name, payload in payloads.items()
            },
        }
    assert_output(f"{json.dumps(actual, indent=2)}\n", EXPECTED_PATH / expected_name)


def test_cache_hits_do_not_reorder_fifo_eviction() -> None:
    """Keep cache hits from changing which entry FIFO eviction removes."""
    schemas = [
        json.loads((DATA_PATH / f"cache_field_order_{name}.json").read_text(encoding="utf-8"))
        for name in ("first", "second", "third")
    ]
    first_models = generate_dynamic_models(schemas[0], cache_size=2)
    second_models = generate_dynamic_models(schemas[1], cache_size=2)
    first_hit_models = generate_dynamic_models(schemas[0], cache_size=2)
    generate_dynamic_models(schemas[2], cache_size=2)
    second_retained_models = generate_dynamic_models(schemas[1], cache_size=2)
    first_rebuilt_models = generate_dynamic_models(schemas[0], cache_size=2)

    actual = {
        "first_hit": first_hit_models is first_models,
        "second_retained": second_retained_models is second_models,
        "first_evicted": first_rebuilt_models is not first_models,
    }
    assert_output(f"{json.dumps(actual, indent=2)}\n", EXPECTED_PATH / "cache_fifo_order.txt")


def test_module_name_is_applied_for_same_schema_with_different_runtime_modules() -> None:
    """Test generated models keep the requested module name for identical schemas."""
    schema = make_object_schema({"name": {"type": "string"}}, required=["name"])

    runtime_model = generate_dynamic_models(schema, module_name="runtime_models")["Model"]
    other_runtime_model = generate_dynamic_models(schema, module_name="other_runtime_models")["Model"]

    assert runtime_model.__module__ == "runtime_models"
    assert runtime_model.model_validate({"name": "Alice"}).model_dump(mode="json") == {"name": "Alice"}
    assert other_runtime_model.__module__ == "other_runtime_models"
    assert other_runtime_model.model_validate({"name": "Bob"}).model_dump(mode="json") == {"name": "Bob"}


@pytest.mark.parametrize(
    ("module_name", "base_class"),
    [
        ("tests.data.dynamic_models.runtime_custom_base", "tests.data.dynamic_models.runtime_custom_base.CustomBase"),
        ("runtime_missing_package.models", None),
    ],
    ids=["importable", "missing-package"],
)
def test_module_name_of_importable_module(
    module_name: str, base_class: str | None, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Import a module that module_name names before the generated code imports from it, instead of masking it."""
    monkeypatch.delitem(sys.modules, module_name, raising=False)
    config = GenerateConfig(
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        **({"base_class": base_class} if base_class else {}),
    )
    model = generate_dynamic_models(
        make_object_schema({"name": {"type": "string"}}, required=["name"]),
        config=config,
        cache_size=0,
        module_name=module_name,
    )["Model"]
    actual = {
        "model_module": model.__module__,
        "bases": [f"{base.__module__}.{base.__qualname__}" for base in model.__bases__],
        "validated": model.model_validate({"name": "Alice"}).model_dump(mode="json"),
        "module_imported": module_name in sys.modules,
    }
    assert_output(
        f"{json.dumps(actual, indent=2)}\n",
        EXPECTED_PATH / f"module_name_{request.node.callspec.id}.txt",
    )


def test_target_model_names_filters_single_model() -> None:
    """Test filtering returned dynamic models to a single requested model."""
    schema = make_object_schema({"name": {"type": "string"}}, required=["name"])

    models = generate_dynamic_models(schema, config=make_config(class_name="User"), target_model_names=["User"])
    User = models["User"]

    assert list(models) == ["User"]
    assert User.__name__ == "User"
    assert User.model_validate({"name": "Alice"}).model_dump(mode="json") == {"name": "Alice"}


def test_target_model_names_with_module_name() -> None:
    """Test module_name is assigned when filtering returned dynamic models."""
    schema = make_object_schema({"name": {"type": "string"}}, required=["name"])

    models = generate_dynamic_models(
        schema,
        config=make_config(class_name="User"),
        module_name="runtime_models",
        target_model_names=["User"],
    )
    User = models["User"]

    assert User.__module__ == "runtime_models"
    assert User.model_validate({"name": "Alice"}).model_dump(mode="json") == {"name": "Alice"}


def test_target_model_names_filters_multiple_models() -> None:
    """Test filtering returned dynamic models to multiple requested models."""
    schema: dict[str, Any] = {
        "openapi": "3.0.0",
        "info": {"title": "User API", "version": "1.0.0"},
        "paths": {},
        "components": {
            "schemas": {
                "User": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
                "Order": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}, "user": {"$ref": "#/components/schemas/User"}},
                    "required": ["id", "user"],
                },
            }
        },
    }

    models = generate_dynamic_models(schema, target_model_names=["Order", "User"])

    assert list(models) == ["Order", "User"]
    User = models["User"]
    Order = models["Order"]
    assert User.__name__ == "User"
    assert User.model_validate({"name": "Alice"}).model_dump(mode="json") == {"name": "Alice"}
    assert Order.model_validate({"id": 1, "user": {"name": "Alice"}}).user.name == "Alice"


def test_target_model_names_does_not_reduce_cached_models() -> None:
    """Test target filtering does not cache a reduced model dictionary."""
    schema: dict[str, Any] = {
        "openapi": "3.0.0",
        "info": {"title": "User API", "version": "1.0.0"},
        "paths": {},
        "components": {
            "schemas": {
                "User": {"type": "object", "properties": {"name": {"type": "string"}}},
                "Order": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}, "user": {"$ref": "#/components/schemas/User"}},
                },
            }
        },
    }

    filtered_models = generate_dynamic_models(schema, target_model_names=["User"])
    all_models = generate_dynamic_models(schema)

    assert list(filtered_models) == ["User"]
    assert filtered_models["User"].model_validate({"name": "Alice"}).model_dump(mode="json") == {"name": "Alice"}
    assert sorted(all_models) == ["Order", "User"]
    assert all_models["Order"].model_validate({"id": 1, "user": {"name": "Alice"}}).user.name == "Alice"


def test_target_model_names_raises_for_missing_model() -> None:
    """Test target filtering gives available names when requested models are missing."""
    schema: dict[str, Any] = {
        "openapi": "3.0.0",
        "info": {"title": "User API", "version": "1.0.0"},
        "paths": {},
        "components": {
            "schemas": {
                "User": {"type": "object", "properties": {"name": {"type": "string"}}},
                "Order": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}, "user": {"$ref": "#/components/schemas/User"}},
                },
            }
        },
    }

    with pytest.raises(
        Error,
        match=r"target_model_names: 'Missing'.*Available models: Order, User",
    ):
        generate_dynamic_models(schema, target_model_names=["Missing"])


def test_target_model_names_rejects_empty_sequence() -> None:
    """Test target filtering rejects an empty target model name sequence."""
    schema = make_object_schema({"name": {"type": "string"}})

    with pytest.raises(Error, match="target_model_names must contain at least one model name"):
        generate_dynamic_models(schema, target_model_names=[])


def test_target_model_names_rejects_string() -> None:
    """Test target filtering rejects a string instead of a model name sequence."""
    schema = make_object_schema({"name": {"type": "string"}})

    with pytest.raises(Error, match="target_model_names must be a sequence of model names"):
        generate_dynamic_models(schema, target_model_names="Model")  # type: ignore[arg-type]


def test_target_model_names_rejects_invalid_names() -> None:
    """Test target filtering rejects empty and non-string model names."""
    schema = make_object_schema({"name": {"type": "string"}})

    with pytest.raises(Error, match=r"target_model_names contains invalid model names: '', 1"):
        generate_dynamic_models(schema, target_model_names=["", 1])  # type: ignore[list-item]


def test_cache_disabled() -> None:
    """Test that caching can be disabled."""
    schema = make_object_schema({"name": {"type": "string"}})
    models1 = generate_dynamic_models(schema, cache_size=0)
    models2 = generate_dynamic_models(schema, cache_size=0)
    assert models1 is not models2


def test_cache_eviction() -> None:
    """Test that old entries are evicted when cache is full."""
    for i in range(5):
        generate_dynamic_models(make_object_schema({f"field{i}": {"type": "string"}}), cache_size=3)
    assert clear_dynamic_models_cache() == 3


def test_cache_shrinks_when_smaller_size_requested() -> None:
    """Test that cache shrinks when a smaller cache_size is used."""
    schemas = [make_object_schema({f"field{i}": {"type": "string"}}) for i in range(5)]
    for schema in schemas:
        generate_dynamic_models(schema, cache_size=10)
    assert clear_dynamic_models_cache() == 5

    for schema in schemas:
        generate_dynamic_models(schema, cache_size=10)
    generate_dynamic_models(make_object_schema({"new_field": {"type": "string"}}), cache_size=2)
    assert clear_dynamic_models_cache() == 2


def test_cache_hit_shrinks_when_smaller_size_requested() -> None:
    """Test that a cache hit still enforces a smaller cache_size."""
    schemas = [make_object_schema({f"field{i}": {"type": "string"}}) for i in range(5)]
    cached_models = [generate_dynamic_models(schema, cache_size=10) for schema in schemas]

    assert generate_dynamic_models(schemas[-1], cache_size=2) is cached_models[-1]
    assert clear_dynamic_models_cache() == 2


def test_clear_cache() -> None:
    """Test clearing the cache."""
    generate_dynamic_models(make_object_schema({"name": {"type": "string"}}))
    assert clear_dynamic_models_cache() == 1
    assert clear_dynamic_models_cache() == 0


def test_concurrent_same_schema() -> None:
    """Test concurrent access with the same schema."""
    schema = make_object_schema({"name": {"type": "string"}})
    results: list[dict[str, type]] = []

    def worker() -> None:
        results.append(generate_dynamic_models(schema))

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 10
    assert all(r is results[0] for r in results)


def test_concurrent_different_schemas() -> None:
    """Test concurrent access with different schemas."""
    schemas = [make_object_schema({f"field{i}": {"type": "string"}}) for i in range(5)]
    results: list[dict[str, type]] = []

    def worker(schema: dict[str, Any]) -> None:
        results.append(generate_dynamic_models(schema))

    with ThreadPoolExecutor(max_workers=5) as executor:
        executor.map(worker, schemas)

    assert len(results) == 5


def test_numeric_constraints() -> None:
    """Test models with numeric constraints validate properly."""
    schema = make_object_schema({"age": {"type": "integer", "minimum": 0, "maximum": 150}})
    assert_dynamic_models(schema, {"Model": {"age": 30}}, EXPECTED_PATH / "numeric_constraints.json")

    models = generate_dynamic_models(schema)
    with pytest.raises(pydantic.ValidationError):
        models["Model"].model_validate({"age": -1})
    with pytest.raises(pydantic.ValidationError):
        models["Model"].model_validate({"age": 200})


def test_string_constraints() -> None:
    """Test models with string constraints validate properly."""
    schema = make_object_schema({"email": {"type": "string", "pattern": r"^[\w\.-]+@[\w\.-]+\.\w+$"}})
    assert_dynamic_models(schema, {"Model": {"email": "test@example.com"}}, EXPECTED_PATH / "string_constraints.json")

    models = generate_dynamic_models(schema)
    with pytest.raises(pydantic.ValidationError):
        models["Model"].model_validate({"email": "invalid-email"})


def test_explicit_input_file_type() -> None:
    """Test passing explicit input_file_type via config."""
    schema = make_object_schema({"name": {"type": "string"}})
    models = generate_dynamic_models(schema, config=make_config())
    assert sorted(models.keys()) == ["Model"]


def test_openapi_auto_detection() -> None:
    """Test that OpenAPI schemas are auto-detected and models work."""
    with (DATA_PATH / "openapi_schema.json").open() as f:
        openapi_schema = json.load(f)
    assert_dynamic_models(
        openapi_schema, {"User": {"id": 1, "name": "Alice"}}, EXPECTED_PATH / "openapi_auto_detection.json"
    )


def test_force_optional_discriminator_literals() -> None:
    """Keep force-optional discriminator literals valid in dynamic Pydantic v2 models."""
    schema = load_yaml_dict_from_path(OPEN_API_DATA_PATH / "discriminator_force_optional.yaml", "utf-8")
    config = GenerateConfig(
        input_file_type=InputFileType.OpenAPI,
        output_model_type=DataModelType.PydanticV2BaseModel,
        force_optional_for_required_fields=True,
    )
    assert_dynamic_models(
        schema,
        {
            "CardPayment": {},
            "CashPayment": {},
            "Payment": {"kind": "cash", "received_amount": 50},
        },
        EXPECTED_PATH / "force_optional_discriminator_literals.json",
        config=config,
    )


def test_config_with_auto_input_type() -> None:
    """Test that input_file_type=Auto in config is auto-detected."""
    schema = make_object_schema({"name": {"type": "string"}})
    models = generate_dynamic_models(schema, config=GenerateConfig(class_name="User"))
    assert sorted(models.keys()) == ["User"]


def test_leading_underscore_class_name_default_unchanged() -> None:
    """Test leading underscore class names are normalized by default."""
    schema = make_object_schema({"name": {"type": "string"}})
    config = GenerateConfig(
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        class_name="__ParsedModel",
    )
    assert_dynamic_models(
        schema,
        {"FieldParsedModel": {"name": "Alice"}},
        EXPECTED_PATH / "leading_underscore_class_name_default_unchanged.json",
        config=config,
        include_model_names=True,
    )


def test_allow_leading_underscore_class_name() -> None:
    """Test dynamic models return explicitly allowed leading underscore class names."""
    schema = make_object_schema({"name": {"type": "string"}})
    config = GenerateConfig(
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        class_name="__ParsedModel",
        allow_leading_underscore_class_name=True,
    )
    assert_dynamic_models(
        schema,
        {"__ParsedModel": {"name": "Alice"}},
        EXPECTED_PATH / "allow_leading_underscore_class_name.json",
        config=config,
        include_model_names=True,
    )


def test_non_serializable_schema_skips_cache() -> None:
    """Test that non-JSON-serializable schemas skip caching."""
    from datamodel_code_generator.dynamic import _make_cache_key

    schema: dict[str, Any] = {"type": "object", "properties": {"name": {"type": "string"}}, "custom": object()}
    assert _make_cache_key(schema, make_config()) is None


def test_dynamic_cache_key_does_not_retain_schema_payload() -> None:
    """Test cache keys keep digests instead of the original schema text."""
    from datamodel_code_generator import dynamic as dcg

    unique_description = "schema-payload-marker-that-should-not-be-in-cache-key"
    schema = make_object_schema({"name": {"type": "string", "description": unique_description}})

    generate_dynamic_models(schema)

    assert len(dcg._dynamic_models_cache) == 1
    cache_key = next(iter(dcg._dynamic_models_cache))
    assert unique_description not in cache_key
    assert len(cache_key) < 180


def test_cache_hit_inside_lock() -> None:
    """Test cache hit after acquiring lock (double-checked locking)."""
    from datamodel_code_generator import dynamic as dcg
    from datamodel_code_generator.dynamic import _make_cache_key

    schema = make_object_schema({"name": {"type": "string"}})
    original_lock = dcg._dynamic_models_lock
    cached_models: dict[str, type] = {"Model": type("Model", (), {})}
    cache_populated, lock_acquired = threading.Event(), threading.Event()
    result_holder: list[dict[str, type]] = []
    cache_key: str | None = None

    class InstrumentedLock:
        def __enter__(self) -> None:
            lock_acquired.set()
            cache_populated.wait(timeout=5)
            original_lock.__enter__()

        def __exit__(self, *args: object) -> None:
            original_lock.__exit__(*args)

    dcg._dynamic_models_lock = InstrumentedLock()  # type: ignore[assignment]

    try:
        thread = threading.Thread(target=lambda: result_holder.append(generate_dynamic_models(schema)))
        thread.start()
        lock_acquired.wait(timeout=5)

        cache_key = _make_cache_key(schema, make_config())
        assert cache_key is not None
        dcg._dynamic_models_cache[cache_key] = cached_models

        cache_populated.set()
        thread.join(timeout=10)

        assert len(result_holder) == 1
        assert result_holder[0] is cached_models
    finally:
        cache_populated.set()
        dcg._dynamic_models_lock = original_lock
        dcg._dynamic_models_cache.pop(cache_key, None)  # type: ignore[arg-type]


def test_multi_module_output() -> None:
    """Test generating models with multi-module output (module_split_mode=Single)."""
    schema: dict[str, Any] = {
        "$defs": {
            "User": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
                "required": ["name"],
            },
            "Order": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "user": {"$ref": "#/$defs/User"}},
                "required": ["id"],
            },
        },
        "$ref": "#/$defs/Order",
    }
    config = make_config(module_split_mode=ModuleSplitMode.Single)
    assert_dynamic_models(
        schema,
        {"User": {"name": "Alice", "age": 25}, "Order": {"id": 1, "user": {"name": "Bob", "age": 30}}},
        EXPECTED_PATH / "multi_module_output.json",
        config=config,
    )
    assert_output(
        f"{json.dumps({'models': list(generate_dynamic_models(schema, config=config))}, indent=2)}\n",
        EXPECTED_PATH / "multi_module_model_order.txt",
    )


def test_multi_module_relative_parent_import(tmp_path: Path) -> None:
    """Resolve parent-package imports before executing generated modules."""
    schema = json.loads((DATA_PATH / "relative_parent_import.json").read_text(encoding="utf-8"))
    payload_path = DATA_PATH / "relative_parent_import_payload.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    config = GenerateConfig(
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        module_split_mode=ModuleSplitMode.Single,
        all_exports_scope=AllExportsScope.Recursive,
        all_exports_collision_strategy=AllExportsCollisionStrategy.FullPrefix,
        disable_timestamp=True,
        formatters=[],
    )
    modules = cast("dict[tuple[str, ...], str]", generate(schema, config=config))
    assert_generated_modules_output(
        modules,
        EXPECTED_PATH / "relative_parent_import_modules",
        transform=lambda output: f"{output}\n",
    )

    package_name = "runtime_relative_parent_import"
    models = generate_dynamic_models(schema, config=config, cache_size=0, module_name=package_name)
    actual = {
        "models": list(models),
        "model_module": models["Model"].__module__,
        "validated": models["Model"].model_validate(payload).model_dump(mode="json"),
        "modules_restored": not any(
            name == package_name or name.startswith(f"{package_name}.") for name in sys.modules
        ),
    }
    assert_output(f"{json.dumps(actual, indent=2)}\n", EXPECTED_PATH / "relative_parent_import.txt")
    dynamic_payload = f"{json.dumps(actual['validated'], indent=2)}\n"
    assert_output(dynamic_payload, EXPECTED_PATH / "relative_parent_import_payload.txt")

    output_path = tmp_path / "cli_generated"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "datamodel_code_generator",
            "--input",
            str(DATA_PATH / "relative_parent_import.json"),
            "--output",
            str(output_path),
            "--input-file-type",
            "jsonschema",
            "--output-model-type",
            DataModelType.PydanticV2BaseModel.value,
            "--module-split-mode",
            ModuleSplitMode.Single.value,
            "--all-exports-scope",
            AllExportsScope.Recursive.value,
            "--all-exports-collision-strategy",
            AllExportsCollisionStrategy.FullPrefix.value,
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ],
        check=True,
        text=True,
    )
    assert_directory_content(output_path, EXPECTED_PATH / "relative_parent_import_cli_modules")

    cli_import_result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; "
                "from pathlib import Path; "
                "from cli_generated.model import Model; "
                f"payload = json.loads(Path({str(payload_path)!r}).read_text(encoding='utf-8')); "
                "print(json.dumps(Model.model_validate(payload).model_dump(mode='json'), indent=2))"
            ),
        ],
        capture_output=True,
        check=True,
        cwd=tmp_path,
        text=True,
    )
    assert_output(cli_import_result.stdout, EXPECTED_PATH / "relative_parent_import_payload.txt")


def test_multi_module_restores_preexisting_modules_after_execution_failure() -> None:
    """Restore every user module when dynamic execution succeeds or raises."""
    schema = json.loads((DATA_PATH / "relative_parent_import.json").read_text(encoding="utf-8"))
    config = GenerateConfig(
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        module_split_mode=ModuleSplitMode.Single,
        all_exports_scope=AllExportsScope.Recursive,
        all_exports_collision_strategy=AllExportsCollisionStrategy.FullPrefix,
        disable_timestamp=True,
        formatters=[],
    )
    package_name = "runtime_nested_module_restore"
    module_names = (
        package_name,
        f"{package_name}.left",
        f"{package_name}.left.shared",
        f"{package_name}.right",
        f"{package_name}.right.deep",
        f"{package_name}.right.deep.consumer",
        f"{package_name}.right.shared",
        f"{package_name}.model",
    )
    previous_modules = {name: types.ModuleType(name) for name in module_names}
    sys.modules.update(previous_modules)
    try:
        generate_dynamic_models(schema, config=config, cache_size=0, module_name=package_name)
        success_restored = {
            name: module
            for name, module in sys.modules.items()
            if name == package_name or name.startswith(f"{package_name}.")
        } == previous_modules and all(sys.modules[name] is module for name, module in previous_modules.items())

        failing_config = config.model_copy(update={"additional_imports": ["missing.module"]})
        with pytest.raises(ModuleNotFoundError, match="No module named 'missing'"):
            generate_dynamic_models(schema, config=failing_config, cache_size=0, module_name=package_name)
        failure_restored = {
            name: module
            for name, module in sys.modules.items()
            if name == package_name or name.startswith(f"{package_name}.")
        } == previous_modules and all(sys.modules[name] is module for name, module in previous_modules.items())

        actual = {
            "success_restored": success_restored,
            "failure_restored": failure_restored,
        }
        assert_output(f"{json.dumps(actual, indent=2)}\n", EXPECTED_PATH / "nested_module_restoration.txt")
    finally:
        for name in module_names:
            sys.modules.pop(name, None)


def test_multi_module_output_with_module_name() -> None:
    """Test module_name is assigned to generated multi-module classes."""
    schema: dict[str, Any] = {
        "$defs": {
            "User": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            "Order": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "user": {"$ref": "#/$defs/User"}},
                "required": ["id"],
            },
        },
        "$ref": "#/$defs/Order",
    }

    models = generate_dynamic_models(
        schema,
        config=make_config(module_split_mode=ModuleSplitMode.Single),
        module_name="runtime_package",
    )

    assert models["User"].__module__.startswith("runtime_package")
    assert models["Order"].__module__.startswith("runtime_package")
    assert models["Order"].model_validate({"id": 1, "user": {"name": "Alice"}}).user.name == "Alice"


def test_generated_code_matches_expected() -> None:
    """Test that generate() produces expected code for a complex schema."""
    schema: dict[str, Any] = {
        "$defs": {
            "Address": {
                "type": "object",
                "properties": {"street": {"type": "string"}, "city": {"type": "string"}},
                "required": ["street", "city"],
            },
            "Person": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "address": {"$ref": "#/$defs/Address"},
                },
                "required": ["name"],
            },
        },
        "$ref": "#/$defs/Person",
    }
    config = GenerateConfig(
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
    )
    result = generate(input_=schema, config=config)
    assert isinstance(result, str)
    assert_output(result, EXPECTED_PATH / "complex_schema.py")

    assert_dynamic_models(
        schema,
        {
            "Person": {"name": "John", "address": {"street": "123 Main St", "city": "NYC"}},
            "Address": {"street": "456 Oak Ave", "city": "LA"},
        },
        EXPECTED_PATH / "generated_code_validation.json",
    )


def test_build_module_edges_ignores_unresolvable_relative_imports() -> None:
    """Ignore invalid relative imports while building generated-module dependencies."""
    from datamodel_code_generator.dynamic import _build_module_edges

    fixture = json.loads((DATA_PATH / "relative_import_edge_cases.json").read_text(encoding="utf-8"))
    modules = {tuple(path.split("/")): code for path, code in fixture["modules"].items()}
    edges = _build_module_edges(modules)
    actual = {
        "/".join(path): ["/".join(dependency) for dependency in sorted(dependencies)]
        for path, dependencies in edges.items()
    }
    assert_output(f"{json.dumps(actual, indent=2)}\n", EXPECTED_PATH / "relative_import_edge_cases.txt")


def test_build_module_edges_no_matching_import() -> None:
    """Test _build_module_edges when import doesn't match any module."""
    from datamodel_code_generator.dynamic import _build_module_edges

    modules = {
        ("user.py",): "class User: pass",
        ("order.py",): "from .nonexistent import Something\nclass Order: pass",
    }
    edges = _build_module_edges(modules)
    assert edges["user.py",] == set()
    assert edges["order.py",] == set()


def test_execute_multi_module_without_init() -> None:
    """Test _execute_multi_module without __init__.py to cover package registration branch."""
    from datamodel_code_generator.dynamic import _execute_multi_module

    modules = {
        ("user.py",): "from pydantic import BaseModel\n\nclass User(BaseModel):\n    name: str",
    }
    models = _execute_multi_module(modules)
    assert "User" in models
    user = models["User"](name="Alice")
    assert user.name == "Alice"


def test_multi_module_output_restores_existing_module_name() -> None:
    """Test dynamic generation restores an existing user-provided module name."""
    package_name = "runtime_package_restore"
    user_module_name = f"{package_name}.user"
    original_user_module = types.ModuleType(user_module_name)
    sys.modules[user_module_name] = original_user_module

    schema: dict[str, Any] = {
        "$defs": {
            "User": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            }
        },
        "$ref": "#/$defs/User",
    }

    try:
        models = generate_dynamic_models(
            schema,
            config=make_config(module_split_mode=ModuleSplitMode.Single),
            module_name=package_name,
            cache_size=0,
        )

        assert "User" in models
        assert models["User"].__module__ == f"{package_name}.user"
        assert models["User"].model_validate({"name": "Alice"}).name == "Alice"
        assert package_name not in sys.modules
        assert sys.modules[user_module_name] is original_user_module
    finally:
        sys.modules.pop(package_name, None)
        sys.modules.pop(user_module_name, None)


def test_execute_multi_module_no_models() -> None:
    """Test _execute_multi_module with code that has no models."""
    from datamodel_code_generator.dynamic import _execute_multi_module

    modules = {
        ("utils.py",): "def helper(): pass",
    }
    models = _execute_multi_module(modules)
    assert models == {}


def test_multiple_aliases_required_field() -> None:
    """Test dynamic models with multiple aliases on required fields. (#2989)."""
    schema = make_object_schema({"name": {"type": "string"}}, required=["name"])
    config = GenerateConfig(
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        aliases={"name": ["full_name", "customer_name"]},
        class_name="Customer",
    )
    assert_dynamic_models(
        schema,
        {"Customer": {"name": "John"}},
        EXPECTED_PATH / "multiple_aliases_required.json",
        config=config,
    )


def test_multiple_aliases_required_field_code_output() -> None:
    """Test generated code includes Field import with multiple aliases on required fields. (#2989)."""
    schema = make_object_schema({"name": {"type": "string"}}, required=["name"])
    config = GenerateConfig(
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        aliases={"name": ["full_name", "customer_name"]},
        class_name="Customer",
    )
    result = generate(input_=schema, config=config)
    assert isinstance(result, str)
    assert_output(result, EXPECTED_PATH / "multiple_aliases_required_code.py")


def test_execute_multi_module_enum_only() -> None:
    """Test _execute_multi_module with enum only to cover non-BaseModel branch."""
    from datamodel_code_generator.dynamic import _execute_multi_module

    modules = {
        (
            "status.py",
        ): "from enum import Enum\n\nclass Status(Enum):\n    ACTIVE = 'active'\n    INACTIVE = 'inactive'",
    }
    models = _execute_multi_module(modules)
    assert "Status" in models
    assert models["Status"].ACTIVE.value == "active"
