"""Where the parser builds each field and type node from: FIELD_ORIGIN and TYPE_ORIGIN.

The builders record them on the object when they build it, as bookkeeping (``scripts/fact_tables.py``) that no
generated output depends on; ``reference.py`` reads them (``field_origin_of``, ``type_origin_of``), and copies of a
field or node keep them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from datamodel_code_generator.reference import FIELD_ORIGIN_KEY, TYPE_ORIGIN_KEY, FieldOrigin

if TYPE_CHECKING:
    from datamodel_code_generator.model import DataModelFieldBase
    from datamodel_code_generator.reference import FieldOriginKind, SchemaLocation
    from datamodel_code_generator.types import DataType

__all__ = ["with_field_origin", "with_type_origin"]

_FieldT = TypeVar("_FieldT", bound="DataModelFieldBase")


def with_field_origin(field: _FieldT, kind: FieldOriginKind, location: SchemaLocation | None) -> _FieldT:
    """Record that ``field`` is built as ``kind`` from the schema object at ``location``, replacing a record."""
    field.__dict__[FIELD_ORIGIN_KEY] = FieldOrigin(kind, location)
    return field


def with_type_origin(data_type: DataType, location: SchemaLocation | None) -> DataType:
    """Record that ``data_type`` is built from the schema object at ``location``.

    A record a nested build made first is kept: the node came unchanged from that build's schema.
    """
    if location is not None:
        data_type.__dict__.setdefault(TYPE_ORIGIN_KEY, location)
    return data_type
