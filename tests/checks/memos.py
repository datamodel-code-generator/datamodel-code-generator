"""``RENDER_MEMOS``: every value a per-parse memo returns equals the value computed anew where it is read.

The memos return what the caches they replace returned, stale or not; a value that differs is a stale hit. Three
tables are known to return stale values that today's output keeps (``LEGACY_ROWS``): their stale hits are recorded
with a report file and never raise.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from functools import wraps
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, final

from datamodel_code_generator._parse_memo import MemoTable
from tests.checks.areas import Area, Mode, mode

if TYPE_CHECKING:
    from collections.abc import Callable, Hashable, Mapping

LEGACY_ROWS: Final[Mapping[MemoTable, str]] = MappingProxyType({
    MemoTable.DEDUP_KEYS: "legacy-stale-dedup-keys",
    MemoTable.SELF_REFERENCE: "legacy-stale-self-reference",
    MemoTable.NESTED_MODEL_REQUIRED_FIELDS_BEFORE_NULLABILITY: "legacy-nested-factory-order-before-nullability",
})
"""The tables whose stale values today's output keeps, with the row that names that.

* ``legacy-stale-dedup-keys`` (``parser/_model_shapes.py``): deduplication and reuse compare the key a model kept from
  an earlier pass, which only render-cache invalidation drops.
* ``legacy-stale-self-reference``: under ``--collapse-root-models-name-strategy parent``, the root field of a wrapper
  whose name the inner model takes keeps whether its type named the wrapper before the rename, and whether its
  constraints apply reads that.
* ``legacy-nested-factory-order-before-nullability`` (``model/base.py:_nested_model_factory``): without
  ``--order-constructor-fields-by written-defaults``, the constructor ordering of a Pydantic dataclass under
  ``--use-default-factory-for-optional-nested-models --strip-default-none`` builds a nested model by factory only when
  the model had no required constructor field at the first read, a stripped ``None`` default counting as no default
  unless the field's type was optional then, before reference unions become nullable. The order is 0.86.0's; the
  option orders by the factories render preparation decides instead.
"""


def _dedup_key(owner: Any, args: tuple[str | None, bool]) -> object:
    """Render the dedup key that ``DataModel.get_dedup_key`` keeps for ``args``, without the writes that precede it."""
    class_name, use_default = args
    return owner._render_dedup_key(class_name if class_name is not None or not use_default else "M")


_RECOMPUTE: Final[Mapping[MemoTable, Callable[[Any, Any], object]]] = MappingProxyType({
    MemoTable.DEDUP_KEYS: _dedup_key,
})
"""What the check computes anew instead of the memo's ``compute``, for the tables whose ``compute`` writes."""


def value(original: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap ``ParseMemo.value``: a kept value it returns is compared with the value computed anew."""

    @wraps(original)
    def value(self: Any, table: MemoTable, key: Hashable, owner: object, args: Any, compute: Callable[[], Any]) -> Any:
        if (memo_mode := mode(Area.RENDER_MEMOS)) is None:
            return original(self, table, key, owner, args, compute)
        slot_key = (table, key)
        hit = (
            (slot := self._slots.get(slot_key)) is not None
            and slot.owner is owner
            and slot.epoch == self._epochs.get(slot_key, 0)
            and args in slot.values
        )
        kept = original(self, table, key, owner, args, compute)
        if hit:
            recompute = _RECOMPUTE.get(table)
            _check_hit(memo_mode, table, owner, kept, compute if recompute is None else lambda: recompute(owner, args))
        return kept

    return value


def derived(original: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap ``ParseMemo.derived``: a kept value it returns is compared with the value computed anew."""

    @wraps(original)
    def derived(
        self: Any, table: MemoTable, field: Any, view: Hashable, compute: Callable[[], Any], *, source: object = None
    ) -> Any:
        if (memo_mode := mode(Area.RENDER_MEMOS)) is None:
            return original(self, table, field, view, compute, source=source)
        hit = (entry := self._derived.get((table, id(field), view))) is not None and self._holds(entry, field, source)
        kept = original(self, table, field, view, compute, source=source)
        if hit:
            _check_hit(memo_mode, table, field, kept, compute)
        return kept

    return derived


@final
@dataclass(slots=True)
class _Joins:
    """The union renderings one memo kept in a parse.

    Their count, the count each node's last one made, and the count before each member of the unions whose member
    hints are spread now.
    """

    memo: object
    count: int = 0
    serials: dict[Hashable, int] = field(default_factory=dict)
    marks: list[int] = field(default_factory=list)


def _joins(memo_mode: Mode, memo: object) -> _Joins:
    if (joins := memo_mode.state.get(id(memo))) is None:
        joins = memo_mode.state[id(memo)] = _Joins(memo)
    return joins


def keep_union_members(original: Callable[..., None]) -> Callable[..., None]:
    """Wrap ``ParseMemo.keep_union_members``: count the rendering that kept the members."""

    @wraps(original)
    def keep_union_members(self: Any, key: Hashable, node: object, hint: str, members: tuple[str, ...]) -> None:
        original(self, key, node, hint, members)
        if (memo_mode := mode(Area.RENDER_MEMOS)) is not None:
            joins = _joins(memo_mode, self)
            joins.count += 1
            joins.serials[key] = joins.count

    return keep_union_members


def spread_union_member_hints(original: Callable[..., list[str]]) -> Callable[..., list[str]]:
    """Wrap ``types._spread_union_member_hints``: mark the count before the first member renders."""

    @wraps(original)
    def spread_union_member_hints(memo: Any, members: tuple[Any, ...]) -> list[str]:
        if (memo_mode := mode(Area.RENDER_MEMOS)) is None:
            return original(memo, members)
        joins = _joins(memo_mode, memo)
        joins.marks.append(joins.count)
        try:
            return original(memo, members)
        finally:
            joins.marks.pop()

    return spread_union_member_hints


def union_members(original: Callable[..., tuple[str, ...] | None]) -> Callable[..., tuple[str, ...] | None]:
    """Wrap ``ParseMemo.union_members``: members a rendering before the node rendered ``hint`` kept are a stale hit.

    The node rendered ``hint`` without joining them. The member after this one starts rendering at the count now.
    """

    @wraps(original)
    def union_members(self: Any, key: Hashable, node: object, hint: str) -> tuple[str, ...] | None:
        members = original(self, key, node, hint)
        if (memo_mode := mode(Area.RENDER_MEMOS)) is None:
            return members
        joins = _joins(memo_mode, self)
        if members is not None and joins.serials.get(key, 0) <= joins.marks[-1]:
            _stale(
                memo_mode,
                f"the {MemoTable.UNION_MEMBERS.value} joined to {hint}",
                "were joined by an earlier rendering of the node",
                table=MemoTable.UNION_MEMBERS.name,
                hint=hint,
            )
        joins.marks[-1] = joins.count
        return members

    return union_members


def _owner_name(owner: object) -> str:
    """Name a model by its class name and a field by its model's class name and its name."""
    if (class_name := getattr(owner, "class_name", None)) is not None:
        return str(class_name)
    return f"{getattr(getattr(owner, 'parent', None), 'class_name', None)}.{getattr(owner, 'name', None)}"


def _same(kept: object, fresh: object) -> bool:
    """Compare two values; a mapping of model objects (an enum member index) holds the same objects.

    Values that differ only by objects that equal nothing, such as a ``nan`` default computed again, write the same.
    """
    if isinstance(kept, dict) and isinstance(fresh, dict):
        return kept.keys() == fresh.keys() and all(kept[name] is fresh[name] for name in kept)
    return _equal(kept, fresh)


def _equal(kept: object, fresh: object) -> bool:
    """Compare two values item by item inside tuples, lists and dataclasses.

    Other values of one type are the same when they write the same text, such as ``nan`` or a runtime expression
    built again, which equal nothing but themselves.
    """
    if kept is fresh or kept == fresh:
        return True
    if isinstance(kept, (tuple, list)) and isinstance(fresh, (tuple, list)) and type(kept) is type(fresh):
        return len(kept) == len(fresh) and all(map(_equal, kept, fresh))
    if is_dataclass(kept) and type(kept) is type(fresh):
        return all(_equal(getattr(kept, field.name), getattr(fresh, field.name)) for field in fields(kept))
    return type(kept) is type(fresh) and repr(kept) == repr(fresh)


def _check_hit(memo_mode: Mode, table: MemoTable, owner: object, kept: object, recompute: Callable[[], object]) -> None:
    """Compare a returned value with the value computed anew; a stale hit of a legacy row is only recorded."""
    if (row := LEGACY_ROWS.get(table)) is None:
        if not _same(kept, recompute()):
            name = _owner_name(owner)
            why = "differ from the value computed anew"
            _stale(memo_mode, f"the {table.value} of {name}", why, table=table.name, owner=name)
        return
    if memo_mode.report_path is not None and not _same(kept, recompute()):
        memo_mode.record(
            f"{memo_mode.area.value}: the {table.value} of {_owner_name(owner)} that row {row} reads differ from the "
            "value computed anew",
            table=table.name,
            row=row,
            owner=_owner_name(owner),
        )


def _stale(memo_mode: Mode, subject: str, why: str, **details: object) -> None:
    from datamodel_code_generator import Error

    memo_mode.violation(Error, f"{subject} that the memo returns {why}", **details)
