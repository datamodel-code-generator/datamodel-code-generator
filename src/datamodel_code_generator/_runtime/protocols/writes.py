"""Request parameters and media that write the values a server chose into a caller's querystring or JSON body.

Others show a helper what the caller's own first request sends, such as the position a traversal starts at. Only a
helper's plan loads them, since they need the operation runtime every generated operation already imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..client.operations import BodyMedia, ParameterSpec
from .values import Patch

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..client.options import RequestValidation
    from ..model_codecs.wire import WireValue

__all__ = ("PatchedMedia", "PatchedParameter", "ReadMedia", "ReadParameter")


@dataclass(frozen=True, slots=True, kw_only=True)
class PatchedParameter(ParameterSpec):
    """A parameter whose argument is always a Patch of the caller's argument, such as a querystring's properties."""

    def encode(self, value: object, mode: RequestValidation) -> WireValue:
        """Return the wire value of the caller's argument with the patch's writes."""
        assert isinstance(value, Patch)
        return value.applied(lambda given: ParameterSpec.encode(self, given, mode))


@dataclass(frozen=True, slots=True, kw_only=True)
class PatchedMedia(BodyMedia):
    """A JSON request media whose body is always a Patch of the caller's body."""

    def wire(self, value: object, mode: RequestValidation) -> WireValue:
        """Return the wire value of the caller's body with the patch's writes."""
        assert isinstance(value, Patch)
        return value.applied(lambda given: BodyMedia.wire(self, given, mode))


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadParameter(ParameterSpec):
    """A parameter whose encoded argument a helper reads before it is sent, as validated as any argument's."""

    read: Callable[[WireValue], None]

    def encode(self, value: object, mode: RequestValidation) -> WireValue:
        """Return the wire value of the caller's argument once the helper has read it."""
        wire = ParameterSpec.encode(self, value, mode)
        self.read(wire)
        return wire


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadMedia(BodyMedia):
    """A JSON request media whose encoded body a helper reads before it is sent, as validated as any body's."""

    read: Callable[[WireValue], None]

    def wire(self, value: object, mode: RequestValidation) -> WireValue:
        """Return the wire value of the caller's body once the helper has read it."""
        wire = BodyMedia.wire(self, value, mode)
        self.read(wire)
        return wire
