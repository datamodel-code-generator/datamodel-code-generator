"""The import cycles of ``scripts/``, read from the import graph of `scripts/_source_model.py`.

The import cycles of the package are heki's ``IMP101`` (``scripts/build_heki_contracts.py``, ``scripts/heki.toml``).

A cycle is a strongly connected component of more than one module. `import_cycles` measures ``scripts`` three ways,
each a key ``import-cycle:<measure>@scripts`` of ``scripts/check_ratchets.py``:

- ``module-level``: the modules on cycles of the imports that run when their module is imported (`runs_at_import`).
  The scope that runs decides: a class body in a function runs with the function, a module-level comprehension at
  import.
- ``lazy``: the modules on cycles of every import; function-local and ``TYPE_CHECKING`` imports count too, as code
  scanning reports cycles that they close.
- ``largest-lazy``: the modules of the largest of those cycles, so that two cycles which merge count as a rise.

Each module counts once, at the line of its first import of another module of its cycle. The edges are the model's
(`SourceTree.import_edges`): a dotted import initialises each parent package and counts as an import of it, a literal
``import_module`` call counts, an import of the module's own package or one above it is no edge (Python initialises
those first), and an import whose module name is computed is not seen (the ``dynamic-import`` key counts it).
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from scripts._source_model import SourceTree

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from scripts._source_model import Edge

Cycle = dict[str, int]
"""The modules of a cycle, each with the line of its first import of another module of the cycle."""


def runs_at_import(edge: Edge) -> bool:
    """Whether an import runs when its module is imported: at module or class level, outside ``TYPE_CHECKING``.

    An import under a version, platform or ``ImportError`` guard, or under any other test, runs on some interpreter,
    so it counts; one in a function, or in a class defined in a function, runs only when the function is called, and
    one under ``TYPE_CHECKING`` never. A list, set or dict comprehension runs where it stands, a generator expression
    only its first iterable (`Scope.runs_in` gives the edge's level).
    """
    return edge.level != "function" and not any(
        condition.kind == "type-checking" and condition.branch == "body" for condition in edge.guard
    )


def _reached(graph: dict[str, set[str]], start: str) -> set[str]:
    """Return the modules that a module's imports reach, itself included only when it is on a cycle."""
    found: set[str] = set()
    pending = [start]
    while pending:
        for module in graph.get(pending.pop(), set()) - found:
            found.add(module)
            pending.append(module)
    return found


def cycles(edges: list[Edge]) -> list[Cycle]:
    """Return the cycles of an import graph, largest first, then by their modules."""
    graph: defaultdict[str, set[str]] = defaultdict(set)
    for edge in edges:
        graph[edge.importer].add(edge.imported)
    reached = {module: _reached(graph, module) for module in graph}
    components = {
        frozenset(other for other in found if module in reached.get(other, set()))
        for module, found in reached.items()
        if module in found
    }
    found_cycles: list[Cycle] = []
    for component in components:
        lines: Cycle = {}
        for edge in edges:
            if edge.importer in component and edge.imported in component:
                lines[edge.importer] = min(lines.get(edge.importer, edge.line), edge.line)
        found_cycles.append(dict(sorted(lines.items())))
    return sorted(found_cycles, key=lambda cycle: (-len(cycle), list(cycle)))


def tree_cycles(tree: SourceTree) -> dict[str, list[Cycle]]:
    """Return the cycles of a tree by measure: ``module-level``, ``lazy`` and ``largest-lazy``."""
    edges = tree.import_edges()
    lazy = cycles(edges)
    return {
        "module-level": cycles([edge for edge in edges if runs_at_import(edge)]),
        "lazy": lazy,
        "largest-lazy": lazy[:1],
    }


def import_cycles(root: Path, files: Iterable[str] | None = None) -> dict[str, list[list[tuple[str, int]]]]:
    """Return the cycles of each key of ``scripts`` under ``root``: each module's path and line.

    ``files`` limits the tree to the files of that set (paths relative to ``root``), as the ratchet selects them.
    """
    tree = SourceTree(root, ["scripts"], files=None if files is None else set(files))
    return {
        f"import-cycle:{measure}@scripts": [
            [(tree.paths[module].relative_to(root).as_posix(), line) for module, line in cycle.items()]
            for cycle in measured
        ]
        for measure, measured in tree_cycles(tree).items()
        if measured
    }
