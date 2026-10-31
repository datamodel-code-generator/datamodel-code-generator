"""Which container a node of a type tree is in: one flag per container slot, and the kinds the generator decides on.

A data type (``types.DataType``), and every view of one, holds one flag per slot: ``is_frozen_set``, ``is_set``,
``is_sequence``, ``is_list``, ``is_mapping``, ``is_dict`` and ``is_tuple``. A decision that treats several slots alike
(a mapping is a dict or a ``Mapping``) asks whether a node is ``in`` one of the kinds below instead of testing the
flags itself, so the slots of each kind are listed once and a slot is added in one place.
"""

from __future__ import annotations

from functools import cache
from operator import attrgetter
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, TypeAlias, final

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "ARRAYS",
    "CONTAINERS",
    "CONTAINER_CLASSES",
    "CONTAINER_FLAGS",
    "GENERIC_CONTAINERS",
    "ITEM_CONTAINERS",
    "MAPPINGS",
    "SEQUENCES",
    "SETS",
    "UNHASHABLE_CONTAINERS",
    "WRITTEN_CONTAINERS",
    "ContainerKind",
    "ContainerSlot",
    "LegacyKindRow",
]

ContainerSlot: TypeAlias = Literal["frozen_set", "set", "sequence", "list", "mapping", "dict", "tuple"]

WRITTEN_CONTAINERS: Final[tuple[ContainerSlot, ...]] = ("frozen_set", "set", "sequence", "list", "mapping", "dict")
"""The slots a type hint writes as a generic class around the types of a node, in the order it looks for them."""

CONTAINER_FLAGS: Final[Mapping[ContainerSlot, str]] = MappingProxyType({
    "frozen_set": "is_frozen_set",
    "set": "is_set",
    "sequence": "is_sequence",
    "list": "is_list",
    "mapping": "is_mapping",
    "dict": "is_dict",
    "tuple": "is_tuple",
})
"""The flag that puts a node in each slot: the ``WRITTEN_CONTAINERS`` in their order, then the tuple, a form of its
own."""

CONTAINER_CLASSES: Final[Mapping[tuple[str | None, str], ContainerSlot]] = MappingProxyType({
    (None, "frozenset"): "frozen_set",
    (None, "set"): "set",
    ("collections.abc", "Sequence"): "sequence",
    ("typing", "Sequence"): "sequence",
    (None, "list"): "list",
    ("collections.abc", "Mapping"): "mapping",
    ("typing", "Mapping"): "mapping",
    (None, "dict"): "dict",
})
"""The slot of each generic class a target's type view writes a written container as, by its module and name (no
module for a builtin): a set under ``--use-generic-container-types`` is a frozen set, and a list or dict an abstract
``Sequence`` or ``Mapping``, so each class reads as the slot that writes it there."""

LegacyKindRow: TypeAlias = Literal[
    "legacy-abstract-containers-as-unions",
    "legacy-frozen-set-not-an-array",
    "legacy-all-of-items-not-in-frozen-sets",
    "legacy-unique-tuple-inside-sequence",
    "legacy-collapse-beside-lists-and-dicts",
]
"""A decision that keeps the slots it read before its kind was named, which ``ContainerKind.without`` leaves out.

- `legacy-abstract-containers-as-unions`: the keywords a field's type consumes or enforces itself read a ``Sequence``
  or ``Mapping`` through to its members, like a union.
- `legacy-frozen-set-not-an-array`: the uniqueItems checks of ``--generate-schema-validators`` do not read a frozen
  set as an array.
- `legacy-all-of-items-not-in-frozen-sets`: an allOf root types the items of the array it holds in any array but a
  frozen set.
- `legacy-unique-tuple-inside-sequence`: the tuple of a uniqueItems array is looked up inside a ``Sequence`` too.
- `legacy-collapse-beside-lists-and-dicts`: under ``--field-constraints``, collapse keeps a constrained root model
  beside a list, a dict or a union only.
"""

_LEFT_OUT: Final[Mapping[LegacyKindRow, frozenset[ContainerSlot]]] = MappingProxyType({
    "legacy-abstract-containers-as-unions": frozenset(("sequence", "mapping")),
    "legacy-frozen-set-not-an-array": frozenset(("frozen_set",)),
    "legacy-all-of-items-not-in-frozen-sets": frozenset(("frozen_set",)),
    "legacy-unique-tuple-inside-sequence": frozenset(("sequence",)),
    "legacy-collapse-beside-lists-and-dicts": frozenset(("frozen_set", "set", "sequence", "mapping", "tuple")),
})
"""The slots each legacy row leaves out of the kind its decision names."""


@final
class ContainerKind:
    """The container slots that a decision treats alike; ``node in kind`` tells whether a node is in one of them."""

    __slots__ = ("_flags_of", "flags", "slots")

    def __init__(self, *slots: ContainerSlot) -> None:
        """Make the kind of two or more ``slots``."""
        self.slots = frozenset(slots)
        self.flags = frozenset(CONTAINER_FLAGS[slot] for slot in slots)
        self._flags_of = attrgetter(*(CONTAINER_FLAGS[slot] for slot in slots))

    def __contains__(self, node: object) -> bool:
        """Return whether the flags of ``node`` put it in a container of this kind."""
        return any(self._flags_of(node))

    def without(self, row: LegacyKindRow) -> ContainerKind:
        """Return this kind without the slots that the legacy ``row`` leaves out."""
        return _kind_without(self.slots, row)


@cache
def _kind_without(slots: frozenset[ContainerSlot], row: LegacyKindRow) -> ContainerKind:
    """Return the kind of ``slots`` but those ``row`` leaves out, in the order of ``CONTAINER_FLAGS``."""
    return ContainerKind(*(slot for slot in CONTAINER_FLAGS if slot in slots and slot not in _LEFT_OUT[row]))


MAPPINGS: Final = ContainerKind("mapping", "dict")
"""A dict or a ``Mapping``: values under keys, which a type hint writes after the key type."""
ITEM_CONTAINERS: Final = ContainerKind("frozen_set", "set", "sequence", "list")
"""A list, a sequence, a set or a frozen set: items of the one type it holds."""
ARRAYS: Final = ContainerKind("frozen_set", "set", "sequence", "list", "tuple")
"""An item container or a tuple: what a JSON array is read as."""
GENERIC_CONTAINERS: Final = ContainerKind(*WRITTEN_CONTAINERS)
"""An item container or a mapping: what a type hint writes as a generic class around the types of a node."""
CONTAINERS: Final = ContainerKind(*CONTAINER_FLAGS)
"""Any container: an array or a mapping."""
UNHASHABLE_CONTAINERS: Final = ContainerKind("set", "sequence", "list", "mapping", "dict")
"""A container whose values Pydantic validates as a list, a set or a dict, which do not hash; a frozen set and a tuple
do."""
SETS: Final = ContainerKind("frozen_set", "set")
"""A set or a frozen set, whose items must hash."""
SEQUENCES: Final = ContainerKind("sequence", "list")
"""A list or a ``Sequence``: items in order, duplicates kept, as a root model's sequence interface reads them (a set has
no order; a tuple is a form of its own)."""
