from __future__ import annotations
from typing import List, Optional, TypeAlias, Union
from dataclasses import dataclass



@dataclass
class BaseArrayItem:
    ship_to: Optional[str] = None
    base_value: Optional[int] = None



BaseArray: TypeAlias = List[BaseArrayItem]



Array: TypeAlias = BaseArray



@dataclass
class BaseTupleItem:
    first_key: Optional[str] = None



@dataclass
class BaseTupleItem1:
    tail_key: Optional[int] = None



BaseTuple: TypeAlias = List[Union[BaseTupleItem, BaseTupleItem1]]



TupleValue: TypeAlias = BaseTuple



@dataclass
class BaseClosedItem:
    kept: Optional[str] = None



BaseClosed: TypeAlias = List[BaseClosedItem]



ClosedValue: TypeAlias = BaseClosed



@dataclass
class ObjectValue:
    nested_key: Optional[str] = None



BaseReferenced: TypeAlias = List[ObjectValue]



ReferencedValue: TypeAlias = BaseReferenced