"""
Custom module docstring header.

This is a multi-line docstring used for testing.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel


class Species(Enum):
    dog = 'dog'
    cat = 'cat'
    snake = 'snake'


class Pet(BaseModel):
    id: int
    name: str
    tag: str | None = None
    species: Species | None = None


class User(BaseModel):
    id: int
    name: str
    tag: str | None = None


class Event(BaseModel):
    name: str | float | int | bool | dict[str, Any] | list[str] | None = None
