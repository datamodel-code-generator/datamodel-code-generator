"""A registered public model adapter with controlled restoration and serialization work."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from tests.data.python.parameter_adapters import RawText, _caller

if TYPE_CHECKING:
    from collections.abc import Callable

restoring: Callable[[], None] | None = None
encoding: Callable[[], None] | None = None
changed_trace = False


class OrderAdapter:
    """Convert a request object through the public codec registration contract."""

    api_version: Literal[1] = 1

    def __init__(self) -> None:
        """Use the public codec records of the generated package invoking this factory."""
        self.public = _caller()

    def capabilities(self, *, binding: object) -> object:
        """Declare the request model capability used by the external registration."""
        del binding
        return self.public.CodecCapabilities(backends=("pydantic_v2.BaseModel",), native_kinds=("model",))

    def decode_native(self, *, wire: object, binding: object, context: object) -> object:
        """Restore a native request after the controlled callback consumes clock time."""
        del binding, context
        if restoring is not None:
            restoring()
        return self.public.thaw_wire(wire)

    def encode_native(self, *, value: object, binding: object, context: object) -> object:
        """Serialize a native request through the public wire representation."""
        del binding, context
        return self.public.freeze_wire(value)

    def presence(self, *, value: object, binding: object, context: object) -> None:
        """Leave native presence unspecified as the declared adapter capability allows."""
        del value, binding, context


def order() -> OrderAdapter:
    """Construct the adapter when generated bindings first require it."""
    return OrderAdapter()


class TraceAdapter(RawText):
    """A registered header adapter whose wire result can change during actual execution preparation."""

    def encode_parameter(self, *, value: object, plan: object, context: object) -> object:
        """Expose controlled encoding work through the public parameter adapter contract."""
        if encoding is not None:
            encoding()
        return super().encode_parameter(value="changed" if changed_trace else value, plan=plan, context=context)


def trace() -> TraceAdapter:
    """Construct the request's registered header adapter."""
    return TraceAdapter(locations=("header",), styles=("simple",), explode_values=(False,), value_kinds=("string",))
