"""Account generated calls for the token acquisitions of the SDK's OAuth providers: budgets, counters, and hooks."""

from __future__ import annotations

import asyncio
import importlib
import json
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING, Any, Final

from tests.data.python.client_oauth import (
    LIMIT,
    Adapter,
    AsyncAdapter,
    AsyncResponse,
    Response,
    credential_context,
    failure_line,
    started,
    watched,
)
from tests.data.python.client_runtime import Exchange, raw_response, run

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator
    from types import ModuleType

_TOKEN: Final = "https://auth.example.com/token"
_INVALID: Final = {"WWW-Authenticate": 'Bearer error="invalid_token"'}


def _issued(value: str) -> bytes:
    return json.dumps({"access_token": value, "token_type": "Bearer", "expires_in": 3600}).encode()


def _ok() -> Callable[[Any], Any]:
    return raw_response(200, b"ok", "application/octet-stream")


def _rejected() -> Callable[[Any], Any]:
    return raw_response(401, b"rejected", "application/octet-stream", **_INVALID)


def _counters(source: Any) -> str:
    return (
        f"exchanges={source.auth_exchange_count} sends={source.network_send_count}"
        f" budgets={source.auth_exchange_budget_used}/{source.network_send_budget_used}"
        f" refreshes={len(source.auth_refresh_ids)} pending={source.auth_refresh_pending}"
    )


def _failed(error: BaseException) -> str:
    stop = getattr(error, "retry_stop_reason", None)
    return f"{failure_line(error)}{'' if stop is None else f' stop={stop}'} {_counters(error)}"


def _called(call: Callable[[], Any]) -> str:
    try:
        result = call()
    except Exception as error:  # noqa: BLE001
        return _failed(error)
    return f"{result.info.status_code} {_counters(result.info)}"


async def _acalled(call: Callable[[], Any]) -> str:
    try:
        result = await call()
    except Exception as error:  # noqa: BLE001
        return _failed(error)
    return f"{result.info.status_code} {_counters(result.info)}"


def _token(call: Callable[[], Any]) -> str:
    try:
        return call().token.value
    except Exception as error:  # noqa: BLE001
        return failure_line(error)


class _Events:
    """A hook keeping a call's event names and the send limit it starts with, running an action on some events."""

    def __init__(self, **actions: Callable[[Any], object]) -> None:
        self.names: list[str] = []
        self.limits: list[object] = []
        self.actions = actions

    def on_event(self, event: Any) -> None:
        self.names.append(event.name)
        if event.name == "call_start":
            self.limits.append(event.options["max_network_sends"])
        if (action := self.actions.get(event.name)) is not None:
            action(event)


class _AsyncEvents(_Events):
    async def on_event(self, event: Any) -> None:  # ty: ignore[invalid-method-override]
        super().on_event(event)


class _Wrapper:
    """A custom provider delegating to a provider of the SDK, which leaves the call without its accounting."""

    def __init__(self, provider: Any) -> None:
        self.provider = provider

    def get(self, context: Any) -> Any:
        return self.provider.get(context)

    def invalidate(self, version: Any) -> None:
        self.provider.invalidate(version)

    def refresh(self, context: Any) -> Any:
        return self.provider.refresh(context)


def _failing(event: Any) -> None:
    msg = f"hook failed on {event.name}"
    raise RuntimeError(msg)


class _GatedSecret:
    """A client secret provider whose lookup waits until released."""

    def __init__(self, material: object) -> None:
        self.material = material
        self.entered = threading.Event()
        self.release = threading.Event()

    def get(self, context: object) -> object:
        del context
        self.entered.set()
        self.release.wait(LIMIT)
        return self.material


def _cancelling(signal: threading.Event, token: Any) -> threading.Thread:
    """Cancel the token once the signal says the acquisition reached the point the scenario needs."""
    thread = threading.Thread(target=lambda: signal.wait(LIMIT) and token.cancel())
    thread.start()
    return thread


class _Harness:
    """A generated package, its modules, one exchange for resources, and injected token transports."""

    def __init__(self, package: ModuleType, lines: list[str]) -> None:
        self.package = package
        self.auth, self.options, self.transports, self.responses = (
            importlib.import_module(f"{package.__name__}.{name}")
            for name in ("auth", "options", "transports", "responses")
        )
        self.lines = lines
        self.exchange = Exchange(lines)

    def provider(
        self, *tokens: str, gate: threading.Event | None = None, secret: object = None, **limits: Any
    ) -> tuple[Any, Adapter]:
        adapter = Adapter(
            self.transports, *(Response(self.responses, 200, _issued(token)) for token in tokens), gate=gate
        )
        return self.auth.ClientCredentialsProvider(
            _TOKEN,
            client_id="c",
            client_secret=secret or self.auth.StaticCredentialProvider(self.auth.ApiKeyCredential("s")),
            options=self.auth.OAuthProviderOptions(**limits),
            token_transport=adapter,
        ), adapter

    def async_provider(self, *tokens: str, hold: asyncio.Event | None = None) -> tuple[Any, AsyncAdapter]:
        adapter = AsyncAdapter(
            self.transports, *(AsyncResponse(self.responses, 200, _issued(token)) for token in tokens), hold=hold
        )
        return self.auth.AsyncClientCredentialsProvider(
            _TOKEN,
            client_id="c",
            client_secret=self.auth.AsyncStaticCredentialProvider(self.auth.ApiKeyCredential("s")),
            token_transport=adapter,
        ), adapter

    def settings(self, credentials: Any, hooks: tuple[object, ...], exchanges: int, **options: Any) -> Any:
        auth = self.auth.AuthConfig({"bearer": credentials}, max_token_exchanges=exchanges)
        return self.options.ClientOptions(
            auth=auth, hooks=hooks, retry=self.options.RetryOptions(initial_delay=0), **options
        )

    @contextmanager
    def client(
        self, credentials: Any, *hooks: object, exchanges: int = 2, **options: Any
    ) -> Iterator[Any]:
        with (
            self.exchange.client() as native,
            self.package.Client(
                http_client=native, options=self.settings(credentials, hooks, exchanges, **options)
            ) as api,
        ):
            yield api.auth.with_response

    @asynccontextmanager
    async def async_client(
        self, credentials: Any, *hooks: object, exchanges: int = 2, **options: Any
    ) -> AsyncIterator[Any]:
        async with (
            self.exchange.async_client() as native,
            self.package.AsyncClient(
                http_client=native, options=self.settings(credentials, hooks, exchanges, **options)
            ) as api,
        ):
            yield api.auth.with_response


def _settled(provider: Any, refresh_id: str) -> None:
    deadline = time.monotonic() + LIMIT
    while (info := provider.refresh_snapshot(refresh_id)) is not None and info.state == "PENDING":
        if time.monotonic() >= deadline:
            return
        time.sleep(0.01)


def _acquisitions(harness: _Harness, lines: list[str]) -> None:
    """Charge a call's first acquisition to it, serve later calls from the cache, and report the send limits."""
    provider, _ = harness.provider("access-1")
    events = _Events()
    harness.exchange.respond(_ok(), _ok())
    with harness.client(provider, events) as calls:
        lines.append(f"  first call = {_called(calls.bearer)}")
        lines.append(f"    events = {events.names}")
        lines.append(f"  cached token = {_called(calls.bearer)}")
    static = harness.auth.StaticTokenProvider(harness.auth.AccessToken("t"))
    for label, credentials, settings in (
        ("static provider", static, {}),
        ("provider of the SDK", provider, {}),
        ("wrapper of a provider of the SDK", _Wrapper(provider), {}),
        ("explicit send limit", provider, {"max_network_sends": 4}),
    ):
        events = _Events()
        harness.exchange.respond(_ok())
        with harness.client(credentials, events, **settings) as calls:
            outcome = _called(calls.bearer)
        lines.append(f"  {label} send limit = {events.limits} {outcome}")
    provider.close()
    for label, exchanges, settings in (
        ("no token exchange left", 0, {}),
        ("no network send left", 2, {"max_network_sends": 0}),
        ("no room for the request the token serves", 2, {"max_network_sends": 1}),
    ):
        refused, adapter = harness.provider()
        with refused, harness.client(refused, exchanges=exchanges, **settings) as calls:
            lines.append(f"  {label} = {_called(calls.bearer)} token sends={adapter.sends}")


def _joiners(harness: _Harness, lines: list[str]) -> None:
    """Let a call without exchange slots join an admitted acquisition, reporting its wait to its hooks."""
    gate = threading.Event()
    provider, adapter = harness.provider("access-1", gate=gate)
    starter = started(lambda: provider.get(credential_context(harness.auth)), adapter.entered, _token)
    events = _Events(auth_wait=lambda _event: gate.set())
    harness.exchange.respond(_ok())
    with provider, harness.client(provider, events, exchanges=0) as calls:
        lines.append(f"  joiner without exchange slots = {_called(calls.bearer)}")
        starter.join(LIMIT)
    lines.append(f"    starter = {starter.line} events = {events.names}")
    gate = threading.Event()
    provider, adapter = harness.provider("access-1", gate=gate)
    starter = started(lambda: provider.get(credential_context(harness.auth)), adapter.entered, _token)
    with provider, harness.client(provider, _Events(auth_wait=_failing)) as calls:
        lines.append(f"  failing wait hook = {_called(calls.bearer)}")
        gate.set()
        starter.join(LIMIT)
    lines.append(f"    starter = {starter.line}")


def _queued(harness: _Harness, lines: list[str]) -> None:
    """Refuse a call without budget room from a queued acquisition, and charge the admission to its oldest waiter."""
    gate = threading.Event()
    provider, adapter = harness.provider(
        "late", "access-2", gate=gate, refresh_timeout=0.5, max_pending_refreshes=2
    )
    stuck = started(lambda: provider.get(credential_context(harness.auth)), adapter.entered, _token)
    stuck.join(LIMIT)
    lines.append(f"  acquisition outliving its session = {stuck.line}")
    creator_token = watched(harness.options)
    creator = started(
        lambda: provider.get(
            credential_context(
                harness.auth, deadline=harness.options.Deadline.after(0.5), cancel_token=creator_token
            )
        ),
        creator_token.checked,
        _token,
    )
    harness.exchange.respond(_ok(), _ok())
    with harness.client(provider) as calls:
        waiters = []
        for _ in range(2):
            waiting = threading.Event()
            hooks = (_Events(auth_wait=lambda _event, waiting=waiting: waiting.set()),)
            options = harness.options.RequestOptions(hooks=hooks)
            waiters.append(started(lambda options=options: calls.bearer(options=options), waiting, _called))
        refused = harness.options.RequestOptions(
            auth=harness.auth.AuthConfig({"bearer": provider}, max_token_exchanges=0)
        )
        lines.append(f"  joining a queued acquisition without exchange slots = {_called(lambda: calls.bearer(options=refused))}")
        creator.join(LIMIT)
        lines.append(f"    standalone creator out of time = {creator.line}")
        gate.set()
        for waiter in waiters:
            waiter.join(LIMIT)
        lines.append(f"    oldest waiter pays the admission = {waiters[0].line}")
        lines.append(f"    later waiter = {waiters[1].line}")
    provider.close()


def _departures(harness: _Harness, lines: list[str]) -> None:
    """Keep a charged acquisition running after its call left, without reporting it to that call's hooks.

    The call counts the token request only when the acquisition sent it before the call left.
    """
    gate = threading.Event()
    provider, adapter = harness.provider("access-1", gate=gate)
    secret = _GatedSecret(harness.auth.ApiKeyCredential("s"))
    unsent, _ = harness.provider("access-1", secret=secret)
    for label, shared, signal, release in (
        ("call leaving its acquisition", provider, adapter.entered, gate),
        ("call leaving before its acquisition sent", unsent, secret.entered, secret.release),
    ):
        events = _Events()
        token = harness.options.CancelToken()
        canceller = _cancelling(signal, token)
        with harness.client(shared, events, cancel_token=token) as calls:
            try:
                calls.bearer()
            except Exception as error:  # noqa: BLE001
                lines.append(f"  {label} = {_failed(error)}")
                release.set()
                _settled(shared, error.auth_refresh_ids[0])
        canceller.join(LIMIT)
        lines.append(f"    events once the acquisition ended = {events.names}")
        harness.exchange.respond(_ok())
        with shared, harness.client(shared) as calls:
            lines.append(f"    next call = {_called(calls.bearer)}")


def _recoveries(harness: _Harness, lines: list[str]) -> None:
    """Recover from a rejected token within the exchange and network budgets, or stop for the budget that ran out."""
    for label, replies, exchanges, settings, operation in (
        ("recovery", (_rejected(), _ok()), 2, {}, "bearer"),
        ("recovery without an exchange slot", (_rejected(),), 1, {}, "bearer"),
        ("second rejection", (_rejected(), _rejected()), 2, {}, "bearer"),
        ("recovery without a network send", (_rejected(),), 2, {"max_network_sends": 3}, "bearer"),
        ("unsafe operation", (_rejected(),), 2, {}, "unsafe_auth"),
    ):
        provider, adapter = harness.provider("access-1", "access-2", "access-3")
        harness.exchange.respond(*replies)
        with provider, harness.client(provider, exchanges=exchanges, **settings) as calls:
            lines.append(f"  {label} = {_called(getattr(calls, operation))} token sends={adapter.sends}")
            if label == "recovery without an exchange slot":
                harness.exchange.respond(_ok())
                lines.append(f"    next call = {_called(calls.bearer)} token sends={adapter.sends}")
    provider, adapter = harness.provider("access-1", "access-2")

    def renew(event: Any) -> None:
        if event.status == 401:
            provider.refresh(credential_context(harness.auth))

    harness.exchange.respond(_rejected(), _ok())
    with provider, harness.client(provider, _Events(response_headers=renew)) as calls:
        lines.append(f"  newer token published meanwhile = {_called(calls.bearer)} token sends={adapter.sends}")
    gate = threading.Event()
    gate.set()
    provider, adapter = harness.provider("access-1", "access-2", gate=gate)
    running: list[threading.Thread] = []

    def refreshing(event: Any) -> None:
        if event.status == 401:
            gate.clear()
            adapter.entered.clear()
            running.append(started(lambda: provider.refresh(credential_context(harness.auth)), adapter.entered, _token))

    events = _Events(response_headers=refreshing, auth_wait=lambda _event: gate.set())
    harness.exchange.respond(_rejected(), _ok())
    with provider, harness.client(provider, events) as calls:
        lines.append(f"  acquisition running meanwhile = {_called(calls.bearer)} token sends={adapter.sends}")
        for thread in running:
            thread.join(LIMIT)
    lines.append(f"    events = {events.names}")
    gate = threading.Event()
    gate.set()
    provider, adapter = harness.provider("access-1", "access-2", gate=gate)
    token = harness.options.CancelToken()
    cancellers: list[threading.Thread] = []

    def blocking(event: Any) -> None:
        if event.status == 401:
            gate.clear()
            adapter.entered.clear()
            cancellers.append(_cancelling(adapter.entered, token))

    harness.exchange.respond(_rejected())
    with provider, harness.client(provider, _Events(response_headers=blocking), cancel_token=token) as calls:
        lines.append(f"  recovery cancelled while acquiring = {_called(calls.bearer)}")
        gate.set()
        for thread in cancellers:
            thread.join(LIMIT)
    gate = threading.Event()
    provider, adapter = harness.provider("access-1", gate=gate, refresh_timeout=0.05)
    with provider:
        deadline = harness.options.Deadline.after(0.05)
        left = _token(lambda: provider.get(credential_context(harness.auth, deadline=deadline)))
        time.sleep(1.2)
        lines.append(
            f"  exchange needed once a stuck acquisition outlived its session ="
            f" {provider.exchange_needed(harness.auth.TokenVersion())} after {left}"
        )
        gate.set()


async def _async_calls(harness: _Harness, lines: list[str]) -> None:
    """Account asyncio calls the same way: first acquisition, cache, joining, refusal, and recovery."""
    provider, _ = harness.async_provider("access-1", "access-2")
    harness.exchange.respond(_ok(), _ok(), _rejected(), _ok())
    async with harness.async_client(provider) as calls:
        lines.append(f"  async first call = {await _acalled(calls.bearer)}")
        lines.append(f"  async cached token = {await _acalled(calls.bearer)}")
        lines.append(f"  async recovery = {await _acalled(calls.bearer)}")
    await provider.aclose()
    refused, adapter = harness.async_provider()
    async with harness.async_client(refused, exchanges=0) as calls:
        lines.append(f"  async no token exchange left = {await _acalled(calls.bearer)} token sends={adapter.sends}")
    await refused.aclose()
    hold = asyncio.Event()
    provider, adapter = harness.async_provider("access-1", hold=hold)
    starter = asyncio.create_task(provider.get(credential_context(harness.auth)))
    deadline = time.monotonic() + LIMIT
    while not adapter.entered.is_set() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    events = _AsyncEvents(auth_wait=lambda _event: hold.set())
    harness.exchange.respond(_ok())
    async with harness.async_client(provider, events, exchanges=0) as calls:
        lines.append(f"  async joiner without exchange slots = {await _acalled(calls.bearer)}")
    lines.append(f"    starter = {(await starter).token.value} events = {events.names}")
    await provider.aclose()


def oauth_accounting(package: ModuleType, lines: list[str]) -> None:
    """Exercise budgets, counters, send limits, hooks, and recovery gates of calls using the SDK's OAuth providers."""
    harness = _Harness(package, lines)
    _acquisitions(harness, lines)
    _joiners(harness, lines)
    _queued(harness, lines)
    _departures(harness, lines)
    _recoveries(harness, lines)

    async def flows() -> None:
        await _async_calls(harness, lines)

    run(flows)
