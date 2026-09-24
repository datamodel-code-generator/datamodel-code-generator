from __future__ import annotations
from typing import List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field, RootModel, constr


class BaseArrayItem(BaseModel):
    ship_to: Optional[str] = Field(None, alias='ship-to')
    base_value: Optional[int] = Field(None, alias='base-value')


class BaseArray(RootModel[List[BaseArrayItem]]):
    root: List[BaseArrayItem]


class ArrayItem(BaseModel):
    ship_to: Optional[constr(min_length=2)] = Field(None, alias='ship-to')
    base_value: Optional[int] = Field(None, alias='base-value')
    extra_value: Optional[bool] = Field(None, alias='extra-value')


class Array(RootModel[List[ArrayItem]]):
    root: List[ArrayItem]


class BaseTupleItem(BaseModel):
    first_key: Optional[str] = Field(None, alias='first-key')


class BaseTupleItem1(BaseModel):
    tail_key: Optional[int] = Field(None, alias='tail-key')


class BaseTuple(RootModel[List[Union[BaseTupleItem, BaseTupleItem1]]]):
    root: List[Union[BaseTupleItem, BaseTupleItem1]]


class TupleValueItem(BaseModel):
    first_key: Optional[str] = Field(None, alias='first-key')
    first_extra: Optional[bool] = Field(None, alias='first-extra')


class TupleValueItem1(BaseModel):
    tail_key: Optional[int] = Field(None, alias='tail-key')
    second_key: Optional[float] = Field(None, alias='second-key')


class TupleValue(RootModel[List[Union[TupleValueItem, TupleValueItem1]]]):
    root: List[Union[TupleValueItem, TupleValueItem1]] = Field(..., max_length=2)


class BaseClosedItem(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    kept: Optional[str] = None


class BaseClosed(RootModel[List[BaseClosedItem]]):
    root: List[BaseClosedItem]


class ClosedValueItem(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    kept: Optional[str] = None


class ClosedValue(RootModel[List[ClosedValueItem]]):
    root: List[ClosedValueItem]


class ObjectValue(BaseModel):
    nested_key: Optional[str] = Field(None, alias='nested-key')


class BaseReferenced(RootModel[List[ObjectValue]]):
    root: List[ObjectValue]


class ReferencedValueItem(BaseModel):
    nested_key: Optional[constr(min_length=1)] = Field(None, alias='nested-key')
    sibling_key: Optional[bool] = Field(None, alias='sibling-key')


class ReferencedValue(RootModel[List[ReferencedValueItem]]):
    root: List[ReferencedValueItem]


