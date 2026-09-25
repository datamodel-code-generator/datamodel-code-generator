"""Hand-written models that stand outside, or deliberately disagree with, generated codec bindings."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CodecBase(BaseModel):
    """Stand in for a user base class whose serializer and validation behavior the generator cannot see."""


class AliasPet(BaseModel):
    """Accept the bound wire key through a plain validation alias."""

    id: int
    name: str
    status: str
    tag: str | None = None
    nick_name: str | None = Field(None, validation_alias="nick-name")
    password: str | None = None
    kind: str | None = None
    owner: object = None
    co_owner: object = Field(None, alias="co-owner")
    born: str | None = None
    price: str | None = None
    cost: str | None = None
    weight: float | None = None
    labels: set[str] | None = None
    scores: dict[str, int] | None = None
    visits: int | None = 0


class NamePet(BaseModel):
    """Read the nick name only by its Python name, so the bound wire key is not accepted."""

    id: int
    name: str
    status: str
    tag: str | None = None
    nick_name: str | None = None
    password: str | None = None
    kind: str | None = None
    owner: object = None
    co_owner: object = Field(None, alias="co-owner")
    born: str | None = None
    price: str | None = None
    cost: str | None = None
    weight: float | None = None
    labels: set[str] | None = None
    scores: dict[str, int] | None = None
    visits: int | None = 0
