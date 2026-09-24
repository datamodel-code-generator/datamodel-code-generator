"""Tests for AsyncAPI input file code generation."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import InputFileType, inferred_message
from datamodel_code_generator.__main__ import Exit
from datamodel_code_generator.parser.asyncapi import AsyncAPIParser
from tests.conftest import assert_output
from tests.main.asyncapi.conftest import assert_file_content
from tests.main.conftest import (
    ASYNC_API_DATA_PATH,
    BACKEND_GOLDEN_CASES,
    BACKEND_GOLDEN_TARGET_ARGS,
    DATA_PATH,
    EXPECTED_ASYNC_API_PATH,
    run_generate_file_and_assert,
    run_main_and_assert,
)

PY310_TARGET_ARGS = ["--target-python-version", "3.10"]
ASYNCAPI_IMPORT_PROBE_TIMEOUT_SECONDS = 15
ASYNCAPI_CONVERTER_MODULES = (
    "datamodel_code_generator.parser.avro",
    "datamodel_code_generator.parser.protobuf",
    "datamodel_code_generator.parser.xmlschema",
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.allow_direct_assert
def test_import_asyncapi_parser_does_not_load_embedded_schema_converters() -> None:
    """Importing AsyncAPI parser does not load converters for optional embedded schema formats."""
    code = f"""
import importlib
import json
import sys

importlib.import_module("datamodel_code_generator.parser.asyncapi")

converter_modules = {ASYNCAPI_CONVERTER_MODULES!r}
loaded = sorted(module_name for module_name in converter_modules if module_name in sys.modules)
print(json.dumps(loaded))
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        timeout=ASYNCAPI_IMPORT_PROBE_TIMEOUT_SECONDS,
    )

    assert json.loads(result.stdout) == []


@pytest.mark.allow_direct_assert
def test_asyncapi_converter_compatibility_attributes_are_lazy() -> None:
    """Keep existing AsyncAPI converter attributes importable without eager module import."""
    from datamodel_code_generator.parser.asyncapi import (
        convert_avro_schema_data,
        convert_protobuf_schema_data,
        convert_xml_schema_data,
    )

    assert convert_avro_schema_data.__module__ == "datamodel_code_generator.parser.avro"
    assert convert_protobuf_schema_data.__module__ == "datamodel_code_generator.parser.protobuf"
    assert convert_xml_schema_data.__module__ == "datamodel_code_generator.parser.xmlschema"


def test_main_asyncapi_2_yaml(output_file: Path) -> None:
    """Generate models from an AsyncAPI 2.x YAML document."""
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "user-events.yaml",
        output_path=output_file,
        input_file_type="asyncapi",
        assert_func=assert_file_content,
        expected_file="user_events.py",
    )


def test_main_asyncapi_3_json(output_file: Path) -> None:
    """Generate models from an AsyncAPI 3.x JSON document."""
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "order-events.json",
        output_path=output_file,
        input_file_type="asyncapi",
        assert_func=assert_file_content,
        expected_file="order_events.py",
    )


def test_main_asyncapi_discriminator_state_isolated_per_document(output_dir: Path) -> None:
    """Reset document-local discriminator metadata for directory inputs."""
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "discriminator_state_isolation",
        output_path=output_dir,
        input_file_type="asyncapi",
        expected_directory=EXPECTED_ASYNC_API_PATH / "discriminator_state_isolation",
        extra_args=["--disable-timestamp"],
        runtime_validation_module="b",
        runtime_validation_model_name="Holder",
        runtime_validation_data={"pet": {"name": "Milo"}},
    )


@pytest.mark.parametrize(("output_model_type", "expected_name"), BACKEND_GOLDEN_CASES)
def test_main_asyncapi_output_model_types(
    output_file: Path,
    output_model_type: str,
    expected_name: str,
) -> None:
    """Generate representative AsyncAPI models across supported output backends."""
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "order-events.json",
        output_path=output_file,
        input_file_type="asyncapi",
        assert_func=assert_file_content,
        expected_file=f"output_model_types/order_events_{expected_name}.py",
        extra_args=[*BACKEND_GOLDEN_TARGET_ARGS, "--output-model-type", output_model_type],
        force_exec_validation=True,
        importable_module_name=f"generated_asyncapi_{expected_name}",
        importable_module_attribute="Order",
    )


def test_main_asyncapi_auto_inference(output_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Infer AsyncAPI input type from the asyncapi field."""
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "user-events.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="user_events.py",
        capsys=capsys,
        expected_stderr=inferred_message.format("asyncapi") + "\n",
    )


def test_generate_asyncapi(output_file: Path) -> None:
    """Generate models through the public generate() API."""
    run_generate_file_and_assert(
        input_path=ASYNC_API_DATA_PATH / "user-events.yaml",
        output_path=output_file,
        input_file_type=InputFileType.AsyncAPI,
        assert_func=assert_file_content,
        expected_file="user_events.py",
    )


def test_main_asyncapi_schema_version(output_file: Path) -> None:
    """Accept explicit AsyncAPI schema version selection."""
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "order-events.json",
        output_path=output_file,
        input_file_type="asyncapi",
        assert_func=assert_file_content,
        expected_file="order_events.py",
        extra_args=["--schema-version", "3.0"],
        expected_exit=Exit.OK,
    )


def test_main_asyncapi_unknown_version_falls_back_to_v3(output_file: Path) -> None:
    """Treat unknown AsyncAPI versions as v3-compatible when validation is not requested."""
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "unknown-version.yaml",
        output_path=output_file,
        input_file_type="asyncapi",
        assert_func=assert_file_content,
        expected_file="unknown_version.py",
        extra_args=[*PY310_TARGET_ARGS, "--schema-version-mode", "strict"],
    )


def test_main_asyncapi_edge_cases(output_file: Path) -> None:
    """Generate models for AsyncAPI schema/reference edge cases through the CLI."""
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "edge-cases.yaml",
        output_path=output_file,
        input_file_type="asyncapi",
        assert_func=assert_file_content,
        expected_file="edge_cases.py",
        extra_args=PY310_TARGET_ARGS,
    )


def test_main_asyncapi_non_object_components(output_file: Path) -> None:
    """Ignore invalid component containers while still generating channel payloads."""
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "non-object-components.yaml",
        output_path=output_file,
        input_file_type="asyncapi",
        assert_func=assert_file_content,
        expected_file="non_object_components.py",
        extra_args=PY310_TARGET_ARGS,
    )


def test_main_asyncapi_external_message_ref(output_file: Path) -> None:
    """Resolve external message refs using the referenced document context."""
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "external-message-ref.yaml",
        output_path=output_file,
        input_file_type="asyncapi",
        assert_func=assert_file_content,
        expected_file="external_message_ref.py",
        extra_args=PY310_TARGET_ARGS,
    )


def test_main_asyncapi_dynamic_ref(output_file: Path) -> None:
    """Resolve $dynamicRef in component schemas and in merged references to another document's components."""
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "dynamic_ref" / "events.yaml",
        output_path=output_file,
        input_file_type="asyncapi",
        assert_func=assert_file_content,
        expected_file="dynamic_ref.py",
        extra_args=PY310_TARGET_ARGS,
    )


def test_main_asyncapi_stable_surface(output_file: Path) -> None:
    """Generate models from the stable AsyncAPI schema-bearing surface."""
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "stable-surface.yaml",
        output_path=output_file,
        input_file_type="asyncapi",
        assert_func=assert_file_content,
        expected_file="stable_surface.py",
        extra_args=PY310_TARGET_ARGS,
    )


def test_main_asyncapi_multi_format_schema_objects(output_file: Path) -> None:
    """Unwrap AsyncAPI 3.x Multi Format Schema Objects and dispatch supported schema formats."""
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "multi-format-schemas.yaml",
        output_path=output_file,
        input_file_type="asyncapi",
        assert_func=assert_file_content,
        expected_file="multi_format_schemas.py",
        extra_args=PY310_TARGET_ARGS,
    )


def test_main_asyncapi_v2_message_schema_format(output_file: Path) -> None:
    """Apply AsyncAPI 2.x message-level schemaFormat to the payload schema."""
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "schema-format-v2.yaml",
        output_path=output_file,
        input_file_type="asyncapi",
        assert_func=assert_file_content,
        expected_file="schema_format_v2.py",
        extra_args=PY310_TARGET_ARGS,
    )


def test_main_asyncapi_protobuf_schema_format(output_file: Path) -> None:
    """Dispatch AsyncAPI embedded Protocol Buffers schemas to the Protobuf parser."""
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "protobuf-schema-format.yaml",
        output_path=output_file,
        input_file_type="asyncapi",
        assert_func=assert_file_content,
        expected_file="protobuf_schema_format.py",
        extra_args=PY310_TARGET_ARGS,
    )


def test_main_asyncapi_protobuf_schema_format_local_import(output_file: Path) -> None:
    """Resolve local imports from AsyncAPI embedded Protocol Buffers schemas."""
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "protobuf-local-import.yaml",
        output_path=output_file,
        input_file_type="asyncapi",
        assert_func=assert_file_content,
        expected_file="protobuf_local_import.py",
        extra_args=PY310_TARGET_ARGS,
    )


def test_main_asyncapi_xml_schema_format(output_file: Path) -> None:
    """Dispatch AsyncAPI embedded XML Schema strings to the XML Schema parser."""
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "xml-schema-format.yaml",
        output_path=output_file,
        input_file_type="asyncapi",
        assert_func=assert_file_content,
        expected_file="xml_schema_format.py",
        extra_args=PY310_TARGET_ARGS,
    )


def test_main_asyncapi_non_finite_embedded_schemas(output_file: Path) -> None:
    """Render source-safe non-finite defaults from embedded schema formats."""
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "non_finite_embedded_schemas.yaml",
        output_path=output_file,
        input_file_type="asyncapi",
        assert_func=assert_file_content,
        expected_file="non_finite_embedded_schemas.py",
        force_exec_validation=True,
    )


def test_asyncapi_parser_renders_non_finite_embedded_schemas() -> None:
    """Render embedded schema defaults without a subprocess boundary."""
    parser = AsyncAPIParser(ASYNC_API_DATA_PATH / "non_finite_embedded_schemas.yaml")

    assert_output(
        f"{parser.parse(format_=False)}\n",
        DATA_PATH / "expected/parser/asyncapi/non_finite_embedded_schemas.py",
    )


@pytest.mark.parametrize(
    ("fixture_name", "expected_stderr_contains"),
    [
        (
            "message-schema-format-non-string.yaml",
            "AsyncAPI message schemaFormat must be a string",
        ),
        (
            "multiple-trait-headers.yaml",
            "Multiple AsyncAPI message traits define headers",
        ),
        (
            "schema-format-missing-schema.yaml",
            "AsyncAPI Multi Format Schema Object requires a schema field",
        ),
        (
            "schema-format-non-string.yaml",
            "AsyncAPI schemaFormat must be a string",
        ),
        (
            "raml-schema-format.yaml",
            "Unsupported AsyncAPI schemaFormat 'application/raml+yaml'",
        ),
        (
            "protobuf-schema-format-non-string.yaml",
            "Protocol Buffers schemaFormat requires a .proto schema string",
        ),
        (
            "xml-schema-format-non-string.yaml",
            "XML Schema schemaFormat requires an XSD schema string",
        ),
        (
            "schema-format-scalar-schema.yaml",
            "requires a schema object",
        ),
        (
            "unsupported-custom-schema-format.yaml",
            "Unsupported AsyncAPI schemaFormat 'application/vnd.example.custom+json'",
        ),
    ],
)
def test_main_asyncapi_invalid_schema_format_documents(
    fixture_name: str,
    expected_stderr_contains: str,
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Reject malformed or unsupported AsyncAPI schemaFormat declarations."""
    run_main_and_assert(
        input_path=DATA_PATH / "asyncapi_invalid" / fixture_name,
        output_path=output_file,
        input_file_type="asyncapi",
        expected_exit=Exit.ERROR,
        expected_stderr_contains=expected_stderr_contains,
        capsys=capsys,
    )


def test_main_asyncapi_validation_flag(output_file: Path) -> None:
    """Keep validation non-goal explicit while still generating models."""
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "user-events.yaml",
        output_path=output_file,
        input_file_type="asyncapi",
        assert_func=assert_file_content,
        expected_file="user_events.py",
        extra_args=["--validation"],
    )


def test_main_asyncapi_invalid_schema_version(output_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Reject unknown explicit AsyncAPI schema versions through the CLI."""
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "user-events.yaml",
        output_path=output_file,
        input_file_type="asyncapi",
        extra_args=["--schema-version", "invalid-version"],
        expected_exit=Exit.ERROR,
        expected_stderr_contains="Invalid AsyncAPI version: invalid-version",
        capsys=capsys,
    )
