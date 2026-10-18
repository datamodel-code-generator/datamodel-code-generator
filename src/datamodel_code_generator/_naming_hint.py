"""Hint at ``--recommend-options`` after a command line generation whose class names are mostly numbered.

A command line invocation opens a scope with ``naming_hint_scope``. A generation run that may show the hint starts
collecting with ``collect_class_names``; its parse then keeps the final class names of its models, the generation
records those of the parse it keeps, and the command line warns at most once per invocation, also when a watch
regenerates the models. Outside a scope, as for ``generate()`` callers, and in runs that do not collect, such as the
candidate runs of ``--recommend-options``, nothing is counted. The module imports nothing from the package, so the
parser and the generation pipeline can import it.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence

__all__ = [
    "NamingHint",
    "collect_class_names",
    "collecting_class_names",
    "naming_hint_scope",
    "numbered_names",
    "record_class_names",
]

MIN_NUMBERED_NAMES: Final = 10
MIN_NUMBERED_SHARE: Final = 5


@dataclass(frozen=True, slots=True)
class NumberedNames:
    """The classes whose names are numbered collisions, and the largest family of such names as an example."""

    count: int
    example: str


def numbered_names(names: Sequence[tuple[str, str]]) -> NumberedNames | None:
    """Count the classes whose names are numbered collisions; None when there are none.

    ``names`` holds each class name with the name the model resolver wanted for it, which is empty unless the name
    was taken. A class name is a numbered collision when it is that wanted name with the number the resolver added,
    as ``Status1`` is when ``Status`` was taken, also by a model no class is generated for, such as a collapsed root
    model. Digits of the schema's own, such as ``Level1`` and ``Level2``, and the suffix of
    ``--duplicate-name-suffix`` never count. The example is the largest family, first and last by length.
    """
    families: dict[str, set[str]] = {}
    count = 0
    for name, wanted in names:
        if wanted and name != wanted and name.startswith(wanted) and name[len(wanted) :].isdecimal():
            count += 1
            families.setdefault(wanted, set()).add(name)
    if not count:
        return None
    _, members = min(families.items(), key=lambda family: (-len(family[1]), family[0]))
    ordered = sorted(members, key=lambda name: (len(name), name))
    return NumberedNames(count=count, example=ordered[0] if len(ordered) == 1 else f"{ordered[0]}...{ordered[-1]}")


@dataclass(slots=True)
class NamingHint:
    """The naming hint of one command line invocation: whether it was shown, and the class names collected last.

    ``names`` holds each class name with the name the model resolver wanted for it, as ``numbered_names`` takes them.
    """

    shown: bool = False
    collecting: bool = False
    names: tuple[tuple[str, str], ...] = ()

    def message(self) -> str | None:
        """Return the hint for the collected class names when they call for one, marking it shown.

        It needs at least ``MIN_NUMBERED_NAMES`` numbered names that make one in ``MIN_NUMBERED_SHARE`` classes or more.
        """
        if (
            (numbered := numbered_names(self.names)) is None
            or numbered.count < MIN_NUMBERED_NAMES
            or numbered.count * MIN_NUMBERED_SHARE < len(self.names)
        ):
            return None
        self.shown = True
        return (
            f"{numbered.count} of {len(self.names)} class names are numbered collisions ({numbered.example}). "
            "Add --recommend-options to this command to see naming options for this schema (experimental)."
        )


_NAMING_HINT: ContextVar[NamingHint | None] = ContextVar("naming_hint", default=None)


@contextmanager
def naming_hint_scope() -> Generator[None]:
    """Show the naming hint at most once within one command line invocation, including its watch regenerations."""
    token = _NAMING_HINT.set(NamingHint())
    try:
        yield
    finally:
        _NAMING_HINT.reset(token)


def collect_class_names(*, wanted: bool) -> NamingHint | None:
    """Collect the class names of the next generation run of the invocation when ``wanted``, or stop collecting.

    Returns the invocation's hint while it collects: never outside an invocation, or once the hint was shown.
    """
    if (hint := _NAMING_HINT.get()) is None:
        return None
    hint.collecting = wanted and not hint.shown
    hint.names = ()
    return hint if hint.collecting else None


def collecting_class_names() -> bool:
    """Return whether the running parse keeps the final class names of its models for the hint."""
    return (hint := _NAMING_HINT.get()) is not None and hint.collecting


def record_class_names(names: tuple[tuple[str, str], ...]) -> None:
    """Record the class names of the parse a generation keeps, while the invocation collects them."""
    if (hint := _NAMING_HINT.get()) is not None and hint.collecting:
        hint.names = names
