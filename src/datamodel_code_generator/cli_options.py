"""CLI option metadata for documentation and browser integrations.

This module provides metadata for CLI options used in documentation generation
and the browser playground.
The option table (_option_table.py) is the source of truth for CLI behavior and for this
metadata: this module derives the documentation row of each option from its row, such as
its category, deprecation message and option relationships.

Synchronization between this module and argparse is verified by tests in
tests/cli_doc/test_cli_options_sync.py.
"""

from __future__ import annotations

import re
from argparse import BooleanOptionalAction
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from datamodel_code_generator._config_fields import TARGET_SELECTORS
from datamodel_code_generator._format_types import PythonVersion  # noqa: F401  # Preserve the established cli_options export.
from datamodel_code_generator._option_names import option_arguments as _arguments_negated_by
from datamodel_code_generator._option_names import option_flag
from datamodel_code_generator._option_names import values_arguments as _values_arguments_negated_by
from datamodel_code_generator._option_rules import (
    IMPLIED_OPTIONS,
    OPTION_RELATIONS,
    TARGET_INPUTS,
    TARGET_REQUIRED_OPTIONS,
)
from datamodel_code_generator._option_table import (
    OPTIONS,
    CLIOptionRelation,
    OptionCategory,
    OptionGroup,
    OptionTopic,
)
from datamodel_code_generator.deprecations import cli_deprecations
from datamodel_code_generator.enums import (  # noqa: F401  # Preserve the established cli_options exports.
    DataModelType,
    InputFileType,
    NamingStrategy,
    TargetPydanticVersion,
)
from datamodel_code_generator.python_literal import stable_repr

if TYPE_CHECKING:
    from argparse import Action, ArgumentParser
    from collections.abc import Iterable

    from datamodel_code_generator._option_table import Option


@dataclass(frozen=True)
class CLIOptionMeta:
    """Shared metadata for a CLI option.

    This is NOT the argparse definition - it only contains metadata.
    The actual CLI behavior is defined by the option table (_option_table.py).
    """

    name: str
    category: OptionCategory
    topic: OptionTopic | None = None
    group: OptionGroup | None = None
    since_version: str | None = None
    deprecated: bool = False
    deprecated_message: str | None = None
    implies: tuple[CLIOptionRelation, ...] = ()
    requires: tuple[CLIOptionRelation, ...] = ()
    conflicts: tuple[CLIOptionRelation, ...] = ()


OPTION_RELATION_KINDS = ("implies", "requires", "conflicts")
CLI_REFERENCE_ROOT = "cli-reference"
UTILITY_OPTIONS_DOC = "utility-options"
OPTION_TOPIC_ALLOWED_GROUPS: dict[OptionTopic, frozenset[OptionGroup]] = {
    OptionTopic.MODEL_CUSTOMIZATION: frozenset({
        OptionGroup.MODEL_NAMING,
        OptionGroup.MODEL_REUSE,
        OptionGroup.MODEL_SHAPE,
        OptionGroup.ROOT_MODEL,
    }),
    OptionTopic.TEMPLATE_CUSTOMIZATION: frozenset({
        OptionGroup.CUSTOM_TEMPLATES,
        OptionGroup.GENERATED_OUTPUT,
        OptionGroup.IMPORTS,
        OptionGroup.OUTPUT_FORMATTING,
    }),
    OptionTopic.TYPING_CUSTOMIZATION: frozenset({
        OptionGroup.COLLECTION_TYPES,
        OptionGroup.IMPORTS,
        OptionGroup.TYPE_ALIAS,
        OptionGroup.TYPE_MAPPING,
        OptionGroup.TYPE_SYNTAX,
    }),
    OptionTopic.OPENAPI: frozenset({
        OptionGroup.OPENAPI_NAMING,
        OptionGroup.OPENAPI_PATHS,
        OptionGroup.OPENAPI_SCOPES,
        OptionGroup.READ_ONLY_WRITE_ONLY,
    }),
}


# Options with manual documentation (not auto-generated from tests)
# These options have hand-written docs in docs/cli-reference/manual/
MANUAL_DOCS: frozenset[str] = frozenset({
    "--help",
    "--version",
    "--debug",
    "--profile",
    "--job",
    "--all-jobs",
    "--output-format",
    "--output-format-json-schema",
    "--no-color",
    "--generate-prompt",
    "--install-skill",
    "--skill-scope",
    "--overwrite-skill",
    "--list-deprecations",
    "--list-experimental",
})

# Backward compatibility alias
EXCLUDED_FROM_DOCS = MANUAL_DOCS

_CATEGORY_TOPIC = {
    OptionCategory.MODEL: OptionTopic.MODEL_CUSTOMIZATION,
    OptionCategory.TYPING: OptionTopic.TYPING_CUSTOMIZATION,
    OptionCategory.TEMPLATE: OptionTopic.TEMPLATE_CUSTOMIZATION,
    OptionCategory.OPENAPI: OptionTopic.OPENAPI,
}
_DEPRECATED_OPTIONS = cli_deprecations()


def _canonical_option_key(option: str) -> tuple[int, str]:
    """Sort key of the canonical option: the longest option string (--help over -h), then the lexicographically last."""
    return (len(option), option)


def _documented_options(options: tuple[Option, ...]) -> dict[str, CLIOptionMeta]:
    """Build the documentation rows of the options of the option table, in its order.

    A row documents the canonical one of its flags and, for a flag with a --no- form, that form (only that form when
    ``meta_on_negative_only``), unless the manual documents it. Its category is that of its help section unless the
    row names another, and a group takes the topic of its category unless the row names one. The relations of a row
    start from its positive flag, which also implies the option it turns on (``IMPLIED_OPTIONS``); every spelling of a
    target's option requires the option that selects the target, which requires the options and an input kind the
    target needs (``TARGET_REQUIRED_OPTIONS``, ``TARGET_INPUTS``; the kind as the value where the target reads one).
    An option is deprecated, with its message, when the deprecation registry has an entry for it.
    """
    prefixes = {option_flag(field): option_flag(prefix) for field, prefix in TARGET_SELECTORS}
    targets = {  # the prefix of each target's options, the option that selects the target and its values
        prefixes[selector]: (selector, " or ".join(f"`{selector} {value}`" for value in option.arguments["choices"]))
        for option in options
        if (selector := option.flags[0]) in prefixes
    }
    implied = {option_flag(option): option_flag(other) for option, other in IMPLIED_OPTIONS.items()}
    needs = {  # what each option that selects a target requires
        (flag := option_flag(field)): (
            *(
                CLIOptionRelation(required, message=f"`{flag}` requires `{required}`.")
                for required in (option_flag(f"{prefix}{name}") for name in TARGET_REQUIRED_OPTIONS)
            ),
            CLIOptionRelation(
                "--input-file-type",
                kinds[0].value if len(kinds := TARGET_INPUTS[field]) == 1 else None,
                message=f"`{flag}` requires {' or '.join(f'`--input-file-type {kind.value}`' for kind in kinds)}.",
            ),
        )
        for field, prefix in TARGET_SELECTORS
    }
    documented: dict[str, CLIOptionMeta] = {}
    for option in options:
        if (name := max(option.flags, key=_canonical_option_key)) in MANUAL_DOCS:
            continue
        category = option.category or OptionCategory[option.section.name]
        topic = option.topic or (_CATEGORY_TOPIC[category] if option.group else None)
        toggle = option.arguments.get("action") is BooleanOptionalAction
        names = [name, f"--no-{name.removeprefix('--')}"] if toggle else [name]
        target = next((target for prefix, target in targets.items() if name.startswith(prefix)), None)
        for spelling in names[option.meta_on_negative_only :]:
            implies, requires, conflicts = (
                (option.implies, (*option.requires, *needs.get(name, ())), option.conflicts)
                if spelling == name
                else ((), (), ())
            )
            if target is not None:
                selector, values = target
                requires = (*requires, CLIOptionRelation(selector, message=f"`{spelling}` requires {values}."))
            if spelling == name and name in implied:
                implies = (*implies, CLIOptionRelation(implied[name], value=True))
            documented[spelling] = CLIOptionMeta(
                spelling,
                category,
                topic,
                option.group,
                deprecated=(message := _DEPRECATED_OPTIONS.get(spelling)) is not None,
                deprecated_message=message,
                implies=implies,
                requires=requires,
                conflicts=conflicts,
            )
    return documented


def _with_relation_rows(options: dict[str, CLIOptionMeta]) -> dict[str, CLIOptionMeta]:
    """Add the relations of ``OPTION_RELATIONS``, the rows a run checks, to the options they start from."""
    for row in OPTION_RELATIONS:
        option, kind, other = row.pair
        ends = [(option, CLIOptionRelation(other, row.value, row.when, row.message))]
        if kind == "conflicts":
            ends.append((other, CLIOptionRelation(option, message=row.other_message)))
        for start, relation in ends if row.documented else ():
            options[start] = replace(options[start], **{kind: (*getattr(options[start], kind), relation)})
    return options


# Documentation metadata for CLI options, derived from the option table
CLI_OPTION_META: dict[str, CLIOptionMeta] = _with_relation_rows(_documented_options(OPTIONS))


def _validate_option_topic_groups() -> None:
    """Validate CLI option topic/group invariants at import time."""
    for option, meta in CLI_OPTION_META.items():
        if meta.topic is None or meta.group is None:
            continue
        allowed_groups = OPTION_TOPIC_ALLOWED_GROUPS.get(meta.topic)
        if allowed_groups is None:  # pragma: no cover
            msg = f"{option}: topic {stable_repr(meta.topic.value)} has no allowed groups"
            raise AssertionError(msg)
        if meta.group not in allowed_groups:  # pragma: no cover
            msg = (
                f"{option}: group {stable_repr(meta.group.value)} is not allowed for topic "
                f"{stable_repr(meta.topic.value)}"
            )
            raise AssertionError(msg)


_validate_option_topic_groups()


@dataclass(frozen=True, slots=True)
class _ArgparseIndex:
    """The option strings of the command-line parser: each one's canonical option and siblings, actions by dest."""

    canonical: dict[str, str]
    siblings: dict[str, tuple[str, ...]]
    actions: dict[str, Action]


@lru_cache(maxsize=1)
def _argparse_index() -> _ArgparseIndex:
    """Index the command-line parser (the source of truth), when a caller first needs it.

    The canonical option is the longest option string of an action; among options of one length, the
    lexicographically last. A destination maps to the last action with option strings that stores it.
    """
    from datamodel_code_generator.arguments import arg_parser as argument_parser  # noqa: PLC0415

    index = _ArgparseIndex({}, {}, {})
    for action in argument_parser._actions:  # noqa: SLF001
        if not (options := tuple(action.option_strings)):
            continue  # pragma: no cover
        canonical = max(options, key=_canonical_option_key)
        for option in options:
            index.canonical[option] = canonical
            index.siblings[option] = options
        index.actions[action.dest] = action
    return index


def get_canonical_option(option: str) -> str:
    """Normalize an option alias to its canonical form.

    Uses argparse definitions as the source of truth.

    Examples:
        >>> get_canonical_option("-h")
        '--help'
        >>> get_canonical_option("--help")
        '--help'
    """
    return _argparse_index().canonical.get(option, option)


def _actions_by_dest(parser: ArgumentParser | None) -> dict[str, Action]:
    """Map argparse destination names to the actions that have option strings."""
    if parser is None:
        return {}
    return {action.dest: action for action in parser._actions if action.option_strings}  # noqa: SLF001


def _no_option(action: Action) -> str | None:
    """Return the first --no- option string of an action, or None."""
    return next((option for option in action.option_strings if option.startswith("--no-")), None)


def _negative_option(dest: str) -> str | None:
    """Return the --no- flag the parser defines for a destination, or None.

    A boolean's is the --no- string of its action; a list option's is the flag that turns it off with an empty list.
    """
    index = _argparse_index()
    if isinstance(action := index.actions.get(dest), BooleanOptionalAction):
        return _no_option(action)
    return flag if (flag := option_flag(dest, negative=True)) in index.canonical else None


def _option_arguments(field: str, value: object) -> list[Any] | None:
    """Return the arguments that set a config field to a value (``option_arguments``), with this parser's --no-."""
    return _arguments_negated_by(field, value, negative=_negative_option)


def _values_arguments(values: Iterable[tuple[str, object]]) -> list[Any]:
    """Return the arguments that set config fields to values (``values_arguments``), with this parser's --no-."""
    return _values_arguments_negated_by(values, negative=_negative_option)


@lru_cache(maxsize=1)
def get_all_canonical_options() -> frozenset[str]:
    """Get all canonical options from argparse."""
    return frozenset(_argparse_index().canonical.values())


@lru_cache(maxsize=1)
def get_all_argparse_options() -> frozenset[str]:
    """Get all options from argparse (including aliases)."""
    return frozenset(_argparse_index().canonical.keys())


def is_manual_doc(option: str) -> bool:
    """Check if an option has manual documentation (not auto-generated)."""
    canonical = get_canonical_option(option)
    return canonical in MANUAL_DOCS


# Backward compatibility alias
def is_excluded_from_docs(option: str) -> bool:
    """Check if an option is excluded from auto-generated documentation.

    Deprecated: Use is_manual_doc() instead.
    """
    return is_manual_doc(option)


def get_option_meta(option: str) -> CLIOptionMeta | None:
    """Get documentation metadata for an option.

    Uses an exact match first so BooleanOptionalAction variants can carry
    separate metadata, then the alias of the same variant that has a section, then the canonical option name.
    If the option is not explicitly registered, returns a default entry
    with General category (auto-categorization for new options).
    """
    if option in CLI_OPTION_META:
        return CLI_OPTION_META[option]
    canonical = get_canonical_option(option)
    for name in (get_cli_option_doc_name(option), canonical):
        if name in CLI_OPTION_META:
            return CLI_OPTION_META[name]
    if canonical in get_all_canonical_options() and canonical not in EXCLUDED_FROM_DOCS:
        return CLIOptionMeta(name=canonical, category=OptionCategory.GENERAL)
    return None


def get_cli_doc_slug(text: str) -> str:
    """Convert CLI documentation text to the generated page/anchor slug."""
    slug = text.lstrip("-").lower()
    slug = re.sub(r"[^a-z0-9-]", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-")
    return slug or "unknown"


def get_cli_option_doc_name(option: str) -> str:
    """Return the option name used by generated CLI documentation anchors.

    The --name and --no-name forms of a flag have a section each; the aliases of a form share the section of the
    canonical one of them.
    """
    canonical = get_canonical_option(option)
    if canonical in MANUAL_DOCS:
        return canonical
    options = _argparse_index().siblings.get(option, ())
    if option.startswith("--") and any(item.startswith("--no-") for item in options):
        negative = option.startswith("--no-")
        return max((item for item in options if item.startswith("--no-") is negative), key=_canonical_option_key)
    return canonical


def get_cli_option_doc_path(option: str, *, root: str = "", extension: str = ".md") -> str | None:
    """Return the generated CLI documentation path for an option."""
    doc_option = get_cli_option_doc_name(option)
    if doc_option in MANUAL_DOCS:
        page = UTILITY_OPTIONS_DOC
    elif meta := get_option_meta(doc_option):
        page = get_cli_doc_slug(meta.category.value)
    else:
        return None

    prefix = f"{root.rstrip('/')}/" if root else ""
    return f"{prefix}{page}{extension}#{get_cli_doc_slug(doc_option)}"
