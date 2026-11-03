"""The import edges of the written modules: which modules a module imports when Python runs it.

A written module imports the module that each of its relative import blocks names, and the submodule of each name the
block imports where that module has one (``relative_import_targets``); ``module_import_edges`` reads them for every
written module from its final import plan. A module that writes a package's ``__init__.py`` resolves its relative
imports from that package (``_written_package``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from datamodel_code_generator._module_paths import absolute, dotted_module, dotted_name, is_package_init, written_path
from datamodel_code_generator.imports import _stored_import

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from datamodel_code_generator.imports import Imports
    from datamodel_code_generator.parser._passes._state import ModuleContextView, ModulePath

__all__ = ["module_import_edges", "relative_import_targets"]


def _written_package(ctx: ModuleContextView, *, treat_dot_as_module: bool | None) -> ModulePath | None:
    """Return the package whose ``__init__.py`` the module writes, by its written path, or None for another module.

    The module writes it whatever the input that holds it was named: ``ctx.is_init`` is the module's place in the
    import paths, which the root package and the models of an input ``__init__.json`` do not take.
    """
    path = written_path(ctx.module, treat_dot_as_module=treat_dot_as_module)
    return path[:-1] if is_package_init(path[-1]) else None


def relative_import_targets(importer: str, imports: Imports, *, is_init: bool) -> Iterator[tuple[str, str, list[str]]]:
    """Yield each relative import block of the module ``importer`` in order, with the module it names.

    A block is its module text (``..pkg``), the dotted name of the module that text names (``absolute``) and the names
    it imports, sorted; a name reaches the submodule of that name where the module has one. ``is_init`` makes
    ``importer`` a package ``__init__``.
    """
    for from_ in sorted(name for name in imports if name and name.startswith(".")):
        names = sorted(_stored_import(name)[0] for name in imports[from_])
        yield from_, absolute(importer, from_, current_is_init=is_init), names


def module_import_edges(
    contexts: Iterable[ModuleContextView], *, treat_dot_as_module: bool | None
) -> dict[str, set[str]]:
    """Return the dotted names of the modules that each written module imports or imports from, by its dotted name.

    Each relative import block reaches the module it names and the dotted name of each name it imports, which is the
    submodule of that name where the module has one (``relative_import_targets``).
    """
    edges: dict[str, set[str]] = {}
    for ctx in contexts:
        importer = dotted_module(written_path(ctx.module, treat_dot_as_module=treat_dot_as_module))
        is_init = _written_package(ctx, treat_dot_as_module=treat_dot_as_module) is not None
        targets = edges.setdefault(importer, set())
        for _, target, names in relative_import_targets(importer, ctx.imports, is_init=is_init):
            targets.add(target)
            targets.update(dotted_name(target, name) for name in names)
    return edges
