"""Constants for schema-derived payload validation tests."""

from __future__ import annotations

import json

from datamodel_code_generator.format import PythonVersion, is_supported_in_black
from tests.main.conftest import CURRENT_PYTHON_VERSION, DATA_PATH
from tests.main.payload_validation.models import PayloadBackend

PAYLOAD_CODEGEN_WARNING_FILES: dict[str, str] = json.loads(
    (DATA_PATH / "payloads/numeric_allof_type_warning_files.json").read_text(encoding="utf-8")
)

PAYLOAD_CLASS_NAME = "Payload"
PAYLOAD_CURRENT_PYTHON_VERSION = PythonVersion(CURRENT_PYTHON_VERSION)
SCHEMA_FILE_SUFFIXES = {".json", ".yaml", ".yml"}
COMBINED_SCHEMA_KEYS = ("allOf", "anyOf", "oneOf")
JSON_SCHEMA_KEYS = {
    "$defs",
    "$id",
    "$ref",
    "$schema",
    "additionalProperties",
    *COMBINED_SCHEMA_KEYS,
    "definitions",
    "enum",
    "items",
    "properties",
    "type",
}
OBJECT_SCHEMA_KEYWORDS = {
    "additionalProperties",
    "maxProperties",
    "minProperties",
    "properties",
    "propertyNames",
    "required",
    "unevaluatedProperties",
}
ARRAY_SCHEMA_KEYWORDS = {
    "additionalItems",
    "contains",
    "items",
    "maxContains",
    "maxItems",
    "minContains",
    "minItems",
    "prefixItems",
    "unevaluatedItems",
    "uniqueItems",
}
SIMPLE_ALLOF_IGNORED_KEYS = {"description", "title"}
SIMPLE_ALLOF_SUPPORTED_KEYS = {
    "const",
    "enum",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "format",
    "maximum",
    "maxItems",
    "maxLength",
    "minimum",
    "minItems",
    "minLength",
    "multipleOf",
    "type",
    "uniqueItems",
}
PAYLOAD_FORMAT_ENUMS = {
    "byte": ["", "MA==", "Zm9v"],
    "date-time-local": ["2023-12-25T10:30:00"],
    "duration": ["P1D", "PT1S"],
    "ipv4": ["127.0.0.1"],
    "ipv6": ["::1"],
    "uuid": ["00000000-0000-4000-8000-000000000000"],
}
SERIALIZED_DECIMAL_DEFAULT_PAYLOAD_EXCLUSION_REASON = (
    "focused Decimal-default e2e fixture intentionally emits a compatibility warning in default generation"
)
EXCLUDED_FILES: dict[str, str] = {
    "jsonschema/allof_class_hierarchy.json": "intentionally invalid JSON fixture",
    "jsonschema/collapse_root_models_decimal_defaults.json": SERIALIZED_DECIMAL_DEFAULT_PAYLOAD_EXCLUSION_REASON,
    "jsonschema/non_dict_files/list_only.json": "input is JSON data, not a JSON Schema document",
    "jsonschema/non_dict_files/list_only.yaml": "input is YAML data, not a JSON Schema document",
    "jsonschema/non_dict_files/whitespace_only.yaml": "empty YAML fixture",
    "jsonschema/non_json_object.json": "input is JSON data, not a JSON Schema document",
    "jsonschema/null.json": "intentionally invalid JSON fixture",
    "jsonschema/ref_to_json_list/list.json": "referenced JSON data list, not a schema document",
    "jsonschema/schema_validators_multiple_aliases_property_count.json": (
        "opt-in schema-validator e2e fixture; payload validation intentionally uses default generation"
    ),
    "jsonschema/serialized_decimal_default_alias.json": SERIALIZED_DECIMAL_DEFAULT_PAYLOAD_EXCLUSION_REASON,
    "jsonschema/serialized_decimal_default_alias_from_import.json": SERIALIZED_DECIMAL_DEFAULT_PAYLOAD_EXCLUSION_REASON,
    "jsonschema/serialized_decimal_default_alias_from_type.json": SERIALIZED_DECIMAL_DEFAULT_PAYLOAD_EXCLUSION_REASON,
    "jsonschema/serialized_decimal_default_collision.json": SERIALIZED_DECIMAL_DEFAULT_PAYLOAD_EXCLUSION_REASON,
    "jsonschema/serialized_decimal_default_condecimal_collision.json": (
        SERIALIZED_DECIMAL_DEFAULT_PAYLOAD_EXCLUSION_REASON
    ),
    "jsonschema/serialized_decimal_default_scope.json": SERIALIZED_DECIMAL_DEFAULT_PAYLOAD_EXCLUSION_REASON,
    "jsonschema/serialized_decimal_defaults.json": SERIALIZED_DECIMAL_DEFAULT_PAYLOAD_EXCLUSION_REASON,
    "jsonschema/unique_items_prefix_items_draft7.json": (
        "opt-in schema-validator dialect e2e fixture; Draft 7 ignores prefixItems, "
        "which hypothesis-jsonschema cannot satisfy for generic payload validation"
    ),
    "jsonschema/unknown_format.json": "unknown format fixture intentionally emits a generator warning",
    "openapi/complex_reference.json": "intentionally invalid JSON fixture",
    "openapi/list.json": "input is JSON data, not an OpenAPI document",
    "openapi/not.json": "intentionally invalid JSON fixture",
    "openapi/subclass_enum.json": "intentionally invalid JSON fixture",
}
ALLOF_REF_SIBLING_DIAGNOSTICS = json.loads(
    (DATA_PATH / "payloads/allof_ref_sibling_diagnostics.json").read_text(encoding="utf-8")
)
EXCLUDED_CASES: dict[str, str] = {
    **dict.fromkeys(
        (
            "jsonschema/allof_outer_constraints/duration_length.json",
            "jsonschema/allof_outer_constraints/uuid4_length.json",
        ),
        "hypothesis-jsonschema generates arbitrary strings for these outer allOf formats; "
        "test_allof_outer_constraints checks native duration and UUID4 validation and serialization",
    ),
    **dict.fromkeys(
        (
            "jsonschema/allof_outer_constraints/mapped_date_time_length.json",
            "jsonschema/allof_outer_constraints/number_mapped_email_length.json",
            "jsonschema/allof_outer_constraints/number_mapped_string_length.json",
        ),
        "custom type-mapping fixtures require explicit generation options; test_allof_outer_constraints "
        "checks the configured runtime types and their mapped schema constraints",
    ),
    **dict.fromkeys(
        (
            "jsonschema/allof_outer_constraints/unknown_format.json",
            "jsonschema/allof_outer_constraints/unknown_length.json",
        ),
        "unknown formats intentionally emit compatibility warnings; test_allof_outer_constraints "
        "asserts these warnings and the fallback runtime behavior",
    ),
    **dict.fromkeys(
        (
            "jsonschema/compound_property_names/allof_branch_title.json",
            "jsonschema/compound_property_names/allof_lookbehind.json",
            "jsonschema/compound_property_names/unsupported_one_sibling.json",
            "jsonschema/compound_property_names/unsupported_sibling.json",
        ),
        "legacy propertyNames model keys cannot validate nonempty dictionaries; "
        "valid empty dictionaries are covered by test_compound_property_names_compatibility",
    ),
    **{
        f"jsonschema/allof_ref_siblings/{name}.json": (
            "empty literal intersection; CLI/API diagnostics and native rejection are exercised by "
            "test_allof_ref_sibling_empty_intersection"
        )
        for name in ALLOF_REF_SIBLING_DIAGNOSTICS
    },
    "jsonschema/all_of_any_of_base_class_ref.json": "hypothesis-jsonschema cannot satisfy the allOf/anyOf constraints",
    "jsonschema/additional_properties_schema_with_allof_properties.json": (
        "hypothesis-jsonschema cannot satisfy the allOf object constraints; typed extras are covered by e2e tests"
    ),
    "jsonschema/decimal_fractional_constraints.json": (
        "format decimal strings from hypothesis-jsonschema are arbitrary text that Decimal cannot parse"
    ),
    "jsonschema/integer_fractional_constraints.json": (
        "hypothesis-jsonschema emits integers near float precision limits where multipleOf checks are unstable"
    ),
    "jsonschema/msgspec_decimal_constraints.json": (
        "format decimal strings from hypothesis-jsonschema are arbitrary text that Decimal cannot parse"
    ),
    **dict.fromkeys(
        (
            "jsonschema/nested_resources/draft4_id_scope/root.json",
            "jsonschema/nested_resources/undeclared_modern_scope/root.json",
        ),
        "hypothesis-jsonschema resolves the child resource's local pointer against the outer document, "
        "producing strings where the source requires integers; test_schema_resource_identifier_versions "
        "retains the focused generation and runtime checks",
    ),
    "jsonschema/nested_resources/undeclared_id_scope/root.json": (
        "undeclared-dialect compatibility retains legacy id scoping, while jsonschema's default modern "
        "oracle ignores id; test_schema_resource_identifier_versions covers this compatibility policy"
    ),
    "jsonschema/nested_resources/unrecognized_id_scope/root.json": (
        "unknown-metaschema compatibility has no declared standard oracle; jsonschema warns and selects "
        "a different modern id policy; test_schema_resource_identifier_versions covers the legacy fallback"
    ),
    "jsonschema/non_finite_container_defaults.json": (
        "non-finite defaults cannot be represented in the JSON payloads hypothesis-jsonschema generates"
    ),
    "jsonschema/non_finite_number_values.json": (
        "non-finite bounds cannot be satisfied by any JSON payload hypothesis-jsonschema generates"
    ),
    "jsonschema/uuid_format_versions.json": ("versioned uuid formats reject the fixed version-4 uuid example payloads"),
    "jsonschema/prefix_items_fixed_unevaluated_tail_schema.json": (
        "hypothesis-jsonschema cannot satisfy fixed prefixItems with unevaluatedItems tail constraints"
    ),
    "jsonschema/string_min_max_items_compat.json": (
        "compatibility fixture maps string minItems/maxItems to Pydantic length constraints, "
        "which is stricter than JSON Schema"
    ),
    "jsonschema/typed_dict_allof_constraint_extra_items.json": (
        "TypedDict-only e2e fixture intentionally emits a generator warning; Pydantic payload validation "
        "uses the same schema corpus but does not target this output mode"
    ),
    "openapi/allof.yaml::components.schemas.AllOfNested3": (
        "hypothesis-jsonschema cannot satisfy the nested allOf component constraints"
    ),
    "openapi/allof_with_required_inherited_complex_allof.yaml::components.schemas.ProjectedItem": (
        "hypothesis-jsonschema generates this nested allOf schema inconsistently"
    ),
    "openapi/allof_with_required_inherited_comprehensive.yaml::components.schemas.Entity": (
        "hypothesis-jsonschema generates this inherited allOf schema inconsistently"
    ),
    "openapi/allof_with_required_inherited_comprehensive.yaml::components.schemas.ProjectedEntity": (
        "hypothesis-jsonschema generates this inherited allOf schema inconsistently"
    ),
    "openapi/allof_with_required_inherited_coverage.yaml::components.schemas.EdgeCasesCoverage": (
        "hypothesis-jsonschema generates this inherited allOf coverage schema inconsistently"
    ),
    "openapi/allof_with_required_inherited_edge_cases.yaml::components.schemas.EdgeCases": (
        "hypothesis-jsonschema generates this inherited allOf edge-case schema inconsistently"
    ),
    "openapi/allof_with_required_inherited_edge_cases.yaml::components.schemas.ProjectedEdgeCases": (
        "hypothesis-jsonschema generates this allOf edge-case schema inconsistently"
    ),
    "openapi/discriminator.yaml::components.schemas.Demo": (
        "discriminator branches overlap under JSON Schema validation; see payload_validation_future_work.md"
    ),
    "openapi/discriminator_float_mapping.yaml::components.schemas.Base": (
        "numeric discriminator mapping needs an explicit backend compatibility policy; "
        "see payload_validation_future_work.md"
    ),
    "openapi/discriminator_in_array_oneof.yaml::components.schemas.Demo": (
        "array item discriminator tags conflict with inherited JSON Schema constraints; "
        "see payload_validation_future_work.md"
    ),
    "openapi/discriminator_without_mapping.yaml::components.schemas.Demo": (
        "implicit discriminator tags conflict with source enum constraints; see payload_validation_future_work.md"
    ),
    "openapi/ref_nullable.yaml::components.schemas.NullableChild": (
        "top-level nullable object components need a wrapper policy; nullable refs are covered via Parent"
    ),
}
MISSING_SENTINEL_PAYLOAD_CASE_IDS = (
    "jsonschema/missing_sentinel_payload.json",
    "openapi/missing_sentinel_nullable.yaml::components.schemas.MissingSentinelNullable",
)
PAYLOAD_BACKEND_EXTRA_ARGS_BY_CASE_ID: dict[str, dict[PayloadBackend, tuple[str, ...]]] = {
    **{
        f"jsonschema/type_union_constraints/{name}.json": dict.fromkeys(
            (PayloadBackend.PYDANTIC_V2, PayloadBackend.PYDANTIC_V2_DATACLASS), ("--field-constraints",)
        )
        for name in ("keys_field", "keys_root", "keys_count_field", "keys_count_root")
    },
    **{
        case_id: {PayloadBackend.PYDANTIC_V2: ("--use-missing-sentinel",)}
        for case_id in MISSING_SENTINEL_PAYLOAD_CASE_IDS
    },
    "jsonschema/collapse_root_models_property_names_reference.json": dict.fromkeys(
        PayloadBackend, ("--collapse-root-models",)
    ),
    "jsonschema/msgspec_boolean_enum_literal.json": {
        PayloadBackend.MSGSPEC: ("--enum-field-as-literal", "all"),
    },
}
ROUND_TRIP_EXCLUDED_CASES: dict[str, str] = {
    **dict.fromkeys(
        (
            "jsonschema/allof_outer_constraints/integer_date_time_bounds.json",
            "jsonschema/allof_outer_constraints/number_date_time_bounds.json",
        ),
        "native Pydantic datetime JSON serialization emits strings for numeric timestamps; "
        "test_allof_outer_constraints checks the preserved numeric bounds and exact serialized values",
    ),
    "jsonschema/default_factory_nested_model_with_dict.json": (
        "pydantic union branch normalization can dump a oneOf value into a shape that matches multiple branches"
    ),
    "jsonschema/external_child_root.json": (
        "schema requires a property absent from properties, so the generated model has no field to dump"
    ),
    "jsonschema/nested_json_pointer.json": (
        "schema requires a property absent from properties, so the generated model has no field to dump"
    ),
    "jsonschema/one_of_with_sub_schema_array_item.json": (
        "pydantic serializes URI values in JSON mode, which can collapse distinct source-valid strings and violate "
        "uniqueItems"
    ),
    "jsonschema/strict_types_matrix.json": (
        "pydantic serializes Decimal JSON values as strings while the source schema requires number"
    ),
    "jsonschema/unique_items_schema_validators.json": (
        "pydantic drops extra nested object properties while dumping, which can collapse distinct uniqueItems values"
    ),
    "openapi/allof_array_ref_override.yaml::components.schemas.DataType": (
        "schema requires a property absent from properties, so the generated model has no field to dump"
    ),
    "openapi/issue_2953.yaml::components.schemas.DataType": (
        "schema requires a property absent from properties, so the generated model has no field to dump"
    ),
}
PYDANTIC_V2_FULL_PAYLOAD_RUNTIME_MIN_VERSION = "2.5.0"
PYDANTIC_V2_0_RUNTIME_MAX_VERSION = "2.1.0"
PYDANTIC_V2_MISSING_SENTINEL_RUNTIME_MIN_VERSION = "2.12.0"
PYDANTIC_V2_TYPE_ALIAS_RUNTIME_MIN_VERSION = "2.10.0"
PYDANTIC_V2_FLOAT_MULTIPLE_OF_RUNTIME_MIN_VERSION = "2.5.2"
PYDANTIC_V2_FLOAT_MULTIPLE_OF_CASE_IDS = (
    "jsonschema/numeric_constraint_precision/ordinary.json",
    "jsonschema/native_decimal_default_constrained.json",
    "jsonschema/serialized_decimal_default_multiple_of.json",
)
PYDANTIC_V2_FLOAT_MULTIPLE_OF_EXCLUSION_REASON = (
    "Pydantic before 2.5.2 can reject schema-valid float multipleOf values near float boundaries"
)
LITERAL_PATTERN_INTERSECTION_CASE_IDS = (
    "jsonschema/allof_literal_patterns/anchored.json",
    "jsonschema/allof_literal_patterns/character_class.json",
    "jsonschema/allof_literal_patterns/empty.json",
    "jsonschema/allof_literal_patterns/escaped_dollar.json",
    "jsonschema/allof_literal_patterns/escaped_dot.json",
    "jsonschema/allof_literal_patterns/escaped_metacharacters.json",
    "jsonschema/allof_literal_patterns/escaped_prefix.json",
    "jsonschema/allof_literal_patterns/escaped_reverse_prefix.json",
    "jsonschema/allof_literal_patterns/newline.json",
    "jsonschema/allof_literal_patterns/prefix.json",
    "jsonschema/allof_literal_patterns/punctuation.json",
    "jsonschema/allof_literal_patterns/reverse_prefix.json",
    "jsonschema/allof_literal_patterns/right_anchor.json",
    "jsonschema/allof_literal_patterns/split.json",
    "jsonschema/allof_literal_patterns/suffix.json",
    "jsonschema/allof_literal_patterns/triple.json",
    "jsonschema/allof_literal_patterns/unicode.json",
    "jsonschema/allof_literal_patterns/unknown_escape.json",
    "jsonschema/allof_literal_patterns/unknown_newline_escape.json",
)
PYDANTIC_V2_LEGACY_LOOKAROUND_EXCLUDED_CASES: dict[str, str] = {
    **dict.fromkeys(
        LITERAL_PATTERN_INTERSECTION_CASE_IDS,
        "Pydantic before 2.5.0 cannot apply regex_engine='python-re' to intersected literal patterns",
    ),
    "jsonschema/lookaround_anyof_nullable.json": (
        "Pydantic before 2.5.0 cannot apply regex_engine='python-re' to lookaround pattern validators"
    ),
    "jsonschema/lookaround_dict.json": (
        "Pydantic before 2.5.0 cannot apply regex_engine='python-re' to lookaround pattern validators"
    ),
    "jsonschema/lookaround_mixed_constraints.json": (
        "Pydantic before 2.5.0 cannot apply regex_engine='python-re' to lookaround pattern validators"
    ),
    "jsonschema/lookaround_union_types.json": (
        "Pydantic before 2.5.0 cannot apply regex_engine='python-re' to lookaround pattern validators"
    ),
    "jsonschema/nested_lookaround_array.json": (
        "Pydantic before 2.5.0 cannot apply regex_engine='python-re' to nested lookaround pattern validators"
    ),
    "jsonschema/root_alias_constraints/lookaround.json": (
        "Pydantic before 2.5.0 cannot apply regex_engine='python-re' to lookaround pattern validators"
    ),
    "jsonschema/schema_validators_runtime_root_cross_module/a.json": (
        "Pydantic before 2.5.0 cannot apply regex_engine='python-re' to lookaround pattern validators"
    ),
    "openapi/pattern_lookaround.yaml::components.schemas.info": (
        "Pydantic before 2.5.0 cannot apply regex_engine='python-re' to OpenAPI lookaround pattern validators"
    ),
}
PYDANTIC_V2_DATACLASS_LEGACY_LOOKAROUND_CASE_IDS = (
    "jsonschema/lookaround_anyof_nullable.json",
    "jsonschema/lookaround_dict.json",
    "jsonschema/lookaround_union_types.json",
    "jsonschema/nested_lookaround_array.json",
    "jsonschema/schema_validators_runtime_root_cross_module/a.json",
)


def _pydantic_v2_legacy_lookaround_excluded_cases(backend: PayloadBackend) -> dict[str, str]:
    """Return old-runtime lookaround exclusions supported by the selected payload backend."""
    match backend:
        case PayloadBackend.PYDANTIC_V2:
            return dict(PYDANTIC_V2_LEGACY_LOOKAROUND_EXCLUDED_CASES)
        case PayloadBackend.PYDANTIC_V2_DATACLASS:
            case_ids = PYDANTIC_V2_DATACLASS_LEGACY_LOOKAROUND_CASE_IDS
        case _:
            return {}
    return {
        case_id: reason for case_id in case_ids if (reason := PYDANTIC_V2_LEGACY_LOOKAROUND_EXCLUDED_CASES.get(case_id))
    }


PYDANTIC_V2_LEGACY_RUNTIME_EXCLUDED_CASES: dict[PayloadBackend, dict[str, str]] = {
    PayloadBackend.PYDANTIC_V2: {
        **_pydantic_v2_legacy_lookaround_excluded_cases(PayloadBackend.PYDANTIC_V2),
        "jsonschema/use_decimal_for_multiple_of.json": (
            "Pydantic before 2.5.0 can reject schema-valid Decimal multipleOf values near float boundaries"
        ),
        "openapi/allof_with_required_inherited_coverage.yaml::components.schemas.MultipleOfBase": (
            "Pydantic before 2.5.0 can reject schema-valid inherited Decimal multipleOf values"
        ),
    },
    PayloadBackend.PYDANTIC_V2_DATACLASS: {
        **_pydantic_v2_legacy_lookaround_excluded_cases(PayloadBackend.PYDANTIC_V2_DATACLASS),
        "jsonschema/use_decimal_for_multiple_of.json": (
            "Pydantic before 2.5.0 can reject schema-valid dataclass float multipleOf values near float boundaries"
        ),
    },
}
PYDANTIC_V2_FLOAT_MULTIPLE_OF_RUNTIME_EXCLUDED_CASES: dict[PayloadBackend, dict[str, str]] = {
    backend: dict.fromkeys(
        PYDANTIC_V2_FLOAT_MULTIPLE_OF_CASE_IDS,
        PYDANTIC_V2_FLOAT_MULTIPLE_OF_EXCLUSION_REASON,
    )
    for backend in (PayloadBackend.PYDANTIC_V2, PayloadBackend.PYDANTIC_V2_DATACLASS)
}
PYDANTIC_V2_TYPE_ALIAS_RUNTIME_EXCLUDED_CASES: dict[PayloadBackend, dict[str, str]] = {
    PayloadBackend.PYDANTIC_V2_DATACLASS: {
        "jsonschema/false_reference_fast_path.json": (
            "Pydantic before 2.10.0 cannot fill nested TypeAliasType aliases while building dataclass schemas"
        ),
    },
}
PYDANTIC_V2_MISSING_SENTINEL_RUNTIME_EXCLUDED_CASES: dict[PayloadBackend, dict[str, str]] = {
    PayloadBackend.PYDANTIC_V2: dict.fromkeys(
        MISSING_SENTINEL_PAYLOAD_CASE_IDS,
        "Pydantic MISSING sentinel runtime support requires Pydantic 2.12+",
    )
}
PYDANTIC_V2_0_RUNTIME_ROUND_TRIP_EXCLUDED_CASES: dict[PayloadBackend, dict[str, str]] = {
    PayloadBackend.PYDANTIC_V2: {
        "jsonschema/msgspec_payload_runtime_compatibility.json": (
            "Pydantic 2.0.x emits JSON-mode serializer warnings for object unions "
            "distinguished by boolean Literal fields"
        ),
    },
}
PYDANTIC_V2_LEGACY_RUNTIME_ROUND_TRIP_EXCLUDED_CASES: dict[PayloadBackend, dict[str, str]] = {
    PayloadBackend.PYDANTIC_V2: {
        **dict.fromkeys(
            (
                "jsonschema/compound_property_names/enum_oneof.json",
                "jsonschema/compound_property_names/enum_refs.json",
                "jsonschema/compound_property_names/extra_enum_disjoint.json",
            ),
            "Pydantic before 2.5.0 emits JSON-mode serializer warnings for enum dictionary keys",
        ),
        "jsonschema/property_names_anyof_ref.json": (
            "Pydantic before 2.5.0 emits JSON-mode serializer warnings for enum dictionary keys"
        ),
        "jsonschema/property_names_ref_enum.json": (
            "Pydantic before 2.5.0 emits JSON-mode serializer warnings for enum dictionary keys"
        ),
        "jsonschema/type_overrides_dict_key.json": (
            "Pydantic before 2.5.0 emits JSON-mode serializer warnings for enum dictionary keys"
        ),
    },
}


def _payload_target_python_version() -> str:
    if is_supported_in_black(PAYLOAD_CURRENT_PYTHON_VERSION):
        return CURRENT_PYTHON_VERSION
    supported_versions = [
        python_version
        for python_version in PythonVersion
        if python_version.version_key <= PAYLOAD_CURRENT_PYTHON_VERSION.version_key
        and is_supported_in_black(python_version)
    ]
    return max(supported_versions, key=lambda python_version: python_version.version_key).value


PAYLOAD_TARGET_PYTHON_VERSION = _payload_target_python_version()
