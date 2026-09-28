"""Read the responses of an API that evolved past its generated client, as each backend's model settings decide."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Final

import httpx2

from tests.data.python.client_runtime import Exchange, json_response, record

if TYPE_CHECKING:
    from types import ModuleType

_CARD: Final = {"kind": "card", "last4": "1234"}


def evolution(package: ModuleType, lines: list[str]) -> None:
    """Decode what the schema still describes, refuse what it no longer does, and keep every body readable raw."""
    codecs = importlib.import_module(f"{package.__name__}.types.orders").GetOrderRequestCodecs
    order = codecs.parameter(location="path", name="orderId").from_wire(1)
    brief = codecs.parameter(location="query", name="view").from_wire("brief")
    exchange = Exchange(lines)
    http = httpx2.Client(transport=httpx2.MockTransport(exchange.handle))
    with package.Client(http_client=http) as api:
        exchange.respond(json_response(200, {"id": 1, "status": "open", "note": "n", "channel": "web", "payment": _CARD}))
        record(lines, "order", lambda: api.orders.get_order(order_id=order, view=brief))
        for label, payload in (
            ("order without its optional members", {"id": 1, "status": "closed"}),
            ("order with a member added since", {"id": 1, "status": "open", "priority": 2}),
            ("order of a status added since", {"id": 1, "status": "archived"}),
            ("order of a payment kind added since", {"id": 1, "status": "open", "payment": {"kind": "crypto"}}),
            ("order of a card with a transfer's members", {"id": 1, "status": "open", "payment": {"kind": "card", "iban": "X"}}),
            ("order of a status that changed its type", {"id": 1, "status": 3}),
        ):
            exchange.respond(json_response(200, payload))
            record(lines, label, lambda: api.orders.get_order(order_id=order))
        exchange.respond(json_response(200, {"id": 1, "status": "archived"}))
        record(lines, "order of a status added since, read raw", lambda: api.orders.with_raw_response.get_order(order_id=order).json())
        for label, payload in (("problem", {"code": 7}), ("problem that changed its shape", {"message": "gone"})):
            exchange.respond(json_response(409, payload))
            record(lines, label, lambda: api.orders.get_order(order_id=order))
    http.close()
