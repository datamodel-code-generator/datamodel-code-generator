"""Write CLI arguments as a `pyproject.toml` table (`--generate-pyproject-config`) and a table as a command line."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from datamodel_code_generator._option_names import (
    CONFIG_TABLE,
    compact_json,
    hyphenated,
    mapping_argument,
    option_flag,
    toml_table,
    toml_text,
)
from datamodel_code_generator._option_table import COMMAND_DESTS

if TYPE_CHECKING:
    from argparse import Namespace

    from datamodel_code_generator._option_names import TomlValue

_MAIN_MODULE = "datamodel_code_generator.__main__"


EXCLUDED_CONFIG_OPTIONS: frozenset[str] = COMMAND_DESTS | {
    "check",
    "diff_against",
    "debug",
    "disable_warnings",
    "list_deprecations",
    "list_experimental",
    "watch",
    "watch_delay",
}
"""The options ``--generate-pyproject-config`` leaves out: those of a command and the config fields of a run mode."""


def _pyproject_toml_value(key: str, value: object) -> TomlValue:
    from datamodel_code_generator.json_config import JsonConfigSpecs  # noqa: PLC0415

    if key not in JsonConfigSpecs.by_field_name or not isinstance(value, Mapping):
        return cast("TomlValue", value)
    return compact_json(value)


def _cli_config_values(args: Namespace) -> dict[str, TomlValue]:
    """Return the configuration values of CLI arguments, keyed by field name."""
    return {
        key: _pyproject_toml_value(key, value)
        for key, value in sorted(vars(args).items())
        if value is not None and key not in EXCLUDED_CONFIG_OPTIONS
    }


def _pyproject_config_data(args: Namespace) -> dict[str, TomlValue]:
    """Return pyproject.toml configuration data from CLI arguments."""
    return hyphenated(_cli_config_values(args))


def _format_pyproject_config(config_data: Mapping[str, TomlValue], table: tuple[str, ...] = CONFIG_TABLE) -> str:
    lines: list[str] = [toml_table(table)]
    for key, value in config_data.items():
        lines.append(f"{key} = {toml_text(value)}")
    return "\n".join(lines) + "\n"


def generate_pyproject_config(args: Namespace) -> str:
    """Generate pyproject.toml [tool.datamodel-codegen] section from CLI arguments."""
    return _format_pyproject_config(_pyproject_config_data(args))


def _format_cli_value(value: str | list[str]) -> str:
    """Format a value for CLI argument."""
    if isinstance(value, list):
        return " ".join(f'"{v}"' if " " in v else v for v in value)
    return f'"{value}"' if " " in value else value


def generate_cli_command(config: dict[str, TomlValue]) -> str:
    """Generate CLI command from pyproject.toml configuration."""
    from datamodel_code_generator.cli_options import _option_arguments  # noqa: PLC0415

    parts: list[str] = ["datamodel-codegen"]

    for key, value in sorted(config.items()):
        if key in EXCLUDED_CONFIG_OPTIONS:
            continue
        if isinstance(value, Mapping):
            if argument := mapping_argument(key, value):
                parts.extend((option_flag(key), argument))
            continue
        match _option_arguments(key, value):
            case [option, *items] if items and isinstance(value, list | tuple):
                parts.extend((option, _format_cli_value(items)))
            case [option, *items]:
                parts.extend((option, *(_format_cli_value(str(item)) for item in items)))

    return " ".join(parts)


generate_pyproject_config.__module__ = _MAIN_MODULE
generate_cli_command.__module__ = _MAIN_MODULE
