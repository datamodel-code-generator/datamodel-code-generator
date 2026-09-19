from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType, field
from typing import List, TypeAlias, Union



class BaseArrayItem(Struct):
    ship_to: Union[str, UnsetType] = field(name='ship-to', default=UNSET)
    base_value: Union[int, UnsetType] = field(name='base-value', default=UNSET)



BaseArray: TypeAlias = List[BaseArrayItem]



Array: TypeAlias = BaseArray



class BaseTupleItem(Struct):
    first_key: Union[str, UnsetType] = field(name='first-key', default=UNSET)



class BaseTupleItem1(Struct):
    tail_key: Union[int, UnsetType] = field(name='tail-key', default=UNSET)



BaseTuple: TypeAlias = List[Union[BaseTupleItem, BaseTupleItem1]]



TupleValue: TypeAlias = BaseTuple



class BaseClosedItem(Struct):
    kept: Union[str, UnsetType] = UNSET



BaseClosed: TypeAlias = List[BaseClosedItem]



ClosedValue: TypeAlias = BaseClosed



class ObjectValue(Struct):
    nested_key: Union[str, UnsetType] = field(name='nested-key', default=UNSET)



BaseReferenced: TypeAlias = List[ObjectValue]



ReferencedValue: TypeAlias = BaseReferenced