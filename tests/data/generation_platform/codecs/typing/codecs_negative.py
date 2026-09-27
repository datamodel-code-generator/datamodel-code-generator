"""Deliberately invalid model codec typing cases; every error is checked by code and line."""

from dataclasses import dataclass

from msgspec import Struct
from pydantic import BaseModel
from typing_extensions import TypedDict

from datamodel_code_generator._runtime.model_codecs.context import CodecContext
from datamodel_code_generator._runtime.model_codecs.outbound import EnvelopeOutboundCodec, ModelCodec, NativeOutboundCodec
from datamodel_code_generator._runtime.model_codecs.pydantic_v2 import PydanticModelCodec
from datamodel_code_generator._runtime.model_codecs.structural import StructuralModelCodec
from datamodel_code_generator._runtime.model_codecs.values import ModelValue
from datamodel_code_generator._runtime.model_codecs.wire import WireValue


class Pet(BaseModel):
    name: str


class Owner(BaseModel):
    name: str


def exercise(codec: PydanticModelCodec[Pet], context: CodecContext, wire: WireValue, owner: Owner) -> None:
    _widened: ModelCodec[object] = codec  # error
    NativeOutboundCodec(codec, context).snapshot(owner)  # error
    _value: ModelValue[Owner] = NativeOutboundCodec(codec, context).from_wire(wire)  # error
    _bare: Pet = EnvelopeOutboundCodec(codec, context).from_wire(wire)  # error
    _native: NativeOutboundCodec[Pet] = EnvelopeOutboundCodec(codec, context)  # error
    _sent: ModelValue[Pet] = EnvelopeOutboundCodec(codec, context).from_wire(wire)  # error


@dataclass
class Tag:
    name: str


def exercise_structural(codec: StructuralModelCodec[Tag], context: CodecContext, wire: WireValue) -> None:
    _widened: ModelCodec[object] = codec  # error
    _bare: Tag = codec.decode(wire, context)  # error
    _value: ModelValue[Owner] = codec.snapshot(Tag("a"), context)  # error


class TagRecord(TypedDict):
    name: str


def exercise_record(codec: StructuralModelCodec[TagRecord], context: CodecContext) -> None:
    codec.snapshot(Tag("a"), context)  # error
    _tag: Tag = codec.snapshot({"name": "a"}, context).value  # error


class TagStruct(Struct):
    name: str


def exercise_struct(codec: StructuralModelCodec[TagStruct], context: CodecContext) -> None:
    codec.snapshot(Tag("a"), context)  # error
    _tag: Tag = codec.snapshot(TagStruct("a"), context).value  # error
