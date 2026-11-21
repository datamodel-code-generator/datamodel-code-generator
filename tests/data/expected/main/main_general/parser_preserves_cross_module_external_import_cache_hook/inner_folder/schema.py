from __future__ import annotations

from typing import Union

from msgspec import Struct

from .. import type_4
from ..subfolder import type_5
from . import type_2
from .artificial_folder import type_1


class Type3(Struct, tag_field='type_', tag='c'):
    pass


class Response(Struct):
    inner: Union[type_1.Type1, type_2.Type2, Type3, type_4.Type4, type_5.Type5]
