"""Omit the None a reference restates of a model that holds null itself (``--omit-redundant-reference-null``, #5081).

A field, item, map value or union member that names a model writes that model's None again (``Name | None``) when the
schema it references admits null. A model can hold that null as a value of its own when it is a root model or type
alias whose type admits None, or an enum with a None member; the decision drops the restated None at every type that
names such a model, so a null parses to the model (``Name(root=None)``). An object model, or an enum without a None
member, cannot hold null and keeps it. The None a use writes itself (``anyOf: [$ref, null]``, a nullable field) stays:
only the None marked as the target's (``GenerationStore.mark_target_null``) and the None of a nullable model go.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from datamodel_code_generator.parser._facts import Fact, Phase
from datamodel_code_generator.parser._pass_decls import PassDecl, PassKind, Scope
from datamodel_code_generator.parser._phases import DecisionPass, DecisionWriter

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from datamodel_code_generator.model._views import DataTypeView, FieldView, ModelView
    from datamodel_code_generator.parser._phases import FinalFacts

__all__ = ["OMIT_HELD_NULL", "HeldNullWriter"]


class HeldNullWriter(DecisionWriter, Protocol):
    """The write of the decision: drop the None a type restates of the model it names."""

    def drop_held_null(self, model: ModelView, field: FieldView, data_types: Sequence[DataTypeView]) -> None:
        """Drop the None each of ``data_types``, types of ``field`` of ``model``, restates of the model it names.

        The field gets a copy of its type tree that loses the Nones; the nodes other fields share stay as they are.
        """


def _types(data_type: DataTypeView) -> Iterator[DataTypeView]:
    """Yield ``data_type`` and every type nested in it: members, items and dict keys."""
    for member in data_type.data_types:
        yield from _types(member)
    if (dict_key := data_type.dict_key) is not None:
        yield from _types(dict_key)
    yield data_type


def _omit_held_null(facts: FinalFacts, writer: HeldNullWriter) -> None:
    """Drop the None of every type of a live model that names a live model holding null.

    Which models hold null is read for every model before the first write, so a root model whose root names a model
    holding null keeps holding null after its root loses the restated None.
    """
    holders = {id(model.reference) for model in facts.live_models if model.holds_null}
    if not holders:
        return
    for model in facts.live_models:
        for field in model.fields:
            if data_types := [
                data_type
                for data_type in _types(field.data_type)
                if (reference := data_type.reference) is not None and id(reference) in holders
            ]:
                writer.drop_held_null(model, field, data_types)


OMIT_HELD_NULL = DecisionPass(
    {
        "decl": PassDecl(
            "omit_held_null",
            Phase.DECIDE,
            Scope.GLOBAL,
            PassKind.TRANSFORM,
            reads=frozenset({Fact.FIELD_TYPES, Fact.NULLABLE, Fact.MODEL_KIND}),
            writes=frozenset({Fact.FIELD_TYPES, Fact.NULLABLE}),
            summary="Drops the None a type restates of the model it names when that model holds null itself.",
            condition="--omit-redundant-reference-null",
        ),
        "issue": "#5081",
    },
    HeldNullWriter,
    _omit_held_null,
    when=lambda parser: parser.omit_redundant_reference_null,
)
