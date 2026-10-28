"""Tests for output model capability resolution."""

from __future__ import annotations

import subprocess
import sys
from collections import defaultdict
from typing import TYPE_CHECKING, Any, cast

import pytest

from datamodel_code_generator import DataModelType, PythonVersion
from datamodel_code_generator.config import JSONSchemaParserConfig
from datamodel_code_generator.model import get_data_model_types
from datamodel_code_generator.model.base import UNDEFINED, DataModel, DataModelFieldBase
from datamodel_code_generator.model.output import OutputModelContext
from datamodel_code_generator.model.type_alias import TypeAliasTypeBackport
from datamodel_code_generator.parser._output_context import OutputModelContext as ParserOutputModelContext
from datamodel_code_generator.parser.jsonschema import JsonSchemaObject, JsonSchemaParser
from datamodel_code_generator.reference import Reference

if TYPE_CHECKING:
    from datamodel_code_generator.types import DataType

_OUTPUT_CAPABILITIES: dict[DataModelType, tuple[bool, bool, bool, bool, bool]] = {
    DataModelType.PydanticV2BaseModel: (True, True, False, False, True),
    DataModelType.PydanticV2Dataclass: (True, True, False, False, True),
    DataModelType.DataclassesDataclass: (False, True, False, False, False),
    DataModelType.TypingTypedDict: (False, True, False, True, False),
    DataModelType.MsgspecStruct: (True, False, True, False, False),
}


@pytest.mark.allow_direct_assert
def test_output_capability_matrix_covers_every_data_model_type() -> None:
    """Require every output target to declare an intentional capability contract."""
    assert set(_OUTPUT_CAPABILITIES) == set(DataModelType)


@pytest.mark.allow_direct_assert
def test_parser_output_context_import_preserves_model_context_identity() -> None:
    """Keep the established parser import path as a zero-cost compatibility alias."""
    assert ParserOutputModelContext is OutputModelContext
    assert OutputModelContext.__module__ == "datamodel_code_generator.parser._output_context"


@pytest.mark.parametrize(
    ("target_python_version"),
    [
        pytest.param(PythonVersion.PY_311, id="py311"),
        pytest.param(PythonVersion.PY_312, id="py312"),
    ],
)
@pytest.mark.parametrize(
    ("output_model_type", "expected_capabilities"),
    [
        pytest.param(output_model_type, capabilities, id=output_model_type.name)
        for output_model_type, capabilities in _OUTPUT_CAPABILITIES.items()
    ],
)
@pytest.mark.allow_direct_assert
def test_output_capabilities_for_standard_generation_types(
    output_model_type: DataModelType,
    expected_capabilities: tuple[bool, bool, bool, bool, bool],
    target_python_version: PythonVersion,
) -> None:
    """Resolve stable capabilities for every canonical output target."""
    model_types = get_data_model_types(output_model_type, target_python_version=target_python_version)
    context = OutputModelContext.from_generation_types(
        data_model_type=model_types.data_model,
        data_model_root_type=model_types.root_model,
        data_model_field_type=model_types.field_model,
        data_type_manager_type=model_types.data_type_manager,
        configured_types_are_builtin=True,
        use_annotated=True,
    )
    (
        supports_annotated_constraints,
        supports_boolean_literals,
        requires_tagged_union_discriminator,
        requires_additional_properties_reference_classes,
        writes_constrained_types,
    ) = expected_capabilities

    assert context.supports_annotated_constraints is supports_annotated_constraints
    assert context.supports_internal_annotated_constraints is supports_annotated_constraints
    assert context.supports_boolean_literals is supports_boolean_literals
    assert context.requires_tagged_union_discriminator is requires_tagged_union_discriminator
    assert context.requires_additional_properties_reference_classes is requires_additional_properties_reference_classes
    assert context.writes_constrained_types is writes_constrained_types
    if not supports_annotated_constraints:
        return

    expected_nested_model_type = (
        TypeAliasTypeBackport if output_model_type is DataModelType.PydanticV2BaseModel else model_types.root_model
    )
    assert context.resolve_nested_constrained_model_type() is expected_nested_model_type


@pytest.mark.parametrize(
    ("use_type_alias", "use_root_model_type_alias"),
    [
        pytest.param(True, False, id="type-alias"),
        pytest.param(False, True, id="root-model-type-alias"),
    ],
)
@pytest.mark.parametrize(
    "target_python_version",
    [
        pytest.param(PythonVersion.PY_311, id="py311"),
        pytest.param(PythonVersion.PY_312, id="py312"),
    ],
)
@pytest.mark.allow_direct_assert
def test_pydantic_root_model_variants_preserve_annotated_constraint_capability(
    target_python_version: PythonVersion,
    use_type_alias: bool,
    use_root_model_type_alias: bool,
) -> None:
    """Accept every canonical Pydantic root representation without changing nested aliases."""
    model_types = get_data_model_types(
        DataModelType.PydanticV2BaseModel,
        target_python_version=target_python_version,
        use_type_alias=use_type_alias,
        use_root_model_type_alias=use_root_model_type_alias,
    )

    context = OutputModelContext.from_generation_types(
        data_model_type=model_types.data_model,
        data_model_root_type=model_types.root_model,
        data_model_field_type=model_types.field_model,
        data_type_manager_type=model_types.data_type_manager,
        configured_types_are_builtin=True,
        use_annotated=True,
    )

    assert context.supports_annotated_constraints is True
    assert context.supports_internal_annotated_constraints is True
    assert context.resolve_nested_constrained_model_type() is TypeAliasTypeBackport


@pytest.mark.parametrize(
    ("use_annotated", "configured_types_are_builtin"),
    [
        pytest.param(False, True, id="annotated-disabled"),
        pytest.param(True, False, id="custom-generation-types"),
        pytest.param(False, False, id="both-disabled"),
    ],
)
@pytest.mark.allow_direct_assert
def test_annotated_constraint_capability_requires_both_gates(
    use_annotated: bool,
    configured_types_are_builtin: bool,
) -> None:
    """Keep optional and custom generator configurations on the legacy path."""
    model_types = get_data_model_types(
        DataModelType.PydanticV2BaseModel,
        target_python_version=PythonVersion.PY_311,
    )

    context = OutputModelContext.from_generation_types(
        data_model_type=model_types.data_model,
        data_model_root_type=model_types.root_model,
        data_model_field_type=model_types.field_model,
        data_type_manager_type=model_types.data_type_manager,
        configured_types_are_builtin=configured_types_are_builtin,
        use_annotated=use_annotated,
    )

    assert context.supports_annotated_constraints is False
    assert context.supports_internal_annotated_constraints is configured_types_are_builtin
    assert context.supports_boolean_literals is True
    assert context.requires_tagged_union_discriminator is False
    assert context.requires_additional_properties_reference_classes is False


@pytest.mark.parametrize(
    ("data_model_target", "root_target", "field_target", "manager_target"),
    [
        pytest.param(
            DataModelType.PydanticV2BaseModel,
            DataModelType.PydanticV2BaseModel,
            DataModelType.MsgspecStruct,
            DataModelType.PydanticV2BaseModel,
            id="msgspec-field-with-pydantic",
        ),
        pytest.param(
            DataModelType.PydanticV2BaseModel,
            DataModelType.PydanticV2BaseModel,
            DataModelType.PydanticV2BaseModel,
            DataModelType.MsgspecStruct,
            id="msgspec-manager-with-pydantic",
        ),
        pytest.param(
            DataModelType.MsgspecStruct,
            DataModelType.PydanticV2BaseModel,
            DataModelType.MsgspecStruct,
            DataModelType.MsgspecStruct,
            id="pydantic-root-with-msgspec",
        ),
        pytest.param(
            DataModelType.PydanticV2Dataclass,
            DataModelType.PydanticV2Dataclass,
            DataModelType.DataclassesDataclass,
            DataModelType.PydanticV2Dataclass,
            id="stdlib-field-with-pydantic-dataclass",
        ),
    ],
)
@pytest.mark.allow_direct_assert
def test_mixed_builtin_generation_contexts_fail_closed_for_annotated_constraints(
    data_model_target: DataModelType,
    root_target: DataModelType,
    field_target: DataModelType,
    manager_target: DataModelType,
) -> None:
    """Reject incompatible built-in component contexts without changing model semantics."""
    data_model_types = get_data_model_types(data_model_target, target_python_version=PythonVersion.PY_311)
    root_types = get_data_model_types(root_target, target_python_version=PythonVersion.PY_311)
    field_types = get_data_model_types(field_target, target_python_version=PythonVersion.PY_311)
    manager_types = get_data_model_types(manager_target, target_python_version=PythonVersion.PY_311)

    context = OutputModelContext.from_generation_types(
        data_model_type=data_model_types.data_model,
        data_model_root_type=root_types.root_model,
        data_model_field_type=field_types.field_model,
        data_type_manager_type=manager_types.data_type_manager,
        configured_types_are_builtin=True,
        use_annotated=True,
    )
    expected_capabilities = _OUTPUT_CAPABILITIES[data_model_target]

    assert context.supports_annotated_constraints is False
    assert context.supports_internal_annotated_constraints is False
    assert context.supports_boolean_literals is expected_capabilities[1]
    assert context.requires_tagged_union_discriminator is expected_capabilities[2]
    assert context.requires_additional_properties_reference_classes is expected_capabilities[3]


@pytest.mark.allow_direct_assert
def test_output_context_stores_reference_metadata_for_data_and_root_model_types() -> None:
    """Keep custom data and root model metadata encodings aligned with either output shape."""
    model_types = get_data_model_types(
        DataModelType.TypingTypedDict,
        target_python_version=PythonVersion.PY_311,
    )

    def store_metadata(
        model_type: type[DataModel],
        extra_template_data: dict[str, Any],
        reference_classes: set[str],
    ) -> None:
        extra_template_data[model_type.__dict__["_test_reference_key"]] = reference_classes

    def metadata_references(model: DataModel) -> Any:
        return model.extra_template_data.get(type(model).__dict__["_test_reference_key"], ())

    def custom_model_type(name: str, base: type[DataModel], key: str) -> type[DataModel]:
        return cast(
            "type[DataModel]",
            type(
                name,
                (base,),
                {
                    "_additional_properties_reference_classes": property(metadata_references),
                    "_store_additional_properties_reference_classes": classmethod(store_metadata),
                    "_test_reference_key": key,
                },
            ),
        )

    data_model_type = custom_model_type("CustomDataModel", model_types.data_model, "data_model_references")
    root_model_type = custom_model_type("CustomRootModel", model_types.root_model, "root_model_references")
    context = OutputModelContext.from_generation_types(
        data_model_type=data_model_type,
        data_model_root_type=root_model_type,
        data_model_field_type=model_types.field_model,
        data_type_manager_type=model_types.data_type_manager,
        configured_types_are_builtin=False,
        use_annotated=False,
    )
    reference_classes = {"Dependency"}
    legacy_metadata: dict[str, Any] = {}
    separate_metadata: dict[str, Any] = {}
    shared_metadata: dict[str, Any] = {}

    context._store_additional_properties_type(legacy_metadata, "Dependency")
    context.store_additional_properties_value(separate_metadata, value=True)
    context.store_additional_properties_type(
        separate_metadata,
        "Dependency",
        reference_classes,
        use_backport=True,
    )
    assert context.has_additional_properties_type(separate_metadata)
    assert OutputModelContext._has_additional_properties_type(separate_metadata)
    OutputModelContext.from_generation_types(
        data_model_type=data_model_type,
        data_model_root_type=data_model_type,
        data_model_field_type=model_types.field_model,
        data_type_manager_type=model_types.data_type_manager,
        configured_types_are_builtin=False,
        use_annotated=False,
    )._store_additional_properties_reference_classes(shared_metadata, reference_classes)

    data_model = data_model_type(
        fields=[],
        reference=Reference(path="DataModel", original_name="DataModel", name="DataModel"),
        extra_template_data=defaultdict(dict, {"DataModel": separate_metadata}),
    )
    root_model = root_model_type(
        fields=[],
        reference=Reference(path="RootModel", original_name="RootModel", name="RootModel"),
        extra_template_data=defaultdict(dict, {"RootModel": separate_metadata}),
    )

    assert {
        "metadata": separate_metadata,
        "legacy_metadata": legacy_metadata,
        "data_model_references": set(data_model.additional_properties_reference_classes),
        "root_model_references": set(root_model.additional_properties_reference_classes),
        "shared_metadata": shared_metadata,
    } == {
        "metadata": {
            "additionalProperties": True,
            "additionalPropertiesType": "Dependency",
            "use_typeddict_backport": True,
            "data_model_references": {"Dependency"},
            "root_model_references": {"Dependency"},
        },
        "legacy_metadata": {"additionalPropertiesType": "Dependency"},
        "data_model_references": {"Dependency"},
        "root_model_references": {"Dependency"},
        "shared_metadata": {"data_model_references": {"Dependency"}},
    }


@pytest.mark.allow_direct_assert
def test_output_context_capabilities_preserve_mutability() -> None:
    """Keep the established mutable compatibility surface after moving ownership."""
    model_types = get_data_model_types(
        DataModelType.DataclassesDataclass,
        target_python_version=PythonVersion.PY_311,
    )
    context = OutputModelContext.from_generation_types(
        data_model_type=model_types.data_model,
        data_model_root_type=model_types.root_model,
        data_model_field_type=model_types.field_model,
        data_type_manager_type=model_types.data_type_manager,
        configured_types_are_builtin=True,
        use_annotated=False,
    )

    context.supports_boolean_literals = False

    assert context.supports_boolean_literals is False


@pytest.mark.parametrize(
    "custom_component",
    [
        pytest.param("data-model", id="data-model"),
        pytest.param("root-model", id="root-model"),
        pytest.param("field-model", id="field-model"),
        pytest.param("type-manager", id="type-manager"),
    ],
)
@pytest.mark.allow_direct_assert
def test_custom_generation_type_subclasses_fail_closed_for_annotated_constraints(custom_component: str) -> None:
    """Keep inherited custom generator classes on the parser's legacy path."""
    model_types = get_data_model_types(
        DataModelType.PydanticV2BaseModel,
        target_python_version=PythonVersion.PY_311,
    )
    data_model_type = model_types.data_model
    data_model_root_type = model_types.root_model
    data_model_field_type = model_types.field_model
    data_type_manager_type = model_types.data_type_manager

    match custom_component:
        case "data-model":
            data_model_type = type("CustomDataModel", (data_model_type,), {})
        case "root-model":
            data_model_root_type = type("CustomRootModel", (data_model_root_type,), {})
        case "field-model":
            data_model_field_type = type("CustomFieldModel", (data_model_field_type,), {})
        case _:
            data_type_manager_type = type("CustomTypeManager", (data_type_manager_type,), {})

    parser = JsonSchemaParser(
        "{}",
        config=JSONSchemaParserConfig(
            data_model_type=data_model_type,
            data_model_root_type=data_model_root_type,
            data_model_field_type=data_model_field_type,
            data_type_manager_type=data_type_manager_type,
            field_constraints=True,
            use_annotated=True,
        ),
    )

    assert parser._configured_generation_types_are_builtin is False
    assert parser._output_model_context.supports_annotated_constraints is False
    assert parser._output_model_context.supports_internal_annotated_constraints is False
    assert parser._output_model_context.supports_boolean_literals is True
    assert parser._output_model_context.requires_tagged_union_discriminator is False
    assert parser._output_model_context.requires_additional_properties_reference_classes is False


@pytest.mark.parametrize(
    "custom_component",
    [
        pytest.param("inherited", id="inherited"),
        pytest.param("parse-item", id="parse-item"),
        pytest.param("parse-root-type", id="parse-root-type"),
        pytest.param("register-root-model", id="register-root-model"),
        pytest.param("instance-parse-item", id="instance-parse-item"),
        pytest.param("instance-parse-root-type", id="instance-parse-root-type"),
        pytest.param("instance-register-root-model", id="instance-register-root-model"),
        pytest.param("private-name-collision", id="private-name-collision"),
    ],
)
@pytest.mark.allow_direct_assert
def test_parser_extension_hooks_keep_legacy_signatures_and_fail_closed(custom_component: str) -> None:
    """Avoid passing internal output context through established subclass hooks."""
    hook_calls: list[str] = []

    def parse_item(
        self: JsonSchemaParser,
        name: str,
        item: JsonSchemaObject,
        path: list[str],
        singular_name: bool = False,
        parent: JsonSchemaObject | None = None,
    ) -> DataType:
        hook_calls.append("parse-item")
        return JsonSchemaParser.parse_item(self, name, item, path, singular_name, parent)

    def parse_root_type(
        self: JsonSchemaParser,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
    ) -> DataType:
        hook_calls.append("parse-root-type")
        return JsonSchemaParser.parse_root_type(self, name, obj, path)

    def register_root_model(
        self: JsonSchemaParser,
        *,
        reference: Reference,
        fields: list[DataModelFieldBase],
        obj: JsonSchemaObject,
        custom_base_class_name: str,
        description: str | None = None,
        default: Any = UNDEFINED,
    ) -> DataModel:
        hook_calls.append("register-root-model")
        return JsonSchemaParser._register_root_model(
            self,
            reference=reference,
            fields=fields,
            obj=obj,
            custom_base_class_name=custom_base_class_name,
            description=description,
            default=default,
        )

    namespace: dict[str, Any] = {}
    match custom_component:
        case "parse-item":
            namespace = {"parse_item": parse_item}
        case "parse-root-type":
            namespace = {"parse_root_type": parse_root_type}
        case "register-root-model":
            namespace = {"_register_root_model": register_root_model}
        case "private-name-collision":
            namespace = {
                "_parse_constrained_additional_properties_value_item": pytest.fail,
                "_parse_root_type_with_context": pytest.fail,
                "_register_root_model_as": pytest.fail,
            }
        case _:
            pass
    parser_type = type("ExtensionJsonSchemaParser", (JsonSchemaParser,), namespace)
    model_types = get_data_model_types(
        DataModelType.PydanticV2BaseModel,
        target_python_version=PythonVersion.PY_311,
    )
    parser = parser_type(
        "{}",
        config=JSONSchemaParserConfig(
            data_model_type=model_types.data_model,
            data_model_root_type=model_types.root_model,
            data_model_field_type=model_types.field_model,
            data_type_manager_type=model_types.data_type_manager,
            field_constraints=True,
            use_annotated=True,
        ),
    )
    match custom_component:
        case "instance-parse-item":
            parser.__dict__["parse_item"] = parse_item.__get__(parser, parser_type)
        case "instance-parse-root-type":
            parser.__dict__["parse_root_type"] = parse_root_type.__get__(parser, parser_type)
        case "instance-register-root-model":
            parser.__dict__["_register_root_model"] = register_root_model.__get__(parser, parser_type)
    constrained_map = JsonSchemaObject.model_validate({
        "type": "object",
        "additionalProperties": {"type": "integer", "minimum": 1},
    })

    parser.parse_item("Payload", constrained_map, [])

    expected_models = 1 if custom_component in {"inherited", "private-name-collision"} else 0
    assert len(list(parser.results)) == expected_models

    parser.parse_root_type("LegacyRoot", JsonSchemaObject.model_validate({"type": "integer"}), ["legacy"])

    expected_calls = {
        "inherited": [],
        "parse-item": ["parse-item", "parse-item"],
        "parse-root-type": ["parse-root-type"],
        "register-root-model": ["register-root-model"],
        "instance-parse-item": ["parse-item", "parse-item"],
        "instance-parse-root-type": ["parse-root-type"],
        "instance-register-root-model": ["register-root-model"],
        "private-name-collision": [],
    }
    assert hook_calls == expected_calls[custom_component]


@pytest.mark.allow_direct_assert
def test_parse_item_skips_parent_constraint_cache_for_unconstrained_children() -> None:
    """Preserve the constraint-check ordering on the parse-item hot path."""
    parser = JsonSchemaParser("{}")
    parent = JsonSchemaObject.model_validate({"type": "array"})
    child = JsonSchemaObject.model_validate({"type": "string"})

    parser.parse_item("Value", child, [], parent=parent)

    assert "has_constraint" in child.__dict__
    assert "has_constraint" not in parent.__dict__
    assert parent.model_copy(update={"minItems": 1}).has_constraint is True


@pytest.mark.parametrize(
    "schema_case",
    [
        pytest.param("python-override", id="python-override"),
        pytest.param("enum", id="enum"),
        pytest.param("title", id="title"),
    ],
)
@pytest.mark.allow_direct_assert
def test_constrained_item_fast_path_preserves_legacy_short_circuits(schema_case: str) -> None:
    """Keep type overrides, enums, and titled aliases on their established paths."""
    schema_data: dict[str, Any] = {"title": "TitledValue", "type": "integer", "minimum": 1}
    match schema_case:
        case "python-override":
            schema_data = {"type": "integer", "minimum": 1, "x-python-type": "str"}
        case "enum":
            schema_data = {"type": "integer", "minimum": 1, "enum": [1, 2]}
        case _:
            pass
    model_types = get_data_model_types(
        DataModelType.PydanticV2BaseModel,
        target_python_version=PythonVersion.PY_311,
    )
    parser = JsonSchemaParser(
        "{}",
        config=JSONSchemaParserConfig(
            data_model_type=model_types.data_model,
            data_model_root_type=model_types.root_model,
            data_model_field_type=model_types.field_model,
            data_type_manager_type=model_types.data_type_manager,
            field_constraints=True,
            use_annotated=True,
            use_title_as_name=True,
        ),
    )

    data_type = parser._parse_constrained_additional_properties_value_item(
        "Value",
        JsonSchemaObject.model_validate(schema_data),
        ["value"],
        parent=JsonSchemaObject.model_validate({"type": "object"}),
        data_model_root_type=TypeAliasTypeBackport,
    )

    match schema_case:
        case "python-override":
            assert data_type.type_hint == "str"
            assert not list(parser.results)
        case "enum":
            assert data_type.reference is not None
            assert len(list(parser.results)) == 1
        case _:
            assert data_type.reference is not None
            assert data_type.reference.name == "TitledValue"
            assert len(list(parser.results)) == 1


@pytest.mark.allow_direct_assert
def test_output_context_import_does_not_load_output_backends() -> None:
    """Keep importing the context independent from concrete output backends."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "import datamodel_code_generator.parser._output_context; "
                "print('datamodel_code_generator.model.pydantic_v2' in sys.modules); "
                "print('datamodel_code_generator.model.msgspec' in sys.modules)"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == ["False", "False"]


@pytest.mark.allow_direct_assert
def test_pydantic_nested_constraint_alias_import_is_lazy() -> None:
    """Load the Pydantic compatibility alias only when resolution needs it."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "from datamodel_code_generator.model.pydantic_v2.base_model "
                "import BaseModel, DataModelField; "
                "from datamodel_code_generator.model.pydantic_v2.root_model import RootModel; "
                "from datamodel_code_generator.model.pydantic_v2.types import DataTypeManager; "
                "from datamodel_code_generator.parser._output_context import OutputModelContext; "
                "module_name = 'datamodel_code_generator.model.type_alias'; "
                "print(module_name in sys.modules); "
                "context = OutputModelContext.from_generation_types("
                "data_model_type=BaseModel, data_model_root_type=RootModel, "
                "data_model_field_type=DataModelField, data_type_manager_type=DataTypeManager, "
                "configured_types_are_builtin=True, use_annotated=True); "
                "print(module_name in sys.modules); "
                "print(context.resolve_nested_constrained_model_type().__name__); "
                "print(module_name in sys.modules)"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == ["False", "False", "TypeAliasTypeBackport", "True"]


@pytest.mark.parametrize("omit_redundant_constraints", [False, True])
@pytest.mark.allow_direct_assert("a custom type manager is reachable only through the parser configuration")
def test_type_manager_with_the_released_constructor_keeps_working(omit_redundant_constraints: bool) -> None:
    """Build a custom type manager whose constructor has the 0.86.0 signature and pass it the option as an attribute."""
    model_types = get_data_model_types(
        DataModelType.PydanticV2BaseModel,
        target_python_version=PythonVersion.PY_311,
    )
    base_manager = model_types.data_type_manager

    class ReleasedTypeManager(base_manager):  # ty: ignore[unsupported-base]
        def __init__(
            self,
            python_version: PythonVersion = PythonVersion.PY_311,
            use_standard_collections: bool = False,
            use_generic_container_types: bool = False,
            strict_types: Any = None,
            use_non_positive_negative_number_constrained_types: bool = False,
            use_decimal_for_multiple_of: bool = False,
            use_union_operator: bool = False,
            use_pendulum: bool = False,
            use_standard_primitive_types: bool = False,
            use_object_type: bool = False,
            target_datetime_class: Any = None,
            target_date_class: Any = None,
            treat_dot_as_module: bool | None = None,
            use_serialize_as_any: bool = False,
        ) -> None:
            super().__init__(
                python_version,
                use_standard_collections,
                use_generic_container_types,
                strict_types,
                use_non_positive_negative_number_constrained_types,
                use_decimal_for_multiple_of,
                use_union_operator,
                use_pendulum,
                use_standard_primitive_types,
                use_object_type,
                target_datetime_class,
                target_date_class,
                treat_dot_as_module,
                use_serialize_as_any,
            )

    parser = JsonSchemaParser(
        '{"type": "object", "properties": {"a": {"type": "integer", "minimum": 0}}}',
        config=JSONSchemaParserConfig(
            data_model_type=model_types.data_model,
            data_model_root_type=model_types.root_model,
            data_model_field_type=model_types.field_model,
            data_type_manager_type=ReleasedTypeManager,
            omit_redundant_constraints=omit_redundant_constraints,
        ),
    )

    assert parser.data_type_manager.omit_redundant_constraints is omit_redundant_constraints
    assert "class Model" in str(parser.parse())
