"""Tests for GraphQL schema code generation."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

import black
import pytest

from tests.main.conftest import (
    DEFAULT_VALUES_DATA_PATH,
    EXPECTED_GRAPHQL_PATH,
    GRAPHQL_DATA_PATH,
    LEGACY_BLACK_SKIP,
    assert_generated_model_json_validation,
    run_main_and_assert,
    target_pydantic_args,
)
from tests.main.graphql.conftest import assert_file_content

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("output_model", "expected_output"),
    [
        (
            "pydantic_v2.BaseModel",
            "simple_star_wars.py",
        ),
        (
            "dataclasses.dataclass",
            "simple_star_wars_dataclass.py",
        ),
    ],
)
@pytest.mark.cli_doc(
    options=["--output-model-type"],
    option_description="""Generate models from GraphQL with different output model types.

This example demonstrates using `--output-model-type` with GraphQL schemas
to generate either Pydantic models or dataclasses.""",
    input_schema="graphql/simple-star-wars.graphql",
    cli_args=["--output-model-type", "pydantic_v2.BaseModel"],
    model_outputs={
        "pydantic_v2": "graphql/simple_star_wars.py",
        "dataclass": "graphql/simple_star_wars_dataclass.py",
    },
)
@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_graphql_simple_star_wars(output_model: str, expected_output: str, output_file: Path) -> None:
    """Generate models from GraphQL with different output model types.

    This example demonstrates using `--output-model-type` with GraphQL schemas
    to generate either Pydantic models or dataclasses.
    """
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "simple-star-wars.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file=expected_output,
        extra_args=["--output-model-type", output_model],
    )


@LEGACY_BLACK_SKIP
@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_graphql_different_types_of_fields(output_file: Path) -> None:
    """Test GraphQL code generation with different field types."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "different-types-of-fields.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="different_types_of_fields.py",
    )


def test_main_graphql_root_type_references(output_file: Path) -> None:
    """Resolve operation roots to Any while retaining non-root types named Query."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "root_type_references.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="root_type_references.py",
        extra_args=["--disable-timestamp"],
        force_exec_validation=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="generated_graphql_root_type_references",
        model_name="Payload",
        valid_json='{"queryRoot":{"anything":1},"ordinaryQuery":{"label":"ok"}}',
        invalid_json='{"ordinaryQuery":{"label":1}}',
        expected_error_type="string_type",
    )


@pytest.mark.cli_doc(
    options=["--use-default-kwarg"],
    option_description="""Use default= keyword argument instead of positional argument for fields with defaults.

The `--use-default-kwarg` flag generates Field() declarations using `default=`
as a keyword argument instead of a positional argument for fields that have
default values.""",
    input_schema="graphql/annotated.graphql",
    cli_args=["--use-default-kwarg"],
    golden_output="graphql/annotated_use_default_kwarg.py",
)
def test_main_use_default_kwarg(output_file: Path) -> None:
    """Use default= keyword argument instead of positional argument for fields with defaults.

    The `--use-default-kwarg` flag generates Field() declarations using `default=`
    as a keyword argument instead of a positional argument for fields that have
    default values.
    """
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "annotated.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="annotated_use_default_kwarg.py",
        extra_args=["--use-default-kwarg"],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] in {"19", "22"},
    reason="Installed black doesn't support Python 3.12 target version",
)
def test_main_graphql_empty_list_default(output_file: Path) -> None:
    """Test GraphQL generation with empty list default values."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "empty_list_default.graphql",
        output_path=output_file,
        assert_func=assert_file_content,
        expected_file=EXPECTED_GRAPHQL_PATH / "pydantic_v2_empty_list_default.py",
        input_file_type="graphql",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--target-python-version",
            "3.12",
        ],
    )


def test_main_graphql_field_enum_default(output_file: Path) -> None:
    """Test GraphQL enum default values keep the default option behavior."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "field-default-enum.graphql",
        output_path=output_file,
        assert_func=assert_file_content,
        expected_file="field_default_enum.py",
        input_file_type="graphql",
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_graphql_custom_scalar_types(output_file: Path) -> None:
    """Test GraphQL code generation with custom scalar types."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "custom-scalar-types.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="custom_scalar_types.py",
        extra_args=["--extra-template-data", str(GRAPHQL_DATA_PATH / "custom-scalar-types.json")],
    )


def test_main_graphql_builtin_scalar_template_data_is_non_executing(output_file: Path) -> None:
    """Built-in scalar aliases serialize executable-looking public type data."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "builtin-template-data.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="builtin_template_data.py",
        extra_args=[
            "--disable-timestamp",
            "--extra-template-data",
            str(GRAPHQL_DATA_PATH / "builtin-template-data.json"),
        ],
    )
    unsafe_import_calls = [
        node
        for node in ast.walk(ast.parse(output_file.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "__import__"
    ]
    if unsafe_import_calls:  # pragma: no cover - reports the precise generated-source regression
        pytest.fail(f"Generated scalar type executed public template data: {unsafe_import_calls!r}")


def test_main_graphql_custom_scalar_template_data_remains_raw(output_file: Path) -> None:
    """Trusted custom scalar templates retain their raw ``py_type`` context."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "builtin-template-data.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="custom_template_data.py",
        extra_args=[
            "--disable-timestamp",
            "--extra-template-data",
            str(GRAPHQL_DATA_PATH / "builtin-template-data.json"),
            "--custom-template-dir",
            str(GRAPHQL_DATA_PATH.parent / "templates_scalar_template_data"),
        ],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
@pytest.mark.cli_doc(
    options=["--aliases"],
    option_description="""Apply custom field and class name aliases via inline JSON or a JSON file path.

The `--aliases` option allows you to rename fields using an inline JSON object or JSON mapping file.
Supports hierarchical formats for scoped or global aliasing.""",
    input_schema="graphql/field-aliases.graphql",
    cli_args=["--aliases", "graphql/field-aliases.json"],
    golden_output="graphql/field_aliases.py",
)
def test_main_graphql_field_aliases(output_file: Path) -> None:
    """Apply custom field and class name aliases via inline JSON or a JSON file path.

    The `--aliases` option allows you to rename fields using an inline JSON object or JSON mapping file.
    Supports hierarchical formats for scoped or global aliasing.
    """
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "field-aliases.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="field_aliases.py",
        extra_args=["--aliases", str(GRAPHQL_DATA_PATH / "field-aliases.json")],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_graphql_multiple_aliases_pydantic_v2(output_file: Path) -> None:
    """Test GraphQL with multiple aliases using Pydantic v2 AliasChoices."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "multiple-aliases.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="graphql_multiple_aliases_pydantic_v2.py",
        extra_args=[
            "--aliases",
            str(GRAPHQL_DATA_PATH / "multiple-aliases.json"),
            "--output-model-type",
            "pydantic_v2.BaseModel",
        ],
    )


@LEGACY_BLACK_SKIP
def test_main_graphql_serialization_aliases_pydantic_v2(output_file: Path) -> None:
    """Test GraphQL with explicit Pydantic v2 serialization aliases."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "multiple-aliases.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="graphql_serialization_aliases_pydantic_v2.py",
        extra_args=[
            "--aliases",
            str(GRAPHQL_DATA_PATH / "multiple-aliases.json"),
            "--serialization-aliases",
            str(GRAPHQL_DATA_PATH / "serialization-aliases.json"),
            "--output-model-type",
            "pydantic_v2.BaseModel",
        ],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_graphql_casing(output_file: Path) -> None:
    """Test GraphQL code generation with casing transformations."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "casing.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="casing.py",
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_graphql_enums(output_file: Path) -> None:
    """Test GraphQL code generation with enums."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "enums.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="enums.py",
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "22",
    reason="Installed black doesn't support the old style",
)
def test_main_graphql_specialized_enums(output_file: Path) -> None:
    """Test GraphQL code generation with specialized enums for Python 3.11+."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "enums.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="enums_specialized.py",
        extra_args=["--target-python-version", "3.11"],
    )


@pytest.mark.cli_doc(
    options=["--enum-field-as-literal"],
    option_description="""Convert all enum fields to Literal types instead of Enum classes.

The `--enum-field-as-literal all` flag converts all enum types to Literal
type annotations. This is useful when you want string literal types instead
of Enum classes for all enumerations.""",
    input_schema="graphql/enums.graphql",
    cli_args=["--enum-field-as-literal", "all"],
    golden_output="graphql/enum_literals_all.py",
    comparison_output="graphql/enums.py",
)
def test_main_graphql_enums_as_literals_all(output_file: Path) -> None:
    """Convert all enum fields to Literal types instead of Enum classes.

    The `--enum-field-as-literal all` flag converts all enum types to Literal
    type annotations. This is useful when you want string literal types instead
    of Enum classes for all enumerations.
    """
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "enums.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="enum_literals_all.py",
        extra_args=["--enum-field-as-literal", "all"],
    )


@pytest.mark.cli_doc(
    options=["--enum-field-as-literal"],
    option_description="""Convert single-member enums to Literal types.

The `--enum-field-as-literal one` flag only converts enums with a single
member to Literal types, keeping multi-member enums as Enum classes.""",
    input_schema="graphql/enums.graphql",
    cli_args=["--enum-field-as-literal", "one"],
    golden_output="graphql/enum_literals_one.py",
)
def test_main_graphql_enums_as_literals_one(output_file: Path) -> None:
    """Convert single-member enums to Literal types.

    The `--enum-field-as-literal one` flag only converts enums with a single
    member to Literal types, keeping multi-member enums as Enum classes.
    """
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "enums.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="enum_literals_one.py",
        extra_args=["--enum-field-as-literal", "one"],
    )


def test_main_graphql_enums_to_typed_dict(output_file: Path) -> None:
    """Test GraphQL code generation paired with typing.TypedDict output which forces enums as literals."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "enums.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="enums_typed_dict.py",
        extra_args=["--output-model-type", "typing.TypedDict"],
    )


@pytest.mark.cli_doc(
    options=["--ignore-enum-constraints"],
    option_description="""Ignore enum constraints and use base string type instead of Enum classes.

The `--ignore-enum-constraints` flag ignores enum constraints and uses
the base type (str) instead of generating Enum classes. This is useful
when you need flexibility in the values a field can accept beyond the
defined enum members.""",
    input_schema="graphql/enums.graphql",
    cli_args=["--ignore-enum-constraints"],
    golden_output="graphql/enums_ignore_enum_constraints.py",
    comparison_output="graphql/enums.py",
)
def test_main_graphql_enums_ignore_enum_constraints(output_file: Path) -> None:
    """Ignore enum constraints and use base string type instead of Enum classes.

    The `--ignore-enum-constraints` flag ignores enum constraints and uses
    the base type (str) instead of generating Enum classes. This is useful
    when you need flexibility in the values a field can accept beyond the
    defined enum members.
    """
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "enums.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="enums_ignore_enum_constraints.py",
        extra_args=["--ignore-enum-constraints"],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "22",
    reason="Installed black doesn't support the old style",
)
@pytest.mark.cli_doc(
    options=["--no-use-specialized-enum"],
    option_description="""Disable specialized Enum classes for Python 3.11+ code generation.

The `--no-use-specialized-enum` flag prevents the generator from using
specialized Enum classes (StrEnum, IntEnum) when generating code for
Python 3.11+, falling back to standard Enum classes instead.""",
    input_schema="graphql/enums.graphql",
    cli_args=["--target-python-version", "3.11", "--no-use-specialized-enum"],
    golden_output="graphql/enums_no_specialized.py",
    related_options=["--use-specialized-enum", "--target-python-version"],
)
def test_main_graphql_specialized_enums_disabled(output_file: Path) -> None:
    """Disable specialized Enum classes for Python 3.11+ code generation.

    The `--no-use-specialized-enum` flag prevents the generator from using
    specialized Enum classes (StrEnum, IntEnum) when generating code for
    Python 3.11+, falling back to standard Enum classes instead.
    """
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "enums.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="enums_no_specialized.py",
        extra_args=["--target-python-version", "3.11", "--no-use-specialized-enum"],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
@pytest.mark.cli_doc(
    options=["--use-subclass-enum"],
    option_description="""Generate typed Enum subclasses for enums with specific field types.

The `--use-subclass-enum` flag generates Enum classes as subclasses of the
appropriate field type (int, float, bytes, str) when an enum has a specific
type, providing better type safety and IDE support.""",
    input_schema="graphql/enums.graphql",
    cli_args=["--use-subclass-enum"],
    golden_output="graphql/enums_using_subclass.py",
)
def test_main_graphql_enums_subclass(output_file: Path) -> None:
    """Generate typed Enum subclasses for enums with specific field types.

    The `--use-subclass-enum` flag generates Enum classes as subclasses of the
    appropriate field type (int, float, bytes, str) when an enum has a specific
    type, providing better type safety and IDE support.
    """
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "enums.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="enums_using_subclass.py",
        extra_args=["--use-subclass-enum"],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_graphql_union(output_file: Path) -> None:
    """Test GraphQL code generation with union types."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "union.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="union.py",
    )


@pytest.mark.parametrize(
    ("extra_args", "expected_file"),
    [
        (
            ["--target-python-version", "3.10", "--output-model-type", "pydantic_v2.BaseModel"],
            "union_description_cr_pydantic_v2.py",
        ),
        (
            ["--target-python-version", "3.10", "--output-model-type", "dataclasses.dataclass"],
            "union_description_cr_dataclass.py",
        ),
        pytest.param(
            ["--target-python-version", "3.12", "--output-model-type", "pydantic_v2.BaseModel"],
            "union_description_cr_py312.py",
            marks=pytest.mark.skipif(
                int(black.__version__.split(".")[0]) < 23,
                reason="Installed black doesn't support the new 'type' statement",
            ),
        ),
    ],
)
def test_main_graphql_union_description_comment_is_safe(
    output_file: Path,
    extra_args: list[str],
    expected_file: str,
) -> None:
    """Test GraphQL union descriptions with CR in generated comments."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "union-description-cr.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file=expected_file,
        extra_args=extra_args,
        force_exec_validation=True,
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
@pytest.mark.cli_doc(
    options=["--additional-imports"],
    option_description="""Add custom imports to generated output files.

The `--additional-imports` flag allows you to specify custom imports as a
comma-delimited list that will be added to the generated output file. This
is useful when using custom types defined in external modules (e.g.,
"datetime.datetime,datetime.date,mymodule.myclass.MyCustomPythonClass").""",
    input_schema="graphql/additional-imports.graphql",
    cli_args=["--additional-imports", "datetime.datetime,datetime.date,mymodule.myclass.MyCustomPythonClass"],
    golden_output="graphql/additional_imports.py",
)
def test_main_graphql_additional_imports(output_file: Path) -> None:
    """Add custom imports to generated output files.

    The `--additional-imports` flag allows you to specify custom imports as a
    comma-delimited list that will be added to the generated output file. This
    is useful when using custom types defined in external modules (e.g.,
    "datetime.datetime,datetime.date,mymodule.myclass.MyCustomPythonClass").
    """
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "additional-imports.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="additional_imports.py",
        extra_args=[
            "--extra-template-data",
            str(GRAPHQL_DATA_PATH / "additional-imports-types.json"),
            "--additional-imports",
            "datetime.datetime,datetime.date,mymodule.myclass.MyCustomPythonClass",
        ],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_graphql_additional_imports_in_extra_template_data(output_file: Path) -> None:
    """Test additional_imports specified in extra-template-data JSON file (string format)."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "additional-imports.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="additional_imports.py",
        extra_args=[
            "--extra-template-data",
            str(GRAPHQL_DATA_PATH / "additional-imports-in-extra-template-data.json"),
        ],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_graphql_additional_imports_in_extra_template_data_inline_json(output_file: Path) -> None:
    """Test extra-template-data loaded from inline JSON."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "additional-imports.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="additional_imports.py",
        extra_args=[
            "--extra-template-data",
            (GRAPHQL_DATA_PATH / "additional-imports-in-extra-template-data.json").read_text(encoding="utf-8"),
        ],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_graphql_additional_imports_in_extra_template_data_list_format(output_file: Path) -> None:
    """Test additional_imports specified in extra-template-data JSON file (list format)."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "additional-imports.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="additional_imports.py",
        extra_args=[
            "--extra-template-data",
            str(GRAPHQL_DATA_PATH / "additional-imports-list-format.json"),
        ],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_graphql_additional_imports_merged(output_file: Path) -> None:
    """Test merging additional_imports from CLI and extra-template-data JSON."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "additional-imports.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="additional_imports.py",
        extra_args=[
            "--extra-template-data",
            str(GRAPHQL_DATA_PATH / "additional-imports-partial.json"),
            "--additional-imports",
            " datetime.date, mymodule.myclass.MyCustomPythonClass ",
        ],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
@pytest.mark.cli_doc(
    options=["--custom-formatters"],
    option_description="""Apply custom Python code formatters to generated output.

The `--custom-formatters` flag allows you to specify custom Python functions
that will be applied to format the generated code. The formatter is specified
as a module path (e.g., "mymodule.formatter_function"). This is useful for
adding custom comments, modifying code structure, or applying project-specific
formatting rules beyond what black/isort provide.""",
    input_schema="graphql/custom-scalar-types.graphql",
    cli_args=["--custom-formatters", "tests.data.python.custom_formatters.add_comment"],
    golden_output="graphql/custom_formatters.py",
)
def test_main_graphql_custom_formatters(output_file: Path) -> None:
    """Apply custom Python code formatters to generated output.

    The `--custom-formatters` flag allows you to specify custom Python functions
    that will be applied to format the generated code. The formatter is specified
    as a module path (e.g., "mymodule.formatter_function"). This is useful for
    adding custom comments, modifying code structure, or applying project-specific
    formatting rules beyond what black/isort provide.
    """
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "custom-scalar-types.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="custom_formatters.py",
        extra_args=["--custom-formatters", "tests.data.python.custom_formatters.add_comment"],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_graphql_use_standard_collections(output_file: Path) -> None:
    """Test GraphQL code generation with standard collections."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "use-standard-collections.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="use_standard_collections.py",
        extra_args=["--use-standard-collections"],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_graphql_use_union_operator(output_file: Path) -> None:
    """Test GraphQL code generation with union operator syntax."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "use-union-operator.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="use_union_operator.py",
        extra_args=["--use-union-operator"],
    )


@pytest.mark.cli_doc(
    options=["--extra-fields"],
    option_description="""Configure how generated models handle extra fields not defined in schema.

The `--extra-fields` flag sets the generated models to allow, forbid, or
ignore extra fields. With `--extra-fields allow`, models will accept and
store fields not defined in the schema. Options: allow, ignore, forbid.""",
    input_schema="graphql/simple-star-wars.graphql",
    cli_args=["--extra-fields", "allow"],
    golden_output="graphql/simple_star_wars_extra_fields_allow.py",
)
def test_main_graphql_extra_fields_allow(output_file: Path) -> None:
    """Configure how generated models handle extra fields not defined in schema.

    The `--extra-fields` flag sets the generated models to allow, forbid, or
    ignore extra fields. With `--extra-fields allow`, models will accept and
    store fields not defined in the schema. Options: allow, ignore, forbid.
    """
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "simple-star-wars.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="simple_star_wars_extra_fields_allow.py",
        extra_args=["--extra-fields", "allow"],
    )


@pytest.mark.cli_doc(
    options=["--use-type-alias"],
    option_description="""Use TypeAlias instead of root models for type definitions (experimental).

The `--use-type-alias` flag generates TypeAlias declarations instead of
root model classes for certain type definitions. For Python 3.10-3.11, it
generates TypeAliasType, and for Python 3.12+, it uses the 'type' statement
syntax. This feature is experimental.""",
    input_schema="graphql/type_alias.graphql",
    cli_args=["--use-type-alias"],
    golden_output="graphql/type_alias.py",
    related_options=["--target-python-version"],
)
def test_main_graphql_type_alias(output_file: Path) -> None:
    """Use TypeAlias instead of root models for type definitions (experimental).

    The `--use-type-alias` flag generates TypeAlias declarations instead of
    root model classes for certain type definitions. For Python 3.10-3.11, it
    generates TypeAliasType, and for Python 3.12+, it uses the 'type' statement
    syntax. This feature is experimental.
    """
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "type_alias.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="type_alias.py",
        extra_args=["--use-type-alias"],
    )


@pytest.mark.skipif(
    int(black.__version__.split(".")[0]) < 23,
    reason="Installed black doesn't support the new 'type' statement",
)
def test_main_graphql_type_alias_py312(output_file: Path) -> None:
    """Test that type statement syntax is generated for GraphQL schemas with Python 3.12+ and Pydantic v2."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "type_alias.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="type_alias_py312.py",
        extra_args=[
            "--use-type-alias",
            "--target-python-version",
            "3.12",
            "--output-model-type",
            "pydantic_v2.BaseModel",
        ],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
@pytest.mark.cli_doc(
    options=["--dataclass-arguments"],
    option_description="""Customize dataclass decorator arguments via JSON dictionary.

The `--dataclass-arguments` flag accepts custom dataclass arguments as a JSON
dictionary (e.g., '{"frozen": true, "kw_only": true, "slots": true, "order": true}').
This overrides individual flags like --frozen-dataclasses and provides fine-grained
control over dataclass generation.""",
    input_schema="graphql/simple-star-wars.graphql",
    cli_args=[
        "--output-model-type",
        "dataclasses.dataclass",
        "--dataclass-arguments",
        '{"slots": true, "order": true}',
    ],
    golden_output="graphql/simple_star_wars_dataclass_arguments.py",
    related_options=["--frozen-dataclasses", "--keyword-only"],
)
def test_main_graphql_dataclass_arguments(output_file: Path) -> None:
    """Customize dataclass decorator arguments via JSON dictionary.

    The `--dataclass-arguments` flag accepts custom dataclass arguments as a JSON
    dictionary (e.g., '{"frozen": true, "kw_only": true, "slots": true, "order": true}').
    This overrides individual flags like --frozen-dataclasses and provides fine-grained
    control over dataclass generation.
    """
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "simple-star-wars.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="simple_star_wars_dataclass_arguments.py",
        extra_args=[
            "--output-model-type",
            "dataclasses.dataclass",
            "--dataclass-arguments",
            '{"slots": true, "order": true}',
        ],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_graphql_dataclass_arguments_with_pydantic(output_file: Path) -> None:
    """Test GraphQL code generation with dataclass arguments passed but using Pydantic model.

    This verifies that dataclass_arguments is properly ignored for non-dataclass models.
    """
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "simple-star-wars.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="simple_star_wars.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--dataclass-arguments",
            '{"slots": true, "order": true}',
        ],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
@pytest.mark.cli_doc(
    options=["--keyword-only"],
    option_description="""Generate dataclasses with keyword-only fields (Python 3.10+).

The `--keyword-only` flag generates dataclasses where all fields must be
specified as keyword arguments (kw_only=True). This is only available for
Python 3.10+. When combined with `--frozen-dataclasses`, it creates immutable
dataclasses with keyword-only arguments, improving code clarity and preventing
positional argument errors.""",
    input_schema="graphql/simple-star-wars.graphql",
    cli_args=[
        "--output-model-type",
        "dataclasses.dataclass",
        "--frozen-dataclasses",
        "--keyword-only",
        "--target-python-version",
        "3.10",
    ],
    golden_output="graphql/simple_star_wars_dataclass_frozen_kw_only.py",
    related_options=["--frozen-dataclasses", "--target-python-version", "--output-model-type"],
)
def test_main_graphql_dataclass_frozen_keyword_only(output_file: Path) -> None:
    """Generate dataclasses with keyword-only fields (Python 3.10+).

    The `--keyword-only` flag generates dataclasses where all fields must be
    specified as keyword arguments (kw_only=True). This is only available for
    Python 3.10+. When combined with `--frozen-dataclasses`, it creates immutable
    dataclasses with keyword-only arguments, improving code clarity and preventing
    positional argument errors.
    """
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "simple-star-wars.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="simple_star_wars_dataclass_frozen_kw_only.py",
        extra_args=[
            "--output-model-type",
            "dataclasses.dataclass",
            "--frozen-dataclasses",
            "--keyword-only",
            "--target-python-version",
            "3.10",
        ],
    )


def test_main_graphql_class_name_prefix(output_file: Path) -> None:
    """Test GraphQL code generation with class name prefixing."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "simple-star-wars.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="simple_star_wars_class_name_prefix.py",
        extra_args=[
            "--class-name-prefix",
            "Foo",
        ],
    )


@LEGACY_BLACK_SKIP
def test_main_graphql_union_class_name_prefix(output_file: Path) -> None:
    """Test that union type members get class name prefix applied.

    When using --class-name-prefix, the prefix should be applied to both
    the union type name and all its member type references.

    This test verifies fix for issue #2939.
    """
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "union_class_name_prefix.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="union_class_name_prefix.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--class-name-prefix",
            "Foo",
            "--use-annotated",
            "--snake-case-field",
        ],
    )


def test_main_graphql_union_snake_case_field(output_file: Path) -> None:
    """Test that union type references are not converted to snake_case."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "union.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="union_snake_case_field.py",
        extra_args=["--snake-case-field", "--output-model-type", "pydantic_v2.BaseModel"],
    )


@LEGACY_BLACK_SKIP
def test_main_graphql_interface_mro(output_file: Path) -> None:
    """Test that interface inheritance is ordered correctly for Python MRO.

    When a class implements multiple interfaces where some interfaces extend others,
    the base classes must be ordered so that subclasses come before their parent classes.
    For example, if Notification implements Node, and a class implements both
    Node and Notification, the order should be (Notification, Node) not (Node, Notification).

    This test verifies fix for issue #2938.
    """
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "interface_mro.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="interface_mro.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-annotated",
            "--snake-case-field",
        ],
    )


def test_main_graphql_split_graphql_schemas(output_file: Path) -> None:
    """Test GraphQL code generation with multiple schema files in a directory."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "split",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="split_graphql_schemas.py",
    )


def test_main_graphql_use_default_with_default_values(output_file: Path) -> None:
    """Test --use-default combined with --default-values on required GraphQL fields."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "default_values_required.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="default_values_required_use_default.py",
        extra_args=[
            "--use-default",
            "--default-values",
            str(DEFAULT_VALUES_DATA_PATH / "graphql_user_defaults.json"),
        ],
    )


def test_main_graphql_use_default_with_default_values_inline_json(output_file: Path) -> None:
    """Test --default-values loaded from inline JSON for GraphQL."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "default_values_required.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="default_values_required_use_default.py",
        extra_args=[
            "--use-default",
            "--default-values",
            (DEFAULT_VALUES_DATA_PATH / "graphql_user_defaults.json").read_text(encoding="utf-8"),
        ],
    )


@pytest.mark.cli_doc(
    options=["--graphql-no-typename"],
    option_description="""Exclude __typename field from generated GraphQL models.

The `--graphql-no-typename` flag prevents the generator from adding the
`typename__` field (aliased to `__typename`) to generated models. This is
useful when using generated models for GraphQL mutations, as servers typically
don't expect this field in input data.""",
    input_schema="graphql/no-typename.graphql",
    cli_args=["--graphql-no-typename"],
    golden_output="graphql/no_typename.py",
)
def test_main_graphql_no_typename(output_file: Path) -> None:
    """Test that --graphql-no-typename excludes typename__ field from all types."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "no-typename.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="no_typename.py",
        extra_args=["--graphql-no-typename"],
    )


@pytest.mark.parametrize("target_pydantic_version", [None, "2"], ids=["target-unset", "target-2"])
def test_main_graphql_alias_generator_union_members(output_file: Path, target_pydantic_version: str | None) -> None:
    """Keep union members unpinned for --target-pydantic-version 2: they take no alias and no Field import."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "alias_generator_empty_union.graphql",
        output_path=output_file,
        input_file_type="graphql",
        assert_func=assert_file_content,
        expected_file="alias_generator_empty_union.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--alias-generator",
            "to_camel",
            "--graphql-no-typename",
            *target_pydantic_args(target_pydantic_version),
        ],
    )
