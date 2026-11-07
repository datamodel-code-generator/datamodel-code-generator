"""Write one ``str`` for the str-like members of a msgspec union (``--merge-msgspec-str-like-union-members``).

msgspec decodes a union only if it holds at most one str-like member: ``str`` (with the str values of a ``Literal``,
which count as the same member), an Enum of str values, ``bytes``, ``bytearray``, the dates and times, ``UUID`` and
``Decimal``. A schema whose ``anyOf`` or ``oneOf`` holds two of them (``format: uuid`` beside a plain string, a
``format: uri`` string beside an empty one) writes a union that ``msgspec.convert`` refuses with ``TypeError`` for
every value; without the option the generator warns about it (``parser/_passes/imports.py:_msgspec_kind_clashes``).

With the option the decision writes one plain ``str`` in place of the first str-like member and leaves the others out,
so the union decodes every string that one of them accepts, as ``str``. A member that is a type alias or root model of
one str-like type counts as that type and goes too, with the constraints of its alias; the warning of
``model/_decisions/msgspec_str_like.py:merged_str_like_union_message`` names the members each union loses. A union is
left as it is, still warned about, when the str-like members are not all of the kinds the decision can merge: an Enum
of str values (whose class the union would lose), a member a ``x-python-type`` annotation types, or an alias of a union
that mixes str-like and other members.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Protocol, TypeAlias

from datamodel_code_generator._container_kinds import CONTAINERS, ITEM_CONTAINERS, MAPPINGS
from datamodel_code_generator._model_keys import model_key_of
from datamodel_code_generator.model._decisions.msgspec_str_like import STR, msgspec_str_like_leaf
from datamodel_code_generator.parser._phases import DecisionPass, DecisionWriter

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from datamodel_code_generator.model._views import DataTypeView, FieldView, ModelView
    from datamodel_code_generator.parser._phases import FinalFacts

__all__ = ["MERGE_MSGSPEC_STR_LIKE_UNION_MEMBERS", "StrLikeUnionWriter"]

_Slot: TypeAlias = "tuple[DataTypeView, str | None]"
"""A written member of a union and the str-like member msgspec counts it as, or None."""


class _Blocked(Exception):  # noqa: N818
    """The union holds a str-like member that the decision cannot merge."""


_Rewrite: TypeAlias = "tuple[DataTypeView, tuple[DataTypeView | None, ...]]"
"""A union of a field's type tree and the members it is written with; None stands for the one ``str``."""


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

        ``None`` among them is a new plain ``str``; ``merged`` are the members the field loses, whose references are
        dropped. The field gets a copy of its type tree; the nodes other fields share stay as they are.
        """


def _union_members(data_type: DataTypeView) -> Sequence[DataTypeView]:
    """Return the members of the union a type writes: its own, or the items of a list, set or map of several types.

    The items of a tuple are its positions, not a union.
    """
    if len(data_type.data_types) < 2:  # noqa: PLR2004
        return ()
    if data_type not in CONTAINERS or data_type in ITEM_CONTAINERS or data_type in MAPPINGS:
        return data_type.data_types
    return ()


def _is_spread(data_type: DataTypeView) -> bool:
    """Return whether a member of a union is a union of its own, whose members the enclosing union writes."""
    return len(data_type.data_types) > 1 and data_type not in CONTAINERS


def _spread(data_types: Sequence[DataTypeView]) -> Iterator[DataTypeView]:
    """Yield the members of a union as written, spreading each member that is a union of its own."""
    for data_type in data_types:
        if _is_spread(data_type):
            yield from _spread(data_type.data_types)
        else:
            yield data_type


def _unwrap(data_type: DataTypeView) -> DataTypeView:
    """Return the type a type of one member and no text of its own writes: that member."""
    while (
        len(data_type.data_types) == 1
        and data_type.type is None
        and data_type.reference is None
        and data_type not in CONTAINERS
    ):
        data_type = data_type.data_types[0]
    return data_type


def _union_roots(data_type: DataTypeView, *, spread: bool = False) -> Iterator[DataTypeView]:
    """Yield each union of a type tree that no enclosing union spreads, inner ones first.

    A union member that is a union of its own is written as members of the enclosing union, so only the enclosing one
    is a root; a container member, such as a list of several item types, is a root of its own.
    """
    members = _union_members(data_type)
    for member in data_type.data_types:
        yield from _union_roots(member, spread=bool(members) and _is_spread(member))
    if (dict_key := data_type.dict_key) is not None:
        yield from _union_roots(dict_key)
    if members and not spread:
        yield data_type


class _Classifier:
    """What msgspec counts each member of a union as, following the type aliases and root models it names."""

    __slots__ = ("_models",)

    def __init__(self, models: dict[object, ModelView]) -> None:
        self._models = models

    def slot(self, member: DataTypeView, seen: frozenset[int] = frozenset()) -> str | None:
        """Return the str-like member ``member`` counts as, or None; raise ``_Blocked`` for one that cannot merge.

        A type alias or root model of one type counts as that type, and one of a union whose members are all
        str-like members it merges as ``str``; an ``IntEnum`` counts as none. One of any other union that holds a
        str-like member, any other Enum and a type an ``x-python-type`` annotation writes cannot merge.
        """
        member = _unwrap(member)
        if (reference := member.reference) is None:
            if member.python_type is not None:
                raise _Blocked
            return None if member in CONTAINERS else msgspec_str_like_leaf(member)
        if id(reference) in seen or (model := self._models.get(model_key_of(reference))) is None:
            return None
        match model.form:
            case "enum" if model.value_base == "int":
                return None
            case "enum":
                raise _Blocked
            case "alias" | "root" if len(model.fields) == 1:
                value = _unwrap(model.fields[0].data_type)
                if not _union_members(value):
                    return self.slot(value, seen | {id(reference)})
                slots = self._slots(value, seen | {id(reference)})
                if all(counted is not None for _, counted in slots):
                    return STR
                if any(counted is not None for _, counted in slots):
                    raise _Blocked
        return None

    def _slots(self, union: DataTypeView, seen: frozenset[int] = frozenset()) -> list[_Slot]:
        """Return each written member of ``union`` with the str-like member it counts as."""
        return [(member, self.slot(member, seen)) for member in _spread(_union_members(union))]

    def merge(self, union: DataTypeView) -> Sequence[DataTypeView]:
        """Return the written members of ``union`` that one ``str`` replaces, or nothing when it is left as it is.

        Those are its str-like members when msgspec counts them as more than one member and the decision can merge
        each str-like member it holds.
        """
        try:
            slots = self._slots(union)
        except _Blocked:
            return ()
        if len({counted for _, counted in slots if counted is not None}) < 2:  # noqa: PLR2004
            return ()
        return [member for member, counted in slots if counted is not None]


def _rewrites(union: DataTypeView, merged: Sequence[DataTypeView]) -> list[_Rewrite]:
    """Return the member lists that write ``union`` with one ``str`` in place of ``merged``, inner unions first.

    The ``str`` takes the place of the first merged member as the union is written; a union of its own among the
    members that loses every member leaves the enclosing union.
    """
    lost = {id(member) for member in merged}
    rewrites: list[_Rewrite] = []
    placed = False

    def rewrite(owner: DataTypeView) -> tuple[DataTypeView | None, ...]:
        nonlocal placed
        members: list[DataTypeView | None] = []
        for member in owner.data_types:
            if id(member) in lost:
                if not placed:
                    placed = True
                    members.append(None)
            elif _is_spread(member) and any(id(leaf) in lost for leaf in _spread(member.data_types)):
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
    classifier = _Classifier({model_key_of(model.reference): model for model in facts.live_models})
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
