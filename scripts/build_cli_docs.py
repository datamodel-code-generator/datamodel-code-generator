"""CLI documentation builder.

Generates Markdown documentation from pytest CLI doc collection.
Groups options by category into single pages for better searchability.

Usage:
    # First, collect CLI doc metadata
    pytest --collect-cli-docs -p no:xdist

    # Then, build documentation
    python scripts/build_cli_docs.py

The generated documentation uses MkDocs Material tabs syntax for
version-specific output examples.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import operator

from datamodel_code_generator.cli_options import (
    MANUAL_DOCS,
    OPTION_RELATION_KINDS,
    OPTION_TOPIC_ALLOWED_GROUPS,
    CLIOptionMeta,
    OptionCategory,
    OptionGroup,
    OptionTopic,
    get_canonical_option,
    get_cli_doc_slug,
    get_cli_option_doc_name,
    get_cli_option_doc_path,
    get_option_meta,
    is_manual_doc,
)


@dataclass
class CLIDocExample:
    """Represents a single CLI documentation example from a test."""

    node_id: str
    option_description: str
    input_schema: str | None = None
    config_content: str | None = None
    cli_args: list[str] = field(default_factory=list)
    golden_output: str | None = None
    comparison_output: str | None = None
    version_outputs: dict[str, str] | None = None
    model_outputs: dict[str, str] | None = None
    expected_stdout: str | None = None
    extra_outputs: list[dict[str, str]] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    related_options: list[str] = field(default_factory=list)
    is_primary: bool = False

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> CLIDocExample:
        """Create CLIDocExample from collected test item."""
        kwargs = item.get("marker_kwargs", {})
        return cls(
            node_id=item.get("node_id", ""),
            option_description=item.get("option_description", ""),
            input_schema=kwargs.get("input_schema"),
            config_content=kwargs.get("config_content"),
            cli_args=kwargs.get("cli_args", []),
            golden_output=kwargs.get("golden_output"),
            comparison_output=kwargs.get("comparison_output"),
            version_outputs=kwargs.get("version_outputs"),
            model_outputs=kwargs.get("model_outputs"),
            expected_stdout=kwargs.get("expected_stdout"),
            extra_outputs=kwargs.get("extra_outputs", []),
            aliases=kwargs.get("aliases", []),
            related_options=kwargs.get("related_options", []),
            is_primary=kwargs.get("primary", False),
        )

    def get_schema_type(self) -> str:
        """Determine schema type from input_schema path."""
        if not self.input_schema:
            return "config" if self.config_content else "unknown"
        path_lower = self.input_schema.lower()
        if path_lower.endswith(".graphql"):
            return "GraphQL"
        prefix_map = {
            "graphql/": "GraphQL",
            "openapi/": "OpenAPI",
            "jsonschema/": "JSON Schema",
            "json/": "JSON",
            "yaml/": "YAML",
            "csv/": "CSV",
        }
        for prefix, schema_type in prefix_map.items():
            if path_lower.startswith(prefix):
                return schema_type
        return "Schema"


@dataclass
class CLIDocOption:
    """Represents a CLI option with all its documentation examples."""

    option_name: str
    examples: list[CLIDocExample] = field(default_factory=list)

    def add_example(self, example: CLIDocExample) -> None:
        """Add an example, maintaining primary example priority."""
        self.examples.append(example)

    def get_primary(self) -> CLIDocExample | None:
        """Get the primary example for this option."""
        for ex in self.examples:
            if ex.is_primary:
                return ex
        return self.examples[0] if self.examples else None

    def get_option_description(self) -> str:
        """Get option description from primary example."""
        primary = self.get_primary()
        return primary.option_description if primary else ""

    def get_aliases(self) -> list[str]:
        """Get all aliases from all examples."""
        aliases: set[str] = set()
        for ex in self.examples:
            aliases.update(ex.aliases)
        return sorted(aliases)

    def get_related_options(self) -> list[str]:
        """Get all related options from all examples."""
        related: set[str] = set()
        for ex in self.examples:
            related.update(ex.related_options)
        return sorted(related)

    def get_unique_schema_types(self) -> list[str]:
        """Get unique schema types across all examples."""
        types: dict[str, CLIDocExample] = {}
        for ex in self.examples:
            schema_type = ex.get_schema_type()
            if schema_type not in types:
                types[schema_type] = ex
        return list(types.keys())


@dataclass(frozen=True)
class CategoryRecipe:
    """A concise set of options that solve a common category workflow."""

    title: str
    description: str
    options: tuple[str, ...]


COLLECTION_PATH = Path(__file__).parent.parent / "tests" / "cli_doc" / ".cli_doc_collection.json"
DATA_PATH = Path(__file__).parent.parent / "tests" / "data"
EXPECTED_BASE_PATH = DATA_PATH / "expected"
EXPECTED_PATH = EXPECTED_BASE_PATH / "main"
DOCS_OUTPUT = Path(__file__).parent.parent / "docs" / "cli-reference"
TOPICS_OUTPUT = DOCS_OUTPUT / "topics"
MANUAL_DOCS_DIR = DOCS_OUTPUT / "manual"
DOCS_ROOT = Path(__file__).parent.parent / "docs"

DESC_LENGTH_SHORT = 60
DESC_LENGTH_LONG = 80

# Regex pattern for extracting CLI options from markdown files
# Format: <!-- related-cli-options: --option1, --option2, ... -->
CLI_OPTIONS_TAG_PATTERN = re.compile(r"<!--\s*related-cli-options:\s*([^>]+?)\s*-->", re.IGNORECASE)
DOC_EXAMPLE_BLOCK_PATTERN = re.compile(
    r"(?ms)^<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: (?P<example_id>[^>]+) -->\n"
    r".*?"
    r"^<!-- END AUTO-GENERATED DOC EXAMPLE: (?P=example_id) -->\n?"
)
OPTION_HEADING_PATTERN = re.compile(r"^## `(?P<option>--[^`]+)` \{#(?P<slug>[^}]+)\}\n", re.MULTILINE)
EXAMPLES_ADMONITION = '??? example "Examples"'

# Emoji mapping for categories
CATEGORY_EMOJIS = {
    OptionCategory.BASE: "📁",
    OptionCategory.TYPING: "🔧",
    OptionCategory.FIELD: "🏷️",
    OptionCategory.MODEL: "🏗️",
    OptionCategory.TEMPLATE: "🎨",
    OptionCategory.OPENAPI: "📘",
    OptionCategory.GENERAL: "⚙️",
}
CATEGORY_INTROS = {
    OptionCategory.OPENAPI: (
        "This generated reference lists every OpenAPI-only CLI flag and its tested examples. "
        "For a workflow-oriented guide, see [OpenAPI Options](../openapi-options.md). "
        "For input format basics, see [Generate from OpenAPI](../openapi.md)."
    ),
}

OPTION_CATEGORY_ORDER = (
    OptionCategory.BASE,
    OptionCategory.TYPING,
    OptionCategory.FIELD,
    OptionCategory.MODEL,
    OptionCategory.TEMPLATE,
    OptionCategory.OPENAPI,
    OptionCategory.GRAPHQL,
    OptionCategory.GENERAL,
)

TOPIC_DESCRIPTIONS = {
    OptionTopic.MODEL_CUSTOMIZATION: "Choose model class shape, naming, reuse, and root-model behavior.",
    OptionTopic.TEMPLATE_CUSTOMIZATION: "Tune generated file headers, imports, decorators, templates, and formatting.",
    OptionTopic.TYPING_CUSTOMIZATION: "Control Python annotation syntax, collection types, imports, and type mappings.",
    OptionTopic.OPENAPI: "Handle OpenAPI operation naming, path selection, scopes, and readOnly/writeOnly behavior.",
}

GROUP_DESCRIPTIONS = {
    OptionGroup.MODEL_NAMING: "Class names, suffixes, prefixes, and duplicate-name behavior.",
    OptionGroup.MODEL_REUSE: "Schema deduplication and shared generated modules.",
    OptionGroup.MODEL_SHAPE: "Output model family and compatibility targets.",
    OptionGroup.ROOT_MODEL: "Root model creation, collapse, and alias behavior.",
    OptionGroup.CUSTOM_TEMPLATES: "Custom templates and extra template data.",
    OptionGroup.GENERATED_OUTPUT: "Generated file headers and reproducible output.",
    OptionGroup.IMPORTS: "Generated imports and type-checking import behavior.",
    OptionGroup.OUTPUT_FORMATTING: "Formatter selection, quote style, and string wrapping.",
    OptionGroup.COLLECTION_TYPES: "Collection and tuple/set generation.",
    OptionGroup.TYPE_ALIAS: "TypeAlias and root-model alias output.",
    OptionGroup.TYPE_MAPPING: "Scalar, date/time, and custom type mapping.",
    OptionGroup.TYPE_SYNTAX: "Modern annotation syntax and Annotated usage.",
    OptionGroup.OPENAPI_NAMING: "Operation and response model naming.",
    OptionGroup.OPENAPI_PATHS: "Path selection and path parameter output.",
    OptionGroup.OPENAPI_SCOPES: "OpenAPI generation scopes.",
    OptionGroup.READ_ONLY_WRITE_ONLY: "readOnly/writeOnly model behavior.",
}

OPTION_TOPIC_ORDER = tuple(OPTION_TOPIC_ALLOWED_GROUPS)

CATEGORY_RECIPES: dict[OptionCategory, tuple[CategoryRecipe, ...]] = {
    OptionCategory.BASE: (
        CategoryRecipe(
            title="Generate a local schema file",
            description="Pin the input type and destination when the source extension is ambiguous or generated output "
            "needs a stable path.",
            options=("--input", "--input-file-type", "--output"),
        ),
        CategoryRecipe(
            title="Fetch a protected remote schema",
            description="Use URL input together with HTTP request controls for schemas served behind headers or slower "
            "endpoints.",
            options=("--url", "--http-headers", "--http-timeout"),
        ),
    ),
    OptionCategory.TYPING: (
        CategoryRecipe(
            title="Use modern Python annotations",
            description="Target a recent Python version and prefer built-in collection and union syntax in generated "
            "types.",
            options=("--target-python-version", "--use-union-operator", "--use-standard-collections"),
        ),
        CategoryRecipe(
            title="Keep validation constraints in type hints",
            description="Combine Annotated hints with strict scalar handling when downstream tooling reads type "
            "metadata.",
            options=("--use-annotated", "--field-constraints", "--strict-types"),
        ),
    ),
    OptionCategory.FIELD: (
        CategoryRecipe(
            title="Normalize incoming field names",
            description="Convert source names to Python identifiers while preserving explicit alias data for runtime "
            "IO.",
            options=("--snake-case-field", "--original-field-name-delimiter", "--aliases"),
        ),
        CategoryRecipe(
            title="Carry schema documentation into models",
            description="Promote schema and field descriptions into generated docstrings or field metadata.",
            options=("--use-schema-description", "--use-field-description", "--use-field-description-example"),
        ),
    ),
    OptionCategory.MODEL: (
        CategoryRecipe(
            title="Target Pydantic v2 on modern Python",
            description="Set the output model family and Python/Pydantic compatibility targets together.",
            options=("--output-model-type", "--target-python-version", "--target-pydantic-version"),
        ),
        CategoryRecipe(
            title="Deduplicate reusable schemas",
            description="Reuse equivalent models and tune the scope or root-model behavior when schemas repeat.",
            options=("--reuse-model", "--reuse-scope", "--collapse-root-models"),
        ),
    ),
    OptionCategory.TEMPLATE: (
        CategoryRecipe(
            title="Control generated file headers",
            description="Choose a header source and remove volatile timestamp content for reproducible output.",
            options=("--custom-file-header", "--custom-file-header-path", "--disable-timestamp"),
        ),
        CategoryRecipe(
            title="Inject project-specific code",
            description="Add imports, decorators, or custom templates when generated classes must fit local framework "
            "conventions.",
            options=("--additional-imports", "--class-decorators", "--custom-template-dir"),
        ),
    ),
    OptionCategory.OPENAPI: (
        CategoryRecipe(
            title="Generate operation-focused models",
            description="Limit OpenAPI output to operation shapes and name models from operation IDs and status codes.",
            options=("--openapi-scopes", "--use-operation-id-as-name", "--use-status-code-in-response-name"),
        ),
        CategoryRecipe(
            title="Trim an OpenAPI build",
            description="Restrict generation to selected paths while preserving parameter and info-version context.",
            options=("--openapi-include-paths", "--include-path-parameters", "--openapi-include-info-version"),
        ),
    ),
    OptionCategory.GRAPHQL: (
        CategoryRecipe(
            title="Trim GraphQL metadata fields",
            description="Skip injected typename fields when generated models should expose only business data.",
            options=("--graphql-no-typename",),
        ),
    ),
    OptionCategory.GENERAL: (
        CategoryRecipe(
            title="Resolve remote references deliberately",
            description="Enable remote `$ref` loading and configure request metadata, timeouts, or local ref roots.",
            options=("--allow-remote-refs", "--http-headers", "--http-timeout", "--http-local-ref-path"),
        ),
        CategoryRecipe(
            title="Regenerate during schema edits",
            description="Watch input files with a short debounce while writing output to a stable target path.",
            options=("--watch", "--watch-delay", "--output"),
        ),
    ),
}

HOMEPAGE_RECIPE_CATEGORIES: tuple[OptionCategory, ...] = (
    OptionCategory.BASE,
    OptionCategory.MODEL,
    OptionCategory.TYPING,
    OptionCategory.FIELD,
    OptionCategory.OPENAPI,
    OptionCategory.GENERAL,
)

# Manual option descriptions for utility options
MANUAL_OPTION_DESCRIPTIONS = {
    "--help": "Show help message and exit",
    "--version": "Show program version and exit",
    "--debug": "Show debug messages during code generation",
    "--profile": "Use a named profile from pyproject.toml",
    "--job": "Run a named generation job from pyproject.toml (experimental)",
    "--all-jobs": "Run every named generation job from pyproject.toml (experimental)",
    "--output-format": "Choose the command output format",
    "--output-format-json-schema": "Output JSON Schema for structured command output or JSON configuration",
    "--no-color": "Disable colorized output",
    "--generate-prompt": "Generate a prompt for consulting LLMs about CLI options",
    "--install-skill": "Install the bundled Agent Skill (experimental)",
    "--skill-scope": "Choose an Agent Skill installation scope",
    "--overwrite-skill": "Replace an existing Agent Skill installation",
    "--list-deprecations": "List registered deprecations and scheduled breaking changes",
    "--list-experimental": "List registered experimental features",
    "--generate-server": "Generate a FastAPI server package with the models (experimental)",
    "--target-config": "Read the generation target's settings from a TOML file",
    "--target-output": "Override the generation target's output directory",
    "--diagnostics-json": "Write the generation target's diagnostics as JSON",
}

# Regex pattern for detecting MkDocs Material admonitions in docstrings
# Matches: !!! type "title" or !!! type (without title)
ADMONITION_PATTERN = re.compile(r'^(!{3}\s+\w+(?:\s+"[^"]*")?)', re.MULTILINE)


@dataclass
class ParsedDocstring:
    """Parsed docstring with description and admonitions separated."""

    description: str
    admonitions: list[str]


def parse_docstring(docstring: str) -> ParsedDocstring:
    """Parse docstring to separate description from admonitions.

    Admonitions are MkDocs Material syntax blocks starting with !!!
    Example:
        '''Short description.

        Longer description paragraph.

        !!! warning "Title"
            Warning content here.
            Can be multiple lines.

        !!! note "Another"
            Note content.
        '''

    Returns:
        ParsedDocstring with description and list of admonition blocks.
    """
    if not docstring:
        return ParsedDocstring(description="", admonitions=[])

    lines = docstring.strip().split("\n")
    description_lines: list[str] = []
    admonitions: list[str] = []
    current_admonition: list[str] = []
    in_admonition = False
    admonition_indent = 0

    for line in lines:
        # Check if this line starts a new admonition
        if ADMONITION_PATTERN.match(line.lstrip()):
            # Save previous admonition if exists
            if current_admonition:
                admonitions.append("\n".join(current_admonition))
            current_admonition = [line.lstrip()]
            in_admonition = True
            # Calculate the base indent for admonition content (typically 4 spaces)
            admonition_indent = len(line) - len(line.lstrip())
        elif in_admonition:
            stripped = line.lstrip()
            current_indent = len(line) - len(line.lstrip())
            # Check if we're still in the admonition (indented content or empty line)
            if not stripped:
                # Empty line - could be part of admonition or separator
                current_admonition.append("")
            elif current_indent > admonition_indent or line.startswith("    "):
                # Indented content belongs to admonition
                # Remove the base indentation and add standard 4-space indent
                content = line[admonition_indent:] if current_indent >= admonition_indent else line
                current_admonition.append(content)
            else:
                # Not indented - end of admonition, start of new content
                if current_admonition:
                    admonitions.append("\n".join(current_admonition))
                current_admonition = []
                in_admonition = False
                description_lines.append(line)
        else:
            description_lines.append(line)

    # Don't forget the last admonition
    if current_admonition:
        admonitions.append("\n".join(current_admonition))

    # Clean up description - remove trailing empty lines
    while description_lines and not description_lines[-1].strip():
        description_lines.pop()

    return ParsedDocstring(
        description="\n".join(description_lines).strip(),
        admonitions=admonitions,
    )


def scan_docs_for_cli_option_tags(
    documented_options: frozenset[str] | None = None,
) -> dict[str, list[tuple[str, str]]]:
    """Scan markdown files in docs/ for related-cli-options tags.

    Returns:
        Dictionary mapping CLI option name to list of (page_path, page_title) tuples.
        page_path is relative to docs/ directory.
    """
    option_to_pages: dict[str, list[tuple[str, str]]] = defaultdict(list)

    # Scan all markdown files in docs/ (excluding cli-reference/)
    # Sort to ensure consistent ordering across different environments
    for md_file in sorted(DOCS_ROOT.glob("**/*.md")):
        # Skip cli-reference directory (auto-generated)
        try:
            md_file.relative_to(DOCS_OUTPUT)
            continue
        except ValueError:
            pass  # Not in cli-reference, continue processing

        content = md_file.read_text(encoding="utf-8")
        matches = CLI_OPTIONS_TAG_PATTERN.findall(content)

        if not matches:
            continue

        # Get page title from first H1 heading
        title_match = re.search(r"^#\s+(.+?)$", content, re.MULTILINE)
        if title_match:
            page_title = title_match.group(1).strip()
            # Remove emojis and clean up (preserve dots for filenames like pyproject.toml)
            page_title = re.sub(r"[^\w\s\-/.]", "", page_title).strip()
        else:
            page_title = md_file.stem.replace("-", " ").replace("_", " ").title()

        # Get relative path from docs/
        page_path = str(md_file.relative_to(DOCS_ROOT))

        # Parse all matched tags
        available_options = documented_options or frozenset()
        for match in matches:
            options = [opt.strip() for opt in match.split(",")]
            for option in options:
                if option.startswith("--"):
                    canonical = _documented_related_option(option, available_options)
                    option_to_pages[canonical].append((page_path, page_title))

    # Sort the page lists for each option to ensure consistent ordering
    return {option: sorted(pages) for option, pages in sorted(option_to_pages.items())}


def safe_read_file(base_path: Path, relative_path: str, file_types: tuple[str, ...] = ("*.py",)) -> str:
    """Read file or directory with path traversal protection.

    For directories, concatenates all matching files with file headers.

    Args:
        base_path: Base directory for path resolution
        relative_path: Relative path to file or directory
        file_types: Glob patterns for files to include when reading directories
    """
    base_resolved = base_path.resolve()
    full_path = (base_path / relative_path).resolve()

    try:
        full_path.relative_to(base_resolved)
    except ValueError:
        msg = f"Path traversal detected: {relative_path}"
        raise ValueError(msg) from None

    if not full_path.exists():
        msg = f"File not found: {relative_path}"
        raise FileNotFoundError(msg)

    if full_path.is_dir():
        # Handle directory - concatenate all matching files
        all_files = []
        for pattern in file_types:
            all_files.extend(full_path.glob(f"**/{pattern}"))
        all_files = sorted(set(all_files))

        if not all_files:
            return "# (empty directory - no matching files)\n"

        parts = []
        for file in all_files:
            rel_name = file.relative_to(full_path)
            parts.extend((f"# {rel_name}", file.read_text(encoding="utf-8").rstrip(), ""))  # blank line separator
        return "\n".join(parts)

    return full_path.read_text(encoding="utf-8")


def read_expected_file(relative_path: str) -> str:
    """Read expected output file, trying main/ then expected/ base for main_kr/ paths.

    This supports both:
    - Paths relative to tests/data/expected/main/ (backward compatible)
    - Paths like main_kr/... relative to tests/data/expected/
    """
    # Try main/ subdirectory first (backward compatibility)
    try:
        return safe_read_file(EXPECTED_PATH, relative_path)
    except FileNotFoundError:
        # Fall back to expected/ base for main_kr/ and other subdirectory paths
        return safe_read_file(EXPECTED_BASE_PATH, relative_path)


def indent_code_block(content: str, prefix: str, language: str = "python") -> str:
    """Indent a fixture for MkDocs tabs and preserve its original formatting."""
    lines = content.strip().split("\n")
    result = f"{prefix}<!-- fmt: off -->\n\n{prefix}```{language}\n"
    for line in lines:
        result += f"{prefix}{line}\n" if line else "\n"
    result += f"{prefix}```\n\n{prefix}<!-- fmt: on -->\n\n"
    return result


ORDERED_MODEL_OUTPUT_LABELS: tuple[tuple[str, str], ...] = (
    ("pydantic_v2", "Pydantic v2"),
    ("pydantic_v2.dataclass", "Pydantic v2 dataclass"),
    ("dataclass", "dataclass"),
    ("dataclasses.dataclass", "dataclass"),
    ("typeddict", "TypedDict"),
    ("typing.TypedDict", "TypedDict"),
    ("msgspec", "msgspec"),
    ("msgspec.Struct", "msgspec"),
)


def summarize_description(description: str, max_length: int | None = None) -> str:
    """Collapse the first paragraph of a description to a single summary line."""
    summary = " ".join(description.strip().split("\n\n", 1)[0].split())
    if max_length is not None and len(summary) > max_length:
        return summary[:max_length] + "..."
    return summary


def validated_model_outputs(example: CLIDocExample) -> list[tuple[str, str]]:
    """Return model outputs in display order and reject stale model keys."""
    display_order = tuple(key for key, _ in ORDERED_MODEL_OUTPUT_LABELS)
    unexpected_keys = sorted(set(example.model_outputs or ()) - set(display_order))
    if unexpected_keys:
        msg = f"Unexpected model output key(s) {unexpected_keys!r} in CLI doc example {example.node_id!r}"
        raise ValueError(msg)
    return sorted(example.model_outputs.items(), key=lambda item: display_order.index(item[0]))


def format_cli_args(cli_args: list[str]) -> list[str]:
    """Format cli_args as proper command line (group option with its value)."""
    formatted = []
    i = 0
    while i < len(cli_args):
        arg = cli_args[i]
        if arg.startswith("-") and i + 1 < len(cli_args) and not cli_args[i + 1].startswith("-"):
            value = cli_args[i + 1]
            if '"' in value or "'" in value:
                value = shlex.quote(value)
            elif " " in value or not value:
                value = f'"{value}"'
            formatted.append(f"{arg} {value}")
            i += 2
        else:
            formatted.append(arg)
            i += 1
    return formatted


def _get_schema_lang(schema_path: str) -> str:
    """Get language identifier for schema file."""
    path_lower = schema_path.lower()
    if path_lower.endswith((".yaml", ".yml")):
        return "yaml"
    if path_lower.endswith(".graphql"):
        return "graphql"
    return "json"


def _generate_single_example_output(example: CLIDocExample, prefix: str = "    ") -> str:
    """Generate output section for a single example."""
    md = ""
    if example.expected_stdout:
        stdout_value = example.expected_stdout
        if "/" in stdout_value or stdout_value.endswith((".py", ".txt")):
            try:
                content = read_expected_file(stdout_value)
                md += indent_code_block(content, prefix, language="")
            except (FileNotFoundError, ValueError) as e:
                md += f"{prefix}> **Error:** {e}\n\n"
        else:
            md += indent_code_block(stdout_value, prefix, language="")
    elif example.comparison_output and example.model_outputs:
        model_labels = dict(ORDERED_MODEL_OUTPUT_LABELS)
        for model_key, output_file in validated_model_outputs(example):
            label = model_labels.get(model_key, model_key)
            md += f'{prefix}=== "{label}"\n\n'
            try:
                content = read_expected_file(output_file)
                md += indent_code_block(content, prefix + "    ")
            except (FileNotFoundError, ValueError) as e:
                md += f"{prefix}    > **Error:** {e}\n\n"
        md += f'{prefix}=== "Without Option (Baseline)"\n\n'
        try:
            content = read_expected_file(example.comparison_output)
            md += indent_code_block(content, prefix + "    ")
        except (FileNotFoundError, ValueError) as e:
            md += f"{prefix}    > **Error:** {e}\n\n"
    elif example.comparison_output and example.golden_output:
        md += f'{prefix}=== "With Option"\n\n'
        try:
            content = read_expected_file(example.golden_output)
            md += indent_code_block(content, prefix + "    ")
        except (FileNotFoundError, ValueError) as e:
            md += f"{prefix}    > **Error:** {e}\n\n"
        md += f'{prefix}=== "Without Option"\n\n'
        try:
            content = read_expected_file(example.comparison_output)
            md += indent_code_block(content, prefix + "    ")
        except (FileNotFoundError, ValueError) as e:
            md += f"{prefix}    > **Error:** {e}\n\n"
    elif example.version_outputs:
        sorted_versions = sorted(
            example.version_outputs.items(),
            key=lambda x: tuple(map(int, x[0].split("."))),
        )
        for version, output_file in sorted_versions:
            md += f'{prefix}=== "Python {version}"\n\n'
            try:
                content = read_expected_file(output_file)
                md += indent_code_block(content, prefix + "    ")
            except (FileNotFoundError, ValueError) as e:
                md += f"{prefix}    > **Error:** {e}\n\n"
    elif example.model_outputs:
        model_labels = dict(ORDERED_MODEL_OUTPUT_LABELS)
        for model_key, output_file in validated_model_outputs(example):
            label = model_labels.get(model_key, model_key)
            md += f'{prefix}=== "{label}"\n\n'
            try:
                content = read_expected_file(output_file)
                md += indent_code_block(content, prefix + "    ")
            except (FileNotFoundError, ValueError) as e:
                md += f"{prefix}    > **Error:** {e}\n\n"
    elif example.golden_output:
        try:
            content = read_expected_file(example.golden_output)
            md += indent_code_block(content, prefix)
        except (FileNotFoundError, ValueError) as e:
            md += f"{prefix}> **Error:** {e}\n\n"
    return md


def _generate_extra_outputs(example: CLIDocExample, prefix: str = "    ") -> str:
    """Generate additional output files for a single example."""
    md = ""
    for output in example.extra_outputs:
        title = output.get("title", "Additional Output")
        output_path = output.get("path")
        language = output.get("language", "")
        if not output_path:
            md += f"{prefix}> **Error:** extra output is missing a path\n\n"
            continue

        md += f"{prefix}**{title}:**\n\n"
        try:
            content = read_expected_file(output_path)
            md += indent_code_block(content, prefix, language=language)
        except (FileNotFoundError, ValueError) as e:
            md += f"{prefix}> **Error:** {e}\n\n"
    return md


def _generate_example_with_input_output(example: CLIDocExample, prefix: str = "    ") -> str:
    """Generate combined input schema and output for an example."""
    md = ""

    # Input schema section
    if example.config_content:
        md += f"{prefix}**Configuration (pyproject.toml):**\n\n"
        md += f"{prefix}```toml\n"
        for line in example.config_content.strip().split("\n"):
            md += f"{prefix}{line}\n"
        md += f"{prefix}```\n\n"
    elif example.input_schema:
        md += f"{prefix}**Input Schema:**\n\n"
        try:
            schema_content = safe_read_file(
                DATA_PATH, example.input_schema, file_types=("*.json", "*.yaml", "*.yml", "*.graphql")
            )
            lang = _get_schema_lang(example.input_schema)
            md += f"{prefix}```{lang}\n"
            for line in schema_content.strip().split("\n"):
                md += f"{prefix}{line}\n"
            md += f"{prefix}```\n\n"
        except (FileNotFoundError, ValueError) as e:
            md += f"{prefix}> **Error:** {e}\n\n"

    # Output section
    md += f"{prefix}**Output:**\n\n"
    md += _generate_single_example_output(example, prefix)
    md += _generate_extra_outputs(example, prefix)

    return md


def generate_option_section(
    option: str,
    cli_doc_option: CLIDocOption,
    option_related_pages: dict[str, list[tuple[str, str]]] | None = None,
    documented_options: frozenset[str] | None = None,
) -> str:
    """Generate Markdown section for a single CLI option."""
    meta = get_option_meta(option)
    primary = cli_doc_option.get_primary()
    if not primary:
        return ""

    md = f"## `{option}` {{#{get_cli_doc_slug(get_cli_option_doc_name(option))}}}\n\n"

    # Parse option_description to separate description from admonitions
    option_description = cli_doc_option.get_option_description()
    parsed = (
        parse_docstring(option_description) if option_description else ParsedDocstring(description="", admonitions=[])
    )

    # Output description (without admonitions)
    if parsed.description:
        md += f"{parsed.description}\n\n"

    meta_parts = []
    aliases = cli_doc_option.get_aliases()
    if aliases:
        aliases_str = ", ".join(f"`{a}`" for a in aliases)
        meta_parts.append(f"**Aliases:** {aliases_str}")
    if meta and meta.since_version:
        meta_parts.append(f"**Since:** {meta.since_version}")
    if meta and meta.deprecated:
        msg = meta.deprecated_message or "Deprecated"
        meta_parts.append(f"**Deprecated:** {msg}")
    related_options = cli_doc_option.get_related_options()
    if related_options:
        related_links = []
        for r in related_options:
            # Skip self-references
            if r == option:
                continue
            canonical = _documented_related_option(r, documented_options or frozenset())
            # Also skip if canonical form is the current option
            if canonical == option:
                continue
            related_links.append(
                _format_option_link(
                    canonical,
                    documented_options or frozenset(),
                    current_category=meta.category if meta else None,
                )
            )
        if related_links:  # Only add Related if there are non-self-referencing options
            meta_parts.append(f"**Related:** {', '.join(related_links)}")

    if meta_parts:
        md += " | ".join(meta_parts) + "\n\n"

    if option_related_pages and option in option_related_pages:
        related_page_links = []
        for page_path, page_title in option_related_pages[option]:
            related_page_links.append(f"[{page_title}](../{page_path})")
        md += f"**See also:** {', '.join(related_page_links)}\n\n"

    md += _generate_option_relationships(option, meta, documented_options or frozenset())

    # Usage section (from primary example)
    md += '!!! tip "Usage"\n\n'
    md += "    ```bash\n"
    has_explicit_input = any(
        arg in {"--input", "--url"} or arg.startswith(("--input=", "--url=")) for arg in primary.cli_args
    )
    if primary.config_content or has_explicit_input:
        md += "    datamodel-codegen "
    else:
        md += "    datamodel-codegen --input schema.json "
    formatted_args = format_cli_args(primary.cli_args)
    md += " ".join(formatted_args)
    md += " # (1)!\n"
    md += "    ```\n\n"
    md += f"    1. :material-arrow-left: `{option}` - the option documented here\n\n"

    # Output admonitions (warnings, notes, etc.) after Usage section
    for admonition in parsed.admonitions:
        md += f"{admonition}\n\n"

    # Group examples by schema type
    examples_by_type: dict[str, list[CLIDocExample]] = defaultdict(list)
    for example in cli_doc_option.examples:
        schema_type = example.get_schema_type()
        examples_by_type[schema_type].append(example)

    # Determine if we need tabs (multiple schema types)
    schema_types = list(examples_by_type.keys())
    use_tabs = len(schema_types) > 1

    md += '??? example "Examples"\n\n'

    if use_tabs:
        # Multiple schema types - use tabs with input+output together
        schema_type_order = ["OpenAPI", "JSON Schema", "GraphQL", "JSON", "YAML", "CSV", "config", "Schema", "unknown"]
        sorted_types = sorted(schema_types, key=lambda t: schema_type_order.index(t) if t in schema_type_order else 999)

        for schema_type in sorted_types:
            examples = examples_by_type[schema_type]
            # Use first example of this type (or primary if it's of this type)
            example = next((ex for ex in examples if ex.is_primary), examples[0])

            md += f'    === "{schema_type}"\n\n'
            md += _generate_example_with_input_output(example, "        ")
    else:
        # Single schema type - show input schema and output separately (original behavior)
        example = primary

        if example.config_content:
            md += "    **Configuration (pyproject.toml):**\n\n"
            md += "    ```toml\n"
            for line in example.config_content.strip().split("\n"):
                md += f"    {line}\n"
            md += "    ```\n\n"
        elif example.input_schema:
            md += "    **Input Schema:**\n\n"
            try:
                schema_content = safe_read_file(
                    DATA_PATH, example.input_schema, file_types=("*.json", "*.yaml", "*.yml", "*.graphql")
                )
                lang = _get_schema_lang(example.input_schema)
                md += f"    ```{lang}\n"
                for line in schema_content.strip().split("\n"):
                    md += f"    {line}\n"
                md += "    ```\n\n"
            except (FileNotFoundError, ValueError) as e:
                md += f"    > **Error:** {e}\n\n"

        md += "    **Output:**\n\n"
        md += _generate_single_example_output(example, "    ")
        md += _generate_extra_outputs(example, "    ")

    md += "---\n\n"
    return md


def _documented_related_option(option: str, documented_options: frozenset[str]) -> str:
    """Return the related option name that has a generated section."""
    canonical = get_canonical_option(option)
    for candidate in (option, canonical):
        if candidate in documented_options:
            return candidate
    return canonical


def _format_option_link(
    option: str,
    documented_options: frozenset[str],
    *,
    current_category: OptionCategory | None = None,
    root: str = "",
    extension: str = ".md",
) -> str:
    """Return a Markdown link to a generated option section when metadata is available."""
    documented_option = _documented_related_option(option, documented_options)
    if not (meta := get_option_meta(documented_option)):
        return f"`{documented_option}`"

    if current_category == meta.category:
        target = f"#{get_cli_doc_slug(get_cli_option_doc_name(documented_option))}"
    elif doc_path := get_cli_option_doc_path(documented_option, root=root, extension=extension):
        target = doc_path
    else:
        return f"`{documented_option}`"
    return f"[`{documented_option}`]({target})"


def _recipe_option_names(recipes: tuple[CategoryRecipe, ...]) -> frozenset[str]:
    """Return recipe option names for preserving generated documentation aliases."""
    return frozenset(option for recipe in recipes for option in recipe.options)


def _homepage_recipes() -> tuple[CategoryRecipe, ...]:
    """Return a bounded recipe set for README and docs-home quick-start sections."""
    return tuple(recipes[0] for category in HOMEPAGE_RECIPE_CATEGORIES if (recipes := CATEGORY_RECIPES.get(category)))


def generate_homepage_recipe_quick_starts(
    *,
    cli_reference_root: str,
    cli_reference_extension: str,
    cli_reference_index: str,
) -> str:
    """Generate concise CLI recipe quick-starts for README and docs home."""
    if not (recipes := _homepage_recipes()):
        return ""

    documented_options = _recipe_option_names(recipes)
    md = "### CLI option quick starts\n\n"
    md += (
        "Use these starting points when combining options; each option links to the generated CLI reference for "
        "details and examples.\n\n"
    )
    for recipe in recipes:
        option_links = ", ".join(
            _format_option_link(
                option,
                documented_options,
                root=cli_reference_root,
                extension=cli_reference_extension,
            )
            for option in recipe.options
        )
        md += f"- **{recipe.title}:** {recipe.description} Options: {option_links}.\n"
    md += f"\nSee the [CLI Reference]({cli_reference_index}) for the full option list and category-specific recipes.\n"
    return md


def generate_category_recipes(
    category: OptionCategory,
    documented_options: frozenset[str],
) -> str:
    """Generate category recipes that link to option details."""
    if not (recipes := CATEGORY_RECIPES.get(category)):
        return ""

    md = "## 🍳 Recipes\n\n"
    for recipe in recipes:
        option_links = ", ".join(
            _format_option_link(option, documented_options, current_category=category) for option in recipe.options
        )
        md += f"### {recipe.title}\n\n"
        md += f"{recipe.description}\n\n"
        md += f"**Options:** {option_links}\n\n"
    return md


def _format_relation_value(value: Any) -> str:
    """Format a relationship value for Markdown output."""
    if value is True:
        return "enabled"
    if value is False:
        return "disabled"
    if value is None:
        return ""
    return f"= `{value}`"


def _format_relation_condition(option: str, when: Any) -> str:
    """Format the source option condition for a relationship."""
    if when is True:
        return f"When `{option}` is enabled, "
    if when is False:
        return f"When `{option}` is disabled, "
    if when is None:
        return ""
    return f"When `{option}={when}`, "


def _generate_option_relationships(
    option: str,
    meta: CLIOptionMeta | None,
    documented_options: frozenset[str],
) -> str:
    """Generate Markdown for CLI option relationship metadata."""
    if not meta:
        return ""

    relationship_lines: list[str] = []
    for relation_kind in OPTION_RELATION_KINDS:
        for relation in getattr(meta, relation_kind):
            option_link = _format_option_link(relation.option, documented_options)
            relation_value = _format_relation_value(relation.value)
            target = f"{option_link} {relation_value}".rstrip()
            condition = _format_relation_condition(option, relation.when)
            message = f" - {relation.message}" if relation.message else ""
            relationship_lines.append(f"- **{relation_kind.capitalize()}:** {condition}{target}{message}")

    if not relationship_lines:
        return ""

    return "**Option relationships:**\n\n" + "\n".join(relationship_lines) + "\n\n"


def _escape_relationship_table_cell(value: str) -> str:
    """Escape Markdown table delimiters while preserving generated links."""
    return value.replace("|", "\\|")


def _format_relation_condition_cell(option: str, when: Any) -> str:
    """Format a relationship condition for a summary table cell."""
    if when is True:
        return f"`{option}` enabled"
    if when is False:
        return f"`{option}` disabled"
    if when is None:
        return "Always"
    return f"`{option}` = `{when}`"


def generate_relationship_summary(
    categories: dict[OptionCategory, dict[str, CLIDocOption]],
    documented_options: frozenset[str],
) -> str:
    """Generate an index-level summary of CLI option relationship metadata."""
    rows: list[tuple[str, str, str, str, str]] = []
    for category in OPTION_CATEGORY_ORDER:
        for option in sorted(categories.get(category, {})):
            if not (meta := get_option_meta(option)):
                continue
            source = _format_option_link(option, documented_options)
            for relation_kind in OPTION_RELATION_KINDS:
                for relation in getattr(meta, relation_kind):
                    target = _format_option_link(relation.option, documented_options)
                    if relation_value := _format_relation_value(relation.value):
                        target = f"{target} {relation_value}"
                    rows.append((
                        source,
                        relation_kind.capitalize(),
                        _format_relation_condition_cell(option, relation.when),
                        target,
                        relation.message or "-",
                    ))

    if not rows:
        return ""

    md = "## 🔗 Option Relationships\n\n"
    md += (
        "These links are generated from CLI option metadata and summarize options that imply, require, "
        "or conflict with other options.\n\n"
    )
    md += "| Source | Kind | Condition | Target | Note |\n"
    md += "|--------|------|-----------|--------|------|\n"
    for row in rows:
        md += "| " + " | ".join(_escape_relationship_table_cell(cell) for cell in row) + " |\n"
    return md + "\n"


def generate_category_page(
    category: OptionCategory,
    options: dict[str, CLIDocOption],
    option_related_pages: dict[str, list[tuple[str, str]]] | None = None,
    documented_options: frozenset[str] | None = None,
) -> str:
    """Generate a category page with all options."""
    emoji = CATEGORY_EMOJIS.get(category, "📋")
    md = f"# {emoji} {category.value}\n\n"
    if intro := CATEGORY_INTROS.get(category):
        md += f"{intro}\n\n"
    md += "## 📋 Options\n\n"
    md += "| Option | Description |\n"
    md += "|--------|-------------|\n"
    for option in sorted(options.keys()):
        cli_doc_option = options[option]
        option_description = cli_doc_option.get_option_description()
        desc = summarize_description(option_description, DESC_LENGTH_SHORT) if option_description else ""
        md += f"| [`{option}`](#{get_cli_doc_slug(option)}) | {desc} |\n"
    md += "\n---\n\n"

    category_documented_options = documented_options or frozenset(options)
    if recipes := generate_category_recipes(category, category_documented_options):
        md += recipes
        md += "---\n\n"

    for option in sorted(options.keys()):
        cli_doc_option = options[option]
        md += generate_option_section(
            option,
            cli_doc_option,
            option_related_pages,
            category_documented_options,
        )

    return md


def generate_quick_reference(
    categories: dict[OptionCategory, dict[str, CLIDocOption]],
    manual_docs: dict[str, str] | None = None,
) -> str:
    """Generate a quick reference page with all options in CLI format for easy searching."""
    md = "# 🔍 Quick Reference\n\n"
    md += "All CLI options in one page for easy **Ctrl+F** searching.\n\n"
    md += "👆 Click any option to see detailed documentation with examples.\n\n"

    # Collect all options with their descriptions
    all_options: list[tuple[str, str, OptionCategory | None]] = []
    for category, options in categories.items():
        for option, cli_doc_option in options.items():
            option_description = cli_doc_option.get_option_description()
            desc = summarize_description(option_description) if option_description else ""
            all_options.append((option, desc, category))
    if manual_docs:
        for option in manual_docs:
            desc = MANUAL_OPTION_DESCRIPTIONS.get(option, "")
            all_options.append((option, desc, None))

    # Sort alphabetically by option name
    all_options.sort(key=operator.itemgetter(0))

    md += "---\n\n"
    md += "```text\n"
    md += "datamodel-codegen [OPTIONS]\n"
    md += "```\n\n"

    # Group by category for organized display
    md += "## 📂 All Options by Category\n\n"

    for category in OptionCategory:
        if category not in categories:
            continue
        options = categories[category]
        if not options:
            continue

        slug_cat = get_cli_doc_slug(category.value)
        emoji = CATEGORY_EMOJIS.get(category, "📋")
        md += f"### {emoji} {category.value}\n\n"
        md += "| Option | Description |\n"
        md += "|--------|-------------|\n"

        for option in sorted(options.keys()):
            cli_doc_option = options[option]
            option_description = cli_doc_option.get_option_description()
            desc = summarize_description(option_description, DESC_LENGTH_LONG) if option_description else ""
            slug_opt = get_cli_doc_slug(option)
            md += f"| [`{option}`]({slug_cat}.md#{slug_opt}) | {desc} |\n"

        md += "\n"

    if manual_docs:
        md += "### 📝 Utility Options\n\n"
        md += "| Option | Description |\n"
        md += "|--------|-------------|\n"
        for option in sorted(manual_docs.keys()):
            desc = MANUAL_OPTION_DESCRIPTIONS.get(option, "")
            slug_opt = get_cli_doc_slug(option)
            md += f"| [`{option}`](utility-options.md#{slug_opt}) | {desc} |\n"
        md += "\n"

    # Alphabetical list for Ctrl+F
    md += "---\n\n"
    md += "## 🔤 Alphabetical Index\n\n"
    md += "All options sorted alphabetically:\n\n"

    for option, desc, category in all_options:
        if category is None:
            md += f"- [`{option}`]({get_cli_option_doc_path(option)}) - {desc}\n"
        else:
            slug_cat = get_cli_doc_slug(category.value)
            slug_opt = get_cli_doc_slug(option)
            short_desc = desc[:DESC_LENGTH_SHORT] + "..." if len(desc) > DESC_LENGTH_SHORT else desc
            md += f"- [`{option}`]({slug_cat}.md#{slug_opt}) - {short_desc}\n"

    return md


TopicOptions = dict[OptionTopic, dict[OptionGroup, list[tuple[str, CLIDocOption]]]]


def _title_from_slug(value: str) -> str:
    """Return a display title for enum values stored as URL slugs."""
    return value.replace("openapi", "OpenAPI").replace("-", " ").title().replace("Openapi", "OpenAPI")


def _topic_title(topic: OptionTopic) -> str:
    """Return a stable topic title."""
    return _title_from_slug(topic.value)


def _group_title(group: OptionGroup) -> str:
    """Return a stable group title."""
    return _title_from_slug(group.value)


def _iter_topic_groups(topic: OptionTopic) -> tuple[OptionGroup, ...]:
    """Return topic groups in enum order."""
    allowed_groups = OPTION_TOPIC_ALLOWED_GROUPS.get(topic, frozenset())
    return tuple(group for group in OptionGroup if group in allowed_groups)


def _escape_table_cell(value: str) -> str:
    """Escape Markdown syntax that is ambiguous inside generated tables."""
    return value.replace("|", r"\|").replace("[", r"\[").replace("]", r"\]")


def collect_topic_options(categories: dict[OptionCategory, dict[str, CLIDocOption]]) -> TopicOptions:
    """Collect documented options by focused topic and subgroup."""
    topics: TopicOptions = defaultdict(lambda: defaultdict(list))
    for options in categories.values():
        for option, cli_doc_option in options.items():
            if not (meta := get_option_meta(option)):
                continue
            if meta.topic is None or meta.group is None:
                continue
            topics[meta.topic][meta.group].append((option, cli_doc_option))

    for groups in topics.values():
        for group_options in groups.values():
            group_options.sort(key=operator.itemgetter(0))
    return dict(topics)


def generate_topic_index(topic_options: TopicOptions) -> str:
    """Generate the CLI reference topic index section."""
    if not topic_options:
        return ""

    md = "## 🎯 Focused Topics\n\n"
    md += "Use these pages when you know the workflow area but not the exact option name.\n\n"
    md += "| Topic | Options | Groups |\n"
    md += "|-------|---------|--------|\n"
    for topic in OPTION_TOPIC_ORDER:
        if not (groups := topic_options.get(topic)):
            continue
        option_count = sum(len(options) for options in groups.values())
        group_names = ", ".join(_group_title(group) for group in _iter_topic_groups(topic) if group in groups)
        md += f"| [{_topic_title(topic)}](topics/{topic.value}.md) | {option_count} | {group_names} |\n"
    return md + "\n"


def generate_topic_page(topic: OptionTopic, groups: dict[OptionGroup, list[tuple[str, CLIDocOption]]]) -> str:
    """Generate one focused CLI topic page."""
    title = _topic_title(topic)
    md = f"# {title}\n\n"
    if description := TOPIC_DESCRIPTIONS.get(topic):
        md += f"{description}\n\n"
    md += "Options are grouped from shared CLI metadata and link back to their generated reference sections.\n\n"
    md += "## Groups\n\n"
    md += "| Group | Options | Description |\n"
    md += "|-------|---------|-------------|\n"
    for group in _iter_topic_groups(topic):
        if group not in groups:
            continue
        md += f"| [{_group_title(group)}](#{get_cli_doc_slug(group.value)}) | {len(groups[group])} | "
        md += f"{GROUP_DESCRIPTIONS.get(group, '')} |\n"
    md += "\n"

    for group in _iter_topic_groups(topic):
        if not (options := groups.get(group)):
            continue
        md += f"## {_group_title(group)} {{#{get_cli_doc_slug(group.value)}}}\n\n"
        if description := GROUP_DESCRIPTIONS.get(group):
            md += f"{description}\n\n"
        md += "| Option | Description |\n"
        md += "|--------|-------------|\n"
        for option, cli_doc_option in options:
            option_description = cli_doc_option.get_option_description()
            desc = summarize_description(option_description, DESC_LENGTH_LONG) if option_description else ""
            md += f"| [`{option}`]({get_cli_option_doc_path(option, root='..')}) | {_escape_table_cell(desc)} |\n"
        md += "\n"
    return md


def generate_index_page(
    categories: dict[OptionCategory, dict[str, CLIDocOption]],
    manual_docs: dict[str, str] | None = None,
) -> str:
    """Generate the index page with overview of all categories."""
    md = "# 🖥️ CLI Reference\n\n"
    md += "This documentation is auto-generated from test cases.\n\n"
    md += "🔍 **[Quick Reference](quick-reference.md)** - All options on one page for Ctrl+F search\n\n"

    md += "## 📂 Categories\n\n"
    md += "| Category | Options | Description |\n"
    md += "|----------|---------|-------------|\n"

    category_descriptions = {
        OptionCategory.BASE: "Input/output configuration",
        OptionCategory.TYPING: "Type annotation and import behavior",
        OptionCategory.FIELD: "Field naming and docstring behavior",
        OptionCategory.MODEL: "Model generation behavior",
        OptionCategory.TEMPLATE: "Output formatting and custom rendering",
        OptionCategory.OPENAPI: "OpenAPI-specific features",
        OptionCategory.GENERAL: "Utilities and meta options",
    }

    for category in OptionCategory:
        if category in categories:
            count = len(categories[category])
            desc = category_descriptions.get(category, "")
            slug = get_cli_doc_slug(category.value)
            emoji = CATEGORY_EMOJIS.get(category, "📋")
            md += f"| {emoji} [{category.value}]({slug}.md) | {count} | {desc} |\n"

    if manual_docs:
        md += f"| 📝 [Utility Options](utility-options.md) | {len(manual_docs)} | Help, version, debug options |\n"

    md += "\n"
    md += generate_topic_index(collect_topic_options(categories))
    documented_options = frozenset(option for options in categories.values() for option in options)
    if relationship_summary := generate_relationship_summary(categories, documented_options):
        md += relationship_summary

    md += "## All Options\n\n"
    all_options: list[tuple[str, OptionCategory | None]] = []
    for category, options in categories.items():
        all_options.extend((option, category) for option in options)
    if manual_docs:
        all_options.extend((option, None) for option in manual_docs)

    sorted_options = sorted(all_options)
    letters_with_options = sorted({opt.lstrip("-")[0].upper() for opt, _ in sorted_options})
    md += "**Jump to:** "
    md += " · ".join(f"[{letter}](#{letter.lower()})" for letter in letters_with_options)
    md += "\n\n"

    current_letter = None
    for option, category in sorted_options:
        first_letter = option.lstrip("-")[0].upper()
        if first_letter != current_letter:
            current_letter = first_letter
            md += f"\n### {current_letter} {{#{current_letter.lower()}}}\n\n"
        if category is None:
            md += f"- [`{option}`]({get_cli_option_doc_path(option)})\n"
        else:
            slug_cat = get_cli_doc_slug(category.value)
            slug_opt = get_cli_doc_slug(option)
            md += f"- [`{option}`]({slug_cat}.md#{slug_opt})\n"

    return md


def _doc_example_blocks_by_option_slug(existing_text: str) -> dict[str, str]:
    """Return existing injected docs example blocks keyed by CLI option heading slug."""
    headings = list(OPTION_HEADING_PATTERN.finditer(existing_text))
    blocks: dict[str, str] = {}
    for block_match in DOC_EXAMPLE_BLOCK_PATTERN.finditer(existing_text):
        heading = next((heading for heading in reversed(headings) if heading.end() <= block_match.start()), None)
        if heading is None:
            continue
        blocks[heading.group("slug")] = block_match.group(0).rstrip()
    return blocks


def _replace_option_example_block(generated_text: str, *, option_slug: str, replacement: str) -> str:
    """Replace a generated option example admonition with a preserved docs example block."""
    heading_match = re.search(rf"^## `[^`]+` \{{#{re.escape(option_slug)}\}}\n", generated_text, re.MULTILINE)
    if heading_match is None:
        return generated_text

    next_heading = OPTION_HEADING_PATTERN.search(generated_text, heading_match.end())
    section_end = next_heading.start() if next_heading else len(generated_text)
    example_start = generated_text.find(EXAMPLES_ADMONITION, heading_match.end(), section_end)
    if example_start == -1:
        return generated_text

    divider_start = generated_text.find("\n---\n", example_start, section_end)
    example_end = divider_start if divider_start != -1 else section_end
    return generated_text[:example_start] + replacement.rstrip() + "\n" + generated_text[example_end:]


def preserve_existing_doc_example_sections(generated_text: str, existing_text: str) -> str:
    """Preserve docs example sections injected after CLI docs generation."""
    updated_text = generated_text
    for option_slug, block in _doc_example_blocks_by_option_slug(existing_text).items():
        updated_text = _replace_option_example_block(updated_text, option_slug=option_slug, replacement=block)
    return updated_text


def read_manual_docs() -> dict[str, str]:
    """Read manual documentation files from MANUAL_DOCS_DIR.

    Returns:
        Dictionary mapping option name to markdown content.
    """
    manual_docs: dict[str, str] = {}
    if not MANUAL_DOCS_DIR.exists():
        return manual_docs

    for md_file in MANUAL_DOCS_DIR.glob("*.md"):
        option_name = f"--{md_file.stem}"
        if option_name in MANUAL_DOCS:
            manual_docs[option_name] = md_file.read_text(encoding="utf-8")
    return manual_docs


def generate_manual_docs_section(manual_docs: dict[str, str]) -> str:
    """Generate markdown page for manual documentation options."""
    if not manual_docs:
        return ""

    md = "# 📝 Utility Options\n\n"
    md += "## 📋 Options\n\n"
    md += "| Option | Description |\n"
    md += "|--------|-------------|\n"

    for option in sorted(manual_docs.keys()):
        desc = MANUAL_OPTION_DESCRIPTIONS.get(option, "")
        md += f"| [`{option}`](#{get_cli_doc_slug(option)}) | {desc} |\n"

    md += "\n---\n\n"

    for option in sorted(manual_docs.keys()):
        content = manual_docs[option]
        # Manual docs move one directory up when embedded in utility-options.md.
        content = content.replace("](../", "](")
        for related_option in manual_docs:
            related_slug = get_cli_doc_slug(related_option)
            content = content.replace(f"]({related_slug}.md#{related_slug})", f"](#{related_slug})")
        md += content
        if not content.endswith("\n"):
            md += "\n"
        md += "\n---\n\n"

    return md


def collect_cli_docs() -> int:
    """Run pytest to collect CLI doc metadata."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-cli-docs", "-p", "no:xdist", "-q"],
        check=False,
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    if result.returncode != 0:
        return result.returncode
    return 0


def build_docs(*, check: bool = False) -> int:
    """Build CLI documentation from collection data.

    Args:
        check: If True, validate that existing docs match generated content.
               Returns error if any differences found.
    """
    items: list[dict[str, Any]] = []
    if COLLECTION_PATH.exists():
        with Path(COLLECTION_PATH).open(encoding="utf-8") as f:
            collection = json.load(f)

        items = collection.get("items", [])

    manual_docs = read_manual_docs()
    if not items and not manual_docs:
        return 0

    # Group by option - collect ALL examples per option
    # Use the option as-is if it has metadata, otherwise use canonical
    options_map: dict[str, CLIDocOption] = {}
    for item in items:
        example = CLIDocExample.from_item(item)
        for opt in item["marker_kwargs"].get("options", []):
            # Use option as-is if it has metadata, otherwise use canonical
            # This allows --use-* and --no-use-* to be documented separately
            option_key = opt if get_option_meta(opt) else get_canonical_option(opt)
            if is_manual_doc(option_key):
                continue
            if option_key not in options_map:
                options_map[option_key] = CLIDocOption(option_name=option_key)
            options_map[option_key].add_example(example)

    categories: dict[OptionCategory, dict[str, CLIDocOption]] = defaultdict(dict)

    for option, cli_doc_option in options_map.items():
        meta = get_option_meta(option)
        if meta:
            categories[meta.category][option] = cli_doc_option
        else:
            categories[OptionCategory.GENERAL][option] = cli_doc_option

    # Scan markdown files for CLI option tags
    documented_options = frozenset(options_map)
    option_related_pages = scan_docs_for_cli_option_tags(documented_options)

    existing_docs: dict[Path, str] = {}
    if DOCS_OUTPUT.exists():
        existing_docs = {path: path.read_text(encoding="utf-8") for path in DOCS_OUTPUT.rglob("*.md")}

    if not check:
        DOCS_OUTPUT.mkdir(parents=True, exist_ok=True)
        TOPICS_OUTPUT.mkdir(parents=True, exist_ok=True)
        for old_file in DOCS_OUTPUT.glob("*.md"):
            old_file.unlink()
        for old_file in TOPICS_OUTPUT.glob("*.md"):
            old_file.unlink()

    generated = 0
    errors = 0
    mismatches: list[str] = []

    def write_or_check(output_path: Path, content: str, label: str) -> bool:
        """Write content to file or check if it matches existing content."""
        nonlocal generated, errors
        normalized_content = content.rstrip() + "\n"
        existing_text = (
            output_path.read_text(encoding="utf-8") if output_path.exists() else existing_docs.get(output_path)
        )
        if existing_text:
            normalized_content = preserve_existing_doc_example_sections(normalized_content, existing_text)
        if check:
            if not output_path.exists():
                mismatches.append(f"{label}: file does not exist")
                return False
            existing = output_path.read_text(encoding="utf-8").rstrip() + "\n"
            if existing != normalized_content:
                mismatches.append(f"{label}: content differs")
                return False
            return True
        output_path.write_text(normalized_content, encoding="utf-8")
        generated += 1
        return True

    # Iterate in OptionCategory enum order for consistent output
    for category in OptionCategory:
        if category not in categories:
            continue
        options = categories[category]
        if not options:
            continue
        try:
            md = generate_category_page(category, options, option_related_pages, documented_options)
            output_path = DOCS_OUTPUT / f"{get_cli_doc_slug(category.value)}.md"
            write_or_check(output_path, md, f"{output_path.name} ({len(options)} options)")
        except (OSError, ValueError, KeyError) as e:
            print(f"Error generating {category.value}: {e}", file=sys.stderr)
            errors += 1

    if manual_docs:
        try:
            md = generate_manual_docs_section(manual_docs)
            output_path = DOCS_OUTPUT / "utility-options.md"
            write_or_check(output_path, md, f"utility-options.md ({len(manual_docs)} options)")
        except (OSError, ValueError, KeyError) as e:
            print(f"Error generating utility-options.md: {e}", file=sys.stderr)
            errors += 1

    topic_options = collect_topic_options(categories)
    for topic in OPTION_TOPIC_ORDER:
        if not (groups := topic_options.get(topic)):
            continue
        try:
            md = generate_topic_page(topic, groups)
            output_path = TOPICS_OUTPUT / f"{topic.value}.md"
            count = sum(len(options) for options in groups.values())
            write_or_check(output_path, md, f"topics/{output_path.name} ({count} options)")
        except (OSError, ValueError, KeyError) as e:
            print(f"Error generating topics/{topic.value}.md: {e}", file=sys.stderr)
            errors += 1

    try:
        md = generate_index_page(categories, manual_docs)
        output_path = DOCS_OUTPUT / "index.md"
        write_or_check(output_path, md, "index.md")
    except (OSError, ValueError, KeyError) as e:
        print(f"Error generating index.md: {e}", file=sys.stderr)
        errors += 1

    try:
        md = generate_quick_reference(categories, manual_docs)
        output_path = DOCS_OUTPUT / "quick-reference.md"
        total_options = sum(len(opts) for opts in categories.values()) + len(manual_docs)
        write_or_check(output_path, md, f"quick-reference.md ({total_options} options)")
    except (OSError, ValueError, KeyError) as e:
        print(f"Error generating quick-reference.md: {e}", file=sys.stderr)
        errors += 1

    if check:
        if errors:
            print(f"Generation errors occurred: {errors}", file=sys.stderr)
            return 1
        if mismatches:
            for m in mismatches:
                print(f"Mismatch: {m}", file=sys.stderr)
            return 1
        return 0
    if errors:
        print(f"Errors occurred: {errors}", file=sys.stderr)
    return 1 if errors else 0


def main() -> int:
    """Parse CLI arguments and build documentation."""
    parser = argparse.ArgumentParser(description="Build CLI documentation from test cases")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check if docs are up to date without modifying files",
    )
    parser.add_argument(
        "--collect",
        action="store_true",
        help="Run pytest to collect CLI doc metadata first",
    )
    args = parser.parse_args()

    if args.collect:
        result = collect_cli_docs()
        if result != 0:
            return result

    return build_docs(check=args.check)


if __name__ == "__main__":
    sys.exit(main())
