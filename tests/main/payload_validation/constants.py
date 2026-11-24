"""Constants for schema-derived payload validation tests."""

from __future__ import annotations

import json

from datamodel_code_generator import TargetPydanticVersion
from datamodel_code_generator.format import PythonVersion, is_supported_in_black
from tests.main.conftest import CURRENT_PYTHON_VERSION, DATA_PATH, installed_pydantic_runs_target
from tests.main.payload_validation.models import PayloadBackend

PAYLOAD_CODEGEN_WARNING_CASES: frozenset[str] = frozenset(
    json.loads((DATA_PATH / "payloads/codegen_warning_cases.json").read_text(encoding="utf-8"))
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
SERIALIZED_DECIMAL_DEFAULT_PAYLOAD_EXCLUSION_REASON = (
    "focused Decimal-default e2e fixture intentionally emits a compatibility warning in default generation"
)
_YAML_1_1_NUMBERS_EXCLUSION_REASON = (
    "unquoted 00:00:00.00 or 18_24 where a string is required, which the sweep reads as the YAML 1.1 numbers 0.0 and "
    "1824, a pattern and component names that generation refuses; test_main_yaml_1_1_numbers validates its payloads"
)
EXCLUDED_FILES: dict[str, str] = {
    "jsonschema/allof_class_hierarchy.json": "intentionally invalid JSON fixture",
    "jsonschema/allof_inheritance_errors/mro_conflict.json": "generation rejects the inconsistent allOf inheritance",
    "jsonschema/collapse_root_models_decimal_defaults.json": SERIALIZED_DECIMAL_DEFAULT_PAYLOAD_EXCLUSION_REASON,
    "jsonschema/dataclass_nested_mapping_default_chain.json": (
        "30-level chain with two references per level: hypothesis-jsonschema time roughly doubles per level "
        "(10 examples take 47 s at depth 10); test_main_dataclass_nested_mapping_default_chain covers its generation"
    ),
    "jsonschema/discriminator_enum_const_outside_enum.json": (
        "the const lies outside the Enum of the base, so no document is valid; "
        "test_use_enum_values_in_discriminator_const_outside_enum covers its generation"
    ),
    "jsonschema/discriminator_enum_unnamed_tags.json": (
        "option-only e2e fixture; payload validation intentionally uses default generation, which tags the variants "
        "by schema name; test_use_enum_values_in_discriminator_without_member validates its payloads"
    ),
    "jsonschema/field_constraint_sites/mixed_type_map_property.json": (
        "an object map beside an array in its type list drops the array branch by default "
        "(--use-single-dict-for-object-maps writes it); test_field_constraint_sites validates its map payloads"
    ),
    "jsonschema/msgspec_number_limits/float_range_bounds.json": (
        "number bounds beyond the float range make hypothesis-jsonschema raise OverflowError; "
        "test_msgspec_number_limits validates its msgspec payloads"
    ),
    "jsonschema/msgspec_str_like_number_members.json": (
        "Pydantic reads its number time-delta as a timedelta of at most 999,999,999 days and dumps its number decimal "
        "as a string; test_msgspec_str_like_number_members validates its msgspec payloads"
    ),
    "jsonschema/msgspec_wide_int_enums.json": (
        "Pydantic refuses JSON ints beyond the 64-bit range as Enum and Literal values (int_parsing_size); "
        "test_msgspec_wide_int_enums validates its msgspec payloads"
    ),
    "jsonschema/msgspec_wide_int_tags.json": (
        "Pydantic refuses JSON ints beyond the 64-bit range as Literal values (int_parsing_size); "
        "test_msgspec_wide_int_tags validates its msgspec payloads"
    ),
    "jsonschema/non_dict_files/list_only.json": "input is JSON data, not a JSON Schema document",
    "jsonschema/non_dict_files/list_only.yaml": "input is YAML data, not a JSON Schema document",
    "jsonschema/non_dict_files/whitespace_only.yaml": "empty YAML fixture",
    "jsonschema/non_json_object.json": "input is JSON data, not a JSON Schema document",
    "jsonschema/null.json": "intentionally invalid JSON fixture",
    "jsonschema/numeric_constraint_precision/beyond_float_range.json": (
        "number bounds beyond the float range make hypothesis-jsonschema raise OverflowError; "
        "test_number_bounds_beyond_float_range validates its payloads"
    ),
    "jsonschema/numeric_constraint_precision/non_finite_multiple_of.json": (
        "a multipleOf beyond the float range has no JSON value for the payload generator; "
        "test_non_finite_multiple_of_left_out_accepts_non_multiples validates its payloads"
    ),
    "jsonschema/ref_to_json_list/list.json": "referenced JSON data list, not a schema document",
    "jsonschema/schema_validators_multiple_aliases_property_count.json": (
        "opt-in schema-validator e2e fixture; payload validation intentionally uses default generation"
    ),
    "jsonschema/type_alias_lookaround/multiple_bases.json": (
        "msgspec.Struct generation rejects two generated bases with fields; test_type_alias_lookaround validates it"
    ),
    "jsonschema/rust_only_syntax.json": (
        "Rust-only regex syntax that Python's re rejects or warns about; test_main_rust_only_syntax validates it"
    ),
    "jsonschema/rust_unsupported_atomic_group.json": (
        "Python-only regex syntax switches engines only for --target-python-version 3.11+; "
        "test_main_rust_unsupported_target_python validates the generated models"
    ),
    "jsonschema/rust_unsupported_end_of_string.json": (
        "Python-only regex syntax switches engines only for --target-python-version 3.14+; "
        "test_main_rust_unsupported_target_python validates the generated models"
    ),
    "jsonschema/rust_unsupported_python_rejected.json": (
        "patterns both regex engines reject fail to import under either engine"
    ),
    "jsonschema/rust_unsupported_sizes_and_dialects.json": (
        "patterns past Rust's size limit, ones no engine compiles and a class Python's re warns about; "
        "test_patterns_no_engine_compiles_as_written validates the generated models"
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
    "jsonschema/string_constraints_on_emitted_types.json": (
        "e2e fixture that intentionally emits generator warnings for the string keywords its types leave out; "
        "test_main_string_constraints_on_emitted_types validates its payloads"
    ),
    "jsonschema/strict_types_constraints.json": (
        "maxLength of a binary string counts JSON characters while the generated bytes count UTF-8 bytes, as "
        "without --strict-types; test_main_strict_types_keep_constraints validates its payloads"
    ),
    "jsonschema/unique_items_prefix_items_draft7.json": (
        "opt-in schema-validator dialect e2e fixture; Draft 7 ignores prefixItems, "
        "which hypothesis-jsonschema cannot satisfy for generic payload validation"
    ),
    "jsonschema/unknown_format.json": "unknown format fixture intentionally emits a generator warning",
    "jsonschema/format_string_keywords.json": (
        "option-only e2e fixture of --keep-string-keywords-beside-formats; payload validation intentionally uses "
        "default generation, which drops the keywords; test_keep_string_keywords_beside_formats and the decision "
        "tables validate its payloads"
    ),
    "jsonschema/format_string_keywords_all_of.json": (
        "option-only e2e fixture of --keep-string-keywords-beside-formats; payload validation intentionally uses "
        "default generation, which drops the keywords; test_keep_string_keywords_beside_formats_all_of validates its "
        "output"
    ),
    "jsonschema/uuid_version_formats.json": (
        "option-only e2e fixture of --read-uuid6-uuid7-uuid8-formats; payload validation intentionally uses default "
        "generation, which types the formats as str; test_read_uuid6_uuid7_uuid8_formats validates its payloads"
    ),
    "openapi/complex_reference.json": "intentionally invalid JSON fixture",
    "openapi/list.json": "input is JSON data, not an OpenAPI document",
    "openapi/msgspec_str_like_number_members.yaml": (
        "Pydantic dumps its number decimal as a string; test_msgspec_str_like_number_members_openapi pins its msgspec "
        "output"
    ),
    "openapi/not.json": "intentionally invalid JSON fixture",
    "openapi/subclass_enum.json": "intentionally invalid JSON fixture",
    **dict.fromkeys(
        (f"openapi/yaml_1_1_numbers/{name}.yaml" for name in ("names", "pattern", "positions")),
        _YAML_1_1_NUMBERS_EXCLUSION_REASON,
    ),
    "openapi/uuid_version_formats.json": (
        "option-only e2e fixture of --read-uuid6-uuid7-uuid8-formats; payload validation intentionally uses default "
        "generation, which types the formats as str; test_read_uuid6_uuid7_uuid8_formats validates its payloads"
    ),
    "openapi/yaml_1_1_numbers/ratio.yaml": (
        "the string enum [16:9, 4:3], which the sweep reads as the YAML 1.1 numbers 969 and 243, admits no payload"
    ),
    "openapi/yaml_1_1_numbers/still_refused.yaml": "generation refuses its maxLength `ten` in every reading",
}
ALLOF_REF_SIBLING_DIAGNOSTICS = json.loads(
    (DATA_PATH / "payloads/allof_ref_sibling_diagnostics.json").read_text(encoding="utf-8")
)
_DIAMOND = "openapi/discriminator_nested_variant_diamond"
_UNCONSTRAINED_TAG_REASON = (
    "OpenAPI selects a variant by its tag, which JSON Schema validation does not read, and the suite constrains the "
    "tag only of a branch that declares a string tag property of its own; "
)
_OVERLAPPING_BRANCHES = (
    ", so the branches overlap and hypothesis-jsonschema draws no payload that one branch alone takes"
)
_DISTRIBUTE_ALLOF_REASON = (
    "default output drops the allOf member beside the oneOf or anyOf, so the model has no required id; "
    "--distribute-allof-over-unions writes it and test_schema_intersection_shape_payloads checks it"
)
_TAG_CLASH_WARNING_REASON = (
    "generation warns that a variant which two discriminated unions tag differently cannot be decoded by one of them"
)
_INCOMPLETE_DEFAULT_WARNING_REASON = (
    "generation warns that a default lacks required fields of its model, which --omit-incomplete-model-defaults leaves "
    "out"
)
EXCLUDED_CASES: dict[str, str] = {
    "jsonschema/diamond_inherited_field_override.json": (
        "D's allOf types p as an integer and as a string, so no payload holds d; "
        "test_main_jsonschema_diamond_inherited_field_override pins its output and imports it"
    ),
    "jsonschema/enum_const_unsatisfiable.json": (
        "each const is none of its enum values, so no payload holds those properties; "
        "test_main_jsonschema_enum_const_unsatisfiable pins its output and warnings"
    ),
    "openapi/empty_enum.yaml::components.schemas.Size": (
        "its enum is empty, so the schema accepts no payload; test_empty_enum_warning pins its output and warnings"
    ),
    "openapi/discriminator_pattern_mismatch.yaml::components.schemas.Cat": (
        "its const tag is outside its pattern, so the schema accepts no payload; "
        "test_main_openapi_discriminator_pattern_mismatch pins its output and warnings"
    ),
    "jsonschema/number_constraints_on_mapped_formats.json": (
        "the boolean numeric format needs --type-mappings; "
        "test_main_number_constraints_on_mapped_formats checks the mapped number bound"
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
            "jsonschema/compound_property_names/unsupported_one_sibling.json",
            "jsonschema/compound_property_names/unsupported_sibling.json",
        ),
        "a propertyNames anyOf with a sibling keyword is written as Any keys, which accept keys the schema rejects; "
        "test_compound_property_names_compatibility covers them",
    ),
    "jsonschema/property_names_root_model_mixed_union_keys.json": (
        "a union key with a non-RootModel member keeps its RootModel class unless "
        "--use-root-types-for-mixed-union-map-keys is set, as in 0.100.0, so a key that only the RootModel matches "
        "raises TypeError; "
        "test_property_names_root_model_mixed_union_keys covers both sides"
    ),
    **{
        f"jsonschema/allof_ref_siblings/{name}.json": (
            "empty literal intersection, so no payload is valid; test_allof_ref_sibling_empty_intersection pins its "
            "output and warning"
        )
        for name in ALLOF_REF_SIBLING_DIAGNOSTICS
    },
    "jsonschema/all_of_any_of_base_class_ref.json": "hypothesis-jsonschema cannot satisfy the allOf/anyOf constraints",
    "jsonschema/additional_properties_schema_with_allof_properties.json": (
        "hypothesis-jsonschema cannot satisfy the allOf object constraints; typed extras are covered by e2e tests"
    ),
    "jsonschema/integer_fractional_constraints.json": (
        "hypothesis-jsonschema emits integers near float precision limits where multipleOf checks are unstable"
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
    "jsonschema/non_finite_enum_members.yaml": (
        "non-finite enum members cannot be represented in the JSON payloads hypothesis-jsonschema generates; "
        "test_main_jsonschema_non_finite_enum_members imports the output of every backend"
    ),
    "jsonschema/non_finite_number_values.json": (
        "non-finite bounds cannot be satisfied by any JSON payload hypothesis-jsonschema generates"
    ),
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
    "openapi/allof_scalar_roots.yaml::components.schemas.Anything": (
        "an allOf of untyped constraint members stays an empty model until flat untyped constraints are fixed"
    ),
    "openapi/allof_scalar_roots.yaml::components.schemas.TwoPatterns": (
        "hypothesis-jsonschema seldom draws a string that meets both of its patterns; "
        "test_schema_intersection_shape_payloads checks the same allOf of two patterns (root_forms)"
    ),
    "jsonschema/schema_intersection/allof_values.json": (
        "pins the 0.85.0 output G01 PR 3 fixes: the root allOf of untyped members is an empty model that "
        "rejects valid values; test_schema_intersection_shape_payloads records it"
    ),
    "jsonschema/schema_intersection/allof_ref_item_constraints.json": (
        "pins the 0.85.0 output G01 PR 5 fixes: an item allOf of two scalar references is an empty model that "
        "rejects valid values; test_schema_intersection_shape_payloads records it"
    ),
    "jsonschema/schema_intersection/allof_union_parts.json": (
        "pins the 0.89.0 output --distribute-allof-over-unions fixes: the branches of a second anyOf of an allOf "
        "replace the first's and $ref branches join one union; test_schema_intersection_shape_payloads records it"
    ),
    "jsonschema/schema_intersection/allof_union_through_ref.json": (
        "pins the 0.89.0 output --distribute-allof-over-unions fixes: an allOf member naming an allOf over a union "
        "is a base later passes drop; test_schema_intersection_shape_payloads records it"
    ),
    "jsonschema/schema_intersection/allof_patterns.json": (
        "hypothesis-jsonschema cannot draw a $ref whose sibling pattern differs from its target's; "
        "test_schema_intersection_shape_payloads checks the pattern conjunctions"
    ),
    "jsonschema/schema_intersection/enum_refs.json": (
        "pins the 0.85.0 output G01 PR 5 fixes: an allOf of two enum references warns about duplicated members; "
        "test_schema_intersection_shape_output records it"
    ),
    "openapi/allof_with_required_inherited_comprehensive.yaml::components.schemas.Entity": (
        "hypothesis-jsonschema generates this inherited allOf schema inconsistently"
    ),
    "openapi/allof_with_required_inherited_comprehensive.yaml::components.schemas.ProjectedEntity": (
        "legacy-lookahead-patterns: the patterns of allof_with_pattern are joined as (?=^[a-z]+)(?=[0-9]$), which no "
        "string matches, so the model refuses a valid value such as a0; --intersect-allof-keywords writes them apart"
    ),
    "openapi/allof_with_required_inherited_coverage.yaml::components.schemas.EdgeCasesCoverage": (
        "generation warns that the constraint beside its allOf $ref is ignored in inherited field type resolution"
    ),
    "openapi/discriminator.yaml::components.schemas.Demo": (
        "discriminator branches overlap under JSON Schema validation; see payload_validation_future_work.md"
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
    **dict.fromkeys(
        (
            "openapi/strip_default_none_constructor_order.yaml::components.schemas.Nullable",
            "openapi/strip_default_none_constructor_order.yaml::components.schemas.NullableLater",
        ),
        "top-level nullable object components need a wrapper policy; "
        "test_main_openapi_strip_default_none_constructor_order imports the models that reference them",
    ),
    "openapi/redundant_reference_null_oas30.yaml::components.schemas.NullObj": (
        "top-level nullable object components need a wrapper policy; "
        "test_redundant_reference_null pins the models that reference it"
    ),
    "openapi/strip_default_none_nullable_reference.yaml::components.schemas.S": (
        "top-level nullable object components need a wrapper policy; "
        "test_main_openapi_strip_default_none_nullable_reference pins the models that reference it"
    ),
    **dict.fromkeys(
        (
            "openapi/schema_intersection/null_multiple_types_oas30.yaml::components.schemas.U",
            "openapi/schema_intersection/null_multiple_types_oas30.yaml::components.schemas.V",
            "openapi/schema_intersection/null_multiple_types_oas30.yaml::components.schemas.W",
        ),
        "a top-level type list holding object is a model beside the root model that holds it, and the payload type "
        "is the model; test_schema_intersection_shape_payloads checks them through Model",
    ),
    **dict.fromkeys(
        (
            "openapi/nullable_reuse_collapse.yaml::components.schemas.Home",
            "openapi/nullable_reuse_collapse.yaml::components.schemas.Shop",
        ),
        "top-level nullable object components need a wrapper policy; nullable refs are covered via Person",
    ),
    "openapi/type_overrides_constructor_order.yaml::components.schemas.Nullable": (
        "top-level nullable object components need a wrapper policy; "
        "test_main_openapi_type_overrides_constructor_order imports the models that reference it"
    ),
    "jsonschema/collapse_root_models_lookaround_discriminator.json": (
        "discriminator tags come from a referenced pattern rather than an enum, so schema-valid tags miss the mapping; "
        "test_main_jsonschema_collapse_root_models_lookaround checks the generated tags and regex engine"
    ),
    "jsonschema/discriminator_mapping_msgspec_tags.json": (
        "discriminator tags come only from $defs mappings and schema names, which payload sampling does not constrain; "
        "test_main_msgspec_discriminator_mapping_tags checks the generated tags"
    ),
    **dict.fromkeys(
        (
            "openapi/allof_member_declarations_rw.yaml::components.schemas.Child",
            "openapi/allof_member_declarations_rw.yaml::components.schemas.Members",
            "openapi/allof_member_declarations_rw.yaml::paths./children.post.requestBody.content.application/json",
            "openapi/allof_member_declarations_rw.yaml::paths./children.post.responses.200.content.application/json",
        ),
        "two allOf members declare mid, which generation warns about as a duplicated field",
    ),
    "openapi/allof_with_required_inherited_coverage.yaml::components.schemas.NestedAllOfWithoutDirectRef": (
        "generation warns that the constraint beside its allOf $ref is ignored in inherited field type resolution"
    ),
    "openapi/component_embedded_ids/api.yaml::components.schemas.Pet": (
        "generation warns that its #/components/schemas/Tag reference is resolved against the document, where JSON "
        "Schema resolves it against the component's $id pet.json"
    ),
    "openapi/allof_no_merge_boolean_false_nested.yaml::components.schemas.Derived": (
        "generation refuses the false schema nested in its inherited allOf; "
        "test_main_openapi_allof_no_merge_boolean_false_schema_errors pins the error"
    ),
    "openapi/discriminator_external_mapping/openapi.yaml::components.schemas.Wrapper": (
        "its discriminator mapping names schemas/foo.yaml beside the document, which the case's temporary input lacks"
    ),
    **dict.fromkeys(
        (
            "openapi/discriminator_merged_nested_variant_tags.yaml::components.schemas.Holder",
            "openapi/discriminator_merged_variant_tag_limits.yaml::components.schemas.Holder",
            "openapi/discriminator_merged_variant_tags.yaml::components.schemas.Holder",
            "openapi/discriminator_merged_variant_tags_nested_clash.yaml::components.schemas.Holder",
            "openapi/discriminator_read_write_variant_tags.yaml::components.schemas.Owner",
            "openapi/discriminator_read_write_variant_tags.yaml::paths./owner.get.responses.200.content.application/json",
        ),
        _TAG_CLASH_WARNING_REASON,
    ),
    **dict.fromkeys(
        (
            "openapi/field_render_plan.yaml::components.schemas.RenderPlan",
            "openapi/incomplete_model_defaults_strip_default_none.yaml::components.schemas.Workload",
        ),
        _INCOMPLETE_DEFAULT_WARNING_REASON,
    ),
    "openapi/union_mode_constrained_union.yaml::components.schemas.Child": (
        "generation warns that Pydantic does not take min_length on the ListOrText union of tags"
    ),
    "openapi/discriminator_pattern.yaml::components.schemas.PaymentRequest": (
        "a pattern uses \\p{...}, which Python's re rejects, and the suite checks patterns with re"
    ),
    "openapi/annotated_model_defaults.yaml::components.schemas.Broken": (
        "its default 'many' for an integer violates its schema, and the generated model validates the default; "
        "test_main_openapi_annotated_model_defaults checks that Broken() fails"
    ),
    "openapi/annotated_model_container_defaults.yaml::components.schemas.Kennel": (
        "its default -1 is below the minimum 0 of its schema, and the generated model validates the default; "
        "test_main_openapi_annotated_model_container_defaults checks that Kennel() fails"
    ),
    "openapi/not_real_string.json::components.schemas.ImageHash": (
        "its lengths 64 contradict the length 32 of the FileHash it references, so no string is valid"
    ),
    "openapi/definitions_first.yaml::components.schemas.event": (
        "both oneOf branches take any object without created and deleted strings, so hypothesis-jsonschema draws no "
        "payload that one branch alone takes"
    ),
    "openapi/schema_intersection/inherited_override_null_oas31.yaml::components.schemas.NamedCat": (
        "hypothesis-jsonschema draws no payload for the allOf of Cat and the member that requires seven overrides"
    ),
    **dict.fromkeys(
        (
            "openapi/schema_intersection/allof_beside_site_discriminator.yaml::components.schemas.Mapped",
            "openapi/schema_intersection/allof_beside_site_discriminator.yaml::components.schemas.Named",
            "openapi/schema_intersection/allof_beside_union_oas31.yaml::components.schemas.Holder",
            "openapi/schema_intersection/allof_beside_union_oas31.yaml::components.schemas.Pet",
            "openapi/schema_intersection/allof_beside_union_oas31.yaml::paths./pets.post.requestBody.content.application/json",
            "openapi/schema_intersection/allof_beside_union_oas31.yaml::paths./pets.post.responses.200.content.application/json",
        ),
        _DISTRIBUTE_ALLOF_REASON,
    ),
    "openapi/allof_member_parts_request_response.yaml::components.schemas.M": (
        "default output drops the required extra beside an allOf member's $ref and the members of the $ref alias Root; "
        "--intersect-combined-schema-keywords reads them and test_main_openapi_allof_member_parts_request_response "
        "pins it"
    ),
    **dict.fromkeys(
        (
            "openapi/variant_tag_values/integer_values_mapped.yaml::components.schemas.Pet",
            "openapi/variant_tag_values/several_values_mapped.yaml::components.schemas.Pet",
            "openapi/variant_tag_values/several_values_mapped_twice.yaml::components.schemas.Pet",
            "jsonschema/discriminator_mapping_key_after_union.json",
            *(
                f"openapi/variant_tag_values/{kind}_key_{order}_union.yaml::components.schemas.{union}"
                for kind in ("mapping", "enum")
                for order in ("after", "before")
                for union in ("First", "Second")
            ),
        ),
        "legacy-mapped-tags-only: a variant a mapping key names takes only its mapping keys, not the other values its "
        "tag property admits; --use-unmapped-variant-tag-values takes them and test_variant_tag_values pins both",
    ),
    **dict.fromkeys(
        (
            "openapi/variant_tag_values/nullable_tags.yaml::components.schemas.ConstOptional",
            "openapi/variant_tag_values/nullable_tags.yaml::components.schemas.Nested",
            "openapi/variant_tag_values/nullable_tags.yaml::components.schemas.RefOrNull",
        ),
        "a union whose variants' tags admit null is written without discriminator= (#7102), so a null tag both "
        "variants admit, which oneOf refuses, is accepted and dumps as either; under --strict-nullable the tag "
        "written for an anyOf of a $ref and null drops the null, so that union keeps discriminator= and refuses the "
        "null only Cat admits, as in 0.100.0; test_tags_that_admit_null pins the verdicts",
    ),
    **dict.fromkeys(
        (
            "openapi/variant_tag_values/mapping_key_of_referenced_type.yaml::components.schemas.Payment",
            "openapi/variant_tag_values/mapping_key_of_referenced_type.yaml::components.schemas.Refund",
        ),
        "legacy-tag-type-from-earlier-union: a mapping key of a tag property that names an integer schema stays "
        "text, so the union refuses the integer tag the schema requires, as since 0.65.0; "
        "--read-mapping-keys-by-schema-tag-type reads it as an integer and test_mapping_key_of_a_referenced_tag_type "
        "pins both",
    ),
    "openapi/variant_tag_values/object_values.yaml::components.schemas.Pet": (
        "Cat's tag property admits only objects, which tag no variant, as in 0.97.0; "
        "test_object_values_of_a_tag_property pins it"
    ),
    **dict.fromkeys(
        (
            f"openapi/variant_tag_values/scalar_kind_mapped.yaml::components.schemas.{name}Union"
            for name in (
                *("Int", "BoundedInt", "Number", "BoundedNumber", "FractionNumber", "DecimalNumber", "MultipleNumber"),
                *("Flag", "PositiveInt", "NegativeInt", "NonNegativeInt", "NonPositiveInt", "PositiveNumber"),
                *("NegativeNumber", "NonNegativeNumber", "NonPositiveNumber"),
            )
        ),
        _UNCONSTRAINED_TAG_REASON + "here the variants take an integer, number or boolean tag from a scalar type, so "
        "a payload tagged 2 with the first variant's properties is schema-valid; "
        "test_scalar_tag_mapping_keys pins the tags",
    ),
    "openapi/variant_tag_values/integer_kind_mapped.yaml::components.schemas.Pet": (
        _UNCONSTRAINED_TAG_REASON
        + "here both variants take their integer tag from one enum $ref, so a payload tagged 1 "
        "with Dog's properties is schema-valid"
    ),
    **dict.fromkeys(
        (
            "openapi/discriminator_alias_generator.yaml::components.schemas.Pet",
            "openapi/discriminator_lone_variant_tags.yaml::components.schemas.Both",
            f"{_DIAMOND}/optional_tag_restated_by_the_second_base.yaml::components.schemas.Holder",
        ),
        _UNCONSTRAINED_TAG_REASON + "here a branch declares no tag property or leaves it optional, so a payload "
        "without the tag is schema-valid and the discriminated union refuses it",
    ),
    "openapi/discriminator_inlined_list_items/remapped_variant_with_tagged_base.yaml::components.schemas.Method": (
        _UNCONSTRAINED_TAG_REASON + "here CardV2 takes its kind through allOf, so a payload carries a tag no variant "
        "takes"
    ),
    "openapi/schema_intersection/ref_branches_beside_properties.yaml::components.schemas.Holder": (
        _UNCONSTRAINED_TAG_REASON + "here Owned tags Cat and Dog by their names and Mapped by c and d, which one "
        "constraint per component cannot hold at once"
    ),
    "openapi/schema_intersection/allof_beside_discriminated_branch.yaml::components.schemas.Based": (
        _UNCONSTRAINED_TAG_REASON + "here Shape maps kind to Square and Circle, which do not extend it, so a Shape "
        "payload tagged ci need not hold Circle's radius"
    ),
    **dict.fromkeys(
        (
            "openapi/discriminator_typed_mapping_keys.yaml::components.schemas.ByBoolean",
            "openapi/discriminator_typed_mapping_keys.yaml::components.schemas.ByBooleanAgain",
            "openapi/discriminator_typed_mapping_keys.yaml::components.schemas.ByFraction",
            "openapi/discriminator_typed_mapping_keys.yaml::components.schemas.ByInteger",
            "openapi/discriminator_typed_mapping_keys.yaml::components.schemas.ByIntegerAgain",
            "openapi/discriminator_typed_mapping_keys.yaml::components.schemas.ByWholeNumber",
        ),
        _UNCONSTRAINED_TAG_REASON
        + "here the mapping keys are integer, number and boolean tags"
        + _OVERLAPPING_BRANCHES,
    ),
    **dict.fromkeys(
        (
            "openapi/discriminator_lone_variant_tags.yaml::components.schemas.MemberBoth",
            "openapi/open_enums.yaml::components.schemas.Pet",
            "openapi/shared_enum_discriminator/repeated.yaml::components.schemas.Pet",
        ),
        _UNCONSTRAINED_TAG_REASON + "here the variants take their tag from one enum $ref" + _OVERLAPPING_BRANCHES,
    ),
    **dict.fromkeys(
        (
            "openapi/discriminator_field_aliases/missing.json::components.schemas.Pet",
            "openapi/discriminator_field_aliases/wire_collision_missing.json::components.schemas.Pet",
            "openapi/field_options_every_field.yaml::components.schemas.Pet",
            "openapi/discriminator_inlined_list_items/tag_named_like_enum.yaml::components.schemas.Pet",
            "openapi/discriminator_shared_written_tag/tag_named_like_enum_list_items.yaml::components.schemas.Pet",
        ),
        _UNCONSTRAINED_TAG_REASON
        + "here variants take their tag property through allOf or declare none"
        + _OVERLAPPING_BRANCHES,
    ),
    "openapi/discriminator_merged_variant_tag_limits.yaml::components.schemas.ByName": (
        _UNCONSTRAINED_TAG_REASON + "here other unions of the document tag Cat and Dog by other keys, which one "
        "constraint per component cannot hold at once" + _OVERLAPPING_BRANCHES
    ),
}
MISSING_SENTINEL_PAYLOAD_CASE_IDS = (
    "jsonschema/missing_sentinel_payload.json",
    "openapi/missing_sentinel_nullable.yaml::components.schemas.MissingSentinelNullable",
)
TYPE_ALIAS_TYPE_PAYLOAD_ARGS = ("--use-type-alias", "--target-python-version", "3.11")
"""Write roots as ``TypeAliasType``: Pydantic before 2.10 cannot build models that use ``type`` statements."""
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
    **{
        f"jsonschema/type_alias_lookaround/{name}.json": {PayloadBackend.PYDANTIC_V2: TYPE_ALIAS_TYPE_PAYLOAD_ARGS}
        for name in ("nested", "siblings", "inherited")
    },
    "jsonschema/type_alias_lookaround/annotated_root.json": {PayloadBackend.PYDANTIC_V2: ("--use-annotated",)},
    "jsonschema/discriminator_msgspec_wire_name.json": {PayloadBackend.MSGSPEC: ("--tag-msgspec-by-payload-name",)},
    "jsonschema/discriminator_mapped_variant_several_values.json": dict.fromkeys(
        PayloadBackend, ("--use-unmapped-variant-tag-values",)
    ),
    "jsonschema/redundant_constraints.json": {
        backend: (
            "--use-non-positive-negative-number-constrained-types",
            "--use-single-value-enum-as-const",
            "--omit-redundant-constraints",
            form,
        )
        for backend, form in (
            (PayloadBackend.PYDANTIC_V2, "--use-annotated"),
            (PayloadBackend.PYDANTIC_V2_DATACLASS, "--field-constraints"),
        )
    },
    "jsonschema/decimal_multiple_of_field_forms.json": {
        PayloadBackend.PYDANTIC_V2: (
            "--use-decimal-for-multiple-of",
            "--use-decimal-in-field-forms",
            "--use-annotated",
        ),
        PayloadBackend.PYDANTIC_V2_DATACLASS: (
            "--use-decimal-for-multiple-of",
            "--use-decimal-in-field-forms",
            "--field-constraints",
        ),
    },
    **{
        f"jsonschema/{name}.json": dict.fromkeys(PayloadBackend, ("--collapse-root-models",))
        for name in (
            "collapse_root_models_lookaround",
            "collapse_root_models_lookaround_inheritance",
            "collapse_root_models_lookaround_root",
            "collapse_root_models_property_names_reference",
        )
    },
    **{
        f"jsonschema/{name}.json": dict.fromkeys(PayloadBackend, ("--read-const-beside-enum",))
        for name in (
            "enum_const_collapsed_root_models",
            "enum_const_discriminator_tag",
            "enum_const_draft_04",
            "enum_const_forward_positions",
            "enum_const_hidden_by_field",
            "enum_const_integral_float",
            "enum_const_integral_float_literal",
            "enum_const_non_literal",
            "enum_const_positions",
            "enum_const_titles",
            "enum_const_type_list",
            "read_as_const_marker_in_document",
        )
    },
    "jsonschema/enum_multiple_of_big_integer.json": dict.fromkeys(PayloadBackend, ("--filter-literal-values",)),
    "jsonschema/tool_input_formats/tool_input_formats.json": dict.fromkeys(
        PayloadBackend, ("--read-format-as-annotation",)
    ),
    "jsonschema/event_log_untyped/event_log_untyped.json": dict.fromkeys(
        (PayloadBackend.PYDANTIC_V2, PayloadBackend.PYDANTIC_V2_DATACLASS),
        ("--untyped-value-type", "pydantic.JsonValue"),
    ),
    **{
        f"jsonschema/{name}.json": dict.fromkeys(PayloadBackend, ("--keep-enum-member-values",))
        for name in ("enum_value_base_kinds", "msgspec_enum_value_base_kept")
    },
    **{
        case_id: dict.fromkeys(PayloadBackend, ("--intersect-allof-keywords",))
        for case_id in (
            "jsonschema/allof_mapping_every_part_null.json",
            "openapi/schema_intersection/root_site_null_oas30.yaml::components.schemas.InlineArray",
        )
    },
    "jsonschema/msgspec_boolean_enum_literal.json": {
        PayloadBackend.MSGSPEC: ("--enum-field-as-literal", "all"),
    },
    "jsonschema/items_array_additional_items_false.json": dict.fromkeys(
        PayloadBackend, ("--use-tuple-for-fixed-items", "--use-tuple-for-closed-arrays")
    ),
    "jsonschema/prefix_items_closed_length.json": dict.fromkeys(PayloadBackend, ("--use-tuple-for-closed-arrays",)),
    "openapi/nullable_object_maps.yaml::components.schemas.ValueRoot": dict.fromkeys(
        PayloadBackend, ("--use-single-dict-for-object-maps",)
    ),
    "jsonschema/nullable_reuse_collapse.json": dict.fromkeys(
        PayloadBackend, ("--strict-nullable", "--reuse-model", "--collapse-reuse-models")
    ),
    "jsonschema/keep_reference_null_on_merged_models.json": dict.fromkeys(
        PayloadBackend, ("--strict-nullable", "--reuse-model", "--keep-reference-null-on-merged-models")
    ),
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
    "jsonschema/number_constraints_on_numeric_string_formats.json": (
        "the integer and unixtime string formats are typed int, which dumps a JSON number where the source schema "
        "requires a string"
    ),
    "jsonschema/one_of_with_sub_schema_array_item.json": (
        "pydantic serializes URI values in JSON mode, which can collapse distinct source-valid strings and violate "
        "uniqueItems"
    ),
    **dict.fromkeys(
        (
            "jsonschema/decimal_multiple_of_field_forms.json",
            "jsonschema/msgspec_union_shared_bounds.json",
            "jsonschema/strict_types_matrix.json",
        ),
        "pydantic serializes Decimal JSON values as strings while the source schema requires number",
    ),
    "jsonschema/type_alias_annotated/shadowed_imports.json": (
        "pydantic serializes Decimal JSON values as strings while the source schema requires number"
    ),
    "jsonschema/literal_constraints.json": (
        "a Python Enum holding both 1 and true keeps one member for the two, so the dump of true is 1, which the "
        "source schema's minimum rejects"
    ),
    **dict.fromkeys(
        (
            "jsonschema/nested_unique_item_sets.json",
            "jsonschema/unique_items_by_value.json",
            "jsonschema/unique_items_collapsed_root_items.json",
            "jsonschema/unique_items_model_items.json",
            "jsonschema/unique_items_schema_validators.json",
        ),
        "pydantic drops extra nested object properties while dumping, which can collapse distinct uniqueItems values",
    ),
    "jsonschema/use_tag_as_union_variant_name_edges.json": (
        "pydantic union branch normalization can dump a oneOf value into a shape that matches multiple branches"
    ),
    "openapi/allof_array_ref_override.yaml::components.schemas.DataType": (
        "schema requires a property absent from properties, so the generated model has no field to dump"
    ),
    **dict.fromkeys(
        (
            "openapi/allof_array_ref_override.yaml::components.schemas.PaginatedDataTypeList",
            "openapi/not_real_string.json::components.schemas.ImageRequest",
        ),
        "schema requires a property absent from properties, so the generated model has no field to dump",
    ),
    **dict.fromkeys(
        (
            *(
                f"openapi/read_only_write_only_variant_graph.yaml::{name}"
                for name in (
                    "components.schemas.ConditionalEnvelope",
                    "components.schemas.ConditionalForwardWrapper",
                    "paths./conditional.post.requestBody.content.application/json",
                )
            ),
        ),
        "the properties its if/then/else adds are no fields of the model, so the dump drops them and holds fewer than "
        "minProperties 2",
    ),
    **dict.fromkeys(
        (
            "openapi/discriminator_duplicate_value.yaml::components.schemas.GroupedItem",
            "openapi/discriminator_duplicate_value.yaml::components.schemas.UniqueOuter",
            "openapi/discriminator_enum_tag_variants.yaml::components.schemas.Pets",
            "openapi/discriminator_lone_variant_tags.yaml::components.schemas.LoneNullableWrapper",
            "openapi/discriminator_merged_nested_variant_tags.yaml::components.schemas.Outer",
            "openapi/discriminator_merged_variant_tag_limits.yaml::components.schemas.Outer",
            "openapi/discriminator_merged_variant_tags_nested_clash.yaml::components.schemas.Coast",
            "openapi/discriminator_merged_variant_tags_nested_clash.yaml::components.schemas.Middle",
            "openapi/discriminator_merged_variant_tags_nested_clash.yaml::components.schemas.Outer",
            "openapi/discriminator_merged_variant_tags_nested_clash.yaml::components.schemas.Pond",
            "openapi/discriminator_merged_variant_tags_nested_clash.yaml::components.schemas.Tank",
            "openapi/discriminator_merged_variant_tags_nested_clash.yaml::components.schemas.Top",
            "openapi/discriminator_nested_variant_defaults.yaml::components.schemas.ChainedPets",
            "openapi/discriminator_nested_variant_defaults.yaml::components.schemas.Pets",
            "openapi/discriminator_nested_variant_defaults.yaml::components.schemas.PlainPets",
            "openapi/discriminator_nested_variant_diamond/shallow_diamond.yaml::components.schemas.Holder",
            "openapi/discriminator_optional_literal_nested.yaml::components.schemas.RootChoice",
            "openapi/discriminator_pattern_nested_choices.yaml::components.schemas.Outer",
            "openapi/variant_tag_values/nested_union_of_several_values.yaml::components.schemas.Pet",
        ),
        "Pydantic warns while it dumps a discriminated union that lists a union root model as a variant "
        "(PydanticSerializationUnexpectedValue), which fails the run that turns warnings into errors (#7326)",
    ),
}
REJECTION_EXCLUDED_CASES: dict[str, str] = {
    **dict.fromkeys(
        (
            "openapi/discriminator_inlined_list_items/tag_named_like_enum.yaml::components.schemas.Shelter",
            "openapi/discriminator_msgspec_tagged_unions_base_earlier.yaml::components.schemas.Owner",
            "openapi/discriminator_msgspec_tagged_unions_base_later.yaml::components.schemas.Owner",
            "openapi/discriminator_msgspec_tagged_unions_inherited.yaml::components.schemas.Garage",
            "openapi/discriminator_msgspec_tagged_unions_inherited.yaml::components.schemas.Shed",
            "openapi/discriminator_msgspec_tagged_unions_inherited.yaml::components.schemas.Zoo",
            "openapi/discriminator_plain_enum_tags.yaml::components.schemas.Holder",
            "openapi/discriminator_plain_enum_tags_string.yaml::components.schemas.Holder",
            "openapi/discriminator_shared_written_tag/nested_variant_inherits_tag.yaml::components.schemas.Shelter",
            "openapi/discriminator_shared_written_tag/tag_named_like_enum.yaml::components.schemas.Holder",
            "openapi/discriminator_shared_written_tag/tag_named_like_enum_list_items.yaml::components.schemas.Holder",
            "openapi/discriminator_typed_mapping_keys.yaml::components.schemas.Holder",
        ),
        _UNCONSTRAINED_TAG_REASON + "here the variants take their tag through allOf, from an enum $ref or as no "
        "string, so a valid value of the optional union property is seldom drawn and the payloads drawn leave it out, "
        "which leaves the rejection oracle no mutation (Unsatisfiable)",
    ),
    **dict.fromkeys(
        (
            "openapi/discriminator_enum_tag_variants.yaml::components.schemas.LoneOptional",
            "openapi/discriminator_lone_variant_tags.yaml::components.schemas.LoneFloatTag",
            "openapi/discriminator_lone_variant_tags.yaml::components.schemas.LoneOptionalRootTag",
            "openapi/discriminator_lone_variant_tags.yaml::components.schemas.LoneStringTag",
        ),
        "the suite requires a variant's tag, as OpenAPI reads it, where the generated model keeps the optional tag of "
        "the schema or no discriminator for one variant, so it takes a payload without the tag; "
        "test_main_openapi_discriminator_lone_variant_tags pins those models",
    ),
    **dict.fromkeys(
        (
            *(
                f"{_DIAMOND}/{document}.yaml::components.schemas.{name}"
                for document, names in (
                    ("optional_tag_from_the_first_base", ("D", "Group", "Holder")),
                    ("optional_tag_from_the_first_base_in_a_list", ("D", "Group", "Shelter", "Thing")),
                    ("shallow_diamond", ("D", "Group")),
                )
                for name in names
            ),
            *(
                f"openapi/allof_parents_as_models/required_names.yaml::components.schemas.{name}"
                for name in ("NewPet", "NewPetFirst", "Owner")
            ),
        ),
        "a property that a later allOf parent requires stays optional in the model, whose class takes the parents in "
        "order (class D(B, C) keeps B's optional kind over C's required one), so it takes a payload without kind "
        "(#7331; the suite generates without --read-allof-parents-as-their-models, which writes the name again)",
    ),
}
"""The cases the Pydantic v2 rejection check leaves out, by reason; their acceptance and round trip checks run."""
PYDANTIC_V2_FULL_PAYLOAD_RUNTIME_MIN_VERSION = "2.5.0"
PYDANTIC_V2_0_RUNTIME_MAX_VERSION = "2.1.0"
PYDANTIC_V2_0_UNION_SERIALIZER_WARNING = r"Pydantic serializer warnings:\s+Expected `Union\["
"""The warning Pydantic before 2.1.0 gives for a union whose boolean Literal member holds the value."""
PYDANTIC_V2_MISSING_SENTINEL_RUNTIME_MIN_VERSION = "2.12.0"
PYDANTIC_V2_TYPE_ALIAS_RUNTIME_MIN_VERSION = "2.10.0"
PYDANTIC_V2_FLOAT_MULTIPLE_OF_RUNTIME_MIN_VERSION = "2.5.2"
PYDANTIC_V2_INEXACT_FLOAT_MULTIPLE_OF_RUNTIME_MIN_VERSION = "2.11.0"
PYDANTIC_V2_COMPILED_PATTERN_RUNTIME_MIN_VERSION = "2.11.0"
"""The oldest Pydantic whose payload target, the newest target it runs, writes compiled alias patterns (2.8)."""
PYDANTIC_V2_DECIMAL_PRECISION_RUNTIME_MIN_VERSION = "3.0.0"
"""No Pydantic 2 release validates these cases: the exclusion lasts until Pydantic 3."""
PYDANTIC_V2_FLOAT_MULTIPLE_OF_CASE_IDS = (
    "jsonschema/numeric_constraint_precision/ordinary.json",
    "jsonschema/native_decimal_default_constrained.json",
    "jsonschema/serialized_decimal_default_multiple_of.json",
    "jsonschema/msgspec_union_member_constraints.json",
    "openapi/variant_tag_values/scalar_kind_mapped.yaml::components.schemas.MultipleNumberA",
    "openapi/variant_tag_values/scalar_kind_mapped.yaml::components.schemas.MultipleNumberB",
)
PYDANTIC_V2_FLOAT_MULTIPLE_OF_EXCLUSION_REASON = (
    "Pydantic before 2.5.2 rejects schema-valid negative float multipleOf values"
)
PYDANTIC_V2_INEXACT_FLOAT_MULTIPLE_OF_CASE_IDS = (
    "jsonschema/msgspec_float_multiple_of.json",
    "jsonschema/use_decimal_for_multiple_of.json",
)
PYDANTIC_V2_INEXACT_FLOAT_MULTIPLE_OF_EXCLUSION_REASON = (
    "Pydantic before 2.11.0 rejects schema-valid negative float multipleOf values of steps that floats cannot "
    "represent exactly, such as 0.1"
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
PYDANTIC_V2_DATACLASS_BARE_LOOKAROUND_ALIAS_CASE_IDS = (
    *LITERAL_PATTERN_INTERSECTION_CASE_IDS,
    "jsonschema/lookaround_mixed_constraints.json",
    "jsonschema/root_alias_constraints/lookaround.json",
)
"""Lookaround aliases validated on their own: only a compiled pattern carries the engine without a config."""
PYDANTIC_V2_COMPILED_PATTERN_RUNTIME_EXCLUDED_CASES: dict[PayloadBackend, dict[str, str]] = {
    PayloadBackend.PYDANTIC_V2_DATACLASS: dict.fromkeys(
        PYDANTIC_V2_DATACLASS_BARE_LOOKAROUND_ALIAS_CASE_IDS,
        "Pydantic before 2.11.0 runs target 2, which predates compiled patterns (2.8) and keeps string lookaround "
        "patterns that a bare type alias has no model config to select regex_engine='python-re' for",
    ),
}
PYDANTIC_V2_LEGACY_RUNTIME_EXCLUDED_CASES: dict[PayloadBackend, dict[str, str]] = {
    PayloadBackend.PYDANTIC_V2: {
        "openapi/allof_with_required_inherited_coverage.yaml::components.schemas.MultipleOfBase": (
            "Pydantic before 2.5.0 can reject schema-valid inherited Decimal multipleOf values"
        ),
    },
}
"""Pydantic before 2.5 classifications that no generated construct explains.

Cases whose generation states that their models need a newer Pydantic, such as ``regex_engine`` (2.5), are skipped
from that statement instead of from a table (``runtime_below_stated_floor``).
"""
PYDANTIC_V2_FLOAT_MULTIPLE_OF_RUNTIME_EXCLUDED_CASES: dict[PayloadBackend, dict[str, str]] = {
    backend: dict.fromkeys(
        PYDANTIC_V2_FLOAT_MULTIPLE_OF_CASE_IDS,
        PYDANTIC_V2_FLOAT_MULTIPLE_OF_EXCLUSION_REASON,
    )
    for backend in (PayloadBackend.PYDANTIC_V2, PayloadBackend.PYDANTIC_V2_DATACLASS)
}
PYDANTIC_V2_INEXACT_FLOAT_MULTIPLE_OF_RUNTIME_EXCLUDED_CASES: dict[PayloadBackend, dict[str, str]] = {
    backend: dict.fromkeys(
        PYDANTIC_V2_INEXACT_FLOAT_MULTIPLE_OF_CASE_IDS,
        PYDANTIC_V2_INEXACT_FLOAT_MULTIPLE_OF_EXCLUSION_REASON,
    )
    for backend in (PayloadBackend.PYDANTIC_V2, PayloadBackend.PYDANTIC_V2_DATACLASS)
}
PYDANTIC_V2_DECIMAL_PRECISION_RUNTIME_EXCLUDED_CASES: dict[PayloadBackend, dict[str, str]] = {}
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
PYDANTIC_V2_FIRST_RELEASE_RUNTIME_EXCLUDED_CASES: tuple[tuple[str, dict[PayloadBackend, dict[str, str]]], ...] = (
    (
        "2.1.0",
        {
            PayloadBackend.PYDANTIC_V2: dict.fromkeys(
                (
                    "jsonschema/type_overrides_replace_whole_type.json",
                    "openapi/false_all_of_values.yaml::components.schemas.Holder",
                    "openapi/false_values.yaml::components.schemas.Holder",
                ),
                "Pydantic 2.0.x rejects a dict that holds exactly the maximum number of items "
                "('at most N items after validation, not N')",
            ),
            PayloadBackend.PYDANTIC_V2_DATACLASS: dict.fromkeys(
                (
                    "jsonschema/allof_map_member_counts_no_merge.json",
                    "jsonschema/type_overrides_replace_whole_type.json",
                ),
                "Pydantic 2.0.x rejects a dict that holds exactly the maximum number of items "
                "('at most N items after validation, not N')",
            ),
        },
    ),
    (
        "2.5.2",
        {
            PayloadBackend.PYDANTIC_V2_DATACLASS: {
                "jsonschema/decimal_strict_aliased_import.json": (
                    "Pydantic before 2.5.2 rejects float values that are multiples of a Decimal multipleOf of 0.01"
                ),
            },
        },
    ),
    (
        "2.8.0",
        {
            backend: {
                "jsonschema/decimal_multiple_of_field_forms.json": (
                    "Pydantic before 2.8.0 raises TypeError for a Decimal field with a float multipleOf "
                    "(Decimal % float)"
                ),
            }
            for backend in (PayloadBackend.PYDANTIC_V2, PayloadBackend.PYDANTIC_V2_DATACLASS)
        },
    ),
    (
        "2.11.0",
        {
            PayloadBackend.PYDANTIC_V2: {
                "jsonschema/decimal_strict_aliased_import.json": (
                    "Pydantic before 2.11.0 rejects float values that are multiples of a Decimal multipleOf of 0.01"
                ),
            },
            PayloadBackend.PYDANTIC_V2_DATACLASS: {
                "jsonschema/compound_property_names/allof_lookbehind.json": (
                    "Pydantic before 2.11.0 cannot build the dict validator of a lookbehind key pattern in a dataclass"
                ),
                **dict.fromkeys(
                    (
                        "jsonschema/discriminator_msgspec_wire_name.json",
                        "jsonschema/msgspec_tag_below_instance_field.json",
                    ),
                    "Pydantic before 2.11.0 does not support the alias of a hyphenated discriminator property in a "
                    "discriminated union of dataclasses",
                ),
            },
        },
    ),
    (
        "2.13.0",
        {
            PayloadBackend.PYDANTIC_V2: {
                "openapi/discriminator_lone_variant_tags.yaml::components.schemas.LoneRootTag": (
                    "Pydantic before 2.13.0 refuses the discriminator of a variant whose tag is a root model of a "
                    "Literal when the module is imported (discriminator-needs-literal, #7327)"
                ),
            },
        },
    ),
)
"""The first Pydantic release that validates the cases, by the release, for the payload and round trip checks."""
PYDANTIC_V2_FIRST_RELEASE_RUNTIME_ROUND_TRIP_EXCLUDED_CASES: tuple[
    tuple[str, dict[PayloadBackend, dict[str, str]]], ...
] = (
    (
        "2.3.0",
        {
            PayloadBackend.PYDANTIC_V2: {
                "jsonschema/property_names_root_model_key_literal_validation.json": (
                    "Pydantic before 2.3.0 emits JSON-mode serializer warnings for enum dictionary keys"
                ),
            },
        },
    ),
    (
        "2.6.0",
        {
            PayloadBackend.PYDANTIC_V2: {
                **dict.fromkeys(
                    (
                        "jsonschema/prefix_items_closed_tail_unevaluated.json",
                        "jsonschema/prefix_items_positions_unchecked.json",
                    ),
                    "Pydantic before 2.6.0 dumps a boolean item of a prefixItems tuple as 0",
                ),
            },
        },
    ),
    (
        "2.10.0",
        {
            PayloadBackend.PYDANTIC_V2: {
                "jsonschema/allof_base64_member_constraints.json": (
                    "Pydantic before 2.10.0 dumps a base64 value with a trailing newline, which the schema's "
                    "maxLength counts"
                ),
            },
        },
    ),
)
PYDANTIC_V2_LEGACY_RUNTIME_ROUND_TRIP_EXCLUDED_CASES: dict[PayloadBackend, dict[str, str]] = {
    PayloadBackend.PYDANTIC_V2: dict.fromkeys(
        (
            "jsonschema/compound_property_names/enum_oneof.json",
            "jsonschema/compound_property_names/enum_refs.json",
            "jsonschema/compound_property_names/extra_enum_disjoint.json",
            "jsonschema/open_enums_values.json",
            "jsonschema/property_names_anyof_ref.json",
            "jsonschema/property_names_ref_enum.json",
            "jsonschema/type_overrides_dict_key.json",
            "openapi/read_only_write_only_variant_graph.yaml::components.schemas.ForwardGraphWrapper",
            "openapi/read_only_write_only_variant_graph.yaml::components.schemas.ForwardKeyedMap",
        ),
        "Pydantic before 2.5.0 emits JSON-mode serializer warnings for enum dictionary keys",
    ),
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
PAYLOAD_TARGET_PYDANTIC_VERSION = max(
    (target.value for target in TargetPydanticVersion if installed_pydantic_runs_target(target.value)),
    key=lambda value: tuple(map(int, value.split("."))),
)
"""The newest target the installed Pydantic can run, passed only when it cannot run the output of an unset target."""
PAYLOAD_RUNS_UNSET_TARGET_PYDANTIC_VERSION = installed_pydantic_runs_target(None)
"""Whether the installed Pydantic runs the output of an unset target, so payload models keep the default output."""
