"""Build the generated inventory section in docs/architecture.md.

Usage:
    python scripts/build_architecture_docs.py
    python scripts/build_architecture_docs.py --check

The architecture page is intentionally not fully generated. This script only
syncs the code-sourced inventory that tends to drift during releases: parser
routes, parser inheritance, output backends, config model sizes, and formatter
names, and the pass table that ``parser/_pass_decls.py`` declares.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
DOCS_PATH = ROOT / "docs" / "architecture.md"
INIT_PATH = SRC / "datamodel_code_generator" / "__init__.py"
PARSER_DIR = SRC / "datamodel_code_generator" / "parser"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from datamodel_code_generator import DataModelType, InputFileType  # noqa: E402
from datamodel_code_generator.config import (  # noqa: E402
    AsyncAPIParserConfig,
    AvroParserConfig,
    BaseGenerateConfig,
    GenerateConfig,
    GraphQLParserConfig,
    JSONSchemaParserConfig,
    OpenAPIParserConfig,
    ParserConfig,
    ProtobufParserConfig,
    XMLSchemaParserConfig,
)
from datamodel_code_generator.format import DEFAULT_FORMATTERS, Formatter, PythonVersionMin  # noqa: E402
from datamodel_code_generator.model import get_data_model_types  # noqa: E402
from datamodel_code_generator.parser._facts import Fact  # noqa: E402, PLC2701
from datamodel_code_generator.parser._pass_decls import PASS_DECLS, PassDecl  # noqa: E402, PLC2701

BEGIN_MARKER = "<!-- BEGIN AUTO-GENERATED ARCHITECTURE INVENTORY -->"
END_MARKER = "<!-- END AUTO-GENERATED ARCHITECTURE INVENTORY -->"
PASS_BEGIN_MARKER = "<!-- BEGIN AUTO-GENERATED PASS DECLARATIONS -->"
PASS_END_MARKER = "<!-- END AUTO-GENERATED PASS DECLARATIONS -->"

PARSER_ROUTE_NOTES: dict[InputFileType, str] = {
    InputFileType.Auto: "Resolved before parser selection by content inference.",
    InputFileType.Json: "Sample data is converted to JSON Schema with genson first.",
    InputFileType.Yaml: "Sample data is converted to JSON Schema with genson first.",
    InputFileType.Dict: "In-memory mapping is converted to JSON Schema with genson first.",
    InputFileType.CSV: "The header and first data row are converted to JSON Schema with genson first.",
    InputFileType.MCPTools: "MCP tool input/output schemas are hoisted into JSON Schema definitions first.",
}

PARSER_ROUTE_OVERRIDES: dict[InputFileType, str] = {
    InputFileType.Auto: "pre-parser inference",
    InputFileType.Json: "JsonSchemaParser after conversion",
    InputFileType.Yaml: "JsonSchemaParser after conversion",
    InputFileType.Dict: "JsonSchemaParser after conversion",
    InputFileType.CSV: "JsonSchemaParser after conversion",
    InputFileType.MCPTools: "JsonSchemaParser after conversion",
}

PARSER_CONFIG_CLASSES = [
    BaseGenerateConfig,
    GenerateConfig,
    ParserConfig,
    JSONSchemaParserConfig,
    OpenAPIParserConfig,
    AsyncAPIParserConfig,
    XMLSchemaParserConfig,
    ProtobufParserConfig,
    AvroParserConfig,
    GraphQLParserConfig,
]


def _name_from_expr(node: ast.AST) -> str | None:
    match node:
        case ast.Name(id=name):
            return name
        case ast.Attribute(attr=name):
            return name
        case ast.Subscript(value=value):
            return _name_from_expr(value)
    return None


def _input_file_type_name(pattern: ast.pattern) -> str | None:
    match pattern:
        case ast.MatchValue(value=ast.Attribute(value=ast.Name(id="InputFileType"), attr=input_type_name)):
            return input_type_name
    return None


def _returned_parser_name(case: ast.match_case) -> str | None:
    for node in ast.walk(ast.Module(body=case.body, type_ignores=[])):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Call):
            return _name_from_expr(node.value.func)
    return None


def parser_routes_from_source() -> dict[InputFileType, str]:
    """Read _build_parser() and infer direct InputFileType -> Parser routes."""
    tree = ast.parse(INIT_PATH.read_text(encoding="utf-8"))
    direct_routes: dict[InputFileType, str] = {}
    default_route = "JsonSchemaParser"

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "_build_parser":
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Match):
                continue
            for case in child.cases:
                parser_name = _returned_parser_name(case)
                if parser_name is None:
                    continue
                input_type_name = _input_file_type_name(case.pattern)
                if input_type_name is None:
                    default_route = parser_name
                    continue
                direct_routes[InputFileType[input_type_name]] = parser_name

    routes: dict[InputFileType, str] = {}
    for input_file_type in InputFileType:
        if input_file_type in PARSER_ROUTE_OVERRIDES:
            routes[input_file_type] = PARSER_ROUTE_OVERRIDES[input_file_type]
        elif input_file_type in direct_routes:
            routes[input_file_type] = direct_routes[input_file_type]
        elif input_file_type in PARSER_ROUTE_NOTES:
            routes[input_file_type] = default_route
        else:
            routes[input_file_type] = default_route
    return routes


def parser_inheritance_edges() -> list[tuple[str, str]]:
    """Return parser inheritance edges as (base, child) from parser source files."""
    class_bases: dict[str, list[str]] = {}
    for path in sorted(PARSER_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name.endswith("Parser"):
                class_bases[node.name] = [
                    base_name for base in node.bases if (base_name := _name_from_expr(base)) is not None
                ]

    parser_names = set(class_bases)
    edges: list[tuple[str, str]] = []
    for child, bases in sorted(class_bases.items()):
        edges.extend((base, child) for base in bases if base in parser_names)
    return sorted(edges)


def type_display(value: Any) -> str:
    """Return a compact import-style name for a class or callable."""
    module = getattr(value, "__module__", "")
    name = getattr(value, "__name__", repr(value))
    module = module.removeprefix("datamodel_code_generator.")
    return f"`{module}.{name}`" if module else f"`{name}`"


def generate_parser_diagram() -> str:
    """Generate the Mermaid parser inheritance diagram."""
    lines = ["```mermaid", "classDiagram"]
    for base, child in parser_inheritance_edges():
        lines.append(f"    {base} <|-- {child}")
    lines.append("```")
    return "\n".join(lines)


def generate_input_routes_table() -> str:
    """Generate the input file type to parser route table."""
    routes = parser_routes_from_source()
    lines = [
        "| Input file type | Parser route | Notes |",
        "| --- | --- | --- |",
    ]
    for input_file_type in InputFileType:
        note = PARSER_ROUTE_NOTES.get(input_file_type, "Routed directly by `_build_parser()`.")
        lines.append(f"| `{input_file_type.value}` | `{routes[input_file_type]}` | {note} |")
    return "\n".join(lines)


def generate_output_backend_table() -> str:
    """Generate the output backend inventory table."""
    lines = [
        "| Output model type | Data model | Root model | Field model | Type manager |",
        "| --- | --- | --- | --- | --- |",
    ]
    for data_model_type in DataModelType:
        model_set = get_data_model_types(data_model_type, PythonVersionMin)
        lines.append(
            f"| `{data_model_type.value}` | {type_display(model_set.data_model)} | "
            f"{type_display(model_set.root_model)} | {type_display(model_set.field_model)} | "
            f"{type_display(model_set.data_type_manager)} |"
        )
    return "\n".join(lines)


def generate_config_table() -> str:
    """Generate the configuration model inventory table."""
    lines = [
        "| Config model | Field count | Purpose |",
        "| --- | ---: | --- |",
    ]
    purposes = {
        "BaseGenerateConfig": "Shared generation options.",
        "GenerateConfig": "Public `generate()` configuration.",
        "ParserConfig": "Base parser dependency injection and parser options.",
        "JSONSchemaParserConfig": "JSON Schema parser options.",
        "OpenAPIParserConfig": "OpenAPI-specific parser options.",
        "AsyncAPIParserConfig": "AsyncAPI-specific parser options.",
        "XMLSchemaParserConfig": "XML Schema-specific parser options.",
        "ProtobufParserConfig": "Protocol Buffers-specific parser options.",
        "AvroParserConfig": "Avro-specific parser options.",
        "GraphQLParserConfig": "GraphQL-specific parser options.",
    }
    lines.extend(
        (f"| `{config_class.__name__}` | {len(config_class.model_fields)} | {purposes[config_class.__name__]} |")
        for config_class in PARSER_CONFIG_CLASSES
    )
    return "\n".join(lines)


def generate_formatter_table() -> str:
    """Generate the formatter inventory table."""
    defaults = {formatter.value for formatter in DEFAULT_FORMATTERS}
    lines = [
        "| Formatter | Default when unspecified |",
        "| --- | --- |",
    ]
    for formatter in Formatter:
        is_default = "yes" if formatter.value in defaults else "no"
        lines.append(f"| `{formatter.value}` | {is_default} |")
    return "\n".join(lines)


def generate_architecture_inventory() -> str:
    """Generate the code-sourced architecture inventory section."""
    return (
        f'\n!!! note "Generated inventory"\n'
        "    This section is generated by `scripts/build_architecture_docs.py` from the current source tree.\n"
        "    Edit the surrounding prose by hand, then run the script before release.\n"
        "\n"
        "### Parser Inheritance\n"
        "\n"
        f"{generate_parser_diagram()}\n"
        "\n"
        "### Input Routes\n"
        "\n"
        f"{generate_input_routes_table()}\n"
        "\n"
        "### Output Backends\n"
        "\n"
        f"{generate_output_backend_table()}\n"
        "\n"
        "### Configuration Surface\n"
        "\n"
        f"{generate_config_table()}\n"
        "\n"
        "### Formatter Names\n"
        "\n"
        f"{generate_formatter_table()}\n"
    )


def _facts_cell(facts: frozenset[Any]) -> str:
    """List the facts of a declaration, with ``every Fact`` for all members of ``Fact``."""
    names = sorted(fact.name for fact in facts if not isinstance(fact, Fact) or not set(Fact) <= facts)
    return ", ".join(["every Fact"] * (set(Fact) <= facts) + names) or "-"


def _pass_notes(decl: PassDecl) -> str:
    notes = [decl.summary] if decl.summary else []
    if decl.condition:
        notes.append(f"Runs when: {decl.condition}.")
    if decl.target is not None:
        notes.append(f"Moves to {decl.target.name}.")
    if decl.removed_by:
        notes.append(f"Removed by {decl.removed_by}.")
    notes.extend(f"After `{edge.pass_name}`: {edge.reason}." for edge in decl.after)
    return " ".join(notes)


def _provisional_reads() -> list[str]:
    """List each explicit edge that runs a pass reading a fact before the pass that writes it, in execution order."""
    by_name = {decl.name: decl for decl in PASS_DECLS}
    return [
        f"- `{edge.pass_name}` reads {_facts_cell(read_first)} before `{decl.name}` writes it: {edge.reason}."
        for decl in PASS_DECLS
        for edge in decl.after
        if (read_first := by_name[edge.pass_name].reads & decl.writes)
    ]


def generate_pass_table() -> str:
    """Generate the table of pass declarations in execution order."""
    lines = [
        "",
        '!!! note "Generated pass table"',
        "    Generated by `scripts/build_architecture_docs.py` from `parser/_pass_decls.py`, in execution order.",
        "",
        "| Phase | Pass | Scope | Kind | Writes | Reads | Notes |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines.extend(
        f"| {decl.phase.name} | `{decl.name}` | {decl.scope.value} | {decl.kind.value} | {_facts_cell(decl.writes)} | "
        f"{_facts_cell(decl.reads)} | {_pass_notes(decl)} |"
        for decl in PASS_DECLS
    )
    lines.extend(["", "Provisional reads, from the explicit edges:", "", *_provisional_reads()])
    return "\n".join(lines) + "\n"


GENERATED_BLOCKS = (
    (BEGIN_MARKER, END_MARKER, generate_architecture_inventory),
    (PASS_BEGIN_MARKER, PASS_END_MARKER, generate_pass_table),
)
"""Each generated block of docs/architecture.md: its markers and the function that writes it."""


def update_docs_file(*, check: bool = False) -> int:
    """Update docs/architecture.md or check that it is already synchronized."""
    if not DOCS_PATH.exists():
        print(f"Error: {DOCS_PATH} does not exist", file=sys.stderr)
        return 1

    current_content = DOCS_PATH.read_text(encoding="utf-8")
    new_content = current_content
    for begin, end, generate in GENERATED_BLOCKS:
        pattern = re.compile(rf"{re.escape(begin)}.*?{re.escape(end)}", re.DOTALL)
        if not pattern.search(new_content):
            print(f"Architecture markers are missing in {DOCS_PATH}", file=sys.stderr)
            print(f"Add {begin} and {end}.", file=sys.stderr)
            return 1
        generated = generate()
        new_content = pattern.sub(lambda _: f"{begin}{generated}{end}", new_content)  # noqa: B023
    if check:
        if current_content != new_content:
            print(f"Architecture docs are out of date: {DOCS_PATH}", file=sys.stderr)
            print("Run 'python scripts/build_architecture_docs.py' to update.", file=sys.stderr)
            return 1
        print("Architecture docs are up to date.")
        return 0

    if current_content == new_content:
        print("Architecture docs are already up to date.")
        return 0

    DOCS_PATH.write_text(new_content, encoding="utf-8")
    print(f"Updated {DOCS_PATH.relative_to(ROOT)}")
    return 0


def main() -> int:
    """Run the architecture documentation synchronization command."""
    parser = argparse.ArgumentParser(description="Build architecture documentation inventory from source")
    parser.add_argument("--check", action="store_true", help="Check whether docs/architecture.md is up to date")
    args = parser.parse_args()
    return update_docs_file(check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
