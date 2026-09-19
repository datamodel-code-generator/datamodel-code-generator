from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict, RootModel
from enum import Enum


class Payload(BaseModel):
    model_config = ConfigDict(
        extra='allow',
    )
    __annotations__ = {
        '__pydantic_extra__': Dict[str, int],
    }
    normal: Optional[str] = None


class Dictionary(RootModel[Dict[str, str]]):
    root: Dict[str, str]


class Choice(Enum):
    first = 'first'
    second = 'second'


class Required(Payload):
    undeclared: Any


