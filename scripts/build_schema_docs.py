"""Schema documentation builder.

Generates feature tables from JsonSchemaFeatures and OpenAPISchemaFeatures
metadata for schema support documentation.

Usage:
    python scripts/build_schema_docs.py          # Generate/update docs
    python scripts/build_schema_docs.py --check  # Check if docs are up to date
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from datamodel_code_generator import RAW_DATA_TYPES
from datamodel_code_generator.enums import (
    AsyncAPIVersion,
    InputFileType,
    JsonSchemaVersion,
    OpenAPIVersion,
    ProtobufVersion,
    XMLSchemaVersion,
)
from datamodel_code_generator.format import DatetimeClassType
from datamodel_code_generator.model.pydantic_v2.types import DataTypeManager as PydanticV2DataTypeManager
from datamodel_code_generator.parser.schema_version import (
    DataFormatMapping,
    FeatureMetadata,
    JsonSchemaFeatures,
    OpenAPISchemaFeatures,
    get_data_formats,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from enum import Enum

    from datamodel_code_generator.types import Types

ROOT = Path(__file__).parent.parent
SRC = ROOT / "src"
INIT_PATH = SRC / "datamodel_code_generator" / "__init__.py"
AVRO_PARSER_PATH = SRC / "datamodel_code_generator" / "parser" / "avro.py"
GRAPHQL_PARSER_PATH = SRC / "datamodel_code_generator" / "parser" / "graphql.py"
SUPPORTED_FORMATS_DOCS_PATH = ROOT / "docs" / "supported_formats.md"
SUPPORTED_DATA_TYPES_DOCS_PATH = ROOT / "docs" / "supported-data-types.md"

# Markers for auto-generated content
BEGIN_MARKER = "<!-- BEGIN AUTO-GENERATED SUPPORTED FEATURES -->"
END_MARKER = "<!-- END AUTO-GENERATED SUPPORTED FEATURES -->"
DATA_TYPES_BEGIN_MARKER = "<!-- BEGIN AUTO-GENERATED SCHEMA FEATURE SUPPORT -->"
DATA_TYPES_END_MARKER = "<!-- END AUTO-GENERATED SCHEMA FEATURE SUPPORT -->"
DATA_FORMAT_BEGIN_MARKER = "<!-- BEGIN AUTO-GENERATED DATA FORMAT SUPPORT -->"
DATA_FORMAT_END_MARKER = "<!-- END AUTO-GENERATED DATA FORMAT SUPPORT -->"
DATA_TYPES_FORMAT_BEGIN_MARKER = "<!-- BEGIN AUTO-GENERATED DATA TYPE SUPPORT -->"
DATA_TYPES_FORMAT_END_MARKER = "<!-- END AUTO-GENERATED DATA TYPE SUPPORT -->"
INPUT_FORMAT_GUIDE_BEGIN_MARKER = "<!-- BEGIN AUTO-GENERATED INPUT FORMAT GUIDE -->"
INPUT_FORMAT_GUIDE_END_MARKER = "<!-- END AUTO-GENERATED INPUT FORMAT GUIDE -->"

# Status emoji mapping
STATUS_EMOJI = {
    "supported": "✅",
    "partial": "⚠️",
    "not_supported": "❌",
}

SCHEMA_TYPE_KEYWORDS = {
    "string": "pattern, minLength, maxLength",
    "number": "maximum, exclusiveMaximum, minimum, exclusiveMinimum, multipleOf",
    "integer": "maximum, exclusiveMaximum, minimum, exclusiveMinimum, multipleOf",
    "boolean": "",
    "array": "items, prefixItems, minItems, maxItems, uniqueItems",
    "object": "properties, required, additionalProperties, patternProperties",
    "null": "",
}

FORMAT_NOTES = {
    ("string", "byte"): "Base64 encoded string",
    ("string", "binary"): "File content",
    ("string", "email"): "Requires email-validator",
    ("string", "idn-email"): "Requires email-validator",
    ("string", "ulid"): "Requires python-ulid",
}

PARSER_ROUTE_OVERRIDES = {
    InputFileType.Auto: "pre-parser inference",
    InputFileType.Json: "JsonSchemaParser after genson conversion",
    InputFileType.Yaml: "JsonSchemaParser after genson conversion",
    InputFileType.Dict: "JsonSchemaParser after genson conversion",
    InputFileType.CSV: "JsonSchemaParser after genson conversion",
    InputFileType.MCPTools: "JsonSchemaParser after MCP conversion",
}

VERSION_ENUM_BY_INPUT_TYPE: dict[InputFileType, type[Enum]] = {
    InputFileType.JsonSchema: JsonSchemaVersion,
    InputFileType.OpenAPI: OpenAPIVersion,
    InputFileType.AsyncAPI: AsyncAPIVersion,
    InputFileType.XMLSchema: XMLSchemaVersion,
    InputFileType.Protobuf: ProtobufVersion,
}

SCHEMA_VERSION_UNSUPPORTED_INPUT_TYPES = frozenset({
    InputFileType.Avro,
    InputFileType.GraphQL,
})

INPUT_FORMAT_LABELS: dict[InputFileType, str] = {
    InputFileType.OpenAPI: "OpenAPI",
    InputFileType.AsyncAPI: "AsyncAPI",
    InputFileType.JsonSchema: "JSON Schema",
    InputFileType.MCPTools: "MCP tool schemas",
    InputFileType.XMLSchema: "XML Schema",
    InputFileType.Protobuf: "Protocol Buffers",
    InputFileType.Avro: "Apache Avro",
    InputFileType.Json: "JSON data",
    InputFileType.Yaml: "YAML data",
    InputFileType.Dict: "Python dictionary data",
    InputFileType.CSV: "CSV data",
    InputFileType.GraphQL: "GraphQL schema",
}

INPUT_SOURCE_NOTES: dict[InputFileType, str] = {
    InputFileType.OpenAPI: "OpenAPI document as JSON/YAML, mapping, URL, or file",
    InputFileType.AsyncAPI: "AsyncAPI document as JSON/YAML, mapping, URL, or file",
    InputFileType.JsonSchema: "JSON Schema document as JSON/YAML, mapping, URL, or file",
    InputFileType.MCPTools: "MCP tool list/profile as JSON/YAML, mapping, list, URL, or file",
    InputFileType.XMLSchema: "XSD XML text, URL, or file",
    InputFileType.Protobuf: ".proto text, file, directory, URL, or path list",
    InputFileType.Avro: "Avro schema JSON/YAML, mapping, list, URL, or file",
    InputFileType.Json: "JSON sample data text or file",
    InputFileType.Yaml: "YAML sample data text or file",
    InputFileType.Dict: "In-memory mapping or Python literal data",
    InputFileType.CSV: "CSV text or file with a header row and at least one data row",
    InputFileType.GraphQL: "GraphQL SDL text, URL, or file",
}

INPUT_AUTO_DETECTION_NOTES: dict[InputFileType, str] = {
    InputFileType.OpenAPI: "`openapi` field",
    InputFileType.AsyncAPI: "`asyncapi` field",
    InputFileType.JsonSchema: "`$schema`, `type`, `properties`, or composition keywords",
    InputFileType.MCPTools: "Explicit only",
    InputFileType.XMLSchema: "XML Schema namespace on the root element",
    InputFileType.Protobuf: "Protocol Buffers syntax/message-like text",
    InputFileType.Avro: "Avro schema object, union, or primitive schema form",
    InputFileType.Json: "Mapping that is not a schema/OpenAPI/AsyncAPI/Avro document",
    InputFileType.Yaml: "Explicit for YAML sample data",
    InputFileType.Dict: "Explicit for mapping input",
    InputFileType.CSV: (
        "Detected from CSV-like text with consistent comma counts across non-empty lines or explicit `csv`"
    ),
    InputFileType.GraphQL: "Explicit only",
}

GRAPHQL_KIND_NOTES = {
    "SCALAR": "Generates scalar aliases",
    "ENUM": "Generates enums or literals depending on enum options",
    "INTERFACE": "Generates model classes from interface fields",
    "OBJECT": "Generates model classes except root Query/Mutation objects",
    "INPUT_OBJECT": "Generates model classes for input objects",
    "UNION": "Generates union type aliases",
}

PYTHON_INPUT_TYPE_NOTES = {
    "dict": "Returned directly as the schema; `--input-file-type` is required",
    "Pydantic v2 BaseModel": "Converted with Pydantic `model_json_schema()`",
    "dataclass": "Converted with Pydantic `TypeAdapter`; includes stdlib and Pydantic dataclasses",
    "TypedDict": "Converted with Pydantic `TypeAdapter`",
}


@dataclass(frozen=True, slots=True)
class GeneratedDocsTarget:
    """One Markdown file section generated from schema feature metadata."""

    path: Path
    begin_marker: str
    end_marker: str
    content: str


def get_feature_metadata(cls: type) -> list[tuple[str, FeatureMetadata]]:
    """Extract feature metadata from a dataclass.

    Args:
        cls: JsonSchemaFeatures or OpenAPISchemaFeatures class.

    Returns:
        List of (field_name, metadata) tuples.
    """
    result = []
    for f in fields(cls):
        if f.metadata:
            meta = FeatureMetadata(
                introduced=f.metadata.get("introduced", ""),
                doc_name=f.metadata.get("doc_name", f.name),
                description=f.metadata.get("description", ""),
                status=f.metadata.get("status", "supported"),
            )
            result.append((f.name, meta))
    return result


def generate_feature_table(
    features: list[tuple[str, FeatureMetadata]],
    *,
    include_status: bool = True,
) -> str:
    """Generate a Markdown table from feature metadata.

    Args:
        features: List of (field_name, metadata) tuples.
        include_status: Whether to include the Status column.

    Returns:
        Markdown table string.
    """
    if include_status:
        lines = ["| Feature | Introduced | Status | Description |"]
        lines.append("|---------|------------|--------|-------------|")
        for _name, meta in features:
            emoji = STATUS_EMOJI.get(meta["status"], "")
            status_text = f"{emoji} {meta['status'].replace('_', ' ').title()}"
            lines.append(f"| `{meta['doc_name']}` | {meta['introduced']} | {status_text} | {meta['description']} |")
    else:
        lines = ["| Feature | Introduced | Description |"]
        lines.append("|---------|------------|-------------|")
        for _name, meta in features:
            lines.append(f"| `{meta['doc_name']}` | {meta['introduced']} | {meta['description']} |")

    return "\n".join(lines)


def heading(level: int, title: str) -> str:
    """Return a Markdown heading at the requested level."""
    return f"{'#' * level} {title}"


def get_default_pydantic_v2_data_type_manager() -> PydanticV2DataTypeManager:
    """Return the default Pydantic v2 type manager used for docs labels."""
    return PydanticV2DataTypeManager(target_datetime_class=DatetimeClassType.Awaredatetime)


def get_openapi_specific_feature_metadata() -> list[tuple[str, FeatureMetadata]]:
    """Return OpenAPI feature metadata that is not inherited from JSON Schema."""
    json_field_names = {f.name for f in fields(JsonSchemaFeatures)}
    return [(name, meta) for name, meta in get_feature_metadata(OpenAPISchemaFeatures) if name not in json_field_names]


def iter_data_formats(data_formats: DataFormatMapping) -> list[tuple[str, str, Types]]:
    """Return schema data formats in docs-friendly order."""
    rows = []
    for schema_type, formats in data_formats.items():
        if default_type := formats.get("default"):
            rows.append((schema_type, "default", default_type))
        rows.extend(
            (schema_type, format_name, type_) for format_name, type_ in formats.items() if format_name != "default"
        )
    return rows


def get_openapi_only_data_formats() -> DataFormatMapping:
    """Return OpenAPI formats that do not exist in JSON Schema mappings."""
    common_formats = get_data_formats(is_openapi=False)
    result: DataFormatMapping = {}
    for schema_type, formats in get_data_formats(is_openapi=True).items():
        openapi_only_formats = {
            format_name: type_
            for format_name, type_ in formats.items()
            if common_formats.get(schema_type, {}).get(format_name) != type_
        }
        if openapi_only_formats:
            result[schema_type] = openapi_only_formats
    return result


def get_python_type_label(type_: Types) -> str:
    """Return a Markdown label for the default Pydantic v2 type hint."""
    data_type = get_default_pydantic_v2_data_type_manager().get_data_type(type_)
    return f"`{data_type.base_type_hint}`"


def get_format_label(format_name: str) -> str:
    """Return a Markdown label for a schema format name."""
    if format_name == "default":
        return "`default` (no `format`)"
    return f"`{format_name}`"


def markdown_table(headers: tuple[str, ...], rows: Iterable[tuple[str, ...]]) -> str:
    """Render a compact Markdown table."""
    lines = [
        f"| {' | '.join(markdown_cell(header) for header in headers)} |",
        f"| {' | '.join('---' for _header in headers)} |",
    ]
    lines.extend(f"| {' | '.join(markdown_cell(cell) for cell in row)} |" for row in rows)
    return "\n".join(lines)


def markdown_cell(value: str) -> str:
    """Escape Markdown table delimiters in a generated cell."""
    return value.replace("|", "\\|")


def code_label(value: object) -> str:
    """Return a Markdown code label."""
    return f"`{value}`"


def code_list_label(values: Iterable[object], *, limit: int | None = None) -> str:
    """Return a comma-separated Markdown code list."""
    labels = [code_label(value) for value in values]
    if limit is None or len(labels) <= limit:
        return ", ".join(labels)
    return f"{', '.join(labels[:limit])}, +{len(labels) - limit} more"


def enum_values_label(enum_type: type[Enum]) -> str:
    """Return supported enum values as Markdown code labels."""
    return code_list_label(item.value for item in enum_type)


def _name_from_expr(node: ast.AST) -> str | None:
    """Return the visible name represented by a simple expression."""
    match node:
        case ast.Name(id=name):
            return name
        case ast.Attribute(attr=name):
            return name
        case ast.Subscript(value=value):
            return _name_from_expr(value)
    return None


def _input_file_type_name(pattern: ast.pattern) -> str | None:
    """Return the InputFileType member name matched by a pattern."""
    match pattern:
        case ast.MatchValue(value=ast.Attribute(value=ast.Name(id="InputFileType"), attr=input_type_name)):
            return input_type_name
    return None


def _returned_parser_name(case: ast.match_case) -> str | None:
    """Return the parser class name returned by a match case."""
    for node in ast.walk(ast.Module(body=case.body, type_ignores=[])):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Call):
            return _name_from_expr(node.value.func)
    return None


def parser_routes_from_source() -> dict[InputFileType, str]:
    """Read _build_parser() and infer InputFileType to parser routes."""
    tree = ast.parse(INIT_PATH.read_text(encoding="utf-8"))
    direct_routes: dict[InputFileType, str] = {}
    default_route = "JsonSchemaParser"
    found_build_parser = False

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "_build_parser":
            continue
        found_build_parser = True
        for child in ast.walk(node):
            if not isinstance(child, ast.Match):
                continue
            for case in child.cases:
                if (parser_name := _returned_parser_name(case)) is None:
                    continue
                if (input_type_name := _input_file_type_name(case.pattern)) is None:
                    default_route = parser_name
                    continue
                direct_routes[InputFileType[input_type_name]] = parser_name

    if not found_build_parser or not direct_routes:
        msg = "Could not infer parser routes from _build_parser()"
        raise RuntimeError(msg)

    return {
        input_file_type: PARSER_ROUTE_OVERRIDES.get(input_file_type, direct_routes.get(input_file_type, default_route))
        for input_file_type in InputFileType
    }


def input_schema_version_label(input_file_type: InputFileType) -> str:
    """Return the schema-version support label for an input format."""
    if input_file_type in SCHEMA_VERSION_UNSUPPORTED_INPUT_TYPES:
        return "Not supported"
    if enum_type := VERSION_ENUM_BY_INPUT_TYPE.get(input_file_type):
        return enum_values_label(enum_type)
    if input_file_type in RAW_DATA_TYPES or input_file_type is InputFileType.MCPTools:
        return f"Converted to JSON Schema; {enum_values_label(JsonSchemaVersion)}"
    return "-"


def input_format_order() -> tuple[InputFileType, ...]:
    """Return input formats in docs-friendly order."""
    order = (
        InputFileType.JsonSchema,
        InputFileType.OpenAPI,
        InputFileType.AsyncAPI,
        InputFileType.GraphQL,
        InputFileType.XMLSchema,
        InputFileType.Protobuf,
        InputFileType.Avro,
        InputFileType.MCPTools,
        InputFileType.Json,
        InputFileType.Yaml,
        InputFileType.CSV,
        InputFileType.Dict,
    )
    if missing := set(InputFileType) - {InputFileType.Auto, *order}:
        labels = ", ".join(sorted(item.value for item in missing))
        msg = f"Input format guide is missing InputFileType values: {labels}"
        raise RuntimeError(msg)
    return order


def generate_input_format_route_table() -> str:
    """Generate a parser route and version support table for input formats."""
    routes = parser_routes_from_source()
    rows = [
        (
            INPUT_FORMAT_LABELS[input_file_type],
            code_label(input_file_type.value),
            code_label(routes[input_file_type]),
            input_schema_version_label(input_file_type),
            INPUT_AUTO_DETECTION_NOTES[input_file_type],
        )
        for input_file_type in input_format_order()
    ]
    rows.append((
        "Python input model",
        "`--input-model`",
        "`JsonSchemaParser` after Python schema conversion",
        "JSON Schema after conversion; dict input can select another explicit schema type",
        "Explicit only",
    ))
    return markdown_table(
        ("Input format", "Selector", "Parser route", "`--schema-version`", "Auto detection"),
        rows,
    )


def generate_input_source_table() -> str:
    """Generate accepted source-shape guidance for input formats."""
    rows = [
        (
            INPUT_FORMAT_LABELS[input_file_type],
            INPUT_SOURCE_NOTES[input_file_type],
        )
        for input_file_type in input_format_order()
    ]
    rows.append(("Python input model", "`module:Object` or `path/to/file.py:Object` via `--input-model`"))
    return markdown_table(("Input format", "Accepted source shape"), rows)


def schema_label(schema: Mapping[str, Any]) -> str:
    """Return a compact label for a JSON Schema fragment."""
    if not schema:
        return "`Any`"
    if any_of := schema.get("anyOf"):
        labels = [schema_label(item) for item in any_of if isinstance(item, dict)]
        return " | ".join(dict.fromkeys(labels))
    schema_type = schema.get("type", "Any")
    if schema_format := schema.get("format"):
        return f"`{schema_type}` + `{schema_format}`"
    if schema_type == "object" and schema.get("additionalProperties") is True:
        return "`object` map"
    return code_label(schema_type)


def schema_constraints_label(schema: Mapping[str, Any]) -> str:
    """Return a compact label for common JSON Schema constraints."""
    parts = [f"{key}={schema[key]}" for key in ("minimum", "maximum", "minLength", "maxLength") if key in schema]
    return ", ".join(parts) or "-"


def generate_format_type_guide_table() -> str:
    """Generate a concise per-format type guide summary."""
    from datamodel_code_generator.parser import protobuf as protobuf_parser  # noqa: PLC0415
    from datamodel_code_generator.parser import xmlschema as xmlschema_parser  # noqa: PLC0415
    from datamodel_code_generator.parser.avro import PRIMITIVE_SCHEMAS  # noqa: PLC0415

    common_format_count = sum(len(formats) for formats in get_data_formats(is_openapi=False).values())
    openapi_only_count = sum(len(formats) for formats in get_openapi_only_data_formats().values())
    rows = [
        (
            "JSON Schema",
            f"{len(get_data_formats(is_openapi=False))} schema types, {common_format_count} common formats",
            "Uses JSON Schema type/format mappings and feature metadata below",
        ),
        (
            "OpenAPI",
            f"JSON Schema mappings plus {openapi_only_count} OpenAPI-only formats",
            "Adds OpenAPI-specific schema features and formats",
        ),
        (
            "GraphQL",
            f"{len(graphql_type_kind_rows())} GraphQL type kinds",
            "Uses GraphQL SDL parser order and parser method map",
        ),
        (
            "XML Schema",
            f"{len(xmlschema_parser.BUILTIN_TYPE_SCHEMAS)} built-in XSD datatypes",
            "Converts XSD built-ins to JSON Schema fragments",
        ),
        (
            "Protocol Buffers",
            (
                f"{len(protobuf_parser.SCALAR_SCHEMAS)} scalar field types, "
                f"{len(protobuf_parser.WELL_KNOWN_SCHEMAS)} well-known type mappings"
            ),
            "Converts descriptors to JSON Schema definitions",
        ),
        (
            "Apache Avro",
            f"{len(PRIMITIVE_SCHEMAS)} primitives, {len(avro_logical_type_rows())} logical type mappings",
            "Converts Avro schemas to JSON Schema while preserving Avro metadata",
        ),
        (
            "JSON/YAML/CSV/Dict data",
            f"{len([item for item in RAW_DATA_TYPES if item is not InputFileType.GraphQL])} raw data selectors",
            "Samples are converted to JSON Schema with genson before parsing",
        ),
        (
            "Python input model",
            f"{len(python_input_type_rows())} supported object kinds",
            "Python objects are converted to schema data before normal generation",
        ),
    ]
    return markdown_table(("Input family", "Code-derived coverage", "Guide"), rows)


def graphql_type_kind_rows() -> list[tuple[str, str, str]]:
    """Return GraphQL type kind rows from parser source."""
    tree = ast.parse(GRAPHQL_PARSER_PATH.read_text(encoding="utf-8"))
    parser_class = next(
        node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "GraphQLParser"
    )
    parse_order = graphql_parse_order(parser_class)
    parser_methods = graphql_parser_methods(parser_class)

    return [
        (code_label(kind.lower()), code_label(parser_methods.get(kind, "-")), GRAPHQL_KIND_NOTES[kind])
        for kind in parse_order
    ]


def graphql_parse_order(parser_class: ast.ClassDef) -> list[str]:
    """Return the GraphQL parser type-kind order from the parser class."""
    for child in parser_class.body:
        if not (
            isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name) and child.target.id == "parse_order"
        ):
            continue
        return [elt.attr for elt in getattr(child.value, "elts", []) if isinstance(elt, ast.Attribute)]
    return []


def graphql_parser_methods(parser_class: ast.ClassDef) -> dict[str, str]:
    """Return GraphQL type kind to parser method mappings."""
    parse_raw = next(
        child for child in parser_class.body if isinstance(child, ast.FunctionDef) and child.name == "parse_raw"
    )
    methods: dict[str, str] = {}
    for subnode in ast.walk(parse_raw):
        if not isinstance(subnode, ast.Assign) or not isinstance(subnode.value, ast.Dict):
            continue
        methods.update({
            key.attr: value.attr
            for key, value in zip(subnode.value.keys, subnode.value.values, strict=False)
            if isinstance(key, ast.Attribute) and isinstance(value, ast.Attribute)
        })
    return methods


def generate_graphql_type_kind_table() -> str:
    """Generate GraphQL type kind guide table."""
    return markdown_table(("GraphQL kind", "Parser method", "Generated shape"), graphql_type_kind_rows())


def avro_logical_type_cases() -> list[tuple[str, tuple[str, ...]]]:
    """Return Avro logical type cases from the converter match statement."""
    tree = ast.parse(AVRO_PARSER_PATH.read_text(encoding="utf-8"))
    rows: list[tuple[str, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "_apply_logical_type":
            continue
        for match_node in (child for child in ast.walk(node) if isinstance(child, ast.Match)):
            for case in match_node.cases:
                logical_types = _literal_patterns(case.pattern)
                avro_types = _avro_type_guard_values(case.guard)
                rows.extend((logical_type, avro_types) for logical_type in logical_types if avro_types)
    return rows


def _literal_patterns(pattern: ast.pattern) -> tuple[str, ...]:
    """Return string literal values from a match pattern."""
    match pattern:
        case ast.MatchValue(value=ast.Constant(value=str() as literal_value)):
            return (literal_value,)
        case ast.MatchOr(patterns=patterns):
            return tuple(value for pattern in patterns for value in _literal_patterns(pattern))
    return ()


def _avro_type_guard_values(guard: ast.expr | None) -> tuple[str, ...]:
    """Return Avro type values from a match guard."""
    match guard:
        case ast.Compare(
            left=ast.Name(id="avro_type"),
            ops=[ast.Eq()],
            comparators=[ast.Constant(value=str() as literal_value)],
        ):
            return (literal_value,)
        case ast.Compare(
            left=ast.Name(id="avro_type"),
            ops=[ast.In()],
            comparators=[ast.Set(elts=elts) | ast.Tuple(elts=elts)],
        ):
            return tuple(elt.value for elt in elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str))
    return ()


def avro_logical_schema(logical_type: str, avro_type: str) -> dict[str, Any]:
    """Return the JSON Schema fragment produced for an Avro logical type."""
    from datamodel_code_generator.parser.avro import PRIMITIVE_SCHEMAS, _AvroSchemaConverter  # noqa: PLC0415, PLC2701

    source: dict[str, Any] = {"logicalType": logical_type}
    target = dict(PRIMITIVE_SCHEMAS.get(avro_type, {"type": "string", "format": "binary"}))
    if logical_type in {"decimal", "big-decimal"}:
        source |= {"precision": 8, "scale": 2}
    return _AvroSchemaConverter()._apply_logical_type(source, target, avro_type=avro_type)  # noqa: SLF001


def avro_logical_type_rows() -> list[tuple[str, str, str]]:
    """Return Avro logical type guide rows."""
    rows = []
    for logical_type, avro_types in avro_logical_type_cases():
        schema = avro_logical_schema(logical_type, avro_types[0])
        rows.append((code_label(logical_type), code_list_label(avro_types), schema_label(schema)))
    return rows


def generate_avro_primitive_table() -> str:
    """Generate Avro primitive type guide table."""
    from datamodel_code_generator.parser.avro import PRIMITIVE_SCHEMAS  # noqa: PLC0415

    rows = [
        (code_label(name), schema_label(schema), schema_constraints_label(schema))
        for name, schema in PRIMITIVE_SCHEMAS.items()
    ]
    return markdown_table(("Avro primitive", "JSON Schema mapping", "Constraints"), rows)


def generate_avro_logical_type_table() -> str:
    """Generate Avro logical type guide table."""
    return markdown_table(("Avro logical type", "Allowed Avro type", "JSON Schema mapping"), avro_logical_type_rows())


def generate_protobuf_scalar_table() -> str:
    """Generate Protocol Buffers scalar type guide table."""
    from datamodel_code_generator.parser import protobuf as protobuf_parser  # noqa: PLC0415

    type_names = {
        value: name.removeprefix("TYPE_").lower()
        for name, value in vars(protobuf_parser).items()
        if name.startswith("TYPE_") and isinstance(value, int)
    }
    rows = [
        (
            code_label(type_names[type_id]),
            schema_label(schema),
            schema_constraints_label(schema),
        )
        for type_id, schema in protobuf_parser.SCALAR_SCHEMAS.items()
    ]
    return markdown_table(("Protobuf scalar", "JSON Schema mapping", "Constraints"), rows)


def generate_protobuf_well_known_type_table() -> str:
    """Generate Protocol Buffers well-known type guide table."""
    from datamodel_code_generator.parser import protobuf as protobuf_parser  # noqa: PLC0415

    rows = [(code_label(name), schema_label(schema)) for name, schema in protobuf_parser.WELL_KNOWN_SCHEMAS.items()]
    return markdown_table(("Well-known type", "JSON Schema mapping"), rows)


def generate_xmlschema_builtin_type_group_table() -> str:
    """Generate XML Schema built-in datatype group table."""
    from datamodel_code_generator.parser import xmlschema as xmlschema_parser  # noqa: PLC0415

    groups: dict[str, list[str]] = {}
    for name, schema in xmlschema_parser.BUILTIN_TYPE_SCHEMAS.items():
        groups.setdefault(schema_label(schema), []).append(name)

    rows = [(mapping, code_list_label(names, limit=8), str(len(names))) for mapping, names in groups.items()]
    return markdown_table(("JSON Schema mapping", "XSD built-ins", "Count"), rows)


def python_input_type_rows() -> list[tuple[str, str]]:
    """Return Python input type guide rows from the input model error text."""
    input_model_source = (SRC / "datamodel_code_generator" / "input_model.py").read_text(encoding="utf-8")
    if match := re.search(r"Supported: (?P<types>[^\"']+)", input_model_source):
        return [(code_label(name), PYTHON_INPUT_TYPE_NOTES.get(name, "-")) for name in match.group("types").split(", ")]
    msg = "Could not extract supported Python input model types"
    raise RuntimeError(msg)


def generate_python_input_type_table() -> str:
    """Generate Python input model type guide table."""
    rows = python_input_type_rows()
    rows.append(("Multiple `--input-model`", "Supported only for Pydantic v2 BaseModel classes"))
    return markdown_table(("Python input object", "Conversion behavior"), rows)


def generate_schema_type_table(data_formats: DataFormatMapping) -> str:
    """Generate the supported schema data type table."""
    lines = ["| Schema Type | Default Pydantic v2 Type | Supported Keywords |"]
    lines.append("|-------------|--------------------------|--------------------|")
    for schema_type, formats in data_formats.items():
        if not (type_ := formats.get("default")):
            continue
        keywords = SCHEMA_TYPE_KEYWORDS.get(schema_type, "")
        lines.append(f"| `{schema_type}` | {get_python_type_label(type_)} | {keywords or '-'} |")
    return "\n".join(lines)


def generate_data_format_table(data_formats: DataFormatMapping) -> str:
    """Generate a Markdown table from schema data format mappings."""
    lines = ["| Schema Type | Format | Default Pydantic v2 Type | Notes |"]
    lines.append("|-------------|--------|--------------------------|-------|")
    for schema_type, format_name, type_ in iter_data_formats(data_formats):
        note = FORMAT_NOTES.get((schema_type, format_name), "")
        lines.append(
            f"| `{schema_type}` | {get_format_label(format_name)} | {get_python_type_label(type_)} | {note or '-'} |"
        )
    return "\n".join(lines)


def print_features_summary() -> None:
    """Print a summary of all features with their metadata."""
    print("=" * 60)
    print("JSON Schema Features")
    print("=" * 60)
    for name, meta in get_feature_metadata(JsonSchemaFeatures):
        print(f"  {name}:")
        print(f"    doc_name: {meta['doc_name']}")
        print(f"    introduced: {meta['introduced']}")
        print(f"    status: {meta['status']}")
        print(f"    description: {meta['description']}")
        print()

    print("=" * 60)
    print("OpenAPI Schema Features (additional)")
    print("=" * 60)
    for name, meta in get_openapi_specific_feature_metadata():
        print(f"  {name}:")
        print(f"    doc_name: {meta['doc_name']}")
        print(f"    introduced: {meta['introduced']}")
        print(f"    status: {meta['status']}")
        print(f"    description: {meta['description']}")
        print()


def generate_supported_features_content(
    *,
    heading_level: int = 3,
    title: str = "Supported Features (from code)",
    intro: str = "The following features are tracked in the codebase with their implementation status:",
) -> str:
    """Generate the supported features content for documentation (without markers)."""
    lines = [
        "",
        heading(heading_level, title),
        "",
        intro,
        "",
        heading(heading_level + 1, "JSON Schema Features"),
        "",
        generate_feature_table(get_feature_metadata(JsonSchemaFeatures)),
        "",
        heading(heading_level + 1, "OpenAPI-Specific Features"),
        "",
    ]
    lines.extend((generate_feature_table(get_openapi_specific_feature_metadata()), ""))

    return "\n".join(lines)


def generate_supported_features_table() -> str:
    """Generate the supported features table with markers for documentation."""
    return f"{BEGIN_MARKER}{generate_supported_features_content()}{END_MARKER}"


def generate_supported_data_types_content() -> str:
    """Generate schema feature content for docs/supported-data-types.md."""
    return generate_supported_features_content(
        title="Schema Feature Support (from code)",
        intro=(
            "The JSON Schema and OpenAPI feature matrix below is generated from the same code metadata "
            "used by the schema version support page."
        ),
    )


def generate_data_format_content(
    *,
    heading_level: int = 3,
    title: str = "Data Format Support",
    intro: str = (
        "The schema type, format, and default Pydantic v2 type columns below are generated from the "
        "schema format registry and the default Pydantic v2 type mapping; notes document supplemental details."
    ),
    include_schema_types: bool = True,
) -> str:
    """Generate schema data type and format content for documentation."""
    data_formats = get_data_formats(is_openapi=False)
    lines = [
        "",
        heading(heading_level, title),
        "",
        intro,
        "",
    ]
    if include_schema_types:
        lines.extend((
            heading(heading_level + 1, "Schema Data Types"),
            "",
            generate_schema_type_table(data_formats),
            "",
        ))

    lines.extend((
        heading(heading_level + 1, "Common Formats (JSON Schema + OpenAPI)"),
        "",
        generate_data_format_table(data_formats),
        "",
        heading(heading_level + 1, "OpenAPI-Only Formats"),
        "",
        generate_data_format_table(get_openapi_only_data_formats()),
        "",
    ))
    return "\n".join(lines)


def generate_supported_data_types_format_content() -> str:
    """Generate schema type and format content for docs/supported-data-types.md."""
    return generate_data_format_content(
        title="Implemented Data Types and Formats",
        intro=(
            "The schema type, format, and default Pydantic v2 type columns below are generated from the "
            "parser's schema format registry and the default Pydantic v2 type mapping; keyword and note columns "
            "document supplemental schema details."
        ),
    )


def generate_input_format_guide_content(*, heading_level: int = 3) -> str:
    """Generate per-input-format guide content from code-owned metadata."""
    lines = [
        "",
        heading(heading_level, "Input Format Guide (from code)"),
        "",
        (
            "The tables below are generated from the input type enum, parser routing code, schema version enums, "
            "raw-data conversion rules, and parser type conversion maps."
        ),
        "",
        heading(heading_level + 1, "Parser Routes and Version Flags"),
        "",
        generate_input_format_route_table(),
        "",
        heading(heading_level + 1, "Accepted Source Shapes"),
        "",
        generate_input_source_table(),
        "",
        heading(heading_level + 1, "Format Type Coverage"),
        "",
        generate_format_type_guide_table(),
        "",
        heading(heading_level + 1, "GraphQL Type Kinds"),
        "",
        generate_graphql_type_kind_table(),
        "",
        heading(heading_level + 1, "Apache Avro Primitive Types"),
        "",
        generate_avro_primitive_table(),
        "",
        heading(heading_level + 1, "Apache Avro Logical Types"),
        "",
        generate_avro_logical_type_table(),
        "",
        heading(heading_level + 1, "Protocol Buffers Scalar Types"),
        "",
        generate_protobuf_scalar_table(),
        "",
        heading(heading_level + 1, "Protocol Buffers Well-Known Types"),
        "",
        generate_protobuf_well_known_type_table(),
        "",
        heading(heading_level + 1, "XML Schema Built-In Type Groups"),
        "",
        generate_xmlschema_builtin_type_group_table(),
        "",
        heading(heading_level + 1, "Python Input Types"),
        "",
        generate_python_input_type_table(),
        "",
    ]
    return "\n".join(lines)


def generated_docs_targets() -> tuple[GeneratedDocsTarget, ...]:
    """Return all schema docs sections generated from feature metadata."""
    return (
        GeneratedDocsTarget(
            path=SUPPORTED_FORMATS_DOCS_PATH,
            begin_marker=INPUT_FORMAT_GUIDE_BEGIN_MARKER,
            end_marker=INPUT_FORMAT_GUIDE_END_MARKER,
            content=generate_input_format_guide_content(),
        ),
        GeneratedDocsTarget(
            path=SUPPORTED_DATA_TYPES_DOCS_PATH,
            begin_marker=INPUT_FORMAT_GUIDE_BEGIN_MARKER,
            end_marker=INPUT_FORMAT_GUIDE_END_MARKER,
            content=generate_input_format_guide_content(),
        ),
        GeneratedDocsTarget(
            path=SUPPORTED_FORMATS_DOCS_PATH,
            begin_marker=BEGIN_MARKER,
            end_marker=END_MARKER,
            content=generate_supported_features_content(),
        ),
        GeneratedDocsTarget(
            path=SUPPORTED_DATA_TYPES_DOCS_PATH,
            begin_marker=DATA_TYPES_BEGIN_MARKER,
            end_marker=DATA_TYPES_END_MARKER,
            content=generate_supported_data_types_content(),
        ),
        GeneratedDocsTarget(
            path=SUPPORTED_FORMATS_DOCS_PATH,
            begin_marker=DATA_FORMAT_BEGIN_MARKER,
            end_marker=DATA_FORMAT_END_MARKER,
            content=generate_data_format_content(include_schema_types=False),
        ),
        GeneratedDocsTarget(
            path=SUPPORTED_DATA_TYPES_DOCS_PATH,
            begin_marker=DATA_TYPES_FORMAT_BEGIN_MARKER,
            end_marker=DATA_TYPES_FORMAT_END_MARKER,
            content=generate_supported_data_types_format_content(),
        ),
    )


def update_docs_target(target: GeneratedDocsTarget, *, check: bool) -> bool:
    """Update or check one generated Markdown section."""
    if not target.path.exists():
        print(f"Error: {target.path} does not exist", file=sys.stderr)
        return False

    current_content = target.path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"{re.escape(target.begin_marker)}.*?{re.escape(target.end_marker)}",
        re.DOTALL,
    )

    if not (match := pattern.search(current_content)):
        print(f"Warning: Markers not found in {target.path}", file=sys.stderr)
        print("Please add the following markers to the file:", file=sys.stderr)
        print(f"  {target.begin_marker}", file=sys.stderr)
        print(f"  {target.end_marker}", file=sys.stderr)
        return False

    new_section = f"{target.begin_marker}{target.content}{target.end_marker}"
    new_content = current_content[: match.start()] + new_section + current_content[match.end() :]

    if check:
        if current_content != new_content:
            print(f"Schema docs are out of date: {target.path}", file=sys.stderr)
            print("Run 'python scripts/build_schema_docs.py' to update.", file=sys.stderr)
            return False
        return True

    if current_content == new_content:
        print(f"Schema docs are already up to date: {target.path}")
        return True

    target.path.write_text(new_content, encoding="utf-8")
    print(f"Updated {target.path}")
    return True


def update_docs_file(*, check: bool = False) -> int:
    """Update generated schema documentation sections."""
    results = [update_docs_target(target, check=check) for target in generated_docs_targets()]
    if all(results):
        if check:
            print("Schema docs are up to date.")
        return 0
    return 1


def main() -> int:
    """Parse arguments and build documentation."""
    parser = argparse.ArgumentParser(description="Build schema documentation from code metadata")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check if docs would change without modifying files",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print a summary of all features and their metadata",
    )
    args = parser.parse_args()

    if args.summary:
        print_features_summary()
        return 0

    return update_docs_file(check=args.check)


if __name__ == "__main__":
    sys.exit(main())
