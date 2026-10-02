"""Observe generation defaults through public generated clients over real TLS."""

from __future__ import annotations

import gzip
import importlib
from typing import TYPE_CHECKING, Any

from tests.data.python.client_runtime import Exchange, json_response, raw_response, run

if TYPE_CHECKING:
    from types import ModuleType

    import httpx2


class _Wire(Exchange):
    """Record only public sent bytes and coding; the base exchange owns the TLS server."""

    def __init__(self) -> None:
        super().__init__([])
        self.requests: list[tuple[str, str | None, bytes]] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        request.read()
        coding = request.headers.get("content-encoding")
        body = gzip.decompress(request.content) if coding == "gzip" else request.content
        self.requests.append((request.method, coding, body))
        return self.responders.pop(0)(request)


class _Clock:
    def __init__(self) -> None:
        self.at = 100.0
        self.delays: list[float] = []
        self.options: list[dict[str, object]] = []

    def __call__(self) -> float:
        return self.at

    def on_event(self, event: Any) -> None:
        if event.name == "call_start":
            self.options.append(dict(event.options))
        if event.name == "retry_scheduled":
            self.delays.append(event.duration)
            self.at += event.duration


def _observed(value: object) -> str:
    info = getattr(value, "info", None) or value
    return (
        f"{type(value).__name__} attempts={getattr(info, 'resource_attempt_count', None)} "
        f"sends={getattr(info, 'network_send_count', None)} "
        f"stop={getattr(value, 'retry_stop_reason', None)} "
        f"limit={getattr(value, 'limit', None)} "
        f"body={getattr(value, 'body_bytes', None)!r} truncated={getattr(value, 'truncated', None)}"
    )


def defaults_wire(package: ModuleType, lines: list[str]) -> None:
    """Exercise all five layer priorities, coding admission, retries, redirects and helper budgets."""
    options, protocols, models = (
        importlib.import_module(f"{package.__name__}{suffix}") for suffix in (".options", ".protocols", "_models")
    )
    for asynchronous in (False, True):
        wire, clock = _Wire(), _Clock()
        settings = options.ClientOptions(clock=options.Clock(monotonic=clock), hooks=(clock,))
        mode = "async" if asynchronous else "sync"

        async def execute(
            asynchronous: bool = asynchronous,
            wire: _Wire = wire,
            clock: _Clock = clock,
            settings: Any = settings,
            mode: str = mode,
        ) -> None:
            native = wire.async_client() if asynchronous else wire.client()
            kind = package.AsyncClient if asynchronous else package.Client
            api = kind(http_client=native, options=settings)

            async def call(label: str, function: Any, *responders: Any) -> None:
                wire.respond(*responders)
                start = len(wire.requests)
                try:
                    value = await function() if asynchronous else function()
                except Exception as error:  # ruff: ignore[blind-except] - Record public delivery and budget outcomes.
                    result = _observed(error)
                else:
                    result = "success"
                    if hasattr(value, "info"):
                        result += f" sends={value.info.network_send_count} redirects={value.info.redirect_count}"
                sent = wire.requests[start:]
                lines.append(f"  {mode} {label} {result} wire={sent!r}")
                wire.responders.clear()

            created = json_response(201, {"name": "ok"})
            stored = raw_response(204)
            item = models.Item(name="gen")
            await call("generation gzip", lambda: api.items.create_item(body=item), created)
            lines.append(f"  {mode} effective defaults {clock.options[-1]}")
            await call("optional UNSET", api.items.create_item, created)
            await call("explicit null", lambda: api.items.create_item(body=None), created)
            await call("empty bytes", lambda: api.items.put_blob(body=b""), stored)
            await call("undeclared", lambda: api.items.create_note(body=item), stored)
            await call("bodyless", api.items.list_items, json_response(200, {"data": []}))
            await call("raw", lambda: api.request_raw("PUT", "https://api.example.com/raw", body=b"raw"), stored)
            await call(
                "call off",
                lambda: api.items.put_blob(body=b"call", options=options.RequestOptions(compression=None)),
                stored,
            )
            plain = api.with_options(options.RequestOptions(compression=None))
            await call("view off", lambda: plain.items.put_blob(body=b"view"), stored)
            await call(
                "call overrides view",
                lambda: plain.items.put_blob(body=b"call", options=options.RequestOptions(compression="gzip")),
                stored,
            )
            await call(
                "inherited retries",
                lambda: api.items.put_blob(body=b"retry"),
                raw_response(503, b"failure", **{"Retry-After": "90"}),
                stored,
            )
            await call("statuses exclude", lambda: api.items.put_blob(body=b"status"), raw_response(500, b"failure"))
            await call(
                "303 drops coding",
                lambda: api.items.with_raw_response.put_blob(body=b"drop"),
                raw_response(303, Location="https://api.example.com/items"),
                json_response(200, {"data": []}),
            )
            await call(
                "allowed origin",
                lambda: api.items.put_blob(body=b"origin"),
                raw_response(307, Location="https://other.example.com/blobs"),
                stored,
            )
            await call(
                "https downgrade",
                lambda: api.items.put_blob(body=b"origin"),
                raw_response(307, Location="http://other.example.com/blobs"),
            )
            await call(
                "network zero",
                lambda: api.items.put_blob(body=b"zero", options=options.RequestOptions(max_network_sends=0)),
            )
            await call(
                "response zero",
                lambda: api.items.list_items(options=options.RequestOptions(max_response_bytes=0)),
                json_response(200, {"data": []}),
            )
            await call(
                "helper inherited",
                lambda: api.protocols.items.search_all.page(body=models.Query(text="q")),
                json_response(200, {"data": []}),
            )
            zero = protocols.PaginationOptions(max_items=0)
            pager = api.protocols.items.search_all.iterate(body=models.Query(text="q"), pagination_options=zero)
            if asynchronous:
                async with pager:
                    values = [value async for value in pager]
            else:
                with pager:
                    values = list(pager)
            lines.append(f"  {mode} empty helper items={len(values)} sends={len(wire.requests)} delays={clock.delays}")
            if asynchronous:
                await api.aclose()
                lines.append(f"  {mode} borrowed native closed={native.is_closed}")
                await native.aclose()
            else:
                api.close()
                lines.append(f"  {mode} borrowed native closed={native.is_closed}")
                native.close()

        run(execute)


def defaults_zero(package: ModuleType, lines: list[str]) -> None:
    """Reject generation's zero send budget without admitting or sending a request."""
    options = importlib.import_module(f"{package.__name__}.options")
    wire, clock = _Wire(), _Clock()
    with (
        wire.client() as native,
        package.Client(http_client=native, options=options.ClientOptions(hooks=(clock,))) as api,
    ):
        try:
            codecs = importlib.import_module(f"{package.__name__}.types.pets").ListPetsRequestCodecs
            trace = codecs.parameter(location="header", name="X-Trace").from_wire("t").value
            api.pets.list_pets(x_trace=trace)
        except Exception as error:  # ruff: ignore[blind-except] - Record the public zero-budget refusal.
            lines.append(f"  zero {_observed(error)} sends={len(wire.requests)}")
        lines.append(f"  zero effective defaults {clock.options}")
