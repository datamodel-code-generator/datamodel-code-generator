"""Which module a written Python file holds: the one place that reads a module off the path of its file.

The output writes each module as a file: ``name.py``, or ``__init__.py`` for a package, below the directories of its
packages. The dotted name of the module is that path without the file's ``.py`` and without a package's ``__init__``
(``("pets", "__init__.py")`` holds ``pets``, ``("pets", "dog.py")`` holds ``pets.dog``); a module path without a file
name (``("pets", "dog")``) reads the same. The parser's module names, the ``--all-exports-scope`` exports, the
submodules a package binds, the model imports of the client and server targets and the modules the dynamic models run
in all read it here, so that they name the modules the output writes. This module imports only the standard library,
so every layer may use it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["dotted_module", "is_package_init", "module_parts"]


def is_package_init(file_name: str) -> bool:
    """Return whether the file named ``file_name`` is a package's ``__init__.py``."""
    return file_name == "__init__.py"


def module_parts(path: Sequence[str]) -> tuple[str, ...]:
    """Return the parts of the dotted name of the module at ``path``, without ``.py`` and a package's ``__init__``."""
    if not path:
        return ()
    *packages, file_name = path
    stem = file_name.removesuffix(".py")
    return tuple(packages) if stem == "__init__" else (*packages, stem)


def dotted_module(path: Sequence[str], package: str | None = None) -> str:
    """Return the dotted name of the module at ``path``, below ``package`` when one is given."""
    parts = module_parts(path)
    return ".".join(parts if package is None else (package, *parts))
