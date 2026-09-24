from __future__ import annotations
from typing import List, Optional, Union
from pydantic import BaseModel, RootModel


class Choice1(BaseModel):
    a: Optional[str] = None


class Choice2(BaseModel):
    b: Optional[int] = None


class Choice(RootModel[Union[Choice1, Choice2]]):
    root: Union[Choice1, Choice2]


class BaseArrayItem(BaseModel):
    a: Optional[str] = None


class BaseArray(RootModel[List[BaseArrayItem]]):
    root: List[BaseArrayItem]


class ArrayItem(BaseModel):
    a: Optional[str] = None
    b: Optional[bool] = None


class Array(RootModel[List[ArrayItem]]):
    root: List[ArrayItem]


