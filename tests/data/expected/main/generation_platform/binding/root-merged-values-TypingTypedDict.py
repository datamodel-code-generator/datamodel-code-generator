from __future__ import annotations
from typing_extensions import NotRequired, TypedDict
from typing import List, TypeAlias, Union




BaseArrayItem = TypedDict('BaseArrayItem', {
    'ship-to': NotRequired[str],
    'base-value': NotRequired[int],})




BaseArray: TypeAlias = List[BaseArrayItem]



Array: TypeAlias = BaseArray




BaseTupleItem = TypedDict('BaseTupleItem', {
    'first-key': NotRequired[str],})





BaseTupleItem1 = TypedDict('BaseTupleItem1', {
    'tail-key': NotRequired[int],})




BaseTuple: TypeAlias = List[Union[BaseTupleItem, BaseTupleItem1]]



TupleValue: TypeAlias = BaseTuple



class BaseClosedItem(TypedDict, closed=True):
    kept: NotRequired[str]



BaseClosed: TypeAlias = List[BaseClosedItem]



ClosedValue: TypeAlias = BaseClosed




ObjectValue = TypedDict('ObjectValue', {
    'nested-key': NotRequired[str],})




BaseReferenced: TypeAlias = List[ObjectValue]



ReferencedValue: TypeAlias = BaseReferenced