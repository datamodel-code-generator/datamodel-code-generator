from __future__ import annotations
from enum import Enum
from pydantic.dataclasses import dataclass
from pydantic import Field
from typing import Annotated, Literal, Union
from typing_extensions import TypeAliasType


class RequestVersionEnum(str, Enum):
    v1 = 'v1'
    v2 = 'v2'



@dataclass
class RequestBase:
    version: RequestVersionEnum



@dataclass
class RequestV1(RequestBase):
    version: Literal['v1']
    request_id: str = Field(..., description='there is description', title='test title')



@dataclass
class RequestV2(RequestBase):
    version: Literal['v2']



Request = TypeAliasType("Request", Annotated[Union[RequestV1, RequestV2], Field(..., discriminator='version')])