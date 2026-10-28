"""Validation-wire collisions with real Pydantic omission and alias policies."""
from dataclasses import dataclass
from typing import Annotated, Any, Callable, Dict, FrozenSet, List
from pydantic import AliasChoices, AliasPath, BaseModel, ConfigDict, Field
from pydantic_core import PydanticOmit
from typing_extensions import TypedDict

class OmitSchema:
    def __get_pydantic_json_schema__(self, core, handler):
        raise PydanticOmit

class ListWins(BaseModel):
    first: int
    values: frozenset[int] = Field(alias='shared')
    items: list[int] = Field(alias='shared')
    last: str

class FrozenWins(BaseModel):
    first: int
    values: list[int] = Field(alias='shared')
    items: frozenset[int] = Field(alias='shared')
    last: str

class CallableWins(BaseModel):
    first: int
    value: Any = Field(alias='shared')
    callback: Callable[[int], int] = Field(alias='shared')
    last: str

class SkippedFrozen(BaseModel):
    first: int
    items: list[int] = Field(alias='shared')
    hidden: Annotated[frozenset[int], OmitSchema()] = Field(alias='shared')
    last: str

class SkippedList(BaseModel):
    first: int
    values: frozenset[int] = Field(alias='shared')
    hidden: Annotated[list[int], OmitSchema()] = Field(alias='shared')
    last: str

class SkippedCallable(BaseModel):
    first: int
    value: Any = Field(alias='shared')
    hidden: Annotated[Callable[[int], int], OmitSchema()] = Field(alias='shared')
    last: str

class AllSkipped(BaseModel):
    first: int
    values: Annotated[frozenset[int], OmitSchema()] = Field(alias='shared')
    items: Annotated[list[int], OmitSchema()] = Field(alias='shared')
    last: str

class ChoicesWins(BaseModel):
    first: int
    values: frozenset[int] = Field(validation_alias=AliasChoices(AliasPath('data', 'values'), 'shared'))
    items: list[int] = Field(alias='ignored', validation_alias=AliasChoices('shared', 'backup'), serialization_alias='out')
    last: str

class Populated(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    first: int
    values: frozenset[int] = Field(alias='shared')
    items: list[int] = Field(alias='shared')
    last: str

class PathWins(BaseModel):
    first: int
    values: frozenset[int] = Field(validation_alias=AliasPath('data', 'values'))
    items: list[int] = Field(alias='values')
    last: str

class Inner(BaseModel):
    first: int
    values: FrozenSet[int] = Field(alias='shared')
    items: List[int] = Field(alias='shared')
    last: str

class NestedWins(BaseModel):
    item: Inner

class Serialization(BaseModel):
    first: int
    values: frozenset[int] = Field(serialization_alias='shared')
    items: list[int] = Field(serialization_alias='shared')
    last: str

@dataclass
class PlainData:
    first: int
    values: frozenset[int]
    last: str

class PlainTyped(TypedDict):
    first: int
    values: frozenset[int]
    last: str


def hide_collision(schema):
    schema['properties'].pop('shared')

class HiddenCollision(BaseModel):
    model_config = ConfigDict(json_schema_extra=hide_collision)
    first: int
    values: frozenset[int] = Field(alias='shared')
    items: list[int] = Field(alias='shared')
    last: str

class NullableWins(BaseModel):
    model_config = ConfigDict(extra='forbid')
    first: int
    values: frozenset[int] | None = Field(alias='shared')
    items: list[int] | None = Field(alias='shared')
    last: str

class InlinedWins(BaseModel):
    item: Inner

    @classmethod
    def __get_pydantic_json_schema__(cls, core, handler):
        schema = handler.resolve_ref_schema(handler(core))
        schema['properties']['item'] = handler.resolve_ref_schema(schema['properties']['item'])
        schema['examples'] = [{'x-datamodel-code-generator-field-names': {'shared': 'user data'}}]
        return schema

class InheritedWins(ListWins):
    label: str = 'extra'


class UserExtensions(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        'x-datamodel-code-generator-field-names': ['user', 'root'],
        'x-datamodel-code-generator-field-name': {'user': 'root'},
        'examples': [{'x-datamodel-code-generator-field-name': 'example'}],
    })
    first: int
    values: frozenset[int] = Field(alias='shared', json_schema_extra={
        'x-datamodel-code-generator-field-name': ['user', 'property'],
        'x-datamodel-code-generator-field-names': False,
    })
    last: str
    metadata: Dict[str, str] = {'x-datamodel-code-generator-field-name': 'default'}


class CollisionExtensions(BaseModel):
    model_config = UserExtensions.model_config
    first: int
    values: FrozenSet[int] = Field(alias='shared')
    last: str
    metadata: Dict[str, str] = {'x-datamodel-code-generator-field-name': 'default'}
    items: List[int] = Field(alias='shared', json_schema_extra={
        'x-datamodel-code-generator-field-name': {'user': 'winner'},
        'x-datamodel-code-generator-field-names': 42,
    })


class ExtensionContainer(BaseModel):
    item: CollisionExtensions
