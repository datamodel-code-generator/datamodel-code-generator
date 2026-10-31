"""Which container a node of a type tree is in: one flag per container slot, and the kinds the generator decides on.

A data type (``types.DataType``), and every view of one, holds one flag per slot: ``is_frozen_set``, ``is_set``,
``is_sequence``, ``is_list``, ``is_mapping``, ``is_dict`` and ``is_tuple``. A decision that treats several slots alike
(a mapping is a dict or a ``Mapping``) asks whether a node is ``in`` one of the kinds below instead of testing the
flags itself, so the slots of each kind are listed once and a slot is added in one place.
"""

from __future__ import annotations

from operator import attrgetter
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, TypeAlias, final

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "ARRAYS",
    "CONTAINERS",
    "CONTAINER_FLAGS",
    "GENERIC_CONTAINERS",
    "ITEM_CONTAINERS",
    "MAPPINGS",
    "SETS",
    "UNHASHABLE_CONTAINERS",
    "WRITTEN_CONTAINERS",
    "ContainerKind",
    "ContainerSlot",
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
