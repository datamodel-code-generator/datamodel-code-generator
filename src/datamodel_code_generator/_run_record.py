"""Where the option values of a run came from, free of pydantic.

``RunOptions`` is the one record every question "was this option given, and by whom" reads. It is built once per
entry of a run from the sources that entry has, before any value is validated: the command line from its arguments
and the ``pyproject.toml`` values (base table, profile and job merged), ``load_pyproject_config()`` from the keys it
keeps and its overrides, ``generate()`` from the fields its config has set, with their values. The option stages read
it and return it with the fields each of them gave a value (``_option_stages.py:_run_option_stages``).

The record is history: a later write to the config does not change it, and a copy of a config shares it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True, eq=False)
class RunOptions:
    """The sources of the option values of one run, and the fields each option stage gave a value."""

    pyproject: Mapping[str, Any] = field(default_factory=dict)
    """The ``[tool.datamodel-codegen]`` values of the run by field name, with its profile and job merged."""
    arguments: Mapping[str, Any] = field(default_factory=dict)
    """The values the caller gave over them, by field name: the command-line arguments, or the overrides of
    ``load_pyproject_config()``."""
    fields_set: Mapping[str, Any] = field(default_factory=dict)
    """The fields a config without those sources had set when its run started, each with the value it had
    (``generate()``; row ``given-is-fields-set``: a validator, a loader and an earlier copy count as the caller), or
    None for the fields of a config ``load_pyproject_config()`` returned, whose values do not say who gave them."""
    staged: Mapping[str, str] = field(default_factory=dict)
    """Each field an option stage gave a value, with the stage that gave it last, in the order they were first given."""

    def __deepcopy__(self, memo: dict[int, Any]) -> RunOptions:
        """Return the record itself: the deep copy of a config shares the history of its source."""
        return self

    @property
    def given(self) -> frozenset[str]:
        """The fields a source of the run gave: never one that only a preset or an implied setting set."""
        return frozenset(self.fields_set).union(self.pyproject, self.arguments)

    @property
    def preset_explicit(self) -> frozenset[str]:
        """The given fields a preset leaves alone (row ``preset-over-pyproject``).

        A preset that an argument selects replaces the values of pyproject.toml, so only the arguments are explicit
        to it.
        """
        return (frozenset(self.arguments) if "preset" in self.arguments else self.given) - {"preset"}

    @property
    def set_fields(self) -> frozenset[str]:
        """The fields that have a value from a source or from an option stage: what a later stage leaves alone."""
        return self.given.union(self.staged)

    @property
    def merged(self) -> dict[str, Any]:
        """The values as given, the arguments over the pyproject.toml values: what ``--watch`` reads paths from."""
        return {**self.pyproject, **self.arguments}

    def given_value(self, name: str) -> Any:
        """Return the value a field was given; None for a field not given.

        An argument's value comes before the pyproject.toml one; a config without those sources gives the value the
        field had when its run started.
        """
        return self.arguments.get(name, self.pyproject.get(name, self.fields_set.get(name)))

    def staged_by(self, stage: str) -> tuple[str, ...]:
        """Return the fields whose value an option stage gave, in the order it gave them."""
        return tuple(name for name, giver in self.staged.items() if giver == stage)

    def after(self, stage: str, values: Mapping[str, Any]) -> RunOptions:
        """Return the record of the run after an option stage gave these values; the same record if it gave none."""
        return replace(self, staged={**self.staged, **dict.fromkeys(values, stage)}) if values else self


__all__ = ["RunOptions"]
