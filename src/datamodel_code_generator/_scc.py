"""Strongly Connected Components detection using Tarjan's algorithm.

Provides SCC detection for directed graphs whose nodes sort: module dependency graphs, to identify circular import
patterns in generated code, and graphs of models by model key.
"""

from __future__ import annotations

from enum import IntEnum
from typing import TYPE_CHECKING, Generic, Protocol, TypeAlias, TypeVar

from typing_extensions import NamedTuple, Self

if TYPE_CHECKING:
    from collections.abc import Mapping
    from collections.abc import Set as AbstractSet


class _SortableNode(Protocol):
    """A hashable graph node that sorts, so that the walk visits nodes and neighbors deterministically."""

    def __lt__(self, other: Self, /) -> bool:
        """Return whether the node sorts before ``other``."""


NodeT = TypeVar("NodeT", bound=_SortableNode)
ModulePath: TypeAlias = tuple[str, ...]
SCC: TypeAlias = set[ModulePath]


class _Phase(IntEnum):
    """DFS traversal phase for iterative Tarjan's algorithm."""

    VISIT = 0
    POSTVISIT = 1


class _Frame(NamedTuple, Generic[NodeT]):
    """Call stack frame for iterative DFS."""

    node: NodeT
    neighbor_idx: int
    phase: _Phase


class _TarjanState(Generic[NodeT]):
    """Mutable state for Tarjan's SCC algorithm."""

    __slots__ = ("graph", "index", "index_counter", "lowlinks", "on_stack", "result", "sorted_cache", "stack")

    def __init__(self, graph: Mapping[NodeT, AbstractSet[NodeT]]) -> None:
        self.graph = graph
        self.index_counter: int = 0
        self.stack: list[NodeT] = []
        self.lowlinks: dict[NodeT, int] = {}
        self.index: dict[NodeT, int] = {}
        self.on_stack: set[NodeT] = set()
        self.result: list[set[NodeT]] = []
        self.sorted_cache: dict[NodeT, list[NodeT]] = {}

    def get_sorted_neighbors(self, node: NodeT) -> list[NodeT]:
        """Get sorted neighbors with lazy memoization."""
        cached: list[NodeT] | None = self.sorted_cache.get(node)
        if cached is None:
            cached = sorted(self.graph.get(node, ()))
            self.sorted_cache[node] = cached
        return cached

    def extract_scc(self, root: NodeT) -> None:
        """Pop nodes from stack to form an SCC rooted at the given node."""
        scc: set[NodeT] = set()
        while True:
            w: NodeT = self.stack.pop()
            self.on_stack.remove(w)
            scc.add(w)
            if w == root:
                break
        self.result.append(scc)

    def initialize_node(self, node: NodeT) -> None:
        """Initialize a node for DFS traversal."""
        self.index[node] = self.lowlinks[node] = self.index_counter
        self.index_counter += 1
        self.stack.append(node)
        self.on_stack.add(node)


def _strongconnect(state: _TarjanState[NodeT], start: NodeT) -> None:
    """Execute Tarjan's strongconnect algorithm iteratively."""
    state.initialize_node(start)
    call_stack: list[_Frame[NodeT]] = [_Frame(start, 0, _Phase.VISIT)]

    while call_stack:
        frame: _Frame[NodeT] = call_stack.pop()
        node: NodeT = frame.node
        neighbors: list[NodeT] = state.get_sorted_neighbors(node)
        neighbor_idx: int = frame.neighbor_idx

        if frame.phase == _Phase.POSTVISIT:
            child: NodeT = neighbors[neighbor_idx]
            state.lowlinks[node] = min(state.lowlinks[node], state.lowlinks[child])
            neighbor_idx += 1

        while neighbor_idx < len(neighbors):
            w: NodeT = neighbors[neighbor_idx]

            if w not in state.index:
                call_stack.append(_Frame(node, neighbor_idx, _Phase.POSTVISIT))
                state.initialize_node(w)
                call_stack.append(_Frame(w, 0, _Phase.VISIT))
                break
            if w in state.on_stack:
                state.lowlinks[node] = min(state.lowlinks[node], state.index[w])

            neighbor_idx += 1
        else:
            if state.lowlinks[node] == state.index[node]:
                state.extract_scc(node)


def strongly_connected_components(graph: Mapping[NodeT, AbstractSet[NodeT]]) -> list[set[NodeT]]:
    """Return every strongly connected component, singletons included, leaves first (Tarjan's algorithm).

    The walk is iterative, so deep graphs do not reach the recursion limit; neighbors are sorted once, lazily.
    """
    all_nodes: set[NodeT] = set(graph.keys())
    for neighbors in graph.values():
        all_nodes.update(neighbors)

    state = _TarjanState(graph)
    for node in sorted(all_nodes):
        if node not in state.index:
            _strongconnect(state, node)

    return state.result


def find_circular_sccs(graph: Mapping[NodeT, AbstractSet[NodeT]]) -> list[set[NodeT]]:
    """Return the components on a cycle, of more than one node or of one node with an edge to itself, by minimum."""
    return sorted(
        (
            scc
            for scc in strongly_connected_components(graph)
            if len(scc) > 1 or (node := next(iter(scc))) in graph.get(node, ())
        ),
        key=min,
    )
