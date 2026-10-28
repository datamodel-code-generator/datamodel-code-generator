"""Who holds each data type node of the generation facts, and the ``data_types`` lists that several nodes share.

A node is held by a field (its ``data_type``), by another node (an item of its ``data_types`` or its ``dict_key``) or
by a model (a base class). The facts give one `HolderEdge` per occurrence they record, read from the recorded models
when asked for, so a node that several holders reach has several edges; a `ChildList` is a ``data_types`` list object
that more than one node holds.

Under the ``MODEL_GRAPH`` check, every refresh checks the models it records against two invariants: the ``parent`` of
every node, when set, is one of its holders, and no node contains itself.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, TypeAlias, final

from datamodel_code_generator._records import record

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

    from datamodel_code_generator._model_keys import FieldKey, ModelKey, NodeKey
    from datamodel_code_generator.types import DataType

HolderRole: TypeAlias = Literal["field", "nested", "dict_key", "base"]
ParentMismatch: TypeAlias = Literal["recorded field", "dropped field", "data type"]
"""What the ``parent`` of a node is when it does not hold the node: a field of a recorded model, another field, or a
data type."""

KNOWN_PARENT_MISMATCHES: Final[Mapping[ParentMismatch, str]] = MappingProxyType({
    "dropped field": "JsonSchemaParser._build_field_array returns the data type of the field that "
    "parse_array_fields builds for an array item and drops the field, which stays the parent of that data type",
})
"""The mismatches the ``MODEL_GRAPH`` check leaves alone, with the code that makes them; entries are only removed."""


@final
@record
class HolderEdge:
    """One place that holds a node: a field's type, an item or the key of another node, or a model's base class."""

    holder: NodeKey | FieldKey | ModelKey
    """The field for ``field``, the node for ``nested`` and ``dict_key``, the model for ``base``."""
    role: HolderRole
    index: int | None
    """The position in the holding node's ``data_types`` or the model's base classes; None for one-slot holders."""


@final
@record
class ChildList:
    """A ``data_types`` list object that more than one recorded node holds."""

    key: int
    """The ``id`` of the list."""
    owners: tuple[NodeKey, ...]
    """The nodes that hold the list, in the order a full rebuild's traversal first reaches them."""


InlinedReplacement: TypeAlias = Literal["sole-holder", "replace-in-owner", "legacy-shared-node-in-place"]
"""How root-model collapse replaces a node under a root type it inlined by sharing (``GenerationStore.inline_shared``).

* ``sole-holder``: no node from the node up to its field is held by more than one holder; the holder gets the
  replacement.
* ``replace-in-owner``: a direct member of a shared `ChildList` (or a shared dict key): the owner, the node its
  ``parent`` names, gets a new list with the replacement; the other holders keep the old list and the member, whose
  reference is detached.
* ``legacy-shared-node-in-place``: a member deeper under a shared `ChildList`: the shared node that holds it is
  written in place, so every holder of that node sees the replacement.
"""


def parent_is_holder(parent: object, edges: Iterable[HolderEdge]) -> bool:
    """Return whether ``parent`` is unset or one of the field and node holders that ``edges`` name.

    Only nodes with a reference or nested types get a ``parent`` when they are built, and a model is never one.
    """
    if parent is None:
        return True
    parent_key = id(parent)
    return any(edge.holder == parent_key and edge.role != "base" for edge in edges)


def node_in_cycle(roots: Iterable[DataType], finished: set[int]) -> DataType | None:
    """Return a node that contains itself among the trees of ``roots``, or None.

    ``finished`` holds the nodes already walked without a cycle; the walk adds the nodes it finishes, so the trees of
    several calls share it.
    """
    active: set[int] = set()
    for root in roots:
        active.add(id(root))
        stack = [(root, _children(root))]
        while stack:
            node, children = stack[-1]
            if (child := next(children, None)) is None:
                stack.pop()
                active.discard(id(node))
                finished.add(id(node))
                continue
            if id(child) in active:
                return child
            if id(child) not in finished:
                active.add(id(child))
                stack.append((child, _children(child)))
    return None


def _children(data_type: DataType) -> Iterator[DataType]:
    yield from data_type.data_types
    if dict_key := data_type.dict_key:
        yield dict_key
