from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType, field
from typing import Union



class BaseRequest(Struct):
    raw_note: Union[str, UnsetType] = field(name='raw-note', default='base')
    secret: Union[str, UnsetType] = UNSET



class BaseResponse(Struct):
    raw_note: Union[str, UnsetType] = field(name='raw-note', default='base')
    id: Union[int, UnsetType] = UNSET



class Base(Struct):
    raw_note: Union[str, UnsetType] = field(name='raw-note', default='base')
    id: Union[int, UnsetType] = UNSET
    secret: Union[str, UnsetType] = UNSET



class ChildRequest(Struct):
    raw_note: Union[str, UnsetType] = field(name='raw-note', default=UNSET)
    secret: Union[str, UnsetType] = UNSET



class ChildResponse(Struct):
    raw_note: Union[str, UnsetType] = field(name='raw-note', default=UNSET)
    id: Union[int, UnsetType] = UNSET



class Child(Base):
    raw_note: Union[str, UnsetType] = field(name='raw-note', default=UNSET)



class GrandChildRequest(Struct):
    raw_note: Union[str, UnsetType] = field(name='raw-note', default=UNSET)
    secret: Union[str, UnsetType] = UNSET



class GrandChildResponse(Struct):
    raw_note: Union[str, UnsetType] = field(name='raw-note', default=UNSET)
    id: Union[int, UnsetType] = UNSET



class GrandChild(Child):
    raw_note: Union[str, UnsetType] = field(name='raw-note', default=UNSET)