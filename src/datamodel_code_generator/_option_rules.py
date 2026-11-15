"""Options whose value implies the value of another option, free of pydantic.

Every stage that applies option values applies what they imply: the command line to the arguments it merges (merging
copies only the given fields), the config models when they validate, ``generate()`` and the command line to the
settings a config leaves unset, and a preset to the updates it makes; the CLI reference lists the same relations.
The settings stage and the preset read every source option of the table. Each config validator applies one row:
``BaseGenerateConfig`` the ``use_type_alias_type`` row (a frozen ``PresetConfig`` sets the other source options) and
the command line's ``Config`` the ``use_annotated`` row (row ``validator-stage-rows``). ``get_data_model_types`` reads
the ``use_type_alias_type`` row through ``implied_options`` for direct callers of the public function.
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


def implied_option_sources(config: object) -> dict[str, object]:
    """Return the value of every source option of ``IMPLIED_OPTIONS`` on ``config``, in the order of the table."""
    return {option: getattr(config, option) for option in IMPLIED_OPTIONS}


def enable_implied_options(config: object, *options: str) -> None:
    """Turn on, on ``config``, the options that its enabled ``options`` imply."""
    for name, value in implied_options({option: getattr(config, option) for option in options}).items():
        setattr(config, name, value)


__all__ = ["IMPLIED_OPTIONS", "enable_implied_options", "implied_option_sources", "implied_options"]
