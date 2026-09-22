from __future__ import annotations
from enum import Enum
from msgspec import Struct
from typing import Literal, TypeAlias, Union


class RequestVersionEnum(str, Enum):
    v1 = 'v1'
    v2 = 'v2'



class RequestBase(Struct):
    version: RequestVersionEnum



class RequestV1(RequestBase):
    version: Literal['v1']
    request_id: str



class RequestV2(RequestBase):
    version: Literal['v2']



Request: TypeAlias = Union[RequestV1, RequestV2]