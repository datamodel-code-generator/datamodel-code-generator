"""Options whose value implies the value of another option, free of pydantic.

Every stage that applies option values applies what they imply: the command line to the arguments it merges (merging
copies only the given fields), the config models when they validate, ``generate()`` and the command line to the
settings a config leaves unset, and a preset to the updates it makes; the CLI reference lists the same relations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

IMPLIED_OPTIONS: Final[dict[str, str]] = {
    "use_annotated": "field_constraints",
    "use_type_alias_type": "use_type_alias",
    "intersect_combined_schema_keywords": "intersect_allof_keywords",
}
"""The option each option turns on when it is enabled, over any value given for that option."""


def implied_options(values: Mapping[str, object]) -> dict[str, bool]:
    """Return the options that the enabled options of ``values`` turn on, each True, in the order of the table."""
    return {implied: True for option, implied in IMPLIED_OPTIONS.items() if values.get(option)}


def enable_implied_options(config: object, *options: str) -> None:
    """Turn on, on ``config``, the options that its enabled ``options`` imply."""
    for name, value in implied_options({option: getattr(config, option) for option in options}).items():
        setattr(config, name, value)


__all__ = ["IMPLIED_OPTIONS", "enable_implied_options", "implied_options"]
