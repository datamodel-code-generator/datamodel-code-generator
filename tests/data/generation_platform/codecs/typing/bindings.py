"""Positive rendered model binding typing cases, checked against a package generated from the adapters fixture."""

from typing_extensions import assert_type

from adapted._generated import model_bindings as bindings
from adapted.model_codecs import (
    DecodedValue,
    EncodedParameterContribution,
    EnvelopeOutboundCodec,
    ModelCodec,
    ModelInput,
    ModelValue,
    NativeOutboundCodec,
    ProjectionIssue,
    RawParameter,
    Unset,
    WireValue,
    freeze_wire,
)
from adapted.models import Code, Keeper, Pet, Secretive, Tags


def exercise(wire: WireValue, pet: Pet, secretive: Secretive, raw: RawParameter) -> None:
    pets: ModelCodec[Pet] = bindings.codec_4()
    decoded = pets.decode(wire, bindings.CONTEXT_4)
    assert_type(decoded, DecodedValue[Pet])
    match decoded:
        case ModelValue():
            assert_type(decoded.value, Pet)
        case ModelInput():
            assert_type(decoded.issues, tuple[ProjectionIssue, ...])
    native = bindings.outbound_5()
    assert_type(native, NativeOutboundCodec[Pet])
    assert_type(native.from_wire({"id": 1, "name": "Rex"}), ModelValue[Pet])
    assert_type(native.snapshot(pet), ModelValue[Pet])
    envelope = bindings.outbound_8()
    assert_type(envelope, EnvelopeOutboundCodec[Secretive])
    assert_type(envelope.from_wire({"name": "s"}), DecodedValue[Secretive])
    assert_type(envelope.snapshot(secretive), ModelValue[Secretive])
    keepers: ModelCodec[Keeper] = bindings.codec_7()
    assert_type(keepers.encode(bindings.outbound_7().from_wire({"badge": "k"}), bindings.CONTEXT_7), WireValue)
    codes: ModelCodec[Code] = bindings.codec_2()
    assert_type(codes.decode("ab", bindings.CONTEXT_2), DecodedValue[Code])
    tags: ModelCodec[Tags] = bindings.codec_0()
    assert_type(tags.decode(wire, bindings.CONTEXT_0), DecodedValue[Tags])
    cookie = bindings.parameter_0()
    assert_type(cookie.encode(freeze_wire(["a,b", "c"]), bindings.CONTEXT_0), EncodedParameterContribution)
    assert_type(cookie.decode(raw, bindings.CONTEXT_0), WireValue | Unset)
