from __future__ import annotations

from msgspec import Struct


class Type5(Struct, tag_field='type_', tag='e'):
    pass
