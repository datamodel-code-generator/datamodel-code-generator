"""Tests for the input-model Python type IR boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datamodel_code_generator import Error, InputFileType, generate
from datamodel_code_generator._input_model_transport import (
    PythonTypeExpressionCollector,
    externalize_python_type_token,
    is_python_type_token,
)
from datamodel_code_generator._python_type_annotation import (
    PythonTypeName,
    PythonTypeOpaqueText,
    PythonTypeQualifiedName,
    PythonTypeRuntimeSymbol,
)
from datamodel_code_generator.input_model import _transport_python_type_expr, load_model_schema
from datamodel_code_generator.input_model_result import LoadedInputModelSchema, PythonTypeSchemaAnnotation
from datamodel_code_generator.parser.jsonschema import JsonSchemaObject, JsonSchemaParser
from tests.conftest import assert_output

EXPECTED_INPUT_MODEL_PATH = Path(__file__).parent / "data" / "expected" / "main" / "input_model"


@pytest.mark.allow_direct_assert
def test_python_type_expression_collector_preserves_identity_and_prunes() -> None:
    """Equal IR is interned while equal rendering never merges different IR."""
    collector = PythonTypeExpressionCollector()
    name_token = collector.add(PythonTypeName("Alias"))
    opaque_token = collector.add(PythonTypeOpaqueText("Alias"))
    unused_token = collector.add(PythonTypeName("Unused"))

    loaded = collector.loaded_schema({
        "type": "object",
        "properties": {"value": {"x-python-type": opaque_token}},
    })

    assert collector.add(PythonTypeName("Alias")) == name_token
    assert name_token != opaque_token
    assert unused_token not in repr(loaded.schema)
    annotation = loaded.schema["properties"]["value"]["x-python-type"]  # type: ignore[index]
    assert isinstance(annotation, PythonTypeSchemaAnnotation)
    assert annotation.expression == PythonTypeOpaqueText("Alias")
    assert loaded.python_type_expressions == {opaque_token: PythonTypeOpaqueText("Alias")}
    first_python_type_expressions = loaded.python_type_expressions
    assert first_python_type_expressions is loaded.python_type_expressions
    assert dict(loaded) == loaded.schema
    assert len(loaded) == 2
    assert list(loaded) == ["type", "properties"]
    assert loaded["type"] == "object"
    assert loaded.name == "<stdin>"
    assert isinstance(loaded, LoadedInputModelSchema)
    assert LoadedInputModelSchema.__module__ == "datamodel_code_generator._input_model_transport"
    assert is_python_type_token(opaque_token)
    assert not is_python_type_token("Alias")
    assert externalize_python_type_token(opaque_token, loaded.python_type_expressions) == "Alias"
    assert externalize_python_type_token(opaque_token, None) == opaque_token
    assert externalize_python_type_token("missing", loaded.python_type_expressions) == "missing"
    assert externalize_python_type_token(object(), loaded.python_type_expressions).__class__ is object
    with pytest.raises(TypeError):
        loaded.python_type_expressions[opaque_token] = PythonTypeName("Changed")  # type: ignore[index]

    legacy = LoadedInputModelSchema(
        {"type": "string", "x-python-type": name_token},
        {name_token: PythonTypeName("Alias")},
    )
    assert legacy.legacy_python_type_expressions == {name_token: PythonTypeName("Alias")}
    assert legacy.python_type_expressions == {name_token: PythonTypeName("Alias")}


@pytest.mark.allow_direct_assert
def test_input_model_transport_normalizes_unqualified_runtime_symbols() -> None:
    """Preserve the historical spelling of runtime symbols without a module."""
    collector = PythonTypeExpressionCollector()
    name_token = _transport_python_type_expr(PythonTypeRuntimeSymbol("", ("Alias",)), collector)
    qualified_token = _transport_python_type_expr(
        PythonTypeRuntimeSymbol("", ("Namespace", "Nested")),
        collector,
    )
    loaded = collector.loaded_schema({
        "anyOf": [
            {"x-python-type": name_token},
            {"x-python-type": qualified_token},
        ]
    })

    annotations = [item["x-python-type"] for item in loaded.schema["anyOf"]]  # type: ignore[union-attr]
    assert [annotation.expression for annotation in annotations] == [
        PythonTypeName("Alias"),
        PythonTypeQualifiedName(("Namespace", "Nested")),
    ]
    assert loaded.python_type_expressions == {
        name_token: PythonTypeName("Alias"),
        qualified_token: PythonTypeQualifiedName(("Namespace", "Nested")),
    }


@pytest.mark.allow_direct_assert
def test_jsonschema_parser_rejects_transport_token_without_context() -> None:
    """Keep the historical explicit failure when a build token loses its context."""
    collector = PythonTypeExpressionCollector()
    token = collector.add(PythonTypeName("str"))
    obj = JsonSchemaObject.model_validate({"type": "string", "x-python-type": token})

    with pytest.raises(Error, match="context is unavailable"):
        JsonSchemaParser("")._get_x_python_type(obj)


def test_load_model_schema_public_contract_keeps_rendered_json_types() -> None:
    """The public loader remains a JSON-compatible dict with canonical text."""
    schema = load_model_schema(
        ["tests.data.python.input_model.structured_annotations:StructuredAnnotations"],
        InputFileType.JsonSchema,
    )
    if type(schema) is not dict:  # pragma: no cover
        pytest.fail(f"Expected public dict, got {type(schema)!r}")
    rendered = f"{json.dumps(schema, indent=2, sort_keys=True)}\n"
    if "<datamodel-code-generator-python-type:" in rendered:  # pragma: no cover
        pytest.fail("Private Python type token leaked through load_model_schema()")

    assert_output(rendered, EXPECTED_INPUT_MODEL_PATH / "structured_annotations_schema.txt")


def test_load_model_schema_public_dict_contract_stays_json_compatible() -> None:
    """Public dict loading remains separate from the private CLI envelope."""
    schema = load_model_schema(
        ["tests.data.python.input_model.dict_schemas:USER_SCHEMA"],
        InputFileType.JsonSchema,
    )

    assert_output(
        f"{json.dumps(schema, indent=2, sort_keys=True)}\n",
        EXPECTED_INPUT_MODEL_PATH / "dict_public_schema.txt",
    )


@pytest.mark.parametrize(
    ("python_type", "expected_file"),
    [
        pytest.param(PythonTypeName("int"), "transport_property_names.py", id="integer"),
        pytest.param(PythonTypeName("bool"), "transport_property_names_bool.py", id="boolean"),
        pytest.param(PythonTypeName("str"), "transport_property_names_string.py", id="string"),
        pytest.param(PythonTypeName("float"), "transport_property_names_string.py", id="unsupported"),
    ],
)
def test_input_model_transport_property_names_uses_expression(
    python_type: PythonTypeName,
    expected_file: str,
) -> None:
    """Transport supported key types and preserve the string fallback for unsupported ones."""
    collector = PythonTypeExpressionCollector()
    token = collector.add(python_type)
    loaded = collector.loaded_schema({
        "title": "Lookup",
        "type": "object",
        "propertyNames": {"x-python-type": token},
        "additionalProperties": {"type": "string"},
    })

    generated = generate(loaded, input_file_type=InputFileType.JsonSchema, disable_timestamp=True, formatters=[])
    if not isinstance(generated, str):  # pragma: no cover
        pytest.fail(f"Expected one generated module, got {type(generated)!r}")
    assert_output(f"{generated}\n", EXPECTED_INPUT_MODEL_PATH / expected_file)


def test_input_model_transport_property_names_survives_schema_copy() -> None:
    """Keep neutral key IR through the parser's real patternProperties schema copy."""
    collector = PythonTypeExpressionCollector()
    token = collector.add(PythonTypeName("int"))
    loaded = collector.loaded_schema({
        "title": "PatternLookup",
        "type": "object",
        "propertyNames": {"x-python-type": token},
        "patternProperties": {"^value": {"type": "string"}},
    })
    legacy_loaded = LoadedInputModelSchema(
        {
            "title": "PatternLookup",
            "type": "object",
            "propertyNames": {"x-python-type": token},
            "patternProperties": {"^value": {"type": "string"}},
        },
        {token: PythonTypeName("int")},
    )
    JsonSchemaParser("")._parse_simple_property_name_key_type(
        JsonSchemaObject.model_validate({"x-python-type": PythonTypeSchemaAnnotation(PythonTypeName("int"))})
    )

    for source in (loaded, legacy_loaded):
        generated = generate(source, input_file_type=InputFileType.JsonSchema, disable_timestamp=True, formatters=[])
        if not isinstance(generated, str):  # pragma: no cover
            pytest.fail(f"Expected one generated module, got {type(generated)!r}")
        assert_output(f"{generated}\n", EXPECTED_INPUT_MODEL_PATH / "transport_property_names_pattern.py")


def test_input_model_transport_externalizes_field_metadata() -> None:
    """Field metadata receives canonical text, never an internal token."""
    collector = PythonTypeExpressionCollector()
    token = collector.add(PythonTypeName("str"))
    loaded = collector.loaded_schema({
        "title": "Metadata",
        "type": "object",
        "properties": {"value": {"type": "string", "x-python-type": token}},
        "required": ["value"],
    })

    generated = generate(
        loaded,
        input_file_type=InputFileType.JsonSchema,
        disable_timestamp=True,
        field_include_all_keys=True,
        formatters=[],
    )
    if not isinstance(generated, str):  # pragma: no cover
        pytest.fail(f"Expected one generated module, got {type(generated)!r}")
    assert_output(f"{generated}\n", EXPECTED_INPUT_MODEL_PATH / "transport_metadata.py")


def test_input_model_transport_externalizes_schema_extensions() -> None:
    """Template and model extras receive canonical extension text."""
    for value, expressions in (
        (PythonTypeSchemaAnnotation(PythonTypeName("str")), None),
        ("private-token", {"private-token": PythonTypeName("str")}),
        ("str", None),
    ):
        parser = JsonSchemaParser("", model_extra_keys={"x-python-type"})
        parser._python_type_expressions = expressions
        obj = JsonSchemaObject.model_validate({"x-python-type": value, "x-note": "kept"})

        parser.set_schema_extensions("#", obj)

        assert_output(
            f"{json.dumps(parser.extra_template_data['#'], indent=2, sort_keys=True)}\n",
            EXPECTED_INPUT_MODEL_PATH / "transport_schema_extras.txt",
        )


def test_input_model_transport_survives_local_ref_and_allof_merges() -> None:
    """Tokens stay attached through local definitions, siblings, and inheritance."""
    collector = PythonTypeExpressionCollector()
    token = collector.add(PythonTypeName("int"))
    loaded = collector.loaded_schema({
        "$defs": {
            "Value": {"type": "string"},
            "Base": {
                "title": "Base",
                "type": "object",
                "properties": {"base": {"type": "string", "x-python-type": token}},
                "required": ["base"],
            },
        },
        "title": "Child",
        "allOf": [{"$ref": "#/$defs/Base"}],
        "type": "object",
        "properties": {"value": {"$ref": "#/$defs/Value", "x-python-type": token}},
        "required": ["value"],
    })

    generated = generate(loaded, input_file_type=InputFileType.JsonSchema, disable_timestamp=True, formatters=[])
    if not isinstance(generated, str):  # pragma: no cover
        pytest.fail(f"Expected one generated module, got {type(generated)!r}")
    assert_output(f"{generated}\n", EXPECTED_INPUT_MODEL_PATH / "transport_local_inheritance.py")


@pytest.mark.allow_direct_assert
def test_input_model_transport_survives_external_ref() -> None:
    """An ordinary external ref does not force internal IR through text."""
    collector = PythonTypeExpressionCollector()
    token = collector.add(PythonTypeName("int"))
    loaded = collector.loaded_schema({
        "title": "ExternalRoot",
        "type": "object",
        "properties": {
            "value": {"type": "string", "x-python-type": token},
            "person": {"$ref": "tests/data/jsonschema/person.json"},
        },
        "required": ["value", "person"],
    })

    annotation = loaded.schema["properties"]["value"]["x-python-type"]  # type: ignore[index]
    assert isinstance(annotation, PythonTypeSchemaAnnotation)
    assert annotation.expression == PythonTypeName("int")
    generated = generate(loaded, input_file_type=InputFileType.JsonSchema, disable_timestamp=True, formatters=[])
    if not isinstance(generated, str):  # pragma: no cover
        pytest.fail(f"Expected one generated module, got {type(generated)!r}")
    assert_output(f"{generated}\n", EXPECTED_INPUT_MODEL_PATH / "transport_external_ref.py")


def test_input_model_transport_parses_only_external_raw_python_type() -> None:
    """A referenced raw extension still uses the public text compatibility path."""
    collector = PythonTypeExpressionCollector()
    token = collector.add(PythonTypeName("int"))
    loaded = collector.loaded_schema({
        "title": "ExternalTypedRoot",
        "type": "object",
        "properties": {
            "value": {"type": "string", "x-python-type": token},
            "typed": {"$ref": "tests/data/jsonschema/external_python_type.json"},
        },
        "required": ["value", "typed"],
    })

    generated = generate(loaded, input_file_type=InputFileType.JsonSchema, disable_timestamp=True, formatters=[])
    if not isinstance(generated, str):  # pragma: no cover
        pytest.fail(f"Expected one generated module, got {type(generated)!r}")
    assert_output(f"{generated}\n", EXPECTED_INPUT_MODEL_PATH / "transport_external_python_type.py")


@pytest.mark.allow_direct_assert
def test_input_model_transport_fingerprint_includes_expression_ir() -> None:
    """Retry safety detects equal schema structure carrying different neutral IR."""
    first_schema = {"type": "string", "x-python-type": PythonTypeSchemaAnnotation(PythonTypeName("str"))}
    second_schema = {"type": "string", "x-python-type": PythonTypeSchemaAnnotation(PythonTypeName("int"))}
    first = JsonSchemaParser(first_schema)
    second = JsonSchemaParser(second_schema)
    first.raw_obj = first_schema
    second.raw_obj = second_schema

    first_fingerprint = first._get_source_data_fingerprint()
    second_fingerprint = second._get_source_data_fingerprint()

    assert first_fingerprint is not None
    assert second_fingerprint is not None
    assert first_fingerprint != second_fingerprint
