"""Spell and parse config values as command-line options and pyproject.toml entries, without importing pydantic.

Only ``json_ready`` imports pydantic, when it is called.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

TomlValue: TypeAlias = str | bool | int | float | list["TomlValue"] | tuple["TomlValue", ...]
TomlKeyRow: TypeAlias = Literal["unicode", "legacy-ascii-table-keys"]
_TOML_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
CONFIG_TABLE: Final = ("tool", "datamodel-codegen")
FIELD_ALIASES: Final = {"capitalize_enum_members": "capitalise_enum_members"}
SENSITIVE_FIELDS: Final = frozenset({"http_headers", "http_query_parameters"})


def option_flag(field: str, *, negative: bool = False) -> str:
    """Return the option of a config field, or its --no- form."""
    return f"--{'no-' if negative else ''}{field.replace('_', '-')}"


def option_flags(field: str) -> tuple[str, ...]:
    """Return the options of a config field: its own, then those of the aliases ``FIELD_ALIASES`` gives it."""
    return (option_flag(field), *(option_flag(alias) for alias, name in FIELD_ALIASES.items() if name == field))


def field_name(key: str) -> str:
    """Return the config field a hyphenated pyproject.toml key names, the reverse of ``hyphenated``."""
    return key.replace("-", "_")


def option_arguments(field: str, value: object, *, negative: Callable[[str], str | None]) -> list[Any] | None:
    """Return the arguments that set a config field to a value, or None when no option can express it.

    True is the field's option and False the --no- option ``negative`` finds for the field (None when the parser has
    none); a list or tuple is the option followed by its items, any other value the option followed by the value.
    Items and values are returned as given, so that each writer spells them: ``str``, its quoting, an enum's value.
    """
    if isinstance(value, bool):
        return [option_flag(field)] if value else None if (flag := negative(field)) is None else [flag]
    return [option_flag(field), *(value if isinstance(value, list | tuple) else (value,))]


def values_arguments(values: Iterable[tuple[str, object]], *, negative: Callable[[str], str | None]) -> list[Any]:
    """Return the arguments that set config fields to values, field by field in the given order.

    Each field's arguments are those of ``option_arguments``; a value that no option can express is left out.
    """
    return [argument for field, value in values for argument in option_arguments(field, value, negative=negative) or ()]


def shell_command(arguments: Iterable[str]) -> str:
    """Return the shell command that runs datamodel-codegen with the arguments, each quoted as the shell needs."""
    import shlex  # noqa: PLC0415

    return shlex.join(("datamodel-codegen", *arguments))


def hyphenated(config: Mapping[str, Any], *, ordered: bool = False) -> dict[str, Any]:
    """Return config values keyed by their hyphenated names, sorted by field name when ordered."""
    return {key.replace("_", "-"): value for key, value in (sorted(config.items()) if ordered else config.items())}


def toml_value(value: object) -> Any:
    """Return a config value as pyproject.toml spells it, for comparing rule values with a configuration."""
    if isinstance(value, Enum):
        return str(value.value)
    return [toml_value(item) for item in value] if isinstance(value, tuple | list) else value


def json_ready(value: Any) -> Any:
    """Return a config value as JSON holds it: an enum's value, a path in POSIX form, a model's JSON dump, lists."""
    from pydantic import BaseModel  # noqa: PLC0415

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, tuple | list):
        return [json_ready(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): json_ready(item) for key, item in value.items()}
    return value


def toml_text(value: TomlValue) -> str:
    """Format a Python value as a TOML value string."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int | float):
        return str(value)
    formatted_items = [toml_text(item) for item in value]
    return f"[{', '.join(formatted_items)}]"


def toml_key(key: str, row: TomlKeyRow = "unicode") -> str:
    """Return a TOML key: bare when it uses only ASCII letters, digits, hyphens, and underscores, otherwise quoted.

    Row ``legacy-ascii-table-keys`` (the notice of the settings a target implied) escapes the non-ASCII characters of
    a quoted key, which ``unicode`` writes as they are.
    """
    return key if _TOML_BARE_KEY.fullmatch(key) else json.dumps(key, ensure_ascii=row == "legacy-ascii-table-keys")


def toml_table(table: Iterable[str], row: TomlKeyRow = "unicode") -> str:
    """Return the header of a TOML table: its keys, each as ``toml_key`` writes it, joined by dots in brackets."""
    return f"[{'.'.join(toml_key(key, row) for key in table)}]"


def config_table(*named: str) -> tuple[str, ...]:
    """Return the keys of the pyproject.toml table of the settings, or of the job or profile ``named`` below it."""
    return (*CONFIG_TABLE, *named)


def configured_value(config: object, field: str) -> Any:
    """Return the value a config holds for a field as pyproject.toml spells it, the spelling rule values compare in."""
    return toml_value(getattr(config, field))


def split_external_ref_mapping(item: object) -> tuple[str, str]:
    """Split a FILE_PATH=PYTHON_PACKAGE mapping, raising ValueError with the message --external-ref-mapping reports."""
    if not isinstance(item, str) or "=" not in item:
        msg = (
            f"Invalid --external-ref-mapping format: {item!r}. "
            "Expected FILE_PATH=PYTHON_PACKAGE (e.g., '../common/schema.yaml=mypackage.models')"
        )
        raise ValueError(msg)
    file_path, python_package = (part.strip() for part in item.rsplit("=", maxsplit=1))
    if not file_path or not python_package:
        msg = f"Invalid --external-ref-mapping format: {item!r}. Both FILE_PATH and PYTHON_PACKAGE must be non-empty."
        raise ValueError(msg)
    return file_path, python_package
