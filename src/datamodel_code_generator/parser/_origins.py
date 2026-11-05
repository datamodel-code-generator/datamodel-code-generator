"""Where the parser builds each field and type node from: FIELD_ORIGIN and TYPE_ORIGIN, and UNIQUE_ITEMS.

The builders record them on the object when they build it, as bookkeeping (``scripts/fact_tables.py``);
``reference.py`` reads them (``field_origin_of``, ``type_origin_of``, ``unique_items_of``), and copies of a field or
node keep them. No generated output depends on the origins; UNIQUE_ITEMS is the ``uniqueItems`` of the schema a node is
built from, which --use-unique-items-as-set reads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from datamodel_code_generator.reference import (
    ENUM_LITERAL_KEY,
    FIELD_ORIGIN_KEY,
    TYPE_ORIGIN_KEY,
    UNIQUE_ITEMS_KEY,
    FieldOrigin,
)

if TYPE_CHECKING:
    from datamodel_code_generator.model import DataModelFieldBase
    from datamodel_code_generator.reference import FieldOriginKind, SchemaLocation
    from datamodel_code_generator.types import DataType

__all__ = ["with_enum_literal", "with_field_origin", "with_type_origin", "with_unique_items"]


def with_field_origin(
    field: DataModelFieldBase, kind: FieldOriginKind, location: SchemaLocation | None
) -> DataModelFieldBase:
    """Record that ``field`` is built as ``kind`` from the schema object at ``location``, replacing a record."""
    field.__dict__[FIELD_ORIGIN_KEY] = FieldOrigin(kind, location)
    return field


def with_type_origin(data_type: DataType, location: SchemaLocation | None) -> DataType:
    """Record that ``data_type``, and each node it holds that no nested build recorded, is built at ``location``.

    A record a nested build made first is kept, with the nodes beneath it: they came from that build's schema.
    """
    if location is None:
        return data_type
    pending = [data_type]
    while pending:
        if TYPE_ORIGIN_KEY in (node := pending.pop()).__dict__:
            continue
        node.__dict__[TYPE_ORIGIN_KEY] = location
        pending.extend(node.data_types)
        if node.dict_key is not None:
            pending.append(node.dict_key)
    return data_type


def with_enum_literal(data_type: DataType) -> DataType:
    """Record on ``data_type`` that it is the ``Literal`` of the values of an ``enum``."""
    data_type.__dict__[ENUM_LITERAL_KEY] = True
    return data_type


def with_unique_items(data_type: DataType, unique_items: bool | None) -> DataType:  # noqa: FBT001
    """Record on ``data_type`` that the schema it is built from declares ``uniqueItems: true`` (``unique_items``)."""
    if unique_items is True:
        data_type.__dict__[UNIQUE_ITEMS_KEY] = True
    return data_type
