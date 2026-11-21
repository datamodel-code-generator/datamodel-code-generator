"""Write one ``str`` for the str-like members of a msgspec union (``--merge-msgspec-str-like-union-members``).

msgspec decodes a union only if it holds at most one str-like member: ``str`` (with the str values of a ``Literal``,
which count as the same member), an Enum of str values, ``bytes``, ``bytearray``, the dates and times, ``UUID`` and
``Decimal``. A schema whose ``anyOf`` or ``oneOf`` holds two of them (``format: uuid`` beside a plain string, a
``format: uri`` string beside an empty one) writes a union that ``msgspec.convert`` refuses with ``TypeError`` for
every value; without the option the generator warns about it (``parser/_passes/imports.py:_msgspec_kind_clashes``).

With the option the decision writes one plain ``str`` in place of the first str-like member and leaves the others out,
so the union decodes every string that one of them accepts, as ``str``. A member that is a type alias or root model of
one str-like type, or of a union of only str-like members, counts as ``str`` and goes too, with the constraints of its
alias; the warning of ``model/_decisions/msgspec_str_like.py:merged_str_like_union_message`` names the members each
union loses. A merged member that admits null leaves ``None`` in the union, and a merged ``Literal`` keeps its values
that are no str in a ``Literal`` of their own. An Enum of no str value, and an alias of a container, count as no
str-like member. A union is left as it is, still warned about, when the str-like members are not all of the kinds the
decision can merge: an Enum of str values (whose class the union would lose), a member a ``x-python-type`` annotation
types, a str-like member built for an ``integer`` or ``number`` schema (``reference.py:_json_number_of``: a
``Decimal`` of ``format: decimal``, a ``timedelta`` of ``format: time-delta``, a ``datetime`` of ``format: date-time``,
directly or through an alias, whose JSON numbers one ``str`` would refuse), an alias of a union that mixes str-like and
other members, or an alias of a ``Literal`` of str and other values.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Protocol, TypeAlias

from datamodel_code_generator._container_kinds import CONTAINERS
from datamodel_code_generator.model._decisions.enum_kind import literal_value_kind
from datamodel_code_generator.model._decisions.msgspec_str_like import STR, msgspec_str_like_leaf
from datamodel_code_generator.parser._passes._shared import msgspec_union_members
from datamodel_code_generator.parser._passes.imports import _msgspec_enum_kinds
from datamodel_code_generator.parser._phases import DecisionPass, DecisionWriter, live_model_of
from datamodel_code_generator.reference import _json_number_of
from datamodel_code_generator.types import _union_members, _written_member, _written_union_members

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from datamodel_code_generator.model._views import DataTypeView, FieldView, ModelView, ReferenceView
    from datamodel_code_generator.parser._phases import FinalFacts

__all__ = ["MERGE_MSGSPEC_STR_LIKE_UNION_MEMBERS", "StrLikeUnionWriter"]

_Slot: TypeAlias = "tuple[DataTypeView, str | None]"
"""A written member of a union and the str-like member msgspec counts it as, or None."""


class _UnmergeableError(Exception):
    """The union holds a str-like member that the decision cannot merge."""


_NONE: Final = "None"

_Member: TypeAlias = "DataTypeView | str | tuple[bool | int | str, ...]"
"""A member a union is written with: a member it holds, a new plain type by name (``str`` or ``None``), or a new
``Literal`` of the values a merged ``Literal`` holds besides its str values."""

_Rewrite: TypeAlias = "tuple[DataTypeView, tuple[_Member, ...]]"
"""A union of a field's type tree and the members it is written with."""


class StrLikeUnionWriter(DecisionWriter, Protocol):
    """The write of the decision: one ``str`` for the str-like members of the unions of a field."""

    def merge_str_like_members(
        self,
        model: ModelView,
        field: FieldView,
        rewrites: Sequence[_Rewrite],
        merged: Sequence[DataTypeView],
    ) -> None:
        """Write the unions of ``rewrites``, types of ``field`` of ``model``, with the members each names.

        A name among them is a new plain type (``str``, ``None``) and a tuple a new ``Literal`` of its values;
        ``merged`` are the members the field loses, whose references are dropped. The field gets a copy of its type
        tree; the nodes other fields share stay as they are.
        """


def _union_roots(data_type: DataTypeView, *, spread: bool = False) -> Iterator[DataTypeView]:
    """Yield each union of a type tree that no enclosing union spreads, inner ones first.

    A union member that is a union of its own is written as members of the enclosing union, so only the enclosing one
    is a root; a container member, such as a list of several item types, is a root of its own. The unions are those
    of the msgspec decoding warnings (``msgspec_union_members``).
    """
    members = msgspec_union_members(data_type)
    for member in data_type.data_types:
        yield from _union_roots(member, spread=bool(members) and bool(_union_members(member)))
    if (dict_key := data_type.dict_key) is not None:
        yield from _union_roots(dict_key)
    if members and not spread:
        yield data_type


class _Classifier:
    """What msgspec counts each member of a union as, following the type aliases and root models it names."""

    __slots__ = ("_model_of",)

    def __init__(self, model_of: Callable[[ReferenceView], ModelView | None]) -> None:
        self._model_of = model_of

    def slot(self, member: DataTypeView, seen: frozenset[int] = frozenset()) -> str | None:
        """Return the str-like member ``member`` counts as, or None; raise ``_UnmergeableError`` where it cannot merge.

        A type alias or root model of one type counts as that type, and one of a union whose members are all
        str-like members it merges as ``str``; one of a container counts as none, as its items are no members. An
        Enum counts by the values of its members: one of no str value as none. A type alias of a ``Literal`` of str
        and other values, which the merge would lose, an Enum of str values, a type an ``x-python-type`` annotation
        writes and a str-like type built for a JSON number (``_json_number_of``) cannot merge.
        """
        member = _written_member(member)
        if (reference := member.reference) is None:
            if member.python_type is not None or (seen and _other_literals(member)):
                raise _UnmergeableError
            counted = None if member in CONTAINERS else msgspec_str_like_leaf(member)
            if counted is not None and _json_number_of(member):
                raise _UnmergeableError
            return counted
        if id(reference) in seen or (model := self._model_of(reference)) is None:
            return None
        match model.form:
            case "enum":
                if "str" in _msgspec_enum_kinds(model):
                    raise _UnmergeableError
            case "alias" | "root" if len(model.fields) == 1:
                value = _written_member(model.fields[0].data_type)
                if value in CONTAINERS:
                    return None
                if not msgspec_union_members(value):
                    return self.slot(value, seen | {id(reference)})
                slots = self._slots(value, seen | {id(reference)})
                if all(counted is not None for _, counted in slots):
                    return STR
                if any(counted is not None for _, counted in slots):
                    raise _UnmergeableError
        return None

    def _slots(self, union: DataTypeView, seen: frozenset[int] = frozenset()) -> list[_Slot]:
        """Return each written member of ``union`` with the str-like member it counts as."""
        return [(member, self.slot(member, seen)) for member in _written_union_members(msgspec_union_members(union))]

    def merge(self, union: DataTypeView) -> Sequence[DataTypeView]:
        """Return the written members of ``union`` that one ``str`` replaces, or nothing when it is left as it is.

        Those are its str-like members when msgspec counts them as more than one member and the decision can merge
        each str-like member it holds.
        """
        try:
            slots = self._slots(union)
        except _UnmergeableError:
            return ()
        if len({counted for _, counted in slots if counted is not None}) <= 1:
            return ()
        return [member for member, counted in slots if counted is not None]


def _other_literals(leaf: DataTypeView) -> tuple[bool | int | str, ...]:
    """Return the values of a ``Literal`` holding a str value besides its str values, which ``str`` does not take."""
    if not any(literal_value_kind(value) == STR for value in leaf.literals):
        return ()
    return tuple(value for value in leaf.literals if literal_value_kind(value) not in {STR, "none"})


def _rewrites(union: DataTypeView, merged: Sequence[DataTypeView]) -> list[_Rewrite]:
    """Return the member lists that write ``union`` with one ``str`` in place of ``merged``, inner unions first.

    The ``str`` takes the place of the first merged member as the union is written, followed by ``None`` when a
    merged member admits null and the union writes no ``None`` of its own; a merged ``Literal`` keeps its values
    besides its str values, which ``str`` does not take. A union of its own among the members that loses every member
    leaves the enclosing union.
    """
    lost = {id(member) for member in merged}
    keeps_null = any(member.admits_none for member in merged) and not any(
        member.type == _NONE
        for member in _written_union_members(msgspec_union_members(union))
        if id(member) not in lost
    )
    rewrites: list[_Rewrite] = []
    placed = False

    def rewrite(owner: DataTypeView) -> tuple[_Member, ...]:
        nonlocal placed
        members: list[_Member] = []
        for member in owner.data_types:
            if id(member) in lost:
                if not placed:
                    placed = True
                    members.extend((STR, _NONE) if keeps_null else (STR,))
                if member.reference is None and (others := _other_literals(_written_member(member))):
                    members.append(others)
            elif _union_members(member) and any(id(leaf) in lost for leaf in _written_union_members(member.data_types)):
                if inner := rewrite(member):
                    rewrites.append((member, inner))
                    members.append(member)
            else:
                members.append(member)
        return tuple(members)

    rewrites.append((union, rewrite(union)))
    return rewrites


def _merge_msgspec_str_like_union_members(facts: FinalFacts, writer: StrLikeUnionWriter) -> None:
    """Write one ``str`` for the str-like members of every union of a live model that msgspec refuses for them.

    Every union is decided on the types before the first write, so a union that names a type alias reads the alias's
    value as the schema gives it, whichever model comes first.
    """
    classifier = _Classifier(live_model_of(facts))
    planned = [
        (model, field, merges)
        for model in facts.live_models
        for field in model.fields
        if (
            merges := [
                (union, merged) for union in _union_roots(field.data_type) if (merged := classifier.merge(union))
            ]
        )
    ]
    for model, field, merges in planned:
        writer.merge_str_like_members(
            model,
            field,
            [rewrite for union, merged in merges for rewrite in _rewrites(union, merged)],
            [member for _, merged in merges for member in merged],
        )


MERGE_MSGSPEC_STR_LIKE_UNION_MEMBERS: Final[DecisionPass[StrLikeUnionWriter]] = DecisionPass(
    {
        "name": "merge_msgspec_str_like_union_members",
        "issue": "fix-msgspec-str-like-unions",
    },
    StrLikeUnionWriter,
    _merge_msgspec_str_like_union_members,
    when=lambda parser: (
        parser.merge_msgspec_str_like_union_members and parser.data_model_type.REQUIRES_TAGGED_UNION_DISCRIMINATOR
    ),
)
"""The decision; it runs for msgspec output (``REQUIRES_TAGGED_UNION_DISCRIMINATOR``) under its option."""
