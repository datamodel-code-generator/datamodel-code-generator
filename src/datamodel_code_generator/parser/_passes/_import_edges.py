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

The modules whose models take the generic base class import it from the module that holds it
(``generic_base_class_module``).

Once every module is rendered, the written import blocks of each module in the order the formatters write them
(``written_import_blocks``) and the names its definitions read of other modules as it runs make an ``ImportPlan``.
``failing_import_reads`` runs the import of each module as Python does and finds a name read of a module that is still
running, which fails; ``import_cycle_seeds`` turns such failures into what a parse again lays out otherwise: the
generic base class in a module that runs none of the modules that import it, else the modules of the import cycle
merged into an ``_internal`` module.
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from typing import TYPE_CHECKING, Final, TypeAlias

from datamodel_code_generator._graph import reach, reaching
from datamodel_code_generator._model_keys import model_key_of
from datamodel_code_generator._module_paths import (
    absolute,
    dotted_module,
    dotted_name,
    is_below,
    is_package_init,
    module_parts,
    package_path,
    written_path,
)
from datamodel_code_generator._records import record
from datamodel_code_generator._reference_graph import EdgeKind, GraphSnapshot, GroupGraph
from datamodel_code_generator._scc import find_circular_sccs, strongly_connected_components
from datamodel_code_generator.imports import _stored_import
from datamodel_code_generator.parser._graph_ids import keyed_models
from datamodel_code_generator.parser._init_exports import exports_reach
from datamodel_code_generator.parser._passes._shared import _parsed_rule_data_types

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence

    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator.enums import AllExportsScope
    from datamodel_code_generator.imports import Imports
    from datamodel_code_generator.model.base import DataModel
    from datamodel_code_generator.parser._passes._state import ModuleContextView, ModulePath
    from datamodel_code_generator.parser.generation import GenerationIndex

__all__ = [
    "NO_IMPORT_CYCLE_SEEDS",
    "FailingRead",
    "GenericBaseClassPlacement",
    "ImportBlock",
    "ImportCycleSeeds",
    "ImportLine",
    "ImportPlan",
    "failing_import_reads",
    "generic_base_class_module",
    "import_cycle_seeds",
    "import_plan",
    "inherits_one_of",
    "module_dependency_graph",
    "module_import_edges",
    "relative_import_targets",
    "runs_into_import_cycle",
    "written_import_blocks",
]

ImportLine: TypeAlias = tuple[str, str]
"""A name that a relative import block imports, and the name the module binds it to."""
ImportBlock: TypeAlias = tuple[str, tuple[ImportLine, ...]]
"""The dotted name of the module that a relative import block names, and its import lines in written order."""


@record(eq=False)
class ImportPlan:
    """What each module of an output runs and reads as Python imports it, by dotted name.

    ``modules`` names every written module and every package above one, the root package ``""`` included. ``blocks``
    holds the relative import blocks of a module in written order (``written_import_blocks``), and ``reads`` the
    names of other modules that its definitions read as it runs, through a module it imports (``pets.Pet``).
    """

    modules: frozenset[str]
    blocks: Mapping[str, tuple[ImportBlock, ...]]
    reads: Mapping[str, frozenset[tuple[str, str]]]


@record
class FailingRead:
    """A name that ``reader`` reads of ``module`` while ``module`` still runs, when Python imports ``entry`` first.

    ``lines`` holds the import block each running module runs, as ``(module, imported module, names)``, the
    reader's own where it fails at an import line.
    """

    entry: str
    reader: str
    module: str
    name: str
    lines: tuple[tuple[str, str, tuple[str, ...]], ...]


@record
class GenericBaseClassPlacement:
    """Where the generic base class is written and which modules take it.

    ``module`` is the dotted name of the module that holds the class named ``name``; ``candidates`` are the modules
    with a model that takes it as a base, in the order its placement picks from, each by dotted name and module path;
    ``users`` are the modules that import it.
    """

    module: str
    name: str
    candidates: tuple[tuple[str, ModulePath], ...]
    users: frozenset[str]


@record
class ImportCycleSeeds:
    """What a parse again of a run lays out otherwise, so that every module it writes imports.

    ``merges`` are the modules, by module path, that merge into an ``_internal`` module beside the merges that the
    references between models make; ``base_class_module`` is the module path of the module that holds the generic
    base class. ``package_classes`` holds, by the module path of each package whose submodules
    --rename-submodules-hidden-by-classes renamed otherwise than the names its ``__init__.py`` binds in the output, the
    names it bound there in every earlier attempt, which the renames of the submodules then read.
    """

    merges: tuple[frozenset[ModulePath], ...] = ()
    base_class_module: ModulePath | None = None
    package_classes: tuple[tuple[ModulePath, frozenset[str]], ...] = ()


NO_IMPORT_CYCLE_SEEDS: Final = ImportCycleSeeds()
"""The seeds of a first attempt, which lays out nothing otherwise."""


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
    contexts: Iterable[ModuleContextView],
    *,
    treat_dot_as_module: bool | None,
    all_exports_scope: AllExportsScope | None = None,
) -> dict[str, set[str]]:
    """Return the dotted names of the modules that each written module imports or imports from, by its dotted name.

    Each relative import block reaches the module it names and the dotted name of each name it imports, which is the
    submodule of that name where the module has one (``relative_import_targets``). With ``all_exports_scope`` the
    ``__init__`` module of a package imports the modules with models that its exports reach (``_exported_imports``) too.
    """
    edges: dict[str, set[str]] = {}
    exporters: list[tuple[tuple[str, ...], int]] = []
    for ctx in contexts:
        path = written_path(ctx.module, treat_dot_as_module=treat_dot_as_module)
        importer = dotted_module(path)
        is_init = _written_package(ctx, treat_dot_as_module=treat_dot_as_module) is not None
        targets = edges.setdefault(importer, set())
        for _, target, names in relative_import_targets(importer, ctx.imports, is_init=is_init):
            targets.add(target)
            targets.update(dotted_name(target, name) for name in names)
        if all_exports_scope is not None and ctx.models and (parts := module_parts(path)):
            exporters.append((parts, -1))
    if all_exports_scope is not None:
        for package, nodes in _exported_imports(exporters, all_exports_scope).items():
            edges.setdefault(".".join(package), set()).update(".".join(node[0]) for node in nodes)
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


def inherits_one_of(model: DataModel, models: Collection[DataModel]) -> bool:
    """Return whether one of ``models`` is a base class of ``model``."""
    return any(reference.source in models for base in model.base_classes if (reference := base.reference))


def generic_base_class_module(
    targets: Sequence[Sequence[DataModel]], modules: Sequence[ModulePath], seeded: ModulePath | None = None
) -> int:
    """Return the position of the module that holds the generic base class, among the modules of ``targets``.

    ``targets`` holds the models of each module that take the class, and ``modules`` the module path of each. It goes
    to the ``seeded`` module where it is one of them (``import_cycle_seeds``), else to the first module with a model
    that inherits none of them, or to the first module when every one inherits another.
    """
    if seeded in modules:
        return modules.index(seeded)
    every_target = {model for models in targets for model in models}
    return next(
        (
            position
            for position, models in enumerate(targets)
            if not all(inherits_one_of(model, every_target) for model in models)
        ),
        0,
    )


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


def _written_order(from_: str) -> tuple[int, str]:
    """Return where the formatters write the relative import block of ``from_``: the furthest package first."""
    module = from_.lstrip(".")
    return len(module) - len(from_), module


def written_import_blocks(importer: str, imports: Iterable[Imports], *, is_init: bool) -> tuple[ImportBlock, ...]:
    """Return the relative import blocks that the module ``importer`` writes from ``imports``, in written order.

    isort, Ruff and the builtin formatter sort the imports of a module together: the blocks of the furthest package
    first (``from .. import`` before ``from . import``), then by module, and the names of a block sorted. Each block
    comes with the module it names and each name with the name the module binds it to. ``is_init`` makes ``importer``
    a package ``__init__``.
    """
    lines: defaultdict[str, set[ImportLine]] = defaultdict(set)
    for held in imports:
        for from_ in (name for name in held if name and name.startswith(".")):
            lines[from_].update((_stored_import(name)[0], held.get_effective_name(from_, name)) for name in held[from_])
    return tuple(
        (absolute(importer, from_, current_is_init=is_init), tuple(sorted(lines[from_])))
        for from_ in sorted(lines, key=_written_order)
    )


def _packages_above(module: str) -> Iterator[str]:
    """Yield the dotted names of the packages above the module ``module``, the root package ``""`` first."""
    if module:
        parts = module.split(".")
        for size in range(len(parts)):
            yield ".".join(parts[:size])


def import_plan(modules: Iterable[str], blocks: Mapping[str, tuple[ImportBlock, ...]]) -> ImportPlan:
    """Return the plan of the written ``modules`` and their ``blocks``, the packages above them included, no reads."""
    written = {*modules, *blocks}
    return ImportPlan(frozenset(written.union(*map(_packages_above, written))), blocks, {})


def _import_graph(plan: ImportPlan, without: frozenset[tuple[str, str]] = frozenset()) -> dict[str, set[str]]:
    """Return the modules that each module of ``plan`` reads from or runs as it runs, leaving out the lines ``without``.

    A block runs or reads the submodule of each name the module it names has, reads that module for any other name,
    and runs it unless it lies above the importing module; it runs the packages above them that are not above the
    importing module, which ran those before it.
    """
    graph: dict[str, set[str]] = {module: set() for module in plan.modules}
    for module, blocks in plan.blocks.items():
        above = set(_packages_above(module))
        reached = graph.setdefault(module, set())
        for target, lines in blocks:
            if not (kept := [name for name, _ in lines if (target, name) not in without]):
                continue
            submodules = [submodule for name in kept if (submodule := dotted_name(target, name)) in plan.modules]
            reads_target = len(submodules) < len(kept) or target not in above
            for imported in (*([target] if reads_target else []), *submodules):
                reached.update(part for part in _packages_above(imported) if part not in above)
                reached.add(imported)
        reached.discard(module)
    return graph


def _first_failing_read(plan: ImportPlan, entry: str) -> FailingRead | None:
    """Return the first read of a name of a running module that Python makes when it imports ``entry`` first.

    Importing a module runs the packages above it first, then the module: its import blocks in order, each importing
    the module it names, then for each name the submodule of that name where the module has one, or else the name the
    module binds; then the reads of its definitions. A module that still runs binds only what its import lines that
    ran bound. The walk keeps its own stack, so a long chain of imports reaches no recursion limit, and ends at the
    first failing read: no module resumes after it.
    """
    bound: dict[str, set[str]] = {}
    done: set[str] = set()
    running: list[str] = []
    lines: dict[str, tuple[str, tuple[str, ...]]] = {}

    def failure(reader: str, module: str, name: str) -> FailingRead:
        held = tuple((running_module, *lines[running_module]) for running_module in running if running_module in lines)
        return FailingRead(entry, reader, module, name, held)

    def importing(module: str) -> Iterator[tuple[bool, str] | FailingRead]:
        for part in (*_packages_above(module), module):
            if part not in bound:
                yield True, part

    def run(module: str) -> Iterator[tuple[bool, str] | FailingRead]:
        names = bound[module] = set()
        running.append(module)
        for target, block in plan.blocks.get(module, ()):
            lines[module] = target, tuple(name for name, _ in block)
            yield False, target
            for name, binding in block:
                if (submodule := dotted_name(target, name)) in plan.modules:
                    yield False, submodule
                elif target not in done and name not in bound[target]:
                    yield failure(module, target, name)
                names.add(binding)
        lines.pop(module, None)
        for target, name in sorted(plan.reads.get(module, ())):
            if target not in done and name not in bound.get(target, ()):
                yield failure(module, target, name)
        running.pop()
        done.add(module)

    stack = [importing(entry)]
    while stack:
        if (step := next(stack[-1], None)) is None:
            stack.pop()
        elif isinstance(step, FailingRead):
            return step
        else:
            starts, module = step
            stack.append(run(module) if starts else importing(module))
    return None


def runs_into_import_cycle(plan: ImportPlan) -> bool:
    """Return whether a module of ``plan`` runs a module that runs it back, the only way to read a running module."""
    return bool(find_circular_sccs(_import_graph(plan)))


def failing_import_reads(plan: ImportPlan) -> tuple[FailingRead, ...]:
    """Return the first failing read of each module of ``plan`` that fails when Python imports it first.

    Only a module that runs into an import cycle (``runs_into_import_cycle``) can read a module that still runs, so
    only the modules that do are walked.
    """
    graph = _import_graph(plan)
    reached = reaching({module for component in find_circular_sccs(graph) for module in component}, graph.items())
    return tuple(
        failure
        for entry in sorted(plan.modules)
        if any(part in reached for part in (*_packages_above(entry), entry))
        and (failure := _first_failing_read(plan, entry)) is not None
    )


def _joined_sets(sets: Iterable[Collection[ModulePath]]) -> tuple[frozenset[ModulePath], ...]:
    """Join the sets that share a member, each joined set in the place of its first, sorted by its smallest member."""
    joined: list[set[ModulePath]] = []
    for members in sets:
        held = [index for index, merge in enumerate(joined) if not merge.isdisjoint(members)]
        merge = set(members).union(*(joined[index] for index in held))
        for index in reversed(held):
            del joined[index]
        joined.insert(held[0] if held else len(joined), merge)
    return tuple(sorted(map(frozenset, joined), key=min))


def _free_base_class_module(plan: ImportPlan, base_class: GenericBaseClassPlacement) -> ModulePath | None:
    """Return the first candidate module of ``base_class`` that runs no other module that takes or imports the class.

    It reaches them through the blocks of its other imports, as the modules that import the class from it would run
    while it still runs; None where every candidate does.
    """
    graph = _import_graph(plan, frozenset({(base_class.module, base_class.name)}))
    takers = base_class.users | {module for module, _ in base_class.candidates}
    return next(
        (
            path
            for module, path in base_class.candidates
            if takers.isdisjoint(set(reach([module], lambda node: graph.get(node, ()))) - {module})
        ),
        None,
    )


def import_cycle_seeds(
    failures: Sequence[FailingRead],
    plan: ImportPlan,
    seeds: ImportCycleSeeds,
    layout: Mapping[str, frozenset[ModulePath]],
    base_class: GenericBaseClassPlacement | None = None,
) -> ImportCycleSeeds | None:
    """Return what a parse again lays out otherwise so that the ``failures`` of ``plan`` import, or None if nothing.

    A failure that runs an import of the generic base class (``base_class``) moves the class, once, to the first of
    its ``candidates`` that reaches no other module that takes or imports it through the blocks of the other imports:
    those modules then import it from a module that runs none of them. Any other failure merges the modules of the
    import cycle that holds the reader and the module it reads: each written module stands for the modules of the
    module structure that ``layout`` gives it (an ``_internal`` module for the modules of its merge, an empty package
    for none). The merges of ``seeds`` grow and never shrink, so a run is parsed again at most once per module.
    """
    if (
        base_class is not None
        and seeds.base_class_module is None
        and any(
            target == base_class.module and base_class.name in names
            for failure in failures
            for _, target, names in failure.lines
        )
        and (path := _free_base_class_module(plan, base_class)) is not None
    ):
        return dataclasses.replace(seeds, base_class_module=path)
    component_of = {
        module: frozenset(component)
        for component in strongly_connected_components(_import_graph(plan))
        for module in component
    }
    cycles = (
        frozenset().union(*(layout.get(module, ()) for module in component_of[failure.module])) for failure in failures
    )
    merges = _joined_sets((*seeds.merges, *(cycle for cycle in cycles if len(cycle) > 1)))
    return None if merges == seeds.merges else dataclasses.replace(seeds, merges=merges)
