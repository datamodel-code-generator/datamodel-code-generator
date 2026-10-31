"""Model keys: the identity of the model a `Reference` names, readable by every layer without importing `reference`.

The keys of the fields and data type nodes the generation store records live here too, so that the per-parse memos
(`_parse_memo.py`) can key by them without importing the parser.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, NewType, Protocol, TypeVar

if TYPE_CHECKING:
    from collections.abc import Mapping

MODEL_KEY_ATTRIBUTE: Final = "_model_key"
FIELDLESS_SITE_KEY: Final = "_fieldless_site"
"""Marks on a `Reference` the model of an allOf site that earlier releases wrote as a model without fields.

Under --collapse-root-models it inlines none of the models it names, and it keeps the regex engine its pattern needs
to itself.
"""

ModelKey = NewType("ModelKey", int)
"""The identity of the model a `Reference` names, unlike `id(reference)` never reused within a process."""
NodeKey = NewType("NodeKey", int)
"""The ``id`` of a data type node that the store records."""
FieldKey = NewType("FieldKey", int)
"""The ``id`` of a field that holds a recorded node."""
_ValueT = TypeVar("_ValueT")


class _ReferenceView(Protocol):
    """What `names_by_path` and `keyed_or_path` read of a `Reference`."""

    @property
    def path(self) -> str:
        """The path of the model the reference names."""

    @property
    def source(self) -> object:
        """The model the reference was built with, or None."""


def model_key_of(reference: object) -> ModelKey:
    """Return the model key that ``reference`` took when it was built (`Reference.model_post_init`)."""
    return reference.__dict__[MODEL_KEY_ATTRIBUTE]


def names_by_path(reference: _ReferenceView) -> bool:
    """Return whether ``reference`` names the model at its path instead of the model of its key.

    Row ``legacy-keyless-reference-by-path``: a reference that no model was built with (one built outside the
    resolver, such as by a parser extension) names the model at its path, as every reference did when models were
    told apart by path.
    """
    return reference.source is None


def keyed_or_path(
    reference: _ReferenceView, by_key: Mapping[ModelKey, _ValueT], by_path: Mapping[str, _ValueT]
) -> _ValueT | None:
    """Return the value kept for the model ``reference`` names, by its model key or, `names_by_path`, at its path."""
    if names_by_path(reference):
        return by_path.get(reference.path)
    return by_key.get(model_key_of(reference))
