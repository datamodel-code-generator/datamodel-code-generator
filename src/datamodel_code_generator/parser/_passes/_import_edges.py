"""The import edges of the modules: which modules a module imports when Python runs it, and the cycles that fail.

A written module imports the module that each of its relative import blocks names, and the submodule of each name the
block imports where that module has one (``relative_import_targets``); ``module_import_edges`` reads them for every
written module from its final import plan. A module that writes a package's ``__init__.py`` resolves its relative
imports from that package (``_written_package``).

Before the imports are planned, the module structure (``parser/_passes/structure.py``) reads which modules import each
other from the references between their models (``module_dependency_graph``) and merges the modules of each cycle into
an ``_internal`` module. An import cycle through an ``_internal`` module that fails once the merges move there
(``_failing_import_cycles``) joins its merge: ``_failing_cycles`` finds the cycles that fail when one of their modules
is imported first, and under ``--all-exports-scope`` the ``__init__`` module of a package imports the modules below it
that it re-exports (``_exported_imports``).
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from datamodel_code_generator._model_keys import model_key_of
from datamodel_code_generator._module_paths import (
    absolute,
    dotted_module,
    dotted_name,
    is_below,
    is_package_init,
    package_path,
    written_path,
)
from datamodel_code_generator._reference_graph import EdgeKind, GraphSnapshot, GroupGraph
from datamodel_code_generator.imports import _stored_import
from datamodel_code_generator.parser._graph_ids import keyed_models
from datamodel_code_generator.parser._init_exports import exports_reach
from datamodel_code_generator.parser._passes._shared import _parsed_rule_data_types

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator.enums import AllExportsScope
    from datamodel_code_generator.imports import Imports
    from datamodel_code_generator.model.base import DataModel
    from datamodel_code_generator.parser._passes._state import ModuleContextView, ModulePath
    from datamodel_code_generator.parser.generation import GenerationIndex

__all__ = ["module_dependency_graph", "module_import_edges", "relative_import_targets"]


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


def module_dependency_graph(
    index: GenerationIndex, module_models_list: list[tuple[tuple[str, ...], list[DataModel]]]
) -> GroupGraph[tuple[str, ...]]:
    """Return the modules that each module of ``module_models_list`` imports, from the snapshot of the references.

    A module imports the modules of the models that its models reference (``index``). The models that the schema
    validator rules of a model name count as its field types do: its module imports them for the class body that
    evaluates the rules, an alias written as a class for its rules included.
    """
    snapshot = GraphSnapshot.MODULE_DEPENDENCIES
    keyed = keyed_models((model for _, models in module_models_list for model in models), snapshot.value)
    module_of: dict[ModelKey, tuple[str, ...]] = {}
    for module, models in module_models_list:
        for model in models:
            module_of[model_key_of(model.reference)] = module
    rule_references = {
        key: keyed.keys_of(
            reference for data_type in _parsed_rule_data_types(model) if (reference := data_type.reference) is not None
        )
        for key, model in keyed.models.items()
    }
    graph = index.reference_graph(snapshot, keyed, edges={EdgeKind.RULE_TYPE: rule_references})
    return graph.quotient(module_of, (EdgeKind.REFERENCE, EdgeKind.RULE_TYPE))


def _failing_cycles(
    nodes: set[tuple[tuple[str, ...], int]],
    imports: dict[tuple[tuple[str, ...], int], set[tuple[tuple[str, ...], int]]],
    packages: dict[tuple[tuple[str, ...], int], set[tuple[tuple[str, ...], int]]],
    entered: frozenset[tuple[tuple[str, ...], int]] = frozenset(),
) -> Iterator[set[tuple[tuple[str, ...], int]]]:
    """Yield the import cycles among ``nodes`` that fail when one of their modules is imported first.

    A cycle of a package and modules below it runs the package first, whichever of them is imported first, so it
    imports unless a module of it imports from the package: the cycles left without running the packages of
    ``entered`` are checked again. Any other cycle finds one of its modules half built.
    """
    graph = GroupGraph({node: (imports[node] | (packages[node] - entered)) & nodes for node in nodes})
    for component in graph.components():
        if len(component) == 1:
            continue
        top = min(component, key=lambda node: len(node[0]))
        if top[1] < 0 and top not in entered and all(node == top or is_below(node[0], top[0]) for node in component):
            yield from _failing_cycles(component, imports, packages, entered | {top})
        else:
            yield component


def _exported_imports(
    exporters: Iterable[tuple[tuple[str, ...], int]], scope: AllExportsScope
) -> defaultdict[tuple[str, ...], set[tuple[tuple[str, ...], int]]]:
    """Return the exporters that the ``__init__`` module of each package imports to re-export their names.

    A package re-exports the modules below it that its scope reaches (``exports_reach``).
    """
    imported: defaultdict[tuple[str, ...], set[tuple[tuple[str, ...], int]]] = defaultdict(set)
    for node in exporters:
        path = node[0]
        for size in range(len(path)):
            if exports_reach(len(path) - size, scope):
                imported[path[:size]].add(node)
    return imported


def _failing_import_cycles(  # noqa: PLR0913
    merges: list[set[tuple[str, ...]]],
    internal_modules: list[tuple[str, ...]],
    module_graph: GroupGraph[tuple[str, ...]],
    exporting: set[tuple[str, ...]],
    all_exports_scope: AllExportsScope | None,
    *,
    dotted: bool,
) -> list[set[tuple[tuple[str, ...], int]]]:
    """Return the import cycles through an ``_internal`` module that fail once each merge moves into its own.

    A node is a module path with ``-1``, or the path of the ``_internal`` module of a merge (``internal_modules``, in
    the order of ``merges``) with the index of the merge. A merged module imports its ``_internal`` module, which
    imports what the merged modules import; a module that uses a merged model imports the ``_internal`` module.
    Importing a module runs the ``__init__`` module of each package above it first. With ``all_exports_scope``, the
    ``__init__`` module of a package that is no merged module imports the modules below it that ``exporting`` names,
    and the ``_internal`` modules, to re-export their names; ``dotted`` places a module in the packages that the dots
    of its name make.
    """
    place = package_path if dotted else lambda module: module
    internal_nodes = [(place(module), index) for index, module in enumerate(internal_modules)]
    forwarded = {module: internal_nodes[index] for index, merge in enumerate(merges) for module in merge}

    def node_of(module: tuple[str, ...]) -> tuple[tuple[str, ...], int]:
        return forwarded.get(module, (place(module), -1))

    imports = {
        (place(module), -1): {forwarded[module]} if module in forwarded else set(map(node_of, targets))
        for module, targets in module_graph.edges.items()
    }
    for node, merge in zip(internal_nodes, merges, strict=True):
        imports[node] = set(map(node_of, {target for module in merge for target in module_graph.edges[module]} - merge))
    exports: dict[tuple[tuple[str, ...], int], set[tuple[tuple[str, ...], int]]] = {}
    if all_exports_scope is not None:
        exporters = [(place(module), -1) for module in exporting if module not in forwarded]
        merged_paths = set(map(place, forwarded))
        exports = {
            (package, -1): targets
            for package, targets in _exported_imports([*exporters, *internal_nodes], all_exports_scope).items()
            if package not in merged_paths
        }
    edges = {node: imports.get(node, set()) | exports.get(node, set()) for node in imports.keys() | exports.keys()}
    packages = {
        (path, index): {(path[:size], -1) for size in range(len(path)) if (path[:size], -1) in edges} - targets
        for (path, index), targets in edges.items()
    }
    module_at = {place(module): module for module in module_graph.edges}
    exported = set().union(*exports.values())

    def moves(node: tuple[tuple[str, ...], int], cycle: set[tuple[tuple[str, ...], int]]) -> bool:
        """Return whether the module of ``node`` joins a merge.

        A package without a module of ``module_graph`` has an empty ``__init__`` module that stays. A module that a
        package imports to re-export it, and a package that does so, join only if the cycle holds an import of
        theirs: the package runs first and imports the module, whichever of them is imported first.
        """
        path, index = node
        if index >= 0:
            return True
        if path not in module_at:
            return False
        if node not in exports and node not in exported:
            return True
        return bool(imports[node] & cycle) or any(node in imports.get(held, ()) for held in cycle)

    return [
        {(module_at[path] if index < 0 else path, index) for path, index in cycle if moves((path, index), cycle)}
        for cycle in _failing_cycles(set(edges), edges, packages)
        if any(index >= 0 for _, index in cycle)
    ]
