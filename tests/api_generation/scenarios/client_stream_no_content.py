"""End declared bodyless stream responses without events, resume metadata, or further reconnections."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from tests.api_generation.support.client_runtime import Exchange, arecord, raw_response, record, run

if TYPE_CHECKING:
    from types import ModuleType
    from typing import Any

_HEADERS = {"X-Stream-Id": "s1", "X-Resume-Token": "t1", "X-Stream-Expires": "2999-01-01T00:00:00Z"}
_FRAME = b'event: created\nid: 1\ndata: {"type":"created","id":"1"}\n\n'


def _helpers() -> tuple[tuple[str, str], ...]:
    """Return each completion mode, a resumable stream and an undeclared status control."""
    return (
        *(("events", name) for name in ("plain", "done", "sentinel", "tracked", "undeclared")),
        ("records", "all"),
        ("records", "done"),
    )


def _drain(stream: Any) -> tuple[int, int, object]:
    """Drain and release a stream, retaining its response status and reconnection progress."""
    with stream:
        count = sum(1 for _ in stream)
    return count, stream.response.status_code, dict(stream.progress)


async def _adrain(stream: Any) -> tuple[int, int, object]:
    """Drain and release an asynchronous stream."""
    async with stream:
        count = 0
        async for _ in stream:
            count += 1
    return count, stream.response.status_code, dict(stream.progress)


def stream_no_content(package: ModuleType, lines: list[str]) -> None:
    """Exercise declared and undeclared responses, metadata, explicit resume and EOF reconnection over real TLS."""
    options = importlib.import_module(f"{package.__name__}.options")
    protocols = importlib.import_module(f"{package.__name__}.protocols")
    exchange = Exchange(lines)
    retry = options.RetryOptions(initial_delay=0, max_delay=0)
    with exchange.client(connections=1) as http, package.Client(http_client=http, retry=retry) as api:
        for group, name in _helpers():
            exchange.respond(raw_response(204))
            helper = getattr(getattr(api.protocols, group), name)
            record(lines, f"sync 204 {group}.{name}", lambda helper=helper: _drain(helper.open()))
        exchange.respond(raw_response(200, b"", "text/event-stream"))
        record(lines, "sync 200 still needs metadata", api.protocols.events.tracked.open)
        helper = api.protocols.events.tracked
        exchange.respond(raw_response(200, _FRAME, "text/event-stream", **_HEADERS))
        with helper.open() as stream:
            next(stream)
            state = stream.checkpoint()
        exchange.respond(raw_response(204))
        record(lines, "sync resumed 204", lambda: _drain(helper.resume(state)))
        exchange.respond(raw_response(200, _FRAME, "text/event-stream", **_HEADERS), raw_response(204))
        record(
            lines,
            "sync reconnected 204",
            lambda: _drain(helper.open(stream_options=protocols.StreamOptions(reconnect=True, max_reconnects=1))),
        )
        lines.append(f"  pending replies {len(exchange.responders)}")
    run(lambda: _async_no_content(package, exchange, lines, retry, protocols))


async def _async_no_content(
    package: ModuleType, exchange: Exchange, lines: list[str], retry: object, protocols: ModuleType
) -> None:
    """Repeat terminal stream acquisition and resumption with the asynchronous client."""
    async with exchange.async_client(connections=1) as http, package.AsyncClient(http_client=http, retry=retry) as api:
        for group, name in _helpers():
            exchange.respond(raw_response(204))
            helper = getattr(getattr(api.protocols, group), name)

            async def opened(helper: Any = helper) -> tuple[int, int, object]:
                return await _adrain(await helper.open())

            await arecord(lines, f"async 204 {group}.{name}", opened)
        exchange.respond(raw_response(200, b"", "text/event-stream"))
        await arecord(lines, "async 200 still needs metadata", api.protocols.events.tracked.open)
        helper = api.protocols.events.tracked
        exchange.respond(raw_response(200, _FRAME, "text/event-stream", **_HEADERS))
        async with await helper.open() as stream:
            await anext(stream)
            state = stream.checkpoint()
        exchange.respond(raw_response(204))

        async def resumed() -> tuple[int, int, object]:
            return await _adrain(await helper.resume(state))

        await arecord(lines, "async resumed 204", resumed)
        exchange.respond(raw_response(200, _FRAME, "text/event-stream", **_HEADERS), raw_response(204))

        async def reconnected() -> tuple[int, int, object]:
            return await _adrain(
                await helper.open(stream_options=protocols.StreamOptions(reconnect=True, max_reconnects=1))
            )

        await arecord(lines, "async reconnected 204", reconnected)
        lines.append(f"  pending replies {len(exchange.responders)}")
