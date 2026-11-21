"""CLI argument definitions for datamodel-codegen.

Defines the ArgumentParser of datamodel-codegen, which adds the options of the option table (``_option_table.py``)
in their groups: base options, typing customization, field customization, model customization, template
customization, OpenAPI-specific options, and general options.
"""

from __future__ import annotations

from argparse import OPTIONAL, ArgumentParser, Namespace, RawDescriptionHelpFormatter
from operator import attrgetter
from typing import TYPE_CHECKING, cast

from datamodel_code_generator._config_fields import DEFAULT_ENCODING
from datamodel_code_generator._option_table import OPTIONS, Exclusive, Section

if TYPE_CHECKING:
    from argparse import Action, _ActionsContainer
    from collections.abc import Iterable, Sequence


def _reset_namespace(target: Namespace) -> None:
    """Empty ``target`` to the namespace a parse starts from: colors on, every other option unset."""
    vars(target).clear()
    target.no_color = False


namespace = Namespace()
_reset_namespace(namespace)


class SortingHelpFormatter(RawDescriptionHelpFormatter):
    """Help formatter that sorts arguments, adds color to section headers, and preserves epilog formatting."""

    def _bold_cyan(self, text: str) -> str:  # noqa: PLR6301
        """Wrap text in ANSI bold cyan escape codes."""
        return f"\x1b[36;1m{text}\x1b[0m"

    def _format_action_invocation(self, action: Action) -> str:
        """Show an option whose value is optional and has an empty metavar without a value placeholder."""
        if action.nargs == OPTIONAL and isinstance(action.metavar, str) and not action.metavar:
            return ", ".join(action.option_strings)
        return super()._format_action_invocation(action)

    def add_arguments(self, actions: Iterable[Action]) -> None:
        """Add arguments sorted by option strings."""
        actions = sorted(actions, key=attrgetter("option_strings"))
        super().add_arguments(actions)

    def start_section(self, heading: str | None) -> None:
        """Start a section with optional colored heading."""
        return super().start_section(heading if namespace.no_color or not heading else self._bold_cyan(heading))


class SuggestingArgumentParser(ArgumentParser):
    """Argument parser that suggests close matches for unknown option names."""

    def parse_args(self, args: Iterable[str] | None = None, namespace: Namespace | None = None) -> Namespace:
        """Parse arguments and suggest close matches for unknown option names."""
        parsed_args, unknown_arguments = self.parse_known_args(args, namespace)
        if not unknown_arguments:
            return cast("Namespace", parsed_args)
        return self.error(self._unrecognized_arguments_message(unknown_arguments))

    def _unrecognized_arguments_message(self, unknown_arguments: Sequence[str]) -> str:
        """Format an unknown-argument message with close option-name matches."""
        message = f"unrecognized arguments: {' '.join(unknown_arguments)}"
        suggestions: list[str] = []
        for unknown_argument in unknown_arguments:
            match unknown_argument:
                case "--":
                    break
                case option_name if option_name.startswith("-"):
                    option_name = option_name.partition("=")[0]
                case _:
                    continue

            from difflib import get_close_matches  # noqa: PLC0415

            if (matches := get_close_matches(option_name, self._option_string_actions, n=1, cutoff=0.7)) and (
                suggestion := matches[0]
            ) not in suggestions:
                suggestions.append(suggestion)
        if not suggestions:
            return message
        return f"{message}\nDid you mean: {', '.join(suggestions)}?"


arg_parser = SuggestingArgumentParser(
    usage="\n  datamodel-codegen [options]",
    description="Generate Python data models from schema definitions or structured data\n\n"
    "For detailed usage, see: https://datamodel-code-generator.koxudaxi.dev",
    epilog="Documentation: https://datamodel-code-generator.koxudaxi.dev\n"
    "Agent skill: https://datamodel-code-generator.koxudaxi.dev/coding-agent-skill/\n"
    "Option recommendations: datamodel-codegen --input SCHEMA --recommend-options (experimental)\n"
    "GitHub: https://github.com/datamodel-code-generator/datamodel-code-generator",
    formatter_class=SortingHelpFormatter,
    add_help=False,
)


def _add_options(parser: ArgumentParser) -> None:
    """Add the options of the option table to the parser, each in its help section or its set of exclusive options."""
    sections = {section: parser.add_argument_group(section.value) for section in Section}
    containers: dict[Section | Exclusive, _ActionsContainer] = {
        **sections,
        **{exclusive: sections[exclusive.section].add_mutually_exclusive_group() for exclusive in Exclusive},
    }
    for option in OPTIONS:
        containers[option.place].add_argument(*option.flags, **option.arguments)


_add_options(arg_parser)

__all__ = [
    "DEFAULT_ENCODING",
    "arg_parser",
    "namespace",
]
