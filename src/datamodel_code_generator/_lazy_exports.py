"""Serve the lazy public names of a module from one table that its ``__getattr__`` and ``__dir__`` both read (PEP 562).

A table maps each lazy name to where its value lives: a module path (the attribute of the same name there), a pair
``(module path, attribute)``, or a function called without arguments that returns the value.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from importlib import import_module
from typing import Any, Literal, TypeAlias

LazyEntry: TypeAlias = str | tuple[str, str] | Callable[[], object]
MissingMessage: TypeAlias = Literal["standard", "name"]


def _load(name: str, entry: LazyEntry) -> object:
    """Return the value that ``entry`` names for the lazy name ``name``."""
    if isinstance(entry, str):
        return getattr(import_module(entry), name)
    if isinstance(entry, tuple):
        return getattr(import_module(entry[0]), entry[1])
    return entry()


def module_hooks(
    module_name: str,
    table: Mapping[str, LazyEntry],
    *,
    cache: bool = False,
    missing_message: MissingMessage = "standard",
) -> tuple[Callable[[str], Any], Callable[[], list[str]]]:
    """Return the ``__getattr__`` and the ``__dir__`` of module ``module_name`` for the lazy names of ``table``.

    ``__getattr__`` serves exactly the keys of ``table``, and with ``cache`` stores each value in the module the first
    time it is read. Any other name raises ``AttributeError``: with the message ``module 'x' has no attribute 'y'``
    (``"standard"``) or with the name alone (``"name"``). ``__dir__`` lists the names of the module and the keys of
    ``table``, each once, whichever of them were read before. Both hooks keep the module object that is imported when
    they are made, so a module object no longer in ``sys.modules`` still lists and caches its own names; hooks made
    for a module not imported yet look it up when they are called.
    """
    made_for = sys.modules.get(module_name)

    def module_getattr(name: str) -> Any:
        if name not in table:
            msg = f"module {module_name!r} has no attribute {name!r}" if missing_message == "standard" else name
            raise AttributeError(msg)
        value = _load(name, table[name])
        if cache:
            setattr(made_for or sys.modules[module_name], name, value)
        return value

    def module_dir() -> list[str]:
        return sorted({*vars(made_for or sys.modules[module_name]), *table})

    return module_getattr, module_dir
