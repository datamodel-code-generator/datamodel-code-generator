from __future__ import annotations
from typing import List, Optional, Union
from pydantic import ConfigDict, Field
from pydantic.dataclasses import dataclass
from typing_extensions import TypeAliasType



@dataclass
class BaseArrayItem:
    ship_to: Optional[str] = Field(None, alias='ship-to')
    base_value: Optional[int] = Field(None, alias='base-value')



BaseArray = TypeAliasType("BaseArray", List[BaseArrayItem])



Array = TypeAliasType("Array", BaseArray)



@dataclass
class BaseTupleItem:
    first_key: Optional[str] = Field(None, alias='first-key')



@dataclass
class BaseTupleItem1:
    tail_key: Optional[int] = Field(None, alias='tail-key')



BaseTuple = TypeAliasType("BaseTuple", List[Union[BaseTupleItem, BaseTupleItem1]])



TupleValue = TypeAliasType("TupleValue", BaseTuple)



@dataclass(config=ConfigDict(extra='forbid'))
class BaseClosedItem:
    kept: Optional[str] = None



BaseClosed = TypeAliasType("BaseClosed", List[BaseClosedItem])



ClosedValue = TypeAliasType("ClosedValue", BaseClosed)



@dataclass
class ObjectValue:
    nested_key: Optional[str] = Field(None, alias='nested-key')



BaseReferenced = TypeAliasType("BaseReferenced", List[ObjectValue])



ReferencedValue = TypeAliasType("ReferencedValue", BaseReferenced)