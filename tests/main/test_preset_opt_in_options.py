"""The dated presets equal the presets before them plus every opt-in option of their release.

The 20261027 presets add the opt-in options of 0.87.0 to the 20261017 presets, the 20261104 presets add those of
0.90.0 to the 20261027 presets, the 20261106 presets add those of 0.91.0 to the 20261104 presets, the 20261111
presets add those of the next release to the 20261106 presets, the 20261112 presets add the opt-in options after
them to the 20261111 presets, the 20261113 presets add the opt-in options after those to the 20261112 presets, the
20261118 presets add the opt-in options of the release after 0.97.0 to the 20261113 presets, the 20261120 presets
add the opt-in options after those to the 20261118 presets, the 20261123 presets add the opt-in options of the
release after 0.100.0 to the 20261120 presets, the 20261124 presets add the opt-in options of the release after
0.101.0 to the 20261123 presets, and the 20261125 presets add the opt-in options of the release after 0.102.0 to the
20261124 presets.
"""

from __future__ import annotations

import sys
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

import pytest
import tomllib

from datamodel_code_generator import chdir
from datamodel_code_generator.preset import get_preset_infos
from tests.main.conftest import (
    ASYNC_API_DATA_PATH,
    DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
    run_main_and_assert,
)

CASES_PATH = Path(__file__).parent / "preset_opt_in_cases"
"""The case files of the options the dates from ``DERIVED_FROM`` on add: ``<test id>.toml`` each."""

DERIVED_FROM = "20261123"
"""The first date whose options come from the rows that join it and whose cases come from case files."""

ORDER_CONSTRUCTOR_FIELDS_BY: tuple[str, ...] = ("--order-constructor-fields-by", "final-names", "written-defaults")
"""The constructor-order values the 20261027 presets set."""

OPT_IN_OPTIONS: tuple[str, ...] = (
    "--use-single-value-enum-as-const",
    "--openapi-as-definitions",
    "paths-bodies",
    "ref-components",
    "--use-site-models-for-constrained-refs",
    "--omit-redundant-constraints",
    "--read-const-beside-enum",
    "--filter-literal-values",
    "--intersect-allof-keywords",
    "--read-nullable-beside-type-list",
    "--keep-field-defaults-off-branch-roots",
    "--use-tuple-for-closed-arrays",
    "--use-single-dict-for-object-maps",
    "--check-pattern-maps-strictly",
    "--use-typed-map-values-beside-other-types",
    "--keep-enum-member-values",
    "--read-unevaluated-properties",
    "--pin-aliases-with-alias-generator",
    "--tag-msgspec-by-payload-name",
    *ORDER_CONSTRUCTOR_FIELDS_BY,
    "--dedupe-request-response-variants",
    "--omit-redundant-reference-null",
    "--use-decimal-in-field-forms",
    "--collapse-shared-parent-wrappers",
)
"""The options the 20261027 presets set beyond the 20261017 presets."""

OPT_IN_OPTIONS_20261104: tuple[str, ...] = (
    "--merge-shared-variant-tags",
    "--use-encoded-str-for-constrained-base64",
    "--read-length-keywords-by-type",
    "--read-msgspec-union-bounds-exactly",
    "--naming-strategy",
    "definitions-first",
    "--name-allof-union-members-after-branches",
    "--use-duplicate-name-suffix-for-every-collision",
    "--capitalise-numbered-enum-members",
    "--keep-first-character-of-path-keys",
    "--name-converted-payloads-by-message",
    "--singularize-last-word-of-item-names",
    "--read-schema-version-per-document",
    "--ignore-ref-siblings-by-schema-version",
    "--intersect-combined-schema-keywords",
    "--distribute-allof-over-unions",
    "--keep-reference-null-on-merged-models",
    "--use-msgspec-meta-for-map-keys",
    "--rename-submodules-hidden-by-classes",
    "--use-serialize-as-any-for-every-subclass",
    "--read-escaped-ref-pointers",
    "--read-dynamic-anchors-in-document-order",
    "--use-one-model-per-ref-site",
    "--use-unique-items-as-set-by-value",
    "--keep-constrained-root-models-in-every-container",
    "--keep-enum-literal-aliases",
    "--read-input-model-dataclass-fields-by-alias",
    "--use-plain-alias-for-recursive-models",
    "--type-overrides-replace-whole-type",
)
"""The options the 20261104 presets set beyond the 20261027 presets."""

OPT_IN_OPTIONS_20261106: tuple[str, ...] = (
    "--keep-false-dataclass-arguments",
    "--keep-content-parameter-refs",
    "--skip-path-extension-keys",
    "--reuse-every-reached-input-model-type",
    "--use-x-python-type-as-written",
    "--use-typed-nullable-map-values",
    "--use-msgspec-forbid-unknown-fields",
    "--keep-reference-null-on-collapsed-root-models",
    "--read-allof-parents-in-mro-order",
    "--apply-field-options-to-every-field",
    "--use-strict-str-for-base64",
    "--use-bytes-for-content-media-type",
    "--collapse-constrained-root-models-as-annotated",
    "--remove-unused-models-beside-type-overrides",
    "--keep-self-referencing-array-items",
    "--keep-shared-members-on-collapsed-root-models",
    "--use-enum-field-as-literal-in-operations",
    "--dedupe-numbered-request-response-variants",
    "--keep-defaults-of-inherited-required-fields",
    "--omit-incomplete-model-defaults",
    "--merge-message-trait-headers",
)
"""The options the 20261106 presets set beyond the 20261104 presets."""

OPT_IN_OPTIONS_20261111: tuple[str, ...] = ("--use-bounds-for-float-const",)
"""The options the 20261111 presets set beyond the 20261106 presets."""

OPT_IN_OPTIONS_20261112: tuple[str, ...] = ("--read-object-keywords-by-type",)
"""The options the 20261112 presets set beyond the 20261111 presets."""

OPT_IN_OPTIONS_20261113: tuple[str, ...] = (
    "--use-x-python-import-for-every-ref",
    "--open-enums",
    "extensible",
    "--infer-union-discriminators",
    "--read-kubernetes-extensions",
    "--read-inherited-property-schemas-in-mro-order",
    "--read-ids-with-empty-fragment",
    "--read-yaml-1-1-numbers-as-text",
    "--read-implied-options-from-merged-values",
    "--read-single-allof-refs-in-their-documents",
)
"""The options the 20261113 presets set beyond the 20261112 presets."""

OPT_IN_OPTIONS_20261118: tuple[str, ...] = (
    "--use-validate-default-for-root-model-type-aliases",
    "--read-ref-siblings-in-closed-allof-members",
    "--use-unmapped-variant-tag-values",
)
"""The options the 20261118 presets set beyond the 20261113 presets."""

OPT_IN_OPTIONS_20261120: tuple[str, ...] = ("--read-uuid6-uuid7-uuid8-formats",)
"""The options the 20261120 presets set beyond the 20261118 presets."""


def _added_options(previous: str, date: str) -> tuple[str, ...]:
    """Return the options of the groups the standard py312 presets of ``date`` list beyond those of ``previous``."""
    groups = {info.name.value: info.option_groups for info in get_preset_infos()}
    before = groups[f"standard-py312-{previous}"]
    return tuple(
        option for group in groups[f"standard-py312-{date}"] if group not in before for option in group.options
    )


def _argv(options: tuple[str, ...]) -> tuple[str, ...]:
    """Return the command-line words of options, a value option such as ``--open-enums extensible`` as two."""
    return tuple(word for option in options for word in option.split())


OPT_IN_OPTIONS_20261123: tuple[str, ...] = _argv(_added_options("20261120", "20261123"))
"""The options the 20261123 presets set beyond the 20261120 presets, from the rows that join them."""

OPT_IN_OPTIONS_20261124: tuple[str, ...] = _argv(_added_options("20261123", "20261124"))
"""The options the 20261124 presets set beyond the 20261123 presets, from the rows that join them."""

OPT_IN_OPTIONS_20261125: tuple[str, ...] = _argv(_added_options("20261124", "20261125"))
"""The options the 20261125 presets set beyond the 20261124 presets, from the rows that join them."""


class CaseFile(TypedDict):
    """A case file: the option, its input under tests/data and the arguments of the two tests that generate it.

    ``args`` go beside the preset that sets the option, ``off_args`` beside ``--output-model-type
    pydantic_v2.BaseModel`` where the option is turned off, a case without them has no turned-off test; ``off`` is the
    --no- form of an alias, ``warns_with`` and ``warns_without`` the warning the input gives with and without the
    option, ``filterwarnings`` the warnings left out.
    """

    option: str
    input: str
    input_file_type: str
    args: list[str]
    off_args: NotRequired[list[str]]
    off: NotRequired[str]
    warns_with: NotRequired[str]
    warns_without: NotRequired[str]
    filterwarnings: NotRequired[list[str]]


WarningKey = Literal["warns_with", "warns_without"]

CASE_FILES: dict[str, CaseFile] = {
    path.stem: tomllib.loads(path.read_text(encoding="utf-8")) for path in sorted(CASES_PATH.glob("*.toml"))
}
"""The case files by test id."""

RELEASED_WARNINGS_WITH_OPTION: dict[str, str] = {
    "uuid_version_formats.json": "Pydantic does not take max_length on UUID7",
}
"""The warning each input of a literal case gives in a generation with its options on, by file name."""

RELEASED_WARNINGS_WITHOUT_OPTION: dict[str, str] = {
    "uuid_version_formats.json": "format of 'uuid[678]' not understood",
}
"""The warning each input of a literal case gives without the option that a turned-off test names, by file name."""


def _case_warnings(released: dict[str, str], key: WarningKey) -> dict[str, str]:
    """Return the warnings of the literal cases and those the case files name under ``key``, by input file name."""
    return released | {Path(case["input"]).name: case[key] for case in CASE_FILES.values() if key in case}


WARNINGS_WITH_OPTION = _case_warnings(RELEASED_WARNINGS_WITH_OPTION, "warns_with")
"""The warning each input gives in a generation with its options on, by file name."""

WARNINGS_WITHOUT_OPTION = _case_warnings(RELEASED_WARNINGS_WITHOUT_OPTION, "warns_without")
"""The warning each input gives in a generation without the option that a turned-off test names, by file name."""


def _warns(warnings_by_input: dict[str, str], input_path: Path) -> AbstractContextManager[object]:
    """Return the context that expects the warning of an input, or one that expects none."""
    pattern = warnings_by_input.get(input_path.name)
    return nullcontext() if pattern is None else pytest.warns(UserWarning, match=pattern)


PRACTICAL_OPT_IN_OPTIONS: dict[str, tuple[str, ...]] = {
    "20261113": ("--untyped-value-type", "pydantic.JsonValue"),
    "20261118": ("--untyped-value-type", "pydantic.JsonValue"),
    "20261120": ("--untyped-value-type", "pydantic.JsonValue"),
    "20261123": ("--untyped-value-type", "pydantic.JsonValue"),
    "20261124": ("--untyped-value-type", "pydantic.JsonValue"),
    "20261125": ("--untyped-value-type", "pydantic.JsonValue"),
}
"""The options the practical presets of a date set beyond its standard options, for Pydantic output."""

PRESET_RELEASES: dict[str, tuple[str, tuple[str, ...]]] = {
    "20261027": ("20261017", OPT_IN_OPTIONS),
    "20261104": ("20261027", OPT_IN_OPTIONS_20261104),
    "20261106": ("20261104", OPT_IN_OPTIONS_20261106),
    "20261111": ("20261106", OPT_IN_OPTIONS_20261111),
    "20261112": ("20261111", OPT_IN_OPTIONS_20261112),
    "20261113": ("20261112", OPT_IN_OPTIONS_20261113),
    "20261118": ("20261113", OPT_IN_OPTIONS_20261118),
    "20261120": ("20261118", OPT_IN_OPTIONS_20261120),
    "20261123": ("20261120", OPT_IN_OPTIONS_20261123),
    "20261124": ("20261123", OPT_IN_OPTIONS_20261124),
    "20261125": ("20261124", OPT_IN_OPTIONS_20261125),
}
"""The preset before each dated preset and the options it adds to it."""

CASES_20261104: tuple[pytest.ParameterSet, ...] = (
    pytest.param(
        OPEN_API_DATA_PATH / "discriminator_merged_variant_tags.yaml",
        "openapi",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="merge-shared-variant-tags",
        marks=pytest.mark.filterwarnings(
            r"ignore:.* is a variant of discriminated unions that map 'kind' to different tags:UserWarning"
        ),
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "encoded_str_for_constrained_base64.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="use-encoded-str-for-constrained-base64",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "length_keywords_by_type.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="read-length-keywords-by-type",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "msgspec_number_limits" / "union_integer_bounds.json",
        "jsonschema",
        ["--output-model-type", "msgspec.Struct"],
        id="read-msgspec-union-bounds-exactly",
        marks=pytest.mark.filterwarnings(r"ignore:msgspec decodes every integer as the int member:UserWarning"),
    ),
    pytest.param(
        OPEN_API_DATA_PATH / "definitions_first.yaml",
        "openapi",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="naming-strategy-definitions-first",
    ),
    pytest.param(
        OPEN_API_DATA_PATH / "allof_union_members.yaml",
        "openapi",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="name-allof-union-members-after-branches",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "duplicate_name_suffix_every_collision.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel", "--duplicate-name-suffix", '{"model": "Schema"}'],
        id="use-duplicate-name-suffix-for-every-collision",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "capitalise_numbered_enum_members.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel", "--capitalise-enum-members"],
        id="capitalise-numbered-enum-members",
    ),
    pytest.param(
        OPEN_API_DATA_PATH / "paths_key_without_leading_slash.yaml",
        "openapi",
        ["--output-model-type", "pydantic_v2.BaseModel", "--openapi-scopes", "schemas", "paths", "parameters"],
        id="keep-first-character-of-path-keys",
    ),
    pytest.param(
        ASYNC_API_DATA_PATH / "converted_payload_names.yaml",
        "asyncapi",
        ["--output-model-type", "pydantic_v2.BaseModel", "--use-title-as-name"],
        id="name-converted-payloads-by-message",
    ),
    pytest.param(
        OPEN_API_DATA_PATH / "singularize_last_word_of_item_names.yaml",
        "openapi",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="singularize-last-word-of-item-names",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "schema_version_per_document",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="read-schema-version-per-document",
    ),
    pytest.param(
        OPEN_API_DATA_PATH / "ref_siblings_ignored_oas30.yaml",
        "openapi",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="ignore-ref-siblings-by-schema-version",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "schema_intersection" / "combined_branch_keywords.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="intersect-combined-schema-keywords",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "schema_intersection" / "allof_beside_union.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="distribute-allof-over-unions",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "keep_reference_null_on_merged_models.json",
        "jsonschema",
        ["--output-model-type", "typing.TypedDict"],
        id="keep-reference-null-on-merged-models",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "msgspec_map_keys.json",
        "jsonschema",
        ["--output-model-type", "msgspec.Struct"],
        id="use-msgspec-meta-for-map-keys",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "submodule_named_like_package_class_files",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="rename-submodules-hidden-by-classes",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "serialize_as_any_every_subclass.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel", "--use-serialize-as-any"],
        id="use-serialize-as-any-for-every-subclass",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "escaped_ref_pointers.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="read-escaped-ref-pointers",
    ),
    pytest.param(
        OPEN_API_DATA_PATH / "dynamic_ref_anchor_parse_order.yaml",
        "openapi",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="read-dynamic-anchors-in-document-order",
        marks=pytest.mark.filterwarnings(r"ignore:The anchor 'T' is declared by more than one schema:UserWarning"),
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "ref_site_chain.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="use-one-model-per-ref-site",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "unique_items_by_value.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel", "--use-unique-items-as-set"],
        id="use-unique-items-as-set-by-value",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "collapse_constrained_roots_in_containers.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel", "--collapse-root-models", "--field-constraints"],
        id="keep-constrained-root-models-in-every-container",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "collapse_enum_literal_aliases.json",
        "jsonschema",
        ["--output-model-type", "typing.TypedDict", "--collapse-root-models"],
        id="keep-enum-literal-aliases",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "recursive_model_alias_two_users.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.dataclass"],
        id="use-plain-alias-for-recursive-models",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "type_overrides_replace_whole_type.json",
        "jsonschema",
        [
            *("--output-model-type", "pydantic_v2.BaseModel", "--type-overrides"),
            (
                '{"Inventory.counts": "builtins.dict", "Inventory.sized": "builtins.dict", '
                '"Inventory.tags": "builtins.tuple"}'
            ),
        ],
        id="type-overrides-replace-whole-type",
    ),
)
"""The inputs each opt-in option of the 20261104 presets changes."""

CASES_20261106: tuple[pytest.ParameterSet, ...] = (
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "keep_false_dataclass_arguments.json",
        "jsonschema",
        ["--output-model-type", "dataclasses.dataclass", "--dataclass-arguments", '{"eq": false}'],
        id="keep-false-dataclass-arguments",
    ),
    pytest.param(
        OPEN_API_DATA_PATH / "content_parameter_refs.yaml",
        "openapi",
        [
            *("--output-model-type", "pydantic_v2.BaseModel", "--openapi-scopes", "schemas", "paths", "parameters"),
            *("--read-only-write-only-model-type", "request-response"),
        ],
        id="keep-content-parameter-refs",
    ),
    pytest.param(
        OPEN_API_DATA_PATH / "paths_extension_keys.yaml",
        "openapi",
        ["--output-model-type", "pydantic_v2.BaseModel", "--openapi-scopes", "schemas", "paths", "parameters"],
        id="skip-path-extension-keys",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "x_python_type_unions.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="use-x-python-type-as-written",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "typed_map_values_beside_other_types.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="use-typed-nullable-map-values",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "msgspec_forbid_unknown_fields.json",
        "jsonschema",
        ["--output-model-type", "msgspec.Struct"],
        id="use-msgspec-forbid-unknown-fields",
    ),
    pytest.param(
        OPEN_API_DATA_PATH / "collapse_nullable_roots.yaml",
        "openapi",
        ["--output-model-type", "msgspec.Struct", "--collapse-root-models"],
        id="keep-reference-null-on-collapsed-root-models",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "allof_inherited_property_nearer_base.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="read-allof-parents-in-mro-order",
    ),
    pytest.param(
        OPEN_API_DATA_PATH / "field_options_every_field.yaml",
        "openapi",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="apply-field-options-to-every-field",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "encoded_str_for_constrained_base64.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel", "--strict-types", "str"],
        id="use-strict-str-for-base64",
    ),
    pytest.param(
        OPEN_API_DATA_PATH / "content_media_type_bytes.yaml",
        "openapi",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="use-bytes-for-content-media-type",
    ),
    pytest.param(
        OPEN_API_DATA_PATH / "collapse_constrained_string_members.yaml",
        "openapi",
        ["--use-annotated", "--collapse-root-models"],
        id="collapse-constrained-root-models-as-annotated",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "collapsed_roots_beside_type_overrides.json",
        "jsonschema",
        ["--type-overrides", '{"Other": "collections.OrderedDict"}'],
        id="remove-unused-models-beside-type-overrides",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "self_referencing_array_items.json",
        "jsonschema",
        [],
        id="keep-self-referencing-array-items",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "collapse_shared_members.json",
        "jsonschema",
        ["--collapse-root-models"],
        id="keep-shared-members-on-collapsed-root-models",
    ),
    pytest.param(
        OPEN_API_DATA_PATH / "operation_enums_as_literal.yaml",
        "openapi",
        [
            *("--output-model-type", "pydantic_v2.BaseModel", "--openapi-scopes", "schemas", "paths"),
            *("--enum-field-as-literal", "all"),
        ],
        id="use-enum-field-as-literal-in-operations",
    ),
    pytest.param(
        OPEN_API_DATA_PATH / "read_only_numbered_variants.yaml",
        "openapi",
        [
            *("--output-model-type", "pydantic_v2.BaseModel"),
            *("--read-only-write-only-model-type", "request-response"),
        ],
        id="dedupe-numbered-request-response-variants",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "use_default_inherited_required.json",
        "jsonschema",
        ["--output-model-type", "dataclasses.dataclass", "--use-default"],
        id="keep-defaults-of-inherited-required-fields",
    ),
    pytest.param(
        OPEN_API_DATA_PATH / "incomplete_model_defaults.yaml",
        "openapi",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="omit-incomplete-model-defaults",
    ),
    pytest.param(
        ASYNC_API_DATA_PATH / "merged_trait_headers" / "v3.yaml",
        "asyncapi",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="merge-message-trait-headers",
    ),
)
"""The inputs each opt-in option of the 20261106 presets changes."""

CASES_20261111: tuple[pytest.ParameterSet, ...] = (
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "float_const_bounds.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="use-bounds-for-float-const",
    ),
)
"""The inputs each opt-in option of the 20261111 presets changes."""

CASES_20261112: tuple[pytest.ParameterSet, ...] = (
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "object_maps" / "string_branches_beside_object_keywords.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="read-object-keywords-by-type",
    ),
)
"""The inputs each opt-in option of the 20261112 presets changes."""

CASES_20261113: tuple[pytest.ParameterSet, ...] = (
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "python_import_all_of_keywords_pointer_targets" / "order.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="use-x-python-import-for-every-ref",
    ),
    pytest.param(
        OPEN_API_DATA_PATH / "open_enums.yaml",
        "openapi",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="open-enums",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "serde_internally_tagged.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="infer-union-discriminators",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "kubernetes_crd_schema.yaml",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="read-kubernetes-extensions",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "inherited_property_schemas" / "sibling.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="read-inherited-property-schemas-in-mro-order",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "ids_with_empty_fragment" / "ninjs_1_0.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="read-ids-with-empty-fragment",
    ),
    pytest.param(
        OPEN_API_DATA_PATH / "yaml_1_1_numbers" / "ratio.yaml",
        "openapi",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="read-yaml-1-1-numbers-as-text",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "single_allof_refs" / "main.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="read-single-allof-refs-in-their-documents",
    ),
)
"""The inputs each opt-in option of the 20261113 presets changes."""

CASES_20261118: tuple[pytest.ParameterSet, ...] = (
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "root_model_type_alias_defaults.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel", "--use-root-model-type-alias", "--no-collapse-root-models"],
        id="use-validate-default-for-root-model-type-aliases",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "allof_unevaluated_closed_member_ref_siblings.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="read-ref-siblings-in-closed-allof-members",
    ),
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "discriminator_mapped_variant_several_values.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="use-unmapped-variant-tag-values",
    ),
)
"""The inputs each opt-in option of the 20261118 presets changes."""

CASES_20261120: tuple[pytest.ParameterSet, ...] = (
    pytest.param(
        JSON_SCHEMA_DATA_PATH / "uuid_version_formats.json",
        "jsonschema",
        ["--output-model-type", "pydantic_v2.BaseModel"],
        id="read-uuid6-uuid7-uuid8-formats",
    ),
)
"""The inputs each opt-in option of the 20261120 presets changes."""

DERIVED_RELEASES: dict[str, tuple[str, ...]] = {
    date: _added_options(previous, date) for date, (previous, _) in PRESET_RELEASES.items() if date >= DERIVED_FROM
}
"""The options each date from ``DERIVED_FROM`` on adds, as its groups list them."""

DERIVED_OPTIONS = frozenset(option for options in DERIVED_RELEASES.values() for option in options)
"""The options the dates from ``DERIVED_FROM`` on add; a case file of another option is reported, not run."""


def _case_release(option: str) -> str:
    """Return the date that adds the option a case file names."""
    return next(date for date, options in DERIVED_RELEASES.items() if option in options)


def _case_marks(case: CaseFile) -> list[pytest.MarkDecorator]:
    """Return the marks of a case file: a ``filterwarnings`` mark for each of its patterns."""
    return [pytest.mark.filterwarnings(pattern) for pattern in case.get("filterwarnings", [])]


DERIVED_CASES: tuple[pytest.ParameterSet, ...] = tuple(
    pytest.param(
        _case_release(case["option"]),
        DATA_PATH / case["input"],
        case["input_file_type"],
        case["args"],
        id=test_id,
        marks=_case_marks(case),
    )
    for test_id, case in CASE_FILES.items()
    if case["option"] in DERIVED_OPTIONS
)
"""The inputs each opt-in option of the dates from ``DERIVED_FROM`` on changes, from the case files."""

TURNED_OFF_CASES: tuple[pytest.ParameterSet, ...] = tuple(
    pytest.param(
        DATA_PATH / case["input"],
        case["input_file_type"],
        off_args,
        case["option"],
        case.get("off", f"--no-{case['option'].split()[0].removeprefix('--')}"),
        id=test_id,
        marks=_case_marks(case),
    )
    for test_id, case in CASE_FILES.items()
    if case["option"] in DERIVED_OPTIONS and (off_args := case.get("off_args")) is not None
)
"""The options of the dates from ``DERIVED_FROM`` on turned off, from the case files."""


def _case_file_problems() -> list[str]:
    """Return what keeps the case files from matching the options the dates from ``DERIVED_FROM`` on add."""
    named = {case["option"] for case in CASE_FILES.values()}
    return [
        *(f"{option}: add {CASES_PATH.name}/<test id>.toml for it" for option in sorted(DERIVED_OPTIONS - named)),
        *(
            f"{CASES_PATH.name}/{test_id}.toml: {case['option']} joins no date from {DERIVED_FROM} on; delete the file"
            for test_id, case in CASE_FILES.items()
            if case["option"] not in DERIVED_OPTIONS
        ),
        *(
            f"{CASES_PATH.name}/{test_id}.toml: {key} differs from the pattern a literal case names for its input"
            for test_id, case in CASE_FILES.items()
            for key, released in (
                ("warns_with", RELEASED_WARNINGS_WITH_OPTION),
                ("warns_without", RELEASED_WARNINGS_WITHOUT_OPTION),
            )
            if released.get(Path(case["input"]).name, case.get(key)) != case.get(key)
        ),
    ]


@pytest.mark.allow_direct_assert("states that the case files match the option rows; no generation output shows it")
def test_case_files_match_the_joined_options() -> None:
    """Every option a date from DERIVED_FROM on adds has one case file, and every case file names such an option."""
    assert _case_file_problems() == []


@pytest.mark.parametrize("family", ["standard", "practical"])
@pytest.mark.parametrize(
    ("release", "input_path", "input_file_type", "extra_args"),
    [
        *DERIVED_CASES,
        *(pytest.param("20261120", *case.values, id=case.id, marks=case.marks) for case in CASES_20261120),
        *(pytest.param("20261118", *case.values, id=case.id, marks=case.marks) for case in CASES_20261118),
        *(pytest.param("20261113", *case.values, id=case.id, marks=case.marks) for case in CASES_20261113),
        *(pytest.param("20261112", *case.values, id=case.id, marks=case.marks) for case in CASES_20261112),
        *(pytest.param("20261111", *case.values, id=case.id, marks=case.marks) for case in CASES_20261111),
        *(pytest.param("20261106", *case.values, id=case.id, marks=case.marks) for case in CASES_20261106),
        *(pytest.param("20261104", *case.values, id=case.id, marks=case.marks) for case in CASES_20261104),
        pytest.param(
            "20261027",
            JSON_SCHEMA_DATA_PATH / "enum_const_positions.json",
            "jsonschema",
            ["--output-model-type", "pydantic_v2.BaseModel"],
            id="read-const-beside-enum",
        ),
        pytest.param(
            "20261027",
            OPEN_API_DATA_PATH / "max_items_enum.yaml",
            "openapi",
            [],
            id="filter-literal-values",
        ),
        pytest.param(
            "20261027",
            JSON_SCHEMA_DATA_PATH / "schema_intersection" / "inherited_overrides.json",
            "jsonschema",
            ["--output-model-type", "pydantic_v2.BaseModel", "--field-constraints"],
            id="intersect-allof-keywords",
        ),
        pytest.param(
            "20261027",
            OPEN_API_DATA_PATH / "schema_intersection" / "null_multiple_types_oas30.yaml",
            "openapi",
            ["--output-model-type", "pydantic_v2.BaseModel", "--use-missing-sentinel"],
            id="read-nullable-beside-type-list",
        ),
        pytest.param(
            "20261027",
            OPEN_API_DATA_PATH / "extracted_branch_default.yaml",
            "openapi",
            ["--output-model-type", "pydantic_v2.BaseModel", "--field-constraints"],
            id="keep-field-defaults-off-branch-roots",
        ),
        pytest.param(
            "20261027",
            JSON_SCHEMA_DATA_PATH / "prefix_items_closed_length.json",
            "jsonschema",
            ["--output-model-type", "pydantic_v2.BaseModel"],
            id="use-tuple-for-closed-arrays",
        ),
        pytest.param(
            "20261027",
            JSON_SCHEMA_DATA_PATH / "pattern_properties_merge.json",
            "jsonschema",
            ["--output-model-type", "pydantic_v2.BaseModel"],
            id="use-single-dict-for-object-maps",
        ),
        pytest.param(
            "20261027",
            JSON_SCHEMA_DATA_PATH / "object_maps" / "strict_pattern_maps.json",
            "jsonschema",
            ["--output-model-type", "pydantic_v2.BaseModel", "--generate-schema-validators"],
            id="check-pattern-maps-strictly",
        ),
        pytest.param(
            "20261027",
            JSON_SCHEMA_DATA_PATH / "typed_map_values_beside_other_types.json",
            "jsonschema",
            ["--output-model-type", "pydantic_v2.BaseModel", "--use-typed-nullable-map-values"],
            id="use-typed-map-values-beside-other-types",
        ),
        pytest.param(
            "20261027",
            JSON_SCHEMA_DATA_PATH / "enum_value_base_kinds.json",
            "jsonschema",
            ["--output-model-type", "pydantic_v2.BaseModel"],
            id="keep-enum-member-values",
        ),
        pytest.param(
            "20261027",
            JSON_SCHEMA_DATA_PATH / "unevaluated_properties_schema.json",
            "jsonschema",
            [],
            id="read-unevaluated-properties",
        ),
        pytest.param(
            "20261027",
            JSON_SCHEMA_DATA_PATH / "alias_generator.json",
            "jsonschema",
            [
                "--output-model-type",
                "pydantic_v2.dataclass",
                "--snake-case-field",
                "--extra-template-data",
                str(JSON_SCHEMA_DATA_PATH / "extra_data_alias_generator.json"),
                "--target-pydantic-version",
                "2",
            ],
            id="pin-aliases-with-alias-generator",
        ),
        pytest.param(
            "20261027",
            JSON_SCHEMA_DATA_PATH / "discriminator_msgspec_wire_name.json",
            "jsonschema",
            ["--output-model-type", "msgspec.Struct"],
            id="tag-msgspec-by-payload-name",
        ),
        pytest.param(
            "20261027",
            JSON_SCHEMA_DATA_PATH / "field_type_collision_inherited_modules",
            "jsonschema",
            ["--output-model-type", "pydantic_v2.dataclass"],
            id="order-constructor-fields-by-final-names",
        ),
        pytest.param(
            "20261027",
            OPEN_API_DATA_PATH / "strip_default_none_constructor_order.yaml",
            "openapi",
            ["--output-model-type", "msgspec.Struct", "--strip-default-none"],
            id="order-constructor-fields-by-written-defaults",
        ),
        pytest.param(
            "20261027",
            OPEN_API_DATA_PATH / "read_only_write_only_allof_parent_inline_models.yaml",
            "openapi",
            [
                "--output-model-type",
                "pydantic_v2.BaseModel",
                "--read-only-write-only-model-type",
                "request-response",
            ],
            id="dedupe-request-response-variants",
        ),
        pytest.param(
            "20261027",
            OPEN_API_DATA_PATH / "ref_siblings_recursive_target" / "schema.yaml",
            "openapi",
            ["--output-model-type", "pydantic_v2.BaseModel"],
            id="openapi-ref-components-as-definitions",
        ),
        pytest.param(
            "20261027",
            OPEN_API_DATA_PATH / "redundant_reference_null.yaml",
            "openapi",
            ["--output-model-type", "pydantic_v2.BaseModel", "--strict-nullable"],
            id="omit-redundant-reference-null",
        ),
        pytest.param(
            "20261027",
            JSON_SCHEMA_DATA_PATH / "decimal_multiple_of_field_forms.json",
            "jsonschema",
            [
                "--output-model-type",
                "pydantic_v2.BaseModel",
                "--use-decimal-for-multiple-of",
                "--strict-types",
                "float",
            ],
            id="use-decimal-in-field-forms",
        ),
        pytest.param(
            "20261027",
            OPEN_API_DATA_PATH / "collapse_root_models_name_strategy" / "later_holder_renames_wrapper.yaml",
            "openapi",
            [
                "--output-model-type",
                "pydantic_v2.BaseModel",
                "--collapse-root-models-name-strategy",
                "parent",
                "--field-type-collision-strategy",
                "rename-type",
                "--no-snake-case-field",
            ],
            id="collapse-shared-parent-wrappers",
        ),
    ],
)
def test_preset_sets_opt_in_option(
    release: str,
    input_path: Path,
    input_file_type: str,
    extra_args: list[str],
    family: str,
    tmp_path: Path,
) -> None:
    """Generate with a dated preset and with the preset before it plus the options it adds: the output is the same.

    The py312 presets may write Python 3.12 syntax, which only Python 3.12 and later compile.
    """
    previous, options = PRESET_RELEASES[release]
    preset_output = tmp_path / "preset"
    options_output = tmp_path / "options"
    if input_path.is_dir():
        preset_output.mkdir()
        options_output.mkdir()
    else:
        preset_output = preset_output.with_suffix(".py")
        options_output = options_output.with_suffix(".py")
    common = ["--disable-timestamp", *extra_args]
    with _warns(WARNINGS_WITH_OPTION, input_path):
        run_main_and_assert(
            input_path=input_path,
            output_path=preset_output,
            input_file_type=input_file_type,
            extra_args=["--preset", f"{family}-py312-{release}", *common],
            skip_code_validation=sys.version_info < (3, 12),
            name="tree" if input_path.is_dir() else "model",
        )
    with _warns(WARNINGS_WITH_OPTION, input_path):
        run_main_and_assert(
            input_path=input_path,
            output_path=options_output,
            input_file_type=input_file_type,
            extra_args=[
                "--preset",
                f"{family}-py312-{previous}",
                *common,
                *options,
                *(PRACTICAL_OPT_IN_OPTIONS.get(release, ()) if family == "practical" else ()),
            ],
            skip_code_validation=sys.version_info < (3, 12),
            name="tree" if input_path.is_dir() else "model",
        )


@pytest.mark.parametrize("source", ["command-line", "pyproject"])
@pytest.mark.parametrize(
    ("input_path", "input_file_type", "extra_args", "option", "off"),
    [
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "enum_const_positions.json",
            "jsonschema",
            [],
            "--read-const-beside-enum",
            "--no-read-const-beside-enum",
            id="read-const-beside-enum",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "length_keywords_by_type.json",
            "jsonschema",
            [],
            "--read-length-keywords-by-type",
            "--no-read-length-keywords-by-type",
            id="read-length-keywords-by-type",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "capitalise_numbered_enum_members.json",
            "jsonschema",
            ["--capitalise-enum-members"],
            "--capitalise-numbered-enum-members",
            "--no-capitalize-numbered-enum-members",
            id="capitalize-numbered-enum-members-alias",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "typed_map_values_beside_other_types.json",
            "jsonschema",
            [],
            "--use-typed-nullable-map-values",
            "--no-use-typed-nullable-map-values",
            id="use-typed-nullable-map-values",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "float_const_bounds.json",
            "jsonschema",
            [],
            "--use-bounds-for-float-const",
            "--no-use-bounds-for-float-const",
            id="use-bounds-for-float-const",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "object_maps" / "string_branches_beside_object_keywords.json",
            "jsonschema",
            [],
            "--read-object-keywords-by-type",
            "--no-read-object-keywords-by-type",
            id="read-object-keywords-by-type",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "python_import_all_of_keywords_pointer_targets" / "order.json",
            "jsonschema",
            [],
            "--use-x-python-import-for-every-ref",
            "--no-use-x-python-import-for-every-ref",
            id="use-x-python-import-for-every-ref",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "serde_internally_tagged.json",
            "jsonschema",
            [],
            "--infer-union-discriminators",
            "--no-infer-union-discriminators",
            id="infer-union-discriminators",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "kubernetes_crd_schema.yaml",
            "jsonschema",
            [],
            "--read-kubernetes-extensions",
            "--no-read-kubernetes-extensions",
            id="read-kubernetes-extensions",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "inherited_property_schemas" / "sibling.json",
            "jsonschema",
            [],
            "--read-inherited-property-schemas-in-mro-order",
            "--no-read-inherited-property-schemas-in-mro-order",
            id="read-inherited-property-schemas-in-mro-order",
        ),
        pytest.param(
            OPEN_API_DATA_PATH / "yaml_1_1_numbers" / "ratio.yaml",
            "jsonschema",
            [],
            "--read-yaml-1-1-numbers-as-text",
            "--no-read-yaml-1-1-numbers-as-text",
            id="read-yaml-1-1-numbers-as-text",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "single_allof_refs" / "main.json",
            "jsonschema",
            [],
            "--read-single-allof-refs-in-their-documents",
            "--no-read-single-allof-refs-in-their-documents",
            id="read-single-allof-refs-in-their-documents",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "root_model_type_alias_defaults.json",
            "jsonschema",
            ["--use-root-model-type-alias", "--no-collapse-root-models"],
            "--use-validate-default-for-root-model-type-aliases",
            "--no-use-validate-default-for-root-model-type-aliases",
            id="use-validate-default-for-root-model-type-aliases",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "allof_unevaluated_closed_member_ref_siblings.json",
            "jsonschema",
            [],
            "--read-ref-siblings-in-closed-allof-members",
            "--no-read-ref-siblings-in-closed-allof-members",
            id="read-ref-siblings-in-closed-allof-members",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "discriminator_mapped_variant_several_values.json",
            "jsonschema",
            [],
            "--use-unmapped-variant-tag-values",
            "--no-use-unmapped-variant-tag-values",
            id="use-unmapped-variant-tag-values",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "uuid_version_formats.json",
            "jsonschema",
            [],
            "--read-uuid6-uuid7-uuid8-formats",
            "--no-read-uuid6-uuid7-uuid8-formats",
            id="read-uuid6-uuid7-uuid8-formats",
        ),
        *TURNED_OFF_CASES,
    ],
)
def test_preset_option_turned_off(
    input_path: Path,
    input_file_type: str,
    extra_args: list[str],
    option: str,
    off: str,
    source: str,
    tmp_path: Path,
) -> None:
    """Turn off one option of the newest standard py312 preset: the output is that of the preset without the option.

    The --no- form on the command line, or false for the option in the pyproject.toml that selects the preset, gives
    the output of the 20261017 preset plus every option the dated presets after it add but that one; an alias of the
    option has a --no- form and a key too.
    """
    project = tmp_path / "project"
    project.mkdir()
    preset = f"standard-py312-{max(PRESET_RELEASES)}"
    preset_args = ["--preset", preset, off]
    if source == "pyproject":
        (project / "pyproject.toml").write_text(
            f'[tool.datamodel-codegen]\npreset = "{preset}"\n{off.removeprefix("--no-")} = false\n', encoding="utf-8"
        )
        preset_args = []
    common = ["--output-model-type", "pydantic_v2.BaseModel", "--disable-timestamp", *extra_args]
    off_output = tmp_path / "off.py"
    with chdir(project), _warns(WARNINGS_WITHOUT_OPTION, input_path):
        run_main_and_assert(
            input_path=input_path,
            output_path=off_output,
            input_file_type=input_file_type,
            extra_args=[*preset_args, *common],
            skip_code_validation=sys.version_info < (3, 12),
            name="model",
        )
    options = [
        *(
            word
            for date, (_, words) in PRESET_RELEASES.items()
            if date < DERIVED_FROM
            for word in words
            if word != option
        ),
        *_argv(tuple(item for item in sorted(DERIVED_OPTIONS) if item != option)),
    ]
    with _warns(WARNINGS_WITHOUT_OPTION, input_path):
        run_main_and_assert(
            input_path=input_path,
            output_path=tmp_path / "options.py",
            input_file_type=input_file_type,
            extra_args=["--preset", "standard-py312-20261017", *common, *options],
            skip_code_validation=sys.version_info < (3, 12),
            name="model",
        )


@pytest.mark.parametrize(
    ("pyproject", "pyproject_args", "options_args"),
    [
        pytest.param(
            'order-constructor-fields-by = ["written-defaults"]',
            ["--target-python-version", "3.10", "--formatters", "builtin"],
            [
                "--target-python-version",
                "3.10",
                "--formatters",
                "builtin",
                "--order-constructor-fields-by",
                "written-defaults",
            ],
            id="toml-list",
        ),
        pytest.param(
            'preset = "standard-py312-20261027"\norder-constructor-fields-by = []',
            ["--disable-timestamp"],
            [
                "--preset",
                "standard-py312-20261017",
                "--disable-timestamp",
                *(option for option in OPT_IN_OPTIONS if option not in ORDER_CONSTRUCTOR_FIELDS_BY),
            ],
            id="empty-list-under-preset",
        ),
    ],
)
def test_option_list_in_pyproject(
    pyproject: str, pyproject_args: list[str], options_args: list[str], tmp_path: Path
) -> None:
    """A pyproject.toml list gives a list option its values, and an empty list turns off the values a preset sets."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(f"[tool.datamodel-codegen]\n{pyproject}\n", encoding="utf-8")
    input_path = OPEN_API_DATA_PATH / "strip_default_none_constructor_order.yaml"
    common = ["--output-model-type", "msgspec.Struct", "--strip-default-none"]
    pyproject_output = tmp_path / "pyproject.py"
    with chdir(project):
        run_main_and_assert(
            input_path=input_path,
            output_path=pyproject_output,
            input_file_type="openapi",
            extra_args=[*common, *pyproject_args],
            name="model",
        )
    run_main_and_assert(
        input_path=input_path,
        output_path=tmp_path / "options.py",
        input_file_type="openapi",
        extra_args=[*common, *options_args],
        name="model",
    )
