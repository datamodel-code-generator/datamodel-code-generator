"""Deliberately invalid model codec typing cases; every error is checked by code and line."""

from dataclasses import dataclass

from pydantic import BaseModel

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
    _widened: ModelCodec[object] = codec
    NativeOutboundCodec(codec, context).snapshot(owner)
    _value: ModelValue[Owner] = NativeOutboundCodec(codec, context).from_wire(wire)
    _bare: Pet = EnvelopeOutboundCodec(codec, context).from_wire(wire)
    _native: NativeOutboundCodec[Pet] = EnvelopeOutboundCodec(codec, context)
    _sent: ModelValue[Pet] = EnvelopeOutboundCodec(codec, context).from_wire(wire)


@dataclass
class Tag:
    name: str


def exercise_structural(codec: StructuralModelCodec[Tag], context: CodecContext, wire: WireValue) -> None:
    _widened: ModelCodec[object] = codec
    _bare: Tag = codec.decode(wire, context)
    _value: ModelValue[Owner] = codec.snapshot(Tag("a"), context)
