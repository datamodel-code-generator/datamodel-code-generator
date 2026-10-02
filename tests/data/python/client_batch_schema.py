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
from tests.data.python.parameter_adapters import _caller

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from types import ModuleType


class _CompiledArray:
    """Validate a captured array or item schema through the package's public schema-adapter contract."""

    def __init__(self, public: Any, validator: Any, schema_id: str) -> None:
        self.public, self.validator, self.schema_id = public, validator, schema_id

    def validate(self, *, wire: Any, context: Any) -> tuple[Any, ...]:
        del context
        return tuple(
            self.public.WireIssue(
                code=f"schema.{error.validator}",
                message=f"The value fails {error.validator}",
                instance_pointer="".join(f"/{part}" for part in error.absolute_path),
                schema_id=self.schema_id,
                schema_pointer="".join(f"/{part}" for part in error.absolute_schema_path),
            )
            for error in self.validator.iter_errors(self.public.thaw_wire(wire))
        )


class _ArraySchema:
    """Own the whole request schema and its captured item uses without replacing their model codecs."""

    api_version = 1

    def __init__(self) -> None:
        self.public = _caller()

    def capabilities(self, *, schema_plan: Any) -> Any:
        del schema_plan
        return self.public.SchemaCodecCapabilities(
            dialects=("https://json-schema.org/draft/2020-12/schema",),
            vocabularies=(),
            keywords=("$ref", "type", "items", "properties", "required", "minItems", "minLength"),
            pattern_dialects=(),
        )

    def compile(self, *, schema_plan: Any, offline_registry: Any) -> _CompiledArray:
        from jsonschema import Draft202012Validator

        validator = Draft202012Validator(
            self.public.thaw_wire(schema_plan.root.normalized),
            registry=offline_registry.as_referencing_registry(),
        )
        return _CompiledArray(self.public, validator, schema_plan.root.schema_id)


def array_schema() -> _ArraySchema:
    """Create the application-owned schema adapter through the generated package's public module."""
    return _ArraySchema()


class _TagServer(Exchange):
    """Answer actual whole-array requests through the normal public transport injection."""

    def __init__(self) -> None:
        super().__init__([])
        self.arrivals = 0

    def handle(self, request: Any) -> Any:
        request.read()
        self.arrivals += 1
        return json_response(200, {"items": [{"tag": item} for item in json.loads(request.content)]})(request)


def batch_schema_adapter(package: ModuleType, lines: list[str]) -> None:
    """Accept a valid two-item array and reject its aggregate and item violations in both modes."""
    models = importlib.import_module(package.__name__ + "_models")
    for label, values in (("two", ("one", "two")), ("one", ("one",)), ("invalid", ("", "two"))):
        server = _TagServer()
        with server.client() as native, package.Client(http_client=native) as api:
            try:
                items = [models.Tag(label=value or "initial") for value in values]
                if label == "invalid":
                    if isinstance(items[0], dict):
                        items[0]["label"] = ""
                    else:
                        items[0].label = ""
                with api.protocols.tags.put.iterate(items) as iterator:
                    list(iterator)
                outcome = "ok"
            except Exception as error:  # noqa: BLE001
                outcome = type(error).__name__
        lines.append(f"  sync {label} {outcome} arrivals={server.arrivals}")

    async def asynchronous() -> None:
        for label, values in (("two", ("one", "two")), ("one", ("one",)), ("invalid", ("", "two"))):
            server = _TagServer()
            async with server.async_client() as native, package.AsyncClient(http_client=native) as api:
                try:
                    items = [models.Tag(label=value or "initial") for value in values]
                    if label == "invalid":
                        if isinstance(items[0], dict):
                            items[0]["label"] = ""
                        else:
                            items[0].label = ""
                    async with api.protocols.tags.put.iterate(items) as iterator:
                        [item async for item in iterator]
                    outcome = "ok"
                except Exception as error:  # noqa: BLE001
                    outcome = type(error).__name__
            lines.append(f"  async {label} {outcome} arrivals={server.arrivals}")

    asyncio.run(asynchronous())


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
