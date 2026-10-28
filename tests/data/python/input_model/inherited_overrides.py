"""Inherited field overrides and unchanged inheritance controls."""

from __future__ import annotations
import json
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class DefaultParent(BaseModel):
    before: int = 10
    value: int = 1
    after: int = 30


class DefaultChild(DefaultParent):
    value: int = 2
    added: int = 40


class SameChild(DefaultParent):
    value: int = 1
    added: int = 40


class RequiredChild(DefaultParent):
    value: int


class TypeChild(DefaultParent):
    value: str


class RequiredParent(BaseModel):
    value: int


class OptionalChild(RequiredParent):
    value: int = 2


class StrictParent(BaseModel):
    value: int = Field(ge=5)


class RelaxedChild(StrictParent):
    value: int = Field(ge=0)


class LooseParent(BaseModel):
    value: int = Field(ge=0)


class TightenedChild(LooseParent):
    value: int = Field(ge=5)


class AliasParent(BaseModel):
    value: int = Field(1, alias="old")


class AliasChild(AliasParent):
    value: int = Field(2, alias="new")


class UniqueParent(BaseModel):
    value: set[int]


class RepeatedChild(UniqueParent):
    value: list[int]


class GrandChild(DefaultChild):
    value: int = 3


class InheritedGrandChild(DefaultChild):
    extra: int = 50


class AliasSameChild(AliasParent):
    extra: int = 3


class AliasRequiredChild(AliasParent):
    value: int = Field(alias="new")


class AliasGrandChild(AliasChild):
    value: int = Field(3, alias="newest")


class AliasPairParent(AliasParent):
    other: int = Field(4, alias="old_other")


class AliasPairChild(AliasPairParent):
    value: int = Field(2, alias="new")
    other: int = Field(5, alias="new_other")


class AliasPairGrandChild(AliasPairChild):
    value: int = Field(3, alias="newest")
    other: int = Field(6, alias="newest_other")


class OtherParent(BaseModel):
    other: str = "other"
    value: int = 9


class MultipleChild(DefaultParent, OtherParent):
    value: int = 4


class UniquePairParent(BaseModel):
    value: set[int]
    retained: set[int]


class RepeatedPairChild(UniquePairParent):
    value: list[int]


class RepeatedGrandChild(RepeatedChild):
    extra: int = 1


class UniqueChild(RepeatedChild):
    value: set[int]


class NonemptyParent(BaseModel):
    value: list[int] = Field(min_length=2)


class EmptyChild(NonemptyParent):
    value: list[int]


class DifferentConstraintsParent(BaseModel):
    value: int = Field(ge=1, le=3)


class DifferentConstraintsChild(DifferentConstraintsParent):
    value: int = Field(ge=5, le=10)


from pydantic import AliasChoices, AliasPath


class ChoicesParent(BaseModel):
    value: int = Field(
        1,
        validation_alias=AliasChoices(AliasPath("nested", 0), "old"),
        serialization_alias="old",
    )


class ChoicesChild(ChoicesParent):
    value: int = Field(
        2,
        validation_alias=AliasChoices(AliasPath("new"), "fallback"),
        serialization_alias="new",
    )


class PathParent(BaseModel):
    value: int = Field(1, validation_alias=AliasChoices(AliasPath("nested", 0)))


class PathChild(PathParent):
    value: int = Field(2, validation_alias=AliasChoices(AliasPath("nested", 0)))


_ALIAS_SCHEMA = json.loads(
    (
        Path(__file__).parents[2]
        / "jsonschema"
        / "input_model_inherited_alias_winner.json"
    ).read_text()
)


class SharedAliasParent(BaseModel):
    values: frozenset[int] = Field(
        alias="shared",
        json_schema_extra=_ALIAS_SCHEMA["$defs"]["SharedAliasParent"]["properties"][
            "shared"
        ],
    )


class SharedAliasChild(SharedAliasParent):
    items: list[int] = Field(
        alias="shared", json_schema_extra=_ALIAS_SCHEMA["properties"]["shared"]
    )


class CallerBooleanNormal(StrictParent):
    label: int = 2
    model_config = ConfigDict(json_schema_extra={"x-python-field-overrides": True})


class CallerDictNormal(StrictParent):
    label: int = 2
    model_config = ConfigDict(
        json_schema_extra={"x-python-field-overrides": {"label": "value"}}
    )


class CallerStringOverride(StrictParent):
    value: int = Field(0, ge=0)
    model_config = ConfigDict(json_schema_extra={"x-python-field-overrides": "caller"})


class CallerListOverride(StrictParent):
    value: int = Field(0, ge=0)
    model_config = ConfigDict(json_schema_extra={"x-python-field-overrides": ["value"]})


class CallerDictOverride(DefaultParent):
    value: int = 2
    model_config = ConfigDict(
        json_schema_extra={"x-python-field-overrides": {"value": "before"}}
    )


class CallerInternalNormal(StrictParent):
    label: int = 2
    model_config = ConfigDict(
        json_schema_extra={"__python_field_overrides": {"label": "value"}}
    )


class CallerInternalOverride(StrictParent):
    value: int = Field(0, ge=0)
    model_config = ConfigDict(json_schema_extra={"__python_field_overrides": True})


class ClassVarAliasChild(SharedAliasParent):
    values: ClassVar[frozenset[int]] = frozenset()
    items: list[int] = Field(
        alias="shared", json_schema_extra=_ALIAS_SCHEMA["properties"]["shared"]
    )

class RawSchemaIntersection(StrictParent):
    pass
