"""Positive model codec typing cases, checked with both pinned checkers."""

from pydantic import BaseModel
from typing_extensions import assert_type

from datamodel_code_generator._runtime.model_codecs.context import CodecContext
from datamodel_code_generator._runtime.model_codecs.outbound import (
    EnvelopeOutboundCodec,
    ModelCodec,
    NativeOutboundCodec,
    OutboundCodec,
)
from datamodel_code_generator._runtime.model_codecs.pydantic_v2 import PydanticModelCodec
from datamodel_code_generator._runtime.model_codecs.values import DecodedValue, ModelInput, ModelValue, ProjectionIssue
from datamodel_code_generator._runtime.model_codecs.wire import PresenceTree, WireValue, presence_of


class Pet(BaseModel):
    name: str


def exercise(
    codec: PydanticModelCodec[Pet],
    selected: NativeOutboundCodec[Pet] | EnvelopeOutboundCodec[Pet],
    context: CodecContext,
    wire: WireValue,
    pet: Pet,
) -> None:
    protocol: ModelCodec[Pet] = codec
    decoded = protocol.decode(wire, context)
    assert_type(decoded, DecodedValue[Pet])
    match decoded:
        case ModelValue():
            assert_type(decoded.value, Pet)
        case ModelInput():
            assert_type(decoded.issues, tuple[ProjectionIssue, ...])
    assert_type(decoded.require_model(), Pet)
    presence: PresenceTree = presence_of({"name": "A"})
    native = NativeOutboundCodec(codec, context)
    sent = native.from_wire({"name": "A"})
    assert_type(sent, ModelValue[Pet])
    assert_type(native.snapshot(pet, presence=presence), ModelValue[Pet])
    envelope = EnvelopeOutboundCodec(codec, context)
    assert_type(envelope.from_wire(wire), DecodedValue[Pet])
    assert_type(envelope.snapshot(pet), ModelValue[Pet])
    outbound: OutboundCodec[Pet] = envelope
    assert_type(outbound.from_wire(wire), DecodedValue[Pet])
    assert_type(selected.from_wire(wire), ModelValue[Pet] | DecodedValue[Pet])
    assert_type(selected.snapshot(pet), ModelValue[Pet])
    assert_type(codec.encode(pet, context), WireValue)
    assert_type(codec.encode(sent, context), WireValue)
    assert_type(codec.encode(envelope.from_wire(wire), context), WireValue)
