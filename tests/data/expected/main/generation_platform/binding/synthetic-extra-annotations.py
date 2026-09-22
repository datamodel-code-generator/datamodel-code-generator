from __future__ import annotations
from typing import Any, Dict, Union
from pydantic.experimental.missing_sentinel import MISSING
from pydantic import BaseModel, ConfigDict, RootModel
from enum import Enum


class Payload(BaseModel):
    model_config = ConfigDict(
        extra='allow',
    )
    __annotations__ = {
        '__pydantic_extra__': Dict[str, int],
    }
    normal: Union[str, MISSING] = MISSING


class Dictionary(RootModel[Dict[str, str]]):
    root: Dict[str, str]


class Choice(Enum):
    first = 'first'
    second = 'second'


class Required(Payload):
    undeclared: Any


