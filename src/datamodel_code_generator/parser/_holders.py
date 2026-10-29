"""Who holds each data type node of the generation facts, and the ``data_types`` lists that several nodes share.

A node is held by a field (its ``data_type``), by another node (an item of its ``data_types`` or its ``dict_key``) or
by a model (a base class). The facts give one `HolderEdge` per occurrence they record, read from the recorded models
when asked for, so a node that several holders reach has several edges; a `ChildList` is a ``data_types`` list object
that more than one node holds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, TypeAlias, final

from datamodel_code_generator._records import record

if TYPE_CHECKING:
    from datamodel_code_generator._model_keys import FieldKey, ModelKey, NodeKey

HolderRole: TypeAlias = Literal["field", "nested", "dict_key", "base"]


@final
@record
class HolderEdge:
    """One place that holds a node: a field's type, an item or the key of another node, or a model's base class."""

    holder: NodeKey | FieldKey | ModelKey
    """The field for ``field``, the node for ``nested`` and ``dict_key``, the model for ``base``."""
    role: HolderRole
    index: int | None
    """The position in the holding node's ``data_types`` or the model's base classes; None for one-slot holders."""


@final
@record
class ChildList:
    """A ``data_types`` list object that more than one recorded node holds."""

    key: int
    """The ``id`` of the list."""
    owners: tuple[NodeKey, ...]
    """The nodes that hold the list, in the order a full rebuild's traversal first reaches them."""
