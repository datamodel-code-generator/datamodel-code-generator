"""Identities of the models of one parse, independent of the names and paths they are written under.

Every `Reference` takes the next value of a process-wide counter when it is built (`Reference.model_post_init`) and
keeps it in its `__dict__`, beside its schema location. Copies of a Reference (`deepcopy`, `model_copy`) keep the key of
their original; an unpickled one takes a new key, since the counter of the process that pickled it is not this one's.
Keys never reach output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, NewType, final

from datamodel_code_generator.reference import _MODEL_KEY, location_of

if TYPE_CHECKING:
    from datamodel_code_generator.reference import Reference, SchemaLocation

ModelKey = NewType("ModelKey", int)
"""The identity of the model a `Reference` names, unlike `id(reference)` never reused within a process."""


def model_key_of(reference: Reference) -> ModelKey:
    """Return the model key that ``reference`` took when it was built."""
    return reference.__dict__[_MODEL_KEY]


@final
@dataclass(frozen=True, slots=True)
class ModelIdentity:
    """Who a model is, whatever name and path it is written under."""

    key: ModelKey
    location: SchemaLocation | None
    """The schema object the model is built from (`location_of`), or None."""
    owner: ModelKey | None
    """The model whose builder built this one; None, since the store does not record owners yet."""


def identity_of(reference: Reference) -> ModelIdentity:
    """Return the identity of the model of ``reference``, from what the Reference carries."""
    return ModelIdentity(model_key_of(reference), location_of(reference), None)
