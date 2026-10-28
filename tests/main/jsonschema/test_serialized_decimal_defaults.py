"""Integration tests for serialized Decimal schema defaults."""

from __future__ import annotations

import warnings
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import (
    DataModelType,
    DefaultValueType,
    DefaultValueTypeWarning,
    Formatter,
    InputFileType,
    PythonVersion,
)
from tests.conftest import (
    assert_output,
    assert_warnings_contain,
    assert_warnings_do_not_contain,
    create_assert_file_content,
)
from tests.main.conftest import (
    BACKEND_GOLDEN_CASES,
    BACKEND_GOLDEN_TARGET_ARGS,
    JSON_SCHEMA_DATA_PATH,
    _validate_output_files,
    run_generate_file_and_assert,
    run_main_and_assert,
)
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH

if TYPE_CHECKING:
    from pathlib import Path


assert_file_content = create_assert_file_content(EXPECTED_JSON_SCHEMA_PATH)


def test_serialized_decimal_defaults_warn_without_changing_output(output_file: Path) -> None:
    """Keep serialized Decimal defaults for compatibility and report the mismatch once."""
    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always", UserWarning)
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "serialized_decimal_defaults.json",
            output_path=output_file,
            input_file_type="jsonschema",
            assert_func=assert_file_content,
            expected_file="serialized_decimal_defaults/serialized.py",
            extra_args=[
                *BACKEND_GOLDEN_TARGET_ARGS,
                "--disable-timestamp",
                "--formatters",
                "builtin",
            ],
            force_exec_validation=True,
        )

    assert_output(
        "".join(f"{warning.message}\n" for warning in recorded_warnings if warning.category is DefaultValueTypeWarning),
        EXPECTED_JSON_SCHEMA_PATH / "serialized_decimal_defaults" / "compatibility_warning.txt",
    )


@pytest.mark.parametrize(
    ("output_model_type", "expected_name"),
    [
        *BACKEND_GOLDEN_CASES,
        pytest.param(
            DataModelType.PydanticV2Dataclass.value,
            "pydantic_v2_dataclass",
            id="pydantic-v2-dataclass",
        ),
    ],
)
@pytest.mark.cli_doc(
    options=["--deserialize-default-values"],
    option_description="""Deserialize selected direct scalar field defaults into their generated Python types.

The `--deserialize-default-values` option accepts an explicit list of generated value types. For example,
`--deserialize-default-values decimal` emits a direct Decimal field's valid string default as a `Decimal` value,
while `--deserialize-default-values enum` emits matching scalar and list defaults as enum members. Defaults that
cannot be deserialized remain serialized so the generated module stays importable.""",
    input_schema="jsonschema/serialized_decimal_defaults.json",
    cli_args=["--deserialize-default-values", "decimal", "--target-python-version", "3.10"],
    model_outputs={
        "pydantic_v2": "main/jsonschema/serialized_decimal_defaults/pydantic_v2_BaseModel.py",
        "pydantic_v2.dataclass": "main/jsonschema/serialized_decimal_defaults/pydantic_v2_dataclass.py",
        "dataclass": "main/jsonschema/serialized_decimal_defaults/dataclasses_dataclass.py",
        "typeddict": "main/jsonschema/serialized_decimal_defaults/typing_TypedDict.py",
        "msgspec": "main/jsonschema/serialized_decimal_defaults/msgspec_Struct.py",
    },
)
def test_deserialize_decimal_defaults(
    output_model_type: str,
    expected_name: str,
    output_file: Path,
) -> None:
    """Deserialize valid Decimal defaults without making invalid defaults break imports."""
    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always", UserWarning)
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "serialized_decimal_defaults.json",
            output_path=output_file,
            input_file_type="jsonschema",
            assert_func=assert_file_content,
            expected_file=f"serialized_decimal_defaults/{expected_name}.py",
            extra_args=[
                *BACKEND_GOLDEN_TARGET_ARGS,
                "--disable-timestamp",
                "--formatters",
                "builtin",
                "--output-model-type",
                output_model_type,
                "--deserialize-default-values",
                "decimal",
            ],
            force_exec_validation=True,
        )

    match output_model_type:
        case DataModelType.TypingTypedDict.value:
            assert_warnings_do_not_contain(recorded_warnings, "could not be deserialized")
        case _:
            assert_warnings_contain(
                recorded_warnings,
                "1 Decimal default value could not be deserialized",
                "Invoice.invalid_amount",
                "kept serialized to keep generated modules importable",
            )


@pytest.mark.filterwarnings("error")
def test_serialized_decimal_defaults_disable_warnings(output_file: Path) -> None:
    """Route Decimal mismatch warnings through the existing warning suppression option."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "serialized_decimal_defaults.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="serialized_decimal_defaults/serialized.py",
        extra_args=[
            *BACKEND_GOLDEN_TARGET_ARGS,
            "--disable-timestamp",
            "--formatters",
            "builtin",
            "--disable-warnings",
        ],
        force_exec_validation=True,
    )


def test_deserialize_decimal_defaults_respects_type_mapping(output_file: Path) -> None:
    """Skip Decimal conversion after a custom mapping changes the generated type to str."""
    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always", UserWarning)
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "serialized_decimal_defaults.json",
            output_path=output_file,
            input_file_type="jsonschema",
            assert_func=assert_file_content,
            expected_file="serialized_decimal_defaults/type_mapping_string.py",
            extra_args=[
                *BACKEND_GOLDEN_TARGET_ARGS,
                "--disable-timestamp",
                "--formatters",
                "builtin",
                "--deserialize-default-values",
                "decimal",
                "--type-mappings",
                "string+decimal=string",
            ],
            force_exec_validation=True,
        )

    assert_warnings_do_not_contain(
        recorded_warnings,
        "emitted as serialized data instead of Decimal",
        "could not be deserialized",
    )


def test_deserialize_decimal_defaults_respects_type_override(output_file: Path) -> None:
    """Classify the final overridden scalar type instead of its original schema format."""
    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always", UserWarning)
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "serialized_decimal_defaults.json",
            output_path=output_file,
            input_file_type="jsonschema",
            assert_func=assert_file_content,
            expected_file="serialized_decimal_defaults/type_override.py",
            extra_args=[
                *BACKEND_GOLDEN_TARGET_ARGS,
                "--disable-timestamp",
                "--formatters",
                "builtin",
                "--deserialize-default-values",
                "decimal",
                "--type-overrides",
                (
                    '{"Invoice.amount": "builtins.str", '
                    '"Invoice.invalid_amount": "builtins.str", '
                    '"Invoice.nullable_amount": "builtins.str"}'
                ),
            ],
            force_exec_validation=True,
        )

    assert_warnings_do_not_contain(
        recorded_warnings,
        "emitted as serialized data instead of Decimal",
        "could not be deserialized",
    )


def test_deserialize_decimal_defaults_scalar_scope(output_file: Path) -> None:
    """Convert constrained scalar defaults without traversing unions or containers."""
    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always", UserWarning)
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "serialized_decimal_default_scope.json",
            output_path=output_file,
            input_file_type="jsonschema",
            assert_func=assert_file_content,
            expected_file="serialized_decimal_defaults/scalar_scope.py",
            extra_args=[
                *BACKEND_GOLDEN_TARGET_ARGS,
                "--disable-timestamp",
                "--formatters",
                "builtin",
                "--strict-types",
                "int",
                "float",
                "str",
                "--deserialize-default-values",
                "decimal",
            ],
            force_exec_validation=True,
        )

    assert_warnings_do_not_contain(
        recorded_warnings,
        "emitted as serialized data instead of Decimal",
        "could not be deserialized",
    )


@pytest.mark.parametrize(
    "deserialize_args",
    [
        pytest.param(["--deserialize-default-values", "decimal"], id="opt-in"),
        pytest.param([], id="default"),
    ],
)
def test_deserialize_decimal_defaults_ignores_collapsed_root_models(
    deserialize_args: list[str],
    output_file: Path,
) -> None:
    """Process only live models after collapsing a Decimal root wrapper."""
    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always", UserWarning)
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "collapse_root_models_decimal_defaults.json",
            output_path=output_file,
            input_file_type="jsonschema",
            assert_func=assert_file_content,
            expected_file="serialized_decimal_defaults/collapse_root_models.py",
            extra_args=[
                *BACKEND_GOLDEN_TARGET_ARGS,
                "--disable-timestamp",
                "--formatters",
                "builtin",
                "--strict-types",
                "int",
                "float",
                "str",
                "--collapse-root-models",
                *deserialize_args,
            ],
            force_exec_validation=True,
        )

    assert_warnings_do_not_contain(
        recorded_warnings,
        "emitted as serialized data instead of Decimal",
        "could not be deserialized",
    )


def test_deserialize_decimal_defaults_type_alias(output_file: Path) -> None:
    """Resolve direct and single-wrapper scalar aliases before converting their defaults."""
    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always", UserWarning)
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "serialized_decimal_default_alias.json",
            output_path=output_file,
            input_file_type="jsonschema",
            assert_func=assert_file_content,
            expected_file="serialized_decimal_defaults/type_alias.py",
            extra_args=[
                *BACKEND_GOLDEN_TARGET_ARGS,
                "--disable-timestamp",
                "--formatters",
                "builtin",
                "--use-type-alias",
                "--deserialize-default-values",
                "decimal",
            ],
            force_exec_validation=True,
        )

    assert_warnings_do_not_contain(
        recorded_warnings,
        "emitted as serialized data instead of Decimal",
        "could not be deserialized",
    )


def test_deserialize_decimal_defaults_condecimal_collision(output_file: Path) -> None:
    """Alias a condecimal constructor when generated field names shadow Decimal imports."""
    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always", UserWarning)
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "serialized_decimal_default_condecimal_collision.json",
            output_path=output_file,
            input_file_type="jsonschema",
            assert_func=assert_file_content,
            expected_file="serialized_decimal_defaults/condecimal_collision.py",
            extra_args=[
                *BACKEND_GOLDEN_TARGET_ARGS,
                "--disable-timestamp",
                "--formatters",
                "builtin",
                "--strict-types",
                "int",
                "float",
                "str",
                "--deserialize-default-values",
                "decimal",
            ],
            force_exec_validation=True,
        )

    assert_warnings_do_not_contain(
        recorded_warnings,
        "emitted as serialized data instead of Decimal",
        "could not be deserialized",
    )


def test_condecimal_constraints_alias_decimal_without_defaults(output_file: Path) -> None:
    """Alias Decimal used in condecimal constraints even when no value is deserialized."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "condecimal_decimal_collision_no_default.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="serialized_decimal_defaults/condecimal_collision_no_default.py",
        extra_args=[
            *BACKEND_GOLDEN_TARGET_ARGS,
            "--disable-timestamp",
            "--formatters",
            "builtin",
            "--use-decimal-for-multiple-of",
        ],
        force_exec_validation=True,
    )


def test_deserialize_decimal_defaults_with_decimal_multiple_of(output_file: Path) -> None:
    """Use the Pydantic condecimal descriptor created for number multipleOf constraints."""
    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always", UserWarning)
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "serialized_decimal_default_multiple_of.json",
            output_path=output_file,
            input_file_type="jsonschema",
            assert_func=assert_file_content,
            expected_file="serialized_decimal_defaults/multiple_of.py",
            extra_args=[
                *BACKEND_GOLDEN_TARGET_ARGS,
                "--disable-timestamp",
                "--formatters",
                "builtin",
                "--use-decimal-for-multiple-of",
                "--deserialize-default-values",
                "decimal",
            ],
            force_exec_validation=True,
        )

    assert_warnings_do_not_contain(
        recorded_warnings,
        "emitted as serialized data instead of Decimal",
        "could not be deserialized",
    )


@pytest.mark.parametrize("alias_source", ["import", "type"])
def test_deserialize_decimal_defaults_reuses_existing_alias(alias_source: str, output_file: Path) -> None:
    """Reuse the same Decimal alias regardless of whether field or type processing registered it first."""
    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always", UserWarning)
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / f"serialized_decimal_default_alias_from_{alias_source}.json",
            output_path=output_file,
            input_file_type="jsonschema",
            assert_func=assert_file_content,
            expected_file=f"serialized_decimal_defaults/alias_from_{alias_source}.py",
            extra_args=[
                *BACKEND_GOLDEN_TARGET_ARGS,
                "--disable-timestamp",
                "--formatters",
                "builtin",
                "--strict-types",
                "int",
                "float",
                "str",
                "--deserialize-default-values",
                "decimal",
            ],
            force_exec_validation=True,
        )

    assert_warnings_do_not_contain(
        recorded_warnings,
        "emitted as serialized data instead of Decimal",
        "could not be deserialized",
    )


def test_deserialize_decimal_defaults_preserves_python_decimal_override(output_file: Path) -> None:
    """Render public API Decimal overrides through structured imports."""
    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always", UserWarning)
        run_generate_file_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "serialized_decimal_defaults.json",
            output_path=output_file,
            input_file_type=InputFileType.JsonSchema,
            assert_func=assert_file_content,
            expected_file="serialized_decimal_defaults/python_decimal_override.py",
            target_python_version=PythonVersion.PY_310,
            disable_timestamp=True,
            formatters=[Formatter.BUILTIN],
            builtin_format_line_length=88,
            deserialize_default_values=(DefaultValueType.Decimal,),
            default_value_overrides={
                "Invoice.amount": Decimal("9.99"),
                "Invoice.invalid_amount": Decimal("8.88"),
            },
        )

    assert_warnings_do_not_contain(
        recorded_warnings,
        "emitted as serialized data instead of Decimal",
        "could not be deserialized",
    )


@pytest.mark.parametrize(
    "deserialize_default_values",
    [(), (DefaultValueType.Decimal,)],
    ids=["disabled", "decimal"],
)
def test_native_decimal_default_override_resolves_field_collision(
    output_file: Path,
    deserialize_default_values: tuple[DefaultValueType, ...],
) -> None:
    """Keep native Decimal overrides importable when a preceding field shadows Decimal."""
    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always", DefaultValueTypeWarning)
        run_generate_file_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "native_decimal_default_constrained.json",
            output_path=output_file,
            input_file_type=InputFileType.JsonSchema,
            assert_func=assert_file_content,
            expected_file="serialized_decimal_defaults/native_override_constrained.py",
            target_python_version=PythonVersion.PY_310,
            disable_timestamp=True,
            formatters=[Formatter.BUILTIN],
            builtin_format_line_length=88,
            use_decimal_for_multiple_of=True,
            deserialize_default_values=deserialize_default_values,
            default_value_overrides={"NativeDecimalDefaultConstrained.amount": Decimal("1.25")},
        )
    _validate_output_files(
        output_file,
        ["--target-python-version", PythonVersion.PY_310.value],
        force_exec_validation=True,
    )
    assert_warnings_do_not_contain(recorded_warnings, "Decimal default value")


def test_deserialize_decimal_defaults_respects_import_override(output_file: Path) -> None:
    """Leave defaults serialized when Decimal is routed through a custom import module."""
    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always", UserWarning)
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "serialized_decimal_defaults.json",
            output_path=output_file,
            input_file_type="jsonschema",
            assert_func=assert_file_content,
            expected_file="serialized_decimal_defaults/import_override.py",
            extra_args=[
                *BACKEND_GOLDEN_TARGET_ARGS,
                "--disable-timestamp",
                "--formatters",
                "builtin",
                "--deserialize-default-values",
                "decimal",
                "--import-overrides",
                '{"Decimal": "tests.data.python.decimal_compat"}',
            ],
            force_exec_validation=True,
        )

    assert_warnings_do_not_contain(
        recorded_warnings,
        "emitted as serialized data instead of Decimal",
        "could not be deserialized",
    )


def test_deserialize_decimal_defaults_bounded_warning(output_file: Path) -> None:
    """Keep warning memory bounded and preserve importable output for many invalid defaults."""
    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always", UserWarning)
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "serialized_decimal_default_collision.json",
            output_path=output_file,
            input_file_type="jsonschema",
            assert_func=assert_file_content,
            expected_file="serialized_decimal_defaults/bounded_warning.py",
            extra_args=[
                *BACKEND_GOLDEN_TARGET_ARGS,
                "--disable-timestamp",
                "--formatters",
                "builtin",
                "--deserialize-default-values",
                "decimal",
            ],
            force_exec_validation=True,
        )

    assert_warnings_contain(
        recorded_warnings,
        "6 Decimal default values could not be deserialized",
        "DecimalCollision.invalid_amount_1",
        "DecimalCollision.invalid_amount_5",
        "and 1 more",
    )
    assert_warnings_do_not_contain(recorded_warnings, "DecimalCollision.invalid_amount_6")


@pytest.mark.parametrize(
    ("deserialize_args", "expected_name", "warning_fragment"),
    [
        pytest.param(
            (),
            "preset_standard",
            "1 Decimal default value could not be deserialized",
            id="preset",
        ),
        pytest.param(
            ("--no-deserialize-default-values",),
            "preset_no_deserialize",
            "2 Decimal default values were emitted as serialized data instead of Decimal",
            id="explicit-no",
            marks=pytest.mark.cli_doc(
                options=["--no-deserialize-default-values"],
                option_description="""Keep serialized schema defaults instead of deserializing selected types.

The `--no-deserialize-default-values` flag explicitly disables default-value deserialization, including when a
preset enables it. Compatibility warnings still identify direct scalar defaults whose serialized form does not
match the generated Python type.""",
                input_schema="jsonschema/serialized_decimal_defaults.json",
                cli_args=[
                    "--preset",
                    "standard-py310-20260826",
                    "--no-deserialize-default-values",
                    "--disable-timestamp",
                    "--formatters",
                    "builtin",
                ],
                golden_output="jsonschema/serialized_decimal_defaults/preset_no_deserialize.py",
                related_options=["--deserialize-default-values", "--preset"],
            ),
        ),
    ],
)
def test_serialized_decimal_defaults_preset(
    deserialize_args: tuple[str, ...],
    expected_name: str,
    warning_fragment: str,
    output_file: Path,
) -> None:
    """Enable Decimal deserialization in the new preset while allowing an explicit opt-out."""
    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always", UserWarning)
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "serialized_decimal_defaults.json",
            output_path=output_file,
            input_file_type="jsonschema",
            assert_func=assert_file_content,
            expected_file=f"serialized_decimal_defaults/{expected_name}.py",
            extra_args=[
                "--preset",
                "standard-py310-20260826",
                "--disable-timestamp",
                "--formatters",
                "builtin",
                *deserialize_args,
            ],
            force_exec_validation=True,
        )

    assert_warnings_contain(recorded_warnings, warning_fragment)
