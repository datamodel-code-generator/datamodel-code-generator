from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, RootModel, constr, model_validator
from enum import Enum


class Parent(BaseModel):
    flag: Optional[Any] = None
    other: Optional[Any] = None
    code: Optional[str] = None


class Child(Parent):
    flag: Optional[Any] = None
    other: Optional[constr(min_length=1)] = None
    code: Optional[constr(min_length=2)] = None


class Node(BaseModel):
    next: Optional[Node] = None


class Holder(BaseModel):
    node: Optional[Node] = None


class A(BaseModel):
    x: Optional[str] = None


class B(BaseModel):
    x: Optional[int] = None


class C(BaseModel):
    x: Optional[int] = None
    y: Optional[str] = None


class Extra(BaseModel):
    model_config = ConfigDict(
        extra='allow',
    )
    __annotations__ = {
        '__pydantic_extra__': Dict[str, List[str]],
    }
    a: Optional[str] = None


class Open(BaseModel):
    model_config = ConfigDict(
        extra='allow',
    )
    __annotations__ = {
        '__pydantic_extra__': Dict[str, Any],
    }
    a: Optional[str] = None


class Code(RootModel[str]):
    root: str


class Limited(RootModel[str]):
    model_config = ConfigDict(
        json_schema_extra={'enum': ['a', 'b']},
    )
    
    @classmethod
    def _json_schema_literal_key(cls, value: Any) -> Any:
        if isinstance(value, Enum):
            value = value.value
        if getattr(type(value), '__pydantic_root_model__', False):
            value = value.model_dump(mode='json')
        if isinstance(value, dict):
            return (
                'object',
                frozenset(
                    (key, cls._json_schema_literal_key(item))
                    for key, item in value.items()
                ),
            )
        if isinstance(value, list):
            return (
                'array',
                tuple(cls._json_schema_literal_key(item) for item in value),
            )
        if isinstance(value, bool):
            return ('boolean', value)
        if isinstance(value, (int, float)):
            return ('number', value)
        if isinstance(value, str):
            return ('string', value)
        return (type(value).__name__, value)
    
    @model_validator(mode='before')
    @classmethod
    def _validate_json_schema_literal(cls, value: Any) -> Any:
        if isinstance(value, Enum):
            value = value.value
        if getattr(type(value), '__pydantic_root_model__', False):
            value = value.model_dump(mode='json')
        candidate = cls._json_schema_literal_key(value)
        allowed_values = ['a', 'b']
        if not any(
            candidate == cls._json_schema_literal_key(allowed)
            for allowed in allowed_values
        ):
            raise ValueError('Value does not match an allowed JSON Schema literal')
        return value
    
    root: str = Field(..., max_length=5)


class Defaulted(BaseModel):
    a: Optional[str] = 'x'


Node.model_rebuild()