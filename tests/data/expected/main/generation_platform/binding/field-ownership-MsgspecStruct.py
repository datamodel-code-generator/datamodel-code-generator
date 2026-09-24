from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType, field
from typing import Union



class A(Struct):
    root: Union[str, UnsetType] = UNSET
    shared_key: Union[str, UnsetType] = field(name='shared-key', default='A')



class B(A):
    left: Union[int, UnsetType] = UNSET
    shared_key: Union[str, UnsetType] = field(name='shared-key', default='B')



class Leaf(B):
    leaf_key: Union[float, UnsetType] = field(name='leaf-key', default=UNSET)