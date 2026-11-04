"""Wrap a type in ``SerializeAsAny`` by the final class hierarchy (--use-serialize-as-any-for-every-subclass, #5673).

Under ``--use-serialize-as-any`` a type that names a model is written ``SerializeAsAny[Model]`` when a subclass of the
model adds fields, so that an instance of the subclass keeps them when the field is serialized. Without this decision
the types read the models that their reference records as children, which counts direct subclasses only, keeps the
children of a model that reuse or collapse merged into another and leaves a subclass of the merged model out
(#5672). The decision reads the live models at B1, when the bases are final, and marks every live model: a type naming
it is wrapped when any subclass at any depth adds fields, and a type naming it through its module (``c.Base``) is
wrapped too (#5595).
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Protocol

from datamodel_code_generator._model_keys import model_key_of
from datamodel_code_generator.parser._phases import DecisionPass, DecisionWriter

if TYPE_CHECKING:
    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator.model._views import ModelView
    from datamodel_code_generator.parser._phases import FinalFacts

__all__ = ["SERIALIZE_AS_ANY_FOR_EVERY_SUBCLASS", "SerializeAsAnyWriter"]


class SerializeAsAnyWriter(DecisionWriter, Protocol):
    """The write of the decision: whether a type naming a model is wrapped in ``SerializeAsAny``."""

    def mark_serialized_as_any(self, model: ModelView, wrapped: bool) -> None:  # noqa: FBT001
        """Record whether a type naming ``model`` is wrapped in ``SerializeAsAny``."""


def _serialize_as_any_for_every_subclass(facts: FinalFacts, writer: SerializeAsAnyWriter) -> None:
    """Mark every live model by whether a live model below it in the class hierarchy declares fields of its own.

    The subclasses are read from the bases of the live models, keyed by model key, so a model that reuse made a
    subclass of its duplicate counts below that duplicate.
    """
    subclasses: defaultdict[ModelKey, list[ModelView]] = defaultdict(list)
    for model in facts.live_models:
        for base_class in model.base_classes:
            if (reference := base_class.reference) is not None:
                subclasses[model_key_of(reference)].append(model)
    extended: dict[ModelKey, bool] = {}

    def is_extended(key: ModelKey) -> bool:
        if (known := extended.get(key)) is not None:
            return known
        extended[key] = False
        extended[key] = any(
            subclass.fields or is_extended(model_key_of(subclass.reference)) for subclass in subclasses[key]
        )
        return extended[key]

    for model in facts.live_models:
        writer.mark_serialized_as_any(model, is_extended(model_key_of(model.reference)))


SERIALIZE_AS_ANY_FOR_EVERY_SUBCLASS = DecisionPass(
    {
        "name": "serialize_as_any_for_every_subclass",
        "issue": "#5673",
    },
    SerializeAsAnyWriter,
    _serialize_as_any_for_every_subclass,
    when=lambda parser: parser.use_serialize_as_any and parser.config.use_serialize_as_any_for_every_subclass,
)
