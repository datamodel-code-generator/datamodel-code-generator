"""Per-parse memos: values computed from models, fields and data type nodes, kept with the epoch of their owner.

The generation store owns one `ParseMemo`, which `Parser.parse` makes current (`PARSE_MEMO`) while it runs, so the
model layer keeps the values it derives there instead of on the mutable objects. A value is kept under its
`MemoTable`, the key of its owner (`ModelKey`, `FieldKey` or `NodeKey`) and its arguments, with the epoch the owner had
in that table when it was computed. The methods that cleared the caches these memos replace bump the epoch instead
(`forget`), so a value computed before is not returned again. A slot holds the object it was computed for, which keeps
a field or node key from being reused within the parse; another object under the same key misses.

Outside a parse no memo is current: values are computed at each read, and the rendering of a union keeps the members
its nodes join in a memo of its own (`rendering_memo`).

The memo also keeps the derived values of a field (`ParseMemo.derived`: its render plan and its imports) with the
revisions they read: the field's own, its model's own (``own``) and the model's own and its fields' (``deep``) of each
model the computation read: the models its type references, through a root model or type alias the models its root
type references, the models an on-demand walk of the model graph reached while it computed (`note_reads`) and what
the derived values of other fields it read depend on. The store's mutations bump what they write (`bump_field`,
`bump_node`, `bump_model`, `bump_deep`); a node bump reaches the fields whose values read the node and the field that
holds it; a model's constructor bumps the model, as it names the model the source of its reference. A derived value is
returned while none of those revisions moved. Every bump moves the memo's clock, so a value whose revisions were seen to
hold at the current clock is returned without comparing them again.

Under the ``RENDER_MEMOS`` check, each value a memo returns is computed anew and compared; a value that differs is a
stale hit. The memos return what the caches they replace returned, stale or not, and two tables are known to return
stale values that today's output keeps (`LEGACY_ROWS`): their stale hits are recorded with a report file and never
raise.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, TypeAlias, TypeVar, final

from datamodel_code_generator._checks import CHECK_MODES, CheckArea, CheckMode, check_mode

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Hashable, Iterable, Mapping

    from datamodel_code_generator._model_keys import FieldKey, ModelKey, NodeKey

_V = TypeVar("_V")
MemoKey: TypeAlias = "ModelKey | FieldKey | NodeKey"


class MemoTable(Enum):
    """What a memo keeps; the value names it in check reports.

    A member hashes by identity: it is the first item of every memo key.
    """

    __hash__ = object.__hash__

    DEDUP_KEYS = "dedup keys"
    """The dedup keys of a model by its model key, one per class name it renders under and whether ``M`` stands in
    (``DataModel.get_dedup_key``)."""
    NESTED_MODEL_REQUIRED_FIELDS_BEFORE_NULLABILITY = "nested model required fields before nullability"
    """``NESTED_MODEL_REQUIRED_FIELDS`` as constructor ordering reads it by default for a Pydantic dataclass under
    ``--strip-default-none``: a field's default counts as the construction key gives it, kept from the first read
    (row ``legacy-nested-factory-order-before-nullability``)."""
    ENUM_MEMBER_INDEX = "enum member index"
    """The members of an enum by their exact value, by its model key."""
    COERCED_ENUM_MEMBER_INDEX = "coerced enum member index"
    """The members of an enum by their value as a string, by its model key."""
    SELF_REFERENCE = "self reference"
    """Whether the type of a field names the model that holds it, by its field key (``DataModelFieldBase``
    ``self_reference``)."""
    FIELD_RENDER_PLAN = "field render plan"
    """The render plan of a field built from its facts (``DataModelFieldBase._build_render_plan``), by its field key
    and what else it reads (``model/base.py:_plan_view``), kept with the revisions it read (`ParseMemo.derived`)."""
    FIELD_IMPORTS = "field imports"
    """The imports of a field (``DataModelFieldBase.imports``), by its field key and the plan they read, kept with the
    revisions they read (`ParseMemo.derived`)."""
    UNION_MEMBERS = "union members"
    """The member hints the last rendering of a union node joined, by its node key and the hint they joined to; an
    enclosing union that reads the same hint spreads them."""


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
@dataclass(slots=True)
class _Derived:
    """A derived value of a field, with the revisions it read (`ParseMemo.derived`)."""

    field: Any
    source: object
    revision: int
    parent: object
    own: int
    references: tuple[tuple[Any, object, int], ...]
    """Each reference the field's type held, also through root models and type aliases, its model then and that model's
    ``deep`` revision then."""
    reads: tuple[tuple[object, int], ...]
    """Each other model the computation read (`note_reads`), with its ``deep`` revision then."""
    value: Any
    verified: int
    """The memo's clock when the revisions it read were last seen to hold; a bump moves the clock."""


@final
class ParseMemo:
    """The memos of one parse, which the generation store owns."""

    __slots__ = (
        "_check",
        "_clock",
        "_deep",
        "_derived",
        "_epochs",
        "_joins",
        "_own",
        "_readers",
        "_reading",
        "_revisions",
        "_slots",
        "_walked",
    )

    def __init__(self) -> None:
        """Start with no values."""
        self._slots: dict[tuple[MemoTable, MemoKey], _Slot] = {}
        self._epochs: dict[tuple[MemoTable, MemoKey], int] = {}
        self._joins = 0
        self._clock = 0
        self._revisions: dict[int, int] = {}
        self._own: dict[int, int] = {}
        self._deep: dict[int, int] = {}
        self._readers: dict[int, dict[int, Any]] = {}
        self._derived: dict[tuple[MemoTable, int, Hashable], _Derived] = {}
        self._reading: list[tuple[Any, dict[int, object]]] = []
        self._check: tuple[Mapping[CheckArea, CheckMode], CheckMode | None] | None = None
        self._walked: dict[int, tuple[Any, int, tuple[tuple[Any, object, int], ...]]] = {}

    def bump_field(self, field: Any) -> None:
        """Move the revision of a field and the ``deep`` revision of its model, after a write to the field."""
        self._clock += 1
        self._revisions[id(field)] = self._clock
        if (parent := field.parent) is not None:
            self._deep[id(parent)] = self._clock

    def bump_model(self, model: object) -> None:
        """Move the ``own`` and ``deep`` revisions of a model, after a write to the model."""
        self._clock += 1
        self._own[id(model)] = self._deep[id(model)] = self._clock

    def bump_deep(self, model: object) -> None:
        """Move the ``deep`` revision of a model, after a write inside its fields that names no field."""
        self._clock += 1
        self._deep[id(model)] = self._clock

    def bump_node(self, node: object) -> None:
        """Bump the fields whose derived values read a data type node, or its member list, and the field holding it."""
        readers = self._readers
        for key in (id(node), id(getattr(node, "data_types", None))):
            if (fields := readers.get(key)) is not None:
                for field in tuple(fields.values()):
                    self.bump_field(field)
        current = node
        while (parent := getattr(current, "parent", None)) is not None:
            if hasattr(parent, "invalidate_semantic_caches"):
                self.bump_field(parent)
                return
            current = parent

    def revision(self, field: Any) -> tuple[int, int]:
        """Return the revisions of a field and of its model's own attributes."""
        return self._revisions.get(id(field), 0), self._own.get(id(field.parent), 0)

    def derived(  # noqa: PLR0913
        self,
        table: MemoTable,
        field: Any,
        view: Hashable,
        compute: Callable[[], _V],
        recompute: Callable[[], _V] | None = None,
        *,
        source: object = None,
    ) -> _V:
        """Return the value of a field kept in the table for ``view`` and read from ``source``, or compute and keep it.

        A kept value is returned while the field's revision, its model's ``own`` revision and the ``deep`` revision of
        each model it read when it was computed hold; the nodes of its type then become its readers. ``source`` (the
        plan imports read) is compared by identity. A value that another field's computation reads adds what it
        depends on to that computation.
        """
        key = (table, id(field), view)
        if (entry := self._derived.get(key)) is not None and self._holds(entry, field, source):
            if self._reading:
                self._read_by_outer(entry)
            return self._checked(table, field, entry.value, recompute or compute)
        reads: dict[int, object] = {}
        self._reading.append((field, reads))
        try:
            value = compute()
        finally:
            self._reading.pop()
        self._derived[key] = entry = self._derive(field, source, value, reads)
        if self._reading:
            self._read_by_outer(entry)
        return value

    def _holds(self, entry: _Derived, field: Any, source: object) -> bool:
        """Return whether a kept value was computed for ``field`` from ``source`` and the revisions it read hold.

        Every revision moves with the clock, so revisions seen to hold at the current clock still hold.
        """
        if entry.field is not field or entry.source is not source or entry.parent is not field.parent:
            return False
        if entry.verified == self._clock:
            return True
        if entry.revision == self._revisions.get(id(field), 0) and self._models_hold(entry):
            entry.verified = self._clock
            return True
        return False

    def _models_hold(self, entry: _Derived) -> bool:
        """Return whether the revisions of the models a kept value read hold: its model's own, the others' deep."""
        deep = self._deep
        return (
            entry.own == self._own.get(id(entry.parent), 0)
            and all(
                reference.source is model and deep.get(id(model), 0) == revision
                for reference, model, revision in entry.references
            )
            and (not entry.reads or all(deep.get(id(model), 0) == revision for model, revision in entry.reads))
        )

    def note_reads(self, models: Iterable[object]) -> None:
        """Record ``models`` as read by the derived value being computed, if any."""
        if reading := self._reading:
            reads = reading[-1][1]
            for model in models:
                reads[id(model)] = model

    def _read_by_outer(self, entry: _Derived) -> None:
        """Add what a derived value depends on to the computation that reads it, when that is another field's value.

        A field's imports read its own plan, which they are kept with (``source``). The ``deep`` revision of the
        field's model covers the field's own revision.
        """
        if (outer := self._reading[-1])[0] is not entry.field:
            reads = outer[1]
            reads[id(entry.parent)] = entry.parent
            for _, model, _ in entry.references:
                reads[id(model)] = model
            for model, _ in entry.reads:
                reads[id(model)] = model

    def _derive(self, field: Any, source: object, value: object, reads: dict[int, object]) -> _Derived:
        """Keep ``value`` with the revisions it read and the references of the field's type (`_references`)."""
        parent = field.parent
        deep = self._deep
        return _Derived(
            field,
            source,
            self._revisions.get(id(field), 0),
            parent,
            self._own.get(id(parent), 0),
            self._references(field),
            tuple((model, deep.get(id(model), 0)) for model in reads.values()),
            value,
            self._clock,
        )

    def _references(self, field: Any) -> tuple[tuple[Any, object, int], ...]:
        """Return the references of the field's type with their models and revisions, and make its nodes its readers.

        The type of a root model or type alias that the field's type names counts as read too, as the type the model
        stands for, and so on through the ones that type names. What the walk reads moves the clock when it changes,
        so the references walked for the field at the current clock are returned again.
        """
        field_key = id(field)
        if (walked := self._walked.get(field_key)) is not None and walked[0] is field and walked[1] == self._clock:
            return walked[2]
        readers = self._readers
        deep = self._deep
        references: list[tuple[Any, object, int]] = []
        aliases: list[Any] = []
        seen: set[int] = set()
        own = True
        stack = [field.data_type]
        while True:
            while stack:
                node = stack.pop()
                if own:
                    readers.setdefault(id(node), {})[field_key] = field
                if children := node.data_types:
                    if own:
                        readers.setdefault(id(children), {})[field_key] = field
                    stack.extend(children)
                if (reference := node.reference) is not None:
                    model = reference.source
                    references.append((reference, model, deep.get(id(model), 0)))
                    if (getattr(model, "IS_ROOT_MODEL", False) or getattr(model, "IS_ALIAS", False)) and (
                        id(model) not in seen
                    ):
                        seen.add(id(model))
                        aliases.append(model)
                if (dict_key := node.dict_key) is not None:
                    stack.append(dict_key)
            if not aliases:
                break
            own = False
            stack.extend(alias_field.data_type for alias_field in aliases.pop().fields)
        walked_references = tuple(references)
        self._walked[field_key] = field, self._clock, walked_references
        return walked_references

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
            return self._checked(table, owner, slot.values[args], recompute or compute)
        value = compute()
        epoch = self._epochs.get(slot_key, 0)
        if (slot := self._slots.get(slot_key)) is None or slot.owner is not owner or slot.epoch != epoch:
            self._slots[slot_key] = _Slot(owner, epoch, {args: value})
        else:
            slot.values[args] = value
        return value

    def _checked(self, table: MemoTable, owner: object, kept: _V, recompute: Callable[[], object]) -> _V:
        """Return a kept value, compared under the ``RENDER_MEMOS`` check with the value computed anew.

        The memo keeps the mode with the check flags it was read from and reads it again when other flags are current:
        a memo outlives a parse, and its parser may parse again under other flags.
        """
        modes = CHECK_MODES.get()
        if (check := self._check) is None or check[0] is not modes:
            check = self._check = (modes, modes.get(CheckArea.RENDER_MEMOS))
        if (mode := check[1]) is not None:
            _check_hit(mode, table, owner, kept, recompute)
        return kept

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


def note_reads(models: Iterable[object]) -> None:
    """Record ``models`` as read by the derived field value the current parse computes (`ParseMemo.note_reads`).

    A walk over the model graph that a computation asks for on demand records the models it reached.
    """
    if (memo := PARSE_MEMO.get()) is not None:
        memo.note_reads(models)


def bump_field(field: object) -> None:
    """Move the revision of a field in the current parse (`ParseMemo.bump_field`); outside a parse nothing is kept."""
    if (memo := PARSE_MEMO.get()) is not None:
        memo.bump_field(field)


def bump_deep(model: object) -> None:
    """Move the ``deep`` revision of a model in the current parse (`ParseMemo.bump_deep`)."""
    if (memo := PARSE_MEMO.get()) is not None:
        memo.bump_deep(model)


def bump_node(node: object) -> None:
    """Bump what reads a data type node in the current parse (`ParseMemo.bump_node`)."""
    if (memo := PARSE_MEMO.get()) is not None:
        memo.bump_node(node)


def bump_model(model: object) -> None:
    """Move the revisions of a model in the current parse (`ParseMemo.bump_model`)."""
    if (memo := PARSE_MEMO.get()) is not None:
        memo.bump_model(model)


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
