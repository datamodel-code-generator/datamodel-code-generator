"""Where the parser builds each field and type node from: FIELD_ORIGIN and TYPE_ORIGIN.

The builders record them on the object when they build it, as bookkeeping (``scripts/fact_tables.py``) that no
generated output depends on; ``reference.py`` reads them (``field_origin_of``, ``type_origin_of``), and copies of a
field or node keep them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from datamodel_code_generator.reference import FIELD_ORIGIN_KEY, TYPE_ORIGIN_KEY, FieldOrigin

if TYPE_CHECKING:
    from datamodel_code_generator.model import DataModelFieldBase
    from datamodel_code_generator.reference import FieldOriginKind, SchemaLocation
    from datamodel_code_generator.types import DataType

__all__ = ["with_field_origin", "with_type_origin"]


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
