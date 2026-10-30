"""How a name that its scope already holds is told apart: the one place that numbers names and finds the first free one.

Every name the generator makes that must differ from the names around it (a class, a field, a module, an import
alias, a validator function, a helper class, a bundled component, a route slot) takes the first candidate of its
feature's sequence that its scope does not hold. Each sequence is released output, so it is a row (``Numbering``):

- ``underscore``: ``name``, ``name_1``, ``name_2``: duplicate fields, names of target scopes and target module
  imports, ``_internal`` modules, merged class names, validator functions, import aliases;
- ``joined``: ``Name``, ``Name1``, ``Name2``: duplicate classes of the models and of target scopes;
- ``joined-from-2``: ``Name``, ``Name2``, ``Name3``: converted definitions (Avro, XML Schema, MCP), schema validator
  helper classes;
- ``underscore-from-2``: ``name``, ``name_2``, ``name_3``: input model definitions, components the served document
  bundles;
- ``joined-from-0``: ``name0``, ``name1`` after ``name``: route placeholder slots;
- ``padded``: ``name``, ``name_``, ``name__``: import aliases for a field collision, the sentinel of the starred
  expression parse.

A sequence that never takes the name itself starts at position 1. With the models' duplicate name ``suffix``, the
``underscore`` and ``joined`` rows number ``name``, ``name<d>Suffix``, ``name<d>Suffix<d>1`` instead. The test of
what a scope holds, and any hint that starts a search past candidates known to be taken, stay with the caller. This
module imports only the standard library, so every layer may use it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["Numbering", "candidate_at", "first_free", "is_numbered"]

Numbering: TypeAlias = Literal["underscore", "joined", "joined-from-2", "underscore-from-2", "joined-from-0", "padded"]
"""How one feature numbers a name its scope already holds (see the module docstring)."""
_ROWS: Final[dict[str, tuple[str, int]]] = {
    "underscore": ("_", 1),
    "joined": ("", 1),
    "joined-from-2": ("", 2),
    "underscore-from-2": ("_", 2),
    "joined-from-0": ("", 0),
}
"""Each numbering row but ``padded``: the text between the name and its number, and the first number."""


def candidate_at(base: str, numbering: Numbering, position: int, suffix: str | None = None) -> str:
    """Return the candidate at ``position`` of the sequence that ``numbering`` writes for ``base`` (0 is ``base``)."""
    if numbering == "padded":
        return base + "_" * position
    if not position:
        return base
    delimiter, first = _ROWS[numbering]
    if suffix:
        return f"{base}{delimiter}{suffix}{delimiter}{position - 1}" if position > 1 else f"{base}{delimiter}{suffix}"
    return f"{base}{delimiter}{first + position - 1}"


def first_free(
    base: str, taken: Callable[[str], bool], numbering: Numbering, *, suffix: str | None = None, start: int = 0
) -> tuple[str, int]:
    """Return the first candidate from position ``start`` on that ``taken`` does not hold, and its position."""
    position = start
    while taken(name := candidate_at(base, numbering, position, suffix)):
        position += 1
    return name, position


def is_numbered(name: str, base: str, numbering: Numbering, suffix: str | None = None) -> bool:
    """Return whether ``name`` is ``base`` or ``base`` with a number as ``numbering`` writes one, of any decimal digits.

    Digits are read as written (``Name01`` counts), as every reader of a sequence has done. Rows ``underscore`` and
    ``joined`` only.
    """
    if name == base:
        return True
    delimiter = _ROWS[numbering][0]
    if suffix:
        if name == (first := f"{base}{delimiter}{suffix}"):
            return True
        base = first
    return name.startswith(prefix := f"{base}{delimiter}") and name[len(prefix) :].isdecimal()
