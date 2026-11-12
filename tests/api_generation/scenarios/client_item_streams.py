"""Read OpenAPI 3.2 itemSchema responses through the stream helpers the client derives from them.

An event stream's items are its events' parsed fields and a JSON Lines or NDJSON stream's items are its lines, each
decoded by the item schema; the plain operations read the same bodies whole.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.api_generation.support.client_runtime import Exchange, describe, injected, raw_response, record, run

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterable, Iterator
    from types import ModuleType

_EVENTS: Final = (
    b"event: add\ndata: first\ndata: second\nid: 7\nretry: 1500\n\n"
    b': a comment\nretry: soon\ndata: {"plain": true}\n\n'
    b"id\nevent: done\ndata: last\nunknown: ignored\n\n"
)
_LOGS: Final = b'{"message": "hi", "level": 1}\n{"message": "bye"}'


def _item(value: object) -> object:
    """Return an item as plain values, whatever its backend: a model, a dataclass, or its root."""
    value = getattr(value, "root", value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return asdict(value) if is_dataclass(value) and not isinstance(value, type) else value


def _drained(lines: list[str], label: str, events: Iterable[Any]) -> None:
    """Report every item a stream yields with its sequence, then its end or the failure that stopped it."""
    lines.append(f"  {label}")
    try:
        lines.extend(f"    {event.sequence} {event.event_type!r} {_item(event.data)!r}" for event in events)
    except Exception as error:  # noqa: BLE001
        lines.append(f"    ! {describe(error)}")
        return
    lines.append("    end")


async def _adrained(lines: list[str], label: str, events: AsyncIterator[Any]) -> None:
    """Report every item an asyncio stream yields, then its end or the failure that stopped it."""
    lines.append(f"  {label}")
    try:
        async for event in events:
            lines.append(f"    {event.sequence} {event.event_type!r} {_item(event.data)!r}")  # noqa: PERF401
    except Exception as error:  # noqa: BLE001
        lines.append(f"    ! {describe(error)}")
        return
    lines.append("    end")


class _Cut(httpx2.SyncByteStream, httpx2.AsyncByteStream):
    """A body that yields its chunks and then loses its connection."""

    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self.chunks = chunks

    def __iter__(self) -> Iterator[bytes]:
        yield from self.chunks
        msg = "connection reset"
        raise httpx2.ReadError(msg)

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk
        msg = "connection reset"
        raise httpx2.ReadError(msg)


def _cut(chunks: tuple[bytes, ...], media: str) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return an injected responder whose body breaks off after its chunks."""
    return injected(lambda _: httpx2.Response(200, headers={"content-type": media}, stream=_Cut(chunks)))


def _replies(exchange: Exchange) -> None:
    """Queue one answer of each stream and failure, in the order the calls send them."""
    exchange.respond(
        raw_response(200, _EVENTS, "text/event-stream"),
        raw_response(200, b"data: kept\n\nevent: other\ndata: refused\n\n", "text/event-stream"),
        _cut((b"data: kept\n\n", b"data: cut"), "text/event-stream"),
        raw_response(200, _LOGS, "application/jsonl"),
        raw_response(200, b'{"message": "a"}\n{"level": "high"}\n', "application/jsonl"),
        raw_response(200, b'{"message": "a"}\n{"message": "cu', "application/x-ndjson"),
        raw_response(200, _LOGS + b"\n", "application/x-ndjson"),
        raw_response(200, _LOGS, "application/jsonl"),
        raw_response(200, b'[{"message": "all"}]', "application/json"),
        raw_response(200, _EVENTS, "text/event-stream"),
        raw_response(404, b'{"message": "gone"}\n', "application/jsonl"),
    )


def item_streams(package: ModuleType, lines: list[str]) -> None:
    """Stream typed items, refuse an invalid one, report a cut end, and read the bodies whole, in both modes."""
    exchange = Exchange(lines)
    _replies(exchange)
    with exchange.client() as http, package.Client(http_client=http) as api:
        protocols = api.protocols.default
        _drained(lines, "event fields", protocols.watch_events.open(topic="a"))
        _drained(lines, "event its item schema refuses", protocols.watch_events.open())
        _drained(lines, "event stream cut", protocols.watch_events.open())
        with protocols.tail_logs.open() as stream:
            _drained(lines, "JSON Lines without a final line feed", stream)
        _drained(lines, "invalid line", protocols.tail_logs.open())
        _drained(lines, "NDJSON cut line", protocols.stream_records.open())
        _drained(lines, "NDJSON", protocols.stream_records.open())
        _drained(lines, "JSON Lines beside JSON", protocols.get_excerpt.open())
        record(lines, "excerpt", api.default.get_excerpt)
        record(lines, "events whole", api.default.watch_events)
        record(lines, "excerpt missing", api.default.get_excerpt)
    exchange = Exchange(lines)
    exchange.respond(
        raw_response(200, _EVENTS, "text/event-stream"),
        _cut((b"data: kept\n\n",), "text/event-stream"),
        raw_response(200, _LOGS, "application/jsonl"),
    )
    run(lambda: _async_item_streams(package, exchange, lines))


async def _async_item_streams(package: ModuleType, exchange: Exchange, lines: list[str]) -> None:
    """Stream typed items and report a cut end through the asyncio client."""
    async with exchange.async_client() as http, package.AsyncClient(http_client=http) as api:
        protocols = api.protocols.default
        await _adrained(lines, "async event fields", await protocols.watch_events.open())
        await _adrained(lines, "async event stream cut", await protocols.watch_events.open())
        async with await protocols.tail_logs.open() as stream:
            await _adrained(lines, "async JSON Lines", stream)
