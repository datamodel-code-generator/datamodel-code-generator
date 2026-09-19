from __future__ import annotations
from typing import Annotated, List, Optional, Union
from pydantic.dataclasses import dataclass
from pydantic import Field
from typing_extensions import TypeAliasType



@dataclass
class PayloadItem:
    item: Optional[int] = None



Payload = TypeAliasType("Payload", Annotated[List[PayloadItem], Field(..., min_length=1)])



@dataclass
class Payload1:
    code: Optional[str] = None



@dataclass
class Parent:
    payload: Optional[Union[List[PayloadItem], Payload1]] = None



@dataclass
class Child(Parent):
    payload: Optional[Union[Payload, Payload1]] = None