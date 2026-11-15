"""Read the format of a schema as an annotation under --read-format-as-annotation."""

from __future__ import annotations

import json
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any

import pydantic
import pytest

from datamodel_code_generator import DataModelType, InputFileType, generate_dynamic_models
from datamodel_code_generator.config import GenerateConfig
from tests.conftest import assert_output
from tests.main.conftest import (
    ASYNC_API_DATA_PATH,
    AVRO_DATA_PATH,
    DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
    PROTOBUF_DATA_PATH,
    XML_SCHEMA_DATA_PATH,
    _generated_model,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

OPTION_SETS = {"off": (), "on": ("--read-format-as-annotation",)}
COMMON_ARGS = ("--formatters", "builtin", "--disable-timestamp")
TOOL_INPUT = JSON_SCHEMA_DATA_PATH / "tool_input_formats" / "tool_input_formats.json"
OUTPUT_FORMS = {
    "pydantic_v2.BaseModel": ("--output-model-type", "pydantic_v2.BaseModel"),
    "pydantic_v2.BaseModel-annotated": ("--output-model-type", "pydantic_v2.BaseModel", "--use-annotated"),
    "pydantic_v2.dataclass": ("--output-model-type", "pydantic_v2.dataclass"),
    "dataclasses.dataclass": ("--output-model-type", "dataclasses.dataclass"),
    "typing.TypedDict": ("--output-model-type", "typing.TypedDict"),
    "msgspec.Struct": ("--output-model-type", "msgspec.Struct"),
}
TOOL_PAYLOADS: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "valid",
        {
            "contactEmail": "front desk",
            "profileUrl": "https://example.com",
            "startsAt": "2026-11-20T10:00:00",
            "callDate": "2026-11-20",
            "dialPin": "4242",
            "attachment": "aGVsbG8=",
            "callId": "call-17",
            "bridgeHost": "pbx.example.com",
            "clientIp": "10.0.0.1",
            "fee": 1.5,
            "feeText": "1.50",
            "createdEpoch": 1763600000,
            "attempts": 2,
            "phone": "+4912345",
            "format": "every day",
        },
    ),
    (
        "valid",
        {
            "contactEmail": "ops@example.com",
            "startsAt": "2026-11-20T10:00:00+01:00",
            "callId": "6f1c2a7e-0c3d-4a59-9a8e-2f7d1b9c0e11",
        },
    ),
    ("invalid", {"contactEmail": "x" * 60 + "@example.com", "startsAt": "2026-11-20T10:00:00Z", "callId": "c"}),
    ("invalid", {"contactEmail": "ops@example.com", "startsAt": "2026-11-20T10:00:00Z"}),
)
"""Documents for ``ScheduleCall`` with the schema's verdict: each format is an annotation, maxLength applies."""


def _verdict(model: Any, document: object) -> str:
    """Return the JSON a generated model dumps a document as, else that it rejects it."""
    try:
        return json.dumps(model.model_validate(document).model_dump(mode="json", exclude_none=True))
    except pydantic.ValidationError:
        return "rejected"


def _format_warnings(option_set: str) -> Any:
    """Expect the warnings of unknown formats and of a msgspec Decimal bound without the option; with it none."""
    return nullcontext() if option_set == "on" else pytest.warns(UserWarning, match="not understood|does not take")


@pytest.mark.cli_doc(
    options=["--read-format-as-annotation"],
    option_description="""Read the format of a schema as an annotation, as JSON Schema does.

JSON Schema reads `format` as an annotation: a validator does not reject a string whose format does not match, and
the value stays the string. Without this option each known format selects a type, as in 0.96.0: `email` is
`EmailStr` (which needs `email-validator` to import, and drops `maxLength`), `date-time` is `AwareDatetime` (which
rejects a date-time without an offset), `password` is `SecretStr` (which dumps `**********`), `uri` is `AnyUrl`
(which dumps `https://example.com/` for `https://example.com`), and `uuid`, `date`, `ipv4` and `decimal` are parsed
into Python objects. With it a string, integer or number schema takes the type its `type` keyword gives, `str`, `int`
or `float`, with its own `minLength`, `maxLength`, `pattern` and bounds, so the models accept every value the schema
accepts and dump a string or an integer as written (a number as the `float` it parses to, as without a format), and an
unknown format gives no warning. Two formats still select a type:
`format: binary`, which stays `bytes` (raw octets, not a JSON string), and a format that a `--type-mappings` entry
names for a type the schema declares (`--type-mappings date-time=date-time` keeps `date-time` typed, and then
`--output-datetime-class` applies). The format is not written into the models. The option reads JSON Schema, OpenAPI,
the JSON Schema payloads of AsyncAPI and MCP tool schemas, with every document they reference; inputs converted from
Avro, Protocol Buffers or XML Schema keep the types of their formats.""",
    input_schema="jsonschema/tool_input_formats/tool_input_formats.json",
    cli_args=["--read-format-as-annotation"],
    golden_output="main/format_annotations/read_format_as_annotation[on-pydantic_v2.BaseModel].py",
    comparison_output="main/format_annotations/read_format_as_annotation[off-pydantic_v2.BaseModel].py",
)
@pytest.mark.parametrize("output_form", OUTPUT_FORMS)
@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_read_format_as_annotation(output_file: Path, option_set: str, output_form: str) -> None:
    """Type each formatted string, integer and number of a tool input by its type keyword (#6816).

    With the option every format but ``binary`` is an annotation: ``email`` keeps its ``maxLength``, ``hostname``
    its ``minLength`` without the hostname pattern, a ``decimal`` number is ``float``, an integer ``date-time`` is
    ``int``, a formatted enum keeps its values, and the format of an allOf part, of a ``$ref`` target and of a
    property named ``format`` goes too, while ``format`` inside ``examples`` stays. The Pydantic models accept every
    value the schema accepts, naive date-times and free text in an ``email`` among them, and dump the strings as
    written. Without the option the output and the unknown-format warnings are as in 0.96.0.
    """
    with _format_warnings(option_set):
        run_main_and_assert(
            input_path=TOOL_INPUT,
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=[*COMMON_ARGS, *OUTPUT_FORMS[output_form], *OPTION_SETS[option_set]],
            force_exec_validation=True,
        )
    if not output_form.startswith("pydantic_v2.BaseModel"):
        return
    with _generated_model(output_file, "tool_input_formats", "ScheduleCall") as model:
        lines = [f"{kind} {json.dumps(document)}: {_verdict(model, document)}\n" for kind, document in TOOL_PAYLOADS]
    assert_output("".join(lines), name="payloads")


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param(
            ("--type-mappings", "date-time=date-time", "--output-datetime-class", "NaiveDatetime"), id="type-mappings"
        ),
        pytest.param(("--strict-types", "str", "int"), id="strict-types"),
        pytest.param(("--field-constraints",), id="field-constraints"),
        pytest.param(("--use-subclass-enum",), id="use-subclass-enum"),
        pytest.param(("--use-single-value-enum-as-const",), id="use-single-value-enum-as-const"),
        pytest.param(("--output-date-class", "PastDate", "--use-standard-primitive-types"), id="class-options"),
    ],
)
def test_read_format_as_annotation_beside_options(output_file: Path, extra_args: tuple[str, ...]) -> None:
    """Keep a format typed that --type-mappings names for its type, and apply the other options as to plain types.

    ``--type-mappings date-time=date-time`` keeps ``startsAt`` and ``format`` typed, by ``--output-datetime-class``,
    but not the integer ``createdEpoch``, whose type has no entry. ``--strict-types`` writes ``StrictStr`` and
    ``StrictInt`` for the former formatted values, ``--field-constraints`` writes no hostname pattern, a formatted
    enum subclasses ``str`` under ``--use-subclass-enum``, the one walk also reads single-value enums as const, and the
    class options of formats the option reads as annotations have no effect.
    """
    run_main_and_assert(
        input_path=TOOL_INPUT,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[*COMMON_ARGS, "--read-format-as-annotation", *extra_args],
        force_exec_validation=True,
    )


@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_read_format_as_annotation_openapi(output_file: Path, option_set: str) -> None:
    """Read the formats of an OpenAPI document, its parameters and a document it references as annotations.

    The multipart ``file`` keeps ``format: binary`` as ``bytes``; the ``byte``, ``password``, ``uuid`` and
    ``date-time`` strings of the document and its query parameter, and the ``email`` and ``uri`` strings of
    ``common.yaml``, are ``str``, ``homepage`` with its ``maxLength``.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "multipart_upload_formats" / "api.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[*COMMON_ARGS, "--openapi-scopes", "schemas", "paths", "parameters", *OPTION_SETS[option_set]],
        force_exec_validation=True,
    )


DISCRIMINATOR_MAPPING_DOCUMENT = """openapi: 3.0.3
info: {title: Pets, version: '1'}
paths: {}
components:
  schemas:
    Pet:
      type: object
      required: [kind]
      properties:
        kind: {type: string}
      discriminator:
        propertyName: kind
        mapping:
          format: '#/components/schemas/Printed'
          const: '#/components/schemas/Fixed'
          other: '#/components/schemas/Other'
    Printed:
      type: object
      required: [kind]
      properties:
        kind: {type: string}
        email: {type: string, format: email}
    Fixed:
      type: object
      required: [kind]
      properties:
        kind: {type: string}
        level: {type: integer, enum: [1]}
    Other:
      type: object
      required: [kind]
      properties:
        kind: {type: string}
    Holder:
      type: object
      properties:
        pet: {$ref: '#/components/schemas/Pet'}
"""


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param(("--read-format-as-annotation",), id="read-format-as-annotation"),
        pytest.param(("--use-single-value-enum-as-const",), id="use-single-value-enum-as-const"),
    ],
)
def test_schema_readings_keep_discriminator_mappings(
    output_file: Path, tmp_path: Path, extra_args: tuple[str, ...]
) -> None:
    """Read no discriminator object as a schema: its mapping keys ``format`` and ``const`` are tag values.

    The union of ``Holder.pet`` keeps ``Printed``, which a mapping key named ``format`` names; up to 0.96.0 a mapping
    key named ``const`` made ``--use-single-value-enum-as-const`` fail.
    """
    document = tmp_path / "pets.yaml"
    document.write_text(DISCRIMINATOR_MAPPING_DOCUMENT, encoding="utf-8")
    run_main_and_assert(
        input_path=document,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[*COMMON_ARGS, *extra_args],
        force_exec_validation=True,
    )


@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_read_format_as_annotation_asyncapi(output_file: Path, option_set: str) -> None:
    """Read the formats of an AsyncAPI JSON Schema payload as annotations, not those an Avro payload converts to.

    ``callStarted`` is a JSON Schema payload, whose ``uuid``, ``date-time`` and ``email`` strings are ``str`` under
    the option; ``callEnded`` is Avro, whose logical types the conversion writes as formats, so they stay ``UUID`` and
    ``AwareDatetime``.
    """
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "format_annotations" / "calls.yaml",
        output_path=output_file,
        input_file_type="asyncapi",
        extra_args=[*COMMON_ARGS, *OPTION_SETS[option_set]],
        force_exec_validation=True,
    )


@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_read_format_as_annotation_mcp_tools(output_file: Path, option_set: str) -> None:
    """Read the formats of an MCP tool input schema as annotations."""
    run_main_and_assert(
        input_path=DATA_PATH / "mcp_tools" / "format_annotations.json",
        output_path=output_file,
        input_file_type="mcp-tools",
        extra_args=[*COMMON_ARGS, *OPTION_SETS[option_set]],
        force_exec_validation=True,
    )


@pytest.mark.parametrize(
    ("input_path", "input_file_type"),
    [
        pytest.param(AVRO_DATA_PATH / "constructs.avsc", "avro", id="avro"),
        pytest.param(PROTOBUF_DATA_PATH / "complex_proto3.proto", "protobuf", id="protobuf"),
        pytest.param(XML_SCHEMA_DATA_PATH / "datetime_classes.xsd", "xmlschema", id="xmlschema"),
    ],
)
def test_read_format_as_annotation_keeps_converted_formats(
    output_file: Path, tmp_path: Path, input_path: Path, input_file_type: str
) -> None:
    """Generate the same models from Avro, Protocol Buffers and XML Schema inputs with the option as without it.

    Their formats carry the types of the source (an Avro logical type, a well-known type, an XML Schema datatype),
    which the conversion writes; they are not annotations an author wrote.
    """
    without = tmp_path / "without.py"
    run_main_and_assert(
        input_path=input_path, output_path=without, input_file_type=input_file_type, extra_args=COMMON_ARGS
    )
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type=input_file_type,
        extra_args=[*COMMON_ARGS, "--read-format-as-annotation"],
        expected_output=without.read_text(encoding="utf-8"),
    )


def test_read_format_as_annotation_dynamic_models() -> None:
    """Build models at run time that take the values a tool schema accepts and dump them as they came in."""
    schema = {
        "title": "CreateContact",
        "type": "object",
        "properties": {
            "email": {"type": "string", "format": "email", "maxLength": 64},
            "website": {"type": "string", "format": "uri"},
            "remindAt": {"type": "string", "format": "date-time"},
            "apiKey": {"type": "string", "format": "password"},
        },
        "required": ["email", "remindAt"],
    }
    config = GenerateConfig(
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        read_format_as_annotation=True,
    )
    model = generate_dynamic_models(schema, config=config)["CreateContact"]
    documents = (
        {"email": "a@b.c", "website": "https://example.com", "remindAt": "2026-11-13T10:00:00", "apiKey": "k-1"},
        {"email": "x" * 65, "remindAt": "2026-11-13T10:00:00"},
    )
    annotations = {name: str(field.annotation) for name, field in model.model_fields.items()}
    lines = [
        json.dumps(annotations),
        *(f"{json.dumps(document)}: {_verdict(model, document)}" for document in documents),
    ]
    assert_output("\n".join(lines) + "\n")
