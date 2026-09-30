"""Protocol types that modules every client imports name, loaded only when first used."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .options import ProtocolClientOptions

__all__ = ["ProtocolClientOptions"]


def __getattr__(name: str) -> object:
    """Load the protocol client settings only when a client names them."""
    if name == "ProtocolClientOptions":
        from .options import ProtocolClientOptions  # noqa: PLC0415 - Clients load protocol settings only on use.

        globals()[name] = ProtocolClientOptions
        return ProtocolClientOptions
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


def __dir__() -> list[str]:
    """List the module's names, including those loaded on first use."""
    return sorted({*globals(), *__all__})
