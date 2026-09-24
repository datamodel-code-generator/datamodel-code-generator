from __future__ import annotations
from enum import Enum
from typing import TypeAlias, TypedDict, Union


class RequestVersionEnum(str, Enum):
    v1 = 'v1'
    v2 = 'v2'



class RequestBase(TypedDict):
    version: RequestVersionEnum



class RequestV1(RequestBase):
    version: RequestVersionEnum
    request_id: str



class RequestV2(RequestBase):
    pass



Request: TypeAlias = Union[RequestV1, RequestV2]