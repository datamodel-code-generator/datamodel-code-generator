from __future__ import annotations
from msgspec import Struct



class Record(Struct, tag='record', tag_field='kind', array_like=True, forbid_unknown_fields=True, omit_defaults=True, kw_only=True, frozen=True, rename={'wireName': 'wire_name'}):
    wireName: int