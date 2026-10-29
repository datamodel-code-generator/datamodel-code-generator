"""Spell and parse config values as command-line options and pyproject.toml entries, without importing pydantic."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


def option_flag(field: str, *, negative: bool = False) -> str:
    """Return the option of a config field, or its --no- form."""
    return f"--{'no-' if negative else ''}{field.replace('_', '-')}"


def option_arguments(field: str, value: object, *, negative: Callable[[str], str | None]) -> list[Any] | None:
    """Return the arguments that set a config field to a value, or None when no option can express it.

    True is the field's option and False the --no- option ``negative`` finds for the field (None when the parser has
    none); a list or tuple is the option followed by its items, any other value the option followed by the value.
    Items and values are returned as given, so that each writer spells them: ``str``, its quoting, an enum's value.
    """
    if isinstance(value, bool):
        return [option_flag(field)] if value else None if (flag := negative(field)) is None else [flag]
    return [option_flag(field), *(value if isinstance(value, list | tuple) else (value,))]


def hyphenated(config: Mapping[str, Any], *, ordered: bool = False) -> dict[str, Any]:
    """Return config values keyed by their hyphenated names, sorted by field name when ordered."""
    return {key.replace("_", "-"): value for key, value in (sorted(config.items()) if ordered else config.items())}


def toml_value(value: object) -> Any:
    """Return a config value as pyproject.toml spells it, for comparing rule values with a configuration."""
    if isinstance(value, Enum):
        return str(value.value)
    return [toml_value(item) for item in value] if isinstance(value, tuple | list) else value


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
