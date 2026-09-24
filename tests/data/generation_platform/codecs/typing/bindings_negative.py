"""Deliberately invalid rendered model binding typing cases; every error is checked by code and line."""

from adapted._generated import model_bindings as bindings
from adapted.model_codecs import ModelCodec, ModelValue, NativeOutboundCodec, WireValue
from adapted.models import Keeper, Pet, Secretive


def exercise(wire: WireValue, keeper: Keeper) -> None:
    _widened: ModelCodec[object] = bindings.codec_4()
    _other: ModelCodec[Keeper] = bindings.codec_4()
    bindings.outbound_5().snapshot(keeper)
    _bare: Pet = bindings.codec_4().decode(wire, bindings.CONTEXT_4)
    _native: NativeOutboundCodec[Secretive] = bindings.outbound_8()
    _sent: ModelValue[Secretive] = bindings.outbound_8().from_wire(wire)
    bindings.outbound_4()
    bindings.parameter_1()
