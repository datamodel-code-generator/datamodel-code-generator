"""Observe exact saved argument and replay wire order through generated public queue APIs."""

from __future__ import annotations

import inspect
import json
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tests.data.python.client_queue_credentials import _Provider
from tests.data.python.client_queues import _AsyncStore, _Store, _Time
from tests.data.python.client_runtime import Exchange, argument, raw_response, run

if TYPE_CHECKING:
    from types import ModuleType

    import httpx2

_INPUT = Path(__file__).parents[1] / "generation_platform/client/queue-order-inputs.json"


async def _value(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _mode(package: ModuleType, lines: list[str], asynchronous: bool) -> None:  # ruff: ignore[too-many-locals, too-many-branches]
    inputs = json.loads(_INPUT.read_text(encoding="utf-8"))
    public = import_module(f"{package.__name__}.protocols")
    options = import_module(f"{package.__name__}.options")
    auth = import_module(f"{package.__name__}.auth")
    clock, exchange = _Time(), Exchange([])
    native = exchange.async_client() if asynchronous else exchange.client()
    provider = _Provider(auth, asynchronous)
    native_type = package.AsyncClient if asynchronous else package.Client
    lines.append("async" if asynchronous else "sync")

    def client(store: _Store, **settings: Any) -> Any:
        return native_type(
            http_client=native,
            options=options.ClientOptions(
                protocols=options.ProtocolClientOptions(
                    queue_stores={"outbox": _AsyncStore(store) if asynchronous else store},
                    security=public.ProtocolSecurityContext(credential_partition="tenant-a"),
                ),
                auth=auth.AuthConfig({"bearer": provider}, send_on_anonymous=True, anonymous_schemes=("bearer",)),
                clock=options.Clock(monotonic=clock.monotonic, time=clock.time, random=clock.random),
                retry=options.RetryOptions(max_retries=0),
                **settings,
            ),
        )

    async def close(api: Any) -> None:
        await _value(api.aclose() if asynchronous else api.close())

    for control in inputs["controls"]:
        store = _Store(public)
        patches = {} if control["operation"] == "whole" else {"query": tuple(map(tuple, inputs["query"]))}
        api = client(store, headers=tuple(map(tuple, inputs["headers"])), **patches)
        operation = control["operation"]
        receipt = await _value(
            getattr(api.protocols.outbox.operations, operation).enqueue(**{
                control["name"]: argument(
                    package, f"get{operation.title()}", control["location"], control["name"], control["value"]
                )
            })
        )
        entry = await _value(api.protocols.outbox.inspect(receipt.entry_id))
        payload = json.loads(entry.payload)
        lines.append(f"  {control['case']} saved={json.dumps(payload['arguments'][0][0], separators=(',', ':'))}")
        before_provider, before_sends = provider.calls, len(exchange.lines)
        lines.append(f"    enqueue providers={before_provider} sends={before_sends // 2}")
        await close(api)
        api = client(store, headers=tuple(map(tuple, inputs["headers"])), **patches)
        sent_headers: list[tuple[str, str]] = []

        def response(request: httpx2.Request, sent_headers: list[tuple[str, str]] = sent_headers) -> httpx2.Response:
            sent_headers.extend((name, value) for name, value in request.headers.multi_items() if name.startswith("x-"))
            return raw_response(204)(request)

        exchange.respond(response)
        state = "succeeded"
        try:
            await _value(api.protocols.outbox.drain())
        except Exception as error:  # ruff: ignore[blind-except]
            state = type(error).__name__
        entry = await _value(api.protocols.outbox.inspect(receipt.entry_id))
        lines.append(
            f"    replay={state}/{entry.state} providers={provider.calls - before_provider} "
            f"sends={(len(exchange.lines) - before_sends) // 2}"
        )
        if len(exchange.lines) > before_sends:
            lines.extend((exchange.lines[-2], f"    patches={sent_headers}"))
        exchange.lines.clear()
        exchange.responders.clear()
        provider.calls = 0
        await close(api)

    for mutation in inputs["mutations"]:
        store = _Store(public)
        patches = {"headers": tuple(map(tuple, inputs["headers"])), "query": tuple(map(tuple, inputs["query"]))}
        operation = (
            "nested" if mutation == "nested-order" else "repeated" if mutation.startswith("repeated") else "deep"
        )
        value = (
            {"alpha": {"a": "cake", "z": "tea"}}
            if operation == "nested"
            else ["alpha", "zulu"]
            if operation == "repeated"
            else {"alpha": "cake", "zulu": "tea"}
        )
        name = "tag" if operation == "repeated" else "filter"
        api = client(store, **patches)
        receipt = await _value(
            getattr(api.protocols.outbox.operations, operation).enqueue(**{
                name: argument(package, f"get{operation.title()}", "query", name, value)
            })
        )
        entry = await _value(api.protocols.outbox.inspect(receipt.entry_id))
        await close(api)
        if mutation in {"argument-order", "argument-value", "nested-order", "repeated-order", "repeated-value"}:
            payload = json.loads(entry.payload)
            given = payload["arguments"][0][0]
            if mutation == "nested-order":
                given["alpha"] = dict(reversed(given["alpha"].items()))
            elif mutation == "argument-order":
                payload["arguments"][0][0] = dict(reversed(given.items()))
            elif mutation.endswith("order"):
                given.reverse()
            elif mutation == "repeated-value":
                given[0] = "changed"
            else:
                given["alpha"] = "changed"
            store.tamper(receipt.entry_id, payload=json.dumps(payload).encode())
        elif mutation == "basepath":
            patches["base_url"] = "https://api.example.com/other"
        else:
            kind = mutation.split("-")[0]
            patches[kind if kind == "query" else "headers"] = (
                tuple(reversed(patches[kind if kind == "query" else "headers"]))
                if mutation.endswith("order")
                else (("patch" if kind == "query" else "X-Zulu", "changed"),)
            )
        api = client(store, **patches)
        state = "accepted"
        try:
            await _value(api.protocols.outbox.drain())
        except Exception as error:  # ruff: ignore[blind-except]
            state = type(error).__name__
        lines.append(f"  changed {mutation}: {state} providers={provider.calls} sends={len(exchange.lines) // 2}")
        await close(api)
    await _value(native.aclose() if asynchronous else native.close())


def queue_order(package: ModuleType, lines: list[str]) -> None:
    """Replay reordered, repeated and nested wire values in both execution modes."""

    async def scenario() -> None:
        await _mode(package, lines, False)
        await _mode(package, lines, True)

    run(scenario)
