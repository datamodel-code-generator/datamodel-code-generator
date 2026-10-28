from __future__ import annotations

from msgspec import Struct


class Type4(Struct, tag_field='type_', tag='d'):
    pass
