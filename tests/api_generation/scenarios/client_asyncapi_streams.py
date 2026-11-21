"""Read the server-sent event channels of AsyncAPI documents through generated clients against a local server."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from tests.api_generation.scenarios.client_sockets import _data
from tests.api_generation.support.client_runtime import Exchange, describe, raw_response, run

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable
    from types import ModuleType

_STREAM: Final = "text/event-stream"
_PRICES: Final = (
    b": prices of XBT\n\n"
    b'data: {"symbol": "XBT", "price": 1.5}\n\n'
    b'id: 2\nevent: tick\ndata: {"symbol": "XBT",\ndata: "price": 2}\n\n'
    b'data: {"symbol": "XBT"}\n\n'
    b'data: {"symbol": "XBT", "price": 3}\n\n'
)
_ACTIVITY: Final = (
    b'data: {"type": "created", "id": "1"}\n\n'
    b'event: anything\ndata: {"type": "deleted", "id": "2"}\n\n'
    b'data: {"type": "renamed", "id": "3"}\n\n'
)
_TRADES: Final = b'data: {"id": 7, "size": 0.5}\n\n: done\n\n'
_NOTICES: Final = b'data: {"text": "hi"}\n\ndata: {"level": 3}\n\ndata: {"other": true}\n\n'


def _event(event: Any) -> str:
    """Describe an event by its sequence, SSE type, ID and decoded data."""
    return (
        f"{event.sequence} {event.event_type!r} id={event.event_id!r} {type(event.data).__name__}{_data(event.data)!r}"
    )


def _drained(lines: list[str], label: str, events: Iterable[Any]) -> None:
    """Report every event a stream yields, then its end or the failure that stopped it."""
    lines.append(f"  {label}")
    try:
        lines.extend(f"    {_event(event)}" for event in events)
    except Exception as error:  # ruff: ignore[blind-except]
        lines.append(f"    ! {describe(error)}")
        return
    lines.append("    end")


async def _adrained(lines: list[str], label: str, events: AsyncIterator[Any]) -> None:
    """Report every event an asyncio stream yields, then its end or the failure that stopped it."""
    lines.append(f"  {label}")
    try:
        async for event in events:
            lines.append(f"    {_event(event)}")  # ruff: ignore[manual-list-comprehension] - interleave with what the stream reports as it ends
    except Exception as error:  # ruff: ignore[blind-except]
        lines.append(f"    ! {describe(error)}")
        return
    lines.append("    end")


def asyncapi_events(package: ModuleType, lines: list[str]) -> None:
    """Open server-sent event channels with their parameters and credentials, and decode each event's data.

    An event that its model refuses, or whose tag no message has, raises DecodeError and ends the stream; a refused
    GET raises the error of its status. The trades channel takes its HTTP binding from an operation trait, and its
    stream ends when the server closes it.
    """
    exchange = Exchange(lines)
    with exchange.client() as native, package.Client(http_client=native, token="secret") as client:
        lines.append(f"  channels {client.prices is client.protocols.prices} {type(client.activity).__name__}")
        exchange.respond(raw_response(200, _PRICES, _STREAM))
        with client.prices.open(symbol="XBT", X_Region="eu", depth=2) as stream:
            _drained(lines, "prices", stream)
        exchange.respond(raw_response(200, _ACTIVITY, f"{_STREAM}; charset=utf-8"))
        with client.activity.open() as stream:
            _drained(lines, "activity", stream)
        exchange.respond(raw_response(404, b"gone", "text/plain"))
        try:
            client.activity.open()
        except Exception as error:  # ruff: ignore[blind-except]
            lines.append(f"  activity refused ! {describe(error)}")
        exchange.respond(raw_response(200, _TRADES, _STREAM))
        with client.trades.open(since=6) as stream:
            _drained(lines, "trades", stream)
    run(lambda: _async_streams(package, lines))


async def _async_streams(package: ModuleType, lines: list[str]) -> None:
    """Read the events of channels through the asyncio client, to a refused event and to the end of the stream."""
    exchange = Exchange(lines)
    async with exchange.async_client() as native, package.AsyncClient(http_client=native, token="secret") as client:
        exchange.respond(raw_response(200, _PRICES, _STREAM))
        async with await client.prices.open(symbol="ETH") as stream:
            await _adrained(lines, "async prices", stream)
        exchange.respond(raw_response(200, _TRADES, _STREAM))
        async with await client.trades.open() as stream:
            await _adrained(lines, "async trades", stream)


def asyncapi_event_unions(package: ModuleType, lines: list[str]) -> None:
    """Decode the untagged events of a channel as the union of their models, which refuses an event neither is."""
    exchange = Exchange(lines)
    with exchange.client() as native, package.Client(http_client=native, token="secret") as client:
        exchange.respond(raw_response(200, _NOTICES, _STREAM))
        with client.notices.open() as stream:
            _drained(lines, "notices", stream)
