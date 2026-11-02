"""The paths of the written modules: where the output writes the file of a module, and which module a file holds.

The output writes each module as a file: ``name.py``, or ``__init__.py`` for a package, below the directories of its
packages. A ``-`` of a directory or file name is written as ``_`` (``written_name``). Without ``--treat-dot-as-module``
so are the dots of a name but its last one (``normalized_path``, the keys of the parser's result); with it, each
dotted name becomes packages (``package_path``), a file name keeping its extension (``expanded_path``).
``written_path`` is the path of the written file.

The dotted name of the module a file holds is that path without the file's ``.py`` and without a package's
``__init__`` (``("pets", "__init__.py")`` holds ``pets``, ``("pets", "dog.py")`` holds ``pets.dog``); a module path
without a file name (``("pets", "dog")``) reads the same. The parser's module files, result keys and module names, the
imported module names, the ``--all-exports-scope`` exports, the submodules a package binds, the packages of the import
cycles, the targets' result keys and model imports and the modules the dynamic models run in all read them here, so
that they name the modules the output writes.

A name in a module has a dotted name: the module's dotted name and the name, or the name alone without a module
(``dotted_name``). A model is named across modules by it, which the imports of other modules read, and an import, a
runtime symbol and a package beside a server package are named by it too. A module lies below a package when
the package's path starts its own and is shorter (``is_below``), so Python runs the package's ``__init__`` first; it
lies within the package when it is that package or below it (``is_within``). A dotted name lies below or within
another by their parts (``is_below_name``, ``is_within_name``), as a loaded module, an imported name or a helper name
does.

A module imports a name of another module by a relative import (``relative``): one dot for its own package and one
more for each package up to the packages both modules share, then the other module's path below them, split into the
module the import is from and its last name. A name in the ``__init__`` of a package the module lies below
(``is_ancestor_package_reference``) is imported from the dots alone. ``join_module`` appends a name to such a relative
module, and ``exact_import`` imports the name itself from the module ``relative`` imports. This module imports only
the standard library, so every layer may use it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "dotted_module",
    "dotted_name",
    "exact_import",
    "expanded_path",
    "is_ancestor_package_reference",
    "is_below",
    "is_below_name",
    "is_package_init",
    "is_within",
    "is_within_name",
    "join_module",
    "module_parts",
    "normalized_path",
    "package_path",
    "relative",
    "written_name",
    "written_names",
    "written_path",
]


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


def dotted_name(module: str | None, name: str) -> str:
    """Return the dotted name of ``name`` in the module ``module``: ``module.name``, or ``name`` without a module."""
    return f"{module}.{name}" if module else name


def is_below(module: tuple[str, ...], package: tuple[str, ...]) -> bool:
    """Return whether the module path ``module`` lies below the package ``package``, not being the package itself."""
    return len(module) > len(package) and module[: len(package)] == package


def is_within(module: tuple[str, ...], package: tuple[str, ...]) -> bool:
    """Return whether the module path ``module`` is the package ``package`` or lies below it."""
    return module[: len(package)] == package


def is_below_name(name: str, package: str) -> bool:
    """Return whether the dotted name ``name`` lies below the dotted name ``package``, by their parts (``is_below``)."""
    return is_below(tuple(name.split(".")), tuple(package.split(".")))


def is_within_name(name: str, package: str) -> bool:
    """Return whether the dotted name ``name`` is the dotted name ``package`` or lies below it (``is_within``)."""
    return is_within(tuple(name.split(".")), tuple(package.split(".")))


def relative(
    current_module: str,
    reference: str,
    *,
    reference_is_module: bool = False,
    current_is_init: bool = False,
) -> tuple[str, str]:
    """Return the module and name that ``current_module`` imports ``reference`` by, relative to it.

    ``reference`` is a ``module.Class`` path, or a module path with ``reference_is_module``; ``current_is_init`` makes
    ``current_module`` a package ``__init__``.
    """
    if current_is_init:
        current_module_path = [*current_module.split("."), "__init__"] if current_module else ["__init__"]
    else:
        current_module_path = current_module.split(".") if current_module else []

    if reference_is_module:
        reference_path = reference.split(".") if reference else []
        name = reference_path[-1] if reference_path else ""
    else:
        *reference_path, name = reference.split(".")

    if current_module_path == reference_path:
        return "", ""

    i = 0
    for x, y in zip(current_module_path, reference_path, strict=False):
        if x != y:
            break
        i += 1

    left = "." * (len(current_module_path) - i)
    right = ".".join(reference_path[i:])

    if not left:
        left = "."
    if not right:
        right = name
    elif "." in right:
        extra, right = right.rsplit(".", 1)
        left += extra

    return left, right


def is_ancestor_package_reference(current_module: str, reference: str) -> bool:
    """Check if reference is in an ancestor package (__init__.py).

    When the reference's module path is an ancestor (prefix) of the current module,
    the reference is in an ancestor package's __init__.py file.

    Args:
        current_module: The current module path (e.g., "v0.mammal.canine")
        reference: The full reference path (e.g., "v0.Animal")

    Returns:
        True if the reference is in an ancestor package, False otherwise.

    Examples:
        - current="v0.animal", ref="v0.Animal" -> True (immediate parent)
        - current="v0.mammal.canine", ref="v0.Animal" -> True (grandparent)
        - current="v0.animal", ref="v0.animal.Dog" -> False (same or child)
        - current="pets", ref="Animal" -> True (root package is immediate parent)
        - current="v0.mammal.canine", ref="Animal" -> True (root package is an ancestor)
    """
    *reference_path, _ = reference.split(".")
    return is_below(tuple(current_module.split(".")) if current_module else (), tuple(reference_path))


def exact_import(from_: str, import_: str, short_name: str) -> tuple[str, str]:
    """Create exact import path to avoid relative import issues."""
    if from_ == len(from_) * ".":
        # Prevents "from . import foo" becoming "from ..foo import Foo"
        # or "from .. import foo" becoming "from ...foo import Foo"
        # when our imported module has the same parent
        return f"{from_}{import_}", short_name
    return f"{from_}.{import_}", short_name


def join_module(from_: str, name: str) -> str:
    """Return the module ``name`` inside the module ``from_``, which may be only the dots of a relative import."""
    return f"{from_}{name}" if from_.endswith(".") else f"{from_}.{name}"


def written_name(name: str) -> str:
    """Return the name that the output writes a package directory or a module file named ``name`` under."""
    return name.replace("-", "_")


def written_names(path: Sequence[str]) -> tuple[str, ...]:
    """Return each name of ``path`` as the output writes it (``written_name``), its dots as they are."""
    return tuple(map(written_name, path))


def normalized_path(path: Sequence[str], *, treat_dot_as_module: bool | None) -> tuple[str, ...]:
    """Return the file path ``path`` as the parser's result keys it, before ``treat_dot_as_module`` expands it.

    Without ``treat_dot_as_module``, the dots of each name but its last one are written as ``_``.
    """
    names = written_names(path)
    if treat_dot_as_module:
        return names
    return tuple(name[: name.rfind(".")].replace(".", "_") + name[name.rfind(".") :] for name in names)


def package_path(module: Sequence[str]) -> tuple[str, ...]:
    """Return the module path ``module`` with each dotted name split into the packages ``treat_dot_as_module`` makes."""
    return tuple(part for name in module for part in name.split("."))


def expanded_path(path: Sequence[str]) -> tuple[str, ...]:
    """Return the file path ``path`` with each dotted name split into packages, its file name keeping the extension."""
    parts = package_path(path)
    return (*parts[:-2], f"{parts[-2]}.{parts[-1]}") if len(parts) > 1 else parts


def written_path(path: Sequence[str], *, treat_dot_as_module: bool | None) -> tuple[str, ...]:
    """Return the path that the output writes the file at ``path`` at: normalized, and expanded with the option."""
    normalized = normalized_path(path, treat_dot_as_module=treat_dot_as_module)
    return expanded_path(normalized) if treat_dot_as_module else normalized
