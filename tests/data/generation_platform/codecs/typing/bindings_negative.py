"""Deliberately invalid rendered model binding typing cases; every error is checked by code and line."""

from adapted._generated import model_bindings as bindings
from adapted.model_codecs import ModelCodec, ModelValue, NativeOutboundCodec, WireValue
from adapted_models import Keeper, Pet


def exercise(wire: WireValue, keeper: Keeper) -> None:
    _widened: ModelCodec[object] = bindings.codec_5()  # error
    _other: ModelCodec[Keeper] = bindings.codec_5()  # error
    bindings.outbound_4().snapshot(keeper)  # error
    _bare: Pet = bindings.codec_5().decode(wire, bindings.CONTEXT_5)  # error
    _native: NativeOutboundCodec[Pet] = bindings.outbound_4()  # error
    _sent: ModelValue[Pet] = bindings.outbound_4().from_wire(wire)  # error
    bindings.outbound_5()  # error
    bindings.parameter_1()  # error
