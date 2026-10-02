"""Observe layered phase limits and retry fields through public transport and trace contracts."""

from __future__ import annotations

import gzip
import importlib
from typing import TYPE_CHECKING, Any

from tests.data.python.client_runtime import run
from tests.data.python.client_runtime_defaults_wire import _Clock, _observed

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from types import ModuleType


class _Response:
    def __init__(self, headers: Any, status: int = 204) -> None:
        self.status_code = status
        self.headers = headers
        self.chunks: tuple[bytes, ...] = ()
        self.closed = 0

    def iter_raw_bytes(self) -> Iterator[bytes]:
        return iter(())

    def close(self) -> None:
        self.closed += 1

    async def aclose(self) -> None:
        self.close()


class _Adapter:
    def __init__(
        self, transports: ModuleType, errors: ModuleType, responses: ModuleType, *, asynchronous: bool
    ) -> None:
        self.capabilities = transports.TransportCapabilities(
            internal_retry_limit=0, delivery_evidence=True, http_versions=("HTTP/1.1",)
        )
        self.errors = errors
        self.headers = responses.HeadersView(())
        self.asynchronous = asynchronous
        self.timeouts: list[tuple[float | None, ...]] = []
        self.bodies: list[bytes] = []
        self.pool = False
        self.closed = 0

    def _answer(self, request: Any, context: Any, body: bytes) -> _Response:
        timeout = context.timeout
        self.timeouts.append((timeout.connect, timeout.read, timeout.write, timeout.pool))
        self.bodies.append(gzip.decompress(body) if request.headers.get("content-encoding") == "gzip" else body)
        if self.pool:
            self.pool = False
            context.trace.phase_started("pool")
            raise self.errors.PhaseTimeoutError(
                phase="pool", effective_timeout=0, delivery_state=self.errors.DeliveryState.NOT_SENT
            )
        context.trace.request_headers_started()
        context.trace.wire_send()
        context.trace.response_headers_received(http_version="HTTP/1.1", status_code=204, headers=self.headers)
        return _Response(self.headers)

    def send(self, request: Any, context: Any) -> _Response:
        body = b"" if request.body is None else b"".join(request.body.iter_bytes())
        return self._answer(request, context, body)

    async def asend(self, request: Any, context: Any) -> _Response:
        body = b"" if request.body is None else b"".join([part async for part in request.body.aiter_bytes()])
        return self._answer(request, context, body)

    def close(self) -> None:
        self.closed += 1

    async def aclose(self) -> None:
        self.close()


class _AsyncAdapter(_Adapter):
    async def send(self, request: Any, context: Any) -> _Response:
        return await self.asend(request, context)


class _AsyncResponse(_Response):
    async def iter_raw_bytes(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk


class _AsyncTransport(_AsyncAdapter):
    def _answer(self, request: Any, context: Any, body: bytes) -> _AsyncResponse:
        result = super()._answer(request, context, body)
        return _AsyncResponse(result.headers, result.status_code)


def defaults_options(package: ModuleType, lines: list[str]) -> None:
    """Resolve generation, client, view and call phases without replacing their public transport seam."""
    options, transports, errors, responses = (
        importlib.import_module(f"{package.__name__}.{name}")
        for name in ("options", "transports", "errors", "responses")
    )
    for asynchronous in (False, True):
        mode = "async" if asynchronous else "sync"
        adapter = (_AsyncTransport if asynchronous else _Adapter)(
            transports, errors, responses, asynchronous=asynchronous
        )
        clock = _Clock()

        async def execute(
            asynchronous: bool = asynchronous,
            adapter: _Adapter = adapter,
            clock: _Clock = clock,
            mode: str = mode,
        ) -> None:
            api = (package.AsyncClient if asynchronous else package.Client)(
                transport_adapter=adapter,
                options=options.ClientOptions(clock=options.Clock(monotonic=clock), hooks=(clock,)),
            )
            client = (package.AsyncClient if asynchronous else package.Client)(
                transport_adapter=adapter,
                options=options.ClientOptions(
                    timeout=options.TimeoutOptions(connect=4), clock=options.Clock(monotonic=clock), hooks=(clock,)
                ),
            )
            for label, view, call in (
                ("generation", api, None),
                ("client", client, None),
                ("view", client.with_options(options.RequestOptions(timeout=options.TimeoutOptions(read=5))), None),
                (
                    "call",
                    client.with_options(options.RequestOptions(timeout=options.TimeoutOptions(read=5))),
                    options.RequestOptions(timeout=options.TimeoutOptions(pool=6)),
                ),
                ("disabled", client, options.RequestOptions(timeout=None)),
                (
                    "zero",
                    api,
                    options.RequestOptions(timeout=options.TimeoutOptions(connect=0, read=0, write=0, pool=0)),
                ),
                ("total zero", api, options.RequestOptions(total_timeout=0)),
                ("pool generation", api, None),
                ("pool off", api, options.RequestOptions(retry=options.RetryOptions(retry_on_pool_timeout=False))),
            ):
                adapter.timeouts.clear()
                adapter.bodies.clear()
                clock.delays.clear()
                if label.startswith("pool"):
                    adapter.pool = True
                try:
                    value = view.items.put_blob(body=b"x", options=call)
                    if asynchronous:
                        value = await value
                except Exception as error:  # ruff: ignore[blind-except] - Observe the public transport/delivery contract.
                    result = _observed(error)
                else:
                    result = "success"
                lines.append(
                    f"  {mode} {label} {result} phases={adapter.timeouts} bodies={adapter.bodies} delays={clock.delays}"
                )
            if asynchronous:
                await client.aclose()
                await api.aclose()
            else:
                client.close()
                api.close()
            lines.append(f"  {mode} borrowed adapter closes={adapter.closed}")

        run(execute)
