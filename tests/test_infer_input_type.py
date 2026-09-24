"""Tests for input type inference functionality."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from datamodel_code_generator import (
    Error,
    InputFileType,
    _is_json_text,
    _is_xml_text,
    _looks_like_csv_text,
    infer_input_type,
)

DATA_PATH: Path = Path(__file__).parent / "data"
HEAVY_INFERENCE_MODULES = (
    "datamodel_code_generator.model",
    "datamodel_code_generator.parser.avro",
    "datamodel_code_generator.parser.base",
    "datamodel_code_generator.parser.jsonschema",
    "datamodel_code_generator.parser.xmlschema",
)
DETECTION_MODULES = (
    "datamodel_code_generator._avro_detection",
    "datamodel_code_generator._xmlschema_detection",
)
SUBPROCESS_TIMEOUT_SECONDS = 15


def _probe_infer_input_type(text: str) -> dict[str, object]:
    code = f"""
import json
import sys
import warnings

from datamodel_code_generator import infer_input_type

warnings.filterwarnings("ignore", category=DeprecationWarning, module="datamodel_code_generator")

try:
    outcome = infer_input_type({text!r}).value
except Exception as exc:
    outcome = type(exc).__name__

heavy_modules = {HEAVY_INFERENCE_MODULES!r}
loaded = sorted(
    module_name
    for module_name in sys.modules
    if module_name in heavy_modules or module_name.startswith("datamodel_code_generator.model.")
)
loaded_detection = sorted(module_name for module_name in sys.modules if module_name in {DETECTION_MODULES!r})
print(json.dumps({{"loaded": loaded, "loaded_detection": loaded_detection, "outcome": outcome}}))
"""
    env = os.environ.copy()
    env.pop("PYTHONWARNINGS", None)
    try:
        return json.loads(
            subprocess.check_output(
                [sys.executable, "-c", code],
                env=env,
                text=True,
                timeout=SUBPROCESS_TIMEOUT_SECONDS,
            )
        )
    except subprocess.TimeoutExpired as exc:
        message = f"infer_input_type subprocess timed out after {exc.timeout} seconds"
        raise AssertionError(message) from exc


def test_infer_input_type() -> None:  # noqa: PLR0912
    """Test automatic input type detection for various file formats."""

    def assert_infer_input_type(file: Path, raw_data_type: InputFileType) -> None:
        __tracebackhide__ = True
        if file.is_dir():
            return
        if file.suffix not in {".yaml", ".json"}:
            return
        result = infer_input_type(file.read_text(encoding="utf-8"))
        assert result == raw_data_type, f"{file} was the wrong type!"

    def assert_invalid_infer_input_type(file: Path) -> None:
        with pytest.raises(
            Error,
            match=(
                r"Can't infer input file type from the input data. "
                r"Please specify the input file type explicitly with --input-file-type option."
            ),
        ):
            infer_input_type(file.read_text(encoding="utf-8"))

    for file in (DATA_PATH / "csv").rglob("*"):
        assert_infer_input_type(file, InputFileType.CSV)

    for file in (DATA_PATH / "json").rglob("*"):
        if file.name.endswith("broken.json"):
            continue
        assert_infer_input_type(file, InputFileType.Json)
    for file in (DATA_PATH / "jsonschema").rglob("*"):
        if file.name.endswith((
            "external_child.json",
            "external_child.yaml",
            "extra_data_builtin_template_config.json",
            "extra_data_builtin_template_msgspec.json",
            "extra_data_builtin_template_reserved.json",
            "extra_data_builtin_template_typed_dict.json",
            "extra_data_msgspec.json",
            "extra_data_msgspec_non_dict.json",
            "field_validators_config.json",
            "field_validators_multi_fields_config.json",
            "list_only.json",
            "list_only.yaml",
            "whitespace_only.yaml",
        )):
            continue
        if "ref_to_json_list" in file.parts and file.name == "list.json":
            continue
        assert_infer_input_type(file, InputFileType.JsonSchema)
    for file in (DATA_PATH / "openapi").rglob("*"):
        if "allof_no_merge_external_relative" in file.parts and file.name != "openapi.yaml":
            continue
        if "allof_required_inherited_external" in file.parts and file.name != "openapi.yaml":
            continue
        if "all_of_with_relative_ref" in file.parts:
            continue
        if "reference_same_hierarchy_directory" in file.parts:
            continue
        if "external_ref_with_transitive_local_ref" in file.parts and file.name != "openapi.yaml":
            continue
        if "paths_external_ref" in file.parts and file.name != "openapi.yaml":
            continue
        if "paths_ref_with_external_schema" in file.parts and file.name != "openapi.yaml":
            continue
        if "webhooks_ref_with_external_schema" in file.parts and file.name != "openapi.yaml":
            continue
        if "component_embedded_ids" in file.parts and file.suffix == ".json":
            continue
        if file.name.endswith((
            "aliases.json",
            "extra_data.json",
            "extra_data_msgspec.json",
            "invalid.yaml",
            "list.json",
            "empty_data.json",
            "root_model.yaml",
            "json_pointer.yaml",
            "const.json",
            "array_called_fields_with_oneOf_items.yaml",
        )):
            continue

        if file.name.endswith("not.json"):
            assert_invalid_infer_input_type(file)
            continue
        assert_infer_input_type(file, InputFileType.OpenAPI)
    for file in (DATA_PATH / "asyncapi").rglob("*"):
        assert_infer_input_type(file, InputFileType.AsyncAPI)

    xmlschema_files = list((DATA_PATH / "xmlschema").rglob("*.xsd"))
    assert xmlschema_files, "XML Schema fixtures are required for input type inference tests."
    for file in xmlschema_files:
        result = infer_input_type(file.read_text(encoding="utf-8"))
        assert result == InputFileType.XMLSchema, f"{file} was the wrong type!"

    assert infer_input_type('syntax = "proto3"; message User { string id = 1; }') == InputFileType.Protobuf


def test_is_xml_text() -> None:
    """Test XML-looking text detection for input type inference."""
    assert _is_xml_text(" \n\t\ufeff<xs:schema />")
    assert not _is_xml_text("")
    assert not _is_xml_text(" \n\t\ufeff")
    assert not _is_xml_text("name: value")


def test_is_json_text() -> None:
    """Test JSON-looking text detection for input type inference."""
    assert _is_json_text(' \n\t\ufeff{"name": "Pet"}')
    assert _is_json_text(" \n\t\ufeff[1, 2]")
    assert not _is_json_text("")
    assert not _is_json_text(" \n\t\ufeff")
    assert not _is_json_text("name: value")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("id,name\n1,taro\n", True),
        ("\n id, name \n 1, taro \n", True),
        ("id,name\n", False),
        ("name\nvalue\n", False),
        ("id,name,tel\n1,taro\n", False),
        ("id,name\n1,taro\n2,jiro,extra\n", False),
        ("", False),
    ],
)
def test_looks_like_csv_text(text: str, expected: bool) -> None:
    """Test bounded CSV text detection for auto inference fallback."""
    assert _looks_like_csv_text(text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("id,name\n1,taro\n", InputFileType.CSV),
        ("\n id, name \n 1, taro \n", InputFileType.CSV),
    ],
)
def test_infer_input_type_detects_csv_text(text: str, expected: InputFileType) -> None:
    """Test auto inference detects CSV data without treating all YAML errors as CSV."""
    assert infer_input_type(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        '{"Pet": {',
        "root:\n  child: [",
        "id,name,tel\n1,taro\n",
        "id,name\n1,taro\n2,jiro,extra\n",
    ],
)
def test_infer_input_type_rejects_non_csv_parse_errors(text: str) -> None:
    """Test malformed JSON/YAML and uneven comma data do not infer as CSV."""
    with pytest.raises(
        Error,
        match=(
            r"(?s)Can't infer input file type from the input data\..*"
            r"Please specify the input file type explicitly with --input-file-type option\."
        ),
    ):
        infer_input_type(text)


def test_infer_input_type_non_schema_xml() -> None:
    """Test non-schema XML does not infer as XML Schema."""
    with pytest.raises(
        Error,
        match=(
            r"Can't infer input file type from the input data. "
            r"Please specify the input file type explicitly with --input-file-type option."
        ),
    ):
        infer_input_type("<root />")


@pytest.mark.parametrize(
    ("text", "expected_outcome", "expected_detection"),
    [
        (
            '{"type": "record", "name": "User", "fields": []}',
            InputFileType.Avro.value,
            ["datamodel_code_generator._avro_detection"],
        ),
        ('{"type": "object"}', InputFileType.JsonSchema.value, ["datamodel_code_generator._avro_detection"]),
        (
            '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" />',
            InputFileType.XMLSchema.value,
            ["datamodel_code_generator._xmlschema_detection"],
        ),
        (
            "<root />",
            Error.__name__,
            [
                "datamodel_code_generator._avro_detection",
                "datamodel_code_generator._xmlschema_detection",
            ],
        ),
    ],
)
def test_infer_input_type_fast_path_does_not_import_heavy_modules(
    text: str,
    expected_outcome: str,
    expected_detection: list[str],
) -> None:
    """Test input inference avoids concrete parsers and output models."""
    probe = _probe_infer_input_type(text)

    assert probe["outcome"] == expected_outcome
    assert probe["loaded"] == []
    assert probe["loaded_detection"] == expected_detection


def test_public_detection_helpers_keep_parser_module_surface() -> None:
    """Test public detection helpers keep their existing parser module surface."""
    from datamodel_code_generator.parser import avro, xmlschema

    assert avro.is_avro_schema_data.__module__ == "datamodel_code_generator.parser.avro"
    assert avro.is_avro_schema_data("string")
    assert frozenset({"null", "boolean", "int", "long", "float", "double", "bytes", "string"}) == avro.PRIMITIVE_TYPES
    assert frozenset({"record", "enum", "fixed"}) == avro.NAMED_TYPES
    assert avro.NAMED_TYPES | {"array", "map"} == avro.COMPLEX_TYPES
    assert (
        frozenset({
            "$schema",
            "$defs",
            "definitions",
            "properties",
            "allOf",
            "anyOf",
            "oneOf",
        })
        == avro.JSON_SCHEMA_MARKER_KEYS
    )
    assert xmlschema.is_xml_schema_text.__module__ == "datamodel_code_generator.parser.xmlschema"
    assert xmlschema.is_xml_schema_text('<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" />')
    assert xmlschema.XML_SCHEMA_NAMESPACE == "http://www.w3.org/2001/XMLSchema"
    assert xmlschema.XML_SCHEMA_TAG == "{http://www.w3.org/2001/XMLSchema}schema"


def test_private_detection_reexports_preserve_identity_and_metadata() -> None:
    """Legacy lightweight detection modules stay import-compatible."""
    from datamodel_code_generator import _avro_detection as shared_avro_detection
    from datamodel_code_generator import _xmlschema_detection as shared_xmlschema_detection
    from datamodel_code_generator.parser import _avro_detection as legacy_avro_detection
    from datamodel_code_generator.parser import _xmlschema_detection as legacy_xmlschema_detection

    assert legacy_avro_detection.is_avro_schema_data is shared_avro_detection.is_avro_schema_data
    assert legacy_xmlschema_detection.is_xml_schema_text is shared_xmlschema_detection.is_xml_schema_text
    assert legacy_avro_detection.is_avro_schema_data.__module__ == "datamodel_code_generator.parser._avro_detection"
    assert (
        legacy_xmlschema_detection.is_xml_schema_text.__module__
        == "datamodel_code_generator.parser._xmlschema_detection"
    )


def test_probe_infer_input_type_reports_subprocess_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test subprocess probe failures surface as bounded assertion failures."""

    def raise_timeout(*_args: object, **_kwargs: object) -> str:
        raise subprocess.TimeoutExpired(cmd=sys.executable, timeout=SUBPROCESS_TIMEOUT_SECONDS)

    monkeypatch.setattr(subprocess, "check_output", raise_timeout)

    with pytest.raises(AssertionError, match=f"timed out after {SUBPROCESS_TIMEOUT_SECONDS} seconds"):
        _probe_infer_input_type("{}")
