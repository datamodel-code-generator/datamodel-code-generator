"""Tests for OpenAPI/Swagger input file code generation."""

from __future__ import annotations

import contextlib
import importlib
import inspect
import json
import pickle
import platform
import re
import shutil
import sys
import warnings
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import black
import pydantic
import pytest
from packaging import version

from datamodel_code_generator import (
    MIN_VERSION,
    DanglingRefWarning,
    DataModelType,
    Error,
    InputFileType,
    OpenAPIScope,
    PythonVersionMin,
    ReadOnlyWriteOnlyModelType,
    chdir,
    generate,
    get_version,
    inferred_message,
    load_data_from_path,
)
from datamodel_code_generator import reference as reference_module
from datamodel_code_generator.__main__ import Exit
from datamodel_code_generator.config import GenerateConfig
from datamodel_code_generator.format import Formatter
from datamodel_code_generator.model import base as model_base
from datamodel_code_generator.model.pydantic_v2.version import (
    PYDANTIC_V2_DATACLASS_ALIAS_NEEDS_FALLBACK,
    PYDANTIC_V2_FIELD_DEPRECATED_NEEDS_JSON_SCHEMA_EXTRA,
)
from datamodel_code_generator.reference import get_singular_name
from tests.conftest import (
    HttpxGetMockFactory,
    MockHttpxResponse,
    assert_directory_content,
    assert_error_message,
    assert_generated_file_matches_output,
    assert_generated_modules_output,
    assert_httpx_get_kwargs,
    assert_output,
    assert_warnings_contain,
    freeze_time,
    validate_generated_code,
)
from tests.main.conftest import (
    ALIASES_DATA_PATH,
    BACKEND_GOLDEN_CASES,
    BACKEND_GOLDEN_TARGET_ARGS,
    BLACK_PY313_SKIP,
    BLACK_PY314_SKIP,
    DATA_PATH,
    DEFAULT_VALUES_DATA_PATH,
    LEGACY_BLACK_SKIP,
    MSGSPEC_LEGACY_BLACK_SKIP,
    OPEN_API_DATA_PATH,
    TIMESTAMP,
    _generated_model,
    _generated_package_module,
    assert_generated_model_json_invalid,
    assert_generated_model_json_validation,
    run_generate_file_and_assert,
    run_main_and_assert,
    run_main_url_and_assert,
    run_main_with_args,
    run_main_with_system_exit,
)
from tests.main.openapi.conftest import EXPECTED_OPENAPI_PATH, assert_file_content

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

EXTERNAL_REF_MAPPING_DATA_PATH = OPEN_API_DATA_PATH / "external_ref_mapping"


@pytest.mark.benchmark
def test_main(output_file: Path) -> None:
    """Test OpenAPI file code generation."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="general.py",
    )


def test_main_openapi_array_type_union_constraints(output_file: Path) -> None:
    """Keep OpenAPI array and string constraints on their matching union branches."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "array_type_union_constraints.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="array_type_union_constraints.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--target-python-version",
            "3.10",
            "--use-standard-collections",
            "--use-union-operator",
            "--disable-timestamp",
        ],
        force_exec_validation=True,
    )
    for valid_json, invalid_json, expected_error_type in (
        ('{"value":"ok"}', '{"value":"x"}', "string_too_short"),
        ('{"value":["ok"]}', '{"value":[]}', "string_type"),
    ):
        assert_generated_model_json_validation(
            output_file,
            module_name="openapi_array_type_union_constraints",
            model_name="Payload",
            valid_json=valid_json,
            invalid_json=invalid_json,
            expected_error_type=expected_error_type,
        )


def test_main_inflect_import_without_typeguard_leak(output_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenAPI generation should keep expected output when inflect starts cold."""
    monkeypatch.delitem(sys.modules, "inflect", raising=False)
    monkeypatch.delitem(sys.modules, "typeguard", raising=False)
    monkeypatch.setattr(reference_module, "_inflect_engine", None)
    get_singular_name.cache_clear()

    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="general.py",
    )


def test_main_openapi_fixed_length_array_tuples_disabled(output_file: Path) -> None:
    """Keep fixed-length homogeneous arrays as lists unless explicitly enabled."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "fixed_length_array_tuples.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="fixed_length_array_tuples_disabled.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
        force_exec_validation=True,
    )


@pytest.mark.cli_doc(
    options=["--use-tuple-for-fixed-length-arrays"],
    option_description="""Generate tuple types for homogeneous fixed-length arrays.

When `--use-tuple-for-fixed-length-arrays` is enabled and an array has one
`items` schema with `minItems == maxItems`, generate a tuple type instead of a
list. An empty fixed-length array becomes `tuple[()]`.""",
    input_schema="openapi/fixed_length_array_tuples.yaml",
    cli_args=["--use-tuple-for-fixed-length-arrays", "--output-model-type", "pydantic_v2.BaseModel"],
    golden_output="openapi/fixed_length_array_tuples.py",
)
def test_main_openapi_fixed_length_array_tuples(output_file: Path) -> None:
    """Generate tuple types for homogeneous fixed-length arrays."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "fixed_length_array_tuples.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="fixed_length_array_tuples.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-tuple-for-fixed-length-arrays",
        ],
        force_exec_validation=True,
    )


@pytest.mark.cli_doc(
    options=["--openapi-include-info-version"],
    option_description="""Emit OpenAPI info.version as a generated constant.

The `--openapi-include-info-version` flag adds `OPENAPI_INFO_VERSION` to the
generated module so applications can check the source OpenAPI document version
at build time or runtime.""",
    input_schema="openapi/api.yaml",
    cli_args=["--openapi-include-info-version"],
    golden_output="openapi/info_version.py",
)
def test_main_openapi_include_info_version(output_file: Path) -> None:
    """Emit OpenAPI info.version as a generated constant."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="info_version.py",
        extra_args=["--openapi-include-info-version"],
    )


def test_main_openapi_include_info_version_modular(output_file: Path) -> None:
    """Emit OpenAPI info.version in the root module for modular output."""
    output_dir = output_file.parent / "output"
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "modular.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        assert_func=assert_file_content,
        output_to_expected=[("__init__.py", EXPECTED_OPENAPI_PATH / "modular_info_version" / "__init__.py")],
        extra_args=["--openapi-include-info-version"],
    )


@pytest.mark.cli_doc(
    options=["--enable-generated-header-marker"],
    option_description="""Include the @generated marker in file header for generated-code tooling.

The `--enable-generated-header-marker` flag marks generated output for tools that
recognize the `@generated` marker.""",
    input_schema="openapi/api.yaml",
    cli_args=["--enable-generated-header-marker"],
    golden_output="openapi/enable_generated_header_marker.py",
)
def test_enable_generated_header_marker(output_file: Path) -> None:
    """Include the @generated marker in file header for generated-code tooling.

    The `--enable-generated-header-marker` flag marks generated output for tools that
    recognize the `@generated` marker.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="enable_generated_header_marker.py",
        extra_args=["--enable-generated-header-marker"],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_openapi_discriminator_enum(output_file: Path) -> None:
    """Test OpenAPI generation with discriminator enum."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_enum.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="discriminator/enum.py",
        extra_args=["--target-python-version", "3.10", "--output-model-type", "pydantic_v2.BaseModel"],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
@pytest.mark.cli_doc(
    options=["--use-enum-values-in-discriminator"],
    option_description="""Use enum values in discriminator mappings for union types.

The `--use-enum-values-in-discriminator` flag configures the code generation behavior.""",
    input_schema="openapi/discriminator_enum.yaml",
    cli_args=["--use-enum-values-in-discriminator", "--output-model-type", "pydantic_v2.BaseModel"],
    golden_output="openapi/discriminator/enum_use_enum_values.py",
)
def test_main_openapi_discriminator_enum_use_enum_values(output_file: Path) -> None:
    """Use enum values in discriminator mappings for union types.

    The `--use-enum-values-in-discriminator` flag configures the code generation behavior.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_enum.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="discriminator/enum_use_enum_values.py",
        extra_args=[
            "--target-python-version",
            "3.10",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-enum-values-in-discriminator",
        ],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_openapi_discriminator_enum_use_enum_values_sanitized(output_file: Path) -> None:
    """Enum values requiring sanitization are rendered as enum members in discriminator."""
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "discriminator_enum_sanitized.yaml",
            output_path=output_file,
            input_file_type="openapi",
            assert_func=assert_file_content,
            expected_file="discriminator/enum_use_enum_values_sanitized.py",
            extra_args=[
                "--target-python-version",
                "3.10",
                "--output-model-type",
                "pydantic_v2.BaseModel",
                "--use-enum-values-in-discriminator",
            ],
        )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_openapi_discriminator_enum_duplicate(output_file: Path) -> None:
    """Test OpenAPI generation with duplicate discriminator enum."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_enum_duplicate.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "discriminator" / "enum_duplicate.py",
        extra_args=["--target-python-version", "3.10", "--output-model-type", "pydantic_v2.BaseModel"],
    )


@pytest.mark.parametrize(
    ("option", "expected_file"),
    [
        (None, "discriminator/duplicate_value.py"),
        ("--collapse-root-models", "discriminator/duplicate_value_collapse_root_models.py"),
        ("--use-type-alias", "discriminator/duplicate_value_type_alias.py"),
    ],
)
def test_main_openapi_discriminator_duplicate_value(option: str | None, expected_file: str, output_file: Path) -> None:
    """Duplicate discriminator values fall back to a regular union."""
    extra_args = [
        "--target-python-version",
        "3.10",
        "--output-model-type",
        "pydantic_v2.BaseModel",
        "--formatters",
        "builtin",
    ]
    if option:
        extra_args.append(option)
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_duplicate_value.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=expected_file,
        extra_args=extra_args,
        force_exec_validation=True,
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_openapi_discriminator_integer_mapping(output_file: Path) -> None:
    """Integer discriminator mapping preserves integer literal values."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_integer_mapping.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "discriminator" / "integer_mapping.py",
        extra_args=["--target-python-version", "3.10", "--output-model-type", "pydantic_v2.BaseModel"],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_openapi_discriminator_integer_mapping_use_enum(output_file: Path) -> None:
    """Integer discriminator mapping preserves enum member literal values."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_integer_mapping.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "discriminator" / "integer_mapping_use_enum.py",
        extra_args=[
            "--target-python-version",
            "3.10",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-enum-values-in-discriminator",
        ],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_openapi_discriminator_float_mapping(output_file: Path) -> None:
    """Unsupported discriminator enum values keep their mapping string values."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_float_mapping.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "discriminator" / "float_mapping.py",
        extra_args=["--target-python-version", "3.10", "--output-model-type", "pydantic_v2.BaseModel"],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_openapi_discriminator_integer_no_mapping(output_file: Path) -> None:
    """Integer discriminator without mapping preserves integer literal values."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_integer_no_mapping.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "discriminator" / "integer_no_mapping.py",
        extra_args=["--target-python-version", "3.10", "--output-model-type", "pydantic_v2.BaseModel"],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_openapi_discriminator_integer_no_mapping_literal(output_file: Path) -> None:
    """Integer discriminator literals remain integers when enums collapse to literals."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_integer_no_mapping.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "discriminator" / "integer_no_mapping_literal.py",
        extra_args=[
            "--target-python-version",
            "3.10",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--enum-field-as-literal",
            "one",
        ],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_openapi_discriminator_enum_single_value(output_file: Path) -> None:
    """Single-value enum discriminator with allOf inheritance."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_enum_single_value.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "discriminator" / "enum_single_value.py",
        extra_args=["--target-python-version", "3.10", "--output-model-type", "pydantic_v2.BaseModel"],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_openapi_discriminator_enum_single_value_use_enum(output_file: Path) -> None:
    """Single-value enum with allOf + --use-enum-values-in-discriminator."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_enum_single_value.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "discriminator" / "enum_single_value_use_enum.py",
        extra_args=[
            "--target-python-version",
            "3.10",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-enum-values-in-discriminator",
        ],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_openapi_discriminator_enum_single_value_anyof(output_file: Path) -> None:
    """Single-value enum discriminator with anyOf - uses enum value, not model name."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_enum_single_value_anyof.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "discriminator" / "enum_single_value_anyof.py",
        extra_args=["--target-python-version", "3.10", "--output-model-type", "pydantic_v2.BaseModel"],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_openapi_discriminator_enum_single_value_anyof_use_enum(output_file: Path) -> None:
    """Single-value enum with anyOf + --use-enum-values-in-discriminator."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_enum_single_value_anyof.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "discriminator" / "enum_single_value_anyof_use_enum.py",
        extra_args=[
            "--target-python-version",
            "3.10",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-enum-values-in-discriminator",
        ],
    )


@pytest.mark.parametrize(
    ("input_file", "expected_file"),
    [
        (
            "discriminator_enum_single_value_anyof.yaml",
            "discriminator/enum_single_value_anyof_use_enum_force_optional.py",
        ),
        (
            "discriminator_integer_mapping.yaml",
            "discriminator/integer_mapping_use_enum_force_optional.py",
        ),
    ],
)
def test_main_openapi_discriminator_enum_use_values_force_optional(
    input_file: str, expected_file: str, output_file: Path
) -> None:
    """Default only single enum-member discriminator literals when forced optional."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / input_file,
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=expected_file,
        extra_args=[
            "--formatters",
            "builtin",
            "--target-python-version",
            "3.10",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--force-optional",
            "--use-enum-values-in-discriminator",
        ],
        force_exec_validation=True,
    )


def test_main_openapi_discriminator_enum_single_value_msgspec(output_file: Path) -> None:
    """Single-value enum discriminator is used as the msgspec tag."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_enum_single_value_msgspec.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "discriminator" / "enum_single_value_msgspec.py",
        extra_args=[
            "--target-python-version",
            "3.10",
            "--output-model-type",
            "msgspec.Struct",
            "--enum-field-as-literal",
            "all",
            "--use-one-literal-as-default",
            "--disable-warnings",
        ],
        force_exec_validation=True,
    )


def test_main_openapi_discriminator_with_properties(output_file: Path) -> None:
    """Test OpenAPI generation with discriminator properties."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_with_properties.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "discriminator" / "with_properties.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )


def test_main_openapi_discriminator_allof(output_file: Path) -> None:
    """Test OpenAPI generation with allOf discriminator polymorphism."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_allof.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "discriminator" / "allof.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--snake-case-field",
            "--use-annotated",
            "--use-union-operator",
            "--collapse-root-models",
        ],
    )


def test_main_openapi_discriminator_allof_no_subtypes(output_file: Path) -> None:
    """Test OpenAPI generation with discriminator but no allOf subtypes.

    This tests the edge case where a schema has a discriminator but nothing
    inherits from it using allOf.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_allof_no_subtypes.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "discriminator" / "allof_no_subtypes.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
        ],
    )


def test_main_openapi_discriminator_short_mapping_names(output_file: Path) -> None:
    """Test OpenAPI generation with discriminator using short mapping names.

    Per OpenAPI spec, mapping values can be short names like "FooItem" instead
    of full refs like "#/components/schemas/FooItem". This tests that short
    names are normalized correctly.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_short_mapping_names.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "discriminator" / "short_mapping_names.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
        ],
    )


def test_main_openapi_discriminator_oneof_short_mapping(output_file: Path) -> None:
    """Use short mapping keys as discriminator literals for oneOf schemas."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_oneof_short_mapping.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="discriminator_oneof_short_mapping.py",
        extra_args=["--disable-timestamp"],
        force_exec_validation=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="generated_discriminator_oneof_short_mapping",
        model_name="Pet",
        valid_json='{"petType":"cat","meow":"yes"}',
        invalid_json='{"petType":"Cat"}',
        expected_error_type="union_tag_invalid",
    )


def test_main_openapi_discriminator_state_isolated_per_document(output_dir: Path) -> None:
    """Do not apply one input document's discriminator metadata to another."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_state_isolation",
        output_path=output_dir,
        input_file_type="openapi",
        expected_directory=EXPECTED_OPENAPI_PATH / "discriminator_state_isolation",
        extra_args=["--disable-timestamp"],
        runtime_validation_module="b",
        runtime_validation_model_name="Holder",
        runtime_validation_data={"pet": {"name": "Milo"}},
    )


def test_main_openapi_path_parameter_operation_override(output_file: Path) -> None:
    """Operation parameters override matching path-item parameters."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "path_parameter_override.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="path_parameter_override.py",
        extra_args=["--openapi-scopes", "paths", "parameters", "--disable-timestamp"],
        force_exec_validation=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="generated_path_parameter_override",
        model_name="PetsGetParametersQuery",
        valid_json='{"limit":100}',
        invalid_json='{"limit":101}',
        expected_error_type="less_than_equal",
    )


def test_main_openapi_discriminator_external_mapping(output_file: Path) -> None:
    """Mapping-only discriminator subtypes can be external refs."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_external_mapping" / "openapi.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "discriminator" / "external_mapping.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
        ],
    )


def test_main_openapi_discriminator_partial_mapping(output_file: Path) -> None:
    """Missing discriminator mappings fall back to the subtype name."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_partial_mapping.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "discriminator" / "partial_mapping.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
        ],
    )


def test_main_openapi_discriminator_no_mapping(output_file: Path) -> None:
    """Test OpenAPI generation with discriminator without mapping.

    This tests the case where a discriminator has only propertyName but no mapping.
    The subtypes are discovered via allOf inheritance.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_no_mapping.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "discriminator" / "no_mapping.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
        ],
    )


def test_main_openapi_discriminator_no_mapping_no_subtypes(output_file: Path) -> None:
    """Test OpenAPI generation with discriminator without mapping and no allOf subtypes.

    This tests the edge case where a discriminator has no mapping and no schemas
    inherit from it using allOf.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_no_mapping_no_subtypes.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "discriminator" / "no_mapping_no_subtypes.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
        ],
    )


def test_main_openapi_allof_with_oneof_ref(output_file: Path) -> None:
    """Test OpenAPI generation with allOf referencing a oneOf schema.

    This tests the case where allOf combines a $ref to a schema with oneOf/discriminator
    and additional properties. Regression test for issue #1763.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_with_oneof_ref.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "allof_with_oneof_ref.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
        ],
    )


def test_main_openapi_allof_with_anyof_ref(output_file: Path) -> None:
    """Test OpenAPI generation with allOf referencing an anyOf schema.

    This tests the case where allOf combines a $ref to a schema with anyOf
    and additional properties.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_with_anyof_ref.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "allof_with_anyof_ref.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
        ],
    )


@pytest.mark.cli_doc(
    options=["--base-class"],
    option_description="""Specify a custom base class for generated models.

The `--base-class` flag configures the code generation behavior.""",
    input_schema="openapi/api.yaml",
    cli_args=["--base-class", "custom_module.Base"],
    golden_output="openapi/base_class.py",
)
def test_main_base_class(output_file: Path) -> None:
    """Specify a custom base class for generated models.

    The `--base-class` flag configures the code generation behavior.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="base_class.py",
        extra_args=["--base-class", "custom_module.Base"],
        copy_files=[(DATA_PATH / "pyproject.toml", output_file.parent / "pyproject.toml")],
    )


def test_target_python_version(output_file: Path) -> None:
    """Test OpenAPI generation with target Python version."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        extra_args=["--target-python-version", f"3.{MIN_VERSION}"],
    )


@BLACK_PY313_SKIP
def test_target_python_version_313_has_future_annotations(output_file: Path) -> None:
    """Test that Python 3.13 target includes future annotations import."""
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "api.yaml",
            output_path=output_file,
            input_file_type=None,
            assert_func=assert_file_content,
            extra_args=["--target-python-version", "3.13"],
        )


@BLACK_PY314_SKIP
def test_target_python_version_314_no_future_annotations(output_file: Path) -> None:
    """Test that Python 3.14 target omits future annotations import (PEP 649)."""
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "api.yaml",
            output_path=output_file,
            input_file_type=None,
            assert_func=assert_file_content,
            extra_args=["--target-python-version", "3.14"],
        )


@pytest.mark.benchmark
def test_main_modular(output_dir: Path) -> None:
    """Test main function on modular file."""
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "modular.yaml",
            output_path=output_dir,
            expected_directory=EXPECTED_OPENAPI_PATH / "modular",
        )


def test_main_modular_reuse_model(output_dir: Path) -> None:
    """Test main function on modular file."""
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "modular.yaml",
            output_path=output_dir,
            expected_directory=EXPECTED_OPENAPI_PATH / "modular_reuse_model",
            extra_args=["--reuse-model"],
        )


def test_main_modular_no_file(tmp_path: Path) -> None:
    """Test main function on modular file with no output name."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "modular.yaml",
        output_path=tmp_path / "output.py",
        input_file_type=None,
        expected_exit=Exit.ERROR,
    )


def test_main_modular_treat_dot_as_module_keeps_subpackage_initializer(output_dir: Path) -> None:
    """Do not replace a generated subpackage initializer with the root module result."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "modular.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        assert_func=assert_file_content,
        output_to_expected=[("foo/__init__.py", "modular_treat_dot_as_module/foo_init.py")],
        extra_args=["--treat-dot-as-module", "--disable-timestamp", "--formatters", "builtin"],
    )
    sys.path.insert(0, str(output_dir.parent))
    try:
        module = importlib.import_module(f"{output_dir.name}.foo")
        assert_output(
            f"{json.dumps(module.__all__)}\n",
            EXPECTED_OPENAPI_PATH / "modular_treat_dot_as_module/foo_exports.txt",
        )
    finally:
        sys.path.remove(str(output_dir.parent))
        for loaded_module in tuple(sys.modules):
            if loaded_module == output_dir.name or loaded_module.startswith(f"{output_dir.name}."):
                del sys.modules[loaded_module]


def test_main_modular_filename(output_file: Path) -> None:
    """Test main function on modular file with filename."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "modular.yaml",
        output_path=output_file,
        input_file_type=None,
        expected_exit=Exit.ERROR,
    )


@pytest.mark.isolate_builtin_formatter_config
def test_main_invalid_dotted_schema_name_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Generate one valid Python module for automatic text stdout output."""
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "invalid_dotted_schema_name.yaml",
            output_path=None,
            input_file_type="openapi",
            expected_stdout_path=EXPECTED_OPENAPI_PATH / "invalid_dotted_schema_name_stdout.py",
            capsys=capsys,
            assert_no_stderr=True,
        )


@pytest.mark.isolate_builtin_formatter_config
def test_main_collapse_root_models_recursive_invalid_dotted_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Carry the circular-root fallback into the invalid-dotted stdout repair."""
    expected_path = EXPECTED_OPENAPI_PATH / "collapse_root_models_recursive_invalid_dotted_stdout.py"
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "collapse_root_models_recursive_invalid_dotted.yaml",
        output_path=None,
        input_file_type="openapi",
        extra_args=["--collapse-root-models", "--disable-timestamp", "--formatters", "builtin"],
        expected_stdout_path=expected_path,
        capsys=capsys,
        assert_no_stderr=True,
    )
    validate_generated_code(expected_path.read_text(), str(expected_path), do_exec=True)


@pytest.mark.isolate_builtin_formatter_config
def test_main_invalid_dotted_mixed_keys_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Fingerprint parsed YAML with heterogeneous mapping keys before a safe retry."""
    expected_path = EXPECTED_OPENAPI_PATH / "invalid_dotted_mixed_keys_stdout.py"
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "invalid_dotted_mixed_keys.yaml",
        output_path=None,
        input_file_type="openapi",
        extra_args=["--disable-timestamp", "--formatters", "builtin"],
        expected_stdout_path=expected_path,
        capsys=capsys,
        assert_no_stderr=True,
    )
    validate_generated_code(expected_path.read_text(), str(expected_path), do_exec=True)


@pytest.mark.isolate_builtin_formatter_config
def test_main_usable_invalid_dotted_schema_name_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Preserve usable legacy stdout even when a dotted name is non-canonical."""
    expected_path = EXPECTED_OPENAPI_PATH / "usable_invalid_dotted_schema_name_stdout.py"
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "usable_invalid_dotted_schema_name.yaml",
        output_path=None,
        input_file_type="openapi",
        extra_args=["--disable-timestamp", "--formatters", "builtin"],
        expected_stdout_path=expected_path,
        capsys=capsys,
        assert_no_stderr=True,
    )
    validate_generated_code(expected_path.read_text(), str(expected_path), do_exec=True)


@pytest.mark.isolate_builtin_formatter_config
def test_main_invalid_dotted_future_import_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Repair a second future import that would make concatenated text invalid."""
    expected_path = EXPECTED_OPENAPI_PATH / "invalid_dotted_future_import_stdout.py"
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "invalid_dotted_future_import.yaml",
        output_path=None,
        input_file_type="openapi",
        extra_args=["--disable-timestamp", "--formatters", "builtin"],
        expected_stdout_path=expected_path,
        capsys=capsys,
        assert_no_stderr=True,
    )
    validate_generated_code(expected_path.read_text(), str(expected_path), do_exec=True)


@pytest.mark.isolate_builtin_formatter_config
def test_main_invalid_dotted_relative_import_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Repair a relative module import that cannot work in standalone stdout text."""
    expected_path = EXPECTED_OPENAPI_PATH / "invalid_dotted_relative_import_stdout.py"
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "invalid_dotted_relative_import.yaml",
        output_path=None,
        input_file_type="openapi",
        extra_args=[
            "--target-python-version",
            "3.14",
            "--disable-future-imports",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ],
        expected_stdout_path=expected_path,
        capsys=capsys,
        assert_no_stderr=True,
    )
    validate_generated_code(expected_path.read_text(), str(expected_path), do_exec=True)


@pytest.mark.isolate_builtin_formatter_config
def test_main_invalid_dotted_import_binding_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Repair a later import that would hide a generated model binding."""
    expected_path = EXPECTED_OPENAPI_PATH / "invalid_dotted_import_binding_stdout.py"
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "invalid_dotted_import_binding.yaml",
        output_path=None,
        input_file_type="openapi",
        extra_args=[
            "--target-python-version",
            "3.14",
            "--disable-future-imports",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ],
        expected_stdout_path=expected_path,
        capsys=capsys,
        assert_no_stderr=True,
    )
    validate_generated_code(expected_path.read_text(), str(expected_path), do_exec=True)


@pytest.mark.isolate_builtin_formatter_config
def test_main_invalid_dotted_conflicting_models_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Repair different model definitions that would share one stdout binding."""
    expected_path = EXPECTED_OPENAPI_PATH / "invalid_dotted_conflicting_models_stdout.py"
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "invalid_dotted_conflicting_models.yaml",
        output_path=None,
        input_file_type="openapi",
        extra_args=[
            "--target-python-version",
            "3.14",
            "--disable-future-imports",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ],
        expected_stdout_path=expected_path,
        capsys=capsys,
        assert_no_stderr=True,
    )
    validate_generated_code(expected_path.read_text(), str(expected_path), do_exec=True)


@pytest.mark.isolate_builtin_formatter_config
def test_main_invalid_dotted_name_priority_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Preserve canonical names before unrelated invalid dotted modules."""
    expected_path = EXPECTED_OPENAPI_PATH / "invalid_dotted_name_priority_stdout.py"
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "invalid_dotted_name_priority.yaml",
        output_path=None,
        input_file_type="openapi",
        extra_args=[
            "--target-python-version",
            "3.14",
            "--disable-future-imports",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ],
        expected_stdout_path=expected_path,
        capsys=capsys,
        assert_no_stderr=True,
    )
    validate_generated_code(expected_path.read_text(), str(expected_path), do_exec=True)


@pytest.mark.isolate_builtin_formatter_config
def test_main_invalid_dotted_info_version_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Repair a future import displaced by the OpenAPI info-version postprocessor."""
    expected_path = EXPECTED_OPENAPI_PATH / "invalid_dotted_info_version_stdout.py"
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "invalid_dotted_info_version.yaml",
        output_path=None,
        input_file_type="openapi",
        extra_args=["--openapi-include-info-version", "--disable-timestamp", "--formatters", "builtin"],
        expected_stdout_path=expected_path,
        capsys=capsys,
        assert_no_stderr=True,
    )
    validate_generated_code(expected_path.read_text(), str(expected_path), do_exec=True)


@pytest.mark.isolate_builtin_formatter_config
def test_main_usable_invalid_dotted_no_future_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Preserve independent modular text when concatenation remains usable."""
    expected_path = EXPECTED_OPENAPI_PATH / "invalid_dotted_no_future_stdout.py"
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "invalid_dotted_future_import.yaml",
        output_path=None,
        input_file_type="openapi",
        extra_args=["--disable-timestamp", "--disable-future-imports", "--formatters", "builtin"],
        expected_stdout_path=expected_path,
        capsys=capsys,
        assert_no_stderr=True,
    )
    validate_generated_code(expected_path.read_text(), str(expected_path), do_exec=True)


@pytest.mark.isolate_builtin_formatter_config
def test_main_usable_invalid_dotted_inherited_enum_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Preserve modular text when inherited-enum processing removes the dependency."""
    expected_path = EXPECTED_OPENAPI_PATH / "usable_invalid_dotted_inherited_enum_stdout.py"
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "usable_invalid_dotted_inherited_enum.yaml",
        output_path=None,
        input_file_type="openapi",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--target-python-version",
            "3.14",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ],
        expected_stdout_path=expected_path,
        capsys=capsys,
        assert_no_stderr=True,
    )
    validate_generated_code(expected_path.read_text(), str(expected_path), do_exec=sys.version_info >= (3, 11))


@pytest.mark.isolate_builtin_formatter_config
def test_main_usable_converging_invalid_dotted_models_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Preserve definitions that differ before inherited-enum extraction but converge afterward."""
    expected_path = EXPECTED_OPENAPI_PATH / "usable_converging_invalid_dotted_models_stdout.py"
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "usable_converging_invalid_dotted_models.yaml",
        output_path=None,
        input_file_type="openapi",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--target-python-version",
            "3.14",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ],
        expected_stdout_path=expected_path,
        capsys=capsys,
        assert_no_stderr=True,
    )
    validate_generated_code(expected_path.read_text(), str(expected_path), do_exec=sys.version_info >= (3, 11))


@pytest.mark.isolate_builtin_formatter_config
def test_main_usable_collapsed_invalid_dotted_models_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Preserve definitions that become identical after root-model collapse."""
    expected_path = EXPECTED_OPENAPI_PATH / "usable_collapsed_invalid_dotted_models_stdout.py"
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "usable_collapsed_invalid_dotted_models.yaml",
        output_path=None,
        input_file_type="openapi",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--target-python-version",
            "3.14",
            "--collapse-root-models",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ],
        expected_stdout_path=expected_path,
        capsys=capsys,
        assert_no_stderr=True,
    )
    validate_generated_code(expected_path.read_text(), str(expected_path), do_exec=True)


@pytest.mark.isolate_builtin_formatter_config
def test_main_usable_discriminator_invalid_dotted_models_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Preserve a collision resolved by imports added during discriminator processing."""
    expected_path = EXPECTED_OPENAPI_PATH / "usable_discriminator_invalid_dotted_models_stdout.py"
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "usable_discriminator_invalid_dotted_models.yaml",
        output_path=None,
        input_file_type="openapi",
        extra_args=[
            "--target-python-version",
            "3.14",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ],
        expected_stdout_path=expected_path,
        capsys=capsys,
        assert_no_stderr=True,
    )
    validate_generated_code(expected_path.read_text(), str(expected_path), do_exec=True)


@pytest.mark.isolate_builtin_formatter_config
def test_main_usable_identical_invalid_dotted_models_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Preserve identical redefinitions when concatenated text remains usable."""
    expected_path = EXPECTED_OPENAPI_PATH / "usable_identical_invalid_dotted_models_stdout.py"
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "usable_identical_invalid_dotted_models.yaml",
        output_path=None,
        input_file_type="openapi",
        extra_args=[
            "--target-python-version",
            "3.14",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ],
        expected_stdout_path=expected_path,
        capsys=capsys,
        assert_no_stderr=True,
    )
    validate_generated_code(expected_path.read_text(), str(expected_path), do_exec=True)


@pytest.mark.isolate_builtin_formatter_config
def test_main_unrelated_invalid_dotted_model_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Do not flatten valid modules for a defect unrelated to the invalid name."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "unrelated_invalid_dotted_model.yaml",
        output_path=None,
        input_file_type="openapi",
        extra_args=[
            "--target-python-version",
            "3.14",
            "--disable-future-imports",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ],
        expected_stdout_path=EXPECTED_OPENAPI_PATH / "unrelated_invalid_dotted_model_stdout.py",
        capsys=capsys,
        assert_no_stderr=True,
    )


@pytest.mark.isolate_builtin_formatter_config
def test_main_invalid_dotted_mixed_module_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Keep the legacy dedup survivor when an invalid and valid name share a module."""
    expected_path = EXPECTED_OPENAPI_PATH / "invalid_dotted_mixed_module_stdout.py"
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "invalid_dotted_mixed_module.yaml",
        output_path=None,
        input_file_type="openapi",
        extra_args=["--disable-timestamp", "--formatters", "builtin"],
        expected_stdout_path=expected_path,
        capsys=capsys,
        assert_no_stderr=True,
    )
    validate_generated_code(expected_path.read_text(), str(expected_path), do_exec=True)


@pytest.mark.isolate_builtin_formatter_config
def test_main_invalid_dotted_schema_name_json_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Preserve structured modules for JSON stdout output."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "invalid_dotted_schema_name.yaml",
        output_path=None,
        input_file_type="openapi",
        extra_args=["--output-format", "json", "--disable-timestamp", "--formatters", "builtin"],
        expected_stdout_path=EXPECTED_OPENAPI_PATH / "invalid_dotted_schema_name_json_stdout.txt",
        capsys=capsys,
        assert_no_stderr=True,
    )


def test_main_invalid_dotted_schema_name_default_directory(output_dir: Path) -> None:
    """Preserve legacy modular output and imports for default directory generation."""
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "invalid_dotted_schema_name.yaml",
            output_path=output_dir,
            input_file_type="openapi",
            expected_directory=EXPECTED_OPENAPI_PATH / "invalid_dotted_schema_name_default",
            importable_module_name="generated_invalid_dotted_name",
            importable_module_file="__init__.py",
        )


@pytest.mark.cli_doc(
    options=["--strict-dotted-module-names"],
    option_description="""Require canonical Python identifiers when inferring dotted module paths.

The `--strict-dotted-module-names` flag applies only to automatic module inference.""",
    input_schema="openapi/invalid_dotted_schema_name.yaml",
    cli_args=["--strict-dotted-module-names"],
    golden_output="openapi/invalid_dotted_schema_name.py",
)
def test_main_strict_dotted_module_names(output_file: Path) -> None:
    """Require canonical Python identifiers when inferring dotted module paths.

    The `--strict-dotted-module-names` flag applies only to automatic module inference.
    """
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "invalid_dotted_schema_name.yaml",
            output_path=output_file,
            input_file_type="openapi",
            assert_func=assert_file_content,
            expected_file="invalid_dotted_schema_name.py",
            extra_args=["--strict-dotted-module-names"],
            importable_module_name="generated_strict_dotted_name",
        )


def test_main_strict_dotted_module_names_explicit_module(output_dir: Path) -> None:
    """Let explicit module treatment override strict automatic inference."""
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "invalid_dotted_schema_name.yaml",
            output_path=output_dir,
            input_file_type="openapi",
            expected_directory=EXPECTED_OPENAPI_PATH / "invalid_dotted_schema_name_default",
            extra_args=["--strict-dotted-module-names", "--treat-dot-as-module"],
        )


def test_main_strict_dotted_module_names_explicit_no_module(output_file: Path) -> None:
    """Let explicit flat treatment override strict automatic inference."""
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "invalid_dotted_schema_name.yaml",
            output_path=output_file,
            input_file_type="openapi",
            assert_func=assert_file_content,
            expected_file="invalid_dotted_schema_name.py",
            extra_args=["--strict-dotted-module-names", "--no-treat-dot-as-module"],
        )


def test_main_strict_dotted_module_names_keeps_valid_modules(output_dir: Path) -> None:
    """Keep inferring valid dotted schema names as modules in strict mode."""
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "modular.yaml",
            output_path=output_dir,
            input_file_type="openapi",
            expected_directory=EXPECTED_OPENAPI_PATH / "modular",
            extra_args=["--strict-dotted-module-names"],
        )


def test_main_no_strict_dotted_module_names_overrides_pyproject(output_dir: Path, tmp_path: Path) -> None:
    """Let the CLI disable strict dotted-name inference configured in pyproject.toml."""
    with chdir(tmp_path), freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "invalid_dotted_schema_name.yaml",
            output_path=output_dir,
            input_file_type="openapi",
            expected_directory=EXPECTED_OPENAPI_PATH / "invalid_dotted_schema_name_default",
            extra_args=["--no-strict-dotted-module-names"],
            copy_files=[
                (
                    DATA_PATH / "config" / "pyproject_strict_dotted_module_names.toml",
                    tmp_path / "pyproject.toml",
                )
            ],
        )


def test_main_strict_dotted_module_names_from_pyproject(output_file: Path, tmp_path: Path) -> None:
    """Enable strict dotted-name inference from pyproject.toml."""
    with chdir(tmp_path), freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "invalid_dotted_schema_name.yaml",
            output_path=output_file,
            input_file_type="openapi",
            assert_func=assert_file_content,
            expected_file="invalid_dotted_schema_name.py",
            copy_files=[
                (
                    DATA_PATH / "config" / "pyproject_strict_dotted_module_names.toml",
                    tmp_path / "pyproject.toml",
                )
            ],
        )


@pytest.mark.isolate_builtin_formatter_config
def test_main_no_strict_dotted_module_names_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Keep the narrow unusable-output repair independent from strict inference."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "invalid_dotted_schema_name.yaml",
        output_path=None,
        input_file_type="openapi",
        extra_args=["--no-strict-dotted-module-names", "--disable-timestamp", "--formatters", "builtin"],
        expected_stdout_path=EXPECTED_OPENAPI_PATH / "invalid_dotted_schema_name_stdout_no_timestamp.py",
        capsys=capsys,
        assert_no_stderr=True,
    )


@pytest.mark.isolate_builtin_formatter_config
def test_main_no_strict_dotted_module_names_from_pyproject_stdout(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Keep the narrow repair when pyproject explicitly disables strict inference."""
    with chdir(tmp_path):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "invalid_dotted_schema_name.yaml",
            output_path=None,
            input_file_type="openapi",
            extra_args=["--disable-timestamp", "--formatters", "builtin"],
            expected_stdout_path=EXPECTED_OPENAPI_PATH / "invalid_dotted_schema_name_stdout_no_timestamp.py",
            capsys=capsys,
            assert_no_stderr=True,
            copy_files=[
                (
                    DATA_PATH / "config" / "pyproject_no_strict_dotted_module_names.toml",
                    tmp_path / "pyproject.toml",
                )
            ],
        )


@pytest.mark.isolate_builtin_formatter_config
def test_main_treat_dot_as_module_preserves_modular_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Preserve explicitly requested module boundaries in text stdout."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "invalid_dotted_schema_name.yaml",
        output_path=None,
        input_file_type="openapi",
        extra_args=["--treat-dot-as-module", "--disable-timestamp", "--formatters", "builtin"],
        expected_stdout_path=EXPECTED_OPENAPI_PATH / "invalid_dotted_schema_name_legacy_stdout.py",
        capsys=capsys,
        assert_no_stderr=True,
    )


@pytest.mark.isolate_builtin_formatter_config
def test_main_schema_validators_preserve_invalid_dotted_modules(capsys: pytest.CaptureFixture[str]) -> None:
    """Keep module-level generated-code modes outside the automatic repair."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "invalid_dotted_schema_name.yaml",
        output_path=None,
        input_file_type="openapi",
        extra_args=["--generate-schema-validators", "--disable-timestamp", "--formatters", "builtin"],
        expected_stdout_path=EXPECTED_OPENAPI_PATH / "invalid_dotted_schema_name_legacy_stdout.py",
        capsys=capsys,
        assert_no_stderr=True,
    )


@pytest.mark.isolate_builtin_formatter_config
def test_main_generic_base_preserves_invalid_dotted_modules(capsys: pytest.CaptureFixture[str]) -> None:
    """Keep synthetic generic-base models outside the automatic repair."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "invalid_dotted_schema_name.yaml",
        output_path=None,
        input_file_type="openapi",
        extra_args=[
            "--use-generic-base-class",
            "--allow-extra-fields",
            "--base-class",
            "builtins.object",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ],
        expected_stdout_path=EXPECTED_OPENAPI_PATH / "invalid_dotted_generic_base_legacy_stdout.py",
        capsys=capsys,
        assert_no_stderr=True,
    )


@pytest.mark.isolate_builtin_formatter_config
def test_main_invalid_dotted_fingerprint_failure_preserves_legacy(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep the completed legacy result when retry input identity cannot be recorded."""

    def fail_pickler(*_: object, **__: object) -> None:
        raise TypeError

    monkeypatch.setattr(pickle, "Pickler", fail_pickler)
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "invalid_dotted_schema_name.yaml",
        output_path=None,
        input_file_type="openapi",
        extra_args=["--disable-timestamp", "--formatters", "builtin"],
        expected_stdout_path=EXPECTED_OPENAPI_PATH / "invalid_dotted_schema_name_legacy_stdout.py",
        capsys=capsys,
        assert_no_stderr=True,
    )


@pytest.mark.parametrize("retry_outcome", ["build_failure", "source_mismatch"])
@pytest.mark.isolate_builtin_formatter_config
def test_main_invalid_dotted_unsafe_retry_preserves_legacy(
    retry_outcome: str,
    capsys: pytest.CaptureFixture[str],
    mocker: MockerFixture,
) -> None:
    """Keep completed legacy stdout whenever its compatibility retry is unsafe."""
    from datamodel_code_generator.parser.openapi import OpenAPIParser

    datamodel_code_generator_module: Any = sys.modules["datamodel_code_generator"]
    probe: Any = None
    expected_calls: list[Any] = []
    match retry_outcome:
        case "build_failure":
            original_build_parser = datamodel_code_generator_module._build_parser
            probe = mocker.patch.object(datamodel_code_generator_module, "_build_parser", autospec=True)

            def fail_retry(*args: Any, **kwargs: Any) -> Any:
                match probe.call_count:
                    case 1:
                        return original_build_parser(*args, **kwargs)
                    case 2:
                        raise RuntimeError
                message = "unexpected extra parser build"  # pragma: no cover
                raise AssertionError(message)  # pragma: no cover

            probe.side_effect = fail_retry
            expected_call = mocker.call(
                InputFileType.OpenAPI,
                mocker.ANY,
                mocker.ANY,
                mocker.ANY,
                mocker.ANY,
                jsonschema_version=mocker.ANY,
                openapi_version=mocker.ANY,
                asyncapi_version=mocker.ANY,
                xmlschema_version=mocker.ANY,
                protobuf_version=mocker.ANY,
                python_type_expressions=mocker.ANY,
            )
            expected_calls = [expected_call, expected_call]
        case "source_mismatch":
            probe = mocker.patch.object(OpenAPIParser, "_get_source_data_fingerprint", autospec=True)

            def change_source_fingerprint(_: OpenAPIParser) -> bytes:
                match probe.call_count:
                    case 1:
                        return bytes(32)
                    case 2:
                        return bytes([1]) * 32
                message = "unexpected extra source fingerprint"  # pragma: no cover
                raise AssertionError(message)  # pragma: no cover

            probe.side_effect = change_source_fingerprint
            expected_calls = [mocker.call(mocker.ANY), mocker.call(mocker.ANY)]
        case _:  # pragma: no cover - fixed parametrization
            pytest.fail(f"unexpected retry outcome: {retry_outcome}")

    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "invalid_dotted_schema_name.yaml",
        output_path=None,
        input_file_type="openapi",
        extra_args=["--disable-timestamp", "--formatters", "builtin"],
        expected_stdout_path=EXPECTED_OPENAPI_PATH / "invalid_dotted_schema_name_legacy_stdout.py",
        capsys=capsys,
        assert_no_stderr=True,
    )
    if probe is None:  # pragma: no cover - fixed parametrization
        pytest.fail(f"retry probe was not configured: {retry_outcome}")
    probe.assert_has_calls(expected_calls)


def test_generate_invalid_dotted_schema_name_default_modules() -> None:
    """Preserve the public generate() multi-module return contract by default."""
    with freeze_time(TIMESTAMP):
        result = generate(
            OPEN_API_DATA_PATH / "invalid_dotted_schema_name.yaml",
            input_file_type=InputFileType.OpenAPI,
        )

    assert_generated_modules_output(
        cast("dict[tuple[str, ...], str]", result),
        EXPECTED_OPENAPI_PATH / "invalid_dotted_schema_name_default",
        transform=lambda content: f"{content}\n",
    )


def test_generate_strict_dotted_module_names(output_file: Path) -> None:
    """Expose strict dotted-name inference through the public generate() config."""
    with freeze_time(TIMESTAMP):
        run_generate_file_and_assert(
            input_path=OPEN_API_DATA_PATH / "invalid_dotted_schema_name.yaml",
            output_path=output_file,
            input_file_type=InputFileType.OpenAPI,
            assert_func=assert_file_content,
            strict_dotted_module_names=True,
            expected_file=EXPECTED_OPENAPI_PATH / "invalid_dotted_schema_name.py",
        )


@pytest.mark.parametrize("source_type", ["text", "mapping"])
def test_generate_inline_openapi_relative_ref_uses_caller_base(source_type: str, output_file: Path) -> None:
    """Resolve inline OpenAPI refs from the caller cwd when output is elsewhere."""
    input_path = OPEN_API_DATA_PATH / "all_of_with_relative_ref" / "openapi.yaml"
    source = (
        input_path.read_text(encoding="utf-8") if source_type == "text" else load_data_from_path(input_path, "utf-8")
    )

    with chdir(input_path.parent):
        generate(
            source,
            input_file_type=InputFileType.OpenAPI,
            output=output_file,
            input_filename=input_path.name,
            output_model_type=DataModelType.PydanticV2BaseModel,
            keep_model_order=True,
            collapse_root_models=True,
            field_constraints=True,
            use_title_as_name=True,
            field_include_all_keys=True,
            use_field_description=True,
            formatters=[Formatter.BUILTIN],
        )

    assert_file_content(output_file, "all_of_with_relative_ref.py")


@pytest.mark.parametrize("source_type", ["text", "mapping"])
def test_generate_invalid_dotted_retry_preserves_relative_ref_base(source_type: str, output_file: Path) -> None:
    """Reuse the first parser base for an invalid-dotted stdout repair retry."""
    input_path = OPEN_API_DATA_PATH / "invalid_dotted_external_relative_ref.yaml"
    source = (
        input_path.read_text(encoding="utf-8") if source_type == "text" else load_data_from_path(input_path, "utf-8")
    )
    config = GenerateConfig(
        input_file_type=InputFileType.OpenAPI,
        output=output_file,
        input_filename=input_path.name,
        disable_timestamp=True,
        formatters=[Formatter.BUILTIN],
    ).model_copy(update={"repair_invalid_dotted_stdout": True})

    with chdir(input_path.parent):
        generate(source, config=config)

    assert_file_content(output_file, "invalid_dotted_external_relative_ref.py")
    with chdir(input_path.parent):
        returned = generate(source, config=config.model_copy(update={"output": None}))
    assert_generated_file_matches_output(returned, output_file)


@pytest.mark.isolate_builtin_formatter_config
def test_main_openapi_no_file(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test main function on non-modular file with no output name."""
    monkeypatch.chdir(tmp_path)

    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "api.yaml",
            output_path=None,
            expected_stdout_path=EXPECTED_OPENAPI_PATH / "no_file.py",
            capsys=capsys,
            expected_stderr=inferred_message.format("openapi") + "\n",
        )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
@pytest.mark.isolate_builtin_formatter_config
@pytest.mark.cli_doc(
    options=["--extra-template-data"],
    option_description="""Pass custom template variables via inline JSON or a JSON file path.

The `--extra-template-data` flag allows you to provide additional variables
from an inline JSON object or JSON file that can be used in custom templates to configure generated
model settings like Config classes, enabling customization beyond standard options.""",
    input_schema="openapi/api.yaml",
    cli_args=["--extra-template-data", "openapi/extra_data.json"],
    model_outputs={
        "pydantic_v2": "openapi/extra_template_data_config_pydantic_v2.py",
    },
)
def test_main_openapi_extra_template_data_config(
    capsys: pytest.CaptureFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass custom template variables via inline JSON or a JSON file path.

    The `--extra-template-data` flag allows you to provide additional variables
    from an inline JSON object or JSON file that can be used in custom templates to configure generated
    model settings like Config classes, enabling customization beyond standard options.
    """
    monkeypatch.chdir(tmp_path)
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "api.yaml",
            output_path=None,
            expected_stdout_path=EXPECTED_OPENAPI_PATH / "extra_template_data_config_pydantic_v2.py",
            capsys=capsys,
            input_file_type=None,
            extra_args=[
                "--extra-template-data",
                str(OPEN_API_DATA_PATH / "extra_data.json"),
                "--output-model-type",
                "pydantic_v2.BaseModel",
            ],
            expected_stderr=inferred_message.format("openapi") + "\n",
        )


@pytest.mark.isolate_builtin_formatter_config
def test_main_custom_template_dir_old_style(
    capsys: pytest.CaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test main function with custom template directory."""
    monkeypatch.chdir(tmp_path)
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "api.yaml",
            output_path=None,
            expected_stdout_path=EXPECTED_OPENAPI_PATH / "custom_template_dir_old_style.py",
            capsys=capsys,
            input_file_type=None,
            extra_args=[
                "--custom-template-dir",
                str(DATA_PATH / "templates_old_style"),
                "--extra-template-data",
                str(OPEN_API_DATA_PATH / "extra_data.json"),
            ],
            expected_stderr=inferred_message.format("openapi") + "\n",
        )


@pytest.mark.cli_doc(
    options=["--custom-template-dir"],
    option_description="""Use custom Jinja2 templates for model generation.

The `--custom-template-dir` option allows you to specify a directory containing custom Jinja2 templates
to override the default templates used for generating data models. This enables full customization of
the generated code structure and formatting. Use with `--extra-template-data` to pass additional data
to the templates.""",
    input_schema="openapi/api.yaml",
    cli_args=["--custom-template-dir", "templates", "--extra-template-data", "openapi/extra_data.json"],
    golden_output="openapi/custom_template_dir.py",
)
@pytest.mark.isolate_builtin_formatter_config
def test_main_openapi_custom_template_dir(
    capsys: pytest.CaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use custom Jinja2 templates for model generation.

    The `--custom-template-dir` option allows you to specify a directory containing custom Jinja2 templates
    to override the default templates used for generating data models. This enables full customization of
    the generated code structure and formatting. Use with `--extra-template-data` to pass additional data
    to the templates.
    """
    monkeypatch.chdir(tmp_path)
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "api.yaml",
            output_path=None,
            expected_stdout_path=EXPECTED_OPENAPI_PATH / "custom_template_dir.py",
            capsys=capsys,
            input_file_type=None,
            extra_args=[
                "--custom-template-dir",
                str(DATA_PATH / "templates"),
                "--extra-template-data",
                str(OPEN_API_DATA_PATH / "extra_data.json"),
            ],
            expected_stderr=inferred_message.format("openapi") + "\n",
        )


@pytest.mark.isolate_builtin_formatter_config
def test_main_openapi_custom_template_dir_include_override(
    capsys: pytest.CaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test custom include lookup when only nested templates are overridden."""
    model_base._get_environment.cache_clear()
    model_base._get_template_with_custom_dir.cache_clear()
    monkeypatch.chdir(tmp_path)
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "api.yaml",
            output_path=None,
            expected_stdout_path=EXPECTED_OPENAPI_PATH / "custom_template_dir_include_override.py",
            capsys=capsys,
            input_file_type=None,
            extra_args=[
                "--custom-template-dir",
                str(DATA_PATH / "templates_include_only"),
                "--extra-template-data",
                str(OPEN_API_DATA_PATH / "extra_data.json"),
                "--output-model-type",
                "pydantic_v2.BaseModel",
            ],
            expected_stderr=inferred_message.format("openapi") + "\n",
        )


@pytest.mark.isolate_builtin_formatter_config
def test_main_openapi_detects_created_include_only_template_directory(
    capsys: pytest.CaptureFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A newly created include-only directory activates a new cached loader."""
    custom_template_dir = tmp_path / "templates"
    custom_config = custom_template_dir / "pydantic_v2/ConfigDict.jinja2"
    extra_args = [
        "--custom-template-dir",
        str(custom_template_dir),
        "--extra-template-data",
        str(OPEN_API_DATA_PATH / "extra_data.json"),
        "--output-model-type",
        "pydantic_v2.BaseModel",
    ]
    monkeypatch.chdir(tmp_path)

    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "api.yaml",
            output_path=None,
            expected_stdout_path=EXPECTED_OPENAPI_PATH / "extra_template_data_config_pydantic_v2.py",
            capsys=capsys,
            input_file_type=None,
            extra_args=extra_args,
            expected_stderr=inferred_message.format("openapi") + "\n",
        )
        custom_config.parent.mkdir(parents=True)
        shutil.copyfile(DATA_PATH / "templates_include_only/pydantic_v2/ConfigDict.jinja2", custom_config)
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "api.yaml",
            output_path=None,
            expected_stdout_path=EXPECTED_OPENAPI_PATH / "custom_template_dir_include_override.py",
            capsys=capsys,
            input_file_type=None,
            extra_args=extra_args,
            expected_stderr=inferred_message.format("openapi") + "\n",
        )


@pytest.mark.isolate_builtin_formatter_config
def test_main_openapi_schema_extensions(
    capsys: pytest.CaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that schema extensions (x-* fields) are passed to custom templates."""
    model_base._get_environment.cache_clear()
    model_base._get_template_with_custom_dir.cache_clear()
    monkeypatch.chdir(tmp_path)
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "schema_extensions.yaml",
            output_path=None,
            expected_stdout_path=EXPECTED_OPENAPI_PATH / "schema_extensions.py",
            capsys=capsys,
            input_file_type=None,
            extra_args=[
                "--custom-template-dir",
                str(DATA_PATH / "templates_extensions"),
                "--output-model-type",
                "pydantic_v2.BaseModel",
            ],
            expected_stderr=inferred_message.format("openapi") + "\n",
        )


@pytest.mark.isolate_builtin_formatter_config
def test_main_openapi_schema_extensions_enum(
    capsys: pytest.CaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that enum schema extensions (x-* fields) are passed to custom enum templates."""
    model_base._get_environment.cache_clear()
    model_base._get_template_with_custom_dir.cache_clear()
    monkeypatch.chdir(tmp_path)
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "schema_extensions_enum.yaml",
            output_path=None,
            expected_stdout_path=EXPECTED_OPENAPI_PATH / "schema_extensions_enum.py",
            capsys=capsys,
            input_file_type=None,
            extra_args=[
                "--custom-template-dir",
                str(DATA_PATH / "templates_extensions"),
                "--output-model-type",
                "pydantic_v2.BaseModel",
            ],
            expected_stderr=inferred_message.format("openapi") + "\n",
        )


@pytest.mark.skipif(
    black.__version__.split(".")[0] >= "24",
    reason="Installed black doesn't support the old style",
)
def test_pyproject(tmp_path: Path) -> None:
    """Test code generation using pyproject.toml configuration."""
    if platform.system() == "Windows":  # pragma: no cover

        def get_path(path: str) -> str:
            return str(path).replace("\\", "\\\\")

    else:

        def get_path(path: str) -> str:
            return str(path)

    output_file: Path = tmp_path / "output.py"
    pyproject_toml_path = Path(DATA_PATH) / "project" / "pyproject.toml"
    pyproject_toml = (
        pyproject_toml_path
        .read_text()
        .replace("INPUT_PATH", get_path(OPEN_API_DATA_PATH / "api.yaml"))
        .replace("OUTPUT_PATH", get_path(output_file))
        .replace("ALIASES_PATH", get_path(OPEN_API_DATA_PATH / "empty_aliases.json"))
        .replace(
            "EXTRA_TEMPLATE_DATA_PATH",
            get_path(OPEN_API_DATA_PATH / "empty_data.json"),
        )
        .replace("CUSTOM_TEMPLATE_DIR_PATH", get_path(tmp_path))
    )
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    with chdir(tmp_path):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "api.yaml",
            output_path=output_file,
            input_file_type=None,
            assert_func=assert_file_content,
        )


def test_pyproject_not_found(tmp_path: Path) -> None:
    """Test code generation when pyproject.toml is not found."""
    output_file: Path = tmp_path / "output.py"
    with chdir(tmp_path):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "api.yaml",
            output_path=output_file,
            input_file_type=None,
            assert_func=assert_file_content,
        )


def test_stdin(monkeypatch: pytest.MonkeyPatch, output_file: Path) -> None:
    """Test OpenAPI code generation from stdin input."""
    run_main_and_assert(
        stdin_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        monkeypatch=monkeypatch,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="general.py",
        transform=lambda s: s.replace("#   filename:  <stdin>", "#   filename:  api.yaml"),
    )


@pytest.mark.cli_doc(
    options=["--validation"],
    option_description="""Enable validation constraints (deprecated, use --field-constraints).

The `--validation` flag configures the code generation behavior.""",
    input_schema="openapi/api.yaml",
    cli_args=["--validation"],
    golden_output="openapi/general.py",
)
def test_validation(mocker: MockerFixture, output_file: Path) -> None:
    """Enable validation constraints (deprecated, use --field-constraints).

    The `--validation` flag configures the code generation behavior.
    """
    mock_prance = mocker.patch("prance.BaseParser")
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="general.py",
        extra_args=["--validation"],
    )
    mock_prance.assert_called_once()


def test_validation_failed(mocker: MockerFixture, output_file: Path) -> None:
    """Test OpenAPI code generation with validation failure."""
    mock_prance = mocker.patch("prance.BaseParser", side_effect=Exception("error"))
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "invalid.yaml",
        output_path=output_file,
        input_file_type="openapi",
        expected_exit=Exit.ERROR,
        extra_args=["--validation"],
    )
    mock_prance.assert_called_once()


@pytest.mark.parametrize(
    ("output_model", "expected_output", "args"),
    [
        ("pydantic_v2.BaseModel", "with_field_constraints_pydantic_v2.py", []),
        (
            "pydantic_v2.BaseModel",
            "with_field_constraints_pydantic_v2_use_generic_container_types.py",
            ["--use-generic-container-types"],
        ),
        (
            "pydantic_v2.BaseModel",
            "with_field_constraints_pydantic_v2_use_generic_container_types_set.py",
            ["--use-generic-container-types", "--use-unique-items-as-set"],
        ),
        (
            "pydantic_v2.BaseModel",
            "with_field_constraints_pydantic_v2_use_standard_collections.py",
            [
                "--use-standard-collections",
            ],
        ),
        (
            "pydantic_v2.BaseModel",
            "with_field_constraints_pydantic_v2_use_standard_collections_set.py",
            ["--use-standard-collections", "--use-unique-items-as-set"],
        ),
    ],
)
@pytest.mark.cli_doc(
    options=["--use-unique-items-as-set"],
    option_description="""Generate set types for arrays with uniqueItems constraint.

The `--use-unique-items-as-set` flag generates Python set types instead of
list types for JSON Schema arrays that have the uniqueItems constraint set
to true, enforcing uniqueness at the type level.""",
    input_schema="openapi/api_constrained.yaml",
    cli_args=["--use-unique-items-as-set", "--field-constraints"],
    golden_output="openapi/with_field_constraints_use_unique_items_as_set.py",
)
def test_main_with_field_constraints(
    output_model: str, expected_output: str, args: list[str], output_file: Path
) -> None:
    """Generate set types for arrays with uniqueItems constraint.

    The `--use-unique-items-as-set` flag generates Python set types instead of
    list types for JSON Schema arrays that have the uniqueItems constraint set
    to true, enforcing uniqueness at the type level.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api_constrained.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file=expected_output,
        extra_args=["--field-constraints", "--output-model-type", output_model, *args],
    )


@pytest.mark.cli_doc(
    options=["--field-constraints"],
    option_description="""Generate Field() with validation constraints from schema.

The `--field-constraints` flag generates Pydantic Field() definitions with
validation constraints (min/max length, pattern, etc.) from the schema.""",
    input_schema="openapi/api_constrained.yaml",
    cli_args=["--field-constraints"],
    model_outputs={
        "pydantic_v2": "main/openapi/with_field_constraints_pydantic_v2.py",
    },
    primary=True,
)
def test_main_field_constraints_model_outputs(output_file: Path) -> None:
    """Generate Field() with validation constraints from schema.

    The `--field-constraints` flag generates Pydantic Field() definitions with
    validation constraints (min/max length, pattern, etc.) from the schema.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api_constrained.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="with_field_constraints.py",
        extra_args=["--field-constraints"],
    )


def test_main_without_field_constraints(output_file: Path) -> None:
    """Test OpenAPI generation without field constraints."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api_constrained.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="without_field_constraints_pydantic_v2.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )


@pytest.mark.parametrize(
    ("output_model", "expected_output"),
    [
        pytest.param(
            "msgspec.Struct",
            "with_aliases_msgspec.py",
            marks=LEGACY_BLACK_SKIP,
        ),
    ],
)
@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
@pytest.mark.cli_doc(
    options=["--aliases"],
    option_description="""Apply custom field and class name aliases via inline JSON or a JSON file path.

The `--aliases` option allows renaming fields and classes via an inline JSON object or JSON mapping file,
providing fine-grained control over generated names independent of schema definitions.""",
    input_schema="openapi/api.yaml",
    cli_args=["--aliases", "openapi/aliases.json", "--target-python-version", "3.10"],
    model_outputs={
        "msgspec": "openapi/with_aliases_msgspec.py",
    },
    primary=True,
)
def test_main_with_aliases(output_model: str, expected_output: str, output_file: Path) -> None:
    """Apply custom field and class name aliases via inline JSON or a JSON file path.

    The `--aliases` option allows renaming fields and classes via an inline JSON object or JSON mapping file,
    providing fine-grained control over generated names independent of schema definitions.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file=expected_output,
        extra_args=[
            "--aliases",
            str(OPEN_API_DATA_PATH / "aliases.json"),
            "--target-python-version",
            "3.10",
            "--output-model-type",
            output_model,
        ],
    )


def test_main_multiple_aliases_parameters_pydantic_v2(output_file: Path) -> None:
    """Test OpenAPI with multiple aliases for parameters using Pydantic v2 AliasChoices."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "multiple_aliases_parameters.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="openapi_multiple_aliases_parameters_pydantic_v2.py",
        extra_args=[
            "--aliases",
            str(DATA_PATH / "aliases" / "multiple_aliases_parameters.json"),
            "--openapi-scopes",
            "paths",
            "schemas",
            "parameters",
            "--use-operation-id-as-name",
            "--output-model-type",
            "pydantic_v2.BaseModel",
        ],
    )


def test_main_openapi_serialization_aliases_parameters_pydantic_v2(output_file: Path) -> None:
    """Test OpenAPI parameters with explicit Pydantic v2 serialization aliases."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "serialization_aliases_parameters.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="openapi_serialization_aliases_parameters.py",
        extra_args=[
            "--serialization-aliases",
            str(DATA_PATH / "aliases" / "serialization_aliases_parameters.json"),
            "--openapi-scopes",
            "paths",
            "parameters",
            "--output-model-type",
            "pydantic_v2.BaseModel",
        ],
    )


def test_main_openapi_parameter_content(output_file: Path) -> None:
    """Test OpenAPI parameters defined with content media types."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "parameter_content.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="parameter_content.py",
        extra_args=[
            "--openapi-scopes",
            "paths",
            "parameters",
        ],
    )


def test_main_with_bad_aliases(output_file: Path) -> None:
    """Test OpenAPI generation with invalid aliases file."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        expected_exit=Exit.ERROR,
        extra_args=["--aliases", str(OPEN_API_DATA_PATH / "not.json")],
    )


def test_main_with_more_bad_aliases(output_file: Path) -> None:
    """Test OpenAPI generation with malformed aliases file."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        expected_exit=Exit.ERROR,
        extra_args=["--aliases", str(OPEN_API_DATA_PATH / "list.json")],
    )


def test_main_with_bad_extra_data(output_file: Path) -> None:
    """Test OpenAPI generation with invalid extra template data file."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        expected_exit=Exit.ERROR,
        extra_args=["--extra-template-data", str(OPEN_API_DATA_PATH / "not.json")],
    )


@pytest.mark.benchmark
def test_main_with_snake_case_field(output_file: Path) -> None:
    """Test OpenAPI generation with snake case field naming."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        extra_args=["--snake-case-field"],
    )


@pytest.mark.benchmark
@pytest.mark.cli_doc(
    options=["--strip-default-none"],
    option_description="""Remove fields with None as default value from generated models.

The `--strip-default-none` option removes fields that have None as their default value from the
generated models. This results in cleaner model definitions by excluding optional fields that
default to None.""",
    input_schema="openapi/api.yaml",
    cli_args=["--strip-default-none"],
    golden_output="openapi/with_strip_default_none.py",
)
def test_main_with_strip_default_none(output_file: Path) -> None:
    """Remove fields with None as default value from generated models.

    The `--strip-default-none` option removes fields that have None as their default value from the
    generated models. This results in cleaner model definitions by excluding optional fields that
    default to None.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        extra_args=["--strip-default-none"],
    )


def test_main_openapi_pydantic_v2_strip_default_none_field_metadata(output_file: Path) -> None:
    """Test strip-default-none removes implicit None defaults from Field metadata."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "strip_default_none_pydantic_v2.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="strip_default_none_pydantic_v2.py",
        extra_args=["--strip-default-none", "--output-model-type", "pydantic_v2.BaseModel"],
    )


@pytest.mark.parametrize(
    ("output_model_type", "target_python_version", "expected_file"),
    [
        (
            "dataclasses.dataclass",
            "3.10",
            "strip_default_none_semantic_dataclass.py",
        ),
        (
            "msgspec.Struct",
            "3.10",
            "strip_default_none_semantic_msgspec.py",
        ),
    ],
    ids=["dataclass", "msgspec"],
)
def test_main_openapi_strip_default_none_semantic_default(
    output_model_type: str,
    target_python_version: str,
    expected_file: str,
    output_file: Path,
) -> None:
    """Test strip-default-none treats only actual None defaults as None."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "strip_default_none_semantic.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=expected_file,
        extra_args=[
            "--strip-default-none",
            "--output-model-type",
            output_model_type,
            "--target-python-version",
            target_python_version,
        ],
    )


def test_disable_timestamp(output_file: Path) -> None:
    """Test OpenAPI generation with timestamp disabled."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        extra_args=["--disable-timestamp"],
    )


@pytest.mark.cli_doc(
    options=["--enable-version-header"],
    option_description="""Include tool version information in file header.

The `--enable-version-header` flag configures the code generation behavior.""",
    input_schema="openapi/api.yaml",
    cli_args=["--enable-version-header"],
    golden_output="openapi/enable_version_header.py",
)
def test_enable_version_header(output_file: Path) -> None:
    """Include tool version information in file header.

    The `--enable-version-header` flag configures the code generation behavior.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="enable_version_header.py",
        extra_args=["--enable-version-header"],
        transform=lambda s: s.replace(f"#   version:   {get_version()}", "#   version:   0.0.0"),
    )


@pytest.mark.cli_doc(
    options=["--enable-command-header"],
    option_description="""Include command-line options in file header for reproducibility.

The `--enable-command-header` flag adds the full command-line used to generate
the file to the header, making it easy to reproduce the generation.""",
    input_schema="openapi/api.yaml",
    cli_args=["--enable-command-header"],
    golden_output="openapi/enable_command_header.py",
)
def test_enable_command_header(output_file: Path) -> None:
    """Include command-line options in file header for reproducibility.

    The `--enable-command-header` flag adds the full command-line used to generate
    the file to the header, making it easy to reproduce the generation.
    """

    def normalize_command(s: str) -> str:
        # Replace the actual command line with a placeholder for consistent testing
        return re.sub(r"#   command:   datamodel-codegen .*", "#   command:   datamodel-codegen [COMMAND]", s)

    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="enable_command_header.py",
        extra_args=["--enable-command-header"],
        transform=normalize_command,
    )


@pytest.mark.parametrize(
    ("header_args", "expected_visible"),
    [
        (["--http-headers", "Authorization: Bearer secret-token"], None),
        (["--http-headers=Authorization: Bearer secret-token"], None),
        (["--http-headers", "Authorization: Bearer secret-token", "--encoding", "utf-8"], "utf-8"),
        (["--http-query-parameters", "api_key=secret-token"], None),
        (["--http-query-parameters=api_key=secret-token"], None),
    ],
)
def test_enable_command_header_redacts_http_headers(
    output_file: Path, header_args: list[str], expected_visible: str | None
) -> None:
    """Redact sensitive HTTP headers from reproducibility command headers."""

    def normalize_command(s: str) -> str:
        return re.sub(r"#   command:   datamodel-codegen .*", "#   command:   datamodel-codegen [COMMAND]", s)

    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="enable_command_header.py",
        extra_args=["--enable-command-header", *header_args],
        transform=normalize_command,
    )
    content = output_file.read_text(encoding="utf-8")
    command_line = next(line for line in content.splitlines() if line.startswith("#   command:"))
    following_option_preserved = expected_visible is None or expected_visible in command_line
    assert_output(
        "\n".join([
            f"redacted={'yes' if '<redacted>' in command_line else 'no'}",
            f"secret_absent={'yes' if 'secret-token' not in command_line else 'no'}",
            f"following_option_preserved={'yes' if following_option_preserved else 'no'}",
        ])
        + "\n",
        EXPECTED_OPENAPI_PATH / "enable_command_header_redacts_http_headers.txt",
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
@pytest.mark.cli_doc(
    options=["--allow-population-by-field-name"],
    option_description="""Allow Pydantic model population by field name (not just alias).

The `--allow-population-by-field-name` flag configures the code generation behavior.""",
    input_schema="openapi/api.yaml",
    cli_args=["--allow-population-by-field-name"],
    model_outputs={
        "pydantic_v2": "openapi/allow_population_by_field_name_pydantic_v2.py",
    },
)
def test_allow_population_by_field_name(output_file: Path) -> None:
    """Allow Pydantic model population by field name (not just alias).

    The `--allow-population-by-field-name` flag configures the code generation behavior.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="allow_population_by_field_name_pydantic_v2.py",
        extra_args=["--allow-population-by-field-name", "--output-model-type", "pydantic_v2.BaseModel"],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
@pytest.mark.cli_doc(
    options=["--allow-extra-fields"],
    option_description="""Allow extra fields in generated Pydantic models (extra='allow').

The `--allow-extra-fields` flag configures the code generation behavior.""",
    input_schema="openapi/api.yaml",
    cli_args=["--allow-extra-fields"],
    model_outputs={
        "pydantic_v2": "openapi/allow_extra_fields_pydantic_v2.py",
    },
)
def test_allow_extra_fields(output_file: Path) -> None:
    """Allow extra fields in generated Pydantic models (extra='allow').

    The `--allow-extra-fields` flag configures the code generation behavior.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="allow_extra_fields_pydantic_v2.py",
        extra_args=["--allow-extra-fields", "--output-model-type", "pydantic_v2.BaseModel"],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
@pytest.mark.cli_doc(
    options=["--enable-faux-immutability"],
    option_description="""Enable faux immutability in Pydantic models (frozen=True).

The `--enable-faux-immutability` flag configures the code generation behavior.""",
    input_schema="openapi/api.yaml",
    cli_args=["--enable-faux-immutability"],
    model_outputs={
        "pydantic_v2": "openapi/enable_faux_immutability_pydantic_v2.py",
    },
)
def test_enable_faux_immutability(output_file: Path) -> None:
    """Enable faux immutability in Pydantic models (frozen=True).

    The `--enable-faux-immutability` flag configures the code generation behavior.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="enable_faux_immutability_pydantic_v2.py",
        extra_args=["--enable-faux-immutability", "--output-model-type", "pydantic_v2.BaseModel"],
    )


@pytest.mark.benchmark
def test_use_default(output_file: Path) -> None:
    """Test OpenAPI generation with use default option."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        extra_args=["--use-default"],
    )


@pytest.mark.cli_doc(
    options=["--force-optional"],
    option_description="""Force all fields to be Optional regardless of required status.

The `--force-optional` flag configures the code generation behavior.""",
    input_schema="openapi/api.yaml",
    cli_args=["--force-optional"],
    golden_output="openapi/force_optional.py",
)
@pytest.mark.benchmark
def test_force_optional(output_file: Path) -> None:
    """Force all fields to be Optional regardless of required status.

    The `--force-optional` flag configures the code generation behavior.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        extra_args=["--force-optional"],
    )


def test_main_with_exclusive(output_file: Path) -> None:
    """Test OpenAPI generation with exclusive keywords."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "exclusive.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
    )


def test_main_subclass_enum(output_file: Path) -> None:
    """Test OpenAPI generation with subclass enum."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "subclass_enum.json",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "22",
    reason="Installed black doesn't support the old style",
)
def test_main_specialized_enum(output_file: Path) -> None:
    """Test OpenAPI generation with specialized enum."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "subclass_enum.json",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="enum_specialized.py",
        extra_args=["--target-python-version", "3.11"],
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
    input_schema="openapi/subclass_enum.json",
    cli_args=["--target-python-version", "3.11", "--no-use-specialized-enum"],
    golden_output="openapi/subclass_enum.py",
    related_options=["--use-specialized-enum", "--target-python-version"],
)
def test_main_specialized_enums_disabled(output_file: Path) -> None:
    """Disable specialized Enum classes for Python 3.11+ code generation.

    The `--no-use-specialized-enum` flag prevents the generator from using
    specialized Enum classes (StrEnum, IntEnum) when generating code for
    Python 3.11+, falling back to standard Enum classes instead.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "subclass_enum.json",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="subclass_enum.py",
        extra_args=["--target-python-version", "3.11", "--no-use-specialized-enum"],
    )


def test_main_use_standard_collections(output_dir: Path) -> None:
    """Test OpenAPI generation with standard collections."""
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "modular.yaml",
            output_path=output_dir,
            expected_directory=EXPECTED_OPENAPI_PATH / "use_standard_collections",
            extra_args=["--use-standard-collections"],
        )


@pytest.mark.skipif(
    black.__version__.split(".")[0] >= "24",
    reason="Installed black doesn't support the old style",
)
def test_main_use_generic_container_types(output_dir: Path) -> None:
    """Test OpenAPI generation with generic container types."""
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "modular.yaml",
            output_path=output_dir,
            expected_directory=EXPECTED_OPENAPI_PATH / "use_generic_container_types",
            extra_args=["--use-generic-container-types"],
        )


@pytest.mark.skipif(
    black.__version__.split(".")[0] >= "24",
    reason="Installed black doesn't support the old style",
)
@pytest.mark.benchmark
def test_main_use_generic_container_types_standard_collections(
    output_dir: Path,
) -> None:
    """Test OpenAPI generation with generic container types and standard collections."""
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "modular.yaml",
            output_path=output_dir,
            expected_directory=EXPECTED_OPENAPI_PATH / "use_generic_container_types_standard_collections",
            extra_args=["--use-generic-container-types", "--use-standard-collections"],
        )


def test_main_original_field_name_delimiter_without_snake_case_field(
    capsys: pytest.CaptureFixture, output_file: Path
) -> None:
    """Test OpenAPI generation with original field name delimiter error."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "modular.yaml",
        output_path=output_file,
        input_file_type=None,
        expected_exit=Exit.ERROR,
        extra_args=["--original-field-name-delimiter", "-"],
        capsys=capsys,
        expected_stderr_contains="`--original-field-name-delimiter` can not be used without `--snake-case-field`.",
    )


@pytest.mark.parametrize(
    ("output_model", "expected_output", "date_type"),
    [
        ("pydantic_v2.BaseModel", "datetime_pydantic_v2.py", "AwareDatetime"),
        ("pydantic_v2.BaseModel", "datetime_pydantic_v2_datetime.py", "datetime"),
        ("pydantic_v2.BaseModel", "datetime_pydantic_v2_past_datetime.py", "PastDatetime"),
        ("pydantic_v2.BaseModel", "datetime_pydantic_v2_future_datetime.py", "FutureDatetime"),
        ("dataclasses.dataclass", "datetime_dataclass.py", "datetime"),
        ("msgspec.Struct", "datetime_msgspec.py", "datetime"),
    ],
)
@pytest.mark.cli_doc(
    options=["--output-datetime-class"],
    option_description="""Specify datetime class type for date-time schema fields.

The `--output-datetime-class` flag controls which datetime type to use for fields
with date-time format. Options include 'AwareDatetime' for timezone-aware datetimes
or 'datetime' for standard Python datetime objects.""",
    input_schema="openapi/datetime.yaml",
    cli_args=["--output-datetime-class", "AwareDatetime"],
    golden_output="openapi/datetime_pydantic_v2.py",
)
def test_main_openapi_aware_datetime(
    output_model: str, expected_output: str, date_type: str, output_file: Path
) -> None:
    """Specify datetime class type for date-time schema fields.

    The `--output-datetime-class` flag controls which datetime type to use for fields
    with date-time format. Options include 'AwareDatetime' for timezone-aware datetimes
    or 'datetime' for standard Python datetime objects.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "datetime.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=expected_output,
        extra_args=["--output-datetime-class", date_type, "--output-model-type", output_model],
    )


def test_main_openapi_datetime(output_file: Path) -> None:
    """Test OpenAPI generation with datetime types."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "datetime.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="datetime_pydantic_v2.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )


@pytest.mark.parametrize(
    ("date_class", "expected_output"),
    [
        ("PastDate", "date_class_past_date.py"),
        ("FutureDate", "date_class_future_date.py"),
    ],
)
@pytest.mark.cli_doc(
    options=["--output-date-class"],
    option_description="""Specify date class type for date schema fields.

The `--output-date-class` flag controls which date type to use for fields
with date format. Options include 'PastDate' for past dates only
or 'FutureDate' for future dates only. This is a Pydantic v2 only feature.""",
    input_schema="openapi/date_class.yaml",
    cli_args=["--output-date-class", "PastDate"],
    golden_output="openapi/date_class_past_date.py",
)
@freeze_time(TIMESTAMP)
def test_main_openapi_date_class(date_class: str, expected_output: str, output_file: Path) -> None:
    """Specify date class type for date schema fields.

    The `--output-date-class` flag controls which date type to use for fields
    with date format. Options include 'PastDate' for past dates only
    or 'FutureDate' for future dates only. This is a Pydantic v2 only feature.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "date_class.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=expected_output,
        extra_args=["--output-date-class", date_class, "--output-model-type", "pydantic_v2.BaseModel"],
    )


def test_main_models_not_found(capsys: pytest.CaptureFixture, output_file: Path) -> None:
    """Test OpenAPI generation with models not found error."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "no_components.yaml",
        output_path=output_file,
        input_file_type="openapi",
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Models not found in the input data",
    )


@pytest.mark.skipif(
    version.parse(pydantic.VERSION) < version.parse("1.9.0"),
    reason="Require Pydantic version 1.9.0 or later ",
)
@pytest.mark.cli_doc(
    options=["--enum-field-as-literal"],
    option_description="""Convert single-member enums to Literal types in OpenAPI schemas.

The `--enum-field-as-literal one` flag converts enums with a single member
to Literal type annotations while keeping multi-member enums as Enum classes.""",
    input_schema="openapi/enum_models.yaml",
    cli_args=["--enum-field-as-literal", "one"],
    golden_output="openapi/enum_models/one.py",
)
def test_main_openapi_enum_models_as_literal_one(min_version: str, output_file: Path) -> None:
    """Convert single-member enums to Literal types in OpenAPI schemas.

    The `--enum-field-as-literal one` flag converts enums with a single member
    to Literal type annotations while keeping multi-member enums as Enum classes.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "enum_models.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="enum_models/one.py",
        extra_args=["--enum-field-as-literal", "one", "--target-python-version", min_version],
    )


@pytest.mark.skipif(
    version.parse(pydantic.VERSION) < version.parse("1.9.0"),
    reason="Require Pydantic version 1.9.0 or later ",
)
@pytest.mark.cli_doc(
    options=["--use-one-literal-as-default"],
    option_description="""Use single literal value as default when enum has only one option.

The `--use-one-literal-as-default` flag configures the code generation behavior.""",
    input_schema="openapi/enum_models.yaml",
    cli_args=["--use-one-literal-as-default", "--enum-field-as-literal", "one"],
    golden_output="openapi/enum_models/one_literal_as_default.py",
)
def test_main_openapi_use_one_literal_as_default(min_version: str, output_file: Path) -> None:
    """Use single literal value as default when enum has only one option.

    The `--use-one-literal-as-default` flag configures the code generation behavior.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "enum_models.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "enum_models" / "one_literal_as_default.py",
        extra_args=[
            "--enum-field-as-literal",
            "one",
            "--target-python-version",
            min_version,
            "--use-one-literal-as-default",
        ],
    )


@pytest.mark.skipif(
    version.parse(pydantic.VERSION) < version.parse("1.9.0"),
    reason="Require Pydantic version 1.9.0 or later ",
)
@pytest.mark.skipif(
    black.__version__.split(".")[0] >= "24",
    reason="Installed black doesn't support the old style",
)
def test_main_openapi_enum_models_as_literal_all(min_version: str, output_file: Path) -> None:
    """Test OpenAPI generation with all enum models as literal."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "enum_models.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="enum_models/all.py",
        extra_args=["--enum-field-as-literal", "all", "--target-python-version", min_version],
    )


@pytest.mark.skipif(
    version.parse(pydantic.VERSION) < version.parse("1.9.0"),
    reason="Require Pydantic version 1.9.0 or later ",
)
@pytest.mark.skipif(
    black.__version__.split(".")[0] >= "24",
    reason="Installed black doesn't support the old style",
)
def test_main_openapi_enum_models_as_literal(output_file: Path) -> None:
    """Test OpenAPI generation with enum models as literal."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "enum_models.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "enum_models" / "as_literal.py",
        extra_args=["--enum-field-as-literal", "all", "--target-python-version", f"3.{MIN_VERSION}"],
    )


@pytest.mark.benchmark
def test_main_openapi_all_of_required(output_file: Path) -> None:
    """Test OpenAPI generation with allOf required fields."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_required.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="allof_required.py",
    )


@pytest.mark.benchmark
def test_main_openapi_nullable(output_file: Path) -> None:
    """Test OpenAPI generation with nullable types."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "nullable.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="nullable.py",
    )


def test_main_openapi_use_missing_sentinel_nullable_keyword(output_file: Path) -> None:
    """Test --use-missing-sentinel preserves OpenAPI nullable keyword fields."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "missing_sentinel_nullable.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="missing_sentinel_nullable.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", "--use-missing-sentinel"],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="missing_sentinel_nullable",
        model_name="MissingSentinelNullable",
        valid_json='{"requiredNullable": null, "nullableUnrequired": null}',
        invalid_json='{"requiredNullable": {}}',
        expected_error_type="int_type",
        expected_attribute_path=("nullableUnrequired",),
    )


@pytest.mark.cli_doc(
    options=["--strict-nullable"],
    option_description="""Treat default field as a non-nullable field.

The `--strict-nullable` flag ensures that fields with default values are generated
with their exact schema type (non-nullable), rather than being made nullable.

This is particularly useful when combined with `--use-default` to generate models
where optional fields have defaults but cannot accept `None` values.""",
    input_schema="openapi/nullable.yaml",
    cli_args=["--strict-nullable"],
    golden_output="openapi/nullable_strict_nullable.py",
    related_options=["--use-default"],
)
def test_main_openapi_nullable_strict_nullable(output_file: Path) -> None:
    """Treat default field as a non-nullable field.

    The `--strict-nullable` flag ensures that fields with default values are generated
    with their exact schema type (non-nullable), rather than being made nullable.

    This is particularly useful when combined with `--use-default` to generate models
    where optional fields have defaults but cannot accept `None` values.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "nullable.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="nullable_strict_nullable.py",
        extra_args=["--strict-nullable"],
    )


def test_main_openapi_ref_nullable_strict_nullable(output_file: Path) -> None:
    """Test that nullable attribute from $ref schema is propagated."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "ref_nullable.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="ref_nullable_strict_nullable.py",
        extra_args=["--strict-nullable", "--use-union-operator"],
    )


@LEGACY_BLACK_SKIP
@pytest.mark.parametrize(
    ("output_model", "expected_output"),
    [
        (
            "pydantic_v2.BaseModel",
            "pydantic_v2.py",
        ),
        (
            "msgspec.Struct",
            "msgspec_pattern.py",
        ),
    ],
)
@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_openapi_pattern(output_model: str, expected_output: str, output_file: Path) -> None:
    """Test OpenAPI generation with pattern validation."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "pattern.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=f"pattern/{expected_output}",
        extra_args=["--target-python-version", "3.10", "--output-model-type", output_model],
        transform=lambda s: s.replace("pattern.yaml", "pattern.json"),
    )


@pytest.mark.parametrize(
    ("expected_output", "args"),
    [
        ("pattern_with_lookaround_pydantic_v2.py", []),
        (
            "pattern_with_lookaround_pydantic_v2_field_constraints.py",
            ["--field-constraints"],
        ),
    ],
)
@pytest.mark.skipif(
    black.__version__.split(".")[0] < "22",
    reason="Installed black doesn't support Python version 3.10",
)
def test_main_openapi_pattern_with_lookaround_pydantic_v2(
    expected_output: str, args: list[str], output_file: Path
) -> None:
    """Test OpenAPI generation with pattern lookaround for Pydantic v2."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "pattern_lookaround.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=expected_output,
        extra_args=["--target-python-version", "3.10", "--output-model-type", "pydantic_v2.BaseModel", *args],
    )


@pytest.mark.parametrize(
    ("expected_output", "args"),
    [
        ("pattern_with_lookaround_pydantic_v2_dataclass.py", []),
        (
            "pattern_with_lookaround_pydantic_v2_dataclass_field_constraints.py",
            ["--field-constraints"],
        ),
    ],
)
def test_main_openapi_pattern_with_lookaround_pydantic_v2_dataclass(
    expected_output: str, args: list[str], output_file: Path
) -> None:
    """Test OpenAPI generation with pattern lookaround for Pydantic v2 dataclasses."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "pattern_lookaround.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=expected_output,
        extra_args=["--target-python-version", "3.10", "--output-model-type", "pydantic_v2.dataclass", *args],
        force_exec_validation=True,
    )


def test_main_generate_custom_class_name_generator_modular(
    tmp_path: Path,
) -> None:
    """Test OpenAPI generation with custom class name generator in modular mode."""
    output_path = tmp_path / "model"
    main_modular_custom_class_name_dir = EXPECTED_OPENAPI_PATH / "modular_custom_class_name"

    def custom_class_name_generator(name: str) -> str:
        return f"Custom{name[0].upper() + name[1:]}"

    with freeze_time(TIMESTAMP):
        input_ = (OPEN_API_DATA_PATH / "modular.yaml").relative_to(Path.cwd())
        generate(
            input_=input_,
            input_file_type=InputFileType.OpenAPI,
            output=output_path,
            custom_class_name_generator=custom_class_name_generator,
        )

        assert_directory_content(output_path, main_modular_custom_class_name_dir)


def test_main_http_openapi(mock_httpx_get: HttpxGetMockFactory, output_file: Path) -> None:
    """Test OpenAPI code generation from HTTP URL."""
    httpx_get_mock = mock_httpx_get(
        MockHttpxResponse("https://example.com/refs.yaml", OPEN_API_DATA_PATH / "refs.yaml"),
        MockHttpxResponse(
            "https://teamdigitale.github.io/openapi/0.0.6/definitions.yaml",
            OPEN_API_DATA_PATH / "definitions.yaml",
        ),
    )

    run_main_url_and_assert(
        url="https://example.com/refs.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="http_refs.py",
    )
    assert_httpx_get_kwargs(
        httpx_get_mock,
        expected_urls=[
            "https://example.com/refs.yaml",
            "https://teamdigitale.github.io/openapi/0.0.6/definitions.yaml",
        ],
    )


def test_main_http_openapi_with_custom_port(mock_httpx_get: HttpxGetMockFactory, output_file: Path) -> None:
    """Test OpenAPI code generation from HTTP URL with custom port preserves port in refs."""
    httpx_get_mock = mock_httpx_get(
        MockHttpxResponse(
            "https://example.com:8123/openapi.json",
            OPEN_API_DATA_PATH / "http_openapi_with_custom_port.yaml",
        )
    )

    run_main_url_and_assert(
        url="https://example.com:8123/openapi.json",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="http_openapi_with_custom_port.py",
        extra_args=["--disable-timestamp"],
    )

    assert_httpx_get_kwargs(httpx_get_mock, expected_url="https://example.com:8123/openapi.json")


@pytest.mark.cli_doc(
    options=["--disable-appending-item-suffix"],
    option_description="""Disable appending 'Item' suffix to array item types.

The `--disable-appending-item-suffix` flag configures the code generation behavior.""",
    input_schema="openapi/api_constrained.yaml",
    cli_args=["--disable-appending-item-suffix", "--field-constraints"],
    golden_output="openapi/disable_appending_item_suffix.py",
)
def test_main_disable_appending_item_suffix(output_file: Path) -> None:
    """Disable appending 'Item' suffix to array item types.

    The `--disable-appending-item-suffix` flag configures the code generation behavior.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api_constrained.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        extra_args=["--field-constraints", "--disable-appending-item-suffix"],
    )


@pytest.mark.cli_doc(
    options=["--openapi-scopes"],
    option_description="""Specify OpenAPI scopes to generate (schemas, paths, parameters).

The `--openapi-scopes` flag configures the code generation behavior.""",
    input_schema="openapi/body_and_parameters.yaml",
    cli_args=["--openapi-scopes", "paths", "schemas"],
    golden_output="openapi/body_and_parameters/general.py",
)
def test_main_openapi_body_and_parameters(output_file: Path) -> None:
    """Specify OpenAPI scopes to generate (schemas, paths, parameters).

    The `--openapi-scopes` flag configures the code generation behavior.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "body_and_parameters.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "body_and_parameters" / "general.py",
        extra_args=["--openapi-scopes", "paths", "schemas"],
    )


def test_main_openapi_body_and_parameters_remote_ref(mock_httpx_get: HttpxGetMockFactory, output_file: Path) -> None:
    """Test OpenAPI generation with body and parameters remote reference."""
    input_path = OPEN_API_DATA_PATH / "body_and_parameters_remote_ref.yaml"
    httpx_get_mock = mock_httpx_get(MockHttpxResponse("https://schema.example", input_path))

    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "body_and_parameters" / "remote_ref.py",
        extra_args=["--openapi-scopes", "paths", "schemas", "--allow-remote-refs"],
    )
    assert_httpx_get_kwargs(httpx_get_mock, expected_url="https://schema.example")


def test_main_openapi_body_and_parameters_only_paths(output_file: Path) -> None:
    """Test OpenAPI generation with only paths scope."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "body_and_parameters.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "body_and_parameters" / "only_paths.py",
        extra_args=["--openapi-scopes", "paths"],
    )


def test_main_openapi_body_and_parameters_only_schemas(output_file: Path) -> None:
    """Test OpenAPI generation with only schemas scope."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "body_and_parameters.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "body_and_parameters" / "only_schemas.py",
        extra_args=["--openapi-scopes", "schemas"],
    )


def test_main_openapi_content_in_parameters(output_file: Path) -> None:
    """Test OpenAPI generation with content in parameters."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "content_in_parameters.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="content_in_parameters.py",
    )


def test_main_openapi_oas_response_reference(output_file: Path) -> None:
    """Test OpenAPI generation with OAS response reference."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "oas_response_reference.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="oas_response_reference.py",
        extra_args=["--openapi-scopes", "paths", "schemas"],
    )


def test_main_openapi_json_pointer(output_file: Path) -> None:
    """Test OpenAPI generation with JSON pointer references."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "json_pointer.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="json_pointer.py",
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
@pytest.mark.cli_doc(
    options=["--use-annotated"],
    option_description="""Use typing.Annotated for field constraints in OpenAPI schemas.

The `--use-annotated` flag wraps field types with `typing.Annotated` to
include constraint metadata, enabling runtime validation frameworks to
access constraints directly from type annotations.

`--use-annotated` alone does not preserve `uniqueItems: true` as set semantics.
Use [`--use-unique-items-as-set`](#use-unique-items-as-set) when you need the
generated type to enforce uniqueness for array schemas like `Pets`.""",
    input_schema="openapi/api_constrained.yaml",
    cli_args=["--field-constraints", "--use-annotated"],
    golden_output="openapi/use_annotated_with_field_constraints_pydantic_v2.py",
    related_options=["--field-constraints"],
)
def test_main_use_annotated_with_field_constraints(min_version: str, output_file: Path) -> None:
    """Use typing.Annotated for field constraints in OpenAPI schemas.

    The `--use-annotated` flag wraps field types with `typing.Annotated` to
    include constraint metadata, enabling runtime validation frameworks to
    access constraints directly from type annotations.

    `--use-annotated` alone does not preserve `uniqueItems: true` as set semantics.
    Use `--use-unique-items-as-set` when you need the generated type to enforce
    uniqueness for array schemas like `Pets`.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api_constrained.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="use_annotated_with_field_constraints_pydantic_v2.py",
        extra_args=[
            "--field-constraints",
            "--use-annotated",
            "--target-python-version",
            min_version,
            "--output-model-type",
            "pydantic_v2.BaseModel",
        ],
    )


def test_main_nested_enum(output_file: Path) -> None:
    """Test OpenAPI generation with nested enum."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "nested_enum.json",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
    )


def test_openapi_special_yaml_keywords(mocker: MockerFixture, output_file: Path) -> None:
    """Test OpenAPI generation with special YAML keywords."""
    mock_prance = mocker.patch("prance.BaseParser")
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "special_yaml_keywords.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="special_yaml_keywords.py",
        extra_args=["--validation"],
    )
    mock_prance.assert_called_once()


@pytest.mark.skipif(
    black.__version__.split(".")[0] < "22",
    reason="Installed black doesn't support Python version 3.10",
)
def test_main_openapi_nullable_use_union_operator(output_file: Path) -> None:
    """Test OpenAPI generation with nullable using union operator."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "nullable.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="nullable_strict_nullable_use_union_operator.py",
        extra_args=["--use-union-operator", "--strict-nullable"],
    )


def test_external_relative_ref(tmp_path: Path) -> None:
    """Test OpenAPI generation with external relative references."""
    with pytest.warns(FutureWarning, match=r"outside the input base path"):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "external_relative_ref" / "model_b",
            output_path=tmp_path,
            expected_directory=EXPECTED_OPENAPI_PATH / "external_relative_ref",
        )


def test_paths_external_ref(output_file: Path) -> None:
    """Test OpenAPI generation with external refs in paths without components/schemas."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "paths_external_ref" / "openapi.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="paths_external_ref.py",
        extra_args=["--openapi-scopes", "paths"],
    )


def test_paths_ref_with_external_schema(output_file: Path) -> None:
    """Test OpenAPI generation with $ref to external path file containing relative schema refs."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "paths_ref_with_external_schema" / "openapi.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="paths_ref_with_external_schema.py",
        extra_args=["--openapi-scopes", "schemas", "paths"],
    )


@LEGACY_BLACK_SKIP
@pytest.mark.benchmark
@pytest.mark.cli_doc(
    options=["--collapse-root-models"],
    option_description="""Inline root model definitions into their referencing locations.

The `--collapse-root-models` flag collapses root model definitions by
inlining their types directly where they are referenced, reducing the
number of generated classes.""",
    input_schema="openapi/not_real_string.json",
    cli_args=["--collapse-root-models"],
    golden_output="openapi/not_real_string_collapse_root_models.py",
)
def test_main_collapse_root_models(output_file: Path) -> None:
    """Inline root model definitions into their referencing locations.

    The `--collapse-root-models` flag collapses root model definitions by
    inlining their types directly where they are referenced, reducing the
    number of generated classes.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "not_real_string.json",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        extra_args=["--collapse-root-models"],
    )


def test_main_collapse_root_models_field_constraints(output_file: Path) -> None:
    """Test OpenAPI generation with collapsed root models and field constraints."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "not_real_string.json",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        extra_args=["--collapse-root-models", "--field-constraints"],
    )


def test_main_collapse_root_models_with_references_to_flat_types(output_file: Path) -> None:
    """Test OpenAPI generation with collapsed root models referencing flat types."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "flat_type.jsonschema",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        extra_args=["--collapse-root-models"],
    )


def test_main_openapi_max_items_enum(output_file: Path) -> None:
    """Test OpenAPI generation with max items enum."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "max_items_enum.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="max_items_enum.py",
    )


def test_main_openapi_const(output_file: Path) -> None:
    """Test OpenAPI generation with const values."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "const.json",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="const_pydantic_v2.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )


def test_main_openapi_const_optional_values(output_file: Path) -> None:
    """Do not treat optional non-literal const values as schema defaults."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "const_optional_values_31.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="const_optional_values_31.py",
    )


@pytest.mark.parametrize(
    ("output_model", "expected_output"),
    [
        (
            "pydantic_v2.BaseModel",
            "const_field_pydantic_v2.py",
        ),
        (
            "msgspec.Struct",
            "const_field_msgspec.py",
        ),
        (
            "typing.TypedDict",
            "const_field_typed_dict.py",
        ),
        (
            "dataclasses.dataclass",
            "const_field_dataclass.py",
        ),
    ],
)
@pytest.mark.cli_doc(
    options=["--collapse-root-models"],
    option_description="""Inline root model definitions instead of creating separate wrapper classes.

The `--collapse-root-models` option generates simpler output by inlining root models
directly instead of creating separate wrapper types. This shows how different output
model types (Pydantic v2, dataclass, TypedDict, msgspec) handle const fields.""",
    input_schema="openapi/const.yaml",
    cli_args=["--collapse-root-models"],
    model_outputs={
        "pydantic_v2": "openapi/const_field_pydantic_v2.py",
        "msgspec": "openapi/const_field_msgspec.py",
        "typeddict": "openapi/const_field_typed_dict.py",
        "dataclass": "openapi/const_field_dataclass.py",
    },
    comparison_output="openapi/const_baseline.py",
    primary=True,
)
def test_main_openapi_const_field(output_model: str, expected_output: str, output_file: Path) -> None:
    """Inline root model definitions instead of creating separate wrapper classes.

    The `--collapse-root-models` option generates simpler output by inlining root models
    directly instead of creating separate wrapper types. This shows how different output
    model types (Pydantic v2, dataclass, TypedDict, msgspec) handle const fields.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "const.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=expected_output,
        extra_args=["--output-model-type", output_model, "--collapse-root-models"],
    )


def test_main_openapi_complex_reference(output_file: Path) -> None:
    """Test OpenAPI generation with complex references."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "complex_reference.json",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="complex_reference.py",
    )


def test_main_openapi_reference_to_object_properties(output_file: Path) -> None:
    """Test OpenAPI generation with reference to object properties."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "reference_to_object_properties.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="reference_to_object_properties.py",
    )


def test_main_openapi_reference_to_object_properties_collapse_root_models(output_file: Path) -> None:
    """Test OpenAPI generation with reference to object properties and collapsed root models."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "reference_to_object_properties.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="reference_to_object_properties_collapse_root_models.py",
        extra_args=["--collapse-root-models"],
    )


def test_main_openapi_override_required_all_of_field(output_file: Path) -> None:
    """Test OpenAPI generation with override required allOf field."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "override_required_all_of.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="override_required_all_of.py",
        extra_args=["--collapse-root-models"],
    )


def test_main_openapi_allof_with_required_inherited_fields(output_file: Path) -> None:
    """Test OpenAPI generation with allOf where required includes inherited fields."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_with_required_inherited_fields.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="allof_with_required_inherited_fields.py",
    )


def test_main_openapi_allof_with_required_inherited_fields_force_optional(output_file: Path) -> None:
    """Test OpenAPI generation with allOf and --force-optional flag."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_with_required_inherited_fields.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="allof_with_required_inherited_fields_force_optional.py",
        extra_args=["--force-optional"],
    )


def test_main_openapi_allof_with_required_inherited_nested_object(output_file: Path) -> None:
    """Test OpenAPI generation with allOf where required includes inherited nested object fields."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_with_required_inherited_nested_object.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="allof_with_required_inherited_nested_object.py",
    )


def test_main_openapi_allof_with_required_inherited_complex_allof(output_file: Path) -> None:
    """Test OpenAPI generation with allOf where required includes complex allOf fields."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_with_required_inherited_complex_allof.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="allof_with_required_inherited_complex_allof.py",
        force_exec_validation=True,
    )
    assert_generated_model_json_invalid(
        output_file,
        module_name="allof_required_inherited_complex_mapping",
        model_name="Item",
        invalid_json='{"id":1,"code":"ok","score":50,"config":{},"metadata":{"key":""}}',
        expected_error_type="string_too_short",
    )


def test_main_openapi_allof_with_required_inherited_comprehensive(output_file: Path) -> None:
    """Test OpenAPI generation with allOf covering all type inheritance scenarios."""
    expected_file = (
        "allof_with_required_inherited_comprehensive_black_lt_24.py"
        if version.parse(black.__version__) < version.parse("24.0.0")
        else "allof_with_required_inherited_comprehensive.py"
    )
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_with_required_inherited_comprehensive.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=expected_file,
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
        pytest.param(
            DataModelType.DataclassesDataclass.value,
            "dataclasses_dataclass_keyword_only",
            id="dataclass-keyword-only",
        ),
        pytest.param(
            DataModelType.PydanticV2Dataclass.value,
            "pydantic_v2_dataclass_keyword_only",
            id="pydantic-v2-dataclass-keyword-only",
        ),
    ],
)
def test_main_openapi_allof_required_inherited_model_references(
    output_file: Path,
    output_model_type: str,
    expected_name: str,
) -> None:
    """Preserve generated model references for required inherited fields across output backends."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_required_inherited_model_references.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=f"output_model_types/allof_required_inherited_model_references_{expected_name}.py",
        extra_args=[
            *BACKEND_GOLDEN_TARGET_ARGS,
            "--formatters",
            "builtin",
            "--output-model-type",
            output_model_type,
            "--snake-case-field",
            "--use-default",
            "--default-values",
            str(DEFAULT_VALUES_DATA_PATH / "allof_required_inherited_model_references.json"),
            *(["--keyword-only"] if expected_name.endswith("_keyword_only") else []),
        ],
        force_exec_validation=True,
    )

    match model_type := DataModelType(output_model_type):
        case DataModelType.DataclassesDataclass:
            valid_payload = {
                "contact_details": {"name": "Ada"},
                "events": [{"latitude": 45}],
                "packages": [{"label_id": "label-1", "alternative_identifiers": ["alt-1"]}],
                "package": {"sku": "sku-1"},
                "tracking_code": "track-1",
                "pickup_window": {"start_at": 1, "end_at": 2},
                "fallback_field": True,
            }
        case DataModelType.PydanticV2BaseModel | DataModelType.PydanticV2Dataclass:
            valid_payload = {
                "contactDetails": {"name": "Ada"},
                "events": [{"latitude": 45}],
                "packages": [{"labelId": "label-1", "alternativeIdentifiers": ["alt-1"]}],
                "package": {"sku": "sku-1"},
                "trackingCode": "track-1",
                "pickupWindow": {"startAt": 1, "endAt": 2},
                "fallbackField": True,
            }
        case _:
            return

    for required_field in valid_payload:
        assert_generated_model_json_invalid(
            output_file,
            module_name=f"allof_required_inherited_{expected_name}_{required_field}",
            model_name="ScheduledPickup",
            invalid_json=json.dumps({
                field_name: value for field_name, value in valid_payload.items() if field_name != required_field
            }),
            expected_error_type="missing",
        )
    if model_type is DataModelType.DataclassesDataclass:
        return

    assert_generated_model_json_validation(
        output_file,
        module_name="allof_required_inherited_contact",
        model_name="ScheduledPickup",
        valid_json=json.dumps(valid_payload),
        invalid_json=json.dumps({**valid_payload, "contactDetails": {"name": ""}}),
        expected_error_type="string_too_short",
        expected_attribute_path=("mode",),
        expected_attribute_value="scheduled",
    )
    assert_generated_model_json_invalid(
        output_file,
        module_name="allof_required_inherited_event",
        model_name="ScheduledPickup",
        invalid_json=json.dumps({**valid_payload, "events": [{"latitude": 91}]}),
        expected_error_type="less_than_equal",
    )
    assert_generated_model_json_invalid(
        output_file,
        module_name="allof_required_inherited_package_extension",
        model_name="ScheduledPickup",
        invalid_json=json.dumps({**valid_payload, "packages": [{"labelId": "label-1"}]}),
        expected_error_type="missing",
    )
    assert_generated_model_json_invalid(
        output_file,
        module_name="allof_required_inherited_direct_reference",
        model_name="ScheduledPickup",
        invalid_json=json.dumps({**valid_payload, "package": {"sku": ""}}),
        expected_error_type="string_too_short",
    )
    assert_generated_model_json_invalid(
        output_file,
        module_name="allof_required_inherited_pickup_window",
        model_name="ScheduledPickup",
        invalid_json=json.dumps({**valid_payload, "pickupWindow": {"startAt": "bad", "endAt": 2}}),
        expected_error_type="int_parsing",
    )
    forward_payload = {
        "detail": {"code": "ready"},
        "forwardEvents": [{"latitude": 45}],
        "forwardWindow": {"startAt": 1},
    }
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_required_inherited_forward_declared",
        model_name="ForwardDeclaredPickup",
        valid_json=json.dumps(forward_payload),
        invalid_json=json.dumps({**forward_payload, "detail": {"code": ""}}),
        expected_error_type="string_too_short",
        expected_attribute_path=("detail", "code"),
        expected_attribute_value="ready",
    )
    for model_name in ("ForwardDeclaredPickup", "ForwardInlineRequiredPickup"):
        for required_field in forward_payload:
            assert_generated_model_json_invalid(
                output_file,
                module_name=f"allof_required_inherited_{expected_name}_{model_name}_{required_field}",
                model_name=model_name,
                invalid_json=json.dumps({
                    field_name: value for field_name, value in forward_payload.items() if field_name != required_field
                }),
                expected_error_type="missing",
            )
    assert_generated_model_json_invalid(
        output_file,
        module_name="allof_required_inherited_forward_inline",
        model_name="ForwardInlineRequiredPickup",
        invalid_json=json.dumps({**forward_payload, "forwardEvents": [{"latitude": 91}]}),
        expected_error_type="less_than_equal",
    )
    assert_generated_model_json_invalid(
        output_file,
        module_name="allof_required_inherited_forward_partial",
        model_name="ForwardPartialPickup",
        invalid_json=json.dumps({**forward_payload, "detail": {"code": ""}}),
        expected_error_type="string_too_short",
    )
    reverse_payload = {
        "contactDetails": {"name": "Ada"},
        "events": [{"latitude": 45}],
        "packages": [{"labelId": "label-1", "alternativeIdentifiers": ["alt-1"]}],
    }
    for required_field in reverse_payload:
        assert_generated_model_json_invalid(
            output_file,
            module_name=f"allof_required_inherited_{expected_name}_reverse_{required_field}",
            model_name="ReverseScheduledPickup",
            invalid_json=json.dumps({
                field_name: value for field_name, value in reverse_payload.items() if field_name != required_field
            }),
            expected_error_type="missing",
        )


@pytest.mark.parametrize(
    ("output_model_type", "expected_name", "additional_args"),
    [
        *(
            pytest.param(*backend_case.values, (), id=backend_case.id, marks=backend_case.marks)
            for backend_case in BACKEND_GOLDEN_CASES
        ),
        pytest.param(
            DataModelType.PydanticV2Dataclass.value,
            "pydantic_v2_dataclass",
            (),
            id="pydantic-v2-dataclass",
        ),
        pytest.param(
            DataModelType.PydanticV2Dataclass.value,
            "pydantic_v2_dataclass_annotated",
            ("--use-annotated", "--field-constraints"),
            id="pydantic-v2-dataclass-annotated",
        ),
        pytest.param(
            DataModelType.PydanticV2Dataclass.value,
            "pydantic_v2_dataclass_annotated",
            (
                "--use-annotated",
                "--field-constraints",
                "--custom-template-dir",
                str(DATA_PATH / "templates_pydantic_v2_dataclass_legacy"),
            ),
            id="pydantic-v2-dataclass-annotated-legacy-template",
        ),
    ],
)
def test_main_openapi_allof_required_inherited_dataclass_metadata(
    output_file: Path,
    output_model_type: str,
    expected_name: str,
    additional_args: tuple[str, ...],
) -> None:
    """Preserve explicit field metadata and exact dataclass ordering across required overrides."""
    expected_output_name = (
        f"{expected_name}_alias_fallback"
        if output_model_type == DataModelType.PydanticV2Dataclass.value and PYDANTIC_V2_DATACLASS_ALIAS_NEEDS_FALLBACK
        else expected_name
    )
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_required_inherited_dataclass_metadata.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=f"output_model_types/allof_required_inherited_dataclass_metadata_{expected_output_name}.py",
        extra_args=[
            *BACKEND_GOLDEN_TARGET_ARGS,
            "--formatters",
            "builtin",
            "--output-model-type",
            output_model_type,
            "--field-extra-keys",
            "init",
            "default_factory",
            "repr",
            "kw_only",
            *additional_args,
            "--disable-timestamp",
        ],
        force_exec_validation=True,
    )

    match model_type := DataModelType(output_model_type):
        case DataModelType.PydanticV2BaseModel | DataModelType.PydanticV2Dataclass | DataModelType.DataclassesDataclass:
            pass
        case DataModelType.TypingTypedDict if sys.version_info >= (3, 12):
            assert_generated_model_json_validation(
                output_file,
                module_name="allof_required_dataclass_metadata_typed_dict",
                model_name="InheritedInitDefaultChild",
                valid_json='{"newAfterInheritedInit":1}',
                invalid_json="{}",
                expected_error_type="missing",
                expected_attribute_path=("newAfterInheritedInit",),
                expected_attribute_value=1,
            )
            return
        case DataModelType.TypingTypedDict:
            return
        case _:
            return

    base_invalid_json, base_error_type = (
        ("[]", "dataclass_type")
        if model_type is DataModelType.DataclassesDataclass
        else ('{"inheritedFactory":[1]}', "string_type")
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"allof_required_dataclass_metadata_{expected_name}_base_defaults",
        model_name="InitBase",
        valid_json="{}",
        invalid_json=base_invalid_json,
        expected_error_type=base_error_type,
        expected_attribute_path=("inheritedMetadata",),
        expected_attribute_value=None,
        expected_repr=(
            "InitBase(inheritedScalar='inherited', inheritedFactory=[], inheritedMetadata=None)"
            if model_type is DataModelType.DataclassesDataclass
            else None
        ),
    )

    required_override_payload = {
        "inheritedScalar": "fresh",
        "inheritedFactory": ["item"],
        "inheritedMetadata": "metadata",
        "newRequired": 1,
    }
    for required_field in required_override_payload:
        assert_generated_model_json_invalid(
            output_file,
            module_name=f"allof_required_dataclass_metadata_{expected_name}_{required_field}",
            model_name="RequiredOverrideChild",
            invalid_json=json.dumps({
                field_name: value
                for field_name, value in required_override_payload.items()
                if field_name != required_field
            }),
            expected_error_type="missing",
        )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"allof_required_dataclass_metadata_{expected_name}_override",
        model_name="RequiredOverrideChild",
        valid_json=json.dumps(required_override_payload),
        invalid_json=json.dumps({**required_override_payload, "inheritedFactory": [1]}),
        expected_error_type="string_type",
        expected_attribute_path=("inheritedScalar",),
        expected_attribute_value="fresh",
    )

    literal_default_payload = {
        "literalDefault": "fresh",
        "afterLiteral": 1,
    }
    for required_field in literal_default_payload:
        assert_generated_model_json_invalid(
            output_file,
            module_name=f"allof_required_dataclass_metadata_{expected_name}_literal_{required_field}",
            model_name="LiteralDefaultChild",
            invalid_json=json.dumps({
                field_name: value
                for field_name, value in literal_default_payload.items()
                if field_name != required_field
            }),
            expected_error_type="missing",
        )
    if additional_args:
        assert_generated_model_json_invalid(
            output_file,
            module_name="allof_required_dataclass_metadata_annotated_constraint",
            model_name="RequiredOverrideChild",
            invalid_json=json.dumps({**required_override_payload, "inheritedScalar": "x"}),
            expected_error_type="string_too_short",
        )

    alias_field = "aliased_value" if model_type is DataModelType.DataclassesDataclass else "aliased-value"
    alias_payload = {alias_field: "alias", "newAfterAlias": 1}
    for required_field in alias_payload:
        assert_generated_model_json_invalid(
            output_file,
            module_name=f"allof_required_dataclass_alias_{expected_name}_{required_field}",
            model_name="RequiredAliasChild",
            invalid_json=json.dumps({
                field_name: value for field_name, value in alias_payload.items() if field_name != required_field
            }),
            expected_error_type="missing",
        )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"allof_required_dataclass_alias_{expected_name}_valid",
        model_name="RequiredAliasChild",
        valid_json=json.dumps(alias_payload),
        invalid_json=json.dumps({**alias_payload, "newAfterAlias": "bad"}),
        expected_error_type="int_parsing",
        expected_attribute_path=("newAfterAlias",),
        expected_attribute_value=1,
    )

    metadata_payload = {"requiredMetadata": "metadata", "newAfterMetadata": 1}
    for required_field in metadata_payload:
        assert_generated_model_json_invalid(
            output_file,
            module_name=f"allof_required_dataclass_structural_{expected_name}_{required_field}",
            model_name="RequiredMetadataChild",
            invalid_json=json.dumps({
                field_name: value for field_name, value in metadata_payload.items() if field_name != required_field
            }),
            expected_error_type="missing",
        )

    assert_generated_model_json_validation(
        output_file,
        module_name=f"allof_required_dataclass_keyword_override_{expected_name}",
        model_name="KeywordOverrideChild",
        valid_json='{"positionalOverride":"fresh"}',
        invalid_json="{}",
        expected_error_type="missing",
        expected_attribute_path=("positionalOverride",),
        expected_attribute_value="fresh",
        expected_keyword_only_fields=(
            {"positionalOverride"}
            if model_type in {DataModelType.PydanticV2Dataclass, DataModelType.DataclassesDataclass}
            else None
        ),
    )

    assert_generated_model_json_validation(
        output_file,
        module_name=f"allof_required_dataclass_explicit_init_{expected_name}",
        model_name="ExplicitInitChild",
        valid_json='{"explicitInit":"fresh","newAfterExplicitInit":1}',
        invalid_json='{"explicitInit":"fresh"}',
        expected_error_type="missing",
        expected_attribute_path=("newAfterExplicitInit",),
        expected_attribute_value=1,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"allof_required_dataclass_inherited_init_{expected_name}",
        model_name="InheritedInitDefaultChild",
        valid_json='{"newAfterInheritedInit":1}',
        invalid_json="{}",
        expected_error_type="missing",
        expected_attribute_path=("inheritedMetadata",),
        expected_attribute_value=None,
        expected_repr=(
            "InheritedInitDefaultChild(inheritedScalar='inherited', inheritedFactory=[], "
            "inheritedMetadata=None, newAfterInheritedInit=1)"
            if model_type is DataModelType.DataclassesDataclass
            else None
        ),
    )

    ordering_payload = {
        "earlyFactory": ["early"],
        "lateFactory": ["late"],
        "newRequired": 1,
    }
    for required_field in ordering_payload:
        assert_generated_model_json_invalid(
            output_file,
            module_name=f"allof_required_dataclass_ordering_{expected_name}_{required_field}",
            model_name="OrderingChild",
            invalid_json=json.dumps({
                field_name: value for field_name, value in ordering_payload.items() if field_name != required_field
            }),
            expected_error_type="missing",
        )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"allof_required_dataclass_ordering_{expected_name}_valid",
        model_name="OrderingChild",
        valid_json=json.dumps(ordering_payload),
        invalid_json=json.dumps({**ordering_payload, "newRequired": "bad"}),
        expected_error_type="int_parsing",
        expected_attribute_path=("earlyFactory",),
        expected_attribute_value=["early"],
        expected_keyword_only_fields=(
            {"lateFactory", "newRequired"}
            if model_type in {DataModelType.PydanticV2Dataclass, DataModelType.DataclassesDataclass}
            else None
        ),
    )


def test_main_openapi_allof_required_inherited_metadata_literal(output_file: Path) -> None:
    """Do not interpret dataclass metadata text as a constructor default."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_required_inherited_metadata_literal.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="output_model_types/allof_required_inherited_metadata_literal_dataclasses_dataclass.py",
        extra_args=[
            *BACKEND_GOLDEN_TARGET_ARGS,
            "--formatters",
            "builtin",
            "--output-model-type",
            DataModelType.DataclassesDataclass.value,
            "--field-extra-keys",
            "metadata",
            "--strip-default-none",
            "--disable-timestamp",
        ],
        force_exec_validation=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_required_inherited_metadata_literal_dataclasses_dataclass",
        model_name="MetadataLiteralChild",
        valid_json='{"metadataOnly": null, "newRequired": 1}',
        invalid_json="[]",
        expected_error_type="dataclass_type",
        expected_attribute_path=("mutableWithMetadata",),
        expected_attribute_value=[],
        expected_keyword_only_fields={"newRequired"},
    )


def test_main_openapi_dataclass_optional_metadata_defaults(output_file: Path) -> None:
    """Preserve optional None and computed factory defaults behind dataclass field metadata."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "dataclass_optional_metadata_defaults.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="dataclass_optional_metadata_defaults.py",
        extra_args=[
            *BACKEND_GOLDEN_TARGET_ARGS,
            "--formatters",
            "builtin",
            "--output-model-type",
            DataModelType.DataclassesDataclass.value,
            "--field-extra-keys",
            "init",
            "repr",
            "kw_only",
            "--use-default-factory-for-optional-nested-models",
            "--disable-timestamp",
        ],
        force_exec_validation=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="dataclass_optional_metadata_defaults",
        model_name="Model",
        valid_json="{}",
        invalid_json="[]",
        expected_error_type="dataclass_type",
        expected_attribute_path=("nested", "value"),
        expected_attribute_value=None,
        expected_keyword_only_fields={"keywordOnly"},
        expected_repr="Model(keywordOnly=None, nested=Nested(value=None))",
    )


def test_main_openapi_allof_required_inherited_model_references_force_optional(output_file: Path) -> None:
    """Keep inherited model types while forcing every required field to remain optional."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_required_inherited_model_references.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="allof_required_inherited_model_references_force_optional.py",
        extra_args=[
            "--output-model-type",
            DataModelType.PydanticV2BaseModel.value,
            "--target-python-version",
            "3.11",
            "--formatters",
            "builtin",
            "--snake-case-field",
            "--use-default",
            "--force-optional",
        ],
        force_exec_validation=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_required_inherited_force_optional",
        model_name="ScheduledPickup",
        valid_json="{}",
        invalid_json='{"contactDetails":{"name":""}}',
        expected_error_type="string_too_short",
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_required_inherited_force_optional_forward",
        model_name="ForwardDeclaredPickup",
        valid_json="{}",
        invalid_json='{"detail":{"code":""}}',
        expected_error_type="string_too_short",
    )


@pytest.mark.parametrize(
    "allof_merge_mode",
    [
        pytest.param(None, id="constraints"),
        pytest.param("all", id="all"),
    ],
)
def test_main_openapi_allof_required_inherited_options(
    output_file: Path,
    allof_merge_mode: str | None,
) -> None:
    """Keep class-scoped field options stable regardless of component order."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_required_inherited_options.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="allof_required_inherited_options.py",
        extra_args=[
            "--output-model-type",
            DataModelType.PydanticV2BaseModel.value,
            "--target-python-version",
            "3.11",
            "--formatters",
            "builtin",
            "--use-default",
            "--allof-class-hierarchy",
            "always",
            "--aliases",
            str(ALIASES_DATA_PATH / "allof_required_inherited_options.json"),
            "--serialization-aliases",
            str(ALIASES_DATA_PATH / "allof_required_inherited_serialization_options.json"),
            "--default-values",
            str(DEFAULT_VALUES_DATA_PATH / "allof_required_inherited_options.json"),
            *([] if allof_merge_mode is None else ["--allof-merge-mode", allof_merge_mode]),
        ],
        force_exec_validation=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_required_inherited_options_base_first",
        model_name="DerivedBaseFirst",
        valid_json='{"x-field":"ready"}',
        invalid_json='{"x-field":""}',
        expected_error_type="string_too_short",
        expected_attribute_path=("mode",),
        expected_attribute_value="schema",
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_required_inherited_options_base_first_scoped",
        model_name="ScopedDerivedBaseFirst",
        valid_json='{"x-field":"ready"}',
        invalid_json='{"x-field":""}',
        expected_error_type="string_too_short",
        expected_attribute_path=("mode",),
        expected_attribute_value="derived",
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_required_inherited_options_forward",
        model_name="ForwardDerived",
        valid_json='{"x-field":"ready"}',
        invalid_json='{"x-field":""}',
        expected_error_type="string_too_short",
        expected_attribute_path=("forward_base_name",),
        expected_attribute_value="ready",
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_required_inherited_options_forward_scoped",
        model_name="ForwardScopedDerived",
        valid_json='{"x-field":"ready"}',
        invalid_json='{"x-field":""}',
        expected_error_type="string_too_short",
        expected_attribute_path=("mode",),
        expected_attribute_value="forward-derived",
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_required_inherited_options_collision",
        model_name="CollisionDerived",
        valid_json='{"x_y":1,"x-y":"ready"}',
        invalid_json='{"x_y":1,"x-y":""}',
        expected_error_type="string_too_short",
        expected_attribute_path=("x_y_1",),
        expected_attribute_value="ready",
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_required_inherited_options_forward_collision",
        model_name="ForwardCollisionDerived",
        valid_json='{"x_y":1,"x-y":"ready"}',
        invalid_json='{"x_y":1,"x-y":""}',
        expected_error_type="string_too_short",
        expected_attribute_path=("x_y_1",),
        expected_attribute_value="ready",
    )
    for model_name in ("C3Derived", "C3DirectDerived"):
        assert_generated_model_json_validation(
            output_file,
            module_name=f"allof_required_inherited_options_{model_name}",
            model_name=model_name,
            valid_json='{"value":1}',
            invalid_json='{"value":0}',
            expected_error_type="greater_than_equal",
            expected_attribute_path=("value",),
            expected_attribute_value=1,
        )
    partial_payload = {
        "inline": {"code": "ok"},
        "mapping": {"item": {"code": "ok"}},
        "unionValue": {"code": "ok"},
        "booleanUnionValue": {"code": "ok"},
        "text": "abz",
        "number": 6,
    }
    for model_name in ("PartialContainerDerived", "ForwardPartialContainerDerived"):
        assert_generated_model_json_validation(
            output_file,
            module_name=f"allof_required_inherited_options_{model_name}",
            model_name=model_name,
            valid_json=json.dumps(partial_payload),
            invalid_json=json.dumps({**partial_payload, "mapping": {"item": {"code": "x"}}}),
            expected_error_type="string_too_short",
            expected_attribute_path=("unionValue", "code"),
            expected_attribute_value="ok",
        )
    assert_generated_model_json_invalid(
        output_file,
        module_name="allof_required_inherited_options_partial_pattern",
        model_name="PartialContainerDerived",
        invalid_json=json.dumps({**partial_payload, "text": "abx"}),
        expected_error_type="string_pattern_mismatch",
    )
    assert_generated_model_json_invalid(
        output_file,
        module_name="allof_required_inherited_options_partial_boolean_schema",
        model_name="PartialContainerDerived",
        invalid_json=json.dumps({**partial_payload, "booleanUnionValue": {"code": "x"}}),
        expected_error_type="string_too_short",
    )
    assert_generated_model_json_invalid(
        output_file,
        module_name="allof_required_inherited_options_partial_max_length",
        model_name="PartialContainerDerived",
        invalid_json=json.dumps({**partial_payload, "text": "abcdefz"}),
        expected_error_type="string_too_long",
    )
    assert_generated_model_json_invalid(
        output_file,
        module_name="allof_required_inherited_options_partial_multiple",
        model_name="PartialContainerDerived",
        invalid_json=json.dumps({**partial_payload, "number": 4}),
        expected_error_type="multiple_of",
    )
    assert_generated_model_json_invalid(
        output_file,
        module_name="allof_required_inherited_options_partial_required",
        model_name="PartialContainerDerived",
        invalid_json=json.dumps({key: value for key, value in partial_payload.items() if key != "inline"}),
        expected_error_type="missing",
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_required_inherited_options_c3_stable_order",
        model_name="C3StableOrderDerived",
        valid_json='{"orderedValue":true}',
        invalid_json='{"orderedValue":2}',
        expected_error_type="bool_parsing",
        expected_attribute_path=("orderedValue",),
        expected_attribute_value=True,
    )


def test_main_openapi_allof_required_inherited_collision_msgspec(output_file: Path) -> None:
    """Keep inherited wire aliases distinct after resolving colliding Python field names."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_required_inherited_collision.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="allof_required_inherited_collision_msgspec.py",
        extra_args=[
            "--output-model-type",
            DataModelType.MsgspecStruct.value,
            "--target-python-version",
            "3.11",
            "--allof-class-hierarchy",
            "always",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ],
        force_exec_validation=True,
    )


@pytest.mark.parametrize(
    ("read_write_mode", "expected_file"),
    [
        pytest.param("all", "allof_required_inherited_rw_c3.py", id="all"),
        pytest.param(
            "request-response",
            "allof_required_inherited_rw_c3_request_response.py",
            id="request-response",
        ),
    ],
)
def test_main_openapi_allof_required_inherited_rw_c3(
    output_file: Path,
    read_write_mode: str,
    expected_file: str,
) -> None:
    """Flatten Request/Response fields with C3 winners and declaration-owner metadata."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_required_inherited_rw_c3.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=expected_file,
        extra_args=[
            "--output-model-type",
            DataModelType.PydanticV2BaseModel.value,
            "--target-python-version",
            "3.11",
            "--formatters",
            "builtin",
            "--allof-class-hierarchy",
            "always",
            "--read-only-write-only-model-type",
            read_write_mode,
            "--use-title-as-name",
            "--aliases",
            str(ALIASES_DATA_PATH / "allof_required_inherited_rw_c3.json"),
            "--serialization-aliases",
            str(ALIASES_DATA_PATH / "allof_required_inherited_rw_c3_serialization.json"),
            "--use-serialization-alias",
        ],
        force_exec_validation=True,
    )
    for model_name, payload in (
        ("RwC3CombinedRequest", '{"c3Value":"ok","requestOnly":"request"}'),
        ("RwC3CombinedResponse", '{"c3Value":"ok","responseOnly":"response"}'),
    ):
        assert_generated_model_json_validation(
            output_file,
            module_name=f"allof_required_inherited_rw_c3_{model_name}",
            model_name=model_name,
            valid_json=payload,
            invalid_json='{"c3Value":"x"}',
            expected_error_type="string_too_short",
            expected_attribute_path=("c3Value",),
            expected_attribute_value="ok",
        )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"allof_required_inherited_rw_c3_reversed_{read_write_mode}",
        model_name="RwC3ReversedRequest",
        valid_json='{"c3Value":2}',
        invalid_json='{"c3Value":"bad"}',
        expected_error_type="int_parsing",
        expected_attribute_path=("c3Value",),
        expected_attribute_value=2,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"allof_required_inherited_rw_c3_diamond_{read_write_mode}",
        model_name="DiamondDerivedRequest",
        valid_json='{"diamondValue":2}',
        invalid_json='{"diamondValue":"bad"}',
        expected_error_type="int_parsing",
        expected_attribute_path=("diamondValue",),
        expected_attribute_value=2,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"allof_required_inherited_rw_required_only_{read_write_mode}",
        model_name="RequiredOnlyDerivedRequest",
        valid_json='{"legacyValue":"ok"}',
        invalid_json='{"legacyValue":"x"}',
        expected_error_type="string_too_short",
        expected_attribute_path=("legacyValue",),
        expected_attribute_value="ok",
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"allof_required_inherited_rw_title_{read_write_mode}",
        model_name="TitleDerivedRequest",
        valid_json='{"renamedValue":"ok"}',
        invalid_json='{"renamedValue":"x"}',
        expected_error_type="string_too_short",
        expected_attribute_path=("renamedValue",),
        expected_attribute_value="ok",
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"allof_required_inherited_rw_chain_{read_write_mode}",
        model_name="ChainDerivedRequest",
        valid_json='{"detail":{"code":"ok"}}',
        invalid_json='{"detail":{"code":"x"}}',
        expected_error_type="string_too_short",
        expected_attribute_path=("detail", "code"),
        expected_attribute_value="ok",
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"allof_required_inherited_rw_mixed_{read_write_mode}",
        model_name="MixedGeneratedForwardDerivedRequest",
        valid_json='{"mixedValue":2}',
        invalid_json='{"mixedValue":0}',
        expected_error_type="greater_than_equal",
        expected_attribute_path=("mixedValue",),
        expected_attribute_value=2,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"allof_required_inherited_rw_boolean_{read_write_mode}",
        model_name="BooleanDerivedRequest",
        valid_json='{"acceptsAnything":{"nested":true}}',
        invalid_json="{}",
        expected_error_type="missing",
    )


@pytest.mark.parametrize("merge_mode", ["constraints", "all", "none"])
@pytest.mark.parametrize(
    ("read_write_mode", "force_optional", "expected_file"),
    [
        pytest.param(
            "all",
            False,
            "allof_required_inherited_nested_inline_all.py",
            id="all",
        ),
        pytest.param(
            "request-response",
            False,
            "allof_required_inherited_nested_inline_request_response.py",
            id="request-response",
        ),
        pytest.param(
            "all",
            True,
            "allof_required_inherited_nested_inline_all_force_optional.py",
            id="all-force-optional",
        ),
        pytest.param(
            "request-response",
            True,
            "allof_required_inherited_nested_inline_request_response_force_optional.py",
            id="request-response-force-optional",
        ),
    ],
)
def test_main_openapi_allof_required_inherited_nested_inline(
    output_file: Path,
    merge_mode: str,
    read_write_mode: str,
    force_optional: bool,
    expected_file: str,
) -> None:
    """Resolve nested inline allOf parents before parsing sibling partial properties."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_required_inherited_nested_inline.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=expected_file,
        extra_args=[
            "--output-model-type",
            DataModelType.PydanticV2BaseModel.value,
            "--target-python-version",
            "3.11",
            "--formatters",
            "builtin",
            "--allof-class-hierarchy",
            "always",
            "--allof-merge-mode",
            merge_mode,
            "--read-only-write-only-model-type",
            read_write_mode,
            *(["--force-optional"] if force_optional else []),
        ],
        force_exec_validation=True,
    )
    for model_name in (
        "BaseFirstDerivedRequest",
        "BaseFirstGrandchildRequest",
        "ForwardDerivedRequest",
        "ForwardGrandchildRequest",
        "DeepDerivedRequest",
        "DiamondDerivedRequest",
    ):
        assert_generated_model_json_validation(
            output_file,
            module_name=(
                f"allof_required_inherited_nested_inline_{read_write_mode}_{force_optional}_{merge_mode}_{model_name}"
            ),
            model_name=model_name,
            valid_json='{"detail":{"id":1}}',
            invalid_json='{"detail":{"id":"bad"}}',
            expected_error_type="int_parsing",
            expected_attribute_path=("detail", "id"),
            expected_attribute_value=1,
        )


def test_main_openapi_allof_required_inherited_rw_cycle(output_file: Path) -> None:
    """Terminate cyclic raw inheritance while retaining each declared request field."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_required_inherited_rw_cycle.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="allof_required_inherited_rw_cycle.py",
        extra_args=[
            "--output-model-type",
            DataModelType.PydanticV2BaseModel.value,
            "--target-python-version",
            "3.11",
            "--formatters",
            "builtin",
            "--allof-class-hierarchy",
            "always",
            "--read-only-write-only-model-type",
            "request-response",
        ],
        force_exec_validation=True,
    )
    valid_payload = '{"a":"ok","b":1}'
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_required_inherited_rw_cycle_a",
        model_name="CycleARequest",
        valid_json=valid_payload,
        invalid_json='{"a":"x","b":1}',
        expected_error_type="string_too_short",
        expected_attribute_path=("b",),
        expected_attribute_value=1,
    )
    assert_generated_model_json_invalid(
        output_file,
        module_name="allof_required_inherited_rw_cycle_b",
        model_name="CycleARequest",
        invalid_json='{"a":"ok","b":0}',
        expected_error_type="greater_than_equal",
    )


@pytest.mark.parametrize(
    ("read_write_mode", "expected_file"),
    [
        pytest.param(None, "allof_required_inherited_external.py", id="standard"),
        pytest.param("all", "allof_required_inherited_external_all.py", id="read-write-all"),
        pytest.param(
            "request-response",
            "allof_required_inherited_external_request_response.py",
            id="request-response",
        ),
    ],
)
def test_main_openapi_allof_required_inherited_external(
    output_file: Path,
    read_write_mode: str | None,
    expected_file: str,
) -> None:
    """Resolve inherited partial fields and nested references in their external document context."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_required_inherited_external" / "openapi.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=expected_file,
        extra_args=[
            "--output-model-type",
            DataModelType.PydanticV2BaseModel.value,
            "--target-python-version",
            "3.11",
            "--formatters",
            "builtin",
            *([] if read_write_mode is None else ["--read-only-write-only-model-type", read_write_mode]),
        ],
        force_exec_validation=True,
    )
    valid_payload = {
        "detail": {"code": "ok"},
        "mapping": {"item": {"code": "ok"}},
    }
    assert_generated_model_json_validation(
        output_file,
        module_name=f"allof_required_inherited_external_{read_write_mode or 'standard'}",
        model_name="ExternalDerived",
        valid_json=json.dumps(valid_payload),
        invalid_json=json.dumps({**valid_payload, "mapping": {"item": {"code": "x"}}}),
        expected_error_type="string_too_short",
        expected_attribute_path=("detail", "code"),
        expected_attribute_value="ok",
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"allof_required_inherited_external_nested_{read_write_mode or 'standard'}",
        model_name="ExternalNestedWrapper" if read_write_mode is None else "ExternalNestedWrapperRequest",
        valid_json=json.dumps(valid_payload),
        invalid_json=json.dumps({**valid_payload, "detail": {"code": "x"}}),
        expected_error_type="string_too_short",
        expected_attribute_path=("mapping", "item", "code"),
        expected_attribute_value="ok",
    )
    if read_write_mode == "request-response":
        assert_generated_model_json_validation(
            output_file,
            module_name="allof_required_inherited_external_nested_response",
            model_name="ExternalNestedWrapperResponse",
            valid_json=json.dumps({**valid_payload, "responseOnly": "visible"}),
            invalid_json=json.dumps({**valid_payload, "responseOnly": 1}),
            expected_error_type="string_type",
            expected_attribute_path=("responseOnly",),
            expected_attribute_value="visible",
        )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"allof_required_inherited_external_write_response_{read_write_mode or 'standard'}",
        model_name=("ExternalWriteOnlyWrapper" if read_write_mode is None else "ExternalWriteOnlyWrapperResponse"),
        valid_json=json.dumps(valid_payload),
        invalid_json=json.dumps({**valid_payload, "detail": {"code": "x"}}),
        expected_error_type="string_too_short",
        expected_attribute_path=("mapping", "item", "code"),
        expected_attribute_value="ok",
    )
    if read_write_mode == "request-response":
        assert_generated_model_json_validation(
            output_file,
            module_name="allof_required_inherited_external_write_request",
            model_name="ExternalWriteOnlyWrapperRequest",
            valid_json=json.dumps({**valid_payload, "requestOnly": "secret"}),
            invalid_json=json.dumps({**valid_payload, "requestOnly": 1}),
            expected_error_type="string_type",
            expected_attribute_path=("requestOnly",),
            expected_attribute_value="secret",
        )


@pytest.mark.parametrize(
    "schema_name",
    [
        pytest.param("allof_no_merge_boolean_false_literal", id="literal"),
        pytest.param("allof_no_merge_boolean_false_ref", id="ref"),
        pytest.param("allof_no_merge_boolean_false_nested", id="nested-ref"),
    ],
)
@pytest.mark.parametrize(
    ("field_constraints", "use_annotated"),
    [(False, False), (True, False), (True, True)],
    ids=["standard", "field-constraints", "annotated"],
)
def test_main_openapi_allof_no_merge_boolean_false_schema_errors(
    output_file: Path,
    schema_name: str,
    *,
    field_constraints: bool,
    use_annotated: bool,
) -> None:
    """Reject literal, referenced, and nested false branches in inherited allOf schemas."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / f"{schema_name}.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            "--output-model-type",
            DataModelType.PydanticV2BaseModel.value,
            "--target-python-version",
            "3.11",
            "--formatters",
            "builtin",
            "--allof-merge-mode",
            "none",
            *(["--field-constraints"] if field_constraints else []),
            *(["--use-annotated"] if use_annotated else []),
        ],
        expected_exit=Exit.ERROR,
        output_should_not_exist=True,
    )


@pytest.mark.parametrize(
    ("field_constraints", "use_annotated", "expected_suffix"),
    [
        (False, False, "standard"),
        (True, False, "field_constraints"),
        (True, True, "annotated"),
    ],
    ids=["standard", "field-constraints", "annotated"],
)
def test_main_openapi_allof_no_merge_external_relative_nested_ref(
    output_file: Path,
    expected_suffix: str,
    *,
    field_constraints: bool,
    use_annotated: bool,
) -> None:
    """Resolve nested inherited refs once in the external schema's defining scope."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_no_merge_external_relative" / "openapi.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=f"allof_no_merge_external_relative_nested_ref_{expected_suffix}.py",
        extra_args=[
            "--output-model-type",
            DataModelType.PydanticV2BaseModel.value,
            "--target-python-version",
            "3.11",
            "--formatters",
            "builtin",
            "--allof-merge-mode",
            "none",
            *(["--field-constraints"] if field_constraints else []),
            *(["--use-annotated"] if use_annotated else []),
        ],
        force_exec_validation=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"allof_no_merge_external_relative_nested_ref_{expected_suffix}",
        model_name="ScopedChild",
        valid_json='{"values":["x"]}',
        invalid_json='{"values":[""]}',
        expected_error_type="string_too_short",
    )
    assert_generated_model_json_invalid(
        output_file,
        module_name=f"allof_no_merge_external_relative_nested_ref_type_{expected_suffix}",
        model_name="ScopedChild",
        invalid_json='{"values":[1]}',
        expected_error_type="string_type",
    )


@pytest.mark.parametrize(
    ("field_constraints", "use_annotated", "expected_suffix"),
    [
        (False, False, "standard"),
        (True, False, "field_constraints"),
        (True, True, "annotated"),
    ],
    ids=["standard", "field-constraints", "annotated"],
)
def test_main_openapi_allof_no_merge_recursive_constraints(
    output_file: Path,
    expected_suffix: str,
    *,
    field_constraints: bool,
    use_annotated: bool,
) -> None:
    """Apply finite child override paths through recursive inherited types."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_no_merge_recursive_constraints.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=f"allof_no_merge_recursive_constraints_{expected_suffix}.py",
        extra_args=[
            "--output-model-type",
            DataModelType.PydanticV2BaseModel.value,
            "--target-python-version",
            "3.11",
            "--formatters",
            "builtin",
            "--allof-merge-mode",
            "none",
            *(["--field-constraints"] if field_constraints else []),
            *(["--use-annotated"] if use_annotated else []),
        ],
        force_exec_validation=True,
    )
    valid_payload = {
        "nextValue": {
            "name": "root",
            "count": 1,
            "next": {"name": "ok", "count": 2},
        },
        "childrenValue": {
            "name": "root",
            "count": 1,
            "children": [{"name": "ok", "count": 2}],
        },
        "deepValue": {
            "name": "root",
            "count": 1,
            "next": {
                "name": "middle",
                "count": 2,
                "children": [
                    {
                        "name": "leaf",
                        "count": 3,
                        "next": {"name": "ok", "count": 4},
                    }
                ],
            },
        },
    }
    module_suffix = f"allof_no_merge_recursive_constraints_{expected_suffix}"
    assert_generated_model_json_validation(
        output_file,
        module_name=module_suffix,
        model_name="RecursiveChild",
        valid_json=json.dumps(valid_payload),
        invalid_json=json.dumps({
            **valid_payload,
            "nextValue": {
                **valid_payload["nextValue"],
                "next": {"name": "x", "count": 2},
            },
        }),
        expected_error_type="string_too_short",
    )
    for case_name, field_name, invalid_value, expected_error_type in (
        (
            "children",
            "childrenValue",
            {
                **valid_payload["childrenValue"],
                "children": [{"name": "x", "count": 2}],
            },
            "string_too_short",
        ),
        (
            "deep",
            "deepValue",
            {
                **valid_payload["deepValue"],
                "next": {
                    **valid_payload["deepValue"]["next"],
                    "children": [
                        {
                            "name": "leaf",
                            "count": 3,
                            "next": {"name": "x", "count": 4},
                        }
                    ],
                },
            },
            "string_too_short",
        ),
        (
            "required",
            "nextValue",
            {
                **valid_payload["nextValue"],
                "next": {"name": "ok"},
            },
            "missing",
        ),
    ):
        assert_generated_model_json_invalid(
            output_file,
            module_name=f"{module_suffix}_{case_name}",
            model_name="RecursiveChild",
            invalid_json=json.dumps({**valid_payload, field_name: invalid_value}),
            expected_error_type=expected_error_type,
        )


@pytest.mark.parametrize("merge_mode", ["constraints", "all", "none"])
@pytest.mark.parametrize(
    ("field_constraints", "use_annotated"),
    [(False, False), (True, False), (True, True)],
    ids=["standard", "field-constraints", "annotated"],
)
def test_main_openapi_allof_partial_unconstrained_schemas(
    output_file: Path,
    merge_mode: str,
    *,
    field_constraints: bool,
    use_annotated: bool,
) -> None:
    """Preserve inherited types while honoring child partial-schema precedence."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_partial_unconstrained_schemas.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=(
            "allof_partial_unconstrained_schemas_annotated.py"
            if use_annotated
            else "allof_partial_unconstrained_schemas_field_constraints.py"
            if field_constraints
            else "allof_partial_unconstrained_schemas_standard.py"
        ),
        extra_args=[
            "--output-model-type",
            DataModelType.PydanticV2BaseModel.value,
            "--target-python-version",
            "3.11",
            "--formatters",
            "builtin",
            "--allof-merge-mode",
            merge_mode,
            "--strict-nullable",
            "--use-tuple-for-fixed-items",
            *(["--field-constraints"] if field_constraints else []),
            *(["--use-annotated"] if use_annotated else []),
        ],
        force_exec_validation=True,
    )
    valid_payload = {
        "arrayValue": [{"code": "ok"}],
        "mappingValue": {"item": {"code": "ok"}},
        "anyEmptyValue": {"code": "ok"},
        "anyTrueValue": {"code": "ok"},
        "oneEmptyValue": {"code": "ok"},
        "oneTrueValue": {"code": "ok"},
        "allEmptyValue": {"code": "ok"},
        "allTrueValue": {"code": "ok"},
        "nullableValue": {"code": "ok"},
        "nullableObjectValue": {"code": "ok"},
        "directScalarValue": "a",
        "directScalarInferred": "a",
        "inlineScalarInferred": "a",
        "scalarArrayInferred": ["a"],
        "scalarMappingInferred": {"item": "a"},
        "scalarDeepInferred": [["a"]],
        "scalarArrayRootInferred": ["ab"],
        "scalarMappingRootInferred": {"item": "ab"},
        "arrayNeutralComposition": [{"code": "ok"}],
        "mappingNeutralComposition": {"item": {"code": "ok"}},
        "deepArrayNeutralComposition": [[{"code": "ok"}]],
        "scalarArrayWeaker": ["a"],
        "scalarMappingWeaker": {"item": "a"},
        "scalarDeepWeaker": [["a"]],
        "prefixItemsNeutral": ["ab", 1],
        "legacyItemsNeutral": ["ab", 1],
        "unevaluatedItemsNeutral": ["ab", 1],
        "inlineObjectNeutral": {"code": "ab"},
        "refObjectNeutral": {"code": "ab"},
    }
    constraint_style = "annotated" if use_annotated else "field_constraints" if field_constraints else "standard"
    module_suffix = f"{merge_mode}_{constraint_style}"
    assert_generated_model_json_validation(
        output_file,
        module_name=f"allof_partial_unconstrained_{module_suffix}",
        model_name="Child",
        valid_json=json.dumps(valid_payload),
        invalid_json=json.dumps({
            field_name: value for field_name, value in valid_payload.items() if field_name != "directScalarValue"
        }),
        expected_error_type="missing",
        expected_attribute_path=("directScalarValue",),
        expected_attribute_value="a",
    )
    for field_name in (
        "directScalarInferred",
        "inlineScalarInferred",
        "scalarArrayInferred",
        "scalarMappingInferred",
        "scalarDeepInferred",
        "scalarArrayRootInferred",
        "scalarMappingRootInferred",
    ):
        assert_generated_model_json_invalid(
            output_file,
            module_name=f"allof_partial_unconstrained_{module_suffix}_{field_name}_missing",
            model_name="Child",
            invalid_json=json.dumps({name: value for name, value in valid_payload.items() if name != field_name}),
            expected_error_type="missing",
        )
    invalid_values_by_field: dict[str, object] = {
        "arrayValue": [{"code": "x"}],
        "mappingValue": {"item": {"code": "x"}},
        "anyEmptyValue": {"code": "x"},
        "anyTrueValue": {"code": "x"},
        "oneEmptyValue": {"code": "x"},
        "oneTrueValue": {"code": "x"},
        "allEmptyValue": {"code": "x"},
        "allTrueValue": {"code": "x"},
        "nullableValue": {"code": "x"},
        "nullableObjectValue": {"code": "x"},
        "directScalarValue": "",
        "directScalarInferred": "",
        "inlineScalarInferred": "",
        "scalarArrayInferred": [""],
        "scalarMappingInferred": {"item": ""},
        "scalarDeepInferred": [[""]],
        "arrayNeutralComposition": [{"code": "x"}],
        "mappingNeutralComposition": {"item": {"code": "x"}},
        "deepArrayNeutralComposition": [[{"code": "x"}]],
        "scalarArrayWeaker": [""],
        "scalarMappingWeaker": {"item": ""},
        "scalarDeepWeaker": [[""]],
        "inlineObjectNeutral": {"code": "x"},
        "refObjectNeutral": {"code": "x"},
    }
    for field_name in valid_payload:
        if (invalid_value := invalid_values_by_field.get(field_name)) is None:
            continue
        assert_generated_model_json_invalid(
            output_file,
            module_name=f"allof_partial_unconstrained_{module_suffix}_{field_name}",
            model_name="Child",
            invalid_json=json.dumps({**valid_payload, field_name: invalid_value}),
            expected_error_type="string_too_short",
        )
    type_errors_by_field: dict[str, object] = {
        "directScalarInferred": 1,
        "inlineScalarInferred": 1,
        "scalarArrayInferred": [1],
        "scalarMappingInferred": {"item": 1},
        "scalarDeepInferred": [[1]],
        "scalarArrayRootInferred": [1],
        "scalarMappingRootInferred": {"item": 1},
    }
    for field_name, invalid_value in type_errors_by_field.items():
        assert_generated_model_json_invalid(
            output_file,
            module_name=f"allof_partial_unconstrained_{module_suffix}_{field_name}_type",
            model_name="Child",
            invalid_json=json.dumps({**valid_payload, field_name: invalid_value}),
            expected_error_type="string_type",
        )
    for field_name, invalid_value in (
        ("scalarArrayRootInferred", []),
        ("scalarMappingRootInferred", {}),
    ):
        assert_generated_model_json_invalid(
            output_file,
            module_name=f"allof_partial_unconstrained_{module_suffix}_{field_name}_length",
            model_name="Child",
            invalid_json=json.dumps({**valid_payload, field_name: invalid_value}),
            expected_error_type="too_short",
        )

    for field_name in ("prefixItemsNeutral", "legacyItemsNeutral", "unevaluatedItemsNeutral"):
        for case_name, invalid_value, expected_error_type in (
            ("tail", ["ab", 1, "tail"], "too_long"),
            ("item", ["x", 1], "string_too_short"),
        ):
            assert_generated_model_json_invalid(
                output_file,
                module_name=f"allof_partial_unconstrained_{module_suffix}_{field_name}_{case_name}",
                model_name="Child",
                invalid_json=json.dumps({**valid_payload, field_name: invalid_value}),
                expected_error_type=expected_error_type,
            )


@pytest.mark.parametrize(
    ("field_constraints", "use_annotated", "expected_suffix"),
    [
        (False, False, "standard"),
        (True, False, "field_constraints"),
        (True, True, "annotated"),
    ],
    ids=["standard", "field-constraints", "annotated"],
)
def test_main_openapi_allof_no_merge_constraint_type_shape(
    output_file: Path,
    expected_suffix: str,
    *,
    field_constraints: bool,
    use_annotated: bool,
) -> None:
    """Apply child constraints only to compatible inherited JSON type shapes."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_no_merge_constraint_type_shape.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=f"allof_no_merge_constraint_type_shape_{expected_suffix}.py",
        extra_args=[
            "--output-model-type",
            DataModelType.PydanticV2BaseModel.value,
            "--target-python-version",
            "3.10",
            "--formatters",
            "builtin",
            "--allof-merge-mode",
            "none",
            "--use-tuple-for-fixed-items",
            *(["--field-constraints"] if field_constraints else []),
            *(["--use-annotated"] if use_annotated else []),
        ],
        force_exec_validation=True,
    )
    valid_payload = {
        "enumValue": "a",
        "constValue": "a",
        "uuidValue": "a",
        "singleAnyValue": "a",
        "singleOneValue": "a",
        "mixedValue": "a",
        "integerValue": 1,
        "stringValue": "a",
        "arrayValue": [1],
        "numberValue": 1.5,
        "booleanValue": True,
        "tupleValue": ["a", 1],
        "containsTrueValue": ["a"],
        "containsFalseValue": ["a"],
        "containsCountValue": ["a", "b"],
        "propertyNamesValue": {"key": "value"},
        "nullableValue": None,
        "nonNullableValue": "a",
        "refSiblingValue": "a",
        "unconstrainedUnionValue": 1,
        "mixedUntypedEnumValue": "a",
        "nullableTypeListValue": "a",
        "nullableAnyValue": "a",
        "nullableOneValue": "a",
        "nullableArrayValue": ["a"],
        "nullableMappingValue": {"key": "a"},
        "unionArrayItemsValue": ["a"],
        "unionArrayContainsValue": ["a"],
        "unionArrayLengthValue": ["a"],
        "unionObjectValuesValue": {"key": "a"},
        "unionObjectNamesValue": {"key": "a"},
        "unionObjectCountValue": {"key": "a"},
        "nestedObjectValue": {
            "detail": {"code": "a", "count": 1},
            "label": "node",
        },
        "itemsTrueValue": [1],
        "mappingTrueValue": {"key": 1},
        "directTrueValue": 1,
        "anyFalseValue": "a",
        "oneFalseValue": "a",
    }
    module_suffix = f"allof_no_merge_constraint_type_shape_{expected_suffix}"
    assert_generated_model_json_validation(
        output_file,
        module_name=module_suffix,
        model_name="Child",
        valid_json=json.dumps(valid_payload),
        invalid_json=json.dumps({**valid_payload, "enumValue": ""}),
        expected_error_type="string_too_short",
        expected_attribute_path=("integerValue",),
        expected_attribute_value=1,
    )
    for field_name, expected_error_type in (
        ("constValue", "string_too_short"),
        ("uuidValue", "string_too_short"),
        ("singleAnyValue", "string_too_short"),
        ("singleOneValue", "string_too_short"),
        ("mixedValue", "string_too_short"),
        ("refSiblingValue", "string_too_short"),
        ("mixedUntypedEnumValue", "int_parsing"),
    ):
        assert_generated_model_json_invalid(
            output_file,
            module_name=f"{module_suffix}_{field_name}",
            model_name="Child",
            invalid_json=json.dumps({**valid_payload, field_name: ""}),
            expected_error_type=expected_error_type,
        )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"{module_suffix}_mixed_integer",
        model_name="Child",
        valid_json=json.dumps({**valid_payload, "mixedValue": 1}),
        invalid_json=json.dumps({**valid_payload, "mixedValue": ""}),
        expected_error_type="string_too_short",
        expected_attribute_path=("integerValue",),
        expected_attribute_value=1,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"{module_suffix}_unconstrained_null",
        model_name="Child",
        valid_json=json.dumps({**valid_payload, "unconstrainedUnionValue": None}),
        invalid_json=json.dumps({
            name: value for name, value in valid_payload.items() if name != "unconstrainedUnionValue"
        }),
        expected_error_type="missing",
        expected_attribute_path=("integerValue",),
        expected_attribute_value=1,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"{module_suffix}_mixed_untyped_integer",
        model_name="Child",
        valid_json=json.dumps({**valid_payload, "mixedUntypedEnumValue": 1}),
        invalid_json=json.dumps({**valid_payload, "mixedUntypedEnumValue": ""}),
        expected_error_type="int_parsing",
        expected_attribute_path=("integerValue",),
        expected_attribute_value=1,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"{module_suffix}_boolean_true",
        model_name="Child",
        valid_json=json.dumps({
            **valid_payload,
            "itemsTrueValue": [None, 1, {}],
            "mappingTrueValue": {"none": None, "integer": 1, "object": {}},
            "directTrueValue": None,
        }),
        invalid_json=json.dumps({name: value for name, value in valid_payload.items() if name != "directTrueValue"}),
        expected_error_type="missing",
        expected_attribute_path=("integerValue",),
        expected_attribute_value=1,
    )
    for field_name, invalid_value, expected_error_type in (
        ("nullableValue", "", "string_too_short"),
        ("nonNullableValue", None, "string_type"),
        ("nullableTypeListValue", None, "string_type"),
        ("nullableAnyValue", None, "string_type"),
        ("nullableOneValue", None, "string_type"),
        ("nullableArrayValue", [None], "string_type"),
        ("nullableMappingValue", {"key": None}, "string_type"),
        ("unionArrayItemsValue", [""], "string_too_short"),
        ("unionArrayContainsValue", [], "too_short"),
        ("unionArrayLengthValue", [], "too_short"),
        ("unionObjectValuesValue", {"key": ""}, "string_too_short"),
        ("unionObjectNamesValue", {"": "value"}, "string_too_short"),
        ("unionObjectCountValue", {}, "too_short"),
        ("containsTrueValue", [], "too_short"),
        ("containsCountValue", ["a"], "too_short"),
        ("containsFalseValue", "a", "list_type"),
        ("propertyNamesValue", {"": "value"}, "string_too_short"),
        ("propertyNamesValue", {"key": 1}, "string_type"),
        ("anyFalseValue", "", "string_too_short"),
        ("oneFalseValue", "", "string_too_short"),
    ):
        assert_generated_model_json_invalid(
            output_file,
            module_name=f"{module_suffix}_{field_name}_{expected_error_type}",
            model_name="Child",
            invalid_json=json.dumps({**valid_payload, field_name: invalid_value}),
            expected_error_type=expected_error_type,
        )
    for field_name in (
        "unionArrayItemsValue",
        "unionArrayContainsValue",
        "unionArrayLengthValue",
        "unionObjectValuesValue",
        "unionObjectNamesValue",
        "unionObjectCountValue",
    ):
        assert_generated_model_json_validation(
            output_file,
            module_name=f"{module_suffix}_{field_name}_scalar_branch",
            model_name="Child",
            valid_json=json.dumps({**valid_payload, field_name: "a"}),
            invalid_json=json.dumps({**valid_payload, field_name: None}),
            expected_error_type="list_type" if "Array" in field_name else "dict_type",
            expected_attribute_path=("integerValue",),
            expected_attribute_value=1,
        )
    for case_name, nested_value, expected_error_type in (
        (
            "code_constraint",
            {"detail": {"code": "", "count": 1}, "label": "node"},
            "string_too_short",
        ),
        (
            "detail_required",
            {"detail": {"code": "a"}, "label": "node"},
            "missing",
        ),
        (
            "node_required",
            {"detail": {"code": "a", "count": 1}},
            "missing",
        ),
    ):
        assert_generated_model_json_invalid(
            output_file,
            module_name=f"{module_suffix}_nested_object_{case_name}",
            model_name="Child",
            invalid_json=json.dumps({**valid_payload, "nestedObjectValue": nested_value}),
            expected_error_type=expected_error_type,
        )
    for invalid_tuple, expected_error_type in (
        (["", 1], "string_too_short"),
        ([1, "a"], "string_type"),
        ([], "missing"),
    ):
        assert_generated_model_json_invalid(
            output_file,
            module_name=f"{module_suffix}_tuple_{expected_error_type}",
            model_name="Child",
            invalid_json=json.dumps({**valid_payload, "tupleValue": invalid_tuple}),
            expected_error_type=expected_error_type,
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
def test_main_openapi_allof_no_merge_backend_types(
    output_file: Path,
    output_model_type: str,
    expected_name: str,
) -> None:
    """Keep inferred no-merge scalar and container types across output backends."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_no_merge_backend_types.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=f"output_model_types/allof_no_merge_backend_types_{expected_name}.py",
        extra_args=[
            *BACKEND_GOLDEN_TARGET_ARGS,
            "--output-model-type",
            output_model_type,
            "--allof-merge-mode",
            "none",
        ],
        force_exec_validation=True,
    )
    match DataModelType(output_model_type):
        case DataModelType.PydanticV2BaseModel | DataModelType.PydanticV2Dataclass:
            pass
        case _:
            return
    valid_payload = {
        "direct": "a",
        "inline": "a",
        "array": ["a"],
        "mapping": {"item": "a"},
        "deep": [["a"]],
        "arrayRoot": ["ab"],
        "mappingRoot": {"item": "ab"},
    }
    assert_generated_model_json_validation(
        output_file,
        module_name=f"allof_no_merge_backend_types_{expected_name}",
        model_name="Child",
        valid_json=json.dumps(valid_payload),
        invalid_json=json.dumps({**valid_payload, "direct": ""}),
        expected_error_type="string_too_short",
        expected_attribute_path=("inline",),
        expected_attribute_value="a",
    )


def test_main_openapi_allof_inherited_constraint_composition_isolation(
    output_file: Path,
) -> None:
    """Keep child-only composition output independent from unrelated inheritance."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_inherited_constraint_composition_isolation.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="allof_inherited_constraint_composition_isolation.py",
        extra_args=[
            "--output-model-type",
            DataModelType.PydanticV2BaseModel.value,
            "--target-python-version",
            "3.11",
            "--formatters",
            "builtin",
            "--allof-class-hierarchy",
            "always",
        ],
        force_exec_validation=True,
    )


@pytest.mark.parametrize(
    ("read_write_mode", "expected_file"),
    [
        pytest.param("all", "allof_required_inherited_model_references_read_write.py", id="all"),
        pytest.param(
            "request-response",
            "allof_required_inherited_model_references_request_response.py",
            id="request-response",
        ),
    ],
)
def test_main_openapi_allof_required_inherited_model_references_read_write(
    output_file: Path,
    read_write_mode: str,
    expected_file: str,
) -> None:
    """Preserve inherited models when read-only and write-only variants are split."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_required_inherited_model_references.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=expected_file,
        extra_args=[
            "--output-model-type",
            DataModelType.PydanticV2BaseModel.value,
            "--target-python-version",
            "3.11",
            "--formatters",
            "builtin",
            "--snake-case-field",
            "--use-default",
            "--default-values",
            str(DEFAULT_VALUES_DATA_PATH / "allof_required_inherited_model_references.json"),
            "--read-only-write-only-model-type",
            read_write_mode,
        ],
        force_exec_validation=True,
    )
    request_payload = {
        "contactDetails": {"name": "Ada"},
        "packages": [{"labelId": "label-1", "alternativeIdentifiers": ["alt-1"]}],
        "package": {"sku": "sku-1"},
        "trackingCode": "track-1",
        "pickupWindow": {"startAt": 1, "endAt": 2},
        "fallbackField": True,
    }
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_required_inherited_request",
        model_name="ScheduledPickupRequest",
        valid_json=json.dumps(request_payload),
        invalid_json=json.dumps({**request_payload, "contactDetails": {"name": ""}}),
        expected_error_type="string_too_short",
    )
    assert_generated_model_json_invalid(
        output_file,
        module_name="allof_required_inherited_request_parent_field",
        model_name="ScheduledPickupRequest",
        invalid_json=json.dumps({**request_payload, "contactDetails": {"name": "Ada", "legacyName": 1}}),
        expected_error_type="string_type",
    )
    response_payload = {
        "contactDetails": {"name": "Ada"},
        "events": [{"latitude": 45}],
        "packages": [{"labelId": "label-1", "alternativeIdentifiers": ["alt-1"]}],
        "package": {"sku": "sku-1"},
        "trackingCode": "track-1",
        "fallbackField": True,
    }
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_required_inherited_response",
        model_name="ScheduledPickupResponse",
        valid_json=json.dumps(response_payload),
        invalid_json=json.dumps({**response_payload, "events": [{"latitude": 91}]}),
        expected_error_type="less_than_equal",
    )
    forward_request_payload = {
        "detail": {"code": "ready"},
        "forwardWindow": {"startAt": 1},
    }
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_required_inherited_forward_request",
        model_name="ForwardDeclaredPickupRequest",
        valid_json=json.dumps(forward_request_payload),
        invalid_json=json.dumps({**forward_request_payload, "detail": {"code": ""}}),
        expected_error_type="string_too_short",
    )
    forward_response_payload = {
        "detail": {"code": "ready"},
        "forwardEvents": [{"latitude": 45}],
    }
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_required_inherited_forward_response",
        model_name="ForwardDeclaredPickupResponse",
        valid_json=json.dumps(forward_response_payload),
        invalid_json=json.dumps({**forward_response_payload, "forwardEvents": [{"latitude": 91}]}),
        expected_error_type="less_than_equal",
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_required_inherited_forward_base_request",
        model_name="ForwardBasePickupRequest",
        valid_json=json.dumps(forward_request_payload),
        invalid_json=json.dumps({**forward_request_payload, "forwardWindow": {"startAt": -1}}),
        expected_error_type="greater_than_equal",
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_required_inherited_forward_base_response",
        model_name="ForwardBasePickupResponse",
        valid_json=json.dumps(forward_response_payload),
        invalid_json=json.dumps({**forward_response_payload, "forwardEvents": [{"latitude": 91}]}),
        expected_error_type="less_than_equal",
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_required_inherited_forward_partial_request",
        model_name="ForwardPartialPickupRequest",
        valid_json=json.dumps(forward_request_payload),
        invalid_json=json.dumps({**forward_request_payload, "detail": {"code": ""}}),
        expected_error_type="string_too_short",
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_required_inherited_forward_partial_response",
        model_name="ForwardPartialPickupResponse",
        valid_json=json.dumps(forward_response_payload),
        invalid_json=json.dumps({**forward_response_payload, "forwardEvents": [{"latitude": 91}]}),
        expected_error_type="less_than_equal",
    )


@pytest.mark.parametrize(
    ("read_write_mode", "expected_file"),
    [
        pytest.param(
            "all",
            "allof_required_inherited_model_references_read_write_reuse.py",
            id="all",
        ),
        pytest.param(
            "request-response",
            "allof_required_inherited_model_references_request_response_reuse.py",
            id="request-response",
        ),
    ],
)
def test_main_openapi_allof_required_inherited_model_references_read_write_reuse(
    output_file: Path,
    read_write_mode: str,
    expected_file: str,
) -> None:
    """Keep variant references canonical when reused models are collapsed."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_required_inherited_model_references.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=expected_file,
        extra_args=[
            "--output-model-type",
            DataModelType.PydanticV2BaseModel.value,
            "--target-python-version",
            "3.11",
            "--formatters",
            "builtin",
            "--snake-case-field",
            "--use-default",
            "--default-values",
            str(DEFAULT_VALUES_DATA_PATH / "allof_required_inherited_model_references.json"),
            "--read-only-write-only-model-type",
            read_write_mode,
            "--reuse-model",
            "--collapse-reuse-models",
        ],
        force_exec_validation=True,
    )
    request_payload = {
        "contactDetails": {"name": "Ada"},
        "packages": [{"labelId": "label-1", "alternativeIdentifiers": ["alt-1"]}],
        "package": {"sku": "sku-1"},
        "trackingCode": "track-1",
        "pickupWindow": {"startAt": 1, "endAt": 2},
        "fallbackField": True,
    }
    assert_generated_model_json_validation(
        output_file,
        module_name=f"allof_required_inherited_read_write_reuse_request_{read_write_mode}",
        model_name="ScheduledPickupRequest",
        valid_json=json.dumps(request_payload),
        invalid_json=json.dumps({**request_payload, "contactDetails": {"name": ""}}),
        expected_error_type="string_too_short",
    )
    response_payload = {
        "contactDetails": {"name": "Ada"},
        "events": [{"latitude": 45}],
        "packages": [{"labelId": "label-1", "alternativeIdentifiers": ["alt-1"]}],
        "package": {"sku": "sku-1"},
        "trackingCode": "track-1",
        "fallbackField": True,
    }
    assert_generated_model_json_validation(
        output_file,
        module_name=f"allof_required_inherited_read_write_reuse_response_{read_write_mode}",
        model_name="ScheduledPickupResponse",
        valid_json=json.dumps(response_payload),
        invalid_json=json.dumps({**response_payload, "events": [{"latitude": 91}]}),
        expected_error_type="less_than_equal",
    )


@pytest.mark.parametrize(
    ("option", "read_write_mode", "expected_name"),
    [
        pytest.param("--reuse-model", None, "reuse_model", id="reuse-model"),
        pytest.param(
            "--collapse-root-models",
            None,
            "collapse_root_models",
            id="collapse-root-models",
        ),
        pytest.param(
            "--collapse-root-models",
            "request-response",
            "collapse_root_models_request_response",
            id="collapse-root-models-request-response",
        ),
    ],
)
def test_main_openapi_allof_required_inherited_model_reference_transforms(
    output_file: Path,
    option: str,
    read_write_mode: str | None,
    expected_name: str,
) -> None:
    """Keep canonical inherited references through model reuse and root collapse."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_required_inherited_model_references.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=f"allof_required_inherited_model_references_{expected_name}.py",
        extra_args=[
            "--output-model-type",
            DataModelType.PydanticV2BaseModel.value,
            "--target-python-version",
            "3.11",
            "--formatters",
            "builtin",
            "--snake-case-field",
            "--use-default",
            "--default-values",
            str(DEFAULT_VALUES_DATA_PATH / "allof_required_inherited_model_references.json"),
            *([] if read_write_mode is None else ["--read-only-write-only-model-type", read_write_mode]),
            option,
        ],
        force_exec_validation=True,
    )
    payload = {
        "contactDetails": {"name": "Ada"},
        "events": [{"latitude": 45}],
        "packages": [{"labelId": "label-1", "alternativeIdentifiers": ["alt-1"]}],
        "package": {"sku": "sku-1"},
        "trackingCode": "track-1",
        "pickupWindow": {"startAt": 1, "endAt": 2},
        "fallbackField": True,
    }
    expected_error_type = ""
    model_name = "ScheduledPickup"
    match option, read_write_mode:
        case "--reuse-model", None:
            payload["events"] = [{"latitude": 91}]
            expected_error_type = "less_than_equal"
            model_name = "ScheduledPickup"
        case "--collapse-root-models", None:
            payload["trackingCode"] = ""
            expected_error_type = "string_too_short"
            model_name = "ScheduledPickup"
        case "--collapse-root-models", "request-response":
            payload.pop("events")
            payload["trackingCode"] = ""
            expected_error_type = "string_too_short"
            model_name = "ScheduledPickupRequest"
        case _:  # pragma: no cover
            raise ValueError((option, read_write_mode))
    assert_generated_model_json_invalid(
        output_file,
        module_name=f"allof_required_inherited_{expected_name}",
        model_name=model_name,
        invalid_json=json.dumps(payload),
        expected_error_type=expected_error_type,
    )


def test_main_openapi_allof_partial_override_inherited_types(output_file: Path) -> None:
    """Test OpenAPI allOf partial overrides inherit parent field types."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_partial_override_inherited_types.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="allof_partial_override_inherited_types.py",
    )


def test_main_openapi_allof_partial_override_array_items(output_file: Path) -> None:
    """Test OpenAPI allOf partial overrides inherit parent array item types."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_partial_override_array_items.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="allof_partial_override_array_items.py",
    )


def test_main_openapi_allof_partial_override_array_items_no_parent(output_file: Path) -> None:
    """Test OpenAPI allOf with array field not present in parent schema."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_partial_override_array_items_no_parent.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="allof_partial_override_array_items_no_parent.py",
    )


def test_main_openapi_allof_partial_override_non_array_field(output_file: Path) -> None:
    """Test OpenAPI allOf partial override with non-array fields for coverage."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_partial_override_non_array_field.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="allof_partial_override_non_array_field.py",
    )


def test_main_openapi_allof_partial_override_nested_array_items(output_file: Path) -> None:
    """Test OpenAPI allOf partial override with nested arrays for coverage."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_partial_override_nested_array_items.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="allof_partial_override_nested_array_items.py",
    )


def test_main_openapi_allof_partial_override_deeply_nested_array(output_file: Path) -> None:
    """Test OpenAPI allOf partial override with 3-level nested arrays for while loop coverage."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_partial_override_deeply_nested_array.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="allof_partial_override_deeply_nested_array.py",
    )


def test_main_openapi_allof_partial_override_simple_list_any(output_file: Path) -> None:
    """Test OpenAPI allOf partial override with simple List[Any] - while loop NOT entered."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_partial_override_simple_list_any.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="allof_partial_override_simple_list_any.py",
    )


def test_main_openapi_allof_partial_override_unique_items(output_file: Path) -> None:
    """Test OpenAPI allOf partial override inherits uniqueItems from parent."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_partial_override_unique_items.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="allof_partial_override_unique_items_pydantic_v2.py",
        extra_args=["--use-unique-items-as-set", "--output-model-type", "pydantic_v2.BaseModel"],
    )


@pytest.mark.cli_doc(
    options=["--allof-merge-mode"],
    option_description="""Merge all properties from parent schemas in allOf.

The `--allof-merge-mode` flag controls how parent schema properties are merged
in allOf compositions. With `all` mode, constraints plus annotations (default,
examples) are merged from parent properties. This ensures child schemas inherit
all metadata from parents.""",
    input_schema="openapi/allof_materialize_defaults.yaml",
    cli_args=["--allof-merge-mode", "all"],
    golden_output="main/openapi/allof_materialize_defaults.py",
)
def test_main_openapi_allof_merge_mode_all(output_file: Path) -> None:
    """Merge all properties from parent schemas in allOf.

    The `--allof-merge-mode` flag controls how parent schema properties are merged
    in allOf compositions. With `all` mode, constraints plus annotations (default,
    examples) are merged from parent properties. This ensures child schemas inherit
    all metadata from parents.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_materialize_defaults.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="allof_materialize_defaults.py",
        extra_args=["--allof-merge-mode", "all"],
    )


@pytest.mark.cli_doc(
    options=["--allof-merge-mode"],
    option_description="""Disable property merging from parent schemas in allOf.

With `none` mode, no fields are merged from parent properties. This is useful
when you want child schemas to define all their own constraints without inheriting
from parents.""",
    input_schema="openapi/allof_merge_mode_none.yaml",
    cli_args=["--allof-merge-mode", "none"],
    golden_output="main/openapi/allof_merge_mode_none.py",
    comparison_output="main/openapi/allof_materialize_defaults.py",
)
def test_main_openapi_allof_merge_mode_none(output_file: Path) -> None:
    """Disable property merging from parent schemas in allOf.

    With `none` mode, no fields are merged from parent properties. This is useful
    when you want child schemas to define all their own constraints without inheriting
    from parents.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_merge_mode_none.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="allof_merge_mode_none.py",
        extra_args=["--allof-merge-mode", "none"],
    )


def test_main_openapi_allof_property_bool_schema(output_file: Path) -> None:
    """Test OpenAPI allOf with bool property schema (e.g., `allowed: true`)."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_property_bool_schema.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="allof_property_bool_schema.py",
    )


def test_main_openapi_allof_parent_no_properties(output_file: Path) -> None:
    """Test OpenAPI allOf with parent schema having no properties."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_parent_no_properties.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="allof_parent_no_properties.py",
    )


def test_main_openapi_allof_parent_bool_property(output_file: Path) -> None:
    """Test OpenAPI allOf with parent having bool property schema (true/false)."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_parent_bool_property.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="allof_parent_bool_property.py",
    )


def test_main_openapi_allof_multiple_parents_same_property(output_file: Path) -> None:
    """Test OpenAPI allOf with multiple parents having the same property."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_multiple_parents_same_property.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="allof_multiple_parents_same_property.py",
    )


def test_main_openapi_allof_with_required_inherited_edge_cases(output_file: Path) -> None:
    """Test OpenAPI generation with allOf edge cases for branch coverage."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_with_required_inherited_edge_cases.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="allof_with_required_inherited_edge_cases.py",
        force_exec_validation=True,
    )
    assert_generated_model_json_invalid(
        output_file,
        module_name="allof_required_inherited_additional_properties",
        model_name="MultipleAdditionalProps",
        invalid_json='{"key":{"id":"bad"}}',
        expected_error_type="int_parsing",
    )


@LEGACY_BLACK_SKIP
def test_main_openapi_allof_with_required_inherited_coverage(output_file: Path) -> None:
    """Test OpenAPI generation with allOf coverage for edge case branches."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "allof_with_required_inherited_coverage.yaml",
            output_path=output_file,
            input_file_type="openapi",
            assert_func=assert_file_content,
            expected_file="allof_with_required_inherited_coverage.py",
        )
        # Verify the warning was raised for $ref combined with constraints
        assert_warnings_contain(w, "allOf combines $ref")


def test_main_use_default_kwarg(output_file: Path) -> None:
    """Test OpenAPI generation with use default kwarg."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "nullable.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        extra_args=["--use-default-kwarg"],
    )


@pytest.mark.parametrize(
    ("output_model_type", "expected_suffix", "extra_args"),
    [
        pytest.param(
            "pydantic_v2.BaseModel",
            "pydantic_v2_BaseModel",
            [],
            id="pydantic-base-model",
        ),
        pytest.param(
            "pydantic_v2.dataclass",
            "pydantic_v2_dataclass_annotated",
            ["--use-annotated"],
            id="pydantic-dataclass-annotated",
        ),
        pytest.param(
            "msgspec.Struct",
            "msgspec_Struct",
            [],
            id="msgspec",
        ),
    ],
)
def test_main_openapi_structured_field_render_plan(
    output_file: Path,
    output_model_type: str,
    expected_suffix: str,
    extra_args: list[str],
) -> None:
    """Render defaults and syntax-like user values without parsing generated fields."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "field_render_plan.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=f"output_model_types/field_render_plan_{expected_suffix}.py",
        extra_args=[
            "--output-model-type",
            output_model_type,
            "--use-default-kwarg",
            "--use-default-factory-for-optional-nested-models",
            "--field-extra-keys",
            "x-render-marker",
            "x-is-classvar",
            *extra_args,
        ],
        force_exec_validation=True,
    )


@pytest.mark.parametrize(
    ("input_", "output"),
    [
        (
            "discriminator.yaml",
            "general.py",
        ),
        (
            "discriminator_without_mapping.yaml",
            "without_mapping.py",
        ),
    ],
)
def test_main_openapi_discriminator(input_: str, output: str, output_file: Path) -> None:
    """Test OpenAPI generation with discriminator."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / input_,
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "discriminator" / output,
    )


@pytest.mark.parametrize(
    ("output_model_type", "expected_file"),
    [
        pytest.param(
            DataModelType.DataclassesDataclass.value,
            "discriminator/dataclass_constructor_order.py",
            id="dataclass",
        ),
        pytest.param(
            DataModelType.MsgspecStruct.value,
            "discriminator/msgspec_constructor_order.py",
            id="msgspec",
        ),
    ],
)
def test_main_openapi_discriminator_constructor_order(
    output_file: Path,
    output_model_type: str,
    expected_file: str,
) -> None:
    """Place injected required discriminator fields before optional constructor fields."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=expected_file,
        extra_args=[
            "--output-model-type",
            output_model_type,
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ],
        force_exec_validation=True,
    )


def test_main_openapi_discriminator_import_override_removes_original(output_file: Path) -> None:
    """Remove original discriminator imports after applying a module override."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "discriminator" / "import_override.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.dataclass",
            "--import-overrides",
            '{"Field": "pydantic.v1"}',
        ],
        force_exec_validation=True,
    )


@freeze_time("2023-07-27")
@pytest.mark.parametrize(
    ("kind", "option", "output_model", "expected"),
    [
        ("anyOf", "--collapse-root-models", None, "in_array_collapse_root_models.py"),
        ("oneOf", "--collapse-root-models", None, "in_array_collapse_root_models.py"),
        ("anyOf", None, None, "in_array.py"),
        ("oneOf", None, None, "in_array.py"),
        ("anyOf", "--collapse-root-models", "pydantic_v2.BaseModel", "in_array_collapse_root_models_pydantic_v2.py"),
        ("oneOf", "--collapse-root-models", "pydantic_v2.BaseModel", "in_array_collapse_root_models_pydantic_v2.py"),
    ],
)
def test_main_openapi_discriminator_in_array(
    kind: str, option: str | None, output_model: str | None, expected: str, output_file: Path
) -> None:
    """Test OpenAPI generation with discriminator in array."""
    input_file = f"discriminator_in_array_{kind.lower()}.yaml"
    extra_args = [option] if option else []
    if output_model:
        extra_args.extend(["--output-model-type", output_model])
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / input_file,
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=f"discriminator/{expected}",
        extra_args=extra_args,
        transform=lambda s: s.replace(input_file, "discriminator_in_array.yaml"),
    )


@freeze_time("2023-07-27")
def test_main_openapi_discriminator_in_array_underscore(output_file: Path) -> None:
    """Test discriminator with underscore property name generates valid Pydantic v2 code."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_in_array_underscore.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="discriminator/in_array_underscore_pydantic_v2.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", "--collapse-root-models"],
    )


@LEGACY_BLACK_SKIP
@freeze_time("2023-07-27")
def test_main_openapi_discriminator_in_array_snake_case(output_file: Path) -> None:
    """Test collapsed list item discriminator uses snake_case field name."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_in_array_snake_case.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="discriminator/in_array_snake_case_pydantic_v2.py",
        extra_args=[
            "--target-python-version",
            "3.12",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-annotated",
            "--snake-case-field",
            "--collapse-root-models",
        ],
        force_exec_validation=True,
    )


@pytest.mark.parametrize(
    ("output_model", "expected_output"),
    [
        (
            "pydantic_v2.BaseModel",
            "pydantic_v2_default_object",
        ),
        (
            "msgspec.Struct",
            "msgspec_default_object",
        ),
    ],
)
@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_openapi_default_object(output_model: str, expected_output: str, tmp_path: Path) -> None:
    """Test OpenAPI generation with default object values."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "default_object.yaml",
        output_path=tmp_path,
        expected_directory=EXPECTED_OPENAPI_PATH / expected_output,
        input_file_type="openapi",
        extra_args=["--output-model-type", output_model, "--target-python-version", "3.10"],
    )


@pytest.mark.parametrize(
    ("output_model", "expected_output"),
    [
        (
            "pydantic_v2.BaseModel",
            "pydantic_v2_union_default_object.py",
        ),
        (
            "msgspec.Struct",
            "msgspec_union_default_object.py",
        ),
    ],
)
@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_openapi_union_default_object(output_model: str, expected_output: str, output_file: Path) -> None:
    """Test OpenAPI generation with Union type default object values."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "union_default_object.yaml",
        output_path=output_file,
        expected_file=EXPECTED_OPENAPI_PATH / expected_output,
        input_file_type="openapi",
        extra_args=[
            "--output-model-type",
            output_model,
            "--target-python-version",
            "3.10",
            "--openapi-scopes",
            "schemas",
        ],
    )


@pytest.mark.parametrize(
    ("output_model", "expected_output"),
    [
        (
            "pydantic_v2.BaseModel",
            "pydantic_v2_empty_dict_default.py",
        ),
        (
            "msgspec.Struct",
            "msgspec_empty_dict_default.py",
        ),
    ],
)
@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_openapi_empty_dict_default(output_model: str, expected_output: str, output_file: Path) -> None:
    """Test OpenAPI generation with empty dict default values."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "empty_dict_default.yaml",
        output_path=output_file,
        expected_file=EXPECTED_OPENAPI_PATH / expected_output,
        input_file_type="openapi",
        extra_args=[
            "--output-model-type",
            output_model,
            "--target-python-version",
            "3.10",
            "--openapi-scopes",
            "schemas",
        ],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_openapi_empty_list_default(output_file: Path) -> None:
    """Test OpenAPI generation with empty list default values."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "empty_list_default.yaml",
        output_path=output_file,
        expected_file=EXPECTED_OPENAPI_PATH / "pydantic_v2_empty_list_default.py",
        assert_func=assert_file_content,
        input_file_type="openapi",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--target-python-version",
            "3.10",
            "--openapi-scopes",
            "schemas",
        ],
    )


def test_main_dataclass(output_file: Path) -> None:
    """Test OpenAPI generation with dataclass output."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        extra_args=["--output-model-type", "dataclasses.dataclass"],
    )


def test_main_dataclass_base_class(output_file: Path) -> None:
    """Test OpenAPI generation with dataclass base class."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        extra_args=["--output-model-type", "dataclasses.dataclass", "--base-class", "custom_base.Base"],
    )


def test_main_openapi_reference_same_hierarchy_directory(tmp_path: Path) -> None:
    """Test OpenAPI generation with reference in same hierarchy directory."""
    output_file: Path = tmp_path / "output.py"
    with (
        chdir(OPEN_API_DATA_PATH / "reference_same_hierarchy_directory"),
        pytest.warns(FutureWarning, match=r"outside the input base path"),
    ):
        run_main_and_assert(
            input_path=Path("./public/entities.yaml"),
            output_path=output_file,
            input_file_type="openapi",
            assert_func=assert_file_content,
            expected_file="reference_same_hierarchy_directory.py",
        )


def test_main_multiple_required_any_of(output_file: Path) -> None:
    """Test OpenAPI generation with multiple required anyOf."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "multiple_required_any_of.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        extra_args=["--collapse-root-models"],
    )


def test_main_openapi_max_min(output_file: Path) -> None:
    """Test OpenAPI generation with max and min constraints."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "max_min_number.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="max_min_number.py",
    )


@pytest.mark.cli_doc(
    options=["--use-operation-id-as-name"],
    option_description="""Use OpenAPI operationId as the generated function/class name.

The `--use-operation-id-as-name` flag configures the code generation behavior.""",
    input_schema="openapi/api.yaml",
    cli_args=["--use-operation-id-as-name", "--openapi-scopes", "paths", "schemas", "parameters"],
    golden_output="openapi/use_operation_id_as_name.py",
)
def test_main_openapi_use_operation_id_as_name(output_file: Path) -> None:
    """Use OpenAPI operationId as the generated function/class name.

    The `--use-operation-id-as-name` flag configures the code generation behavior.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="use_operation_id_as_name.py",
        extra_args=["--use-operation-id-as-name", "--openapi-scopes", "paths", "schemas", "parameters"],
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
@pytest.mark.benchmark
def test_main_openapi_parameter_field_policy(
    output_model_type: str,
    expected_name: str,
    output_file: Path,
) -> None:
    """Apply schema field policy uniformly to schema and content parameters."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "parameter_field_policy.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=f"parameter_field_policy/{expected_name}.py",
        extra_args=[
            *BACKEND_GOLDEN_TARGET_ARGS,
            "--output-model-type",
            output_model_type,
            "--use-operation-id-as-name",
            "--openapi-scopes",
            "paths",
            "schemas",
            "parameters",
            "--use-default-factory-for-optional-nested-models",
            "--use-frozen-field",
        ],
    )
    if output_model_type == DataModelType.DataclassesDataclass.value:
        with _generated_model(
            output_file,
            "parameter_field_policy_dataclasses_dataclass",
            "Defaulted",
        ) as model:
            nested = model().nested
            if type(nested).__name__ != "Nested" or nested.value != "preset":  # pragma: no cover
                pytest.fail(f"Nested mapping default produced an unexpected value: {nested!r}")
        return

    if output_model_type != DataModelType.PydanticV2Dataclass.value:
        return

    with _generated_model(
        output_file,
        "parameter_field_policy_pydantic_v2_dataclass",
        "ListItemsParametersQuery",
    ) as model:
        signature = inspect.signature(model)
        expected_parameters = (
            "schema_nested",
            "content_nested",
            "schema_read_only",
            "content_read_only",
            "schema_write_only",
            "content_write_only",
            "content_array",
            "content_multi",
        )
        if tuple(signature.parameters) != expected_parameters:  # pragma: no cover
            pytest.fail(f"Unexpected constructor parameter order: {tuple(signature.parameters)!r}")
        if any(
            parameter.kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD for parameter in signature.parameters.values()
        ):  # pragma: no cover
            pytest.fail(f"Unexpected keyword-only constructor parameter: {signature!s}")

        instance = model()
        nested_types = tuple(
            type(getattr(instance, field_name)).__name__
            for field_name in ("schema_nested", "content_nested", "content_multi")
        )
        if nested_types != ("Nested", "ContentNested", "ContentMulti"):  # pragma: no cover
            pytest.fail(f"Nested default factories produced unexpected values: {nested_types!r}")


def test_main_openapi_use_operation_id_as_name_not_found_operation_id(
    capsys: pytest.CaptureFixture, output_file: Path
) -> None:
    """Test OpenAPI generation with operation ID as name when ID not found."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "body_and_parameters.yaml",
        output_path=output_file,
        input_file_type="openapi",
        expected_exit=Exit.ERROR,
        extra_args=["--use-operation-id-as-name", "--openapi-scopes", "paths", "schemas", "parameters"],
        capsys=capsys,
        expected_stderr_contains="All operations must have an operationId when --use_operation_id_as_name is set.",
    )


def test_main_unsorted_optional_fields(output_file: Path) -> None:
    """Test OpenAPI generation with unsorted optional fields."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "unsorted_optional_fields.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        extra_args=["--output-model-type", "dataclasses.dataclass"],
    )


def test_main_typed_dict(output_file: Path) -> None:
    """Test OpenAPI generation with TypedDict output."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        extra_args=["--output-model-type", "typing.TypedDict"],
    )


def test_main_typed_dict_py(min_version: str, output_file: Path) -> None:
    """Test OpenAPI generation with TypedDict for specific Python version."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        extra_args=["--output-model-type", "typing.TypedDict", "--target-python-version", min_version],
    )


@pytest.mark.skipif(
    version.parse(black.__version__) < version.parse("23.3.0"),
    reason="Require Black version 23.3.0 or later ",
)
def test_main_modular_typed_dict(output_dir: Path) -> None:
    """Test main function on modular file."""
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "modular.yaml",
            output_path=output_dir,
            expected_directory=EXPECTED_OPENAPI_PATH / "modular_typed_dict",
            extra_args=["--output-model-type", "typing.TypedDict", "--target-python-version", "3.11"],
        )


@pytest.mark.skipif(
    version.parse(black.__version__) < version.parse("23.3.0"),
    reason="Require Black version 23.3.0 or later ",
)
def test_main_typed_dict_nullable(output_file: Path) -> None:
    """Test OpenAPI generation with nullable TypedDict."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "nullable.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        extra_args=["--output-model-type", "typing.TypedDict", "--target-python-version", "3.11"],
    )


@LEGACY_BLACK_SKIP
@pytest.mark.skipif(
    version.parse(black.__version__) < version.parse("23.3.0"),
    reason="Require Black version 23.3.0 or later ",
)
def test_main_msgspec_nullable(output_file: Path) -> None:
    """Test OpenAPI generation with nullable msgspec.Struct."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "nullable.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="msgspec_nullable.py",
        extra_args=["--output-model-type", "msgspec.Struct", "--target-python-version", "3.11"],
    )


@pytest.mark.skipif(
    version.parse(black.__version__) < version.parse("23.3.0"),
    reason="Require Black version 23.3.0 or later ",
)
def test_main_typed_dict_nullable_strict_nullable(output_file: Path) -> None:
    """Test OpenAPI generation with strict nullable TypedDict."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "nullable.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        extra_args=["--output-model-type", "typing.TypedDict", "--target-python-version", "3.11", "--strict-nullable"],
    )


@pytest.mark.benchmark
def test_main_openapi_nullable_31(output_file: Path) -> None:
    """Test OpenAPI 3.1 generation with nullable types."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "nullable_31.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="nullable_31.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", "--strip-default-none", "--use-union-operator"],
    )


def test_main_openapi_nullable_required_annotated(output_file: Path) -> None:
    """Test OpenAPI generation with nullable required fields using annotations."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "nullable_required_annotated.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="nullable_required_annotated.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--strict-nullable",
            "--use-annotated",
            "--snake-case-field",
        ],
    )


@pytest.mark.cli_doc(
    options=["--custom-file-header-path"],
    option_description="""Add custom header content from file to generated code.

The `--custom-file-header-path` flag allows you to specify a file containing
custom header content (like copyright notices, linting directives, or module docstrings)
to be inserted at the top of generated Python files.""",
    input_schema="openapi/api.yaml",
    cli_args=["--custom-file-header-path", "custom_file_header.txt"],
    golden_output="openapi/custom_file_header.py",
)
def test_main_custom_file_header_path(output_file: Path) -> None:
    """Add custom header content from file to generated code.

    The `--custom-file-header-path` flag allows you to specify a file containing
    custom header content (like copyright notices, linting directives, or module docstrings)
    to be inserted at the top of generated Python files.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="custom_file_header.py",
        extra_args=["--custom-file-header-path", str(DATA_PATH / "custom_file_header.txt")],
    )


@pytest.mark.parametrize("custom_file_header", ["abc", ""])
def test_main_custom_file_header_duplicate_options(
    capsys: pytest.CaptureFixture, output_file: Path, custom_file_header: str
) -> None:
    """Test OpenAPI generation with duplicate custom file header options."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        expected_exit=Exit.ERROR,
        extra_args=[
            "--custom-file-header-path",
            str(DATA_PATH / "custom_file_header.txt"),
            "--custom-file-header",
            custom_file_header,
        ],
        capsys=capsys,
        expected_stderr_contains="`--custom_file_header_path` can not be used with `--custom_file_header`.",
    )


def test_main_custom_file_header_with_docstring(output_file: Path) -> None:
    """Test future import placement after docstring in custom header."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="custom_file_header_with_docstring.py",
        extra_args=["--custom-file-header-path", str(DATA_PATH / "custom_file_header_with_docstring.txt")],
    )


def test_main_custom_file_header_with_import(output_file: Path) -> None:
    """Test future import placement before existing imports in custom header."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="custom_file_header_with_import.py",
        extra_args=["--custom-file-header-path", str(DATA_PATH / "custom_file_header_with_import.txt")],
    )


@pytest.mark.parametrize("directory_input", [False, True])
def test_main_custom_file_header_with_docstring_and_import(output_file: Path, directory_input: bool) -> None:
    """Test future import placement with docstring and imports in custom header."""
    input_path = OPEN_API_DATA_PATH / "api.yaml"
    if directory_input:
        input_path = output_file.parent / "schemas"
        input_path.mkdir()
        shutil.copyfile(OPEN_API_DATA_PATH / "api.yaml", input_path / "api.yaml")
        output_file = input_path
    for _ in range(2):
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type=None,
            assert_func=assert_file_content,
            expected_file="custom_file_header_with_docstring_and_import.py",
            output_to_expected=[("api.py", "custom_file_header_with_docstring_and_import.py")]
            if directory_input
            else None,
            extra_args=[
                "--custom-file-header-path",
                str(DATA_PATH / "custom_file_header_with_docstring_and_import.txt"),
                # Keep helper-created config/parity outputs outside this repeated-input case.
                *(["--formatters", "builtin"] if directory_input else []),
            ],
        )


def test_main_custom_file_header_without_future_imports(output_file: Path) -> None:
    """Test custom header with --disable-future-imports option."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="custom_file_header_no_future.py",
        extra_args=[
            "--custom-file-header-path",
            str(DATA_PATH / "custom_file_header.txt"),
            "--disable-future-imports",
        ],
    )


def test_main_custom_file_header_empty(output_file: Path) -> None:
    """Test empty custom header file."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="custom_file_header_empty.py",
        extra_args=["--custom-file-header-path", str(DATA_PATH / "custom_file_header_empty.txt")],
    )


def test_main_custom_file_header_invalid_syntax(output_file: Path) -> None:
    """Test custom header with invalid Python syntax."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="custom_file_header_invalid_syntax.py",
        extra_args=["--custom-file-header-path", str(DATA_PATH / "custom_file_header_invalid_syntax.txt")],
        skip_code_validation=True,
    )


def test_main_custom_file_header_comments_only(output_file: Path) -> None:
    """Test custom header with only comments (no statements)."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="custom_file_header_comments_only.py",
        extra_args=["--custom-file-header-path", str(DATA_PATH / "custom_file_header_comments_only.txt")],
    )


def test_main_pydantic_v2(output_file: Path) -> None:
    """Test OpenAPI generation with Pydantic v2 output."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )


def test_main_openapi_custom_id_pydantic_v2(output_file: Path) -> None:
    """Test OpenAPI generation with custom ID for Pydantic v2."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "custom_id.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="custom_id_pydantic_v2.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )


@pytest.mark.cli_doc(
    options=["--use-serialize-as-any"],
    option_description="""Wrap fields with subtypes in Pydantic's SerializeAsAny.

The `--use-serialize-as-any` flag applies Pydantic v2's SerializeAsAny wrapper
to fields that have subtype relationships, ensuring proper serialization of
polymorphic types and inheritance hierarchies.""",
    input_schema="openapi/serialize_as_any.yaml",
    cli_args=["--use-serialize-as-any"],
    golden_output="openapi/serialize_as_any_pydantic_v2.py",
)
def test_main_openapi_serialize_as_any_pydantic_v2(output_file: Path) -> None:
    """Wrap fields with subtypes in Pydantic's SerializeAsAny.

    The `--use-serialize-as-any` flag applies Pydantic v2's SerializeAsAny wrapper
    to fields that have subtype relationships, ensuring proper serialization of
    polymorphic types and inheritance hierarchies.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "serialize_as_any.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="serialize_as_any_pydantic_v2.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", "--use-serialize-as-any"],
    )


@pytest.mark.skipif(
    version.parse(pydantic.VERSION) < version.parse("2.0.0"),
    reason="Require Pydantic version 2.0.0 or later",
)
def test_main_openapi_serialize_as_any_module_import_alias_pydantic_v2(output_dir: Path) -> None:
    """Test SerializeAsAny with modular output and dotted schema import aliases."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "serialize_as_any_module_import_alias" / "openapi.json",
        output_path=output_dir,
        expected_directory=EXPECTED_OPENAPI_PATH / "serialize_as_any_module_import_alias",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--openapi-scopes",
            "schemas",
            "--use-serialize-as-any",
            "--target-python-version",
            "3.10",
        ],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_openapi_all_of_with_relative_ref(output_file: Path) -> None:
    """Test OpenAPI generation with allOf and relative reference."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "all_of_with_relative_ref" / "openapi.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="all_of_with_relative_ref.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--keep-model-order",
            "--collapse-root-models",
            "--field-constraints",
            "--use-title-as-name",
            "--field-include-all-keys",
            "--use-field-description",
        ],
    )


@LEGACY_BLACK_SKIP
def test_main_openapi_msgspec_struct(min_version: str, output_file: Path) -> None:
    """Test OpenAPI generation with msgspec Struct output."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="msgspec_struct.py",
        extra_args=["--target-python-version", min_version, "--output-model-type", "msgspec.Struct"],
    )


@LEGACY_BLACK_SKIP
def test_main_openapi_msgspec_struct_snake_case(min_version: str, output_file: Path) -> None:
    """Test OpenAPI generation with msgspec Struct and snake case."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api_ordered_required_fields.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="msgspec_struct_snake_case.py",
        extra_args=[
            "--target-python-version",
            min_version,
            "--snake-case-field",
            "--output-model-type",
            "msgspec.Struct",
        ],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
@MSGSPEC_LEGACY_BLACK_SKIP
def test_main_openapi_msgspec_use_annotated_with_field_constraints(output_file: Path) -> None:
    """Test OpenAPI generation with msgspec using Annotated and field constraints."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api_constrained.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="msgspec_use_annotated_with_field_constraints.py",
        extra_args=["--field-constraints", "--target-python-version", "3.10", "--output-model-type", "msgspec.Struct"],
    )

    import msgspec

    cases = json.loads((DATA_PATH / "payloads/openapi_msgspec_numeric_unions.json").read_text())
    actual = []
    with _generated_model(output_file, "openapi_msgspec_numeric_unions", "User") as model:
        decoder = msgspec.json.Decoder(model)
        for case in cases:
            payload = json.dumps(case["input"])
            if case["valid"]:
                actual.append(msgspec.to_builtins(decoder.decode(payload)))
            else:
                with pytest.raises(msgspec.ValidationError):
                    decoder.decode(payload)
    assert_output(
        json.dumps(actual, indent=2) + "\n",
        EXPECTED_OPENAPI_PATH / "msgspec_numeric_unions_runtime.txt",
    )


@pytest.mark.parametrize(
    ("output_model", "expected_file"),
    [
        ("pydantic_v2.BaseModel", "discriminator/enum_one_literal_as_default.py"),
        ("dataclasses.dataclass", "discriminator/dataclass_enum_one_literal_as_default.py"),
    ],
)
@pytest.mark.cli_doc(
    options=["--use-one-literal-as-default"],
    option_description="""Set default value when only one literal is valid for a discriminator field.

The `--use-one-literal-as-default` flag sets default values for discriminator
fields when only one literal value is valid, reducing boilerplate in model
instantiation.""",
    input_schema="openapi/discriminator_enum.yaml",
    cli_args=["--use-one-literal-as-default"],
    model_outputs={
        "pydantic_v2": "openapi/discriminator/enum_one_literal_as_default.py",
        "dataclass": "openapi/discriminator/dataclass_enum_one_literal_as_default.py",
    },
)
def test_main_openapi_discriminator_one_literal_as_default(
    output_model: str, expected_file: str, output_file: Path
) -> None:
    """Set default value when only one literal is valid for a discriminator field.

    The `--use-one-literal-as-default` flag sets default values for discriminator
    fields when only one literal value is valid, reducing boilerplate in model
    instantiation.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_enum.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / expected_file,
        extra_args=["--output-model-type", output_model, "--use-one-literal-as-default"],
    )


@pytest.mark.parametrize(
    ("output_model", "optional_args", "expected_file"),
    [
        ("pydantic_v2.BaseModel", (), "discriminator/required_pydantic_v2.py"),
        (
            "pydantic_v2.BaseModel",
            ("--force-optional",),
            "discriminator/force_optional_pydantic_v2.py",
        ),
        (
            "pydantic_v2.BaseModel",
            ("--force-optional", "--use-one-literal-as-default"),
            "discriminator/force_optional_pydantic_v2.py",
        ),
        (
            "pydantic_v2.dataclass",
            ("--force-optional",),
            "discriminator/force_optional_pydantic_v2_dataclass.py",
        ),
    ],
)
def test_main_openapi_discriminator_optional_pydantic_v2(
    output_model: str, optional_args: tuple[str, ...], expected_file: str, output_file: Path
) -> None:
    """Keep required and force-optional Pydantic v2 discriminator literals importable."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_force_optional.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=expected_file,
        extra_args=[
            "--formatters",
            "builtin",
            "--target-python-version",
            "3.10",
            "--output-model-type",
            output_model,
            *optional_args,
        ],
        force_exec_validation=True,
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_openapi_discriminator_one_literal_as_default_dataclass_py310(output_file: Path) -> None:
    """Test OpenAPI generation with discriminator one literal as default for dataclass with Python 3.10+."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_enum.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "discriminator" / "dataclass_enum_one_literal_as_default_py310.py",
        extra_args=[
            "--output-model-type",
            "dataclasses.dataclass",
            "--use-one-literal-as-default",
            "--target-python-version",
            "3.10",
        ],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_openapi_dataclass_inheritance_parent_default(output_file: Path) -> None:
    """Test dataclass field ordering fix when parent has default field."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "dataclass_inheritance_field_ordering.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "dataclass_inheritance_field_ordering_py310.py",
        extra_args=[
            "--output-model-type",
            "dataclasses.dataclass",
            "--target-python-version",
            "3.10",
        ],
    )


def test_main_openapi_pydantic_dataclass_inheritance_parent_default(output_file: Path) -> None:
    """Keep Pydantic dataclass field ordering aligned with standard dataclasses."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "dataclass_inheritance_field_ordering.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "pydantic_dataclass_inheritance_field_ordering_py310.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.dataclass",
            "--target-python-version",
            "3.10",
            "--disable-timestamp",
        ],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_openapi_keyword_only_dataclass(output_file: Path) -> None:
    """Test OpenAPI generation with keyword-only dataclass."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "inheritance.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="dataclass_keyword_only.py",
        extra_args=[
            "--output-model-type",
            "dataclasses.dataclass",
            "--keyword-only",
            "--target-python-version",
            "3.10",
        ],
    )


def test_main_openapi_dataclass_with_naive_datetime(capsys: pytest.CaptureFixture, output_file: Path) -> None:
    """Test OpenAPI generation with dataclass using naive datetime."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "inheritance.yaml",
        output_path=output_file,
        input_file_type="openapi",
        expected_exit=Exit.ERROR,
        extra_args=[
            "--output-model-type",
            "dataclasses.dataclass",
            "--output-datetime-class",
            "NaiveDatetime",
        ],
        capsys=capsys,
        expected_stderr_contains=(
            '`--output-datetime-class` only allows "datetime" for `--output-model-type` dataclasses.dataclass'
        ),
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_openapi_keyword_only_msgspec(min_version: str, output_file: Path) -> None:
    """Test OpenAPI generation with keyword-only msgspec."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "inheritance.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="msgspec_keyword_only.py",
        extra_args=["--output-model-type", "msgspec.Struct", "--keyword-only", "--target-python-version", min_version],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_openapi_keyword_only_msgspec_with_extra_data(min_version: str, output_file: Path) -> None:
    """Test OpenAPI generation with keyword-only msgspec and extra data."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "inheritance.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="msgspec_keyword_only_omit_defaults.py",
        extra_args=[
            "--output-model-type",
            "msgspec.Struct",
            "--keyword-only",
            "--target-python-version",
            min_version,
            "--extra-template-data",
            str(OPEN_API_DATA_PATH / "extra_data_msgspec.json"),
        ],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
def test_main_generate_openapi_keyword_only_msgspec_with_extra_data(tmp_path: Path) -> None:
    """Test OpenAPI generation with keyword-only msgspec using generate function."""
    extra_data = json.loads((OPEN_API_DATA_PATH / "extra_data_msgspec.json").read_text())
    output_file: Path = tmp_path / "output.py"
    generate(
        input_=OPEN_API_DATA_PATH / "inheritance.yaml",
        output=output_file,
        input_file_type=InputFileType.OpenAPI,
        output_model_type=DataModelType.MsgspecStruct,
        keyword_only=True,
        target_python_version=PythonVersionMin,
        extra_template_data=defaultdict(dict, extra_data),
        # Following values are defaults in the CLI, but not in the API
        openapi_scopes=[OpenAPIScope.Schemas],
        # Following values are implied by `msgspec.Struct` in the CLI
        use_annotated=True,
        field_constraints=True,
    )
    assert_file_content(output_file, "msgspec_keyword_only_omit_defaults.py")


@pytest.mark.skipif(
    black.__version__.split(".")[0] < "22",
    reason="Installed black doesn't support Python version 3.10",
)
@MSGSPEC_LEGACY_BLACK_SKIP
def test_main_openapi_msgspec_use_union_operator(output_file: Path) -> None:
    """Test msgspec Struct generation with union operator (Python 3.10+)."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "nullable.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="msgspec_use_union_operator.py",
        extra_args=[
            "--output-model-type",
            "msgspec.Struct",
            "--use-union-operator",
            "--target-python-version",
            "3.10",
        ],
    )


@MSGSPEC_LEGACY_BLACK_SKIP
def test_main_openapi_msgspec_anyof(min_version: str, output_file: Path) -> None:
    """Test msgspec Struct generation with anyOf fields."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "anyof.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="msgspec_anyof.py",
        extra_args=[
            "--output-model-type",
            "msgspec.Struct",
            "--target-python-version",
            min_version,
        ],
    )


@LEGACY_BLACK_SKIP
def test_main_openapi_msgspec_oneof_with_null(output_file: Path) -> None:
    """Test msgspec Struct generation with oneOf containing null type."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "msgspec_oneof_with_null.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="msgspec_oneof_with_null.py",
        extra_args=[
            "--output-model-type",
            "msgspec.Struct",
        ],
    )


@LEGACY_BLACK_SKIP
def test_main_openapi_msgspec_oneof_with_null_union_operator(output_file: Path) -> None:
    """Test msgspec Struct generation with oneOf containing null type using union operator."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "msgspec_oneof_with_null.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="msgspec_oneof_with_null_union_operator.py",
        extra_args=[
            "--output-model-type",
            "msgspec.Struct",
            "--use-union-operator",
        ],
    )


@MSGSPEC_LEGACY_BLACK_SKIP
def test_main_openapi_msgspec_no_use_union_operator(output_file: Path) -> None:
    """Test msgspec Struct generation without union operator (Union[X, Y] syntax)."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "nullable.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="msgspec_no_use_union_operator.py",
        extra_args=[
            "--output-model-type",
            "msgspec.Struct",
            "--no-use-union-operator",
            "--target-python-version",
            "3.10",
        ],
    )


@MSGSPEC_LEGACY_BLACK_SKIP
def test_main_openapi_msgspec_oneof_with_null_no_use_union_operator(output_file: Path) -> None:
    """Test msgspec Struct generation with oneOf containing null without union operator."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "msgspec_oneof_with_null.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="msgspec_oneof_with_null_no_use_union_operator.py",
        extra_args=[
            "--output-model-type",
            "msgspec.Struct",
            "--no-use-union-operator",
            "--target-python-version",
            "3.10",
        ],
    )


def test_main_openapi_msgspec_inline_const(output_file: Path) -> None:
    """Test msgspec Struct generation with inline const field."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "msgspec_inline_const.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="msgspec_inline_const.py",
        extra_args=[
            "--output-model-type",
            "msgspec.Struct",
        ],
    )


def test_main_openapi_referenced_default(output_file: Path) -> None:
    """Test OpenAPI generation with referenced default values."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "referenced_default.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="referenced_default.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )


def test_main_openapi_referenced_default_use_annotated(output_file: Path) -> None:
    """Test OpenAPI generation with referenced default values using --use-annotated."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "referenced_default.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="referenced_default_use_annotated.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", "--use-annotated"],
    )


def test_main_openapi_root_model_default_primitive(output_file: Path) -> None:
    """Test RootModel with primitive default value in union type."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "root_model_default_primitive.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="root_model_default_primitive.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )


@pytest.mark.cli_doc(
    options=["--parent-scoped-naming"],
    option_description="""Namespace models by their parent scope to avoid naming conflicts.

The `--parent-scoped-naming` flag prefixes model names with their parent scope
(operation/path/parameter) to prevent name collisions when the same model name
appears in different contexts within an OpenAPI specification.""",
    input_schema="openapi/duplicate_models2.yaml",
    cli_args=[
        "--parent-scoped-naming",
        "--use-operation-id-as-name",
        "--openapi-scopes",
        "paths",
        "schemas",
        "parameters",
    ],
    golden_output="openapi/duplicate_models2.py",
)
def test_duplicate_models(output_file: Path) -> None:
    """Namespace models by their parent scope to avoid naming conflicts.

    The `--parent-scoped-naming` flag prefixes model names with their parent scope
    (operation/path/parameter) to prevent name collisions when the same model name
    appears in different contexts within an OpenAPI specification.
    """
    with pytest.warns(DanglingRefWarning, match=r"Unresolved local \$ref"):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "duplicate_models2.yaml",
            output_path=output_file,
            input_file_type=None,
            assert_func=assert_file_content,
            expected_file="duplicate_models2.py",
            extra_args=[
                "--use-operation-id-as-name",
                "--openapi-scopes",
                "paths",
                "schemas",
                "parameters",
                "--output-model-type",
                "pydantic_v2.BaseModel",
                "--parent-scoped-naming",
            ],
        )


def test_main_openapi_shadowed_imports(output_file: Path) -> None:
    """Test OpenAPI generation with shadowed imports."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "shadowed_imports.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="shadowed_imports.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )


def test_main_openapi_shadowed_imports_base_and_fields(output_file: Path) -> None:
    """Test that aliased imports are applied to all fields, not just matching field names."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "shadowed_imports_base_and_fields.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="shadowed_imports_base_and_fields.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )


def test_main_openapi_shadowed_imports_base_and_fields_custom_base(output_file: Path) -> None:
    """Test that aliased imports are applied to custom base classes."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "shadowed_imports_base_and_fields.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="shadowed_imports_base_and_fields_custom_base.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", "--base-class", "mymodule.node.Node"],
    )


def test_main_openapi_extra_fields_forbid(output_file: Path) -> None:
    """Test OpenAPI generation with extra fields forbidden."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "additional_properties.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="additional_properties.py",
        extra_args=["--extra-fields", "forbid"],
    )


def test_main_openapi_same_name_objects(output_file: Path) -> None:
    """Test OpenAPI generation with same name objects."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "same_name_objects.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="same_name_objects.py",
    )


def test_main_openapi_type_alias(output_file: Path) -> None:
    """Test that TypeAliasType is generated for OpenAPI schemas for Python 3.10-3.11."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "type_alias.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="type_alias.py",
        extra_args=["--use-type-alias"],
    )


@pytest.mark.skipif(
    version.parse(black.__version__) < version.parse("23.3.0"),
    reason="Installed black doesn't support the target python version",
)
def test_main_openapi_enum_literal_type_alias_property_ref(output_file: Path) -> None:
    """Ensure property-referenced enum schemas produce named literal aliases."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "enum_literal_type_alias_property_ref.json",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="enum_literal_type_alias_property_ref.py",
        extra_args=[
            "--target-python-version",
            "3.11",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-annotated",
            "--use-union-operator",
            "--enum-field-as-literal",
            "all",
            "--use-type-alias",
        ],
    )


@pytest.mark.skipif(
    int(black.__version__.split(".")[0]) < 23,
    reason="Installed black doesn't support the new 'type' statement",
)
def test_main_openapi_type_alias_py312(output_file: Path) -> None:
    """Test that type statement syntax is generated for OpenAPI schemas with Python 3.12+ and Pydantic v2."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "type_alias.yaml",
        output_path=output_file,
        input_file_type="openapi",
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
    int(black.__version__.split(".")[0]) < 23,
    reason="Installed black doesn't support the target python version",
)
def test_main_openapi_type_alias_mutual_recursive_py311(output_file: Path) -> None:
    """Test mutual recursive type aliases render with quoted forward refs on Python 3.11."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "type_alias_mutual_recursive.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="type_alias_mutual_recursive.py",
        extra_args=[
            "--use-type-alias",
            "--target-python-version",
            "3.11",
            "--output-model-type",
            "pydantic_v2.BaseModel",
        ],
    )


@pytest.mark.skipif(
    int(black.__version__.split(".")[0]) < 23,
    reason="Installed black doesn't support the target python version",
)
def test_main_openapi_type_alias_mutual_recursive_typealiastype_py311(output_file: Path) -> None:
    """Test mutual recursive type aliases render with quoted forward refs for TypeAliasType on Python 3.11."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "type_alias_mutual_recursive.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="msgspec_mutual_type_alias.py",
        extra_args=[
            "--use-type-alias",
            "--target-python-version",
            "3.11",
            "--output-model-type",
            "msgspec.Struct",
        ],
    )


@pytest.mark.skipif(
    int(black.__version__.split(".")[0]) < 23,
    reason="Installed black doesn't support the target python version",
)
def test_main_openapi_type_alias_recursive_py311(output_file: Path) -> None:
    """Test recursive type aliases render with quoted self references on Python 3.11."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "type_alias_recursive.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="type_alias_recursive_py311.py",
        extra_args=[
            "--use-type-alias",
            "--target-python-version",
            "3.11",
            "--output-model-type",
            "pydantic_v2.BaseModel",
        ],
    )


@pytest.mark.skipif(
    int(black.__version__.split(".")[0]) < 23,
    reason="Installed black doesn't support the new 'type' statement",
)
def test_main_openapi_type_alias_recursive_py312(output_file: Path) -> None:
    """
    Test that handling of type aliases work as expected for recursive types.

    NOTE: applied to python 3.12--14
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "type_alias_recursive.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="type_alias_recursive_py312.py",
        extra_args=[
            "--use-type-alias",
            "--target-python-version",
            "3.12",
            "--use-standard-collections",
            "--use-union-operator",
            "--output-model-type",
            "pydantic_v2.BaseModel",
        ],
    )


def test_main_openapi_type_alias_recursive(output_file: Path) -> None:
    """Test recursive type aliases with proper forward reference quoting."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "type_alias_recursive.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="type_alias_recursive.py",
        extra_args=["--use-type-alias"],
    )


def test_main_openapi_type_alias_recursive_pydantic_v2(output_file: Path) -> None:
    """Test recursive RootModel with forward references in Pydantic v2.

    Without --use-type-alias, recursive schemas generate RootModel classes.
    Forward references in the generic parameter must be quoted to avoid
    NameError at class definition time.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "type_alias_recursive.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="type_alias_recursive_pydantic_v2.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
        ],
    )


def test_main_openapi_type_alias_cross_module_collision_a(output_file: Path) -> None:
    """Test TypeAlias generation for module A in cross-module collision scenario."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "type_alias_cross_module_collision" / "a.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="type_alias_cross_module_collision_a.py",
        extra_args=[
            "--use-type-alias",
            "--target-python-version",
            "3.10",
        ],
    )


def test_main_openapi_type_alias_cross_module_collision_b(output_file: Path) -> None:
    """Test TypeAlias generation for module B with self-referential forward reference."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "type_alias_cross_module_collision" / "b.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="type_alias_cross_module_collision_b.py",
        extra_args=[
            "--use-type-alias",
            "--target-python-version",
            "3.10",
        ],
    )


def test_main_openapi_type_alias_forward_ref_multiple(output_file: Path) -> None:
    """Test TypeAlias with multiple forward references that require quoting."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "type_alias_forward_ref_multiple.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="type_alias_forward_ref_multiple.py",
        extra_args=[
            "--use-type-alias",
            "--target-python-version",
            "3.10",
        ],
    )


def test_main_openapi_byte_format(output_file: Path) -> None:
    """Test OpenAPI generation with byte format."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "byte_format.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="byte_format.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )


def test_main_openapi_type_mappings_byte_to_binary(output_file: Path) -> None:
    """Test mapping OpenAPI byte format to binary preserves base64 encoding."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "type_mappings_byte_to_binary.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="type_mappings_byte_to_binary.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--type-mappings",
            "string+byte=binary",
        ],
    )


def test_main_openapi_unquoted_null(output_file: Path) -> None:
    """Test OpenAPI generation with unquoted null values."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "unquoted_null.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="unquoted_null.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )


def test_main_openapi_webhooks(output_file: Path) -> None:
    """Test OpenAPI generation with webhooks scope."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "webhooks.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        extra_args=["--openapi-scopes", "schemas", "webhooks"],
    )


def test_main_openapi_item_schema(output_file: Path) -> None:
    """Test OpenAPI 3.2 itemSchema in media type objects."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "item_schema.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="item_schema.py",
        extra_args=[
            "--disable-timestamp",
            "--openapi-scopes",
            "schemas",
            "paths",
            "parameters",
            "requestbodies",
        ],
    )


def test_main_openapi_item_schema_requires_32(output_file: Path) -> None:
    """Test OpenAPI 3.1 ignores OpenAPI 3.2 itemSchema in media type objects."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "item_schema_31.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="item_schema_31.py",
        extra_args=[
            "--disable-timestamp",
            "--openapi-scopes",
            "schemas",
            "paths",
            "parameters",
            "requestbodies",
        ],
    )


def test_main_openapi_querystring_parameter_requires_32(output_file: Path) -> None:
    """Test OpenAPI 3.1 ignores OpenAPI 3.2 querystring parameters."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "querystring_parameter_31.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="querystring_parameter_31.py",
        extra_args=[
            "--disable-timestamp",
            "--openapi-scopes",
            "schemas",
            "paths",
            "parameters",
        ],
    )


def test_main_openapi_non_operations_and_security(output_file: Path) -> None:
    """Test OpenAPI generation with non-operation fields and security inheritance."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "non_operations_and_security.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        extra_args=["--openapi-scopes", "schemas", "paths", "webhooks"],
    )


def test_generate_openapi_keeps_referenced_path_item_original_unchanged(output_file: Path) -> None:
    """Keep cached referenced OpenAPI path items unchanged while inheriting operation metadata."""
    input_path = OPEN_API_DATA_PATH / "referenced_path_item_mutation_guard" / "openapi.yaml"
    path_item_path = OPEN_API_DATA_PATH / "referenced_path_item_mutation_guard" / "path-item.yml"
    cached_path_item = load_data_from_path(path_item_path.resolve(), "utf-8")

    run_generate_file_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type=InputFileType.OpenAPI,
        assert_func=assert_file_content,
        expected_file="referenced_path_item_mutation_guard.py",
        disable_timestamp=True,
        openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Paths],
        unchanged_inputs={"cached path-item.yml": cached_path_item},
    )


def test_main_openapi_webhooks_with_parameters(output_file: Path) -> None:
    """Test OpenAPI generation with webhook-level and operation-level parameters."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "webhooks_with_parameters.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        extra_args=["--openapi-scopes", "schemas", "webhooks", "parameters"],
    )


def test_webhooks_ref_with_external_schema(output_file: Path) -> None:
    """Test OpenAPI generation with $ref to external webhook file containing relative schema refs."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "webhooks_ref_with_external_schema" / "openapi.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="webhooks_ref_with_external_schema.py",
        extra_args=["--openapi-scopes", "schemas", "webhooks"],
    )


def test_main_openapi_external_ref_with_transitive_local_ref(output_file: Path) -> None:
    """Test OpenAPI generation with external ref that has transitive local refs."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "external_ref_with_transitive_local_ref" / "openapi.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="external_ref_with_transitive_local_ref/output.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )


def _assert_external_ref_mapping_cli_parse_error(
    capsys: pytest.CaptureFixture[str],
    mapping: str,
    expected_message: str,
) -> None:
    run_main_with_system_exit(
        [
            "--input",
            str(EXTERNAL_REF_MAPPING_DATA_PATH / "api.yaml"),
            "--input-file-type",
            "openapi",
            "--external-ref-mapping",
            mapping,
        ],
        expected_code=2,
        capsys=capsys,
        expected_stderr_contains=expected_message,
    )


def _assert_external_ref_mapping_pyproject_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mapping: str,
    expected_message: str,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        f"""\
[tool.datamodel-codegen]
external-ref-mapping = ["{mapping}"]
"""
    )
    monkeypatch.chdir(tmp_path)
    run_main_with_args(
        [
            "--input",
            str(EXTERNAL_REF_MAPPING_DATA_PATH / "api.yaml"),
            "--output",
            str(tmp_path / "output.py"),
            "--input-file-type",
            "openapi",
        ],
        expected_exit=Exit.ERROR,
    )
    assert_error_message(capsys, expected_message)


@pytest.mark.cli_doc(
    options=["--external-ref-mapping"],
    option_description="""Map external `$ref` files to Python packages.

Use `--external-ref-mapping FILE_PATH=PYTHON_PACKAGE` to import referenced models from an existing package,
instead of generating duplicate classes from external schema files.
""",
    input_schema="openapi/external_ref_mapping/api.yaml",
    cli_args=["--input-file-type", "openapi", "--external-ref-mapping", "common.yaml=mypackage.shared.models"],
    golden_output="main/openapi/external_ref_mapping.py",
)
def test_main_openapi_external_ref_mapping_basic(output_file: Path) -> None:
    """External refs produce imports, not class definitions."""
    run_main_and_assert(
        input_path=EXTERNAL_REF_MAPPING_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            "--external-ref-mapping",
            "common.yaml=mypackage.shared.models",
        ],
        assert_func=assert_file_content,
        expected_file="external_ref_mapping.py",
    )


def test_main_openapi_external_ref_mapping_nested_relative_ref(output_file: Path) -> None:
    """Mappings work for refs that are relative to nested external files."""
    run_main_and_assert(
        input_path=EXTERNAL_REF_MAPPING_DATA_PATH / "api_nested.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            "--external-ref-mapping",
            "common.yaml=mypackage.shared.models",
        ],
        assert_func=assert_file_content,
        expected_file="external_ref_mapping_nested.py",
    )


def test_main_openapi_external_ref_mapping_normalizes_imported_class_name(tmp_path: Path) -> None:
    """Mapped refs normalize schema keys to generated Python class names."""
    output_file = tmp_path / "output.py"
    generate(
        input_=EXTERNAL_REF_MAPPING_DATA_PATH / "api_normalized.yaml",
        input_file_type=InputFileType.OpenAPI,
        output=output_file,
        external_ref_mapping={"common_normalized.yaml": "mypackage.shared.models"},
    )
    assert_file_content(output_file, "external_ref_mapping_normalized.py")


def test_main_openapi_external_ref_mapping_file_uri(tmp_path: Path) -> None:
    """Mappings accept file URI keys and refs."""
    common_uri = (EXTERNAL_REF_MAPPING_DATA_PATH / "common.yaml").resolve().as_uri()
    input_path = tmp_path / "api_file_uri.yaml"
    input_path.write_text(
        (EXTERNAL_REF_MAPPING_DATA_PATH / "api_file_uri_template.yaml")
        .read_text()
        .replace("__COMMON_URI__", common_uri)
    )
    output_file = tmp_path / "output.py"
    generate(
        input_=input_path,
        input_file_type=InputFileType.OpenAPI,
        output=output_file,
        external_ref_mapping={common_uri: "mypackage.shared.models"},
    )
    assert_file_content(output_file, "external_ref_mapping_file_uri.py")


def test_main_openapi_external_ref_mapping_absolute_path_ref(tmp_path: Path) -> None:
    """Mappings match absolute-path refs to external schemas."""
    common_path = str((EXTERNAL_REF_MAPPING_DATA_PATH / "common.yaml").resolve())
    input_path = tmp_path / "api_absolute_path.yaml"
    input_path.write_text(
        (EXTERNAL_REF_MAPPING_DATA_PATH / "api_absolute_path_template.yaml")
        .read_text()
        .replace("__COMMON_ABSOLUTE_PATH__", common_path)
    )
    output_file = tmp_path / "output.py"
    generate(
        input_=input_path,
        input_file_type=InputFileType.OpenAPI,
        output=output_file,
        external_ref_mapping={common_path: "mypackage.shared.models"},
    )
    assert_file_content(output_file, "external_ref_mapping_absolute_path.py")


def test_main_openapi_external_ref_mapping_local_ref_unchanged(tmp_path: Path) -> None:
    """Local refs remain unchanged when external mapping is configured."""
    output_file = tmp_path / "output.py"
    generate(
        input_=EXTERNAL_REF_MAPPING_DATA_PATH / "api_local_ref.yaml",
        input_file_type=InputFileType.OpenAPI,
        output=output_file,
        external_ref_mapping={"common.yaml": "mypackage.shared.models"},
    )
    assert_file_content(output_file, "external_ref_mapping_local_ref_unchanged.py")


def test_main_openapi_external_ref_mapping_ref_without_fragment_errors(tmp_path: Path) -> None:
    """Refs without a fragment remain unsupported and fail clearly."""
    output_file = tmp_path / "output.py"
    with pytest.raises(Exception, match="A Parser can not resolve classes"):
        generate(
            input_=EXTERNAL_REF_MAPPING_DATA_PATH / "api_no_fragment.yaml",
            input_file_type=InputFileType.OpenAPI,
            output=output_file,
            external_ref_mapping={"common.yaml": "mypackage.shared.models"},
        )


def test_main_openapi_external_ref_mapping_no_duplicate_classes(tmp_path: Path) -> None:
    """When mapping is active, the external file's classes should not be generated."""
    output_file = tmp_path / "output.py"
    generate(
        input_=EXTERNAL_REF_MAPPING_DATA_PATH / "api.yaml",
        input_file_type=InputFileType.OpenAPI,
        output=output_file,
        external_ref_mapping={"common.yaml": "mypackage.shared.models"},
    )
    assert_file_content(output_file, "external_ref_mapping.py")


def test_main_openapi_external_ref_mapping_without_flag_generates_classes(tmp_path: Path) -> None:
    """Without the flag, external refs generate classes (regression check)."""
    output_file = tmp_path / "output.py"
    generate(
        input_=EXTERNAL_REF_MAPPING_DATA_PATH / "api.yaml",
        input_file_type=InputFileType.OpenAPI,
        output=output_file,
    )
    assert_file_content(output_file, "external_ref_mapping_without_flag.py")


def test_main_openapi_external_ref_mapping_invalid_format(capsys: pytest.CaptureFixture[str]) -> None:
    """Invalid format (no equals sign) produces a clear error."""
    _assert_external_ref_mapping_cli_parse_error(capsys, "no-equals-sign", "Invalid --external-ref-mapping format")


@pytest.mark.parametrize("mapping", ["=mypackage.shared.models", "common.yaml="])
def test_main_openapi_external_ref_mapping_invalid_empty_part(
    capsys: pytest.CaptureFixture[str],
    mapping: str,
) -> None:
    """Empty file path or package in mapping produces a clear error."""
    _assert_external_ref_mapping_cli_parse_error(
        capsys,
        mapping,
        "Both FILE_PATH and PYTHON_PACKAGE must be non-empty.",
    )


def test_main_openapi_external_ref_mapping_invalid_format_in_pyproject(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid pyproject external-ref-mapping format returns Exit.ERROR."""
    _assert_external_ref_mapping_pyproject_error(
        tmp_path,
        monkeypatch,
        capsys,
        "no-equals-sign",
        "Invalid --external-ref-mapping format",
    )


@pytest.mark.parametrize("mapping", ["=mypackage.shared.models", "common.yaml="])
def test_main_openapi_external_ref_mapping_invalid_empty_part_in_pyproject(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    mapping: str,
) -> None:
    """Empty file path or package in pyproject mapping returns Exit.ERROR."""
    _assert_external_ref_mapping_pyproject_error(
        tmp_path,
        monkeypatch,
        capsys,
        mapping,
        "Both FILE_PATH and PYTHON_PACKAGE must be non-empty.",
    )


def test_main_openapi_external_ref_mapping_programmatic_api(tmp_path: Path) -> None:
    """Test using GenerateConfig with external_ref_mapping."""
    output_file = tmp_path / "output.py"
    config = GenerateConfig(
        input_file_type=InputFileType.OpenAPI,
        output=output_file,
        external_ref_mapping={"common.yaml": "mypackage.shared.models"},
    )
    generate(
        input_=EXTERNAL_REF_MAPPING_DATA_PATH / "api.yaml",
        config=config,
    )
    assert_file_content(output_file, "external_ref_mapping.py")


def test_main_openapi_namespace_subns_ref(output_dir: Path) -> None:
    """Test OpenAPI generation with namespaced schema referencing subnamespace.

    Regression test for issue #2366: When a schema with a dot-delimited name
    (e.g., ns.wrapper) references another schema in a subnamespace
    (e.g., ns.subns.item), the generated import should be "from . import subns"
    (same package) instead of "from .. import subns" (parent package).
    """
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "namespace_subns_ref.json",
            output_path=output_dir,
            expected_directory=EXPECTED_OPENAPI_PATH / "namespace_subns_ref",
        )


def test_main_openapi_read_only_write_only_default(output_file: Path) -> None:
    """Test readOnly/writeOnly default: base model only."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "read_only_write_only.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="read_only_write_only_default.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )


@pytest.mark.cli_doc(
    options=["--read-only-write-only-model-type"],
    option_description="""Generate separate request and response models for readOnly/writeOnly fields.

The `--read-only-write-only-model-type` option controls how models with readOnly or writeOnly
properties are generated. The 'request-response' mode creates separate Request and Response
variants for each schema that contains readOnly or writeOnly fields, allowing proper type
validation for API requests and responses without a shared base model.""",
    input_schema="openapi/read_only_write_only.yaml",
    cli_args=["--output-model-type", "pydantic_v2.BaseModel", "--read-only-write-only-model-type", "request-response"],
    golden_output="openapi/read_only_write_only_request_response.py",
)
def test_main_openapi_read_only_write_only_request_response(output_file: Path) -> None:
    """Generate separate request and response models for readOnly/writeOnly fields.

    The `--read-only-write-only-model-type` option controls how models with readOnly or writeOnly
    properties are generated. The 'request-response' mode creates separate Request and Response
    variants for each schema that contains readOnly or writeOnly fields, allowing proper type
    validation for API requests and responses without a shared base model.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "read_only_write_only.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="read_only_write_only_request_response.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-only-write-only-model-type",
            "request-response",
        ],
    )


def test_main_openapi_read_only_write_only_all(output_file: Path) -> None:
    """Test readOnly/writeOnly all: Base + Request + Response models."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "read_only_write_only.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="read_only_write_only_all.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-only-write-only-model-type",
            "all",
        ],
    )


def test_main_openapi_read_only_write_only_allof(output_file: Path) -> None:
    """Test readOnly/writeOnly with allOf inheritance."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "read_only_write_only_allof.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="read_only_write_only_allof.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-only-write-only-model-type",
            "all",
        ],
    )


def test_main_openapi_read_only_write_only_allof_request_response(output_file: Path) -> None:
    """Test readOnly/writeOnly with allOf using request-response mode (no base model)."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "read_only_write_only_allof.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="read_only_write_only_allof_request_response.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-only-write-only-model-type",
            "request-response",
        ],
    )


def test_main_openapi_read_only_write_only_collision(output_file: Path) -> None:
    """Test readOnly/writeOnly with name collision (UserRequest already exists)."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "read_only_write_only_collision.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="read_only_write_only_collision.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-only-write-only-model-type",
            "all",
        ],
    )


def test_main_openapi_read_only_write_only_ref(output_file: Path) -> None:
    """Test readOnly/writeOnly on $ref target schema."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "read_only_write_only_ref.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="read_only_write_only_ref.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-only-write-only-model-type",
            "all",
        ],
    )


def test_main_openapi_read_only_write_only_allof_property_ref_runtime(output_file: Path) -> None:
    """Validate allOf readOnly/writeOnly generation when object properties contain refs."""
    generate(
        input_={
            "openapi": "3.0.0",
            "info": {"title": "Read Only Write Only AllOf Ref Runtime API", "version": "1.0"},
            "paths": {},
            "components": {
                "schemas": {
                    "Base": {
                        "type": "object",
                        "properties": {"base": {"type": "string"}},
                    },
                    "Child": {
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                    },
                    "Parent": {
                        "type": "object",
                        "allOf": [{"$ref": "#/components/schemas/Base"}],
                        "properties": {
                            "child": {"$ref": "#/components/schemas/Child"},
                            "extra": {"type": "string", "writeOnly": True},
                        },
                    },
                }
            },
        },
        input_file_type=InputFileType.OpenAPI,
        output=output_file,
        output_model_type=DataModelType.PydanticV2BaseModel,
        read_only_write_only_model_type=ReadOnlyWriteOnlyModelType.All,
        disable_timestamp=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="output_read_only_write_only_allof_property_ref_runtime",
        model_name="Parent",
        valid_json='{"base":"b","child":{"value":"x"},"extra":"secret"}',
        invalid_json='{"base":"b","child":{"value":1}}',
        expected_error_type="string_type",
        expected_attribute_path=("child", "value"),
        expected_attribute_value="x",
    )


def test_main_openapi_read_only_write_only_double_collision(output_file: Path) -> None:
    """Test readOnly/writeOnly with double collision (UserRequest and UserRequestModel exist)."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "read_only_write_only_double_collision.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="read_only_write_only_double_collision.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-only-write-only-model-type",
            "all",
        ],
    )


def test_main_openapi_read_only_write_only_nested_allof(output_file: Path) -> None:
    """Test readOnly/writeOnly with nested allOf inheritance."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "read_only_write_only_nested_allof.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="read_only_write_only_nested_allof.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-only-write-only-model-type",
            "all",
        ],
    )


def test_main_openapi_read_only_write_only_union(output_file: Path) -> None:
    """Test readOnly/writeOnly with Union type field."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "read_only_write_only_union.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="read_only_write_only_union.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-only-write-only-model-type",
            "all",
        ],
    )


def test_main_openapi_read_only_write_only_url_ref(mock_httpx_get: HttpxGetMockFactory, output_file: Path) -> None:
    """Test readOnly/writeOnly with URL $ref to external schema."""
    httpx_get_mock = mock_httpx_get(
        MockHttpxResponse(
            "https://example.com/common.yaml", OPEN_API_DATA_PATH / "read_only_write_only_url_ref_remote.yaml"
        )
    )

    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "read_only_write_only_url_ref.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="read_only_write_only_url_ref.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-only-write-only-model-type",
            "all",
            "--allow-remote-refs",
        ],
    )
    assert_httpx_get_kwargs(httpx_get_mock, expected_url="https://example.com/common.yaml")


def test_main_openapi_read_only_write_only_allof_url_ref(
    mock_httpx_get: HttpxGetMockFactory, output_file: Path
) -> None:
    """Test readOnly/writeOnly with allOf that references external URL schema."""
    httpx_get_mock = mock_httpx_get(
        MockHttpxResponse(
            "https://example.com/common.yaml",
            OPEN_API_DATA_PATH / "read_only_write_only_allof_url_ref_remote.yaml",
        )
    )

    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "read_only_write_only_allof_url_ref.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="read_only_write_only_allof_url_ref.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-only-write-only-model-type",
            "all",
            "--allow-remote-refs",
        ],
    )
    assert_httpx_get_kwargs(httpx_get_mock, expected_url="https://example.com/common.yaml")


def test_main_openapi_read_only_write_only_allof_order(output_file: Path) -> None:
    """Test readOnly/writeOnly with allOf where child is listed before parent in schema."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "read_only_write_only_allof_order.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="read_only_write_only_allof_order.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-only-write-only-model-type",
            "all",
        ],
    )


def test_main_openapi_read_only_write_only_nested_allof_order(output_file: Path) -> None:
    """Test readOnly/writeOnly with nested allOf where models are listed in reverse order."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "read_only_write_only_nested_allof_order.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="read_only_write_only_nested_allof_order.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-only-write-only-model-type",
            "all",
        ],
    )


def test_main_openapi_read_only_write_only_allof_required_only(output_file: Path) -> None:
    """Test readOnly/writeOnly with allOf containing item with only 'required' (no ref, no properties)."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "read_only_write_only_allof_required_only.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="read_only_write_only_allof_required_only.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-only-write-only-model-type",
            "all",
        ],
        force_exec_validation=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="read_only_write_only_allof_required_only",
        model_name="ChildRequest",
        valid_json='{"id":1}',
        invalid_json="{}",
        expected_error_type="missing",
        expected_attribute_path=("id",),
        expected_attribute_value=1,
    )


def test_main_openapi_read_only_write_only_mixed(output_file: Path) -> None:
    """Test request-response mode generates base models for schemas without readOnly/writeOnly."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "read_only_write_only_mixed.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="read_only_write_only_mixed.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-only-write-only-model-type",
            "request-response",
        ],
    )


def test_main_openapi_read_only_write_only_anyof(output_file: Path) -> None:
    """Test readOnly/writeOnly detection in anyOf and oneOf compositions."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "read_only_write_only_anyof.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="read_only_write_only_anyof.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-only-write-only-model-type",
            "all",
        ],
    )


def test_main_openapi_read_only_write_only_duplicate_allof_ref(output_file: Path) -> None:
    """Test readOnly/writeOnly with duplicate $ref in allOf."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "read_only_write_only_duplicate_allof_ref.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="read_only_write_only_duplicate_allof_ref.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-only-write-only-model-type",
            "all",
        ],
    )


def test_main_openapi_read_only_write_only_ref_with_desc(output_file: Path) -> None:
    """Test readOnly/writeOnly on $ref with description (JsonSchemaObject with ref)."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "read_only_write_only_ref_with_desc.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="read_only_write_only_ref_with_desc.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-only-write-only-model-type",
            "all",
        ],
    )


def test_main_openapi_read_only_write_only_shared_base_ref(output_file: Path) -> None:
    """Test readOnly/writeOnly with diamond inheritance (shared base via multiple paths)."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "read_only_write_only_shared_base_ref.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="read_only_write_only_shared_base_ref.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-only-write-only-model-type",
            "all",
        ],
    )


def test_main_openapi_read_only_write_only_empty_base(output_file: Path) -> None:
    """Test readOnly/writeOnly with empty base class (no fields)."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "read_only_write_only_empty_base.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="read_only_write_only_empty_base.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-only-write-only-model-type",
            "all",
        ],
        force_exec_validation=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="read_only_write_only_empty_base",
        model_name="ChildRequest",
        valid_json='{"base_field":"ok"}',
        invalid_json="{}",
        expected_error_type="missing",
        expected_attribute_path=("base_field",),
        expected_attribute_value="ok",
    )


def test_main_openapi_read_only_write_only_ref_request_response(output_file: Path) -> None:
    """Test readOnly/writeOnly with $ref in request-response mode (issue #2940).

    Every split schema generates both variants, including empty one-sided and recursive models.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "read_only_write_only_ref_request_response.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="read_only_write_only_ref_request_response.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-only-write-only-model-type",
            "request-response",
        ],
        force_exec_validation=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="read_only_write_only_ref_request_response_recursive_read",
        model_name="RecursiveOnlyReadOnlyResponse",
        valid_json='{"id":1,"child":{"id":2}}',
        invalid_json='{"id":1,"child":{"id":"bad"}}',
        expected_error_type="int_parsing",
        expected_attribute_path=("child", "id"),
        expected_attribute_value=2,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="read_only_write_only_ref_request_response_recursive_write",
        model_name="RecursiveOnlyWriteOnlyRequest",
        valid_json='{"secret":"one","child":{"secret":"two"}}',
        invalid_json='{"secret":"one","child":{"secret":1}}',
        expected_error_type="string_type",
        expected_attribute_path=("child", "secret"),
        expected_attribute_value="two",
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
def test_main_openapi_read_only_write_only_variant_graph(
    output_file: Path,
    output_model_type: str,
    expected_name: str,
) -> None:
    """Resolve request/response variants through every supported reference shape."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "read_only_write_only_variant_graph.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=f"output_model_types/read_only_write_only_variant_graph_{expected_name}.py",
        extra_args=[
            *BACKEND_GOLDEN_TARGET_ARGS,
            "--formatters",
            "builtin",
            "--output-model-type",
            output_model_type,
            "--read-only-write-only-model-type",
            "request-response",
            "--openapi-scopes",
            "schemas",
            "--class-name-prefix",
            "Api",
            "--class-name-suffix",
            "Model",
            "--disable-timestamp",
        ],
        force_exec_validation=True,
    )

    match DataModelType(output_model_type):
        case DataModelType.PydanticV2BaseModel | DataModelType.PydanticV2Dataclass | DataModelType.DataclassesDataclass:
            pass
        case _:
            return

    graph_request = {
        "alias": {},
        "items": [{}],
        "values": {"first": {}},
        "keyedValues": {"alpha": {}},
    }
    graph_response = {
        "alias": {"id": 1},
        "items": [{"id": 2}],
        "values": {"first": {"id": 3}},
        "keyedValues": {"alpha": {"id": 4}},
    }
    runtime_cases = (
        (
            "graph_request",
            "ApiForwardGraphWrapperRequestModel",
            graph_request,
            {field: value for field, value in graph_request.items() if field != "alias"},
            "missing",
            (),
            None,
        ),
        (
            "graph_response",
            "ApiForwardGraphWrapperResponseModel",
            graph_response,
            {**graph_response, "alias": {"id": "bad"}},
            "int_parsing",
            (),
            None,
        ),
        (
            "direct_inline",
            "ApiDirectInlineContainerResponseModel",
            {"child": {"id": 1, "label": "direct"}},
            {"child": {"id": 1, "label": 2}},
            "string_type",
            ("child", "label"),
            "direct",
        ),
        (
            "direct_inline_request",
            "ApiDirectInlineContainerRequestModel",
            {"child": {"label": "direct-request"}},
            {"child": {"label": 2}},
            "string_type",
            ("child", "label"),
            "direct-request",
        ),
        (
            "forward_inline",
            "ApiForwardInlineWrapperResponseModel",
            {"container": {"child": {"id": 2, "label": "forward"}}},
            {"container": {"child": {"id": "bad", "label": "forward"}}},
            "int_parsing",
            ("container", "child", "label"),
            "forward",
        ),
        (
            "forward_inline_request",
            "ApiForwardInlineWrapperRequestModel",
            {"container": {"child": {"label": "forward-request"}}},
            {"container": {"child": {"label": 2}}},
            "string_type",
            ("container", "child", "label"),
            "forward-request",
        ),
        (
            "root_inline_array",
            "ApiForwardRootInlineArrayWrapperResponseModel",
            {"items": [{"id": 3, "label": "array"}]},
            {"items": [{"id": "bad", "label": "array"}]},
            "int_parsing",
            (),
            None,
        ),
        (
            "root_inline_array_request",
            "ApiForwardRootInlineArrayWrapperRequestModel",
            {"items": [{"label": "array-request"}]},
            {"items": [{"label": 2}]},
            "string_type",
            (),
            None,
        ),
        (
            "root_inline_union",
            "ApiForwardRootInlineUnionWrapperResponseModel",
            {"value": {"id": 4, "label": "union"}},
            {"value": {"id": "bad", "label": "union"}},
            "int_parsing",
            (),
            None,
        ),
        (
            "root_inline_union_request",
            "ApiForwardRootInlineUnionWrapperRequestModel",
            {"value": {"label": "union-request"}},
            {"value": {"label": 2}},
            "string_type",
            (),
            None,
        ),
        (
            "positive_scc",
            "ApiPositiveSccWrapperResponseModel",
            {"node": {"name": "a", "b": {"id": 5, "label": "b", "a": {"name": "nested"}}}},
            {"node": {"name": "a", "b": {"id": "bad", "label": "b"}}},
            "int_parsing",
            ("node", "b", "id"),
            5,
        ),
        (
            "positive_scc_request",
            "ApiPositiveSccWrapperRequestModel",
            {"node": {"name": "a", "b": {"label": "request-b", "a": {"name": "nested"}}}},
            {"node": {"name": "a", "b": {"label": 2}}},
            "string_type",
            ("node", "b", "label"),
            "request-b",
        ),
        (
            "negative_scc",
            "ApiNegativeSccWrapperModel",
            {"node": {"name": "a", "b": {"label": "b", "a": {"name": "nested"}}}},
            {"node": {"name": 1}},
            "string_type",
            ("node", "b", "label"),
            "b",
        ),
        (
            "source_name_collision",
            "ApiCollisionWrapperRequestModel",
            {"user": {"name": "Ada"}, "sourceRequest": {"legacy": True}},
            {"user": {"name": 1}, "sourceRequest": {"legacy": True}},
            "string_type",
            ("user", "name"),
            "Ada",
        ),
        (
            "source_name_collision_response",
            "ApiCollisionWrapperResponseModel",
            {"user": {"id": 1, "name": "Ada"}, "sourceRequest": {"legacy": True}},
            {"user": {"id": "bad", "name": "Ada"}, "sourceRequest": {"legacy": True}},
            "int_parsing",
            ("user", "id"),
            1,
        ),
    )
    for (
        module_suffix,
        model_name,
        valid_payload,
        invalid_payload,
        expected_error_type,
        expected_attribute_path,
        expected_attribute_value,
    ) in runtime_cases:
        assert_generated_model_json_validation(
            output_file,
            module_name=f"read_only_write_only_variant_graph_{expected_name}_{module_suffix}",
            model_name=model_name,
            valid_json=json.dumps(valid_payload),
            invalid_json=json.dumps(invalid_payload),
            expected_error_type=expected_error_type,
            expected_attribute_path=expected_attribute_path,
            expected_attribute_value=expected_attribute_value,
        )

    assert_generated_model_json_validation(
        output_file,
        module_name=f"read_only_write_only_variant_graph_{expected_name}_dict_key",
        model_name="ApiForwardDictKeyWrapperResponseModel",
        valid_json='{"values":{}}',
        invalid_json='{"values":[]}',
        expected_error_type="dict_type",
    )
    if DataModelType(output_model_type) is DataModelType.PydanticV2BaseModel:
        assert_generated_model_json_validation(
            output_file,
            module_name="read_only_write_only_variant_graph_forward_dict_key_root",
            model_name="ApiForwardDictKeyMapResponseModel",
            valid_json="{}",
            invalid_json="[]",
            expected_error_type="dict_type",
        )


def test_main_openapi_read_only_write_only_variant_graph_schema_validators(
    output_file: Path,
) -> None:
    """Apply both JSON Schema conditional branches from the comprehensive fixture."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "read_only_write_only_variant_graph.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="read_only_write_only_variant_graph_schema_validators.py",
        extra_args=[
            *BACKEND_GOLDEN_TARGET_ARGS,
            "--formatters",
            "builtin",
            "--output-model-type",
            DataModelType.PydanticV2BaseModel.value,
            "--generate-schema-validators",
            "--read-only-write-only-model-type",
            "request-response",
            "--openapi-scopes",
            "paths",
            "--class-name-prefix",
            "Api",
            "--class-name-suffix",
            "Model",
            "--disable-timestamp",
        ],
        force_exec_validation=True,
    )
    for module_suffix, valid_json, invalid_json, expected_kind in (
        (
            "then",
            '{"payload":{"kind":"metric","metricLeaf":{}}}',
            '{"payload":{"kind":"metric"}}',
            "metric",
        ),
        (
            "else",
            '{"payload":{"kind":"note","noteLeaf":{}}}',
            '{"payload":{"kind":"note"}}',
            "note",
        ),
    ):
        assert_generated_model_json_validation(
            output_file,
            module_name=f"read_only_write_only_variant_graph_validator_{module_suffix}",
            model_name="ApiConditionalForwardWrapperRequestModel",
            valid_json=valid_json,
            invalid_json=invalid_json,
            expected_error_type="value_error",
            expected_attribute_path=("payload", "kind"),
            expected_attribute_value=expected_kind,
        )
    assert_generated_model_json_validation(
        output_file,
        module_name="read_only_write_only_variant_graph_validator_response_ref",
        model_name="ApiConditionalForwardWrapperResponseModel",
        valid_json='{"payload":{"kind":"metric","metricLeaf":{"id":1}}}',
        invalid_json='{"payload":{"kind":"metric","metricLeaf":{"id":"bad"}}}',
        expected_error_type="int_parsing",
        expected_attribute_path=("payload", "metricLeaf", "id"),
        expected_attribute_value=1,
    )
    assert_generated_model_json_invalid(
        output_file,
        module_name="read_only_write_only_variant_graph_validator_response_property_count",
        model_name="ApiConditionalForwardWrapperResponseModel",
        invalid_json='{"payload":{"kind":"metric","metricLeaf":{"id":1},"extra":true}}',
        expected_error_type="value_error",
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="read_only_write_only_variant_graph_validator_request_property_count",
        model_name="ApiConditionalForwardWrapperRequestModel",
        valid_json='{"payload":{"kind":"metric","metricLeaf":{}}}',
        invalid_json='{"payload":{"kind":"metric","metricLeaf":{},"extra":true}}',
        expected_error_type="value_error",
        expected_attribute_path=("payload", "kind"),
        expected_attribute_value="metric",
    )


def test_main_openapi_dot_notation_inheritance(output_dir: Path) -> None:
    """Test dot notation in schema names with inheritance."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "dot_notation_inheritance.yaml",
        output_path=output_dir,
        expected_directory=EXPECTED_OPENAPI_PATH / "dot_notation_inheritance",
        input_file_type="openapi",
    )


def test_main_openapi_dot_notation_deep_inheritance(output_dir: Path) -> None:
    """Test dot notation with deep inheritance from ancestor packages (issue #2039)."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "dot_notation_deep_inheritance.yaml",
        output_path=output_dir,
        expected_directory=EXPECTED_OPENAPI_PATH / "dot_notation_deep_inheritance",
        input_file_type="openapi",
    )


def test_main_openapi_dot_notation_root_package_inheritance(output_dir: Path) -> None:
    """Test dot notation with inheritance from a model declared in the root package.

    The root package is an ancestor of every nested module, so the base class must be
    imported as ``from ... import Animal`` rather than ``from ...Animal import Animal``.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "dot_notation_root_package_inheritance.yaml",
        output_path=output_dir,
        expected_directory=EXPECTED_OPENAPI_PATH / "dot_notation_root_package_inheritance",
        input_file_type="openapi",
        extra_args=["--disable-timestamp"],
        force_exec_validation=True,
        runtime_validation_module="v0.mammal.canine",
        runtime_validation_model_name="Puppy",
        runtime_validation_data={"species": "dog", "age_weeks": 8},
    )


def test_main_openapi_exact_imports_ancestor_package(output_dir: Path) -> None:
    """Test --use-exact-imports on fields typed by a model in an ancestor package.

    A model whose module path is a prefix of the current one lives in that package's
    ``__init__.py``, so it must stay ``from .. import Animal``. Turning it into
    ``from ..Animal import Animal`` points at a module that does not exist.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "exact_imports_ancestor_package.yaml",
        output_path=output_dir,
        expected_directory=EXPECTED_OPENAPI_PATH / "exact_imports_ancestor_package",
        input_file_type="openapi",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--target-python-version",
            "3.10",
            "--use-exact-imports",
            "--collapse-root-models",
            "--disable-timestamp",
        ],
        force_exec_validation=True,
        runtime_validation_module="v0.mammal.canine",
        runtime_validation_model_name="Puppy",
        runtime_validation_data={
            "animal": {"species": "dog"},
            "parent": {"animal": {"species": "dog"}, "tag": {"label": "pet"}},
            "friend": {"wingspan": 120},
            "tag": {"label": "pet"},
            "tags": [{"label": "friend"}],
        },
    )


def test_main_openapi_strict_types_field_constraints_pydantic_v2(output_file: Path) -> None:
    """Test strict types with field constraints for pydantic v2 (issue #1884)."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "strict_types_field_constraints.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="strict_types_field_constraints_pydantic_v2.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--field-constraints",
            "--strict-types",
            "int",
            "float",
            "str",
        ],
    )


def test_main_openapi_strict_types_field_constraints_msgspec(output_file: Path) -> None:
    """Test strict types with field constraints for msgspec (issue #1884)."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "strict_types_field_constraints.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="strict_types_field_constraints_msgspec.py",
        extra_args=[
            "--output-model-type",
            "msgspec.Struct",
            "--field-constraints",
            "--strict-types",
            "int",
            "float",
            "str",
        ],
    )


def test_main_openapi_circular_imports_stripe_like(output_dir: Path) -> None:
    """Test that circular imports between root and submodules are resolved with _internal.py."""
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "circular_imports_stripe_like.yaml",
            output_path=output_dir,
            expected_directory=EXPECTED_OPENAPI_PATH / "circular_imports_stripe_like",
            input_file_type="openapi",
        )


def test_main_openapi_circular_imports_acyclic(output_dir: Path) -> None:
    """Test that acyclic dependencies do not create _internal.py."""
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "circular_imports_acyclic.yaml",
            output_path=output_dir,
            expected_directory=EXPECTED_OPENAPI_PATH / "circular_imports_acyclic",
            input_file_type="openapi",
        )


def test_main_openapi_circular_imports_class_conflict(output_dir: Path) -> None:
    """Test that class name conflicts in merged _internal.py are resolved with sequential renaming."""
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "circular_imports_class_conflict.yaml",
            output_path=output_dir,
            expected_directory=EXPECTED_OPENAPI_PATH / "circular_imports_class_conflict",
            input_file_type="openapi",
        )


def test_main_openapi_circular_imports_with_inheritance(output_dir: Path) -> None:
    """Test that circular imports with base class inheritance are resolved."""
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "circular_imports_with_inheritance.yaml",
            output_path=output_dir,
            expected_directory=EXPECTED_OPENAPI_PATH / "circular_imports_with_inheritance",
            input_file_type="openapi",
        )


def test_main_openapi_circular_imports_small_cycle(output_dir: Path) -> None:
    """Test that small 2-module cycles also create _internal.py."""
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "circular_imports_small_cycle.yaml",
            output_path=output_dir,
            expected_directory=EXPECTED_OPENAPI_PATH / "circular_imports_small_cycle",
            input_file_type="openapi",
        )


def test_main_openapi_circular_imports_different_prefixes(output_dir: Path) -> None:
    """Test circular imports with different module prefixes (tests LCP computation)."""
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "circular_imports_different_prefixes.yaml",
            output_path=output_dir,
            expected_directory=EXPECTED_OPENAPI_PATH / "circular_imports_different_prefixes",
            input_file_type="openapi",
        )


def test_main_openapi_circular_imports_mixed_prefixes(output_dir: Path) -> None:
    """Test circular imports with mixed common/different prefixes (tests LCP break branch)."""
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "circular_imports_mixed_prefixes.yaml",
            output_path=output_dir,
            expected_directory=EXPECTED_OPENAPI_PATH / "circular_imports_mixed_prefixes",
            input_file_type="openapi",
        )


def test_warning_empty_schemas_with_paths(tmp_path: Path) -> None:
    """Test warning when components/schemas is empty but paths exist."""
    openapi_file = tmp_path / "openapi.yaml"
    openapi_file.write_text("""
openapi: 3.1.0
info:
  title: Test
  version: '1'
paths:
  /test:
    get:
      responses:
        200:
          description: OK
""")

    with pytest.warns(UserWarning, match=r"No schemas found.*--openapi-scopes paths"), contextlib.suppress(Exception):
        generate(openapi_file)


def test_main_allof_enum_ref(output_file: Path) -> None:
    """Test OpenAPI generation with allOf referencing enum from another schema."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_enum_ref.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
    )


@pytest.mark.skipif(
    version.parse(pydantic.VERSION) < version.parse("2.0.0"),
    reason="Require Pydantic version 2.0.0 or later",
)
def test_main_openapi_allof_single_ref_inline(output_file: Path) -> None:
    """Test that single $ref in allOf inline property does not create wrapper class."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_single_ref_inline.yaml",
        output_path=output_file,
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--enum-field-as-literal",
            "all",
        ],
        assert_func=assert_file_content,
    )


@pytest.mark.skipif(
    version.parse(pydantic.VERSION) < version.parse("2.0.0"),
    reason="Require Pydantic version 2.0.0 or later",
)
def test_main_openapi_module_class_name_collision_pydantic_v2(output_dir: Path) -> None:
    """Test Issue #1994: module and class name collision (e.g., A.A schema)."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "module_class_name_collision" / "openapi.json",
        output_path=output_dir,
        expected_directory=EXPECTED_OPENAPI_PATH / "module_class_name_collision",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--openapi-scopes",
            "schemas",
            "--openapi-scopes",
            "paths",
        ],
    )


@pytest.mark.skipif(
    version.parse(pydantic.VERSION) < version.parse("2.0.0"),
    reason="Require Pydantic version 2.0.0 or later",
)
def test_main_openapi_module_class_name_collision_deep_pydantic_v2(output_dir: Path) -> None:
    """Test Issue #1994: deep module collision (e.g., A.B.B schema)."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "module_class_name_collision_deep" / "openapi.json",
        output_path=output_dir,
        expected_directory=EXPECTED_OPENAPI_PATH / "module_class_name_collision_deep",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--openapi-scopes",
            "schemas",
            "--openapi-scopes",
            "paths",
        ],
    )


@pytest.mark.skipif(
    version.parse(pydantic.VERSION) < version.parse("2.0.0"),
    reason="Require Pydantic version 2.0.0 or later",
)
def test_main_openapi_module_class_name_collision_exact_imports_pydantic_v2(output_dir: Path) -> None:
    """Test --use-exact-imports with module/class name collision."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "module_class_name_collision" / "openapi.json",
        output_path=output_dir,
        expected_directory=EXPECTED_OPENAPI_PATH / "module_class_name_collision_exact_imports",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--openapi-scopes",
            "schemas",
            "--openapi-scopes",
            "paths",
            "--use-exact-imports",
            "--disable-timestamp",
        ],
    )


@pytest.mark.skipif(
    version.parse(pydantic.VERSION) < version.parse("2.0.0"),
    reason="Require Pydantic version 2.0.0 or later",
)
def test_main_openapi_module_class_name_collision_deep_exact_imports_pydantic_v2(output_dir: Path) -> None:
    """Test --use-exact-imports with deep module/class name collision."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "module_class_name_collision_deep" / "openapi.json",
        output_path=output_dir,
        expected_directory=EXPECTED_OPENAPI_PATH / "module_class_name_collision_deep_exact_imports",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--openapi-scopes",
            "schemas",
            "--openapi-scopes",
            "paths",
            "--use-exact-imports",
            "--disable-timestamp",
        ],
    )


def test_main_nested_package_enum_default(output_dir: Path) -> None:
    """Test enum default values use short names in same module with nested package paths."""
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "nested_package_enum_default.json",
            output_path=output_dir,
            expected_directory=EXPECTED_OPENAPI_PATH / "nested_package_enum_default",
            extra_args=[
                "--output-model-type",
                "dataclasses.dataclass",
                "--set-default-enum-member",
            ],
        )


def test_main_openapi_x_enum_names(output_file: Path) -> None:
    """Test OpenAPI generation with x-enumNames extension (NSwag/NJsonSchema style)."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "x_enum_names.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="x_enum_names.py",
    )


def test_main_openapi_x_enum_descriptions_null(output_file: Path) -> None:
    """Treat null x-enum-descriptions entries as missing descriptions."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "x_enum_descriptions_null.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="x_enum_descriptions_null.py",
        extra_args=["--use-field-description"],
    )


def test_main_enum_builtin_conflict(output_file: Path) -> None:
    """Test enum member names that conflict with str methods get underscore suffix."""
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "enum_builtin_conflict.yaml",
            output_path=output_file,
            input_file_type="openapi",
            assert_func=assert_file_content,
            expected_file="enum_builtin_conflict.py",
            extra_args=["--use-subclass-enum"],
        )


@pytest.mark.parametrize(
    ("extra_args", "expected_file"),
    [
        ([], "builtin_type_field_names.py"),
        (["--no-use-union-operator"], "builtin_type_field_names_no_union_operator.py"),
    ],
)
def test_main_builtin_type_field_names(output_file: Path, extra_args: list[str], expected_file: str) -> None:
    """Test field names that conflict with Python builtin types get underscore suffix."""
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "builtin_type_field_names.yaml",
            output_path=output_file,
            input_file_type="openapi",
            assert_func=assert_file_content,
            expected_file=expected_file,
            extra_args=["--output-model-type", "pydantic_v2.BaseModel", *extra_args],
        )


@pytest.mark.parametrize(
    ("output_model", "expected_output"),
    [
        ("pydantic_v2.BaseModel", "unique_items_default_set_pydantic_v2.py"),
        ("dataclasses.dataclass", "unique_items_default_set_dataclass.py"),
        ("msgspec.Struct", "unique_items_default_set_msgspec.py"),
    ],
)
def test_main_unique_items_default_set(output_model: str, expected_output: str, output_file: Path) -> None:
    """Test --use-unique-items-as-set converts list defaults to set literals."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "unique_items_default_set.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=expected_output,
        extra_args=["--output-model-type", output_model, "--use-unique-items-as-set"],
    )


def test_main_openapi_null_only_enum(output_file: Path) -> None:
    """Test OpenAPI generation with enum containing only null value."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "null_only_enum.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="null_only_enum.py",
    )


@pytest.mark.cli_doc(
    options=["--use-status-code-in-response-name"],
    option_description="""Include HTTP status code in response model names.

The `--use-status-code-in-response-name` flag includes the HTTP status code
in generated response model class names. Instead of generating ambiguous names
like ResourceGetResponse, ResourceGetResponse1, ResourceGetResponse2, it generates
clear names like ResourceGetResponse200, ResourceGetResponse400, ResourceGetResponseDefault.""",
    input_schema="openapi/use_status_code_in_response_name.yaml",
    cli_args=["--use-status-code-in-response-name", "--openapi-scopes", "schemas", "paths"],
    golden_output="openapi/use_status_code_in_response_name.py",
)
def test_main_openapi_use_status_code_in_response_name(output_file: Path) -> None:
    """Include HTTP status code in response model names.

    The `--use-status-code-in-response-name` flag includes the HTTP status code
    in generated response model class names. Instead of generating ambiguous names
    like ResourceGetResponse, ResourceGetResponse1, ResourceGetResponse2, it generates
    clear names like ResourceGetResponse200, ResourceGetResponse400, ResourceGetResponseDefault.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "use_status_code_in_response_name.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="use_status_code_in_response_name.py",
        extra_args=["--use-status-code-in-response-name", "--openapi-scopes", "schemas", "paths"],
    )


@freeze_time(TIMESTAMP)
def test_main_openapi_request_bodies_scope(output_file: Path) -> None:
    """Test generating models from components/requestBodies using requestbodies scope."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "request_bodies_scope.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="request_bodies_scope.py",
        extra_args=["--openapi-scopes", "requestbodies", "--output-model-type", "pydantic_v2.BaseModel"],
    )


@freeze_time(TIMESTAMP)
def test_main_openapi_request_bodies_scope_with_ref(output_file: Path) -> None:
    """Test generating models from components/requestBodies with $ref at requestBody level."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "request_bodies_scope_with_ref.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="request_bodies_scope_with_ref.py",
        extra_args=["--openapi-scopes", "requestbodies", "--output-model-type", "pydantic_v2.BaseModel"],
    )


def test_main_openapi_x_property_names(output_file: Path) -> None:
    """Test x-propertyNames extension for OpenAPI 3.0 is converted to propertyNames."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "x_property_names.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="x_property_names.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )


def test_main_openapi_x_property_names_non_dict(output_file: Path) -> None:
    """Test x-propertyNames with non-dict value is ignored."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "x_property_names_non_dict.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="x_property_names_non_dict.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )


def test_main_openapi_x_property_names_false(output_file: Path) -> None:
    """Test boolean false x-propertyNames constrains OpenAPI 3.0 maps to empty keys."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "x_property_names_false.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="x_property_names_false.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )


def test_query_parameters_with_model_config(output_file: Path) -> None:
    """Test that query parameter classes include model_config when config options are used.

    Regression test for https://github.com/koxudaxi/datamodel-code-generator/issues/2491
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "query_parameters_with_config.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="query_parameters_with_config.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--openapi-scopes",
            "schemas",
            "paths",
            "parameters",
            "--use-annotated",
            "--extra-fields",
            "forbid",
            "--allow-population-by-field-name",
        ],
    )


def test_main_openapi_use_default_with_default_values_parameters(output_file: Path) -> None:
    """Test --use-default combined with --default-values on required OpenAPI parameters."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "default_values_parameters.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="default_values_parameters_use_default.py",
        extra_args=[
            "--use-default",
            "--default-values",
            str(DEFAULT_VALUES_DATA_PATH / "openapi_params_defaults.json"),
            "--openapi-scopes",
            "paths",
            "parameters",
        ],
    )


@pytest.mark.cli_doc(
    options=["--openapi-include-paths"],
    option_description="""Filter OpenAPI paths to include in model generation.

The `--openapi-include-paths` flag allows filtering which paths are processed.""",
    input_schema="openapi/body_and_parameters.yaml",
    cli_args=["--openapi-scopes", "paths", "schemas", "--openapi-include-paths", "/pets*"],
    golden_output="openapi/openapi_include_paths/pets_only.py",
)
def test_main_openapi_include_paths(output_file: Path) -> None:
    """Filter OpenAPI paths to include in model generation.

    The `--openapi-include-paths` flag allows filtering which paths are processed.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "body_and_parameters.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "openapi_include_paths" / "pets_only.py",
        extra_args=["--openapi-scopes", "paths", "schemas", "--openapi-include-paths", "/pets*"],
    )


def test_main_openapi_include_paths_without_leading_slash(output_file: Path) -> None:
    """Test path pattern matching works without leading slash."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "body_and_parameters.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_OPENAPI_PATH / "openapi_include_paths" / "pets_only.py",
        extra_args=["--openapi-scopes", "paths", "schemas", "--openapi-include-paths", "pets*"],
    )


def test_main_openapi_include_paths_warning_without_paths_scope() -> None:
    """Warn when --openapi-include-paths used without paths scope."""
    import warnings

    from datamodel_code_generator.__main__ import main

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        main([
            "--input",
            str(OPEN_API_DATA_PATH / "body_and_parameters.yaml"),
            "--input-file-type",
            "openapi",
            "--openapi-scopes",
            "schemas",
            "--openapi-include-paths",
            "/pets*",
        ])
        assert_warnings_contain(w, "--openapi-include-paths has no effect without --openapi-scopes paths")


def test_main_openapi_deprecated_field(output_file: Path) -> None:
    """Test OpenAPI generation with deprecated field property."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "deprecated_field.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="deprecated_field.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )


@pytest.mark.skipif(
    not PYDANTIC_V2_FIELD_DEPRECATED_NEEDS_JSON_SCHEMA_EXTRA,
    reason="Pydantic 2.7+ supports Field(deprecated=...) directly",
)
def test_main_openapi_deprecated_field_pydantic26(output_file: Path) -> None:
    """Test OpenAPI deprecated fields stay importable before native Pydantic support."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "deprecated_field.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
        force_exec_validation=True,
    )


def test_main_openapi_recursive_ref_discriminator(output_file: Path) -> None:
    """Test OpenAPI generation with $recursiveRef and discriminator."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "recursive_ref_discriminator.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="recursive_ref_discriminator.py",
    )


def test_main_openapi_recursive_ref_discriminator_pydantic_v2(output_file: Path) -> None:
    """Test OpenAPI generation with $recursiveRef and discriminator for Pydantic v2."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "recursive_ref_discriminator.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="recursive_ref_discriminator_pydantic_v2.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )


def test_main_openapi_dynamic_ref(output_file: Path) -> None:
    """Test OpenAPI 3.1 $dynamicRef resolving to a component $dynamicAnchor."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "dynamic_ref.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="dynamic_ref.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )


def test_main_openapi_dynamic_ref_external_generic(output_file: Path) -> None:
    """Specialize external JSON Schema generics referenced from OpenAPI components."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "dynamic_ref_external_generic" / "api.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="dynamic_ref_external_generic.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="openapi_dynamic_ref_external_generic",
        model_name="StrList",
        valid_json='["one"]',
        invalid_json="[1]",
        expected_error_type="string_type",
    )


def test_main_openapi_dynamic_ref_component_anchors(output_file: Path) -> None:
    """Specialize external generics for the $dynamicAnchor that each OpenAPI component schema declares."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "dynamic_ref_component_anchors" / "api.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="dynamic_ref_component_anchors.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="openapi_dynamic_ref_component_anchors",
        model_name="IntList",
        valid_json="[1, 2]",
        invalid_json='["one"]',
        expected_error_type="int_parsing",
    )


def test_main_openapi_allof_array_ref_no_duplicate_model(output_file: Path) -> None:
    """Test allOf with array property referencing another schema (#2959).

    When allOf merges an array property from parent (with generic items) and child
    (with $ref items), the child's $ref should completely override the parent,
    preventing duplicate model generation like 'Datum' class.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_array_ref_override.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file="allof_array_ref_override.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-standard-collections",
            "--use-union-operator",
            "--use-schema-description",
        ],
        force_exec_validation=True,
    )
    assert_generated_model_json_invalid(
        output_file,
        module_name="allof_array_ref_override",
        model_name="PaginatedDataTypeList",
        invalid_json='{"pagination":{"limit":1,"page":1}}',
        expected_error_type="missing",
    )


def test_ref_merge_parameters(output_file: Path) -> None:
    """Test $ref + const merge in OpenAPI parse_all_parameters path."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "ref_merge_parameters.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-frozen-field",
            "--use-annotated",
            "--openapi-scopes",
            "paths",
            "schemas",
            "parameters",
        ],
    )


@BLACK_PY314_SKIP
def test_main_reuse_model_with_type_alias(output_file: Path) -> None:
    """Test --reuse-model with --use-type-alias doesn't crash on empty fields.

    Regression test for https://github.com/koxudaxi/datamodel-code-generator/issues/3059
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "reuse_model_with_type_alias.json",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--target-python-version",
            "3.14",
            "--reuse-model",
            "--use-type-alias",
        ],
    )


@pytest.mark.timeout(30)
def test_main_openapi_discriminated_oneof_allof_cycle(output_file: Path) -> None:
    """Discriminated oneOf with variants that allOf the parent (circular graph).

    Covers `sort_data_models` ordering for cyclic base dependencies and discriminator
    handling (mapping + RootModel) on a minimal OpenAPI spec. See the
    [Pull Request](https://github.com/koxudaxi/datamodel-code-generator/pull/3078) for
    more details.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "openapi_discriminated_oneof_allof_cycle.json",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
    )


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize(
    ("case", "source", "alias_file", "options", "field_name", "model_name"),
    [
        ("none", "direct", "none", {}, "petType", "Pet"),
        ("global", "direct", "global", {}, "kind", "Pet"),
        ("scoped", "direct", "scoped", {}, "kind", "Pet"),
        (
            "affix",
            "direct",
            "affix",
            {"class_name_prefix": "Pre", "class_name_suffix": "Suffix"},
            "kind",
            "PrePetSuffix",
        ),
        ("snake", "direct", "none", {"snake_case_field": True}, "pet_type", "Pet"),
        ("inherited", "inherited", "inherited", {}, "kind", "Pet"),
        ("missing", "missing", "scoped", {}, "kind", "Pet"),
        ("nullable", "nullable", "scoped", {}, "kind", "Pet"),
        ("collision", "collision", "collision", {}, "kind", "Pet"),
        ("serialization", "direct", "scoped", {"serialization_aliases": {"petType": "wireKind"}}, "kind", "Pet"),
        ("wire_collision", "wire_collision", "wire_collision", {}, "kind", "Pet"),
        ("wire_collision_enum", "wire_collision_enum", "wire_collision", {}, "kind", "Pet"),
        ("wire_collision_const", "wire_collision_const", "wire_collision", {}, "kind", "Pet"),
        ("wire_collision_nullable", "wire_collision_nullable", "wire_collision", {}, "kind", "Pet"),
        (
            "wire_collision_inherited",
            "wire_collision_inherited",
            "wire_collision_inherited",
            {"use_enum_values_in_discriminator": True, "use_subclass_enum": True},
            "kind",
            "Pet",
        ),
        (
            "wire_collision_serialization",
            "wire_collision",
            "wire_collision",
            {"serialization_aliases": {"petType": "wireKind"}},
            "kind",
            "Pet",
        ),
        (
            "wire_collision_global_inherited",
            "wire_collision_inherited",
            "wire_collision_global_inherited",
            {"use_enum_values_in_discriminator": True, "use_subclass_enum": True},
            "kind",
            "Pet",
        ),
        ("wire_collision_global", "wire_collision", "wire_collision_global", {}, "kind", "Pet"),
        ("wire_collision_global_enum", "wire_collision_enum", "wire_collision_global", {}, "kind", "Pet"),
        ("wire_collision_global_const", "wire_collision_const", "wire_collision_global", {}, "kind", "Pet"),
        ("wire_collision_global_missing", "wire_collision_missing", "wire_collision_global", {}, "kind", "Pet"),
        ("wire_collision_global_nullable", "wire_collision_nullable", "wire_collision_global", {}, "kind", "Pet"),
    ],
)
def test_discriminator_final_field_aliases(
    output_file: Path,
    entrypoint: str,
    case: str,
    source: str,
    alias_file: str,
    options: dict[str, Any],
    field_name: str,
    model_name: str,
) -> None:
    """Resolve variant fields without globalizing scoped aliases or changing tag input."""
    fixture_dir = OPEN_API_DATA_PATH / "discriminator_field_aliases"
    aliases_path = fixture_dir / f"{alias_file}_aliases.json"
    expected = EXPECTED_OPENAPI_PATH / "discriminator_field_aliases" / f"{case}.py"
    if entrypoint == "cli":
        args = ["--aliases", str(aliases_path), "--disable-timestamp"]
        for key, value in options.items():
            args.append(f"--{key.replace('_', '-')}")
            if value is not True:
                args.append(str(fixture_dir / "serialization_aliases.json") if isinstance(value, dict) else str(value))
        run_main_and_assert(
            input_path=fixture_dir / f"{source}.json",
            output_path=output_file,
            input_file_type="openapi",
            assert_func=assert_file_content,
            expected_file=expected,
            extra_args=args,
            force_exec_validation=True,
        )
    else:
        aliases = json.loads(aliases_path.read_text())
        run_generate_file_and_assert(
            input_path=fixture_dir / f"{source}.json",
            output_path=output_file,
            input_file_type=InputFileType.OpenAPI,
            assert_func=assert_file_content,
            expected_file=expected,
            aliases=aliases,
            disable_timestamp=True,
            unchanged_inputs={"aliases": aliases},
            **options,
        )
    payload_dir = DATA_PATH / "payloads" / "discriminator_alias_outputs"
    payloads = json.loads((payload_dir / "validation.json").read_text())
    runtime: dict[str, Any] = {}
    wire_name = "wireKind" if "serialization_aliases" in options else "petType"
    for tag, detail in [("cat", "meow"), ("dog", "bark")]:
        assert_generated_model_json_validation(
            output_file,
            module_name=f"generated_discriminator_alias_{case}_{tag}",
            model_name=model_name,
            valid_json=json.dumps(payloads["omitted"][tag]),
            invalid_json=json.dumps(payloads["invalid_tag"]),
            expected_error_type="union_tag_invalid",
            expected_attribute_path=("root", field_name),
            expected_attribute_value=tag,
        )
        with _generated_model(output_file, f"generated_discriminator_dump_{case}_{tag}", model_name) as model:
            value = model.model_validate(payloads["omitted"][tag])
            variant = getattr(sys.modules[model.__module__], model_name.replace("Pet", tag.title()))
            runtime[tag] = {
                "selected_variant": type(value.root) is variant,
                "native_identity": model.model_validate(value.root).root is value.root,
                "coerced_detail": getattr(
                    model.model_validate({
                        **payloads["omitted"][tag],
                        detail: payloads["omitted"][tag][detail].encode(),
                    }).root,
                    detail,
                ),
            }
            assert_output(
                json.dumps(value.model_dump(by_alias=True, exclude_none=True), sort_keys=True) + "\n",
                payload_dir / f"{wire_name}_{tag}.txt",
            )
            if case.startswith("wire_collision"):
                native = variant.model_validate(payloads["ordinary"][tag])
                runtime[tag]["ordinary_native_identity"] = model.model_validate(native).root is native
                assert_output(
                    json.dumps(native.model_dump(mode="json", by_alias=True, exclude_none=True), sort_keys=True) + "\n",
                    payload_dir / f"{wire_name}_{tag}_ordinary.txt",
                )
                # Keep tagged dispatch and its existing schema order, including Python-name-first lookup.
                schema = model.model_json_schema()
                schema_keys = ("anyOf",) if source == "wire_collision_nullable" else ("discriminator", "oneOf")
                assert_output(
                    json.dumps({key: schema[key] for key in schema_keys}, indent=2) + "\n",
                    payload_dir
                    / (
                        "collision_nullable_schema.txt"
                        if source == "wire_collision_nullable"
                        else "collision_schema.txt"
                    ),
                )
                if source in {"wire_collision", "wire_collision_nullable"}:
                    value = model.model_validate(payloads["matching"][tag])
                    runtime[tag]["matching_selected_variant"] = type(value.root) is variant
                    runtime[tag]["matching_dump_preserved"] = (
                        value.model_dump(by_alias=True, exclude_none=True)
                        == payloads["matching_outputs"][wire_name][tag]
                    )
        if case.startswith("wire_collision"):
            # Pydantic still interprets this wire key as its Python discriminator before trying petType.
            assert_generated_model_json_invalid(
                output_file,
                module_name=f"generated_discriminator_collision_{case}_{tag}",
                model_name=model_name,
                invalid_json=json.dumps(payloads["ordinary"][tag]),
                expected_error_type="union_tag_invalid",
            )
    with _generated_model(
        output_file, f"generated_discriminator_unrelated_{case}", model_name.replace("Pet", "Unrelated")
    ) as model:
        runtime["unrelated_dump_preserved"] = (
            model.model_validate(payloads["unrelated"]).model_dump(by_alias=True)
            == payloads["unrelated_outputs"][wire_name]
        )
    runtime_case = "runtime"
    if case.startswith("wire_collision"):
        runtime_case += "_matching" if source in {"wire_collision", "wire_collision_nullable"} else "_collision"
    assert_output(json.dumps(runtime, indent=2, sort_keys=True) + "\n", payload_dir / f"{runtime_case}.txt")


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
def test_discriminator_different_field_aliases(
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
    entrypoint: str,
) -> None:
    """Reject incompatible Python tag names without weakening tagged-union validation."""
    fixture_dir = OPEN_API_DATA_PATH / "discriminator_field_aliases"
    message = (
        "Discriminator 'petType' resolves to different field names 'cat_kind' and 'dog_kind'; "
        "use the same alias for all variants."
    )
    if entrypoint == "cli":
        run_main_with_args(
            [
                "--input",
                str(fixture_dir / "direct.json"),
                "--input-file-type",
                "openapi",
                "--aliases",
                str(fixture_dir / "different_aliases.json"),
                "--output",
                str(output_file),
            ],
            expected_exit=Exit.ERROR,
        )
        assert_error_message(capsys, message)
    else:
        with pytest.raises(Error, match=re.escape(message)):
            generate(
                fixture_dir / "direct.json",
                input_file_type=InputFileType.OpenAPI,
                aliases=json.loads((fixture_dir / "different_aliases.json").read_text()),
                output=output_file,
            )


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
def test_discriminator_field_aliases_msgspec(output_file: Path, entrypoint: str) -> None:
    """Keep msgspec's existing wire-field handling independent of Pydantic tag names."""
    fixture_dir = OPEN_API_DATA_PATH / "discriminator_field_aliases"
    aliases_path = fixture_dir / "different_aliases.json"
    expected = EXPECTED_OPENAPI_PATH / "discriminator_field_aliases" / "msgspec.py"
    if entrypoint == "cli":
        run_main_and_assert(
            input_path=fixture_dir / "direct.json",
            output_path=output_file,
            input_file_type="openapi",
            assert_func=assert_file_content,
            expected_file=expected,
            extra_args=["--aliases", str(aliases_path), "--disable-timestamp", "--output-model-type", "msgspec.Struct"],
            force_exec_validation=True,
        )
    else:
        run_generate_file_and_assert(
            input_path=fixture_dir / "direct.json",
            output_path=output_file,
            input_file_type=InputFileType.OpenAPI,
            assert_func=assert_file_content,
            expected_file=expected,
            aliases=json.loads(aliases_path.read_text()),
            disable_timestamp=True,
            output_model_type=DataModelType.MsgspecStruct,
        )


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("case", ["local", "external", "relative", "url", "scoped", "bare", "empty", "many_ordinary"])
def test_external_discriminator_base(output_file: Path, entrypoint: str, case: str) -> None:
    """Keep discriminator definitions, mappings and tag payloads in their source document."""
    fixture_dir = OPEN_API_DATA_PATH / "external_discriminator_base"
    expected = EXPECTED_OPENAPI_PATH / "external_discriminator_base" / f"{case}.py"
    if entrypoint == "cli":
        args = ["--disable-timestamp"]
        if case in {"bare", "empty"}:
            args.append("--allow-remote-refs")
        if case == "url":
            args.extend(["--http-local-ref-path", str(fixture_dir / "mirror")])
        run_main_and_assert(
            input_path=fixture_dir / f"{case}.json",
            output_path=output_file,
            input_file_type="openapi",
            assert_func=assert_file_content,
            expected_file=expected,
            extra_args=args,
            force_exec_validation=True,
        )
    else:
        options = {"http_local_ref_path": fixture_dir / "mirror"} if case == "url" else {}
        if case in {"bare", "empty"}:
            options["allow_remote_refs"] = True
        run_generate_file_and_assert(
            input_path=fixture_dir / f"{case}.json",
            output_path=output_file,
            input_file_type=InputFileType.OpenAPI,
            assert_func=assert_file_content,
            expected_file=expected,
            disable_timestamp=True,
            **options,
        )
    for tag, inherited_detail, mapped_detail in [("cat", "meow", "purr"), ("dog", "bark", "woof")]:
        inherited = {"kind": tag, inherited_detail: "yes"}
        mapped = {"species": tag, mapped_detail: 7}
        if case == "scoped":
            payload = {"local": inherited, "left": inherited, "right": mapped}
            invalid = {**payload, "right": {"species": "bird"}}
            attribute = "right"
        else:
            payload = {
                "item": {"kind": tag}
                if case == "empty"
                else inherited
                if case in {"local", "external", "many_ordinary"}
                else mapped
            }
            tag_property = "species" if case in {"relative", "url", "bare"} else "kind"
            invalid = {"item": {tag_property: [] if case == "empty" else "bird"}}
            attribute = "item"
        assert_generated_model_json_validation(
            output_file,
            module_name=f"generated_external_discriminator_{case}_{tag}",
            model_name="Wrapper",
            valid_json=json.dumps(payload),
            invalid_json=json.dumps(invalid),
            expected_error_type="string_type" if case == "empty" else "union_tag_invalid",
            expected_attribute_path=(attribute, "species" if case in {"relative", "url", "bare", "scoped"} else "kind"),
            expected_attribute_value=tag,
        )
        with _generated_model(output_file, f"generated_external_discriminator_dump_{case}_{tag}", "Wrapper") as model:
            assert_output(
                json.dumps(model.model_validate(payload).model_dump(by_alias=True, exclude_none=True), sort_keys=True)
                + "\n",
                DATA_PATH / "payloads" / "external_discriminator_outputs" / f"{case}_{tag}.txt",
            )
    if case == "many_ordinary":
        with _generated_model(output_file, "generated_discriminator_ordinary_refs", "Controls") as model:
            payload = {"field0": {"value": "first"}, "field99": {"value": "last"}}
            assert_output(
                json.dumps(model.model_validate(payload).model_dump(exclude_none=True), sort_keys=True) + "\n",
                DATA_PATH / "payloads" / "external_discriminator_outputs" / "ordinary.txt",
            )


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("formatters", [[Formatter.BLACK, Formatter.ISORT], [Formatter.BUILTIN]])
def test_external_discriminator_multiple_inputs(
    output_dir: Path,
    entrypoint: str,
    formatters: list[Formatter],
) -> None:
    """Do not recollect or lose metadata when a referenced document is also an input."""
    fixture_dir = OPEN_API_DATA_PATH / "external_discriminator_base" / "multiple"
    expected = EXPECTED_OPENAPI_PATH / "external_discriminator_base" / "multiple"
    if entrypoint == "cli":
        run_main_and_assert(
            input_path=fixture_dir,
            output_path=output_dir,
            input_file_type="openapi",
            expected_directory=expected,
            extra_args=["--disable-timestamp", "--formatters", *(formatter.value for formatter in formatters)],
            runtime_validation_module="a",
            runtime_validation_model_name="Wrapper",
            runtime_validation_data={"item": {"kind": "cat", "meow": "yes"}},
        )
    else:
        generate(
            fixture_dir,
            input_file_type=InputFileType.OpenAPI,
            output=output_dir,
            disable_timestamp=True,
            formatters=formatters,
        )
        assert_directory_content(output_dir, expected)
    with _generated_package_module(output_dir, "a") as module:
        for tag, detail in [("cat", "meow"), ("dog", "bark")]:
            payload = {"item": {"kind": tag, detail: "yes"}}
            assert_output(
                json.dumps(module.Wrapper.model_validate(payload).model_dump(exclude_none=True), sort_keys=True) + "\n",
                DATA_PATH / "payloads" / "external_discriminator_outputs" / f"local_{tag}.txt",
            )


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("case", ["child_first", "parent_first"])
def test_external_discriminator_parent_load_order(output_file: Path, entrypoint: str, case: str) -> None:
    """Keep external child fields when their discriminator parent is loaded later."""
    source = OPEN_API_DATA_PATH / "external_discriminator_base" / f"{case}.json"
    expected = EXPECTED_OPENAPI_PATH / "external_discriminator_base" / f"{case}.py"
    if entrypoint == "cli":
        run_main_and_assert(
            input_path=source,
            output_path=output_file,
            input_file_type="openapi",
            assert_func=assert_file_content,
            expected_file=expected,
            extra_args=["--disable-timestamp"],
        )
    else:
        run_generate_file_and_assert(
            input_path=source,
            output_path=output_file,
            input_file_type=InputFileType.OpenAPI,
            assert_func=assert_file_content,
            expected_file=expected,
            disable_timestamp=True,
        )
    payloads = json.loads((DATA_PATH / "payloads/external_discriminator_pending.json").read_text())
    for model_file in (DATA_PATH / "python/discriminator_pending/native.py", output_file):
        for tag in ("cat", "dog"):
            assert_generated_model_json_validation(
                model_file,
                module_name="discriminator_pending_validation",
                model_name="Wrapper",
                valid_json=json.dumps(payloads[tag]),
                invalid_json=json.dumps(payloads["unknown"]),
                expected_error_type="union_tag_invalid",
            )
            assert_generated_model_json_validation(
                model_file,
                module_name="discriminator_pending_required",
                model_name="Wrapper",
                valid_json=json.dumps(payloads[tag]),
                invalid_json=json.dumps(payloads["missing"]),
                expected_error_type="missing",
            )
            with _generated_model(model_file, "discriminator_pending_dump", "Wrapper") as model:
                assert_output(
                    json.dumps(model.model_validate(payloads[tag]).model_dump(), sort_keys=True) + "\n",
                    DATA_PATH / "payloads/external_discriminator_outputs" / f"local_{tag}.txt",
                )


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
def test_numeric_union_preserves_nullable_warning(output_file: Path, entrypoint: str) -> None:
    """Keep strict OpenAPI nullable diagnostics when numeric bounds belong to the field."""
    from datamodel_code_generator.enums import VersionMode

    source = OPEN_API_DATA_PATH / "type_union_nullable.json"
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", DeprecationWarning)
        if entrypoint == "cli":
            run_main_and_assert(
                input_path=source,
                output_path=output_file,
                input_file_type="openapi",
                extra_args=[
                    "--disable-timestamp",
                    "--field-constraints",
                    "--schema-version-mode",
                    "strict",
                    "--formatters",
                    "builtin",
                ],
                assert_func=assert_file_content,
                expected_file="type_union_nullable.py",
            )
        else:
            run_generate_file_and_assert(
                input_path=source,
                output_path=output_file,
                input_file_type=InputFileType.OpenAPI,
                disable_timestamp=True,
                field_constraints=True,
                schema_version_mode=VersionMode.Strict,
                formatters=[Formatter.BUILTIN],
                assert_func=assert_file_content,
                expected_file="type_union_nullable.py",
            )
    assert_warnings_contain(recorded, "nullable keyword is deprecated")
