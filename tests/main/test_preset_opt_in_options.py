"""The 20261027 presets equal the 20261017 presets plus every opt-in option of the release."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import chdir
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, OPEN_API_DATA_PATH, run_main_and_assert

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


@pytest.mark.parametrize("family", ["standard", "practical"])
@pytest.mark.parametrize(
    ("input_path", "input_file_type", "extra_args"),
    [
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "enum_const_positions.json",
            "jsonschema",
            ["--output-model-type", "pydantic_v2.BaseModel"],
            id="read-const-beside-enum",
        ),
        pytest.param(
            OPEN_API_DATA_PATH / "max_items_enum.yaml",
            "openapi",
            [],
            id="filter-literal-values",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "schema_intersection" / "inherited_overrides.json",
            "jsonschema",
            ["--output-model-type", "pydantic_v2.BaseModel", "--field-constraints"],
            id="intersect-allof-keywords",
        ),
        pytest.param(
            OPEN_API_DATA_PATH / "schema_intersection" / "null_multiple_types_oas30.yaml",
            "openapi",
            ["--output-model-type", "pydantic_v2.BaseModel", "--use-missing-sentinel"],
            id="read-nullable-beside-type-list",
        ),
        pytest.param(
            OPEN_API_DATA_PATH / "extracted_branch_default.yaml",
            "openapi",
            ["--output-model-type", "pydantic_v2.BaseModel", "--field-constraints"],
            id="keep-field-defaults-off-branch-roots",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "prefix_items_closed_length.json",
            "jsonschema",
            ["--output-model-type", "pydantic_v2.BaseModel"],
            id="use-tuple-for-closed-arrays",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "pattern_properties_merge.json",
            "jsonschema",
            ["--output-model-type", "pydantic_v2.BaseModel"],
            id="use-single-dict-for-object-maps",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "object_maps" / "strict_pattern_maps.json",
            "jsonschema",
            ["--output-model-type", "pydantic_v2.BaseModel", "--generate-schema-validators"],
            id="check-pattern-maps-strictly",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "typed_map_values_beside_other_types.json",
            "jsonschema",
            ["--output-model-type", "pydantic_v2.BaseModel", "--use-typed-nullable-map-values"],
            id="use-typed-map-values-beside-other-types",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "enum_value_base_kinds.json",
            "jsonschema",
            ["--output-model-type", "pydantic_v2.BaseModel"],
            id="keep-enum-member-values",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "unevaluated_properties_schema.json",
            "jsonschema",
            [],
            id="read-unevaluated-properties",
        ),
        pytest.param(
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
            JSON_SCHEMA_DATA_PATH / "discriminator_msgspec_wire_name.json",
            "jsonschema",
            ["--output-model-type", "msgspec.Struct"],
            id="tag-msgspec-by-payload-name",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "field_type_collision_inherited_modules",
            "jsonschema",
            ["--output-model-type", "pydantic_v2.dataclass"],
            id="order-constructor-fields-by-final-names",
        ),
        pytest.param(
            OPEN_API_DATA_PATH / "strip_default_none_constructor_order.yaml",
            "openapi",
            ["--output-model-type", "msgspec.Struct", "--strip-default-none"],
            id="order-constructor-fields-by-written-defaults",
        ),
        pytest.param(
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
            OPEN_API_DATA_PATH / "ref_siblings_recursive_target" / "schema.yaml",
            "openapi",
            ["--output-model-type", "pydantic_v2.BaseModel"],
            id="openapi-ref-components-as-definitions",
        ),
        pytest.param(
            OPEN_API_DATA_PATH / "redundant_reference_null.yaml",
            "openapi",
            ["--output-model-type", "pydantic_v2.BaseModel", "--strict-nullable"],
            id="omit-redundant-reference-null",
        ),
        pytest.param(
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
    input_path: Path,
    input_file_type: str,
    extra_args: list[str],
    family: str,
    tmp_path: Path,
) -> None:
    """Generate with the 20261027 preset and with the 20261017 preset plus the options: the output is the same."""
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
        extra_args=["--preset", f"{family}-py312-20261027", *common],
    )
    run_main_and_assert(
        input_path=input_path,
        output_path=options_output,
        input_file_type=input_file_type,
        extra_args=["--preset", f"{family}-py312-20261017", *common, *OPT_IN_OPTIONS],
        **(
            {"expected_directory": preset_output}
            if input_path.is_dir()
            else {"expected_output": preset_output.read_text(encoding="utf-8")}
        ),
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
