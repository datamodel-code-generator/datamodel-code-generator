"""Model keys: the identity of the model a `Reference` names, readable by every layer without importing `reference`."""

from __future__ import annotations

from typing import Final, NewType

MODEL_KEY_ATTRIBUTE: Final = "_model_key"

ModelKey = NewType("ModelKey", int)
"""The identity of the model a `Reference` names, unlike `id(reference)` never reused within a process."""


def model_key_of(reference: object) -> ModelKey:
    """Return the model key that ``reference`` took when it was built (`Reference.model_post_init`)."""
    return reference.__dict__[MODEL_KEY_ATTRIBUTE]
