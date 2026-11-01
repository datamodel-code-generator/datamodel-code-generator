"""Generic graph algorithms shared by parsing and dynamic model loading."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Hashable, Iterable
from heapq import heappop, heappush
from typing import TypeVar

TNode = TypeVar("TNode", bound=Hashable)
_T = TypeVar("_T")


def reach(
    starts: Iterable[_T], visit: Callable[[_T], Iterable[_T]], key: Callable[[_T], Hashable] | None = None
) -> list[_T]:
    """Return the nodes a stack walk from ``starts`` takes, each once, in the order it takes them.

    The walk takes the node pushed last first, beginning with the last start. It takes a node unless it took one of
    the same ``key`` (the node itself by default) before, calls ``visit`` with it, and pushes the nodes ``visit``
    returns, in their order.
    """
    taken: set[object] = set()
    order: list[_T] = []
    pending = list(starts)
    while pending:
        node = pending.pop()
        if (marker := node if key is None else key(node)) not in taken:
            taken.add(marker)
            order.append(node)
            pending.extend(visit(node))
    return order


def stable_toposort(
    nodes: list[TNode],
    edges: dict[TNode, set[TNode]],
    *,
    key: Callable[[TNode], int],
) -> list[TNode]:
    """Stable topological sort; breaks ties by `key`.

    The `edges` mapping is an adjacency list where `edges[u]` contains all `v`
    such that `u -> v` (i.e., `u` must come before `v`).

    If a cycle is detected, any remaining nodes are appended in `key` order for
    determinism.
    """
    node_set = set(nodes)
    if len(node_set) != len(nodes):
        occurrence_counts = Counter(nodes)
        unique_nodes = list(reversed(dict.fromkeys(reversed(nodes))))
        return [node for node in stable_toposort(unique_nodes, edges, key=key) for _ in range(occurrence_counts[node])]

    order_index = {node: index for index, node in enumerate(nodes)}
    indegree: dict[TNode, int] = dict.fromkeys(nodes, 0)
    outgoing: dict[TNode, set[TNode]] = {node: set() for node in nodes}

    for source in node_set & edges.keys():
        new_destinations = edges[source] & node_set - outgoing[source]
        outgoing[source].update(new_destinations)
        for destination in new_destinations:
            indegree[destination] += 1

    outgoing_sorted = {
        node: sorted(neighbors, key=lambda neighbor: (key(neighbor), order_index[neighbor]))
        for node, neighbors in outgoing.items()
    }

    ready: list[tuple[int, int, TNode]] = []
    for node in nodes:
        if indegree[node] == 0:
            heappush(ready, (key(node), order_index[node], node))

    result: list[TNode] = []
    while ready:
        _, _, node = heappop(ready)
        result.append(node)
        for neighbor in outgoing_sorted[node]:
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                heappush(ready, (key(neighbor), order_index[neighbor], neighbor))

    if len(result) != len(nodes):
        emitted = set(result)
        result.extend(
            sorted((node for node in nodes if node not in emitted), key=lambda node: (key(node), order_index[node]))
        )
    return result


__all__ = ["reach", "stable_toposort"]
