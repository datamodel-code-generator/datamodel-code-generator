"""Observe empty batch compression admission through generated helpers and an independent HTTP server."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from tests.data.python.client_auth_flows import _AsyncProvider, _bearer, _Provider
from tests.data.python.client_batches import _BatchCompressionServer, _Batches, _Interrupt
from tests.data.python.client_runtime import run

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType


class _Source:
    """Count item acquisitions separately from attempts to discover exhaustion."""

    def __init__(self, items: list[Any], failure: BaseException | None = None) -> None:
        self.items = items
        self.failure = failure
        self.reads = 0
        self.pulls = 0

    def __iter__(self) -> Iterator[Any]:
        return self

    def __next__(self) -> Any:
        self.pulls += 1
        if self.failure is not None:
            raise self.failure
        if self.reads == len(self.items):
            raise StopIteration
        value = self.items[self.reads]
        self.reads += 1
        return value


class _AsyncSource(_Source):
    """Expose the same acquisitions as an asynchronous input source."""

    def __aiter__(self) -> _AsyncSource:
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self)
        except StopIteration:
            raise StopAsyncIteration from None


class _Server(_BatchCompressionServer):
    """Count real resource arrivals while retaining their coding and decoded body witnesses."""

    def __init__(self, lines: list[str]) -> None:
        super().__init__(lines)
        self.sends = 0

    def handle(self, request: Any) -> Any:
        self.sends += 1
        return super().handle(request)


def _cases(harness: _Batches) -> Iterator[tuple[str, str, Any, list[Any], dict[str, Any], BaseException | None]]:
    """Supply declared and undeclared operations, absent candidates, and ordinary validation failures."""
    gzip = harness.options.RequestOptions(compression="gzip")
    off = harness.options.RequestOptions(compression=None)
    inherited = harness.options.RequestOptions()
    for label, helper, options, items, arguments in (
        ("declared explicit nonempty", "users", gzip, harness.users(1, start=1), {}),
        ("undeclared explicit nonempty", "tags", gzip, harness.tags("red"), {}),
        ("declared explicit empty", "users", gzip, [], {}),
        ("declared explicit zero", "users", gzip, harness.users(1, start=1), {"max_items": 0}),
        ("declared inherited empty", "users", None, [], {}),
        ("undeclared inherited empty", "tags", None, [], {}),
        ("declared unset empty", "users", inherited, [], {}),
        ("declared off empty", "users", off, [], {}),
        ("declared inherited zero", "users", None, harness.users(1, start=1), {"max_items": 0}),
        ("declared off zero", "users", off, harness.users(1, start=1), {"max_items": 0}),
        ("invalid shared empty", "users", gzip, [], {"dry_run": [1]}),
        ("invalid shared zero", "users", gzip, harness.users(1, start=1), {"max_items": 0, "dry_run": [1]}),
    ):
        yield label, helper, options, items, arguments, None
    for label, failure in (("source failure", ValueError("input marker")), ("native stop", _Interrupt())):
        yield label, "users", gzip, [], {}, failure


def _iterator(harness: _Batches, api: Any, helper: str, source: Any, options: Any, arguments: dict[str, Any]) -> Any:
    settings = dict(arguments)
    limit = settings.pop("max_items", None)
    operation = api.protocols.users.create if helper == "users" else api.protocols.tags.put
    return operation.iterate(source, options=options, batch_options=harness.batch(max_items=limit), **settings)


def _report(
    lines: list[str],
    label: str,
    iterator: Any,
    source: _Source,
    provider: Any,
    server: _Server,
    outcomes: list[str],
    error: BaseException | None,
) -> None:
    """Report public exception fields and independent input, provider, arrival, and budget observations."""
    detail = "None"
    if error is not None:
        cause = error.__cause__ or getattr(error, "cause", None)
        detail = (
            f"{type(error).__name__} field={getattr(error, 'field_path', None)!r} "
            f"condition={getattr(error, 'condition', None)!r} location={getattr(error, 'location', None)!r} "
            f"cause={type(cause).__name__ if cause is not None else None} identity={error is source.failure}"
        )
    progress = dict(iterator.progress)
    lines.append(
        f"  {label}: outcomes={outcomes} error={detail} reads={source.reads} pulls={source.pulls} "
        f"provider={len(provider.calls)} sends={server.sends} "
        f"budget={progress['network_send_count']}/{progress['network_send_budget_used']}"
    )


def batch_admission(package: ModuleType, lines: list[str]) -> None:
    """Drive lazy sync and async helper entries with both synchronous and asynchronous sources."""
    harness = _Batches(package)
    auth = importlib.import_module(f"{package.__name__}.auth")
    for label, helper, options, items, arguments, failure in _cases(harness):
        server = _Server(lines)
        source = _Source(items, failure)
        provider = _Provider(_bearer(auth))
        settings = harness.client_options(compression="gzip", auth=auth.AuthConfig({"bearer": provider}))
        with (
            server.client() as native,
            package.Client(http_client=native, options=settings) as api,
            _iterator(harness, api, helper, source, options, arguments) as iterator,
        ):
            lines.append(
                f"  sync {label} factory: reads={source.reads} pulls={source.pulls} "
                f"provider={len(provider.calls)} sends={server.sends}"
            )
            outcomes: list[str] = []
            error: BaseException | None = None
            try:
                outcomes.extend(item.outcome for item in iterator)
            except BaseException as stopped:  # noqa: BLE001 - Preserve public native-stop identity in the report.
                error = stopped
            _report(lines, f"sync {label}", iterator, source, provider, server, outcomes, error)
    run(lambda: _async_admission(harness, auth, lines))


async def _async_admission(harness: _Batches, auth: ModuleType, lines: list[str]) -> None:
    for mode, kind in (("async iterable", _Source), ("async source", _AsyncSource)):
        for label, helper, options, items, arguments, failure in _cases(harness):
            server = _Server(lines)
            source = kind(items, failure)
            provider = _AsyncProvider(_bearer(auth))
            settings = harness.client_options(compression="gzip", auth=auth.AuthConfig({"bearer": provider}))
            async with (
                server.async_client() as native,
                harness.package.AsyncClient(http_client=native, options=settings) as api,
                _iterator(harness, api, helper, source, options, arguments) as iterator,
            ):
                lines.append(
                    f"  {mode} {label} factory: reads={source.reads} pulls={source.pulls} "
                    f"provider={len(provider.calls)} sends={server.sends}"
                )
                outcomes: list[str] = []
                error: BaseException | None = None
                try:
                    async for item in iterator:
                        outcomes.append(item.outcome)  # noqa: PERF401 - Retain outcomes preceding a failure.
                except BaseException as stopped:  # noqa: BLE001 - Report the original public native interruption.
                    error = stopped
                _report(lines, f"{mode} {label}", iterator, source, provider, server, outcomes, error)
