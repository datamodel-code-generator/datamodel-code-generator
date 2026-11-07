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
    assert_generated_model_json_validation,
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


@pytest.mark.parametrize("version", ["v2", "v3"])
@pytest.mark.parametrize(
    ("directory", "extra_args"),
    [
        pytest.param("single_value_enum_as_const", ["--use-single-value-enum-as-const"], id="enum"),
        pytest.param("single_value_enum_as_const/written_as_const", [], id="written-as-const"),
    ],
)
def test_main_asyncapi_single_value_enum_as_const(
    directory: str, extra_args: list[str], version: str, output_file: Path
) -> None:
    """Read single-value enums of payloads and parameters as const, with entries named like keywords."""
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / directory / f"{version}.yaml",
        output_path=output_file,
        input_file_type="asyncapi",
        assert_func=assert_file_content,
        expected_file=f"single_value_enum_as_const_{version}.py",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", "--formatters", "builtin", *extra_args],
    )


@pytest.mark.parametrize("extra_args", [["--use-single-value-enum-as-const"], []], ids=["option", "without-option"])
def test_main_asyncapi_single_value_enum_as_const_keeps_xml_schema(extra_args: list[str], output_file: Path) -> None:
    """Keep the fixed value of a nillable element in an embedded XML Schema, which is converted, not read as JSON."""
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "single_value_enum_as_const" / "xml_schema.yaml",
        output_path=output_file,
        input_file_type="asyncapi",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", "--formatters", "builtin", *extra_args],
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


@pytest.mark.parametrize(
    "components", [pytest.param("components:\n", id="null"), pytest.param("components: []\n", id="list")]
)
def test_main_asyncapi_components_that_are_not_an_object(tmp_path: Path, output_file: Path, components: str) -> None:
    """Read a ``components`` that is no object as holding no schema, as a ``schemas`` that is no object."""
    document = (ASYNC_API_DATA_PATH / "non-object-components.yaml").read_text(encoding="utf-8")
    input_path = tmp_path / "non-object-components.yaml"
    input_path.write_text(document[: document.index("components:")] + components, encoding="utf-8")
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="asyncapi",
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


def test_main_asyncapi_root_message_ref(output_file: Path) -> None:
    """Resolve a message reference whose pointer has only empty segments to the root of its document."""
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "root-message-ref.yaml",
        output_path=output_file,
        input_file_type="asyncapi",
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


def test_main_asyncapi_avro_named_types(output_file: Path) -> None:
    """Resolve the named types of an embedded Avro schema in the document the converted schema is kept in."""
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "avro-named-types.yaml",
        output_path=output_file,
        input_file_type="asyncapi",
        extra_args=PY310_TARGET_ARGS,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="asyncapi_avro_named_types",
        model_name="OrdersOrderPayload",
        valid_json='{"address": {"street": "a"}, "billing": {"street": "b"}, "color": "RED", "favorite": "GREEN"}',
        invalid_json='{"address": {"street": 1}, "billing": {"street": "b"}, "color": "RED", "favorite": "GREEN"}',
        expected_error_type="string_type",
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


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param([], id="default"),
        pytest.param(["--ignore-ref-siblings-by-schema-version"], id="ignore-ref-siblings"),
        pytest.param(
            ["--ignore-ref-siblings-by-schema-version", "--read-schema-version-per-document"], id="per-document"
        ),
    ],
)
def test_main_asyncapi_v2_converted_payload_ref_siblings(output_file: Path, extra_args: list[str]) -> None:
    """Keep the defaults an Avro or XML Schema payload of an AsyncAPI 2 document writes beside a ``$ref`` (#6394).

    AsyncAPI 2 ignores the keywords beside a ``$ref``, so --ignore-ref-siblings-by-schema-version leaves out the
    ``default`` beside ``Tone`` in the JSON Schema payload; the converted payloads write theirs as parts of a field
    typed by a named type, so ``suit`` and ``color`` keep them.
    """
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "converted_payload_ref_siblings_v2.yaml",
        output_path=output_file,
        input_file_type="asyncapi",
        extra_args=[*PY310_TARGET_ARGS, *extra_args],
        force_exec_validation=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="asyncapi_v2_converted_payload_ref_siblings",
        model_name="PaintsPublishPayload",
        valid_json="{}",
        invalid_json='{"color": "green"}',
        expected_error_type="enum",
        expected_attribute_path=("color",),
        expected_attribute_value="blue",
    )


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param(["--ignore-ref-siblings-by-schema-version"], id="first-read"),
        pytest.param(
            ["--ignore-ref-siblings-by-schema-version", "--read-schema-version-per-document"], id="per-document"
        ),
    ],
)
def test_main_asyncapi_read_schema_version_per_document_external_ref_siblings(
    output_file: Path, extra_args: list[str]
) -> None:
    """Read a document an AsyncAPI 2 document references, which declares no version, as AsyncAPI 2.

    AsyncAPI 2 ignores the keywords beside a ``$ref``, so ``Short`` is ``Text`` alone, without its ``maxLength``. With
    ``--read-schema-version-per-document`` the referenced document takes the version of the AsyncAPI 2 document whose
    reference loads it, as without it, where the document parsed decides.
    """
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "ref_siblings_external_v2" / "api.yaml",
        output_path=output_file,
        input_file_type="asyncapi",
        extra_args=[*PY310_TARGET_ARGS, *extra_args],
        force_exec_validation=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="asyncapi_read_schema_version_per_document_external_ref_siblings",
        model_name="Note",
        valid_json='"abcd"',
        invalid_json="1",
        expected_error_type="string_type",
    )


@pytest.mark.cli_doc(
    options=["--name-converted-payloads-by-message"],
    option_description="""Name a converted payload that names no type of its own after its message.

An AsyncAPI payload in Avro or XML Schema is converted to JSON Schema first. A payload that names no type of its own,
such as an Avro `string`, array, map or union, or an XML Schema with several global elements, takes the converter's
placeholder title `Model`: the root of a string, array or union payload writes `Field(..., title='Model')`, and under
`--use-title-as-name` a string, array, map or XML Schema payload is named `Model`, `Model1`, .... With
`--name-converted-payloads-by-message` such a payload takes no title, as a JSON Schema payload, so its class is named
after its message (`PingPublishPayload`) under `--use-title-as-name` too. A payload that names its type, such as an Avro
record, keeps that name.""",
    input_schema="asyncapi/converted_payload_names.yaml",
    cli_args=["--name-converted-payloads-by-message"],
    golden_output="main/asyncapi/main_asyncapi/name_converted_payloads_by_message[by-message].py",
    comparison_output="main/asyncapi/main_asyncapi/name_converted_payloads_by_message[released].py",
    related_options=["--use-title-as-name"],
)
@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param([], id="released"),
        pytest.param(["--name-converted-payloads-by-message"], id="by-message"),
        pytest.param(["--use-title-as-name"], id="title-as-name-released"),
        pytest.param(["--use-title-as-name", "--name-converted-payloads-by-message"], id="title-as-name-by-message"),
    ],
)
def test_main_asyncapi_name_converted_payloads_by_message(output_file: Path, extra_args: list[str]) -> None:
    """Drop the converter's placeholder title of a converted payload under ``--name-converted-payloads-by-message``.

    The Avro ``string``, array, map and union payloads and the XML Schema payload with two global elements take the
    title ``Model`` without the option: the root of the string, array and union payloads writes ``title='Model'``, and
    ``--use-title-as-name`` names the string, array, map and XML Schema payloads ``Model``, ``Model1``, ...; with it
    they take no title and the names of their messages (#5677). The record named ``Model`` keeps its name.
    """
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "converted_payload_names.yaml",
        output_path=output_file,
        input_file_type="asyncapi",
        extra_args=[*PY310_TARGET_ARGS, *extra_args],
    )
    if "--name-converted-payloads-by-message" in extra_args:
        assert_generated_model_json_validation(
            output_file,
            module_name="asyncapi_name_converted_payloads_by_message",
            model_name="TextPublishPayload",
            valid_json='"a"',
            invalid_json="1",
            expected_error_type="string_type",
        )


def test_main_asyncapi_trait_headers_inline_and_ref_in_either_order(output_file: Path) -> None:
    """Read the headers of an inline trait beside a referenced one the same way in either order (#5884).

    The referenced trait keeps its component model; the inline trait's headers are the message's headers.
    """
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "trait_headers_inline_and_ref.yaml",
        output_path=output_file,
        input_file_type="asyncapi",
        extra_args=PY310_TARGET_ARGS,
    )


@pytest.mark.cli_doc(
    options=["--merge-message-trait-headers"],
    option_description="""Give an AsyncAPI message the headers of all its traits, merged in order, and then its own.

AsyncAPI 2 and 3 apply the traits of a message in the order they are listed, each as a JSON Merge Patch, and the
message's own fields win over them. Without the option a message's headers are its own, else those of its one trait
that is no `$ref` and defines them, else those of a referenced trait; the headers of the other traits are left out, and
a second inline trait with headers is an error. With `--merge-message-trait-headers` the headers of every trait, inline
or `$ref`, are merged in order and then the message's own: a property a later trait or the message gives replaces the
earlier one, a `$ref` is read as the schema it names, and a property set to null is removed. A message whose headers
come from one place keeps the model it has without the option.""",
    input_schema="asyncapi/merged_trait_headers/v3.yaml",
    cli_args=["--merge-message-trait-headers"],
    golden_output="main/asyncapi/main_asyncapi/merge_message_trait_headers[v3-merged].py",
    comparison_output="main/asyncapi/main_asyncapi/merge_message_trait_headers[v3-released].py",
)
@pytest.mark.parametrize(
    ("input_name", "extra_args"),
    [
        pytest.param("merged_trait_headers/v3.yaml", [], id="v3-released"),
        pytest.param("merged_trait_headers/v3.yaml", ["--merge-message-trait-headers"], id="v3-merged"),
        pytest.param("merged_trait_headers/v2/api.yaml", [], id="v2-released"),
        pytest.param("merged_trait_headers/v2/api.yaml", ["--merge-message-trait-headers"], id="v2-merged"),
        pytest.param("merged_trait_headers/null_member.yaml", ["--merge-message-trait-headers"], id="null-merged"),
    ],
)
def test_main_asyncapi_merge_message_trait_headers(output_file: Path, input_name: str, extra_args: list[str]) -> None:
    """Merge the headers of every trait of a message in order, then its own, under ``--merge-message-trait-headers``.

    Without the option an inline trait's headers drop those of a referenced trait in either order and a message's own
    headers drop those of its traits (#5884); with it they are merged as JSON Merge Patches in AsyncAPI 2 and 3, a
    later string ``b`` replacing an earlier integer one, a referenced ``Trace`` read as its properties, a ``$ref`` of
    another file keeping its model and a null property or headers removed.
    """
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / input_name,
        output_path=output_file,
        input_file_type="asyncapi",
        extra_args=[*PY310_TARGET_ARGS, "--output-model-type", "pydantic_v2.BaseModel", *extra_args],
    )
    if input_name == "merged_trait_headers/v3.yaml" and extra_args:
        assert_generated_model_json_validation(
            output_file,
            module_name="asyncapi_merge_message_trait_headers",
            model_name="COwnHeadersHeaders",
            valid_json='{"id": 1, "b": true, "traceId": "123e4567-e89b-12d3-a456-426614174000"}',
            invalid_json='{"b": true}',
            expected_error_type="missing",
        )


def test_main_asyncapi_merge_message_trait_headers_of_two_inline_traits(output_file: Path) -> None:
    """Merge the headers of two inline traits under ``--merge-message-trait-headers``, an error without it (#5884)."""
    run_main_and_assert(
        input_path=DATA_PATH / "asyncapi_invalid" / "multiple-trait-headers.yaml",
        output_path=output_file,
        input_file_type="asyncapi",
        extra_args=[
            *PY310_TARGET_ARGS,
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--merge-message-trait-headers",
        ],
    )
