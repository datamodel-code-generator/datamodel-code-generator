"""Observe parts compression through public helpers and an independent TLS parts server."""

from __future__ import annotations

import gzip
from typing import TYPE_CHECKING, Any

import httpx2

from tests.data.python.client_runtime import Exchange, arecord, record, run
from tests.data.python.client_uploads import _PART_CONTENT, _AsyncCounting, _Counting, _PartsServer, _Uploads


def _field(value: Any, name: str) -> Any:
    """Read the same public response field from model and TypedDict backends."""
    return value[name] if isinstance(value, dict) else getattr(value, name)


if TYPE_CHECKING:
    from types import ModuleType


class _CodedParts:
    """Expand wire bodies before applying the server's original-byte digest and receipt rules."""

    def __init__(self) -> None:
        self.server = _PartsServer()
        self.rows: list[tuple[str, str | None, bytes]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        token = request.headers.get("content-encoding")
        expanded = gzip.decompress(request.content) if token == "gzip" else request.content
        self.rows.append((request.method, token, expanded))
        return self.server(httpx2.Request(request.method, request.url, headers=request.headers, content=expanded))


def parts_compression(package: ModuleType, lines: list[str], *, declaration: str) -> None:
    """Check part-only, complete-only, and undeclared coding, including new and existing completed handles."""
    harness = _Uploads(package)
    exchange = Exchange([])
    wire = _CodedParts()
    coding = harness.options.RequestOptions(compression="gzip")
    source = _Counting(harness.source(_PART_CONTENT))
    with exchange.client() as native, package.Client(http_client=native) as api:
        helper = api.protocols.files.parts
        exchange.respond(*(wire for _ in range(40)))
        if declaration == "off":
            record(lines, "sync undeclared", lambda: helper.start(source, tus_resumable=harness.tus, options=coding))
            lines.append(f"sync refused reads={source.opened} sends={len(wire.rows)}")
        else:
            with helper.start(source, tus_resumable=harness.tus, options=coding) as handle:
                handle.advance()
                state = handle.checkpoint()
            before = wire.server.creates
            with helper.resume(source, state, options=coding) as resumed:
                result = resumed.run()
                sends = len(wire.rows)
                resumed.run()
                state = resumed.checkpoint()
                lines.append(
                    f"sync result size={_field(result, 'size')} sha256={_field(result, 'sha256')} "
                    f"resume creates={wire.server.creates - before} repeat sends={len(wire.rows) - sends}"
                )
            reads, sends = source.opened, len(wire.rows)
            record(lines, "sync completed", lambda: helper.resume(source, state, options=coding))
            lines.append(f"sync completed reads={source.opened - reads} sends={len(wire.rows) - sends}")
            with helper.resume(source, state) as complete:
                lines.append(
                    f"sync plain completed size={_field(complete.run(), 'size')} sends={len(wire.rows) - sends}"
                )
        lines.append(f"sync wire={wire.rows!r}")
        empty = _Counting(harness.source(b""))
        reads, sends = empty.opened, len(wire.rows)
        if declaration == "complete":
            with helper.start(empty, tus_resumable=harness.tus, options=coding) as handle:
                lines.append(f"sync empty explicit size={_field(handle.run(), 'size')}")
        else:
            record(lines, "sync empty", lambda: helper.start(empty, tus_resumable=harness.tus, options=coding))
            lines.append(f"sync empty reads={empty.opened - reads} sends={len(wire.rows) - sends}")
        exchange.responders.clear()
    run(lambda: _async_parts_compression(harness, exchange, lines, declaration))
    for token in (harness.options.UNSET, None):
        wire = _CodedParts()
        exchange.respond(*(wire for _ in range(40)))
        with (
            exchange.client() as native,
            package.Client(http_client=native, options=harness.options.ClientOptions(compression="gzip")) as api,
            api.protocols.files.parts.start(
                harness.source(b""),
                tus_resumable=harness.tus,
                options=harness.options.RequestOptions(compression=token),
            ) as handle,
        ):
            result = handle.run()
            lines.append(
                f"sync empty inherited={token is harness.options.UNSET} size={_field(result, 'size')} "
                f"wire={wire.rows!r}"
            )
        exchange.responders.clear()


async def _async_parts_compression(harness: _Uploads, exchange: Exchange, lines: list[str], declaration: str) -> None:
    """Repeat the original-byte and strict admission controls with native asyncio sources."""
    wire = _CodedParts()
    coding = harness.options.RequestOptions(compression="gzip")
    source = _AsyncCounting(harness.protocols.AsyncBytesUploadSource.from_bytes(_PART_CONTENT))
    async with exchange.async_client() as native, harness.package.AsyncClient(http_client=native) as api:
        helper = api.protocols.files.parts
        exchange.respond(*(wire for _ in range(40)))
        if declaration == "off":
            await arecord(
                lines, "async undeclared", lambda: helper.start(source, tus_resumable=harness.tus, options=coding)
            )
            lines.append(f"async refused reads={source.opened} sends={len(wire.rows)}")
        else:
            async with await helper.start(source, tus_resumable=harness.tus, options=coding) as handle:
                await handle.advance()
                state = handle.checkpoint()
            before = wire.server.creates
            async with await helper.resume(source, state, options=coding) as resumed:
                result = await resumed.run()
                sends = len(wire.rows)
                await resumed.run()
                state = resumed.checkpoint()
                lines.append(
                    f"async result size={_field(result, 'size')} sha256={_field(result, 'sha256')} "
                    f"resume creates={wire.server.creates - before} repeat sends={len(wire.rows) - sends}"
                )
            reads, sends = source.opened, len(wire.rows)
            await arecord(lines, "async completed", lambda: helper.resume(source, state, options=coding))
            lines.append(f"async completed reads={source.opened - reads} sends={len(wire.rows) - sends}")
            async with await helper.resume(source, state) as complete:
                result = await complete.run()
                lines.append(f"async plain completed size={_field(result, 'size')} sends={len(wire.rows) - sends}")
        lines.append(f"async wire={wire.rows!r}")
        empty = _AsyncCounting(harness.protocols.AsyncBytesUploadSource.from_bytes(b""))
        reads, sends = empty.opened, len(wire.rows)
        if declaration == "complete":
            async with await helper.start(empty, tus_resumable=harness.tus, options=coding) as handle:
                lines.append(f"async empty explicit size={_field(await handle.run(), 'size')}")
        else:
            await arecord(lines, "async empty", lambda: helper.start(empty, tus_resumable=harness.tus, options=coding))
            lines.append(f"async empty reads={empty.opened - reads} sends={len(wire.rows) - sends}")
        exchange.responders.clear()
    for token in (harness.options.UNSET, None):
        wire = _CodedParts()
        exchange.respond(*(wire for _ in range(40)))
        async with (
            exchange.async_client() as native,
            harness.package.AsyncClient(
                http_client=native, options=harness.options.ClientOptions(compression="gzip")
            ) as api,
            await api.protocols.files.parts.start(
                harness.protocols.AsyncBytesUploadSource.from_bytes(b""),
                tus_resumable=harness.tus,
                options=harness.options.RequestOptions(compression=token),
            ) as handle,
        ):
            result: Any = await handle.run()
            lines.append(
                f"async empty inherited={token is harness.options.UNSET} size={_field(result, 'size')} "
                f"wire={wire.rows!r}"
            )
        exchange.responders.clear()
