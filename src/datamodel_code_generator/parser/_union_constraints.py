"""Omit the keywords beside a union that a member cannot take, when no check is lost (``--field-constraints``, #6022).

The keywords a schema declares beside ``anyOf`` or ``oneOf`` are written on the field that holds the union
(``root: R1 | R2 = Field(..., min_length=2)``; ``parser/_value_keywords.py:union_sibling_constraints`` records them).
An output model that writes constraints as ``Field()`` arguments has Pydantic apply them to the value the union
validates to, so a keyword that a member's value cannot take (``DataTypeManager.member_constraint_keywords``: the
instance of a model, an ``int`` for ``min_length``) raises ``TypeError`` for valid values. The decision leaves such
keywords out of the field only when every member keeps the check without them in the rendered output: the member
enforces the keyword itself, as the root model a branch localized it into (``R1`` with ``min_length=2``) or the type
alias whose type holds it, whose call writes it (``written_constraints``, the constraints the renderer writes); or the
keyword applies to none of its values (an ``int`` for ``min_length``, an object model); or the member is None.
Otherwise the field stays as it is. A keyword the field's call does not write is no candidate, so an output that writes
no ``Field()`` arguments (msgspec, which leaves out what a union does not take with a warning) keeps its output.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from datamodel_code_generator._container_kinds import CONTAINERS
from datamodel_code_generator.parser._phases import DecisionPass, DecisionWriter, live_model_of

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from datamodel_code_generator.model._views import DataTypeView, FieldView, ModelView, ReferenceView
    from datamodel_code_generator.parser._phases import FinalFacts

__all__ = ["OMIT_UNTAKEN_UNION_CONSTRAINTS", "UnionConstraintsWriter"]


class UnionConstraintsWriter(DecisionWriter, Protocol):
    """The write of the decision: leave keywords out of the constraints of a field."""

    def drop_field_constraints(
        self,
        model: ModelView,
        field: FieldView,
        keywords: frozenset[str],
        members: Sequence[tuple[DataTypeView, object]],
    ) -> None:
        """Write the constraints of ``field`` of ``model`` without those of ``keywords``, ``members`` in place.

        Each member of ``members``, a type of the field, is written as the type paired with it.
        """


def _keeps_check(  # noqa: PLR0913, PLR0917
    facts: FinalFacts,
    model_of: Callable[[ReferenceView], ModelView | None],
    data_type: DataTypeView,
    keyword: str,
    value: object,
    seen: frozenset[int] = frozenset(),
) -> bool:
    """Return whether every value of ``data_type`` meets ``keyword`` at ``value`` when the field leaves it out.

    None does. A union does when every member does. A type alias or a root model does when the call of its field
    writes the keyword at that value (``written_constraints``), or when its type does. An enum, and a type that names
    no model, does when its values are of a kind whose keywords it takes and the keyword is not one of them. An object
    model does, as no such keyword applies to an object. A model the facts do not hold, or one its own type names
    again, does not.
    """
    if (reference := data_type.reference) is None:
        if data_type.data_types and data_type not in CONTAINERS:
            return all(_keeps_check(facts, model_of, member, keyword, value, seen) for member in data_type.data_types)
        keywords = facts.member_keywords(data_type, model_of)
        return keywords is None or (bool(keywords) and keyword not in keywords)
    if (model := None if id(reference) in seen else model_of(reference)) is None:
        return False
    match model.form:
        case "alias" | "root":
            fields = model.fields
            return bool(fields) and (
                fields[0].written_constraints.get(keyword) == value
                or _keeps_check(facts, model_of, fields[0].data_type, keyword, value, seen | {id(reference)})
            )
        case "enum":
            keywords = facts.member_keywords(data_type, model_of)
            return bool(keywords) and keyword not in keywords
    return True


def _members_constrained_in_place(
    facts: FinalFacts,
    model_of: Callable[[ReferenceView], ModelView | None],
    data_type: DataTypeView,
    untaken: Mapping[str, object],
) -> list[tuple[DataTypeView, object]] | None:
    """Return each member of ``data_type`` that loses a check of ``untaken`` with the type that holds it in place.

    A member keeps a check as ``_keeps_check`` says; one that loses some is written with them where it is a plain
    string (``FinalFacts.constrained_member``). None when a member that loses a check is anything else.
    """
    planned: list[tuple[DataTypeView, object]] = []
    pending = [data_type]
    while pending:
        current = pending.pop()
        if current.data_types and current not in CONTAINERS:
            pending.extend(reversed(current.data_types))
            continue
        if not (
            lost := {
                key: value for key, value in untaken.items() if not _keeps_check(facts, model_of, current, key, value)
            }
        ):
            continue
        if (written := facts.constrained_member(current, lost)) is None:
            return None
        planned.append((current, written))
    return planned


def _omit_untaken_union_constraints(facts: FinalFacts, writer: UnionConstraintsWriter) -> None:
    """Leave out of each field the keywords beside a union that its type does not take, when no check is lost.

    The keywords are those the call of the field writes (``written_constraints``) at the value the union declares them
    with. They all go when every member keeps each check without the field, or a member that would lose one is a plain
    string written in place, which then holds the keywords it loses (``O | constr(max_length=3)``, #6053); otherwise
    the field keeps them all, and its output stays as it was.
    """
    model_of = live_model_of(facts)
    for model in facts.live_models:
        for field in model.fields:
            if not (siblings := field.union_sibling_constraints):
                continue
            taken = facts.member_keywords(field.data_type, model_of) or frozenset()
            written = field.written_constraints
            untaken = {key: value for key, value in siblings.items() if key not in taken and written.get(key) == value}
            if (
                untaken
                and (members := _members_constrained_in_place(facts, model_of, field.data_type, untaken)) is not None
            ):
                writer.drop_field_constraints(model, field, frozenset(untaken), members)


OMIT_UNTAKEN_UNION_CONSTRAINTS = DecisionPass(
    {
        "name": "omit_untaken_union_constraints",
        "issue": "#6022",
    },
    UnionConstraintsWriter,
    _omit_untaken_union_constraints,
    when=lambda parser: parser.field_constraints,
)
