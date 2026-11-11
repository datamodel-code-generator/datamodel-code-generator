"""Write a property whose object default lacks required fields of its model without that default (#4828, #6551).

A JSON Schema ``default`` is an annotation that is not validated, so a schema can give a property an object default that
its own model rejects (``required_fields_a_default_lacks``). Under ``--omit-incomplete-model-defaults`` the decision
writes such a property as one without a default: the default None, and the ``nullable`` the property has without the
default, which the parse records where they differ (``nullable_without_default``: ``--strict-nullable`` reads a
property with a default as not null, and one without as undecided). It runs last among the decisions, so the decisions
before it read the default the schema gives, and the constructor ordering then places the field as one without a
default. The warning of render preparation about such defaults reads them through ``required_fields_a_default_lacks``
(``parser/_passes/_shared.py``) too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from datamodel_code_generator.parser._passes._shared import required_fields_a_default_lacks
from datamodel_code_generator.parser._phases import DecisionPass, DecisionWriter, live_model_of

if TYPE_CHECKING:
    from datamodel_code_generator.model._views import FieldView, ModelView
    from datamodel_code_generator.parser._phases import FinalFacts

__all__ = ["OMIT_INCOMPLETE_MODEL_DEFAULTS", "IncompleteDefaultsWriter"]


class IncompleteDefaultsWriter(DecisionWriter, Protocol):
    """The write of the decision: a field written without its default."""

    def omit_default(self, model: ModelView, field: FieldView) -> None:
        """Write ``field`` of ``model`` without its default: the default None and its ``nullable`` without it."""


def _omit_incomplete_model_defaults(facts: FinalFacts, writer: IncompleteDefaultsWriter) -> None:
    """Write each field of a live model whose object default lacks required fields of its model without the default.

    The models a reference names are the live models, by model key. A write changes the default and the nullability
    of the field it writes, and the reading of another field reads neither, so the order of the writes does not matter.
    """
    model_of = live_model_of(facts)
    for model in facts.live_models:
        for field in model.fields:
            if required_fields_a_default_lacks(field, model_of):
                writer.omit_default(model, field)


OMIT_INCOMPLETE_MODEL_DEFAULTS = DecisionPass(
    {
        "name": "omit_incomplete_model_defaults",
        "issue": "#6551",
    },
    IncompleteDefaultsWriter,
    _omit_incomplete_model_defaults,
    when=lambda parser: parser.omit_incomplete_model_defaults,
)
