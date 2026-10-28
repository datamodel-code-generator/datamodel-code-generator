"""The ``__dict__`` keys that tell where the None of a data type comes from.

``types.py:DataType`` reads them and the generation store writes them (``parser/generation.py``); they live in this
leaf module so that both import them without a cycle.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "HELD_NULL_DROPPED_KEY",
    "NAMED_NULL_REJECTED_KEY",
    "REFERENCE_NULL_KEY",
    "RENDER_PREPARATION_NULL_KEY",
    "TARGET_NULL_KEY",
]

REFERENCE_NULL_KEY: Final = "_has_reference_null"
"""The type's ``is_optional`` holds the None of the nullable model it names, written before render preparation."""
TARGET_NULL_KEY: Final = "_has_target_null"
"""The type's own None is the one the schema it references admits (``--omit-redundant-reference-null``)."""
RENDER_PREPARATION_NULL_KEY: Final = "_has_render_preparation_null"
"""Render preparation wrote into the type's ``is_optional`` the None its type hint renders."""
HELD_NULL_DROPPED_KEY: Final = "_is_held_null_dropped"
"""``--omit-redundant-reference-null`` dropped the None the type restates of the model it names."""
NAMED_NULL_REJECTED_KEY: Final = "_is_named_null_rejected"
"""The model the type named rejects null, before merging it into an equal model pointed the type at one admitting it."""
