"""Apply literal array and body contracts to generated batch requests through public SDK calls."""

from __future__ import annotations

import asyncio
import importlib
import json
from collections import UserDict
from itertools import product
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tests.data.python.client_pagination import Harness
from tests.data.python.client_runtime import Exchange, json_response

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from types import ModuleType


class _SchemaServer(Exchange):
    """Answer assembled requests and retain their literal bodies and retry keys."""

    def __init__(self) -> None:
        super().__init__([])
        self.requests: list[tuple[bytes, str | None]] = []
        self.retry = False

    def handle(self, request: Any) -> Any:
        request.read()
        self.requests.append((request.content, request.headers.get("Idempotency-Key")))
        if self.retry and len(self.requests) == 1:
            return json_response(503, {"message": "busy"})(request)
        body = json.loads(request.content)
        items = body["item-list"] if isinstance(body, dict) else body
        return json_response(200, {"results": [{"value": {"id": item["id"]}} for item in items]})(request)


class _Admission:
    """Count public limiter admission without replacing SDK implementation details."""

    def __init__(self) -> None:
        self.calls = 0

    def acquire(self, context: Any) -> _Admission:
        del context
        self.calls += 1
        return self

    def release(self) -> None:
        """Release the public permit after its request."""


class _AsyncAdmission(_Admission):
    async def acquire(self, context: Any) -> _AsyncAdmission:
        del context
        self.calls += 1
        return self

    async def release(self) -> None:
        """Release the public asynchronous permit after its request."""


def _items(models: ModuleType, control: dict[str, Any]) -> list[Any]:
    values = []
    for index, label in enumerate(control["labels"]):
        fields = {
            "id": "same" if control.get("same_id") else f"i{index}",
            (
                "wire-label" if "pydantic" in models.__name__ or "typing_typeddict" in models.__name__ else "wire_label"
            ): label or "initial",
        }
        if not control.get("omit"):
            fields.update({
                "tier": "basic",
                "secret": "sent",
                ("server-note" if "pydantic" in models.__name__ else "server_note"): "excluded",
            })
        value = models.Item(**fields)
        if not label:
            if isinstance(value, dict):
                value["wire-label"] = ""
            else:
                value.wire_label = ""
        values.append(value)
    return values


def _read_items(models: ModuleType) -> tuple[list[Any], dict[str, int]]:
    """Observe application-owned required fields through their public native input objects."""
    reads = {"id": 0, "wire_label": 0}
    if "typing_typeddict" in models.__name__:

        class ReadItem(UserDict[str, Any]):
            def get(self, name: str, default: Any = None) -> Any:
                field = "wire_label" if name == "wire-label" else name
                if field in reads:
                    reads[field] += 1
                return super().get(name, default)

    else:

        class ReadItem(models.Item):
            def __getattribute__(self, name: str) -> Any:
                if name in reads:
                    reads[name] += 1
                return super().__getattribute__(name)

    label = "wire-label" if "pydantic" in models.__name__ or "typing_typeddict" in models.__name__ else "wire_label"
    values = [ReadItem(**{"id": f"i{index}", label: value}) for index, value in enumerate(("one", "two"))]
    reads.update(id=0, wire_label=0)
    return values, reads


async def _source(values: list[Any]) -> AsyncIterator[Any]:
    for value in values:
        await asyncio.sleep(0)
        yield value


def _observed(
    lines: list[str],
    label: str,
    iterator: Any,
    records: list[Any],
    error: Exception | None,
    server: _SchemaServer,
    limiter: _Admission,
) -> None:
    outcome = "ok" if error is None else f"{type(error).__name__}:{getattr(error, 'location', None)}"
    lines.append(
        f"  {label} {outcome} indices={[item.index for item in records]} "
        f"arrivals={len(server.requests)} admissions={limiter.calls} "
        f"sends={iterator.progress['network_send_count']} budget={iterator.progress['network_send_budget_used']}"
    )
    lines.extend(f"    body={body!r}" for body, _ in server.requests)


def batch_schema(package: ModuleType, lines: list[str]) -> None:
    """Exercise aliases, directions, aggregate validation modes, original error indices and replay stability."""
    harness = Harness(package)
    models = importlib.import_module(package.__name__ + "_models")
    controls = json.loads(
        (Path(__file__).parents[1] / "generation_platform/client/batch-schema-controls.json").read_text()
    )
    modes = ["schema", "none"]
    if "native" in package.__name__:
        modes.append("native")
    for mode in modes:
        for number, control in enumerate(controls):
            if mode == "native" and "index" in control:
                continue
            server, limiter = _SchemaServer(), _Admission()
            with (
                server.client() as native,
                package.Client(http_client=native, options=harness.client_options(limiter=limiter)) as api,
            ):
                helper = getattr(api.protocols.schema, control["name"])
                options = harness.options.RequestOptions(validation=harness.options.ValidationOptions(request=mode))
                iterator = helper.iterate(
                    _items(models, control),
                    options=options,
                    batch_options=harness.protocols.BatchOptions(parallelism=1),
                )
                records, error = [], None
                with iterator:
                    try:
                        records = list(iterator)
                    except Exception as failure:  # noqa: BLE001
                        error = failure
                _observed(lines, f"sync {mode} {number}:{control['name']}", iterator, records, error, server, limiter)

    async def asynchronous() -> None:
        for mode, (number, control), source_kind in product(modes, enumerate(controls), ("Iterable", "AsyncIterable")):
            if mode == "native" and "index" in control:
                continue
            values = _items(models, control)

            server, limiter = _SchemaServer(), _AsyncAdmission()
            async with (
                server.async_client() as native,
                package.AsyncClient(http_client=native, options=harness.client_options(limiter=limiter)) as api,
            ):
                helper = getattr(api.protocols.schema, control["name"])
                options = harness.options.RequestOptions(validation=harness.options.ValidationOptions(request=mode))
                iterator = helper.iterate(
                    values if source_kind == "Iterable" else _source(values),
                    options=options,
                    batch_options=harness.protocols.BatchOptions(parallelism=1),
                )
                records, error = [], None
                async with iterator:
                    try:
                        records = [item async for item in iterator]
                    except Exception as failure:  # noqa: BLE001
                        error = failure
                _observed(
                    lines,
                    f"async-{source_kind} {mode} {number}:{control['name']}",
                    iterator,
                    records,
                    error,
                    server,
                    limiter,
                )

    asyncio.run(asynchronous())
    for name in ("property_min", "root_min"):
        server, limiter = _SchemaServer(), _Admission()
        server.retry = True
        with (
            server.client() as native,
            package.Client(http_client=native, options=harness.client_options(limiter=limiter)) as api,
        ):
            iterator = getattr(api.protocols.schema, name).iterate(
                _items(models, controls[0]), batch_options=harness.protocols.BatchOptions(parallelism=1)
            )
            with iterator:
                records = list(iterator)
            _observed(lines, f"retry {name}", iterator, records, None, server, limiter)
            first, second = server.requests
            lines.append(
                f"    same-body={first[0] == second[0]} same-key={first[1] == second[1]} "
                f"key-present={first[1] is not None}"
            )
    for mode in modes:
        if mode == "native":
            continue
        for name in ("property_min", "root_min"):
            server, limiter = _SchemaServer(), _Admission()
            values, reads = _read_items(models)
            with (
                server.client() as native,
                package.Client(http_client=native, options=harness.client_options(limiter=limiter)) as api,
            ):
                options = harness.options.RequestOptions(validation=harness.options.ValidationOptions(request=mode))
                iterator = getattr(api.protocols.schema, name).iterate(values, options=options)
                with iterator:
                    records = list(iterator)
                _observed(lines, f"getters {mode} {name}", iterator, records, None, server, limiter)
                lines.append(f"    original-field-reads={reads}")
