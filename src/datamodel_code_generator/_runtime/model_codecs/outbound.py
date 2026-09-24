"""The model codec protocol and the bound facades that generated surfaces expose for sending values."""

from __future__ import annotations

from typing import TYPE_CHECKING, Generic, Protocol, TypeVar

from .errors import CodecBindingError
from .values import ModelInput

if TYPE_CHECKING:
    from .context import CodecContext
    from .values import DecodedValue, ModelValue
    from .wire import JSONValue, PresenceTree, WireValue

T = TypeVar("T")


class ModelCodec(Protocol[T]):
    """Receive, construct, snapshot, and send values of one bound type use."""

    def decode(self, wire: WireValue, context: CodecContext) -> DecodedValue[T]:
        """Validate and project a received wire value."""
        ...

    def encode(self, value: T | ModelValue[T] | ModelInput[T], context: CodecContext) -> WireValue:
        """Return the validated wire value to send."""
        ...

    def from_wire(self, wire: JSONValue | WireValue, context: CodecContext) -> DecodedValue[T]:
        """Project a wire value to send."""
        ...

    def snapshot(self, value: T, context: CodecContext, *, presence: PresenceTree | None = None) -> ModelValue[T]:
        """Capture a native value to send."""
        ...


class OutboundCodec(Generic[T]):
    """A codec bound to one sending use and its fixed context."""

    __slots__ = ("_codec", "_context")

    def __init__(self, codec: ModelCodec[T], context: CodecContext) -> None:
        """Bind the codec to a context that sends values."""
        if context.inbound:
            msg = "An outbound codec requires a sending context"
            raise CodecBindingError(msg)
        self._codec = codec
        self._context = context

    def from_wire(self, value: JSONValue | WireValue) -> DecodedValue[T]:
        """Project a wire value to send, without inventing values excluded in this direction."""
        return self._codec.from_wire(value, self._context)

    def snapshot(self, value: T, *, presence: PresenceTree | None = None) -> ModelValue[T]:
        """Capture a native value to send, using explicit presence when given."""
        return self._codec.snapshot(value, self._context, presence=presence)


class NativeOutboundCodec(OutboundCodec[T]):
    """A sending codec whose valid wire values always construct T."""

    __slots__ = ()

    def from_wire(self, value: JSONValue | WireValue) -> ModelValue[T]:
        """Project a wire value to send into a constructed native value."""
        decoded = self._codec.from_wire(value, self._context)
        if isinstance(decoded, ModelInput):
            decoded.require_model()
        return decoded


class EnvelopeOutboundCodec(OutboundCodec[T]):
    """A sending codec whose valid wire values may only fit an envelope in this direction."""

    __slots__ = ()
