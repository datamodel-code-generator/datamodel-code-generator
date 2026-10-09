"""Exercise application semaphores and permit ownership through generated clients over real TLS."""

from __future__ import annotations

import asyncio
import importlib
import threading
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

import httpx2

from tests.data.python.client_bodies import _photo
from tests.data.python.client_runtime import (
    Exchange,
    abroken,
    aoutcome,
    arecord,
    argument,
    broken,
    failing,
    json_response,
    outcome,
    raw_response,
    record,
    run,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator
    from types import ModuleType

_PET = {"id": 3, "name": "fox"}
_PNG = b"\x89PNG"
_RAW = "https://raw.example.com/items?private=1"
_HOOK_FAILURES = (
    ("call_start", False),
    ("limiter_wait", False),
    ("limiter_acquired", False),
    ("attempt_start", False),
    ("response_headers", True),
    ("attempt_end", True),
    ("call_end", True),
)


class _Context(Protocol):
    """The public facts that the application limiter reads from its generated context."""

    @property
    def operation_id(self) -> str | None: ...

    @property
    def origin(self) -> str: ...

    @property
    def call_id(self) -> str: ...

    @property
    def parent_session_id(self) -> str | None: ...

    @property
    def remaining_timeout(self) -> float | None: ...


class _Event(Protocol):
    """The public event facts recorded by the application's hooks."""

    @property
    def name(self) -> str: ...

    @property
    def call_id(self) -> str: ...

    @property
    def attempt_count(self) -> int: ...


class _Usage:
    """Thread-safe accounting belonging to an application semaphore."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.contexts: list[_Context] = []
        self.grants = 0
        self.releases = 0
        self.release_calls = 0
        self.active = 0
        self.peak = 0

    def enter(self, context: _Context) -> int:
        with self.lock:
            self.contexts.append(context)
            return len(self.contexts)

    def grant(self) -> None:
        with self.lock:
            self.grants += 1
            self.active += 1
            self.peak = max(self.peak, self.active)

    @property
    def report(self) -> str:
        with self.lock:
            return (
                f"entered={len(self.contexts)} granted={self.grants} released={self.releases} "
                f"release_calls={self.release_calls} active={self.active} peak={self.peak}"
            )


class _Permit:
    """An idempotent semaphore permit, optionally failing after it returns its slot."""

    def __init__(self, usage: _Usage, release_slot: Callable[[], None], failure: BaseException | None) -> None:
        self.usage = usage
        self.release_slot = release_slot
        self.failure = failure
        self.released = False

    def release(self) -> None:
        with self.usage.lock:
            self.usage.release_calls += 1
            if self.released:
                return
            self.released = True
            self.usage.active -= 1
            self.usage.releases += 1
        self.release_slot()
        if self.failure is not None:
            raise self.failure


class _AsyncPermit:
    """The asynchronous callback shape of the same idempotent permit."""

    def __init__(self, permit: _Permit) -> None:
        self.permit = permit

    async def release(self) -> None:
        self.permit.release()


class _SemaphoreLimiter:
    """An application limiter backed by a bounded threading semaphore."""

    def __init__(
        self, *, acquire_failure: BaseException | None = None, release_failure: BaseException | None = None
    ) -> None:
        self.usage = _Usage()
        self.semaphore = threading.BoundedSemaphore(1)
        self.waiting = threading.Event()
        self.acquire_failure = acquire_failure
        self.release_failure = release_failure

    def acquire(self, context: _Context) -> _Permit:
        if self.usage.enter(context) > 1:
            self.waiting.set()
        if self.acquire_failure is not None:
            raise self.acquire_failure
        if not self.semaphore.acquire(timeout=context.remaining_timeout):
            msg = "Application semaphore wait expired"
            raise TimeoutError(msg)
        self.usage.grant()
        return _Permit(self.usage, self.semaphore.release, self.release_failure)


class _AsyncSemaphoreLimiter:
    """An application limiter backed by an asyncio bounded semaphore."""

    def __init__(
        self, *, acquire_failure: BaseException | None = None, release_failure: BaseException | None = None
    ) -> None:
        self.usage = _Usage()
        self.semaphore = asyncio.BoundedSemaphore(1)
        self.waiting = asyncio.Event()
        self.acquire_failure = acquire_failure
        self.release_failure = release_failure

    async def acquire(self, context: _Context) -> _AsyncPermit:
        if self.usage.enter(context) > 1:
            self.waiting.set()
        if self.acquire_failure is not None:
            raise self.acquire_failure
        await self.semaphore.acquire()
        self.usage.grant()
        return _AsyncPermit(_Permit(self.usage, self.semaphore.release, self.release_failure))


class _Events:
    """Record event order and permit ownership, raising only on an explicitly selected failure event."""

    def __init__(self, usage: _Usage, failing: str | None = None) -> None:
        self.usage = usage
        self.failing = failing
        self.names: list[str] = []
        self.ids: set[str] = set()
        self.ends: list[int] = []

    def on_event(self, event: _Event) -> None:
        self.names.append(f"{event.name}:{self.usage.active}")
        self.ids.add(event.call_id)
        if event.name == "call_end":
            self.ends.append(event.attempt_count)
        if event.name == self.failing:
            msg = f"Hook failed on {event.name}"
            raise RuntimeError(msg)


class _AsyncEvents:
    """Deliver the same recorder through the asynchronous hook contract."""

    def __init__(self, events: _Events) -> None:
        self.events = events

    async def on_event(self, event: _Event) -> None:
        self.events.on_event(event)


class _Payload:
    """An application's upload payload that records whether its permit is held when its bytes are read."""

    def __init__(self, usage: _Usage, *, failing: bool = False) -> None:
        self.usage = usage
        self.failing = failing
        self.opened: list[int] = []

    def _open(self) -> None:
        self.opened.append(self.usage.active)
        if self.failing:
            msg = "Upload payload failed"
            raise RuntimeError(msg)

    def __iter__(self) -> Iterator[bytes]:
        self._open()
        yield _PNG

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self._open()
        yield _PNG


def _modules(package: ModuleType) -> tuple[ModuleType, ModuleType, ModuleType]:
    options, bodies, types = (
        importlib.import_module(f"{package.__name__}.{name}") for name in ("options", "bodies", "types.pets")
    )
    return options, bodies, types


def _contexts(lines: list[str], usage: _Usage, events: _Events) -> None:
    """Report only safe limiter facts and relationships to the call's public events."""
    for context in usage.contexts:
        remaining = context.remaining_timeout
        bounded = "disabled" if remaining is None else str(0 < remaining <= 30)
        safe = not any(hasattr(context, name) for name in ("headers", "body", "query", "url"))
        lines.append(
            f"  context operation={context.operation_id} origin={context.origin} parent={context.parent_session_id} "
            f"remaining={bounded} "
            f"call={context.call_id in events.ids}/{UUID(context.call_id).version == 4} safe={safe}"
        )
    lines.append(f"  unique limiter calls={len({context.call_id for context in usage.contexts})}")
    lines.append(f"  events {' '.join(events.names)} ends={events.ends}")


def limiters(package: ModuleType, lines: list[str]) -> None:
    """Report real semaphore ownership, blocked body opening, pre-send refusals, and callback cleanup."""
    _sync_ownership(package, lines)
    _sync_waiting_body(package, lines)
    _sync_failures(package, lines)
    _sync_hooks(package, lines)
    _sync_modes(package, lines)
    _sync_admission(package, lines)
    run(lambda: _async_limiters(package, lines))


def _sync_ownership(package: ModuleType, lines: list[str]) -> None:
    options, _, types = _modules(package)
    limiter = _SemaphoreLimiter()
    events = _Events(limiter.usage)
    exchange = Exchange(lines)
    http = exchange.client()
    configured = options.ClientOptions(limiter=limiter, hooks=(events,), total_timeout=30)
    pet = argument(package, "getPet", "path", "petId", 3)
    with package.Client(http_client=http, options=configured) as api:
        exchange.respond(json_response(200, _PET))
        record(lines, "limited typed", lambda: api.pets.with_response.get_pet(pet_id=pet))
        lines.append(f"  typed returned {limiter.usage.report}")
        exchange.respond(json_response(200, _PET))
        raw = api.pets.with_raw_response.get_pet(pet_id=pet)
        record(lines, "limited raw bytes", raw.read)
        raw.close()
        raw.close()
        lines.append(f"  buffered raw released {limiter.usage.report}")
        exchange.respond(json_response(200, _PET))
        with api.pets.with_streaming_response.get_pet(pet_id=pet) as stream:
            lines.append(f"  early stream held {limiter.usage.report}")
        stream.close()
        lines.append(f"  early stream released {limiter.usage.report}")
        exchange.respond(json_response(200, _PET))
        with api.pets.with_streaming_response.get_pet(pet_id=pet) as stream:
            record(lines, "limited stream read", stream.read)
            lines.append(f"  exhausted stream released {limiter.usage.report}")
        exchange.respond(raw_response(200, b"raw", "text/plain"))
        raw = api.request_raw("GET", _RAW, options=options.RequestOptions(total_timeout=None))
        record(lines, "limited request_raw", raw.read)
        lines.append(f"  request_raw released {limiter.usage.report}")
        exchange.respond(raw_response(200, b"raw", "text/plain"))
        with api.with_streaming_response.request_raw("GET", _RAW):
            lines.append(f"  raw stream held {limiter.usage.report}")
        lines.append(f"  raw stream released {limiter.usage.report}")
        replacement = _SemaphoreLimiter()
        request_limiter = _SemaphoreLimiter()
        view = api.with_options(options.RequestOptions(limiter=replacement, hooks=()))
        exchange.respond(json_response(200, _PET), json_response(200, _PET), json_response(200, _PET))
        record(lines, "view replacement", lambda: view.pets.get_pet(pet_id=pet))
        record(
            lines,
            "request replacement",
            lambda: view.pets.get_pet(pet_id=pet, options=options.RequestOptions(limiter=request_limiter)),
        )
        record(
            lines,
            "limiter disabled",
            lambda: view.pets.get_pet(pet_id=pet, options=options.RequestOptions(limiter=None)),
        )
        lines.append(f"  client limiter {limiter.usage.report}")
        lines.append(f"  view limiter {replacement.usage.report}")
        lines.append(f"  request limiter {request_limiter.usage.report}")
    http.close()
    _contexts(lines, limiter.usage, events)


def _sync_waiting_body(package: ModuleType, lines: list[str]) -> None:
    options, bodies, types = _modules(package)
    limiter = _SemaphoreLimiter()
    payload = _Payload(limiter.usage)
    exchange = Exchange(lines)
    exchange.respond(json_response(200, _PET), raw_response(200, _PNG, "image/png"))
    http = exchange.client()
    result: list[str] = []
    configured = options.ClientOptions(limiter=limiter, total_timeout=30)
    with package.Client(http_client=http, options=configured) as api:
        pet = argument(package, "getPet", "path", "petId", 3)
        with api.pets.with_streaming_response.get_pet(pet_id=pet):
            upload = threading.Thread(
                target=lambda: record(
                    result,
                    "waiting upload",
                    lambda: api.pets.photos.upload(pet_id=_photo(package), body=payload),
                ),
                daemon=True,
            )
            upload.start()
            waiting = limiter.waiting.wait(timeout=10)
            lines.append(
                f"  waiting behind stream={waiting} payload={payload.opened} queued={len(exchange.responders)} "
                f"{limiter.usage.report}"
            )
        upload.join(timeout=10)
        lines.extend(result)
        lines.append(f"  waiting upload joined={not upload.is_alive()} payload={payload.opened}")
        lines.append(f"  semaphore concurrency {limiter.usage.report}")
    http.close()


def _sync_failures(package: ModuleType, lines: list[str]) -> None:
    options, bodies, types = _modules(package)
    exchange = Exchange(lines)
    http = exchange.client()
    pet = argument(package, "getPet", "path", "petId", 3)
    with package.Client(
        http_client=http, options=options.ClientOptions(retry=options.RetryOptions(initial_delay=0))
    ) as api:
        for label, acquire_failure, release_failure, responses, secondary in (
            ("acquire failure", RuntimeError("acquire failed"), None, (), False),
            ("release failure", None, RuntimeError("release failed"), (json_response(200, _PET),), False),
            ("status failure", None, None, (raw_response(404),), False),
            ("decode failure", None, None, (raw_response(200, b"{", "application/json"),), False),
            ("status and release failure", None, RuntimeError("release failed"), (raw_response(404),), True),
            ("body read failure never resent", None, None, (broken, json_response(200, _PET)), False),
            ("body read and release failure", None, RuntimeError("release failed"), (broken,), True),
            (
                "unsent connect failure resent",
                None,
                None,
                (failing(httpx2.ConnectError), json_response(200, _PET)),
                False,
            ),
            (
                "sent read failure never resent",
                None,
                None,
                (failing(httpx2.ReadError), json_response(200, _PET)),
                False,
            ),
        ):
            limiter = _SemaphoreLimiter(acquire_failure=acquire_failure, release_failure=release_failure)
            exchange.respond(*responses)
            configured = options.RequestOptions(limiter=limiter)
            call = lambda configured=configured: api.pets.get_pet(pet_id=pet, options=configured)
            if secondary:
                lines.append(f"  {label}: {outcome(call)}")
            else:
                record(lines, label, call)
            lines.append(f"    {limiter.usage.report} queued={len(exchange.responders)}")
            exchange.responders.clear()
        limiter = _SemaphoreLimiter(release_failure=RuntimeError("release failed"))
        exchange.respond(json_response(200, _PET))
        record(
            lines,
            "buffered raw release failure",
            lambda: api.pets.with_raw_response.get_pet(pet_id=pet, options=options.RequestOptions(limiter=limiter)),
        )
        lines.append(f"    {limiter.usage.report}")
        limiter = _SemaphoreLimiter(release_failure=RuntimeError("release failed"))
        exchange.respond(raw_response(200, b"raw", "text/plain"))
        record(
            lines,
            "request_raw release failure",
            lambda: api.request_raw("GET", _RAW, options=options.RequestOptions(limiter=limiter)),
        )
        lines.append(f"    {limiter.usage.report}")
        limiter = _SemaphoreLimiter(release_failure=RuntimeError("release failed"))
        exchange.respond(json_response(200, _PET))
        with api.pets.with_streaming_response.get_pet(
            pet_id=pet, options=options.RequestOptions(limiter=limiter)
        ) as stream:
            record(lines, "stream close release failure", stream.close)
        lines.append(f"    {limiter.usage.report}")
        limiter = _SemaphoreLimiter()
        payload = _Payload(limiter.usage, failing=True)
        record(
            lines,
            "payload failure after grant",
            lambda: api.pets.photos.upload(
                pet_id=_photo(package),
                body=payload,
                options=options.RequestOptions(limiter=limiter),
            ),
        )
        lines.append(f"  failed payload {payload.opened} {limiter.usage.report}")
        for failure in (KeyboardInterrupt(), SystemExit(7)):
            limiter = _SemaphoreLimiter(acquire_failure=failure)
            try:
                api.pets.get_pet(pet_id=pet, options=options.RequestOptions(limiter=limiter))
            except BaseException as error:
                lines.append(f"  native acquire interruption={type(error).__name__} identity={error is failure}")
            lines.append(f"    {limiter.usage.report}")
        limiter = _SemaphoreLimiter()
        exchange.respond(broken)
        with api.pets.with_streaming_response.get_pet(
            pet_id=pet, options=options.RequestOptions(limiter=limiter)
        ) as stream:
            record(lines, "limited stream read failure", stream.read)
        lines.append(f"  failed stream released {limiter.usage.report}")
    http.close()


def _sync_hooks(package: ModuleType, lines: list[str]) -> None:
    options, _, types = _modules(package)
    exchange = Exchange(lines)
    http = exchange.client()
    pet = argument(package, "getPet", "path", "petId", 3)
    with package.Client(http_client=http) as api:
        for event, sent in _HOOK_FAILURES:
            limiter = _SemaphoreLimiter()
            events = _Events(limiter.usage, event)
            if sent:
                exchange.respond(json_response(200, _PET))
            configured = options.RequestOptions(limiter=limiter, hooks=(events,))
            lines.append(f"  hook {event}: {outcome(lambda: api.pets.get_pet(pet_id=pet, options=configured))}")
            lines.append(f"    {limiter.usage.report} events={' '.join(events.names)} ends={events.ends}")
        for event in ("attempt_end", "call_end"):
            limiter = _SemaphoreLimiter()
            events = _Events(limiter.usage, event)
            exchange.respond(json_response(200, _PET))
            configured = options.RequestOptions(limiter=limiter, hooks=(events,))
            manager = api.pets.with_streaming_response.get_pet(pet_id=pet, options=configured)
            lines.append(f"  stream handoff hook {event}: {outcome(manager.__enter__)}")
            lines.append(f"    {limiter.usage.report} events={' '.join(events.names)}")
        limiter = _SemaphoreLimiter(release_failure=RuntimeError("release failed"))
        events = _Events(limiter.usage, "limiter_acquired")
        configured = options.RequestOptions(limiter=limiter, hooks=(events,))
        lines.append(f"  hook and release failure: {outcome(lambda: api.pets.get_pet(pet_id=pet, options=configured))}")
        lines.append(f"    {limiter.usage.report}")
    http.close()


def _sync_modes(package: ModuleType, lines: list[str]) -> None:
    options, _, types = _modules(package)
    exchange = Exchange(lines)
    http = exchange.client()
    pet = argument(package, "getPet", "path", "petId", 3)
    opposite = _AsyncSemaphoreLimiter()

    def configured_client() -> object:
        with package.Client(http_client=http, options=options.ClientOptions(limiter=opposite)) as api:
            return api.pets.get_pet(pet_id=pet)

    record(lines, "async limiter in sync client", configured_client)
    with package.Client(http_client=http) as api:
        record(
            lines,
            "async limiter in sync view",
            lambda: api.with_options(options.RequestOptions(limiter=opposite)).pets.get_pet(pet_id=pet),
        )
        record(
            lines,
            "async limiter in sync request",
            lambda: api.pets.get_pet(pet_id=pet, options=options.RequestOptions(limiter=opposite)),
        )
    lines.append(f"  opposite sync mode {opposite.usage.report} queued={len(exchange.responders)}")
    http.close()


def _sync_admission(package: ModuleType, lines: list[str]) -> None:
    options, bodies, _ = _modules(package)
    exchange = Exchange(lines)
    exchange.respond(raw_response(200, _PNG, "image/png"))
    http = exchange.client()
    with package.Client(http_client=http) as api:
        for label, refused in (("zero timeout", {"total_timeout": 0}), ("closed client admission", {})):
            limiter = _SemaphoreLimiter()
            payload = _Payload(limiter.usage)
            events = _Events(limiter.usage)
            request = options.RequestOptions(limiter=limiter, hooks=(events,), **refused)
            if not refused:
                api.close()
            record(
                lines,
                label,
                lambda: api.pets.photos.upload(pet_id=_photo(package), body=payload, options=request),
            )
            lines.append(
                f"    {limiter.usage.report} payload={payload.opened} "
                f"events={' '.join(events.names)} ends={events.ends} queued={len(exchange.responders)}"
            )
    http.close()


async def _async_limiters(package: ModuleType, lines: list[str]) -> None:
    await _async_ownership(package, lines)
    await _async_waiting_body(package, lines)
    await _async_failures(package, lines)
    await _async_hooks(package, lines)
    await _async_modes(package, lines)
    await _async_admission(package, lines)


async def _async_ownership(package: ModuleType, lines: list[str]) -> None:
    options, _, types = _modules(package)
    limiter = _AsyncSemaphoreLimiter()
    events = _Events(limiter.usage)
    exchange = Exchange(lines)
    http = exchange.async_client()
    configured = options.ClientOptions(limiter=limiter, hooks=(_AsyncEvents(events),), total_timeout=30)
    pet = argument(package, "getPet", "path", "petId", 3)
    async with package.AsyncClient(http_client=http, options=configured) as api:
        exchange.respond(json_response(200, _PET))
        await arecord(lines, "async limited typed", lambda: api.pets.with_response.get_pet(pet_id=pet))
        lines.append(f"  async typed returned {limiter.usage.report}")
        exchange.respond(json_response(200, _PET))
        raw = await api.pets.with_raw_response.get_pet(pet_id=pet)
        await arecord(lines, "async limited raw bytes", raw.read)
        await raw.aclose()
        await raw.aclose()
        lines.append(f"  async buffered raw released {limiter.usage.report}")
        exchange.respond(json_response(200, _PET))
        async with api.pets.with_streaming_response.get_pet(pet_id=pet) as stream:
            lines.append(f"  async early stream held {limiter.usage.report}")
        await stream.aclose()
        lines.append(f"  async early stream released {limiter.usage.report}")
        exchange.respond(json_response(200, _PET))
        async with api.pets.with_streaming_response.get_pet(pet_id=pet) as stream:
            await arecord(lines, "async limited stream read", stream.read)
            lines.append(f"  async exhausted stream released {limiter.usage.report}")
        exchange.respond(raw_response(200, b"raw", "text/plain"))
        raw = await api.request_raw("GET", _RAW, options=options.RequestOptions(total_timeout=None))
        await arecord(lines, "async limited request_raw", raw.read)
        lines.append(f"  async request_raw released {limiter.usage.report}")
        exchange.respond(raw_response(200, b"raw", "text/plain"))
        async with api.with_streaming_response.request_raw("GET", _RAW):
            lines.append(f"  async raw stream held {limiter.usage.report}")
        lines.append(f"  async raw stream released {limiter.usage.report}")
        replacement = _AsyncSemaphoreLimiter()
        request_limiter = _AsyncSemaphoreLimiter()
        view = api.with_options(options.RequestOptions(limiter=replacement, hooks=()))
        exchange.respond(json_response(200, _PET), json_response(200, _PET), json_response(200, _PET))
        await arecord(lines, "async view replacement", lambda: view.pets.get_pet(pet_id=pet))
        await arecord(
            lines,
            "async request replacement",
            lambda: view.pets.get_pet(pet_id=pet, options=options.RequestOptions(limiter=request_limiter)),
        )
        await arecord(
            lines,
            "async limiter disabled",
            lambda: view.pets.get_pet(pet_id=pet, options=options.RequestOptions(limiter=None)),
        )
        lines.append(f"  async client limiter {limiter.usage.report}")
        lines.append(f"  async view limiter {replacement.usage.report}")
        lines.append(f"  async request limiter {request_limiter.usage.report}")
    await http.aclose()
    _contexts(lines, limiter.usage, events)


async def _async_waiting_body(package: ModuleType, lines: list[str]) -> None:
    options, bodies, types = _modules(package)
    limiter = _AsyncSemaphoreLimiter()
    payload = _Payload(limiter.usage)
    exchange = Exchange(lines)
    exchange.respond(json_response(200, _PET), raw_response(200, _PNG, "image/png"))
    http = exchange.async_client()
    result: list[str] = []
    configured = options.ClientOptions(limiter=limiter, total_timeout=30)
    async with package.AsyncClient(http_client=http, options=configured) as api:
        pet = argument(package, "getPet", "path", "petId", 3)
        async with api.pets.with_streaming_response.get_pet(pet_id=pet):
            upload = asyncio.create_task(
                arecord(
                    result,
                    "async waiting upload",
                    lambda: api.pets.photos.upload(pet_id=_photo(package), body=payload),
                )
            )
            waiting = asyncio.create_task(limiter.waiting.wait())
            try:
                done, _ = await asyncio.wait((waiting,), timeout=10)
                lines.append(
                    f"  async waiting behind stream={bool(done)} payload={payload.opened} queued={len(exchange.responders)} "
                    f"{limiter.usage.report}"
                )
            finally:
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
        await upload
        lines.extend(result)
        lines.append(f"  async waiting upload payload={payload.opened}")
        lines.append(f"  async semaphore concurrency {limiter.usage.report}")
    await http.aclose()


async def _async_failures(package: ModuleType, lines: list[str]) -> None:
    options, bodies, types = _modules(package)
    exchange = Exchange(lines)
    http = exchange.async_client()
    pet = argument(package, "getPet", "path", "petId", 3)
    async with package.AsyncClient(
        http_client=http, options=options.ClientOptions(retry=options.RetryOptions(initial_delay=0))
    ) as api:
        for label, acquire_failure, release_failure, responses, secondary in (
            ("async acquire failure", RuntimeError("acquire failed"), None, (), False),
            ("async release failure", None, RuntimeError("release failed"), (json_response(200, _PET),), False),
            ("async status failure", None, None, (raw_response(404),), False),
            ("async decode failure", None, None, (raw_response(200, b"{", "application/json"),), False),
            ("async status and release failure", None, RuntimeError("release failed"), (raw_response(404),), True),
            ("async body read failure never resent", None, None, (abroken, json_response(200, _PET)), False),
            ("async body read and release failure", None, RuntimeError("release failed"), (abroken,), True),
            (
                "async unsent connect failure resent",
                None,
                None,
                (failing(httpx2.ConnectError), json_response(200, _PET)),
                False,
            ),
            (
                "async sent read failure never resent",
                None,
                None,
                (failing(httpx2.ReadError), json_response(200, _PET)),
                False,
            ),
        ):
            limiter = _AsyncSemaphoreLimiter(acquire_failure=acquire_failure, release_failure=release_failure)
            exchange.respond(*responses)
            configured = options.RequestOptions(limiter=limiter)
            call = lambda configured=configured: api.pets.get_pet(pet_id=pet, options=configured)
            if secondary:
                lines.append(f"  {label}: {await aoutcome(call)}")
            else:
                await arecord(lines, label, call)
            lines.append(f"    {limiter.usage.report} queued={len(exchange.responders)}")
            exchange.responders.clear()
        limiter = _AsyncSemaphoreLimiter(release_failure=RuntimeError("release failed"))
        exchange.respond(json_response(200, _PET))
        await arecord(
            lines,
            "async buffered raw release failure",
            lambda: api.pets.with_raw_response.get_pet(pet_id=pet, options=options.RequestOptions(limiter=limiter)),
        )
        lines.append(f"    {limiter.usage.report}")
        limiter = _AsyncSemaphoreLimiter(release_failure=RuntimeError("release failed"))
        exchange.respond(raw_response(200, b"raw", "text/plain"))
        await arecord(
            lines,
            "async request_raw release failure",
            lambda: api.request_raw("GET", _RAW, options=options.RequestOptions(limiter=limiter)),
        )
        lines.append(f"    {limiter.usage.report}")
        limiter = _AsyncSemaphoreLimiter(release_failure=RuntimeError("release failed"))
        exchange.respond(json_response(200, _PET))
        async with api.pets.with_streaming_response.get_pet(
            pet_id=pet, options=options.RequestOptions(limiter=limiter)
        ) as stream:
            await arecord(lines, "async stream close release failure", stream.aclose)
        lines.append(f"    {limiter.usage.report}")
        limiter = _AsyncSemaphoreLimiter()
        payload = _Payload(limiter.usage, failing=True)
        await arecord(
            lines,
            "async payload failure after grant",
            lambda: api.pets.photos.upload(
                pet_id=_photo(package),
                body=payload,
                options=options.RequestOptions(limiter=limiter),
            ),
        )
        lines.append(f"  async failed payload {payload.opened} {limiter.usage.report}")
        failure = asyncio.CancelledError()
        limiter = _AsyncSemaphoreLimiter(acquire_failure=failure)
        try:
            await api.pets.get_pet(pet_id=pet, options=options.RequestOptions(limiter=limiter))
        except BaseException as error:
            lines.append(f"  native async acquire interruption={type(error).__name__}")
        lines.append(f"    {limiter.usage.report}")
        limiter = _AsyncSemaphoreLimiter()
        exchange.respond(abroken)
        async with api.pets.with_streaming_response.get_pet(
            pet_id=pet, options=options.RequestOptions(limiter=limiter)
        ) as stream:
            await arecord(lines, "async limited stream read failure", stream.read)
        lines.append(f"  async failed stream released {limiter.usage.report}")
    await http.aclose()


async def _async_hooks(package: ModuleType, lines: list[str]) -> None:
    options, _, types = _modules(package)
    exchange = Exchange(lines)
    http = exchange.async_client()
    pet = argument(package, "getPet", "path", "petId", 3)
    async with package.AsyncClient(http_client=http) as api:
        for event, sent in _HOOK_FAILURES:
            limiter = _AsyncSemaphoreLimiter()
            events = _Events(limiter.usage, event)
            if sent:
                exchange.respond(json_response(200, _PET))
            configured = options.RequestOptions(limiter=limiter, hooks=(_AsyncEvents(events),))
            lines.append(
                f"  async hook {event}: {await aoutcome(lambda: api.pets.get_pet(pet_id=pet, options=configured))}"
            )
            lines.append(f"    {limiter.usage.report} events={' '.join(events.names)} ends={events.ends}")
        for event in ("attempt_end", "call_end"):
            limiter = _AsyncSemaphoreLimiter()
            events = _Events(limiter.usage, event)
            exchange.respond(json_response(200, _PET))
            configured = options.RequestOptions(limiter=limiter, hooks=(_AsyncEvents(events),))
            manager = api.pets.with_streaming_response.get_pet(pet_id=pet, options=configured)
            lines.append(f"  async stream handoff hook {event}: {await aoutcome(manager.__aenter__)}")
            lines.append(f"    {limiter.usage.report} events={' '.join(events.names)}")
        limiter = _AsyncSemaphoreLimiter(release_failure=RuntimeError("release failed"))
        events = _Events(limiter.usage, "limiter_acquired")
        configured = options.RequestOptions(limiter=limiter, hooks=(_AsyncEvents(events),))
        lines.append(
            f"  async hook and release failure: {await aoutcome(lambda: api.pets.get_pet(pet_id=pet, options=configured))}"
        )
        lines.append(f"    {limiter.usage.report}")
    await http.aclose()


async def _async_modes(package: ModuleType, lines: list[str]) -> None:
    options, _, types = _modules(package)
    exchange = Exchange(lines)
    http = exchange.async_client()
    pet = argument(package, "getPet", "path", "petId", 3)
    opposite = _SemaphoreLimiter()

    async def configured_client() -> object:
        async with package.AsyncClient(http_client=http, options=options.ClientOptions(limiter=opposite)) as api:
            return await api.pets.get_pet(pet_id=pet)

    await arecord(lines, "sync limiter in async client", configured_client)
    async with package.AsyncClient(http_client=http) as api:
        await arecord(
            lines,
            "sync limiter in async view",
            lambda: api.with_options(options.RequestOptions(limiter=opposite)).pets.get_pet(pet_id=pet),
        )
        await arecord(
            lines,
            "sync limiter in async request",
            lambda: api.pets.get_pet(pet_id=pet, options=options.RequestOptions(limiter=opposite)),
        )
    lines.append(f"  opposite async mode {opposite.usage.report} queued={len(exchange.responders)}")
    await http.aclose()


async def _async_admission(package: ModuleType, lines: list[str]) -> None:
    options, bodies, _ = _modules(package)
    exchange = Exchange(lines)
    exchange.respond(raw_response(200, _PNG, "image/png"))
    http = exchange.async_client()
    async with package.AsyncClient(http_client=http) as api:
        for label, refused in (("async zero timeout", {"total_timeout": 0}), ("async closed client admission", {})):
            limiter = _AsyncSemaphoreLimiter()
            payload = _Payload(limiter.usage)
            events = _Events(limiter.usage)
            request = options.RequestOptions(limiter=limiter, hooks=(_AsyncEvents(events),), **refused)
            if not refused:
                await api.aclose()
            await arecord(
                lines,
                label,
                lambda: api.pets.photos.upload(pet_id=_photo(package), body=payload, options=request),
            )
            lines.append(
                f"    {limiter.usage.report} payload={payload.opened} "
                f"events={' '.join(events.names)} ends={events.ends} queued={len(exchange.responders)}"
            )
    await http.aclose()
