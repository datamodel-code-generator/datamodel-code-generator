from __future__ import annotations

from msgspec import Struct


class Type1(Struct, tag_field='type_', tag='a'):
    pass
