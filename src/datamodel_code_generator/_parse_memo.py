"""Per-parse memos: values computed from models, fields and data type nodes, kept with the epoch of their owner.

The generation store owns one `ParseMemo`, which `Parser.parse` makes current (`PARSE_MEMO`) while it runs, so the
model layer keeps the values it derives there instead of on the mutable objects. A value is kept under its
`MemoTable`, the key of its owner (`ModelKey`, `FieldKey` or `NodeKey`) and its arguments, with the epoch the owner had
in that table when it was computed. The methods that cleared the caches these memos replace bump the epoch instead
(`forget`), so a value computed before is not returned again. A slot holds the object it was computed for, which keeps
a field or node key from being reused within the parse; another object under the same key misses.

Outside a parse no memo is current: values are computed at each read, and the rendering of a union keeps the members
its nodes join in a memo of its own (`rendering_memo`).

Under the ``RENDER_MEMOS`` check, each value a memo returns is computed anew and compared; a value that differs is a
stale hit. The memos return what the caches they replace returned, stale or not, and two tables are known to return
stale values that today's output keeps (`LEGACY_ROWS`): their stale hits are recorded with a report file and never
raise.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, TypeAlias, TypeVar, final

from datamodel_code_generator._checks import CheckArea, CheckMode, check_mode

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Hashable, Mapping

    from datamodel_code_generator._model_keys import FieldKey, ModelKey, NodeKey

_V = TypeVar("_V")
MemoKey: TypeAlias = "ModelKey | FieldKey | NodeKey"


class MemoTable(Enum):
    """What a memo keeps; the value names it in check reports."""

    DEDUP_KEYS = "dedup keys"
    """The dedup keys of a model by its model key, one per class name it renders under and whether ``M`` stands in
    (``DataModel.get_dedup_key``)."""
    ENUM_MEMBER_INDEX = "enum member index"
    """The members of an enum by their exact value, by its model key."""
    COERCED_ENUM_MEMBER_INDEX = "coerced enum member index"
    """The members of an enum by their value as a string, by its model key."""
    SELF_REFERENCE = "self reference"
    """Whether the type of a field names the model that holds it, by its field key (``DataModelFieldBase``
    ``self_reference``)."""
    UNION_MEMBERS = "union members"
    """The member hints the last rendering of a union node joined, by its node key and the hint they joined to; an
    enclosing union that reads the same hint spreads them."""


LEGACY_ROWS: Final[Mapping[MemoTable, str]] = MappingProxyType({
    MemoTable.DEDUP_KEYS: "legacy-stale-dedup-keys",
    MemoTable.SELF_REFERENCE: "legacy-stale-self-reference",
})
"""The tables whose stale values today's output keeps, with the row that names that.

* ``legacy-stale-dedup-keys`` (``parser/_model_shapes.py``): deduplication and reuse compare the key a model kept from
  an earlier pass, which only render-cache invalidation drops.
* ``legacy-stale-self-reference``: under ``--collapse-root-models-name-strategy parent``, the root field of a wrapper
  whose name the inner model takes keeps whether its type named the wrapper before the rename, and whether its
  constraints apply reads that.
"""


@final
@dataclass(slots=True)
class _Slot:
    """The values one owner has in one table, computed at one epoch."""

    owner: object
    epoch: int
    values: dict[Hashable, Any]
    serial: int = 0
    """For ``UNION_MEMBERS``: the count of union renderings the memo had kept when this one was kept."""


@final
class ParseMemo:
    """The memos of one parse, which the generation store owns."""

    __slots__ = ("_epochs", "_joins", "_slots")

    def __init__(self) -> None:
        """Start with no values."""
        self._slots: dict[tuple[MemoTable, MemoKey], _Slot] = {}
        self._epochs: dict[tuple[MemoTable, MemoKey], int] = {}
        self._joins = 0

    def value(  # noqa: PLR0913, PLR0917
        self,
        table: MemoTable,
        key: MemoKey,
        owner: object,
        args: Hashable,
        compute: Callable[[], _V],
        recompute: Callable[[], _V] | None,
    ) -> _V:
        """Return the value kept for the owner and arguments, or compute and keep it.

        ``compute`` may invalidate the owner's values: a value is kept with the epoch the owner has once it returns, so
        the values kept before it are dropped then. ``recompute``, when given, is what the check computes anew instead
        of ``compute``, without its writes.
        """
        slot_key = (table, key)
        if (
            (slot := self._slots.get(slot_key)) is not None
            and slot.owner is owner
            and slot.epoch == self._epochs.get(slot_key, 0)
            and args in slot.values
        ):
            kept = slot.values[args]
            if (mode := check_mode(CheckArea.RENDER_MEMOS)) is not None:
                _check_hit(mode, table, owner, kept, recompute or compute)
            return kept
        value = compute()
        epoch = self._epochs.get(slot_key, 0)
        if (slot := self._slots.get(slot_key)) is None or slot.owner is not owner or slot.epoch != epoch:
            self._slots[slot_key] = _Slot(owner, epoch, {args: value})
        else:
            slot.values[args] = value
        return value

    def forget(self, table: MemoTable, key: MemoKey, owner: object) -> None:
        """Bump the owner's epoch in the table, so that no value it kept there is returned again."""
        slot_key = (table, key)
        if (slot := self._slots.get(slot_key)) is not None and slot.owner is owner:
            self._epochs[slot_key] = self._epochs.get(slot_key, 0) + 1

    @property
    def joins(self) -> int:
        """The count of union renderings kept so far, which `union_members` compares a kept rendering with."""
        return self._joins

    def keep_union_members(self, key: NodeKey, node: object, hint: str, members: tuple[str, ...]) -> None:
        """Keep the member hints a union node joined to ``hint``, in place of what it joined before."""
        self._joins += 1
        self._slots[MemoTable.UNION_MEMBERS, key] = _Slot(node, 0, {hint: members}, self._joins)

    def union_members(self, key: NodeKey, node: object, hint: str, joins: int) -> tuple[str, ...] | None:
        """Return the member hints the node last joined, when they joined to ``hint``, else None.

        ``joins`` is `joins` before the node rendered ``hint``. Under the check, members a rendering before that one
        kept are a stale hit: the node rendered ``hint`` without joining them.
        """
        if (
            (slot := self._slots.get((MemoTable.UNION_MEMBERS, key))) is None
            or slot.owner is not node
            or (members := slot.values.get(hint)) is None
        ):
            return None
        if slot.serial <= joins and (mode := check_mode(CheckArea.RENDER_MEMOS)) is not None:
            _stale(
                mode,
                f"the {MemoTable.UNION_MEMBERS.value} joined to {hint}",
                "were joined by an earlier rendering of the node",
                table=MemoTable.UNION_MEMBERS.name,
                hint=hint,
            )
        return members


PARSE_MEMO: Final[ContextVar[ParseMemo | None]] = ContextVar("parse_memo", default=None)
"""The memo of the parse that runs, or None outside a parse."""


def memoized(  # noqa: PLR0913, PLR0917
    table: MemoTable,
    key: MemoKey,
    owner: object,
    args: Hashable,
    compute: Callable[[], _V],
    recompute: Callable[[], _V] | None = None,
) -> _V:
    """Return what the current parse kept for the owner and arguments in the table (`ParseMemo.value`).

    Outside a parse the value is computed at each read.
    """
    if (memo := PARSE_MEMO.get()) is None:
        return compute()
    return memo.value(table, key, owner, args, compute, recompute)


def forget(table: MemoTable, key: MemoKey, owner: object) -> None:
    """Drop what the current parse kept for the owner in the table; outside a parse nothing is kept."""
    if (memo := PARSE_MEMO.get()) is not None:
        memo.forget(table, key, owner)


@contextmanager
def rendering_memo() -> Generator[ParseMemo, None, None]:
    """Yield a memo of the rendering's own, current while the block runs; outside a parse, a union rendering uses it."""
    memo = ParseMemo()
    token = PARSE_MEMO.set(memo)
    try:
        yield memo
    finally:
        PARSE_MEMO.reset(token)


def _owner_name(owner: object) -> str:
    """Name a model by its class name and a field by its model's class name and its name."""
    if (class_name := getattr(owner, "class_name", None)) is not None:
        return str(class_name)
    return f"{getattr(getattr(owner, 'parent', None), 'class_name', None)}.{getattr(owner, 'name', None)}"


def _same(kept: object, fresh: object) -> bool:
    """Compare two values; a mapping of model objects (an enum member index) holds the same objects."""
    if isinstance(kept, dict) and isinstance(fresh, dict):
        return kept.keys() == fresh.keys() and all(kept[name] is fresh[name] for name in kept)
    return kept == fresh


def _check_hit(mode: CheckMode, table: MemoTable, owner: object, kept: object, recompute: Callable[[], object]) -> None:
    """Compare a returned value with the value computed anew; a stale hit of a legacy row is only recorded."""
    if (row := LEGACY_ROWS.get(table)) is None:
        if not _same(kept, recompute()):
            name = _owner_name(owner)
            why = "differ from the value computed anew"
            _stale(mode, f"the {table.value} of {name}", why, table=table.name, owner=name)
        return
    if mode.report_path is not None and not _same(kept, recompute()):
        mode.record(
            f"{mode.area.value}: the {table.value} of {_owner_name(owner)} that row {row} reads differ from the value "
            "computed anew",
            table=table.name,
            row=row,
            owner=_owner_name(owner),
        )


def _stale(mode: CheckMode, subject: str, why: str, **details: object) -> None:
    from datamodel_code_generator import Error  # noqa: PLC0415

    mode.violation(Error, f"{subject} that the memo returns {why}", **details)
