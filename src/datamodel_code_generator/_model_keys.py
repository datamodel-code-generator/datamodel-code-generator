"""Model keys: the identity of the model a `Reference` names, readable by every layer without importing `reference`.

The keys of the fields and data type nodes the generation store records live here too, so that the per-parse memos
(`_parse_memo.py`) can key by them without importing the parser.
"""

from __future__ import annotations

from typing import Final, NewType

MODEL_KEY_ATTRIBUTE: Final = "_model_key"

ModelKey = NewType("ModelKey", int)
"""The identity of the model a `Reference` names, unlike `id(reference)` never reused within a process."""
NodeKey = NewType("NodeKey", int)
"""The ``id`` of a data type node that the store records."""
FieldKey = NewType("FieldKey", int)
"""The ``id`` of a field that holds a recorded node."""


def model_key_of(reference: object) -> ModelKey:
    """Return the model key that ``reference`` took when it was built (`Reference.model_post_init`)."""
    return reference.__dict__[MODEL_KEY_ATTRIBUTE]
