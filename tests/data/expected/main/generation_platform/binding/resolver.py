from __future__ import annotations
from pydantic import BaseModel


class Pet(BaseModel):
    name: str


class Envelope(BaseModel):
    pet: Pet