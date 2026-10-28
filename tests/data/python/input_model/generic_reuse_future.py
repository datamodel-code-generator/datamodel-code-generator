"""Future annotations with stable exports and individually regenerated metadata."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Annotated, ForwardRef
from pydantic import BaseModel, Field
from typing_extensions import TypedDict
from tests.data.python.input_model.generic_reuse import Box, DefaultBox, Item, LocalEnumRoot, NamedBox, Outer, Pair, Positive
HiddenBox = RaisingBox = SafeExportedBox = Box[Annotated[int, Field(gt=3)]]
SafeExportedBox.__module__ = "unloaded_generic_module"
@dataclass
class FutureAliasRoot:
    box: Box[Positive]
@dataclass
class FutureExportRoot:
    box: SafeExportedBox
@dataclass
class FutureListRoot:
    box: Box[list[Positive]]
@dataclass
class FutureUnionRoot:
    box: Box[Positive | None]
@dataclass
class FutureInlineRoot:
    box: Box[Annotated[int, Field(gt=3)]]

class FutureInlineTyped(TypedDict):
    box: Box[Annotated[int, Field(gt=3)]]

class FutureInlineModel(BaseModel):
    box: Box[Annotated[int, Field(gt=3)]]

LocalEnumSpecialization = LocalEnumRoot.model_fields['validated'].annotation
@dataclass
class FutureLocalEnumRoot:
    box: LocalEnumRoot.model_fields['box'].annotation
    validated: LocalEnumSpecialization

@dataclass
class FutureMixedRoot:
    fallback: Pair[Annotated[int, Field(gt=3)], Item]
    defaulted: DefaultBox[Annotated[int, Field(gt=3)]]
    ordinary: NamedBox[int]
    qualified: Outer.Wrapped[int]
    alias: Box[Positive]

@dataclass
class FutureMixedReverseRoot:
    alias: Box[Positive]
    qualified: Outer.Wrapped[int]
    ordinary: NamedBox[int]
    defaulted: DefaultBox[Annotated[int, Field(gt=3)]]
    fallback: Pair[Annotated[int, Field(gt=3)], Item]

class MixedFutureRoot(BaseModel):
    mixed: FutureMixedRoot

import sys
import types
class NestedOwner:
    class NestedRoot(BaseModel):
        box: Box[Positive]

class _ExportModule(types.ModuleType):
    def __getattribute__(self, name):
        if name == "NestedRoot":
            return NestedOwner.NestedRoot
        if name == "HiddenBox":
            return Box[int]
        if name == "RaisingBox":
            raise AttributeError(name)
        return super().__getattribute__(name)
sys.modules[__name__].__class__ = _ExportModule

# Bind explicit forward-reference modules for the declared Pydantic 2.0 minimum.
for _model in (
    FutureAliasRoot, FutureExportRoot, FutureListRoot, FutureUnionRoot, FutureInlineRoot,
    FutureInlineTyped, FutureLocalEnumRoot, FutureMixedRoot, FutureMixedReverseRoot,
):
    for _name, _annotation in _model.__annotations__.items():
        _reference = ForwardRef(
            _annotation.__forward_arg__ if isinstance(_annotation, ForwardRef) else _annotation,
            module=__name__,
        )
        _model.__annotations__[_name] = _reference
        if hasattr(_model, '__dataclass_fields__'):
            _model.__dataclass_fields__[_name].type = _reference
