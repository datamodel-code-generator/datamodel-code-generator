"""Prompt generation for LLM consultation.

This module generates prompts suitable for consulting LLMs (ChatGPT, Claude, etc.)
about appropriate CLI options for datamodel-code-generator.
"""

from __future__ import annotations

import json
import re
from argparse import SUPPRESS, Action, ArgumentParser, BooleanOptionalAction
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from datamodel_code_generator.cli_options import CLI_OPTION_META, MANUAL_DOCS, OptionCategory
from datamodel_code_generator.prompt_data import OPTION_DESCRIPTIONS

if TYPE_CHECKING:
    from argparse import Namespace

# Options to exclude from prompt output
PROMPT_EXCLUDED_OPTIONS: frozenset[str] = frozenset({
    "generate_prompt",
    "generate_pyproject_config",
    "generate_cli_command",
    "recommend_options",
    "install_skill",
    "skill_scope",
    "overwrite_skill",
    "version",
    "help",
    "no_color",
    "output_format",
    "output_format_json_schema",
})

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
JSON_SCHEMA_DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"

PROMPT_GUIDANCE_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Agent Task",
        (
            "Recommend a final CLI command for the user's schema and target runtime. Include:",
            "- Final `datamodel-codegen` command in a shell code block.",
            "- Brief explanation for each selected option.",
            "- Rejected alternatives with the reason they do not fit.",
            (
                "- Verification command, such as `datamodel-codegen ... --output models.py --check` "
                "(`--check` requires `--output`) or a diff against expected output."
            ),
        ),
    ),
    (
        "Decision Checklist",
        (
            (
                "- Schema-specific options: when the input schema is available, add `--recommend-options` to this "
                "command (experimental) to include the schema's facts and the options recommended for it on top of "
                "the latest dated preset."
            ),
            (
                "- Input type: auto-detected schema/data, OpenAPI, AsyncAPI, JSON Schema, MCP tools, "
                "XML Schema, Protocol Buffers, Avro, GraphQL, CSV, Python input model, or raw JSON/YAML/dict data."
            ),
            (
                "- Output model type: `pydantic_v2.BaseModel`, `pydantic_v2.dataclass`, "
                "`dataclasses.dataclass`, `typing.TypedDict`, or `msgspec.Struct`."
            ),
            (
                "- Python/Pydantic version: align `--target-python-version`, `--output-model-type`, and "
                "`--target-pydantic-version` when generating Pydantic v2 models."
            ),
            (
                "- Strictness: choose `--strict-types` values, `--strict-nullable`, `--field-constraints`, and "
                "`--extra-fields`."
            ),
            (
                "- Aliases/naming: decide between API-compatible aliases and normalized names such as "
                "`--snake-case-field`."
            ),
            "- Module layout: choose one file or an output directory with `--module-split-mode` and reuse options.",
            (
                "- Structured output: use `--output-format json` for machine-readable prompt or generation output, "
                "`--emit-model-metadata` for generated model maps, and `--output-format-json-schema` when a tool "
                "needs a validation schema."
            ),
            (
                "- Runtime model base: use `--base-class` for a custom base class; "
                "it is separate from `--output-model-type`."
            ),
            "- Validation constraints: prefer `--field-constraints`; add `--use-annotated` for Pydantic v2.",
        ),
    ),
    (
        "Common Recipes",
        (
            (
                "- Strict Pydantic v2: `--output-model-type pydantic_v2.BaseModel --target-pydantic-version 2.11 "
                "--use-annotated --field-constraints`, plus needed `--strict-types` values."
            ),
            (
                "- OpenAPI request/response models: `--input-file-type openapi --openapi-scopes schemas paths "
                "--read-only-write-only-model-type request-response`."
            ),
            (
                "- TypedDict modern syntax: `--output-model-type typing.TypedDict --target-python-version 3.12 "
                "--use-standard-collections --use-union-operator`."
            ),
            (
                "- Multi-module OpenAPI output: set `--output` to a directory and use `--module-split-mode single "
                "--all-exports-scope recursive --use-exact-imports`."
            ),
            (
                "- Machine-readable agent flow: run `--generate-prompt --output-format json` first, then validate "
                "tool integration with `--output-format-json-schema generate-prompt` or `structured-output`."
            ),
        ),
    ),
    (
        "Important Option Relationships",
        (
            "- `--use-annotated` also enables `--field-constraints`; prefer it for constrained Pydantic v2 fields.",
            "- `--openapi-include-paths` only has an effect when `--openapi-scopes paths` is included.",
            "- `--strict-types` requires one or more values: `str`, `int`, `float`, `bool`, or `bytes`.",
            "- `--use-specialized-enum` requires `--target-python-version >= 3.11`.",
            (
                "- `--output-format json` is supported for generation, `--generate-prompt`, `--recommend-options`, "
                "`--check`, and `--diff-against`; "
                "not for `--watch`."
            ),
            (
                "- `--output-format-json-schema` accepts `config`, `generate-prompt`, `generation`, "
                "`model-metadata`, `recommend-options`, or `structured-output`; use `config` for JSON configuration "
                "schemas."
            ),
            "- `--validation` is deprecated; use `--field-constraints` for generated Field constraints.",
        ),
    ),
)


class CurrentOptionPayload(BaseModel):
    """Machine-readable metadata for a CLI option currently set by the caller."""

    model_config = ConfigDict(extra="forbid")

    name: str
    dest: str
    value: Any
    flags: list[str]


class OptionMetadataPayload(BaseModel):
    """Machine-readable metadata for one argparse option."""

    model_config = ConfigDict(extra="forbid")

    name: str
    flags: list[str]
    dest: str
    category: str
    description: str
    choices: list[Any] | None
    nargs: Any
    default: Any
    required: bool
    metavar: Any
    type: str | None
    action: str
    deprecated: bool
    deprecated_message: str | None


class PromptPayload(BaseModel):
    """Structured JSON payload emitted by --generate-prompt --output-format json."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    format: Literal["json"]
    kind: Literal["prompt"]
    question: str
    current_options: list[CurrentOptionPayload]
    current_options_text: str
    options_by_category: dict[str, list[OptionMetadataPayload]]
    options: list[OptionMetadataPayload]
    help_text: str
    recommendation: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "The --recommend-options --output-format json payload for the input schema (experimental; its schema is "
            "--output-format-json-schema recommend-options); absent unless the command has --recommend-options."
        ),
    )


def _prompt_payload_json_schema() -> dict[str, Any]:
    """Return the JSON Schema for structured prompt output.

    The payload has no serializers, so it validates as it serializes; validation mode keeps a key the payload may leave
    out optional on every Pydantic 2 release, which serialization mode marks required before Pydantic 2.4.
    """
    return {
        "$schema": JSON_SCHEMA_DRAFT_2020_12,
        **PromptPayload.model_json_schema(mode="validation"),
    }


def _dump_json(value: Any) -> str:
    """Serialize a JSON-compatible value with stable formatting."""
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _dump_payload(payload: PromptPayload) -> str:
    """Serialize a prompt payload with stable JSON formatting, leaving out a recommendation it does not have."""
    exclude = None if payload.recommendation else {"recommendation"}
    return _dump_json(payload.model_dump(mode="json", exclude=exclude))


def generate_prompt_json_schema() -> str:
    """Generate the JSON Schema for --generate-prompt --output-format json."""
    return _dump_json(_prompt_payload_json_schema())


def _serialize_value(value: Any) -> Any:
    """Convert argparse metadata values to JSON-serializable values."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if hasattr(value, "value"):
        return _serialize_value(value.value)
    return str(value)


def _canonical_option(action: Action) -> str:
    """Return the canonical option name for an argparse action."""
    if long_options := [option for option in action.option_strings if option.startswith("--")]:
        positive_options = [option for option in long_options if not option.startswith("--no-")]
        return max(positive_options or long_options, key=lambda option: (len(option), option))
    return max(action.option_strings, key=lambda option: (len(option), option))


def _positive_option(action: Action, fallback: str) -> str:
    """Return the positive CLI flag for an action."""
    for option in action.option_strings:
        if option.startswith("--") and not option.startswith("--no-"):
            return option
    return fallback


def _negative_option(action: Action, fallback: str) -> str:
    """Return the negative CLI flag for a BooleanOptionalAction."""
    for option in action.option_strings:
        if option.startswith("--no-"):
            return option
    return fallback


def _actions_by_dest(parser: ArgumentParser | None) -> dict[str, Action]:
    """Map argparse destination names to actions."""
    if parser is None:
        return {}
    return {action.dest: action for action in parser._actions if action.option_strings}  # noqa: SLF001


def _format_current_option(key: str, value: Any, action: Action | None) -> str | None:
    """Format a current CLI option as a shell-like line."""
    cli_key = key.replace("_", "-")
    fallback = f"--{cli_key}"

    if isinstance(value, bool):
        match action:
            case BooleanOptionalAction():
                return _positive_option(action, fallback) if value else _negative_option(action, fallback)
            case _:
                return fallback if value else None

    option = _positive_option(action, fallback) if action else fallback
    if isinstance(value, list):
        return f"{option} {' '.join(str(v) for v in value)}"
    return f"{option} {value}"


def _format_current_options(args: Namespace, parser: ArgumentParser | None = None) -> str:
    """Format currently set CLI options."""
    lines: list[str] = []
    args_dict = vars(args)
    actions = _actions_by_dest(parser)

    for key, value in sorted(args_dict.items()):
        if value is None:
            continue
        if key in PROMPT_EXCLUDED_OPTIONS:
            continue

        if line := _format_current_option(key, value, actions.get(key)):
            lines.append(line)

    return "\n".join(lines) if lines else "(No options specified)"


def _current_options_metadata(args: Namespace, parser: ArgumentParser) -> list[CurrentOptionPayload]:
    """Return machine-readable metadata for currently set CLI options."""
    current_options: list[CurrentOptionPayload] = []
    actions = _actions_by_dest(parser)

    for key, value in sorted(vars(args).items()):
        if value is None or key in PROMPT_EXCLUDED_OPTIONS:
            continue
        action = actions.get(key)
        if not (name := _format_current_option(key, value, action)):
            continue
        option_name = _canonical_option(action) if action else name.split(maxsplit=1)[0]
        current_options.append(
            CurrentOptionPayload(
                name=option_name,
                dest=key,
                value=_serialize_value(value),
                flags=list(action.option_strings) if action else [option_name],
            )
        )

    return current_options


def _option_category(action: Action) -> OptionCategory:
    """Resolve an argparse action to a documentation category."""
    for option in action.option_strings:
        if meta := CLI_OPTION_META.get(option):
            return meta.category
        if option in MANUAL_DOCS:
            return OptionCategory.GENERAL
    return OptionCategory.GENERAL


def _option_description(action: Action, name: str) -> str:
    """Return a user-facing description for an option."""
    if action.help and action.help != SUPPRESS:
        return action.help
    return OPTION_DESCRIPTIONS.get(name, "")


def _option_type(action: Action) -> str | None:
    """Return a stable display name for an argparse type."""
    action_type = getattr(action, "type", None)
    if action_type is None:
        return None
    if type_name := getattr(action_type, "__name__", None):
        return type_name
    return str(action_type)


def _option_default(action: Action, name: str) -> Any:
    """Return the user-facing default value for an option."""
    if name == "--output-format":
        return "text"
    if action.default == SUPPRESS:
        return None
    return _serialize_value(action.default)


def _option_metadata(action: Action) -> OptionMetadataPayload:
    """Build machine-readable metadata for one argparse action."""
    name = _canonical_option(action)
    meta = next((CLI_OPTION_META[option] for option in action.option_strings if option in CLI_OPTION_META), None)
    choices = None if action.choices is None else [_serialize_value(choice) for choice in action.choices]

    return OptionMetadataPayload(
        name=name,
        flags=list(action.option_strings),
        dest=action.dest,
        category=_option_category(action).value,
        description=_option_description(action, name),
        choices=choices,
        nargs=_serialize_value(action.nargs),
        default=_option_default(action, name),
        required=action.required,
        metavar=_serialize_value(action.metavar),
        type=_option_type(action),
        action=action.__class__.__name__.removeprefix("_"),
        deprecated=bool(meta and meta.deprecated),
        deprecated_message=meta.deprecated_message if meta else None,
    )


def _all_options_metadata(parser: ArgumentParser) -> list[OptionMetadataPayload]:
    """Return metadata for all argparse options."""
    options = [_option_metadata(action) for action in parser._actions if action.option_strings]  # noqa: SLF001
    return sorted(options, key=lambda option: (option.category, option.name))


def _options_by_category_metadata(parser: ArgumentParser) -> dict[str, list[OptionMetadataPayload]]:
    """Return argparse option metadata grouped by documentation category."""
    by_category: dict[str, list[OptionMetadataPayload]] = {category.value: [] for category in OptionCategory}
    for option in _all_options_metadata(parser):
        by_category[option.category].append(option)
    return by_category


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences from text."""
    return ANSI_ESCAPE_RE.sub("", text)


def _format_options_by_category() -> str:
    """Format options grouped by category with descriptions."""
    by_category: dict[OptionCategory, list[tuple[str, str]]] = defaultdict(list)

    for option, meta in CLI_OPTION_META.items():
        desc = OPTION_DESCRIPTIONS.get(option, "")
        by_category[meta.category].append((option, desc))

    lines: list[str] = []
    for category in OptionCategory:
        options = by_category[category]
        lines.append(f"### {category.value}")
        for opt, desc in sorted(options):
            if desc:
                lines.append(f"- `{opt}`: {desc}")
            else:  # pragma: no cover
                lines.append(f"- `{opt}`")
        lines.append("")

    return "\n".join(lines)


def _format_guidance_sections() -> str:
    """Format concise agent guidance sections."""
    lines: list[str] = []

    for title, entries in PROMPT_GUIDANCE_SECTIONS:
        lines.extend((f"## {title}", ""))
        lines.extend(entries)
        lines.append("")

    return "\n".join(lines)


def _generate_prompt_json(
    args: Namespace, help_text: str, parser: ArgumentParser, recommendation: dict[str, Any] | None
) -> str:
    """Generate a machine-readable LLM consultation payload."""
    payload = PromptPayload(
        version=1,
        format="json",
        kind="prompt",
        question=getattr(args, "generate_prompt", "") or "",
        current_options=_current_options_metadata(args, parser),
        current_options_text=_format_current_options(args, parser),
        options_by_category=_options_by_category_metadata(parser),
        options=_all_options_metadata(parser),
        help_text=_strip_ansi(help_text),
        recommendation=recommendation or {},
    )
    return _dump_payload(payload)


def _recommendation_lines(recommendation: dict[str, Any] | None) -> tuple[str, ...]:
    """Format the embedded --recommend-options report as a prompt section.

    The report quotes names from the schema, so its fence is longer than any run of backticks in it.
    """
    if recommendation is None:
        return ()
    content = recommendation["content"].rstrip()
    fence = "`" * max(3, max(map(len, re.findall(r"`+", content)), default=0) + 1)
    return (
        "## Schema analysis and recommended options",
        "",
        "The `--recommend-options` report for the input schema (experimental):",
        "",
        fence,
        content,
        fence,
        "",
    )


def generate_prompt(
    args: Namespace,
    help_text: str,
    parser: ArgumentParser | None = None,
    *,
    recommendation: dict[str, Any] | None = None,
) -> str:
    """Generate LLM consultation prompt.

    Args:
        args: Parsed command-line arguments.
        help_text: Full help text from arg_parser.format_help().
        parser: Argument parser used to extract machine-readable option metadata.
        recommendation: The --recommend-options JSON payload to embed: JSON output includes it as is, and text output
            shows its report and asks to start from its command.

    Returns:
        Formatted prompt string suitable for LLM consultation.
    """
    if parser is None:
        from datamodel_code_generator.arguments import arg_parser  # noqa: PLC0415

        parser = arg_parser

    if getattr(args, "output_format", None) == "json":
        return _generate_prompt_json(args, help_text, parser, recommendation)

    lines: list[str] = []

    # Header
    lines.extend(("# datamodel-code-generator CLI Options Consultation", ""))

    # User's question (if provided)
    question = getattr(args, "generate_prompt", "") or ""
    if question:
        lines.extend(("## Your Question", "", question, ""))

    # Current configuration
    lines.extend((
        "## Current CLI Options",
        "",
        "```",
        _format_current_options(args, parser),
        "```",
        "",
        *_recommendation_lines(recommendation),
        # Options by category with descriptions
        "## Options by Category",
        "",
        _format_options_by_category(),
        # Full help text
        "## All Available Options (Full Help)",
        "",
        "```",
        help_text,
        "```",
        "",
        # Concise guidance for LLM agents choosing option combinations
        _format_guidance_sections(),
        # Instructions for LLM
        "## Instructions",
        "",
        "Based on the above information, propose the smallest datamodel-codegen",
        "command that satisfies the question or use case. Preserve current options",
        "unless they conflict with the goal or another option. Include:",
        "",
        "1. Final CLI command to run",
        "2. Reasons for each non-obvious added or changed option",
        "3. Current options that should remain unchanged",
        "4. Options considered but rejected, with a short reason",
        "5. Verification command that validates the generated CLI or output",
    ))
    if recommendation is not None:
        lines.extend((
            "",
            'Start from the command in "Schema analysis and recommended options" when it',
            "has one, and give a reason for every option you add to it, change, or remove.",
        ))

    return "\n".join(lines)
