"""Native discriminator union preserving both external child payloads."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Cat(BaseModel):
    """Cat payload with its required derived field."""

    kind: Literal["cat"]
    meow: str


class Dog(BaseModel):
    """Dog payload with its required derived field."""

    kind: Literal["dog"]
    bark: str


class Wrapper(BaseModel):
    """Native wrapper using the original discriminator property."""

    item: Cat | Dog = Field(discriminator="kind")
