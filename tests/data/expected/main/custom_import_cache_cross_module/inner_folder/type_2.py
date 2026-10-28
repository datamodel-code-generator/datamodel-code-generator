from __future__ import annotations

from typing import Union

from msgspec import UNSET, Struct, UnsetType

from .artificial_folder import type_1


class Type2(Struct, tag_field='type_', tag='b'):
    ref_type: Union[type_1.Type1, UnsetType] = UNSET
