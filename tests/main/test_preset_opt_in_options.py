"""The dated presets equal the presets before them plus every opt-in option of their release.

The 20261027 presets add the opt-in options of 0.87.0 to the 20261017 presets, and the 20261104 presets add those of
the next release to the 20261027 presets.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import chdir
from tests.main.conftest import ASYNC_API_DATA_PATH, JSON_SCHEMA_DATA_PATH, OPEN_API_DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

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
)
"""The options the 20261104 presets set beyond the 20261027 presets."""

PRESET_RELEASES: dict[str, tuple[str, tuple[str, ...]]] = {
    "20261027": ("20261017", OPT_IN_OPTIONS),
    "20261104": ("20261027", OPT_IN_OPTIONS_20261104),
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
)
"""The inputs each opt-in option of the 20261104 presets changes."""


@pytest.mark.parametrize("family", ["standard", "practical"])
@pytest.mark.parametrize(
    ("release", "input_path", "input_file_type", "extra_args"),
    [
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
    run_main_and_assert(
        input_path=input_path,
        output_path=preset_output,
        input_file_type=input_file_type,
        extra_args=["--preset", f"{family}-py312-{release}", *common],
        skip_code_validation=sys.version_info < (3, 12),
    )
    run_main_and_assert(
        input_path=input_path,
        output_path=options_output,
        input_file_type=input_file_type,
        extra_args=["--preset", f"{family}-py312-{previous}", *common, *options],
        skip_code_validation=sys.version_info < (3, 12),
        **(
            {"expected_directory": preset_output}
            if input_path.is_dir()
            else {"expected_output": preset_output.read_text(encoding="utf-8")}
        ),
    )


@pytest.mark.parametrize("source", ["command-line", "pyproject"])
@pytest.mark.parametrize(
    ("input_path", "extra_args", "option", "off"),
    [
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "enum_const_positions.json",
            [],
            "--read-const-beside-enum",
            "--no-read-const-beside-enum",
            id="read-const-beside-enum",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "length_keywords_by_type.json",
            [],
            "--read-length-keywords-by-type",
            "--no-read-length-keywords-by-type",
            id="read-length-keywords-by-type",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "capitalise_numbered_enum_members.json",
            ["--capitalise-enum-members"],
            "--capitalise-numbered-enum-members",
            "--no-capitalize-numbered-enum-members",
            id="capitalize-numbered-enum-members-alias",
        ),
    ],
)
def test_preset_option_turned_off(
    input_path: Path, extra_args: list[str], option: str, off: str, source: str, tmp_path: Path
) -> None:
    """Turn off one option of a 20261104 preset: the output is that of the preset without the option.

    The --no- form on the command line, or false for the option in the pyproject.toml that selects the preset, gives
    the output of the 20261017 preset plus every option the 20261027 and 20261104 presets add but that one; an alias
    of the option has a --no- form and a key too.
    """
    project = tmp_path / "project"
    project.mkdir()
    preset = "standard-py312-20261104"
    preset_args = ["--preset", preset, off]
    if source == "pyproject":
        (project / "pyproject.toml").write_text(
            f'[tool.datamodel-codegen]\npreset = "{preset}"\n{off.removeprefix("--no-")} = false\n', encoding="utf-8"
        )
        preset_args = []
    common = ["--output-model-type", "pydantic_v2.BaseModel", "--disable-timestamp", *extra_args]
    off_output = tmp_path / "off.py"
    with chdir(project):
        run_main_and_assert(
            input_path=input_path,
            output_path=off_output,
            input_file_type="jsonschema",
            extra_args=[*preset_args, *common],
            skip_code_validation=sys.version_info < (3, 12),
        )
    options = [item for item in (*OPT_IN_OPTIONS, *OPT_IN_OPTIONS_20261104) if item != option]
    run_main_and_assert(
        input_path=input_path,
        output_path=tmp_path / "options.py",
        input_file_type="jsonschema",
        extra_args=["--preset", "standard-py312-20261017", *common, *options],
        skip_code_validation=sys.version_info < (3, 12),
        expected_output=off_output.read_text(encoding="utf-8"),
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
        )
    run_main_and_assert(
        input_path=input_path,
        output_path=tmp_path / "options.py",
        input_file_type="openapi",
        extra_args=[*common, *options_args],
        expected_output=pyproject_output.read_text(encoding="utf-8"),
    )
