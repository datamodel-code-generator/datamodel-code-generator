"""Generated-model conformance policy for invalid payload checks."""

from __future__ import annotations

from fractions import Fraction
from typing import TYPE_CHECKING, Final

from datamodel_code_generator.format import PythonVersion

from .constants import LITERAL_PATTERN_INTERSECTION_CASE_IDS, PAYLOAD_TARGET_PYTHON_VERSION
from .models import PayloadBackend, SchemaCase

if TYPE_CHECKING:
    from collections.abc import Iterable

_PAYLOAD_TARGET_PYTHON_VERSION = PythonVersion(PAYLOAD_TARGET_PYTHON_VERSION)

PYDANTIC_V2_REJECTED_MUTATION_CONSTRAINTS: Final[dict[str, str]] = {
    "additionalProperties": "generated pydantic v2 models forbid extra object properties for closed schemas",
    "required": "generated pydantic v2 models require schema-required object properties",
}
PYDANTIC_V2_UNASSERTED_MUTATION_CONSTRAINTS: Final[dict[str, str]] = {
    "additionalItems": "tuple additionalItems semantics are not represented by generated models",
    "allOf": "combined schema applicator semantics need a dedicated generated-model compatibility policy",
    "anyOf": "combined schema applicator semantics need a dedicated generated-model compatibility policy",
    "contains": "contains/minContains/maxContains membership semantics are not represented by generated models",
    "const": "JSON Schema equality semantics can differ from generated Python literal validation",
    "dependentRequired": "object dependency semantics are not represented by generated models",
    "dependentSchemas": "dependent schema applicator semantics are not represented by generated models",
    "dependencies": "draft7 dependencies object dependency semantics are not represented by generated models",
    "else": "if/then/else conditional semantics are not represented by generated models",
    "enum": "JSON Schema equality semantics can differ from generated Python enum and literal validation",
    "exclusiveMaximum": "numeric exclusive upper bounds are not enforced by generated models by default",
    "exclusiveMinimum": "numeric exclusive lower bounds are not enforced by generated models by default",
    "if": "if/then/else conditional semantics are not represented by generated models",
    "maximum": "numeric upper bounds are not enforced by generated models by default",
    "maxItems": "array length upper bounds are not enforced by generated models by default",
    "maxLength": "string length upper bounds are not enforced by generated models by default",
    "maxProperties": "object property-count upper bounds are not enforced by generated models by default",
    "minimum": "numeric lower bounds are not enforced by generated models by default",
    "minItems": "array length lower bounds are not enforced by generated models by default",
    "minLength": "string length lower bounds are not enforced by generated models by default",
    "minProperties": "object property-count lower bounds are not enforced by generated models by default",
    "multipleOf": "numeric multipleOf constraints are not enforced by generated models by default",
    "not": "not applicator semantics are not represented by generated models",
    "oneOf": "combined schema applicator semantics need a dedicated generated-model compatibility policy",
    "pattern": "string pattern constraints are not enforced by generated models by default",
    "patternProperties": "dynamic-key semantics are not represented by generated models",
    "prefixItems": "tuple prefix item semantics are not represented by generated models",
    "propertyNames": "object key-validation semantics are not represented by generated models",
    "then": "if/then/else conditional semantics are not represented by generated models",
    "type": "primitive type strictness differs because generated pydantic v2 models use non-strict types by default",
    "unevaluatedItems": "annotation-dependent array semantics are not represented by generated models",
    "unevaluatedProperties": "annotation-dependent object semantics are not represented by generated models",
    "uniqueItems": "array uniqueness semantics are not represented by generated models",
}

PYDANTIC_V2_DATACLASS_REJECTED_MUTATION_CONSTRAINTS: Final[dict[str, str]] = {
    "additionalProperties": "generated pydantic v2 dataclasses forbid extra object properties for closed schemas",
    "required": "generated pydantic v2 dataclasses require schema-required object properties",
}
PYDANTIC_V2_DATACLASS_UNASSERTED_MUTATION_CONSTRAINTS: Final[dict[str, str]] = {
    **PYDANTIC_V2_UNASSERTED_MUTATION_CONSTRAINTS,
}

MSGSPEC_REJECTED_MUTATION_CONSTRAINTS: Final[dict[str, str]] = {
    "required": "generated msgspec.Struct classes require schema-required object properties",
}
MSGSPEC_UNASSERTED_MUTATION_CONSTRAINTS: Final[dict[str, str]] = {
    **PYDANTIC_V2_UNASSERTED_MUTATION_CONSTRAINTS,
    "additionalProperties": "generated msgspec.Struct classes ignore unknown object properties by default",
}

BACKEND_REJECTED_MUTATION_CONSTRAINTS: Final[dict[PayloadBackend, dict[str, str]]] = {
    PayloadBackend.PYDANTIC_V2: PYDANTIC_V2_REJECTED_MUTATION_CONSTRAINTS,
    PayloadBackend.PYDANTIC_V2_DATACLASS: PYDANTIC_V2_DATACLASS_REJECTED_MUTATION_CONSTRAINTS,
    PayloadBackend.MSGSPEC: MSGSPEC_REJECTED_MUTATION_CONSTRAINTS,
}
BACKEND_UNASSERTED_MUTATION_CONSTRAINTS: Final[dict[PayloadBackend, dict[str, str]]] = {
    PayloadBackend.PYDANTIC_V2: PYDANTIC_V2_UNASSERTED_MUTATION_CONSTRAINTS,
    PayloadBackend.PYDANTIC_V2_DATACLASS: PYDANTIC_V2_DATACLASS_UNASSERTED_MUTATION_CONSTRAINTS,
    PayloadBackend.MSGSPEC: MSGSPEC_UNASSERTED_MUTATION_CONSTRAINTS,
}
PYDANTIC_V2_DATACLASS_BUILTIN_NAME_EXCLUDED_CASES: Final[dict[str, str]] = {
    "jsonschema/builtin_field_names.json": (
        "pydantic dataclass forward-reference evaluation is confused by fields named like builtins"
    ),
    "jsonschema/field_type_collision_rename_type_double.json": (
        "pydantic dataclass forward-reference evaluation is confused by generated field/type name collisions"
    ),
    "jsonschema/has_default_value.json": (
        "pydantic dataclass forward-reference evaluation is confused by fields named like builtins"
    ),
    "jsonschema/space_field_enum.json": (
        "pydantic dataclass forward-reference evaluation is confused by generated enum field aliases"
    ),
    "openapi/builtin_type_field_names.yaml::components.schemas.BuiltinTypeFieldNames": (
        "pydantic dataclass forward-reference evaluation is confused by fields named like builtins"
    ),
}
PYDANTIC_V2_DATACLASS_REGEX_EXCLUDED_CASES: Final[dict[str, str]] = {
    **dict.fromkeys(
        LITERAL_PATTERN_INTERSECTION_CASE_IDS,
        "root pattern aliases have no consuming dataclass to carry ConfigDict(regex_engine='python-re')",
    ),
    "jsonschema/lookaround_mixed_constraints.json": (
        "root-level oneOf generates a bare TypeAliasType with no consuming dataclass to carry "
        "ConfigDict(regex_engine='python-re'), so TypeAdapter construction still rejects the lookaround pattern"
    ),
    "jsonschema/root_alias_constraints/lookaround.json": (
        "root pattern aliases have no consuming dataclass to carry ConfigDict(regex_engine='python-re')"
    ),
    "openapi/pattern_lookaround.yaml::components.schemas.info": (
        "pydantic dataclass schema construction rejects lookaround regex constraints"
    ),
}
PYDANTIC_V2_DATACLASS_MUTABLE_DEFAULT_EXCLUDED_CASES: Final[dict[str, str]] = {
    "jsonschema/has_classvar_extra_set.json": (
        "dataclass output needs default_factory handling for generated mutable defaults"
    ),
    "jsonschema/msgspec_reserved_field_name.json": (
        "dataclass output needs default_factory handling for generated mutable defaults"
    ),
    "jsonschema/pydantic_v2_dataclass_field.json": (
        "dataclass output needs default_factory handling for generated mutable defaults"
    ),
    "jsonschema/root_model_default_value_branches.json": (
        "dataclass output needs default_factory handling for generated mutable defaults"
    ),
    "jsonschema/unique_items_unhashable_default.json": (
        "dataclass output needs default_factory handling for generated mutable defaults"
    ),
    "openapi/allof_partial_override_deeply_nested_array.yaml::components.schemas.Thing": (
        "dataclass output needs default_factory handling for generated mutable defaults"
    ),
    "openapi/allof_partial_override_nested_array_items.yaml::components.schemas.Thing": (
        "dataclass output needs default_factory handling for generated mutable defaults"
    ),
    "openapi/unique_items_default_set.yaml::components.schemas.TestModel": (
        "dataclass output needs default_factory handling for generated mutable defaults"
    ),
}
PYDANTIC_V2_DATACLASS_DEFINITION_RESOLUTION_EXCLUDED_CASES: Final[dict[str, str]] = {
    "jsonschema/collapse_root_models_name_strategy_child.json": (
        "pydantic 2.7 dataclass schema construction cannot resolve generated collapsed-root definitions"
    ),
    "jsonschema/collapse_root_models_name_strategy_complex.json": (
        "pydantic 2.7 dataclass schema construction cannot resolve generated collapsed-root definitions"
    ),
    "jsonschema/collapse_root_models_name_strategy_direct_refs.json": (
        "pydantic 2.7 dataclass schema construction cannot resolve generated collapsed-root definitions"
    ),
    "jsonschema/collapse_root_models_name_strategy_multiple_wrappers.json": (
        "pydantic 2.7 dataclass schema construction cannot resolve generated collapsed-root definitions"
    ),
    "jsonschema/collapse_root_models_name_strategy_nested_wrappers.json": (
        "pydantic 2.7 dataclass schema construction cannot resolve generated collapsed-root definitions"
    ),
    "jsonschema/collapse_root_models_name_strategy_parent.json": (
        "pydantic 2.7 dataclass schema construction cannot resolve generated collapsed-root definitions"
    ),
    "jsonschema/collapse_root_models_name_strategy_with_inheritance.json": (
        "pydantic 2.7 dataclass schema construction cannot resolve generated collapsed-root definitions"
    ),
}
PYDANTIC_V2_DATACLASS_IMPORT_EXCLUDED_CASES: Final[dict[str, str]] = {
    **PYDANTIC_V2_DATACLASS_BUILTIN_NAME_EXCLUDED_CASES,
    **PYDANTIC_V2_DATACLASS_DEFINITION_RESOLUTION_EXCLUDED_CASES,
    **PYDANTIC_V2_DATACLASS_REGEX_EXCLUDED_CASES,
    **PYDANTIC_V2_DATACLASS_MUTABLE_DEFAULT_EXCLUDED_CASES,
    "jsonschema/discriminator_no_literal.json": (
        "dataclass output cannot import when inherited optional discriminator fields precede required fields"
    ),
    "jsonschema/field_has_same_name.json": (
        "pydantic dataclass forward-reference evaluation is confused by a generated field named Field"
    ),
}
PYDANTIC_V2_DATACLASS_VALIDATION_EXCLUDED_CASES: Final[dict[str, str]] = {
    "jsonschema/deprecated_dataclass.json": (
        "pydantic dataclass validation emits deprecation warnings that the test suite treats as failures"
    ),
    "jsonschema/field_name_shadows_class_name.json": (
        "pydantic dataclass validation resolves a generated field/type name collision to the field value"
    ),
    "openapi/enum_models.yaml::components.schemas.nestedNullableEnum": (
        "pydantic dataclass schema construction warns for defaults attached to nullable enum aliases"
    ),
}
DATACLASS_FIELD_ORDER_EXCLUDED_CASES: Final[dict[str, str]] = {
    "jsonschema/discriminator_no_literal.json": (
        "dataclass-like backends cannot import when inherited optional discriminator fields precede required fields"
    ),
}
MSGSPEC_VALIDATION_EXCLUDED_CASES: Final[dict[str, str]] = {
    **dict.fromkeys(
        (
            "jsonschema/allof_outer_constraints/integer_date_time_bounds.json",
            "jsonschema/allof_outer_constraints/number_date_time_bounds.json",
        ),
        "msgspec string date-time mappings cannot represent numeric timestamp bounds",
    ),
    **dict.fromkeys(
        (
            "jsonschema/compound_property_names/enum_oneof.json",
            "jsonschema/compound_property_names/enum_refs.json",
            "jsonschema/compound_property_names/extra_enum_disjoint.json",
            "jsonschema/compound_property_names/extra_enum_then_string.json",
        ),
        "msgspec conversion rejects unions containing multiple string-like key types",
    ),
    **dict.fromkeys(
        (
            "jsonschema/compound_property_names/nonstring_complex_patterns.json",
            "jsonschema/compound_property_names/nonstring_length.json",
            "jsonschema/compound_property_names/nonstring_literal.json",
            "jsonschema/compound_property_names/nonstring_lookbehind.json",
            "jsonschema/compound_property_names/unconstrained_oneof.json",
            "jsonschema/compound_property_names/unrestricted_type_union.json",
            "jsonschema/compound_property_names/unsupported_mixed.json",
            "jsonschema/compound_property_names/unsupported_nonstring.json",
            "jsonschema/compound_property_names/unsupported_nonstring_union.json",
            "jsonschema/compound_property_names/unsupported_one_constrained.json",
            "jsonschema/compound_property_names/unsupported_oneof.json",
        ),
        "msgspec conversion rejects string constraint metadata on generated non-string property-name aliases",
    ),
    **dict.fromkeys(
        (
            "jsonschema/numeric_allof_types/null_template.json",
            "jsonschema/root_alias_constraints/null_numeric.json",
            "jsonschema/root_alias_constraints/null_pattern.json",
        ),
        "msgspec conversion rejects constraint metadata on generated null-only aliases",
    ),
    "jsonschema/type_union_constraints/enum_field.json": (
        "msgspec conversion only supports Enum classes with homogeneous str or int values"
    ),
    **dict.fromkeys(
        (
            "jsonschema/msgspec_enum_diagnostics/boolean.json",
            "jsonschema/msgspec_enum_diagnostics/boolean_false.json",
            "jsonschema/msgspec_enum_diagnostics/boolean_first_alias.json",
            "jsonschema/msgspec_enum_diagnostics/boolean_integer_alias.json",
            "jsonschema/msgspec_enum_diagnostics/boolean_true.json",
            "jsonschema/msgspec_enum_diagnostics/boolean_untyped.json",
            "jsonschema/msgspec_enum_diagnostics/float.json",
            "jsonschema/msgspec_enum_diagnostics/float_first_alias.json",
            "jsonschema/msgspec_enum_diagnostics/large_aliases.json",
            "jsonschema/msgspec_enum_diagnostics/other_backends.json",
            "jsonschema/msgspec_enum_diagnostics/string_alias_before_boolean.json",
            "jsonschema/msgspec_enum_diagnostics/subclass_bool.json",
            "jsonschema/msgspec_enum_diagnostics/subclass_late.json",
            "jsonschema/msgspec_enum_diagnostics/subclass_rejected.json",
            "jsonschema/msgspec_enum_diagnostics/subclass_reverse.json",
        ),
        "msgspec conversion only supports Enum classes with homogeneous str or int values",
    ),
    **dict.fromkeys(
        (
            "jsonschema/explicit_alias_namespace_inheritance/earlier.json",
            "jsonschema/explicit_alias_namespace_inheritance/later.json",
        ),
        "msgspec Struct cannot combine independent field-bearing base layouts",
    ),
    **dict.fromkeys(
        (
            "jsonschema/allof_constraint_intersections/complex.json",
            "jsonschema/allof_constraint_intersections/metadata.json",
        ),
        "msgspec conversion cannot validate generated empty Enum classes from object or array enum values",
    ),
    **dict.fromkeys(
        (
            "jsonschema/allof_constraint_intersections/scalars_equal.json",
            "jsonschema/allof_constraint_intersections/scalars_partial.json",
        ),
        "msgspec conversion only supports Enum classes with homogeneous str or int values",
    ),
    **dict.fromkeys(
        (
            "jsonschema/custom_template_dependencies_many.json",
            "jsonschema/optional_nested_factory_requirements/union.json",
        ),
        "msgspec conversion requires tagged Struct unions for combined object schemas",
    ),
    "jsonschema/numeric_constraint_precision/large.json": (
        "msgspec Meta cannot represent integer constraints outside its native numeric range"
    ),
    "jsonschema/array_combined.py.json": (
        "msgspec conversion cannot validate generated empty Enum classes from array-valued enum schemas"
    ),
    "jsonschema/all_of_ref_self.json": (
        "msgspec 0.18 cannot evaluate generated null-only union aliases during conversion"
    ),
    "jsonschema/builtin_field_names.json": (
        "msgspec forward-reference evaluation is confused by fields named like builtins"
    ),
    "openapi/builtin_type_field_names.yaml::components.schemas.BuiltinTypeFieldNames": (
        "msgspec forward-reference evaluation is confused by fields named like builtins"
    ),
    "jsonschema/combine_any_of_object.json": (
        "msgspec conversion requires tagged Struct unions for combined object schemas"
    ),
    "jsonschema/combine_one_of_object.json": (
        "msgspec conversion requires tagged Struct unions for combined object schemas"
    ),
    "jsonschema/msgspec_payload_runtime_compatibility.json": (
        "msgspec conversion requires tagged Struct unions, but boolean tag values are not supported"
    ),
    "jsonschema/collapse_root_models_empty_union.json": (
        "msgspec 0.18 cannot evaluate generated null-only union aliases during conversion"
    ),
    "jsonschema/default_factory_nested_model_with_dict.json": (
        "msgspec conversion rejects unions containing multiple dict-like runtime types"
    ),
    **dict.fromkeys(
        (
            "jsonschema/unique_items_union_ref_sibling_2020.json",
            "jsonschema/unique_items_union_ref_sibling_draft7.json",
        ),
        "msgspec conversion rejects unions containing multiple array-like runtime types",
    ),
    "jsonschema/unique_items_union_enum_root.json": (
        "msgspec conversion only supports Enum classes with homogeneous str or int values"
    ),
    "jsonschema/enum_complex_values_literal.json": (
        "msgspec conversion only supports Enum classes with homogeneous str or int values"
    ),
    "jsonschema/enum_member_typed_defaults.json": (
        "msgspec conversion only supports Enum classes with homogeneous str or int values"
    ),
    "jsonschema/enum_object_values.json": (
        "msgspec conversion cannot validate generated empty Enum classes from object-valued enum schemas"
    ),
    "jsonschema/exact_imports_collapse_root_models_module_split_oneof.json": (
        "msgspec conversion requires tagged Struct unions for combined object schemas"
    ),
    "jsonschema/falsy_default_enum_member.json": (
        "msgspec conversion only supports Enum classes with homogeneous str or int values"
    ),
    "jsonschema/field_has_same_name.json": ("msgspec conversion cannot evaluate generated field/type name collisions"),
    "jsonschema/field_name_shadows_class_name.json": (
        "msgspec conversion cannot evaluate generated field/type name collisions"
    ),
    "jsonschema/field_type_collision_rename_type_double.json": (
        "msgspec conversion cannot evaluate generated field/type name collisions"
    ),
    "jsonschema/has_default_value.json": "msgspec conversion cannot evaluate generated fields named like builtins",
    "openapi/discriminator_float_mapping.yaml::components.schemas.Bar": (
        "msgspec conversion only supports Enum classes with homogeneous str or int values"
    ),
    "openapi/discriminator_float_mapping.yaml::components.schemas.Foo": (
        "msgspec conversion only supports Enum classes with homogeneous str or int values"
    ),
    "openapi/enum_models.yaml::components.schemas.Pet": (
        "msgspec conversion only supports Enum classes with homogeneous str or int values"
    ),
    "openapi/enum_models.yaml::components.schemas.version": (
        "msgspec conversion does not accept nullable enum aliases generated from OpenAPI schemas"
    ),
    "jsonschema/nullable_enum_literal_typed_dict.json": (
        "msgspec conversion only supports Enum classes with homogeneous str or int values"
    ),
    "jsonschema/oneof_const_enum_bool.json": (
        "msgspec conversion only supports Enum classes with homogeneous str or int values"
    ),
    "jsonschema/oneof_const_enum_float.json": (
        "msgspec conversion only supports Enum classes with homogeneous str or int values"
    ),
    "jsonschema/oneof_const_enum_object.yaml": (
        "msgspec conversion cannot validate generated empty Enum classes from object-valued const schemas"
    ),
    "jsonschema/property_names_anyof_ref.json": (
        "msgspec conversion rejects unions containing multiple string Enum types"
    ),
    "jsonschema/property_names_ref_enum.json": (
        "msgspec conversion does not support generated property-name Enum key aliases"
    ),
    "jsonschema/ref_type_has_null.json": (
        "msgspec 0.18 cannot evaluate generated null-only union aliases during conversion"
    ),
    "jsonschema/recursive_ref.json": "msgspec conversion cannot evaluate generated recursive Struct aliases",
    "jsonschema/recursive_ref_in_defs.json": ("msgspec conversion cannot evaluate generated recursive Struct aliases"),
    "jsonschema/recursive_ref_no_anchor.json": (
        "msgspec conversion cannot evaluate generated recursive Struct aliases"
    ),
    "jsonschema/special_enum.json": (
        "msgspec conversion only supports Enum classes with homogeneous str or int values"
    ),
    "jsonschema/space_field_enum.json": (
        "msgspec conversion cannot evaluate generated enum field/type name collisions"
    ),
    "jsonschema/strict_types_matrix.json": (
        "msgspec conversion rejects numeric constraint metadata attached to Decimal types"
    ),
    "jsonschema/anyof_const_with_constraints.json": (
        "msgspec conversion rejects constrained scalar unions that differ from JSON Schema branch semantics"
    ),
    "jsonschema/invalid_model_name.json": (
        "msgspec conversion does not accept nullable alias shapes generated for this schema"
    ),
    "jsonschema/msgspec_null_field.json": (
        "msgspec conversion does not accept null-only field aliases generated from JSON Schema"
    ),
    "jsonschema/person.json": ("msgspec conversion does not accept nullable alias shapes generated for this schema"),
    "jsonschema/pydantic_v2_dataclass_field.json": (
        "msgspec conversion rejects fractional multipleOf constraint metadata for scalar fields"
    ),
    "jsonschema/use_decimal_for_multiple_of.json": (
        "msgspec conversion rejects fractional multipleOf constraint metadata for scalar fields"
    ),
    "openapi/msgspec_oneof_with_null.yaml::components.schemas.Model": (
        "msgspec conversion rejects nullable oneOf metadata attached to generated aliases"
    ),
    "openapi/type_mappings_byte_to_binary.yaml::components.schemas.Foo": (
        "msgspec conversion treats binary string mappings as strict bytes while JSON Schema treats encoding as "
        "annotation"
    ),
    "jsonschema/pydantic_v2_model_default_dict_non_empty.json": (
        "msgspec default_factory conversion does not preserve dict-of-model defaults for payload construction"
    ),
    "jsonschema/reduce_duplicate_field_types.json": (
        "msgspec conversion requires tagged Struct unions for combined object schemas"
    ),
    "jsonschema/ref_merge_additional_properties.json": (
        "msgspec conversion rejects length metadata attached to generated Literal types"
    ),
    "jsonschema/ref_merge_field_metadata.json": (
        "msgspec conversion rejects length metadata attached to generated Literal types"
    ),
    "jsonschema/reuse_model_collapse_root_models_constraints.json": (
        "msgspec conversion requires tagged Struct unions for combined object schemas"
    ),
    "jsonschema/root_model_with_additional_properties.json": (
        "msgspec conversion requires tagged Struct unions for combined object schemas"
    ),
    "jsonschema/json_pointer.json": ("msgspec conversion requires tagged Struct unions for combined object schemas"),
    "jsonschema/nested_json_pointer.json": (
        "msgspec conversion requires tagged Struct unions for combined object schemas"
    ),
    "jsonschema/object_has_one_of.json": (
        "msgspec conversion requires tagged Struct unions for combined object schemas"
    ),
    "jsonschema/lookaround_anyof_nullable.json": (
        "msgspec conversion rejects generated lookaround regex metadata in non-string containers"
    ),
    "jsonschema/lookaround_dict.json": (
        "msgspec conversion rejects generated lookaround regex metadata in non-string containers"
    ),
    "jsonschema/lookaround_mixed_constraints.json": (
        "msgspec conversion rejects generated lookaround regex metadata in non-string containers"
    ),
    "jsonschema/lookaround_union_types.json": (
        "msgspec conversion rejects generated lookaround regex metadata in non-string containers"
    ),
    "jsonschema/nested_lookaround_array.json": (
        "msgspec conversion rejects generated lookaround regex metadata in non-string containers"
    ),
    "openapi/pattern_lookaround.yaml::components.schemas.info": (
        "msgspec conversion rejects generated lookaround regex metadata in non-string containers"
    ),
}
MSGSPEC_TYPE_STATEMENT_VALIDATION_EXCLUDED_CASES: Final[dict[str, str]] = {
    "jsonschema/unique_items_schema_validators.json": (
        "msgspec conversion rejects unions containing multiple array-like runtime types"
    ),
    "jsonschema/unique_items_union_ref_mapping_root.json": (
        "msgspec conversion rejects unions containing multiple dict-like runtime types"
    ),
}
MSGSPEC_LEGACY_ALIAS_VALIDATION_EXCLUDED_CASES: Final[dict[str, str]] = {
    "jsonschema/compound_property_names/ref_then_any.json": (
        "msgspec conversion cannot resolve forward references in assignment-based property-name aliases"
    ),
}
BACKEND_FULL_MATRIX_EXCLUDED_CASES: Final[dict[PayloadBackend, dict[str, str]]] = {
    PayloadBackend.PYDANTIC_V2_DATACLASS: {
        **PYDANTIC_V2_DATACLASS_IMPORT_EXCLUDED_CASES,
        **PYDANTIC_V2_DATACLASS_VALIDATION_EXCLUDED_CASES,
    },
    PayloadBackend.MSGSPEC: {
        **DATACLASS_FIELD_ORDER_EXCLUDED_CASES,
        **MSGSPEC_VALIDATION_EXCLUDED_CASES,
    },
}
BACKEND_ACCEPTANCE_EXCLUDED_CASES: Final[dict[PayloadBackend, dict[str, str]]] = BACKEND_FULL_MATRIX_EXCLUDED_CASES
BACKEND_REJECTION_EXCLUDED_CASES: Final[dict[PayloadBackend, dict[str, str]]] = BACKEND_FULL_MATRIX_EXCLUDED_CASES
DATACLASSES_FULL_MATRIX_EXCLUSION_REASON: Final[str] = (
    "plain dataclasses remain representative because native construction has no JSON Schema runtime validation"
)
OPENAPI_FULL_MATRIX_EXCLUSION_REASONS: Final[dict[PayloadBackend, str]] = {
    PayloadBackend.PYDANTIC_V2_DATACLASS: (
        "OpenAPI pydantic dataclass full-matrix coverage remains representative until backend-specific "
        "alias, default, and composition limits are reduced"
    ),
    PayloadBackend.MSGSPEC: (
        "OpenAPI msgspec full-matrix coverage remains representative until backend-specific enum, null, "
        "and composition limits are reduced"
    ),
}
PAYLOAD_VALIDATION_BACKENDS: Final[tuple[PayloadBackend, ...]] = (
    PayloadBackend.PYDANTIC_V2,
    PayloadBackend.PYDANTIC_V2_DATACLASS,
    PayloadBackend.MSGSPEC,
)
MSGSPEC_BINARY_FORMAT_EXCLUSION_REASON: Final[str] = (
    "msgspec conversion treats binary format strings as strict bytes while JSON Schema treats format as annotation"
)
MSGSPEC_FRACTIONAL_MULTIPLE_OF_EXCLUSION_REASON: Final[str] = (
    "msgspec conversion rejects schema-valid float values for fractional multipleOf metadata"
)
MSGSPEC_OPTIONAL_NULL_ONLY_EXCLUSION_REASON: Final[str] = (
    "msgspec conversion treats optional null-only properties as omitted-only Unset fields"
)
SCHEMA_KEYWORD_KINDS: Final[dict[str, str]] = {
    **dict.fromkeys(
        {
            "additionalItems",
            "additionalProperties",
            "allOf",
            "anyOf",
            "contains",
            "contentSchema",
            "else",
            "if",
            "items",
            "not",
            "oneOf",
            "prefixItems",
            "propertyNames",
            "then",
            "unevaluatedItems",
            "unevaluatedProperties",
        },
        "schema",
    ),
    **dict.fromkeys(
        {
            "$defs",
            "definitions",
            "dependentSchemas",
            "patternProperties",
            "properties",
        },
        "schema_map",
    ),
    "dependencies": "dependencies",
}


def _schema_keyword_kind(keyword: object) -> str | None:
    if not isinstance(keyword, str):
        return None
    return SCHEMA_KEYWORD_KINDS.get(keyword)


def _iter_keyword_schema_nodes(keyword: object, value: object) -> Iterable[dict[str, object]]:
    """Yield child schemas for JSON Schema keywords that carry subschemas."""
    match _schema_keyword_kind(keyword):
        case "schema":
            yield from _iter_schema_nodes(value)
            return
        case "schema_map":
            if not isinstance(value, dict):
                return
            for subschema in value.values():
                yield from _iter_schema_nodes(subschema)
            return
        case "dependencies":
            if not isinstance(value, dict):
                return
            for dependency in value.values():
                if isinstance(dependency, dict):
                    yield from _iter_schema_nodes(dependency)
            return
        case _:
            return


def _iter_schema_nodes(schema: object) -> Iterable[dict[str, object]]:
    """Yield nested JSON Schema objects."""
    match schema:
        case dict() as mapping:
            yield mapping
            for keyword, value in mapping.items():
                yield from _iter_keyword_schema_nodes(keyword, value)
            return
        case list() | tuple() as values:
            for item in values:
                yield from _iter_schema_nodes(item)
            return
        case _:
            return


def _schema_types(schema: dict[str, object]) -> frozenset[str]:
    """Return the normalized string values from a schema's type keyword."""
    match schema.get("type"):
        case str() as schema_type:
            return frozenset((schema_type,))
        case list() | tuple() as schema_types:
            return frozenset(schema_type for schema_type in schema_types if isinstance(schema_type, str))
    return frozenset()


def _is_null_only_schema(schema: object) -> bool:
    """Return whether a schema accepts only JSON null."""
    if not isinstance(schema, dict):
        return False

    match _schema_types(schema):
        case schema_types if schema_types == {"null"}:
            return True
        case _:
            return schema.get("enum") == [None] or ("const" in schema and schema["const"] is None)


def _has_optional_null_only_property(schema: dict[str, object]) -> bool:
    """Return whether an object schema has a non-required property that only accepts null."""
    if not isinstance(properties := schema.get("properties"), dict):
        return False

    required = schema.get("required", ())
    required_properties = set(required) if isinstance(required, list | tuple | set) else set()
    return any(
        isinstance(property_name, str)
        and property_name not in required_properties
        and _is_null_only_schema(property_schema)
        for property_name, property_schema in properties.items()
    )


def _has_binary_format(schema: dict[str, object]) -> bool:
    """Return whether a string schema uses the binary format annotation."""
    return "string" in _schema_types(schema) and schema.get("format") == "binary"


def _has_fractional_number_multiple_of(schema: dict[str, object]) -> bool:
    """Return whether a number schema uses fractional multipleOf metadata."""
    if (multiple_of := schema.get("multipleOf")) is None:
        return False

    if (schema_types := _schema_types(schema)) and schema_types.isdisjoint({"integer", "number"}):
        return False

    try:
        return Fraction(str(multiple_of)).denominator != 1
    except (TypeError, ValueError):
        return False


def _msgspec_schema_exclusion_reason(case: SchemaCase) -> str | None:
    """Return a schema-derived msgspec compatibility exclusion reason."""
    if case.input_file_type != "jsonschema":
        return None

    for schema in _iter_schema_nodes(case.source_schema):
        match schema:
            case _ if _has_optional_null_only_property(schema):
                return MSGSPEC_OPTIONAL_NULL_ONLY_EXCLUSION_REASON
            case _ if _has_binary_format(schema):
                return MSGSPEC_BINARY_FORMAT_EXCLUSION_REASON
            case _ if _has_fractional_number_multiple_of(schema):
                return MSGSPEC_FRACTIONAL_MULTIPLE_OF_EXCLUSION_REASON
            case _:
                continue
    return None


def _msgspec_type_statement_exclusion_reason(
    case: SchemaCase,
    target_python_version: PythonVersion = _PAYLOAD_TARGET_PYTHON_VERSION,
) -> str | None:
    """Return msgspec exclusions specific to the generated alias syntax."""
    if not target_python_version.has_type_statement:
        return MSGSPEC_LEGACY_ALIAS_VALIDATION_EXCLUDED_CASES.get(case.id)
    return MSGSPEC_TYPE_STATEMENT_VALIDATION_EXCLUDED_CASES.get(case.id)


def _backend_schema_exclusion_reason(case: SchemaCase, backend: PayloadBackend) -> str | None:
    """Return dynamic full-matrix exclusions derived from schema/runtime semantics."""
    match backend:
        case PayloadBackend.MSGSPEC:
            if reason := _msgspec_type_statement_exclusion_reason(case):
                return reason
            return _msgspec_schema_exclusion_reason(case)
        case _:
            return None


def rejected_mutation_constraints(backend: PayloadBackend) -> frozenset[str]:
    """Return mutation constraints asserted for a backend."""
    return frozenset(BACKEND_REJECTED_MUTATION_CONSTRAINTS.get(backend, {}))


def rejected_mutation_reason(constraint: str, backend: PayloadBackend = PayloadBackend.PYDANTIC_V2) -> str | None:
    """Return the policy reason for generated-model rejection."""
    return BACKEND_REJECTED_MUTATION_CONSTRAINTS.get(backend, {}).get(constraint)


def backend_acceptance_exclusion_reason(case: SchemaCase, backend: PayloadBackend) -> str | None:
    """Return why a backend acceptance case is outside the full scheduled matrix."""
    if backend is PayloadBackend.DATACLASSES:
        return DATACLASSES_FULL_MATRIX_EXCLUSION_REASON
    if reason := BACKEND_ACCEPTANCE_EXCLUDED_CASES.get(backend, {}).get(case.id):
        return reason
    if reason := _backend_schema_exclusion_reason(case, backend):
        return reason
    if case.input_file_type == "openapi":
        return OPENAPI_FULL_MATRIX_EXCLUSION_REASONS.get(backend)
    return None


def backend_rejection_exclusion_reason(case: SchemaCase, backend: PayloadBackend) -> str | None:
    """Return why a backend rejection case is outside the full scheduled matrix."""
    if reason := BACKEND_REJECTION_EXCLUDED_CASES.get(backend, {}).get(case.id):
        return reason
    if reason := _backend_schema_exclusion_reason(case, backend):
        return reason
    if case.input_file_type == "openapi":
        return OPENAPI_FULL_MATRIX_EXCLUSION_REASONS.get(backend)
    return None
