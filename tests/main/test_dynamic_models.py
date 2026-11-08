"""Tests for generate_dynamic_models function."""

from __future__ import annotations

import gc
import importlib
import importlib.util
import json
import os
import pickle
import re
import subprocess
import sys
import threading
import types
import weakref
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pydantic
import pytest

from datamodel_code_generator import (
    AllExportsCollisionStrategy,
    AllExportsScope,
    DataModelType,
    Error,
    InputFileType,
    LiteralType,
    OpenAPIScope,
    ReadOnlyWriteOnlyModelType,
    clear_dynamic_models_cache,
    generate,
    generate_dynamic_models,
    generate_dynamic_module,
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
    from collections.abc import Iterator
    from typing import Any

    from datamodel_code_generator.dynamic import DynamicModule


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
    assert_output(actual, expected_path)


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


def test_optional_literal_discriminator_tags() -> None:
    """Accept payloads without an optional one-value Literal tag, which Pydantic models fill with the tag value."""
    schema = load_yaml_dict_from_path(OPEN_API_DATA_PATH / "discriminator_optional_literal.yaml", "utf-8")
    config = GenerateConfig(
        input_file_type=InputFileType.OpenAPI,
        output_model_type=DataModelType.PydanticV2BaseModel,
        enum_field_as_literal=LiteralType.One,
    )
    assert_dynamic_models(
        schema,
        {
            "Track": {"name": "a"},
            "Chapter": {},
            "CardPayment": {},
            "Queue": {"current": {"type": "track", "name": "a"}},
            "Payment": {"kind": "voucher", "code": "x"},
        },
        EXPECTED_PATH / "optional_literal_discriminator_tags.json",
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


def test_dynamic_cache_key_does_not_depend_on_hash_seed() -> None:
    """Test the cache key sorts set-valued settings, so processes with other hash seeds compute the same key."""
    from datamodel_code_generator.dynamic import _make_cache_key

    schema = make_object_schema({"name": {"type": "string"}})
    extra_keys = {f"x-key-{index}" for index in range(20)}
    script = (
        "from datamodel_code_generator.config import GenerateConfig, _rebuild_generate_config\n"
        "from datamodel_code_generator.dynamic import _make_cache_key\n"
        "_rebuild_generate_config()\n"
        f"print(_make_cache_key({schema!r}, GenerateConfig(field_extra_keys={extra_keys!r})))\n"
    )
    keys = {
        subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
            text=True,
        ).stdout.strip()
        for seed in ("1", "2", "3")
    }

    assert keys == {_make_cache_key(schema, GenerateConfig(field_extra_keys=extra_keys))}


def test_cache_hit_inside_lock() -> None:
    """Test a cache hit when the lock is taken again after the lookups made outside it (double-checked locking)."""
    from datamodel_code_generator import dynamic as dcg
    from datamodel_code_generator.dynamic import _make_cache_key

    schema = make_object_schema({"name": {"type": "string"}})
    original_lock = dcg._dynamic_models_lock
    cached_models: dict[str, type] = {"Model": type("Model", (), {})}
    cache_populated, lock_acquired = threading.Event(), threading.Event()
    result_holder: list[dict[str, type]] = []
    cache_key: str | None = None
    entries: list[None] = []

    class InstrumentedLock:
        def __enter__(self) -> None:
            entries.append(None)
            if len(entries) == 2:
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


def describe_source_refs(module: DynamicModule) -> dict[str, object]:
    """Describe the root class and the class of each schema location of a dynamic module by the classes' names."""
    return {
        "root_class": None if module.root_class is None else module.root_class.__name__,
        "models_by_source_ref": {
            source_ref: model.__name__ for source_ref, model in module.models_by_source_ref.items()
        },
    }


def test_dynamic_module_root_class_and_source_refs() -> None:
    """Test the root class is the class of the document, not the definition whose name the title asks for."""
    schema = json.loads((DATA_PATH / "source_refs.json").read_text(encoding="utf-8"))
    module = generate_dynamic_module(schema)
    assert module.root_class is not None
    payload = {"root": {"name": "Alice"}, "tags": [{"label": "x"}], "status": "active", "owner": {"id": 1}}
    actual = {
        **describe_source_refs(module),
        "models": list(module.models),
        "module_name": module.module_name,
        "payload": cast("type[pydantic.BaseModel]", module.root_class).model_validate(payload).model_dump(mode="json"),
    }
    assert_output(f"{json.dumps(actual, indent=2)}\n")


DYNAMIC_MODULE_SOURCE_REF_CASES: dict[str, tuple[dict[str, Any] | Path, dict[str, Any]]] = {
    "nullable_enum_root": ({"title": "Level", "type": "string", "enum": ["low", "high", None]}, {}),
    "allof_union_members": (
        {
            "title": "Event",
            "allOf": [
                {
                    "type": "object",
                    "required": ["id"],
                    "properties": {"id": {"type": "string"}, "color": {"type": "string", "enum": ["red", None]}},
                },
                {
                    "oneOf": [
                        {"type": "object", "required": ["kind"], "properties": {"kind": {"const": "created"}}},
                        {"type": "object", "required": ["kind"], "properties": {"kind": {"const": "deleted"}}},
                    ]
                },
            ],
        },
        {},
    ),
    "generated_model_paths": (JSON_SCHEMA_DATA_PATH / "all_of_any_of_base_class_ref.json", {}),
    "id_and_anchor_targets": (
        {
            "title": "Doc",
            "type": "object",
            "properties": {"user": {"$ref": "https://example.com/user"}, "address": {"$ref": "#address"}},
            "$defs": {
                "User": {"$id": "https://example.com/user", "type": "object", "properties": {"x": {"type": "string"}}},
                "Address": {"$anchor": "address", "type": "object", "properties": {"y": {"type": "string"}}},
            },
        },
        {},
    ),
    "skip_root_model": (DATA_PATH / "source_refs.json", {"skip_root_model": True}),
    "type_alias_root": ({"title": "Names", "type": "array", "items": {"type": "string"}}, {"use_type_alias": True}),
    "type_alias_allof_union_root": (
        {
            "title": "Event",
            "allOf": [
                {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}},
                {
                    "oneOf": [
                        {"type": "object", "required": ["kind"], "properties": {"kind": {"const": "created"}}},
                        {"type": "object", "required": ["kind"], "properties": {"kind": {"const": "deleted"}}},
                    ]
                },
            ],
        },
        {"use_type_alias": True},
    ),
    "type_alias_allof_union_component": (
        {
            "openapi": "3.0.0",
            "info": {"title": "Events", "version": "1.0.0"},
            "paths": {},
            "components": {
                "schemas": {
                    "Ev": {
                        "allOf": [
                            {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}},
                            {
                                "oneOf": [
                                    {"type": "object", "required": ["kind"], "properties": {"kind": {"const": "new"}}},
                                    {"type": "object", "properties": {"at": {"type": "string"}}},
                                ]
                            },
                        ]
                    },
                    "Created": {"type": "object", "properties": {"at": {"type": "string"}}},
                }
            },
        },
        {"use_type_alias": True},
    ),
    "leading_underscore_class_name": (make_object_schema({"name": {"type": "string"}}), {"class_name": "__Parsed"}),
    "allowed_leading_underscore_class_name": (
        make_object_schema({"name": {"type": "string"}}),
        {"class_name": "__Parsed", "allow_leading_underscore_class_name": True},
    ),
    "openapi_request_body": (
        {
            "openapi": "3.0.0",
            "info": {"title": "Pets", "version": "1.0.0"},
            "paths": {
                "/pets": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"owner": {"$ref": "#/components/schemas/Owner"}},
                                    }
                                }
                            }
                        },
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
            "components": {"schemas": {"Owner": {"type": "object", "properties": {"id": {"type": "integer"}}}}},
        },
        {"openapi_scopes": [OpenAPIScope.Schemas, OpenAPIScope.Paths]},
    ),
    "read_only_write_only_request_response": (
        OPEN_API_DATA_PATH / "read_only_write_only_mixed.yaml",
        {"read_only_write_only_model_type": ReadOnlyWriteOnlyModelType.RequestResponse},
    ),
    "read_only_write_only_all": (
        OPEN_API_DATA_PATH / "read_only_write_only_mixed.yaml",
        {"read_only_write_only_model_type": ReadOnlyWriteOnlyModelType.All},
    ),
    "reuse_model_subclass": (JSON_SCHEMA_DATA_PATH / "reuse_model_subclass_metadata.json", {"reuse_model": True}),
}


@pytest.mark.parametrize(
    ("schema", "options"), DYNAMIC_MODULE_SOURCE_REF_CASES.values(), ids=DYNAMIC_MODULE_SOURCE_REF_CASES.keys()
)
def test_dynamic_module_source_refs(schema: dict[str, Any] | Path, options: dict[str, Any]) -> None:
    """Test a location shared by several classes keeps the one parsed there, and keys only locations in documents."""
    if isinstance(schema, Path):
        schema = load_yaml_dict_from_path(schema, "utf-8")
    module = generate_dynamic_module(schema, config=GenerateConfig(**options))
    assert set(module.models_by_source_ref.values()) <= set(module.models.values())
    assert_output(f"{json.dumps(describe_source_refs(module), indent=2)}\n")


def test_dynamic_module_source_refs_of_referenced_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test the classes of files that references read are keyed by the file path and the pointer in the file."""
    monkeypatch.chdir(DATA_PATH / "source_ref_files")
    schema = json.loads(Path("schema.json").read_text(encoding="utf-8"))
    assert_output(f"{json.dumps(describe_source_refs(generate_dynamic_module(schema)), indent=2)}\n")


def test_dynamic_module_source_refs_of_split_output() -> None:
    """Test split output keys the classes that share a name in different modules, which models holds one of."""
    schema = json.loads((JSON_SCHEMA_DATA_PATH / "nested_person.json").read_text(encoding="utf-8"))
    module = generate_dynamic_module(schema, module_name="runtime_source_refs")
    assert list(module.models) == list(generate_dynamic_models(schema, module_name="runtime_source_refs"))
    actual = {
        "module_name": module.module_name,
        "models_by_source_ref": {
            source_ref: f"{model.__module__}.{model.__name__}"
            for source_ref, model in module.models_by_source_ref.items()
        },
    }
    assert_output(f"{json.dumps(actual, indent=2)}\n")


def test_dynamic_module_caches_entries_of_its_own() -> None:
    """Test the module function shares the cache, its size and its clearing, but not the entries of model calls."""
    schema = make_object_schema({"name": {"type": "string"}})
    other_schema = make_object_schema({"age": {"type": "integer"}})
    module = generate_dynamic_module(schema)

    assert generate_dynamic_module(schema) is module
    assert generate_dynamic_module(schema, cache_size=0) is not module
    assert module.models is not generate_dynamic_models(schema)
    assert module.models["Model"] is not generate_dynamic_models(schema)["Model"]
    assert clear_dynamic_models_cache() == 2
    module = generate_dynamic_module(schema, cache_size=1)
    generate_dynamic_models(other_schema, cache_size=1)
    assert generate_dynamic_module(schema, cache_size=1) is not module
    assert clear_dynamic_models_cache() == 1


REGISTERED_SCHEMA: dict[str, Any] = {
    "title": "Doc",
    "type": "object",
    "properties": {
        "child": {"$ref": "#/$defs/Child"},
        "mode": {"enum": ["a", "b"]},
        "names": {"$ref": "#/$defs/Names"},
    },
    "$defs": {
        "Child": {"type": "object", "properties": {"x": {"type": "integer"}}},
        "Names": {"type": "array", "items": {"type": "string"}},
    },
}
REGISTERED_PICKLE_SCRIPT = """\
import pickle
import sys

from datamodel_code_generator import GenerateConfig, generate_dynamic_module

mode, path = sys.argv[1:]
if mode == "load":
    try:
        pickle.loads(open(path, "rb").read())
    except ModuleNotFoundError as error:
        print(type(error).__name__, str(error).replace(error.name, "<module>"))
module = generate_dynamic_module(
    {schema!r}, config=GenerateConfig(field_extra_keys={extra_keys!r}), register_module=True
)
models = module.models
if mode == "dump":
    doc = module.root_class(child={{"x": 1}}, mode="a", names=["q"])
    open(path, "wb").write(pickle.dumps([doc, models["Mode"].a, models["Names"](["z"]), models["Doc"]]))
else:
    doc, member, names, doc_class = pickle.loads(open(path, "rb").read())
    print(type(doc) is models["Doc"], type(doc.child) is models["Child"], member is models["Mode"].a)
    print(type(names) is models["Names"], doc_class is models["Doc"], doc.model_dump(mode="json"))
print(module.module_name)
"""


@pytest.fixture
def forget_registered_modules() -> Iterator[None]:
    """Remove the modules a test registers from sys.modules, as programs remove a registered module."""
    before = set(sys.modules)
    yield
    for name in {name for name in sys.modules if name.startswith(("_dcg_dynamic_", "runtime_registered"))} - before:
        del sys.modules[name]


def test_registered_module_pickles_load_in_another_process(tmp_path: Path) -> None:
    """Test a process that runs the same generation loads the pickles of registered classes and instances."""
    pickle_path = tmp_path / "models.pickle"
    script = REGISTERED_PICKLE_SCRIPT.format(schema=REGISTERED_SCHEMA, extra_keys={f"x-key-{n}" for n in range(9)})

    def run(mode: str, seed: str) -> list[str]:
        return subprocess.run(
            [sys.executable, "-c", script, mode, str(pickle_path)],
            capture_output=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
            text=True,
        ).stdout.splitlines()

    dumped = run("dump", "1")
    loaded = run("load", "2")

    assert loaded == [
        "ModuleNotFoundError No module named '<module>'",
        "True True True",
        "True True {'child': {'x': 1}, 'mode': 'a', 'names': ['q']}",
        *dumped,
    ]
    assert dumped[0].startswith("_dcg_dynamic_")


@pytest.mark.usefixtures("forget_registered_modules")
def test_registered_module_stays_in_sys_modules() -> None:
    """Test a registered module stays importable and gives its classes to later calls, also without the cache."""
    module = generate_dynamic_module(REGISTERED_SCHEMA, module_name="runtime_registered", register_module=True)
    doc = cast("type[pydantic.BaseModel]", module.root_class)
    instance = doc.model_validate({"child": {"x": 1}, "mode": "b", "names": ["q"]})

    assert module.module_name == "runtime_registered"
    assert doc.__module__ == "runtime_registered"
    assert sys.modules["runtime_registered"].Doc is doc
    assert importlib.util.find_spec("runtime_registered") is not None
    assert pickle.loads(pickle.dumps(instance)) == instance
    assert pickle.loads(pickle.dumps(module.models["Mode"].b)) is module.models["Mode"].b
    assert generate_dynamic_module(REGISTERED_SCHEMA, module_name="runtime_registered", register_module=True) is module
    assert generate_dynamic_module(REGISTERED_SCHEMA, module_name="runtime_registered", cache_size=0) is module
    assert clear_dynamic_models_cache() == 0
    assert generate_dynamic_module(REGISTERED_SCHEMA, module_name="runtime_registered") is module


@pytest.mark.usefixtures("forget_registered_modules")
def test_registered_module_name_is_derived_from_the_schema_and_settings() -> None:
    """Test the derived name differs for another property order and stays outside the cache and its eviction."""
    module = generate_dynamic_module(REGISTERED_SCHEMA, register_module=True)
    reordered = {**REGISTERED_SCHEMA, "properties": dict(reversed(REGISTERED_SCHEMA["properties"].items()))}
    other = generate_dynamic_module(reordered, register_module=True, cache_size=1)
    generate_dynamic_models(make_object_schema({"x": {"type": "string"}}), cache_size=1)

    assert cast("str", module.module_name).startswith("_dcg_dynamic_")
    assert other.module_name != module.module_name
    assert list(other.models["Doc"].model_fields) == ["names", "mode", "child"]
    assert generate_dynamic_module(REGISTERED_SCHEMA, register_module=True, cache_size=1) is module
    assert clear_dynamic_models_cache() == 1


@pytest.mark.usefixtures("forget_registered_modules")
def test_registered_split_output_stays_in_sys_modules() -> None:
    """Test split output keeps its package and modules registered, so the classes of every module pickle."""
    schema = json.loads((JSON_SCHEMA_DATA_PATH / "nested_person.json").read_text(encoding="utf-8"))
    module = generate_dynamic_module(schema, module_name="runtime_registered_package", register_module=True)
    nested = module.models_by_source_ref["#/definitions/empty_parent.nested.deep.Json"]

    assert nested.__module__ == "runtime_registered_package.empty_parent.nested.deep"
    assert sys.modules["runtime_registered_package"].empty_parent.nested.deep is sys.modules[nested.__module__]
    assert pickle.loads(pickle.dumps(nested)) is nested
    assert pickle.loads(pickle.dumps(module.root_class)) is module.root_class
    assert sorted(name for name in sys.modules if name.startswith("runtime_registered_package")) == [
        "runtime_registered_package",
        "runtime_registered_package.empty_parent",
        "runtime_registered_package.empty_parent.nested",
        "runtime_registered_package.empty_parent.nested.deep",
        "runtime_registered_package.nested",
        "runtime_registered_package.nested.deep",
    ]


@pytest.mark.usefixtures("forget_registered_modules")
def test_registered_module_lives_until_removed_from_sys_modules() -> None:
    """Test removing a registered module frees it, and a later call registers new classes under the name."""
    module = generate_dynamic_module(REGISTERED_SCHEMA, module_name="runtime_registered", register_module=True)
    registered = weakref.ref(sys.modules["runtime_registered"])
    del sys.modules["runtime_registered"]
    gc.collect()

    assert registered() is None
    assert generate_dynamic_module(REGISTERED_SCHEMA, module_name="runtime_registered", register_module=True) is not (
        module
    )


@pytest.mark.parametrize(
    ("schema", "module_name", "message"),
    [
        (
            make_object_schema({"x": {"type": "string"}}),
            "runtime_registered",
            "module_name 'runtime_registered' is registered for another schema or other settings",
        ),
        (
            REGISTERED_SCHEMA,
            "json",
            "module_name 'json' names a module that generate_dynamic_module() did not register",
        ),
        (
            REGISTERED_SCHEMA,
            "runtime_registered_parent.models",
            (
                "register_module needs the parent package 'runtime_registered_parent' of module_name "
                "'runtime_registered_parent.models' to be importable"
            ),
        ),
        (
            {**REGISTERED_SCHEMA, "x-callback": object()},
            None,
            "register_module needs module_name when the schema or the config cannot be serialized as JSON",
        ),
    ],
)
@pytest.mark.usefixtures("forget_registered_modules")
def test_register_module_refuses_names_it_cannot_own(
    schema: dict[str, Any], module_name: str | None, message: str
) -> None:
    """Test registration never replaces a module, and needs a name that pickle can import."""
    generate_dynamic_module(REGISTERED_SCHEMA, module_name="runtime_registered", register_module=True)

    with pytest.raises(Error, match=re.escape(message)):
        generate_dynamic_module(schema, module_name=module_name, register_module=True)


@pytest.mark.usefixtures("forget_registered_modules")
def test_register_module_refuses_the_name_of_an_importable_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test registration refuses a name that a module file has, and leaves that module to import."""
    (tmp_path / "runtime_registered_importable.py").write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    with pytest.raises(Error, match="module_name 'runtime_registered_importable' names an importable module"):
        generate_dynamic_module(REGISTERED_SCHEMA, module_name="runtime_registered_importable", register_module=True)
    assert "runtime_registered_importable" not in sys.modules
    assert importlib.import_module("runtime_registered_importable").VALUE == 1


@pytest.mark.usefixtures("forget_registered_modules")
def test_register_module_below_a_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test a dotted name registers below a package that is imported first, so that pickle finds the classes."""
    (tmp_path / "runtime_registered_app").mkdir()
    (tmp_path / "runtime_registered_app" / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    module = generate_dynamic_module(
        REGISTERED_SCHEMA, module_name="runtime_registered_app.generated", register_module=True
    )

    split_schema = json.loads((JSON_SCHEMA_DATA_PATH / "nested_person.json").read_text(encoding="utf-8"))
    split = generate_dynamic_module(split_schema, module_name="runtime_registered_app.split", register_module=True)
    nested = split.models_by_source_ref["#/definitions/empty_parent.nested.deep.Json"]
    app = sys.modules["runtime_registered_app"]

    assert app.generated is sys.modules["runtime_registered_app.generated"]
    assert app.split.empty_parent.nested.deep is sys.modules[nested.__module__]
    assert pickle.loads(pickle.dumps(module.root_class)) is module.root_class
    assert pickle.loads(pickle.dumps(nested)) is nested


REENTRANT_PACKAGE_SCRIPT = """\
import sys
from pathlib import Path

from datamodel_code_generator import generate_dynamic_models, generate_dynamic_module

for package in ("app_module", "app_models"):
    Path(sys.argv[1], package).mkdir()
    Path(sys.argv[1], package, "__init__.py").write_text(
        "from datamodel_code_generator import generate_dynamic_models\\n"
        "MODELS = generate_dynamic_models({'type': 'object', 'properties': {'x': {'type': 'integer'}}})\\n"
    )
sys.path.insert(0, sys.argv[1])
schema = {'type': 'object', 'properties': {'name': {'type': 'string'}}}
print(generate_dynamic_module(schema, module_name="app_module.generated", register_module=True).root_class.__module__)
print(generate_dynamic_models(schema, module_name="app_models.generated")["Model"].__module__)
"""


def test_dynamic_calls_import_packages_that_generate_dynamic_models(tmp_path: Path) -> None:
    """Test a package whose import generates dynamic models can hold the module of a dynamic call."""
    result = subprocess.run(
        [sys.executable, "-c", REENTRANT_PACKAGE_SCRIPT, str(tmp_path)],
        capture_output=True,
        check=True,
        text=True,
        timeout=120,
    )

    assert result.stdout.splitlines() == ["app_module.generated", "app_models.generated"]


LEAF_MODULE_SCRIPT = """\
import sys
from pathlib import Path

from datamodel_code_generator import generate_dynamic_models, generate_dynamic_module

schema = {'type': 'object', 'properties': {'name': {'type': 'string'}}}
for module, call in (("app_leaf_models", "generate_dynamic_models"), ("app_leaf_module", "generate_dynamic_module")):
    Path(sys.argv[1], f"{module}.py").write_text(
        f"from datamodel_code_generator import {call}\\n"
        f"GENERATED = {call}({schema!r}, module_name=__name__)\\n"
    )
sys.path.insert(0, sys.argv[1])
models = generate_dynamic_models(schema, module_name="app_leaf_models")
print(models is sys.modules["app_leaf_models"].GENERATED, models["Model"].__module__)
module = generate_dynamic_module(schema, module_name="app_leaf_module")
print(module is sys.modules["app_leaf_module"].GENERATED, module.root_class.__module__)
"""


def test_dynamic_calls_import_modules_that_generate_dynamic_models_in_themselves(tmp_path: Path) -> None:
    """Test a call names a module not imported yet whose import generates the same models, and gets its classes."""
    result = subprocess.run(
        [sys.executable, "-c", LEAF_MODULE_SCRIPT, str(tmp_path)],
        capture_output=True,
        check=True,
        text=True,
        timeout=60,
    )

    assert result.stdout.splitlines() == ["True app_leaf_models", "True app_leaf_module"]


@pytest.mark.usefixtures("forget_registered_modules")
def test_register_module_refuses_a_name_with_modules_below_it() -> None:
    """Test registration refuses a name with modules left below it, and registers once they go as the message says."""
    schema = json.loads((JSON_SCHEMA_DATA_PATH / "nested_person.json").read_text(encoding="utf-8"))
    other_schema = {**schema, "description": "Another schema"}
    module = generate_dynamic_module(schema, module_name="runtime_registered_package", register_module=True)
    nested = module.models_by_source_ref["#/definitions/empty_parent.nested.deep.Json"]
    submodules = (
        "'runtime_registered_package.empty_parent', 'runtime_registered_package.empty_parent.nested', "
        "'runtime_registered_package.empty_parent.nested.deep', 'runtime_registered_package.nested' and "
        "'runtime_registered_package.nested.deep'"
    )

    with pytest.raises(
        Error, match=re.escape(f"or remove 'runtime_registered_package', {submodules} from sys.modules")
    ):
        generate_dynamic_module(other_schema, module_name="runtime_registered_package", register_module=True)
    del sys.modules["runtime_registered_package"]
    with pytest.raises(
        Error,
        match=re.escape(
            "module_name 'runtime_registered_package' has modules below it in sys.modules, which a registered module "
            f"would replace or hide; pass another module_name, or remove {submodules} from sys.modules first."
        ),
    ):
        generate_dynamic_module(other_schema, module_name="runtime_registered_package", register_module=True)
    assert sys.modules[nested.__module__].Json is nested
    for name in [name for name in sys.modules if name.startswith("runtime_registered_package.")]:
        del sys.modules[name]
    other = generate_dynamic_module(other_schema, module_name="runtime_registered_package", register_module=True)
    assert other.root_class is sys.modules["runtime_registered_package"].NestedPerson
    assert pickle.loads(pickle.dumps(other.root_class)) is other.root_class


@pytest.mark.usefixtures("forget_registered_modules")
def test_register_module_with_a_name_for_settings_that_cannot_be_serialized() -> None:
    """Test a config with a callable registers under a given name, which no later call can be matched to."""
    config = GenerateConfig(custom_class_name_generator=str.title)
    module = generate_dynamic_module(
        REGISTERED_SCHEMA, config=config, module_name="runtime_registered", register_module=True
    )
    unregistered = generate_dynamic_module(REGISTERED_SCHEMA, config=config, module_name="runtime_registered")

    assert module.root_class is sys.modules["runtime_registered"].Doc
    assert unregistered.root_class is not module.root_class
    with pytest.raises(pickle.PicklingError, match=re.escape("not the same object as runtime_registered.Doc")):
        pickle.dumps(unregistered.root_class)
    with pytest.raises(Error, match="module_name 'runtime_registered' is registered, and the schema or the config"):
        generate_dynamic_module(
            REGISTERED_SCHEMA, config=config, module_name="runtime_registered", register_module=True
        )


@pytest.mark.usefixtures("forget_registered_modules")
def test_register_module_from_threads() -> None:
    """Test threads that register one schema get one module and the same classes."""
    with ThreadPoolExecutor(max_workers=10) as executor:
        modules = list(
            executor.map(lambda _: generate_dynamic_module(REGISTERED_SCHEMA, register_module=True), range(10))
        )

    assert all(module is modules[0] for module in modules)


@pytest.mark.parametrize("module_split_mode", [None, ModuleSplitMode.Single])
@pytest.mark.usefixtures("forget_registered_modules")
def test_register_module_leaves_nothing_registered_when_the_code_fails(
    module_split_mode: ModuleSplitMode | None,
) -> None:
    """Test generated code that raises leaves no module of the name behind, in single and split output."""
    config = GenerateConfig(base_class="runtime_registered_missing.Base", module_split_mode=module_split_mode)

    with pytest.raises(ModuleNotFoundError, match="runtime_registered_missing"):
        generate_dynamic_module(
            REGISTERED_SCHEMA, config=config, module_name="runtime_registered", register_module=True
        )
    assert [name for name in sys.modules if name.startswith("runtime_registered")] == []
