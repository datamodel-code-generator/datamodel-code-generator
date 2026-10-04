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
from adapted_models import Code, Keeper, Pet, Tags


def exercise(wire: WireValue, pet: Pet, keeper: Keeper, raw: RawParameter) -> None:
    pets: ModelCodec[Pet] = bindings.codec_5()
    decoded = pets.decode(wire, bindings.CONTEXT_5)
    assert_type(decoded, DecodedValue[Pet])
    match decoded:
        case ModelValue():
            assert_type(decoded.value, Pet)
        case ModelInput():
            assert_type(decoded.issues, tuple[ProjectionIssue, ...])
    native = bindings.outbound_6()
    assert_type(native, NativeOutboundCodec[Keeper])
    assert_type(native.from_wire({"badge": "k"}), ModelValue[Keeper])
    assert_type(native.snapshot(keeper), ModelValue[Keeper])
    envelope = bindings.outbound_4()
    assert_type(envelope, EnvelopeOutboundCodec[Pet])
    assert_type(envelope.from_wire({"name": "Rex"}), DecodedValue[Pet])
    assert_type(envelope.snapshot(pet), ModelValue[Pet])
    keepers: ModelCodec[Keeper] = bindings.codec_6()
    assert_type(keepers.encode(bindings.outbound_6().from_wire({"badge": "k"}), bindings.CONTEXT_6), WireValue)
    codes: ModelCodec[Code] = bindings.codec_3()
    assert_type(codes.decode("ab", bindings.CONTEXT_3), DecodedValue[Code])
    tags: ModelCodec[Tags] = bindings.codec_0()
    assert_type(tags.decode(wire, bindings.CONTEXT_0), DecodedValue[Tags])
    cookie = bindings.parameter_0()
    assert_type(cookie.encode(freeze_wire(["a,b", "c"]), bindings.CONTEXT_0), EncodedParameterContribution)
    assert_type(cookie.decode(raw, bindings.CONTEXT_0), WireValue | Unset)
