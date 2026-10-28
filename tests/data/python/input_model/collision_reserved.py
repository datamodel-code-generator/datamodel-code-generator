"""Names reserved before allocating colliding definition suffixes."""
from enum import Enum

from pydantic import BaseModel


class Data_4(BaseModel):
    fourth: bool


class Root_2(BaseModel):
    second: bool


class Root_4(BaseModel):
    fourth: bool


class Kind_2(Enum):
    VALUE = "second"
    OTHER = "other-second"


class Kind_4(Enum):
    VALUE = "fourth"
    OTHER = "other-fourth"
