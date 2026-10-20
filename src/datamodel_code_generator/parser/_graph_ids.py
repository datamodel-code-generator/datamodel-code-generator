"""Identities of the models of one parse, independent of the names and paths they are written under.

Every `Reference` takes the next value of a process-wide counter when it is built (`Reference.model_post_init`) and
keeps it in its `__dict__`, beside its schema location. Copies of a Reference (`deepcopy`, `model_copy`) keep the key of
their original; an unpickled one takes a new key, since the counter of the process that pickled it is not this one's.
Keys never reach output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, NewType, final

from datamodel_code_generator.reference import _MODEL_KEY, SCHEMA_OWNER_FRAMES_KEY, location_of

if TYPE_CHECKING:
    from datamodel_code_generator.reference import Reference, SchemaLocation

ModelKey = NewType("ModelKey", int)
"""The identity of the model a `Reference` names, unlike `id(reference)` never reused within a process."""


def model_key_of(reference: Reference) -> ModelKey:
    """Return the model key that ``reference`` took when it was built."""
    return reference.__dict__[_MODEL_KEY]


@final
@dataclass(frozen=True, slots=True, eq=False)
class BuildFrames:
    """The model builder frames open when a model is recorded, innermost first, and the model they build."""

    model: Reference
    """The model the frames stand for: the recorded model, or the base model of a read or write variant."""
    frames: tuple[list[Reference | None], ...]
    """One cell per frame: None until its builder returns, then the model that builder built, if it built one."""


def owner_of(reference: Reference) -> Reference | None:
    """Return the model whose builder built the model of ``reference``, or None for a root model.

    It is the innermost filled frame cell that holds another model than the one the frames stand for. A model built
    outside every builder (a definition, a document root, a `$ref` target) has no owner; neither has a model the parser
    did not record.
    """
    if (record := reference.__dict__.get(SCHEMA_OWNER_FRAMES_KEY)) is None:
        return None
    key = model_key_of(record.model)
    return next((owner for (owner,) in record.frames if owner is not None and model_key_of(owner) != key), None)


def is_root_or_owned(reference: Reference) -> bool:
    """Return whether the model of ``reference`` was recorded and has an owner or is a root model.

    A root model is recorded with no builder frame open but its own builders'. A model recorded inside a builder that
    built no model, or never recorded, breaks the rule.
    """
    if owner_of(reference) is not None:
        return True
    record = reference.__dict__.get(SCHEMA_OWNER_FRAMES_KEY)
    return record is not None and all(owner is not None for (owner,) in record.frames)


@final
@dataclass(frozen=True, slots=True)
class ModelIdentity:
    """Who a model is, whatever name and path it is written under."""

    key: ModelKey
    location: SchemaLocation | None
    """The schema object the model is built from (`location_of`), or None."""
    owner: ModelKey | None
    """The model whose builder built this one (`owner_of`), or None for a root model."""


def identity_of(reference: Reference) -> ModelIdentity:
    """Return the identity of the model of ``reference``, from what the Reference carries."""
    owner = owner_of(reference)
    return ModelIdentity(
        model_key_of(reference), location_of(reference), None if owner is None else model_key_of(owner)
    )
