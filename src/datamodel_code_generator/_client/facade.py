"""Render the facade modules of a client target: modules that only import, re-export, and load names on first use."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from datamodel_code_generator._client._compiled_templates import facade as facade_template

if TYPE_CHECKING:
    from collections.abc import Iterable

    from datamodel_code_generator._target_templates import Role


@dataclass(frozen=True, slots=True)
class Lazy:
    """Names a facade loads from a module of the package when one is first requested.

    `names` is the set literal of the names, `attribute` the module imported from `module`, and a cached name is kept
    as a module attribute once loaded.
    """

    names: str
    module: str
    attribute: str
    cache: bool = False


@dataclass(frozen=True, slots=True)
class Attribute:
    """One class attribute: its name and value expression."""

    name: str
    value: str


@dataclass(frozen=True, slots=True)
class Subclass:
    """A class a facade defines: its name, base, docstring, and class attributes."""

    name: str
    base: str
    docstring: str
    attributes: tuple[Attribute, ...]


def statement(module: str, names: Iterable[str]) -> str:
    """Return one import statement of names from a module."""
    return f"from {module} import {', '.join(names)}"


def name_set(values: Iterable[str]) -> str:
    """Return the set literal of names, in the order given."""
    return f"{{{', '.join(map(repr, values))}}}"


def render_facade(  # noqa: PLR0913
    role: Role,
    module: str,
    docstring: str,
    *,
    future: bool = False,
    imports: Iterable[str] = (),
    checking: Iterable[str] = (),
    classes: tuple[Subclass, ...] = (),
    exports: Iterable[str] = (),
    lazy: tuple[Lazy, ...] = (),
    loader: str = "",
    listing: str = "",
) -> str:
    """Render one facade module through the `facade.jinja2` role.

    `module` is its dotted path in the package, empty for the package itself; `imports` are its import statements and
    `checking` those it makes only for type checking. `loader` documents the `__getattr__` that loads the lazy names,
    and `listing` the `__dir__` that lists them.
    """
    return role("facade.jinja2", facade_template.render)(
        module=module,
        docstring=docstring,
        future=future,
        imports="\n".join(imports),
        checking=tuple(checking),
        classes=classes,
        exports=tuple(exports),
        lazy=lazy,
        loader=loader,
        listing=listing,
    )
