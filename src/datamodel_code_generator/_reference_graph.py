"""The reference graph: the models of one snapshot by model key and the edges between them by kind.

Each pass that reads cycles or an order among models takes a snapshot of the graph where its computation runs, over
the models and the edge kinds it reads, and asks the snapshot instead of building a graph of its own. A predicate that
models answer on demand, such as the order of the base classes of a model, takes its snapshot when it is asked, over
the models it reaches (`walk_graph`). Under the ``MODEL_GRAPH`` check, every answer is compared with the computation
it replaces (`graph_answer`).
"""

from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Generic, TypeVar, final

from typing_extensions import NamedTuple

from datamodel_code_generator import Error
from datamodel_code_generator._checks import CheckArea, check_mode
from datamodel_code_generator._graph import stable_toposort
from datamodel_code_generator._parse_memo import note_reads
from datamodel_code_generator._records import record
from datamodel_code_generator._scc import find_circular_sccs, strongly_connected_components

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Hashable, Iterable, Mapping, Sequence

    from datamodel_code_generator._model_keys import ModelKey

_AnswerT = TypeVar("_AnswerT")
_GroupT = TypeVar("_GroupT", bound="Hashable")
_NodeT = TypeVar("_NodeT")


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
    RULE_TYPE = "rule-type"
    """The other models that the schema validator rules of a model name, which its class body evaluates."""
    CLASS_BODY = "class-body"
    """The other models a class body evaluates: base classes, root types and runtime rule types."""
    DEFINITION = "definition"
    """The models of its module that keep model order defines before a model: its bases and runtime rule types."""
    FIELD = "field"
    """The other models of its module that the field types of a model name, for keep model order."""
    BASE = "base"
    """The generated models a model inherits from, in the order its base classes list them."""
    ROOT_TYPE = "root-type"
    """The models that the type of the root field of a model names."""
    MAPPING_DEFAULT = "mapping-default"
    """The dataclasses that the mapping defaults of the constructor fields of a dataclass materialize."""


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
    INHERITED_FIELDS = "inherited fields"
    """On demand: the models whose fields a model inherits, each once, bases first."""
    ANCESTORS = "ancestors"
    """On demand: the generated ancestors of a model, which order its bases descendants first."""
    CYCLIC_ANCESTRY = "cyclic ancestry"
    """On demand: whether the ancestors of a model inherit from one another in a cycle."""
    BASE_CLASS_ORDER = "base class order"
    """On demand: the C3 order of the generated models that models inherit from."""
    STRUCT_TAGS = "struct tags"
    """On demand: the tag and tag field a msgspec struct takes from itself and its generated bases."""
    PROTECTED_NAMESPACES = "protected namespaces"
    """On demand: the model whose configuration gives the protected namespaces of a pydantic model."""
    ROOT_CONSTRAINTS = "root constraints"
    """On demand: whether a root model or a root model its root type names constrains its root field."""
    MAPPING_DEFAULTS = "mapping defaults"
    """On demand: whether the mapping defaults of a dataclass materialize a model of one path twice."""


_NO_KEYS: frozenset[ModelKey] = frozenset()


@final
@record(eq=False)
class ReferenceGraph:
    """The models of one snapshot, in the order the pass lists them, and the edges between them by kind."""

    snapshot: GraphSnapshot
    nodes: tuple[ModelKey, ...]
    edges: Mapping[EdgeKind, Mapping[ModelKey, tuple[ModelKey, ...]]]
    """The targets of each model by kind, in the order the pass lists them, all of them models of the snapshot."""

    def targets(self, key: ModelKey, kinds: Collection[EdgeKind]) -> frozenset[ModelKey]:
        """Return the models that ``key`` points at through edges of ``kinds``."""
        return _NO_KEYS.union(*(self.edges[kind].get(key, ()) for kind in kinds))

    def ordered_targets(self, key: ModelKey, kind: EdgeKind) -> tuple[ModelKey, ...]:
        """Return the models that ``key`` points at through edges of ``kind``, in the order the pass lists them."""
        return self.edges[kind].get(key, ())

    def reached(self, key: ModelKey, kinds: Collection[EdgeKind]) -> frozenset[ModelKey]:
        """Return the models that ``key`` reaches through one or more edges of ``kinds``, itself on a cycle."""
        reached: set[ModelKey] = set()
        pending = list(self.targets(key, kinds))
        while pending:
            if (target := pending.pop()) not in reached:
                reached.add(target)
                pending.extend(self.targets(target, kinds))
        return frozenset(reached)

    def acyclic_order(self, kinds: Collection[EdgeKind]) -> list[ModelKey] | None:
        """Return the models with the targets of each first, or ``None`` when edges of ``kinds`` close a cycle."""
        order: list[ModelKey] = []
        for component in self.components(kinds):
            key = next(iter(component))
            if len(component) > 1 or key in self.targets(key, kinds):
                return None
            order.append(key)
        return order

    def preorder(self, key: ModelKey, kind: EdgeKind) -> list[ModelKey]:
        """Return ``key`` and the models it reaches through edges of ``kind``, each once, as a stack walk takes them.

        A model comes before its targets, and its last target first.
        """
        order: list[ModelKey] = []
        met: set[ModelKey] = set()
        pending = [key]
        while pending:
            if (current := pending.pop()) not in met:
                met.add(current)
                order.append(current)
                pending.extend(self.ordered_targets(current, kind))
        return order

    def postorder(self, key: ModelKey, kind: EdgeKind, met: set[ModelKey]) -> list[ModelKey]:
        """Return ``key`` and the models it reaches through edges of ``kind``, each after its targets in their order.

        A model of ``met`` is left out with the models reached only through it; every model returned is added to it.
        """
        order: list[ModelKey] = []
        entered: list[ModelKey] = []
        stack = [iter((key,))]
        while stack:
            if (target := next(stack[-1], None)) is None:
                stack.pop()
                if entered:
                    order.append(entered.pop())
            elif target not in met:
                met.add(target)
                entered.append(target)
                stack.append(iter(self.ordered_targets(target, kind)))
        return order

    def meets_a_group_twice(
        self, keys: Iterable[ModelKey], group_of: Callable[[ModelKey], Hashable], kinds: Collection[EdgeKind]
    ) -> bool:
        """Return whether a walk through edges of ``kinds`` from one of ``keys`` meets two models of one group.

        A model on a cycle meets itself again; each component is handled once, leaves first.
        """
        later_groups: dict[ModelKey, frozenset[Hashable]] = {}
        for component in self.components(kinds):
            groups: set[Hashable] = set()
            for key in component:
                for target in self.targets(key, kinds):
                    if target in component:
                        groups.update(map(group_of, component))
                    else:
                        groups.add(group_of(target))
                        groups.update(later_groups[target])
            frozen_groups = frozenset(groups)
            later_groups.update(dict.fromkeys(component, frozen_groups))
        return any(group_of(key) in later_groups[key] for key in keys)

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
                key: tuple(target for target in targets if target in node_set)
                for key, targets in targets_by_key.items()
                if key in node_set
            })
            for kind, targets_by_key in edges.items()
        }),
    )


class ReachedModels(NamedTuple, Generic[_NodeT]):
    """The snapshot of the models a walk reaches, the model of each key and the keys the walk starts from."""

    graph: ReferenceGraph
    node_of: Mapping[ModelKey, _NodeT]
    starts: tuple[ModelKey, ...]


def walk_graph(
    snapshot: GraphSnapshot,
    kind: EdgeKind,
    starts: Iterable[_NodeT],
    key_of: Callable[[_NodeT], ModelKey],
    targets_of: Callable[[_NodeT], Iterable[_NodeT]],
) -> ReachedModels[_NodeT]:
    """Take the snapshot of the models that edges of ``kind``, the ``targets_of`` each model, reach from ``starts``.

    The first model met under a key stands for the key. The derived field value being computed, if any, records the
    models reached as read (`note_reads`).
    """
    node_of: dict[ModelKey, _NodeT] = {}

    def met(node: _NodeT) -> ModelKey:
        node_of.setdefault(key := key_of(node), node)
        return key

    start_keys = tuple(map(met, starts))
    targets: dict[ModelKey, tuple[ModelKey, ...]] = {}
    pending = list(start_keys)
    while pending:
        if (key := pending.pop()) not in targets:
            targets[key] = target_keys = tuple(map(met, targets_of(node_of[key])))
            pending.extend(target_keys)
    note_reads(node_of.values())
    return ReachedModels(reference_graph(snapshot, node_of, {kind: targets}), MappingProxyType(node_of), start_keys)


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
