"""Report native redirects of generated public clients: statuses, methods, bodies, limits, retries, and HEAD."""

from __future__ import annotations

import asyncio
import importlib
import io
from functools import partial
from typing import TYPE_CHECKING, Any

import httpx2

from tests.data.python.client_retry_policy import _response
from tests.data.python.client_runtime import Exchange, arecord, argument, failing, raw_response, record, run
from tests.data.python.fixture_native import NativeFixture

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from types import ModuleType


class _Events:
    def __init__(self) -> None:
        self.values: list[tuple[object, ...]] = []

    def on_event(self, event: object) -> None:
        self.values.append(
            tuple(
                getattr(event, name)
                for name in ("name", "attempt_index", "status", "origin", "attempt_count", "outcome")
            )
        )


async def _chunks() -> AsyncIterator[bytes]:
    for chunk in (b"one", b"two"):
        await asyncio.sleep(0)
        yield chunk


def _outcome(call: Callable[[], object], *, error_type: type[Exception]) -> tuple[object, ...]:
    try:
        value = call()
    except error_type as error:
        info = getattr(error, "info", None)
        return (
            type(error).__name__,
            getattr(error, "reason", None),
            getattr(info, "status_code", None),
            getattr(error, "attempt_count", None),
            type(getattr(error, "cause", None)).__name__,
        )
    info = getattr(value, "info", None)
    return (
        getattr(value, "data", getattr(value, "body_bytes", None)),
        getattr(info, "status_code", None),
        getattr(info, "attempt_count", None),
    )


def _statuses(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Follow each redirect status as HTTPX2 does, across origins too, and stop at its limit."""
    exchange = Exchange(lines)
    outcome = partial(_outcome, error_type=importlib.import_module(f"{package.__name__}.errors").SDKError)
    events = _Events()
    with (
        exchange.client(max_redirects=2) as native,
        package.Client(
            http_client=native,
            options=options.ClientOptions(
                retry=options.RetryOptions(initial_delay=0), follow_redirects=True, hooks=(events,)
            ),
        ) as api,
    ):
        for label, status, fields, follow in (
            ("not followed by the call", 301, (("Location", "/done"),), False),
            ("301 GET", 301, (("Location", "/done"),), True),
            ("302 GET", 302, (("Location", "/done"),), True),
            ("303 GET", 303, (("Location", "/done"),), True),
            ("307 GET", 307, (("Location", "/done"),), True),
            ("308 GET", 308, (("Location", "/done"),), True),
            ("another origin", 302, (("Location", "https://other.example.com:9443/done"),), True),
            ("unsupported 305", 305, (("Location", "/done"),), True),
            ("missing Location", 302, (), True),
            ("relative encoded", 302, (("Location", "next%2Fpart?q=a%2Fb#omitted"),), True),
            ("invalid port", 302, (("Location", "https://api.example.com:port/done"),), True),
        ):
            exchange.responders.clear()
            events.values.clear()
            exchange.respond(_response(status, fields), _response(200))
            request = options.RequestOptions(follow_redirects=follow)
            record(
                lines,
                label,
                lambda request=request: outcome(lambda: api.retry.with_response.get_safe(options=request)),
            )
            lines.append(f"    unused={len(exchange.responders)} events={events.values!r}")
        exchange.responders.clear()
        exchange.respond(*(_response(302, (("Location", f"/hop{index}"),)) for index in range(3)), _response(200))
        record(lines, "past the native limit", lambda: outcome(api.retry.with_response.get_safe))
        lines.append(f"    unused={len(exchange.responders)}")


def _methods(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Change POST to GET for 301, 302, and 303, and send the body again for 307 and 308 as HTTPX2 does."""
    exchange = Exchange(lines)
    outcome = partial(_outcome, error_type=importlib.import_module(f"{package.__name__}.errors").SDKError)
    with (
        exchange.client() as native,
        package.Client(
            http_client=native,
            options=options.ClientOptions(retry=options.RetryOptions(initial_delay=0), follow_redirects=True),
        ) as api,
    ):
        for label, method, status, request in (
            ("301 POST becomes GET", "post_idempotent", 301, options.RequestOptions()),
            ("302 POST becomes GET", "post_idempotent", 302, options.RequestOptions()),
            ("303 POST becomes GET", "post_unsafe", 303, options.RequestOptions()),
            ("307 POST sends its bytes again", "post_unsafe", 307, options.RequestOptions()),
            ("308 POST sends its bytes again", "post_idempotent", 308, options.RequestOptions()),
            (
                "308 caller key sent again",
                "post_keyed",
                308,
                options.RequestOptions(idempotency_key=options.IdempotencyKey("same-key")),
            ),
        ):
            exchange.responders.clear()
            exchange.respond(_response(status, (("Location", "/done"),)), _response(200))
            record(
                lines,
                label,
                lambda method=method, request=request: outcome(
                    lambda: getattr(api.retry.with_response, method)(body=b"payload", options=request)
                ),
            )
            lines.append(f"    unused={len(exchange.responders)}")
        exchange.responders.clear()
        exchange.respond(_response(303, (("Location", "/done"),)), _response(200))
        record(
            lines,
            "HEAD303 becomes GET",
            lambda: outcome(lambda: api.request_raw("HEAD", "https://api.example.com/start")),
        )
        for status in (303, 307):
            exchange.responders.clear()
            exchange.respond(_response(status, (("Location", "/done"),)), _response(200))
            body = iter((b"one", b"two"))
            record(
                lines,
                f"{status} one-shot body",
                lambda body=body: outcome(lambda: api.retry.with_response.post_idempotent(body=body)),
            )
            lines.append(f"    unused={len(exchange.responders)}")


def _restored(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Retry from the original request after a redirect, and never resend a call whose redirect was answered."""
    exchange = Exchange(lines)
    outcome = partial(_outcome, error_type=importlib.import_module(f"{package.__name__}.errors").SDKError)
    with (
        exchange.client() as native,
        package.Client(
            http_client=native,
            options=options.ClientOptions(retry=options.RetryOptions(initial_delay=0), follow_redirects=True),
        ) as api,
    ):
        exchange.respond(
            _response(303, (("Location", "/done"),)),
            _response(503),
            _response(303, (("Location", "/done"),)),
            _response(200),
        )
        record(
            lines,
            "303 retry restores original POST",
            lambda: outcome(lambda: api.retry.with_response.post_idempotent(body=b"original")),
        )
        lines.append(f"    unused={len(exchange.responders)}")
        exchange.responders.clear()
        for label, method in (("unsafe POST", "post_unsafe"), ("idempotent POST", "post_idempotent")):
            exchange.respond(_response(303, (("Location", "/done"),)), failing(httpx2.ConnectError), _response(200))
            record(
                lines,
                f"refused redirect after an answered {label}",
                lambda method=method: outcome(lambda: getattr(api.retry.with_response, method)(body=b"original")),
            )
            lines.append(f"    unused={len(exchange.responders)}")
            exchange.responders.clear()


def _refused_hook(request: httpx2.Request) -> None:
    del request
    msg = "refused by the hook"
    raise httpx2.ConnectError(msg)


def _failed_hook(response: httpx2.Response) -> None:
    msg = "failed in the response hook"
    raise (
        httpx2.ConnectError(msg, request=response.request) if response.status_code == 201 else httpx2.ConnectError(msg)
    )


class _AsyncFailedHook:
    async def __call__(self, response: httpx2.Response) -> None:
        _failed_hook(response)


def _hooked(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Classify a failure an injected client's own hook raises: before anything was sent, or after an answer."""
    exchange = Exchange(lines)
    outcome = partial(_outcome, error_type=importlib.import_module(f"{package.__name__}.errors").SDKError)
    with (
        exchange.client(event_hooks={"request": [_refused_hook]}) as native,
        package.Client(
            http_client=native, options=options.ClientOptions(retry=options.RetryOptions(max_retries=0))
        ) as api,
    ):
        record(lines, "native request hook failure", lambda: outcome(api.retry.with_response.get_safe))
    with (
        exchange.client(event_hooks={"response": [_failed_hook]}) as native,
        package.Client(
            http_client=native, options=options.ClientOptions(retry=options.RetryOptions(initial_delay=0))
        ) as api,
    ):
        for label, status in (("without a request", 200), ("of the answered request", 201)):
            exchange.respond(_response(status), _response(status))
            record(
                lines,
                f"native response hook failure {label}",
                lambda: outcome(lambda: api.retry.with_response.post_unsafe(body=b"once")),
            )
            lines.append(f"    unused={len(exchange.responders)}")
            exchange.responders.clear()


def _origins(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Drop Authorization and the Cookie header across origins, as HTTPX2 does, while other headers go on."""
    exchange = Exchange(lines)
    outcome = partial(_outcome, error_type=importlib.import_module(f"{package.__name__}.errors").SDKError)
    with (
        exchange.client(follow_redirects=True) as native,
        package.Client(
            http_client=native,
            options=options.ClientOptions(
                headers=(
                    ("Authorization", "original-secret"),
                    ("Proxy-Authorization", "proxy-secret"),
                    ("Cookie", "private=1"),
                    ("X-Client", "kept"),
                ),
                retry=options.RetryOptions(initial_delay=0),
            ),
        ) as api,
    ):
        exchange.respond(
            _response(302, (("Location", "https://other.example.com/next"),)),
            _response(302, (("Location", "https://api.example.com/back"),)),
            _response(200),
        )
        record(lines, "origin chain returns to initial", lambda: outcome(api.retry.with_response.get_safe))


def _downgrade(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Follow a redirect from TLS to plain HTTP as HTTPX2 does, unless the call keeps redirects."""
    outcome = partial(_outcome, error_type=importlib.import_module(f"{package.__name__}.errors").SDKError)
    secure, plain = NativeFixture(), NativeFixture()
    plain.tls = False
    destination = f"http://localhost:{plain.port}"
    secure.status = 302
    secure.location = f"{destination}/done".encode()
    try:
        with httpx2.Client(verify=secure.verify, trust_env=False, follow_redirects=True) as native:
            for label, follow in (("downgrade followed", True), ("downgrade kept by the call", False)):
                secure.requests.clear()
                plain.requests.clear()
                with package.Client(
                    http_client=native, options=options.ClientOptions(base_url=secure.url, follow_redirects=follow)
                ) as api:
                    record(lines, label, lambda api=api: outcome(api.retry.with_response.get_safe))
                lines.append(f"    secure={secure.requests!r} plain={plain.requests!r}")
    finally:
        secure.stop()
        plain.stop()


async def _async(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    async with (
        exchange.async_client() as native,
        package.AsyncClient(http_client=native, options=options.ClientOptions(follow_redirects=True)) as api,
    ):
        exchange.respond(_response(307, (("Location", "/done"),)), _response(200))
        response = await arecord(lines, "async307", api.retry.with_response.get_safe)
        info = getattr(response, "info", None)
        lines.append(f"    counts={tuple(getattr(info, name, None) for name in ('attempt_count', 'request_id'))!r}")
        exchange.respond(
            _response(303, (("Location", "/middle"),)),
            _response(302, (("Location", "/done"),)),
            _response(200),
        )
        body = _chunks()
        response = await arecord(
            lines, "async one-shot POST303 GET302 chain", lambda: api.retry.with_response.post_unsafe(body=body)
        )
        info = getattr(response, "info", None)
        counts = tuple(getattr(info, name, None) for name in ("attempt_count", "request_id"))
        lines.append(f"    counts={counts!r} unused={len(exchange.responders)}")
        exchange.responders.clear()
        exchange.respond(_response(307, (("Location", "/done"),)), _response(200))
        body = _chunks()
        await arecord(lines, "async one-shot POST307", lambda: api.retry.with_response.post_unsafe(body=body))
        lines.append(f"    unused={len(exchange.responders)}")
        exchange.responders.clear()
        exchange.respond(_response(307, (("Location", "/done"),)), _response(200))
        file = io.BytesIO(b"seekable body")
        await arecord(lines, "async seekable POST307", lambda: api.retry.with_response.post_unsafe(body=file))
        lines.append(f"    unused={len(exchange.responders)}")
        exchange.responders.clear()
    async with (
        exchange.async_client(event_hooks={"response": [_AsyncFailedHook()]}) as native,
        package.AsyncClient(
            http_client=native, options=options.ClientOptions(retry=options.RetryOptions(initial_delay=0))
        ) as api,
    ):
        for label, status in (("without a request", 200), ("of the answered request", 201)):
            exchange.respond(_response(status), _response(status))
            try:
                await api.retry.with_response.post_unsafe(body=b"once")
            except Exception as error:  # noqa: BLE001
                lines.append(f"  async native response hook failure {label} ! {type(error).__name__}")
            lines.append(f"    unused={len(exchange.responders)}")
            exchange.responders.clear()


def redirects(package: ModuleType, lines: list[str]) -> None:
    """Exercise native redirects through generated clients with wire-visible origin, method, and body changes."""
    options = importlib.import_module(f"{package.__name__}.options")
    _statuses(package, options, lines)
    _methods(package, options, lines)
    _restored(package, options, lines)
    _hooked(package, options, lines)
    _origins(package, options, lines)
    _downgrade(package, options, lines)
    run(lambda: _async(package, options, lines))


class _HeadEvents:
    def __init__(self) -> None:
        self.ends: list[tuple[object, ...]] = []

    def on_event(self, event: object) -> None:
        if getattr(event, "name", None) in {"call_end", "stream_end"}:
            self.ends.append(tuple(getattr(event, name) for name in ("name", "status", "outcome", "attempt_count")))


def _head_calls(api: Any, pet: object, *, asynchronous: bool) -> dict[str, Callable[[], object]]:
    """Return the typed, response, raw, streaming, and raw-request HEAD calls of a client."""
    pets = api.pets
    if asynchronous:

        async def buffered() -> bytes:
            return (await pets.with_raw_response.head_pet(pet_id=pet)).body_bytes

        async def streaming() -> bytes:
            async with pets.with_streaming_response.head_pet(pet_id=pet) as response:
                return await response.read()

        async def escape() -> bytes:
            response = await api.request_raw("HEAD", "https://api.example.com/v1/pets/3")
            return response.body_bytes

        return {
            "typed": lambda: pets.head_pet(pet_id=pet),
            "response": lambda: pets.with_response.head_pet(pet_id=pet),
            "raw": buffered,
            "stream": streaming,
            "escape": escape,
        }

    def streamed() -> bytes:
        with pets.with_streaming_response.head_pet(pet_id=pet) as response:
            return response.read()

    return {
        "typed": lambda: pets.head_pet(pet_id=pet),
        "response": lambda: pets.with_response.head_pet(pet_id=pet),
        "raw": lambda: pets.with_raw_response.head_pet(pet_id=pet).body_bytes,
        "stream": streamed,
        "escape": lambda: api.request_raw("HEAD", "https://api.example.com/v1/pets/3").body_bytes,
    }


_HEAD_MODES = (
    ("typed", b"redirected representation", 1),
    ("response", b"redirected representation", 1),
    ("raw", b"redirected representation", 1),
    ("stream", b"redirected representation", 1),
    ("typed", b"", 1),
    ("escape", b"redirected representation", 1),
    ("raw", b"redirected representation", 2),
)


def _head_responses(exchange: Exchange, payload: bytes, hops: int) -> None:
    exchange.respond(raw_response(303, Location="/redirected"))
    if hops == 2:
        exchange.respond(raw_response(302, Location="/done"))
    exchange.respond(raw_response(200, payload, "text/plain", ETag='"redirected"'))


async def _async_head_redirects(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    pet = argument(package, "headPet", "path", "petId", 3)
    exchange, events = Exchange(lines), _HeadEvents()
    async with (
        exchange.async_client() as native,
        package.AsyncClient(
            http_client=native, options=options.ClientOptions(follow_redirects=True, hooks=(events,))
        ) as api,
    ):
        calls = _head_calls(api, pet, asynchronous=True)
        for mode, payload, hops in _HEAD_MODES:
            _head_responses(exchange, payload, hops)
            await arecord(lines, f"async HEAD303 {mode} body={bool(payload)} hops={hops}", calls[mode])
            lines.append(f"    ends={events.ends!r} unused={len(exchange.responders)}")
            events.ends.clear()


def head_redirects(package: ModuleType, lines: list[str]) -> None:
    """Keep HEAD's typed bodyless declaration after HTTPX2 changes a 303 to GET."""
    options = importlib.import_module(f"{package.__name__}.options")
    pet = argument(package, "headPet", "path", "petId", 3)
    exchange, events = Exchange(lines), _HeadEvents()
    with (
        exchange.client() as native,
        package.Client(
            http_client=native, options=options.ClientOptions(follow_redirects=True, hooks=(events,))
        ) as api,
    ):
        calls = _head_calls(api, pet, asynchronous=False)
        for mode, payload, hops in _HEAD_MODES:
            _head_responses(exchange, payload, hops)
            record(lines, f"HEAD303 {mode} body={bool(payload)} hops={hops}", calls[mode])
            lines.append(f"    ends={events.ends!r} unused={len(exchange.responders)}")
            events.ends.clear()
    run(lambda: _async_head_redirects(package, options, lines))
