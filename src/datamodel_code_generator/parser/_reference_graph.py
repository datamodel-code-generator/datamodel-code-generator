"""The reference graph: the models of one snapshot by model key and the edges between them by kind.

Each pass that reads cycles or an order among models takes a snapshot of the graph where its computation runs, over
the models and the edge kinds it reads, and asks the snapshot instead of building a graph of its own. Under the
``MODEL_GRAPH`` check, every answer is compared with the computation it replaces (`graph_answer`).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, TypeVar, final

from datamodel_code_generator import Error
from datamodel_code_generator._checks import CheckArea, check_mode
from datamodel_code_generator._graph import stable_toposort
from datamodel_code_generator.parser._scc import find_circular_sccs, strongly_connected_components

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Hashable, Iterable, Mapping, Sequence

    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator.parser._graph_ids import KeyedModels

_AnswerT = TypeVar("_AnswerT")
_GroupT = TypeVar("_GroupT", bound="Hashable")


class EdgeKind(Enum):
    """What makes a model point at another one."""

    REFERENCE = "reference"
    """The references a model counts as its dependencies (`GenerationIndex.referenced_keys_for_model`), with its
    additional-properties snapshot."""
    ANY_REFERENCE = "any-reference"
    """Every reference the data types of a model hold, dict keys included, with its additional-properties
    snapshot."""
    MAPPING_KEY = "mapping-key"
    """The references that the mapping-key annotations of a root model reach."""
    CONTAINER_ITEM = "container-item"
    """The aliases that an alias references as a container item."""
    CLASS_BODY = "class-body"
    """The other models a class body evaluates: base classes, root types and runtime rule types."""
    DEFINITION = "definition"
    """The models of its module that keep model order defines before a model: its bases and runtime rule types."""
    FIELD = "field"
    """The other models of its module that the field types of a model name, for keep model order."""


class GraphSnapshot(Enum):
    """Where a pass takes a snapshot; the value names the site, as the ``MODEL_GRAPH`` check reports it."""

    MODULE_DEPENDENCIES = "module dependencies"
    """L3, before relocation: the modules that import each other, merged into ``_internal`` modules."""
    CLASS_BODY_ORDER = "class body order"
    """L3, per merged module and in the sort: the models each class body evaluates."""
    CIRCULAR_ROOT_MODELS = "circular root models"
    """L4: the root models on a cycle of root models, which collapsing keeps named."""
    KEEP_MODEL_ORDER = "keep model order"
    """L5, per module: the dependencies that keep model order moves models for."""
    ALIAS_CYCLES = "alias cycles"
    """L5, per module: the aliases on a cycle of container items."""
    NESTED_MODEL_DEFAULT_FACTORIES = "nested model default factories"
    """L6: the models on a reference cycle, whose default factories recurse."""


_NO_KEYS: frozenset[ModelKey] = frozenset()


@final
@dataclass(frozen=True, slots=True, eq=False)
class ReferenceGraph:
    """The models of one snapshot, in the order the pass lists them, and the edges between them by kind."""

    snapshot: GraphSnapshot
    nodes: tuple[ModelKey, ...]
    edges: Mapping[EdgeKind, Mapping[ModelKey, frozenset[ModelKey]]]
    """The targets of each model by kind, all of them models of the snapshot."""

    def targets(self, key: ModelKey, kinds: Collection[EdgeKind]) -> frozenset[ModelKey]:
        """Return the models that ``key`` points at through edges of ``kinds``."""
        return _NO_KEYS.union(*(self.edges[kind].get(key, _NO_KEYS) for kind in kinds))

    def _adjacency(self, kinds: Collection[EdgeKind]) -> dict[ModelKey, frozenset[ModelKey]]:
        return {key: self.targets(key, kinds) for key in self.nodes}

    def components(self, kinds: Collection[EdgeKind]) -> list[set[ModelKey]]:
        """Return every strongly connected component over edges of ``kinds``, leaves first."""
        return strongly_connected_components(self._adjacency(kinds))

    def circular_components(self, kinds: Collection[EdgeKind]) -> list[set[ModelKey]]:
        """Return the components on a cycle over edges of ``kinds``, sorted by their smallest key."""
        return find_circular_sccs(self._adjacency(kinds))

    def quotient(
        self, group_of: Mapping[ModelKey, _GroupT], kinds: Collection[EdgeKind]
    ) -> dict[_GroupT, set[_GroupT]]:
        """Return the edges of ``kinds`` between the groups of ``group_of``, one entry per group, no self-edges."""
        graph: dict[_GroupT, set[_GroupT]] = {group: set() for group in group_of.values()}
        for key, group in group_of.items():
            graph[group].update(
                target for target in map(group_of.__getitem__, self.targets(key, kinds)) if target != group
            )
        return graph

    def dependency_order(
        self, kinds: Collection[EdgeKind], nodes: Sequence[ModelKey], key: Callable[[ModelKey], int]
    ) -> list[ModelKey]:
        """Return ``nodes`` with the targets of each among them first (`stable_toposort`, ties broken by ``key``)."""
        among = frozenset(nodes)
        dependents: dict[ModelKey, set[ModelKey]] = {node: set() for node in nodes}
        for node in nodes:
            for target in self.targets(node, kinds) & among:
                dependents[target].add(node)
        return stable_toposort(list(nodes), dependents, key=key)


def reference_graph(
    snapshot: GraphSnapshot,
    nodes: Iterable[ModelKey],
    edges: Mapping[EdgeKind, Mapping[ModelKey, Iterable[ModelKey]]],
) -> ReferenceGraph:
    """Take the snapshot of ``nodes`` and the ``edges`` between them; edges to other models are left out."""
    node_tuple = tuple(dict.fromkeys(nodes))
    node_set = frozenset(node_tuple)
    return ReferenceGraph(
        snapshot,
        node_tuple,
        MappingProxyType({
            kind: MappingProxyType({
                key: frozenset(targets).intersection(node_set)
                for key, targets in targets_by_key.items()
                if key in node_set
            })
            for kind, targets_by_key in edges.items()
        }),
    )


def graph_answer(snapshot: GraphSnapshot, answer: _AnswerT, old: Callable[[], _AnswerT]) -> _AnswerT:
    """Return ``answer``, what the snapshot answers.

    Under the ``MODEL_GRAPH`` check, an ``answer`` that differs from ``old()``, what the site computed before it read
    the snapshot, is a violation.
    """
    if (mode := check_mode(CheckArea.MODEL_GRAPH)) is not None and answer != old():
        mode.violation(
            Error,
            f"{snapshot.value}: the reference graph and the computation it replaces disagree",
            site=snapshot.value,
        )
    return answer


def unchecked(keyed: KeyedModels) -> KeyedModels:
    """Return ``keyed`` without its check mode, for the computation `graph_answer` compares, so no violation repeats."""
    return replace(keyed, mode=None)
