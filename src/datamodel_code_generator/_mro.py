"""The C3 linearization: the order in which a class and its bases are searched, over items of any kind.

The order of an item is the item, then the C3 merge of the orders of its parents and the list of its parents. Each
caller names its rule (``C3Rule``):

* ``first-head``: the order the generator gives models and their raw schemas (`model/base.py:linearize_data_models`,
  `parser/jsonschema.py:JsonSchemaParser._linearize_inherited_schema_refs`). Where no head is free the merge takes
  the first one, and a walk that meets an item it is still linearizing takes it there without its parents, so every
  input has an order.
* ``python``: Python's method resolution order, or None where Python refuses a class: an inheritance cycle or parents
  whose orders conflict (`parser/base.py:Parser._check_model_inheritance`, the target bindings of
  `_target_binding.py`).

``resolve_members`` reads the members of a class and its bases through such an order: the fields a model has with its
bases (`model/base.py:_effective_field_owners`), the fields a target binds, the struct options msgspec takes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Generic, Literal, TypeAlias, TypeVar, overload

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Hashable, Iterable, Sequence

_T = TypeVar("_T")
_K = TypeVar("_K")
_M = TypeVar("_M")
C3Rule: TypeAlias = Literal["first-head", "python"]


def resolve_members(
    order: Sequence[_T], members_of: Callable[[_T], Iterable[_M]], key: Callable[[_M], _K | None]
) -> dict[_K, tuple[_T, _M]]:
    """Return the member each key resolves to and its owner, searching ``order``: a class, then its bases in order.

    The first owner that declares a key gives its member, at the place where the last one declares it; a member whose
    key is None is left out.
    """
    resolved: dict[_K, tuple[_T, _M]] = {}
    for owner in reversed(order):
        for member in members_of(owner):
            if (member_key := key(member)) is not None:
                resolved[member_key] = owner, member
    return resolved


def sort_descendants_first(
    items: list[_T], key: Callable[[_T], Hashable | None], ancestors_of: Callable[[_T], Collection[Hashable]]
) -> list[_T]:
    """Order ``items`` descendants first, stably: by how many ancestor sets of their distinct keys hold an item's key.

    ``ancestors_of`` gives the keys of an item's ancestors; an item whose key is None counts in no ancestor set.
    """
    if len(items) <= 1:
        return items.copy()
    ancestor_keys = {item_key: ancestors_of(item) for item in items if (item_key := key(item)) is not None}
    return sorted(items, key=lambda item: sum(key(item) in ancestors for ancestors in ancestor_keys.values()))


@overload
def c3_merge(  # noqa: D418 - CodeQL flags an ellipsis body.
    sequences: list[list[_T]], key: Callable[[_T], Hashable], *, rule: Literal["first-head"] = "first-head"
) -> list[_T]:
    """Merge ``sequences``, taking the first head where none is free."""


@overload
def c3_merge(  # noqa: D418
    sequences: list[list[_T]], key: Callable[[_T], Hashable], *, rule: Literal["python"]
) -> list[_T] | None:
    """Merge ``sequences``, or return None where no head is free."""


def c3_merge(
    sequences: list[list[_T]], key: Callable[[_T], Hashable], *, rule: C3Rule = "first-head"
) -> list[_T] | None:
    """Merge ``sequences`` by C3 head selection; the merge empties them.

    A head is free when no sequence holds it after its first item; ``key`` is read once per item. Row ``first-head``
    takes the first head where none is free, and row ``python`` returns None there.
    """
    pending = [[(key(item), item) for item in sequence] for sequence in sequences if sequence]
    for sequence in sequences:
        sequence.clear()
    result: list[_T] = []
    while pending := [sequence for sequence in pending if sequence]:
        tail_keys = {item_key for sequence in pending for item_key, _ in sequence[1:]}
        if (head := next((sequence[0] for sequence in pending if sequence[0][0] not in tail_keys), None)) is None:
            if rule == "python":
                return None
            head = pending[0][0]
        head_key, candidate = head
        result.append(candidate)
        for sequence in pending:
            sequence[:] = [entry for entry in sequence if entry[0] != head_key]
    return result


class _Frame(Generic[_T]):
    """An item being linearized: its parents, the orders of those already linearized and the next one to take."""

    __slots__ = ("item", "next", "node", "orders", "parents")

    def __init__(self, item: _T, node: Hashable, parents: Sequence[_T]) -> None:
        """Start linearizing ``item``, told apart from the items on the walk by ``node``."""
        self.item = item
        self.node = node
        self.parents = list(parents)
        self.orders: list[list[_T]] = []
        self.next = 0


@overload
def linearize(  # noqa: D418
    heads: list[_T],
    key: Callable[[_T], Hashable],
    parents_of: Callable[[_T], Sequence[_T]],
    node_of: Callable[[_T], Hashable] | None = None,
    *,
    rule: Literal["first-head"] = "first-head",
) -> list[_T]:
    """Return the order of ``heads``; a cycle or a conflict keeps the first-head order."""


@overload
def linearize(  # noqa: D418
    heads: list[_T],
    key: Callable[[_T], Hashable],
    parents_of: Callable[[_T], Sequence[_T]],
    node_of: Callable[[_T], Hashable] | None = None,
    *,
    rule: Literal["python"],
) -> list[_T] | None:
    """Return the order of ``heads``, or None where an inheritance cycle or a conflict leaves none."""


def linearize(
    heads: list[_T],
    key: Callable[[_T], Hashable],
    parents_of: Callable[[_T], Sequence[_T]],
    node_of: Callable[[_T], Hashable] | None = None,
    *,
    rule: C3Rule = "first-head",
) -> list[_T] | None:
    """Return the C3 merge of the orders of ``heads`` and their list, walking the parents ``parents_of`` gives.

    Each item is linearized once by ``key``, depth first and parents in order, without recursion. A walk meets an item
    again when the items it is linearizing hold its ``node_of`` (``key`` by default): row ``first-head`` takes it
    there without its parents, row ``python`` returns None.
    """
    node = key if node_of is None else node_of
    lineages: dict[Hashable, list[_T]] = {}

    def lineage(head: _T) -> list[_T] | None:
        frames = [_Frame(head, node(head), parents_of(head))]
        active = {frames[0].node}
        while True:
            frame = frames[-1]
            if frame.next < len(frame.parents):
                parent = frame.parents[frame.next]
                frame.next += 1
                if (cached := lineages.get(key(parent))) is not None:
                    frame.orders.append(cached.copy())
                elif (parent_node := node(parent)) not in active:
                    frames.append(_Frame(parent, parent_node, parents_of(parent)))
                    active.add(parent_node)
                elif rule == "python":
                    return None
                else:
                    frame.orders.append([parent])
                continue
            if (merged := c3_merge([*frame.orders, frame.parents.copy()], key, rule=rule)) is None:
                return None
            frames.pop()
            active.discard(frame.node)
            lineages[key(frame.item)] = order = [frame.item, *merged]
            if not frames:
                return order
            frames[-1].orders.append(order.copy())

    orders: list[list[_T]] = []
    for head in heads:
        if (order := lineages.get(key(head))) is None and (order := lineage(head)) is None:
            return None
        orders.append(order.copy())
    return c3_merge([*orders, heads.copy()], key, rule=rule)
