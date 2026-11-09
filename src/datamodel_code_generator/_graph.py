"""Generic graph algorithms shared by parsing and dynamic model loading."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Hashable, Iterable, Iterator
from heapq import heappop, heappush
from typing import Any, TypeVar

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


def post_order(
    starts: Iterable[_T],
    children: Callable[[_T], Iterable[_T]],
    key: Callable[[_T], Hashable] | None = None,
    *,
    entered: set[Any] | None = None,
) -> Iterator[_T]:
    """Yield the nodes a depth-first walk from ``starts`` enters, each after the nodes it enters from it.

    The walk enters each start in turn, then the first child of the node it is in (``children``, read once as the
    walk enters the node) that it has not entered yet, and leaves a node once it has entered all its children; it
    enters a node of a ``key`` (the node itself by default) once. ``entered`` holds the keys entered so far, which
    an earlier walk may have filled: a node of one of them is left out with the nodes reached only through it.
    Each node is yielded as the walk leaves it, so a caller may act on it before the walk reads further.
    """
    seen: set[Any] = set() if entered is None else entered
    for start in starts:
        if (marker := start if key is None else key(start)) in seen:
            continue
        seen.add(marker)
        stack = [(start, iter(children(start)))]
        while stack:
            node, pending = stack[-1]
            for child in pending:
                if (marker := child if key is None else key(child)) not in seen:
                    seen.add(marker)
                    stack.append((child, iter(children(child))))
                    break
            else:
                stack.pop()
                yield node


def reaching(seeds: Iterable[TNode], edges: Iterable[tuple[TNode, Iterable[TNode]]]) -> set[TNode]:
    """Return ``seeds`` and every node from which an edge, or a chain of edges, leads to one of them.

    ``edges`` pairs a node with the nodes its edges lead to; a node may be paired more than once.
    """
    sources: defaultdict[TNode, list[TNode]] = defaultdict(list)
    for node, targets in edges:
        for target in targets:
            sources[target].append(node)
    return set(reach(seeds, lambda node: sources.get(node, ())))


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


__all__ = ["post_order", "reach", "reaching", "stable_toposort"]
