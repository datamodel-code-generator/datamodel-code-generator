"""The factories of the client's own WebSocket connectors, which load the WebSocket library only when first called.

Only the plans of packages with WebSocket helpers reference them, so other packages never copy the native connectors.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .websocket_types import AsyncWebSocketConnector, WebSocketConnector

__all__ = ("async_native_connector", "native_connector")


def native_connector() -> WebSocketConnector:
    """Return a new threading connector of the websockets library."""
    from .websocket_native import NativeConnector  # noqa: PLC0415 - The library loads at a client's first connect.

    return NativeConnector()


def async_native_connector() -> AsyncWebSocketConnector:
    """Return a new asyncio connector of the websockets library."""
    from .websocket_native import AsyncNativeConnector  # noqa: PLC0415 - The library loads at a client's first connect.

    return AsyncNativeConnector()
