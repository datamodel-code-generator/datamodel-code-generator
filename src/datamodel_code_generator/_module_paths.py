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

A model is named across modules by its dotted name: its module's dotted name and its class name, or the class name
alone in the root module (``dotted_name``), which the imports of other modules read. A module lies below a package when
the package's path starts its own and is shorter (``is_below``), so Python runs the package's ``__init__`` first; it
lies within the package when it is that package or below it (``is_within``). This module imports only the standard
library, so every layer may use it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "dotted_module",
    "dotted_name",
    "expanded_path",
    "is_below",
    "is_package_init",
    "is_within",
    "module_parts",
    "normalized_path",
    "package_path",
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
    """Return the dotted name of ``name`` in the module ``module``: ``module.name``, or ``name`` in the root module."""
    return f"{module}.{name}" if module else name


def is_below(module: tuple[str, ...], package: tuple[str, ...]) -> bool:
    """Return whether the module path ``module`` lies below the package ``package``, not being the package itself."""
    return len(module) > len(package) and module[: len(package)] == package


def is_within(module: tuple[str, ...], package: tuple[str, ...]) -> bool:
    """Return whether the module path ``module`` is the package ``package`` or lies below it."""
    return module[: len(package)] == package


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
