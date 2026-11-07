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
from typing import TYPE_CHECKING, Final, Literal, NamedTuple, TypeAlias, final

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "ARRAYS",
    "BUILTIN_CONTAINERS",
    "CONTAINERS",
    "CONTAINER_CLASSES",
    "CONTAINER_FLAGS",
    "GENERIC_CONTAINERS",
    "GIVEN_CONTAINERS",
    "ITEM_CONTAINERS",
    "MAPPINGS",
    "SEQUENCES",
    "SETS",
    "SLOT_CLASSES",
    "UNHASHABLE_CONTAINERS",
    "WRITTEN_CONTAINERS",
    "ContainerClass",
    "ContainerKind",
    "ContainerSlot",
    "GivenContainer",
    "GivenContainerName",
    "LegacyKindRow",
    "abstract_class_module",
    "given_container_of",
    "hash_slot",
    "written_slot",
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


class ContainerClass(NamedTuple):
    """The generic class a container slot is written as."""

    name: str
    """Its name with ``--use-standard-collections`` and in a target's type view: a builtin, or an abstract class."""
    typing_name: str
    """Its name without ``--use-standard-collections``: the alias of ``typing``."""
    abstract: bool
    """Whether it is an abstract class, imported from ``abstract_class_module``, rather than a builtin."""
    module: str | None = None
    """The module an abstract class is imported from whatever ``abstract_class_module`` says, if it has one."""

    def written(self, *, standard: bool) -> str:
        """Return the name a type hint writes: ``name`` with ``--use-standard-collections``, else ``typing_name``."""
        return self.name if standard else self.typing_name

    def module_of(self, *, standard: bool) -> str | None:
        """Return the module the class a type hint writes is imported from, or None for a builtin it writes."""
        if self.abstract:
            return self.module or abstract_class_module(standard=standard)
        return None if standard else _ABSTRACT_CLASS_MODULES[1]


SLOT_CLASSES: Final[Mapping[ContainerSlot, ContainerClass]] = MappingProxyType({
    "frozen_set": ContainerClass("frozenset", "FrozenSet", abstract=False),
    "set": ContainerClass("set", "Set", abstract=False),
    "sequence": ContainerClass("Sequence", "Sequence", abstract=True),
    "list": ContainerClass("list", "List", abstract=False),
    "mapping": ContainerClass("Mapping", "Mapping", abstract=True),
    "dict": ContainerClass("dict", "Dict", abstract=False),
    "tuple": ContainerClass("tuple", "Tuple", abstract=False),
})
"""The class each slot is written as."""

_ABSTRACT_CLASS_MODULES: Final = ("collections.abc", "typing")


def abstract_class_module(*, standard: bool) -> str:
    """Return the module an abstract class is imported from.

    It is ``collections.abc`` with ``--use-standard-collections`` (``standard``), else ``typing``.
    """
    return _ABSTRACT_CLASS_MODULES[0] if standard else _ABSTRACT_CLASS_MODULES[1]


_GENERIC_SLOTS: Final[Mapping[ContainerSlot, ContainerSlot]] = MappingProxyType({
    "set": "frozen_set",
    "list": "sequence",
    "dict": "mapping",
})


def written_slot(slot: ContainerSlot, *, generic: bool) -> ContainerSlot:
    """Return the slot whose class writes a container of ``slot``.

    Under ``--use-generic-container-types`` (``generic``) a set is written as a frozen set, a list as a ``Sequence`` and
    a dict as a ``Mapping``; every other slot, and every slot without it, as its own class.
    """
    return _GENERIC_SLOTS.get(slot, slot) if generic else slot


CONTAINER_CLASSES: Final[Mapping[tuple[str | None, str], ContainerSlot]] = MappingProxyType({
    (module, SLOT_CLASSES[slot].name): slot
    for slot in WRITTEN_CONTAINERS
    for module in (_ABSTRACT_CLASS_MODULES if SLOT_CLASSES[slot].abstract else (None,))
})
"""The slot of each generic class a target's type view writes a written container as, by its module and name (no
module for a builtin): a set under ``--use-generic-container-types`` is a frozen set, and a list or dict an abstract
``Sequence`` or ``Mapping``, so each class reads as the slot that writes it there."""

GivenContainerName: TypeAlias = Literal[
    "list",
    "List",
    "set",
    "Set",
    "frozenset",
    "FrozenSet",
    "Sequence",
    "MutableSequence",
    "tuple",
    "Tuple",
    "AbstractSet",
    "MutableSet",
    "dict",
    "Dict",
    "Mapping",
    "MutableMapping",
]
"""A container class an ``x-python-type`` names, by its name."""


class GivenContainer(NamedTuple):
    """The container an ``x-python-type`` that names a container class gives the node it types."""

    slot: ContainerSlot
    """The slot the node is in, which every decision on its kind reads."""
    written: ContainerClass
    """The class --use-x-python-type-as-written writes it as, around the item, key and value types of the schema."""
    variadic: bool = False
    """Whether the class is a tuple, written with ``...`` after its one item type."""


def _abstract(name: str, module: str | None = None) -> ContainerClass:
    """Return the abstract class ``name``, written alike with and without --use-standard-collections."""
    return ContainerClass(name, name, abstract=True, module=module)


GIVEN_CONTAINERS: Final[Mapping[str, GivenContainer]] = MappingProxyType({
    "list": GivenContainer("list", SLOT_CLASSES["list"]),
    "List": GivenContainer("list", SLOT_CLASSES["list"]),
    "set": GivenContainer("set", SLOT_CLASSES["set"]),
    "Set": GivenContainer("set", SLOT_CLASSES["set"]),
    "frozenset": GivenContainer("frozen_set", SLOT_CLASSES["frozen_set"]),
    "FrozenSet": GivenContainer("frozen_set", SLOT_CLASSES["frozen_set"]),
    "Sequence": GivenContainer("sequence", SLOT_CLASSES["sequence"]),
    "MutableSequence": GivenContainer("sequence", _abstract("MutableSequence")),
    "tuple": GivenContainer("list", SLOT_CLASSES["tuple"], variadic=True),
    "Tuple": GivenContainer("list", SLOT_CLASSES["tuple"], variadic=True),
    "AbstractSet": GivenContainer("frozen_set", _abstract("AbstractSet", "typing")),
    "MutableSet": GivenContainer("set", _abstract("MutableSet")),
    "dict": GivenContainer("dict", SLOT_CLASSES["dict"]),
    "Dict": GivenContainer("dict", SLOT_CLASSES["dict"]),
    "Mapping": GivenContainer("mapping", SLOT_CLASSES["mapping"]),
    "MutableMapping": GivenContainer("mapping", _abstract("MutableMapping")),
})
"""The container each container class an ``x-python-type`` names gives a node, by the class's name.

The slot is the one the class stands in for (a tuple in the list's, ``MutableSequence`` in the sequence's,
``AbstractSet`` in the frozen set's); ``AbstractSet`` is imported from ``typing``, as ``collections.abc`` spells it
``Set``."""


def given_container_of(name: str | None, slot: str) -> GivenContainer | None:
    """Return the container the class ``name`` an ``x-python-type`` names gives a node in ``slot``, if it gives one."""
    if name is None or (given := GIVEN_CONTAINERS[name]).slot != slot:
        return None
    return given


def hash_slot(slot: ContainerSlot, given: str | None, *, generic: bool) -> ContainerSlot:
    """Return the slot whose values a node in ``slot`` takes as it is written, which tells whether they hash.

    ``given`` is the class an ``x-python-type`` gives the node (``DataType.given_container``): a tuple's values are
    those of a ``tuple`` and any other class's those of its slot; without one the node is written as ``written_slot``.
    """
    if (found := given_container_of(given, slot)) is None:
        return written_slot(slot, generic=generic)
    return "tuple" if found.variadic else slot


BUILTIN_CONTAINERS: Final[Mapping[str, ContainerSlot]] = MappingProxyType({
    written.name: slot for slot, written in SLOT_CLASSES.items() if not written.abstract
})
"""The slot each builtin container class stands for, by its name (``list``, ``tuple``, ...)."""

LegacyKindRow: TypeAlias = Literal[
    "legacy-abstract-containers-as-unions",
    "legacy-collapse-beside-lists-and-dicts",
]
"""A decision that keeps the slots it read before its kind was named, which ``ContainerKind.without`` leaves out.

- `legacy-abstract-containers-as-unions`: the keywords a field's type consumes or enforces itself read a ``Sequence``
  or ``Mapping`` through to its members, like a union.
- `legacy-collapse-beside-lists-and-dicts`: under ``--field-constraints``, collapse keeps a constrained root model
  beside a list, a dict or a union only.
"""

_LEFT_OUT: Final[Mapping[LegacyKindRow, frozenset[ContainerSlot]]] = MappingProxyType({
    "legacy-abstract-containers-as-unions": frozenset(("sequence", "mapping")),
    "legacy-collapse-beside-lists-and-dicts": frozenset(("frozen_set", "set", "sequence", "mapping", "tuple")),
})
"""The slots each legacy row leaves out of the kind its decision names."""


@final
class ContainerKind:
    """The container slots that a decision treats alike; ``node in kind`` tells whether a node is in one of them."""

    __slots__ = ("_flags_of", "flags", "slots")

    def __init__(self, *slots: ContainerSlot) -> None:
        """Make the kind of one or more ``slots``."""
        self.slots = frozenset(slots)
        self.flags = frozenset(CONTAINER_FLAGS[slot] for slot in slots)
        names = tuple(CONTAINER_FLAGS[slot] for slot in slots)
        self._flags_of = attrgetter(*names) if len(names) > 1 else attrgetter(*names, *names)

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
