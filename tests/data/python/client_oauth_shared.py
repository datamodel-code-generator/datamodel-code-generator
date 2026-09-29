"""Share one client credentials acquisition between concurrent callers, within its limits, until the provider closes."""

from __future__ import annotations

import asyncio
import importlib
import json
import threading
import time
from typing import TYPE_CHECKING, Any, Final

from tests.data.python.client_oauth import (
    LIMIT,
    Adapter,
    AsyncAdapter,
    AsyncResponse,
    AsyncSecret,
    Response,
    credential_context,
    failure_line,
    started,
    watched,
)
from tests.data.python.client_runtime import run

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_TOKEN: Final = "https://auth.example.com/token"
_ISSUED: Final = {"access_token": "access-1", "token_type": "Bearer", "expires_in": 3600}
_LATE: Final = {**_ISSUED, "access_token": "late"}


def _outcome(call: Callable[[], object]) -> str:
    try:
        result = call()
    except Exception as error:  # noqa: BLE001
        return failure_line(error)
    return f"{result.token.value}"


async def _aoutcome(call: Callable[[], Any]) -> str:
    try:
        result = await call()
    except Exception as error:  # noqa: BLE001
        return failure_line(error)
    return f"{result.token.value}"


def _failure(call: Callable[[], object]) -> Any:
    try:
        call()
    except Exception as error:  # noqa: BLE001
        return error
    return None


async def _afailure(call: Callable[[], Any]) -> Any:
    try:
        await call()
    except Exception as error:  # noqa: BLE001
        return error
    return None


def _workers() -> int:
    return sum(thread.name.startswith("oauth-refresh") for thread in threading.enumerate())


def _reply(responses: ModuleType, payload: object) -> Response:
    return Response(responses, 200, json.dumps(payload).encode())


def _areply(responses: ModuleType, payload: object) -> AsyncResponse:
    return AsyncResponse(responses, 200, json.dumps(payload).encode())


class _Provider:
    """A client credentials provider over a gated transport, with the limits of one scenario."""

    def __init__(self, auth: ModuleType, transports: ModuleType, *replies: object, **limits: Any) -> None:
        self.gate = threading.Event()
        self.adapter = Adapter(transports, *replies, gate=self.gate)
        self.provider = auth.ClientCredentialsProvider(
            _TOKEN,
            client_id="c",
            client_secret=auth.StaticCredentialProvider(auth.ApiKeyCredential("s")),
            options=auth.OAuthProviderOptions(**limits),
            token_transport=self.adapter,
        )


def _single_flight(auth: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    """Let every caller join the running acquisition, or leave it, while it runs on the provider's worker."""
    shared = _Provider(auth, transports, _reply(responses, _ISSUED), max_waiters=3)
    provider, adapter = shared.provider, shared.adapter
    before = _workers()
    first = started(lambda: provider.get(credential_context(auth)), adapter.entered, _outcome)
    lines.append(f"  workers once a job runs = {_workers() - before}")
    lines.append(
        f"  joiner out of time = {_outcome(lambda: provider.get(credential_context(auth, deadline=options.Deadline.after(0.1))))}"
    )
    token = options.CancelToken()
    token.cancel()
    lines.append(f"  joiner already cancelled = {_outcome(lambda: provider.get(credential_context(auth, cancel_token=token)))}")
    later = options.CancelToken()
    timer = threading.Timer(0.1, later.cancel)
    timer.start()
    lines.append(f"  joiner cancelled while waiting = {_outcome(lambda: provider.get(credential_context(auth, cancel_token=later)))}")
    timer.join()
    bounded = credential_context(auth, deadline=options.Deadline.after(0.1), cancel_token=options.CancelToken())
    lines.append(f"  joiner with a cancel token out of time = {_outcome(lambda: provider.get(bounded))}")
    tokens = [watched(options) for _ in range(2)]
    joiners = [
        started(lambda: provider.get(credential_context(auth, cancel_token=tokens[0])), tokens[0].checked, _outcome),
        started(lambda: provider.refresh(credential_context(auth, cancel_token=tokens[1])), tokens[1].checked, _outcome),
    ]
    limited = _failure(lambda: provider.get(credential_context(auth)))
    lines.append(f"  waiter beyond the limit = {failure_line(limited)}")
    lines.append(f"    snapshot while sending = {provider.refresh_snapshot(limited.refresh_id)}")
    shared.gate.set()
    for caller in (first, *joiners):
        caller.join(LIMIT)
    lines.append(f"  first caller and joiners = {[caller.line for caller in (first, *joiners)]}")
    lines.append(f"    snapshot = {provider.refresh_snapshot(limited.refresh_id)}")
    lines.append(f"    cached = {_outcome(lambda: provider.get(credential_context(auth)))} sends={adapter.sends}")
    provider.close()
    left = _Provider(auth, transports, _reply(responses, _ISSUED))
    lines.append(
        f"  only caller out of time = {_outcome(lambda: left.provider.get(credential_context(auth, deadline=options.Deadline.after(0.1))))}"
    )
    left.gate.set()
    lines.append(f"    job it left published = {_outcome(lambda: left.provider.get(credential_context(auth)))} sends={left.adapter.sends}")
    left.provider.close()


def _released(provider: Any, auth: ModuleType) -> str:
    """Return the first acquisition not refused while a released job still holds its pending slot."""
    deadline = time.monotonic() + LIMIT
    while (line := _outcome(lambda: provider.get(credential_context(auth)))).startswith(
        "AuthConcurrencyLimitError"
    ) and time.monotonic() < deadline:
        time.sleep(0.01)
    return line


def _stuck(auth: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    """Expire jobs outliving their sessions, queue jobs behind them within the limits, and close around them."""
    limits = {"refresh_timeout": 0.5, "max_pending_refreshes": 2}
    pending = _Provider(
        auth, transports, _reply(responses, _LATE), _reply(responses, _ISSUED), refresh_timeout=0.5, max_pending_refreshes=1
    )
    queued = _Provider(auth, transports, _reply(responses, _LATE), _reply(responses, _ISSUED), max_waiters=2, **limits)
    closing = _Provider(auth, transports, _reply(responses, _LATE), **limits)
    draining = _Provider(auth, transports, _reply(responses, _LATE), **limits)
    stuck = [
        started(lambda shared=shared: shared.provider.get(credential_context(auth)), shared.adapter.entered, _outcome)
        for shared in (pending, queued, closing, draining)
    ]
    closer = threading.Thread(target=draining.provider.close)
    closer.start()
    for caller in (*stuck, closer):
        caller.join(LIMIT)
    lines.append(f"  job outliving its session = {stuck[0].line}")
    lines.append(
        f"  new job while the expired one holds the only pending slot = {_outcome(lambda: pending.provider.get(credential_context(auth)))}"
    )
    pending.gate.set()
    lines.append(f"    once it returns, its late token is discarded = {_released(pending.provider, auth)} sends={pending.adapter.sends}")
    pending.provider.close()
    lines.append(
        f"  queued caller out of time = {_outcome(lambda: queued.provider.get(credential_context(auth, deadline=options.Deadline.after(0.05))))}"
    )
    tokens = [watched(options) for _ in range(2)]
    waiting = [started(lambda token=token: queued.provider.get(credential_context(auth, cancel_token=token)), token.checked, _outcome) for token in tokens]
    limited = _failure(lambda: queued.provider.get(credential_context(auth)))
    lines.append(f"  waiter of a queued job beyond the limit = {failure_line(limited)}")
    lines.append(f"    snapshot of a queued job = {queued.provider.refresh_snapshot(limited.refresh_id)}")
    queued.gate.set()
    for caller in waiting:
        caller.join(LIMIT)
    lines.append(f"    queued job once the expired one returns = {[caller.line for caller in waiting]}")
    lines.append(f"    snapshot = {queued.provider.refresh_snapshot(limited.refresh_id)} sends={queued.adapter.sends}")
    queued.provider.close()
    token = watched(options)
    waiter = started(lambda: closing.provider.get(credential_context(auth, cancel_token=token)), token.checked, _outcome)
    closing.provider.close()
    waiter.join(LIMIT)
    lines.append(f"  queued job when the provider closes = {waiter.line}")
    closing.gate.set()
    lines.append(f"  close waits for a running job until its session ends = {stuck[3].line}")
    draining.gate.set()
    lines.append(f"    get after close = {_outcome(lambda: draining.provider.get(credential_context(auth)))}")


def _receipts(auth: ModuleType, transports: ModuleType, lines: list[str]) -> None:
    """Keep the latest 128 completed snapshots."""
    shared = _Provider(auth, transports, *(RuntimeError("adapter") for _ in range(129)))
    shared.gate.set()
    ids = [_failure(lambda: shared.provider.get(credential_context(auth))).refresh_id for _ in range(129)]
    kept = sum(shared.provider.refresh_snapshot(refresh_id) is not None for refresh_id in ids)
    lines.append(f"  snapshots kept = {kept}, oldest evicted = {shared.provider.refresh_snapshot(ids[0]) is None}")
    shared.provider.close()


class _AsyncClosingFailure(AsyncAdapter):
    async def aclose(self) -> None:
        await super().aclose()
        raise RuntimeError


class _Stubborn(AsyncSecret):
    """An asyncio client secret provider whose first lookup ignores its cancellation and ends only once released."""

    def __init__(self, material: object) -> None:
        super().__init__(material)
        self.release = asyncio.Event()
        self.lookups = 0

    async def get(self, context: object) -> object:
        self.lookups += 1
        if self.lookups == 1:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                await self.release.wait()
        return await super().get(context)


def _async_provider(auth: ModuleType, transports: ModuleType, *replies: object, secret: object = None, hold: asyncio.Event | None = None, **limits: Any) -> tuple[Any, AsyncAdapter]:
    adapter = AsyncAdapter(transports, *replies, hold=hold)
    provider = auth.AsyncClientCredentialsProvider(
        _TOKEN,
        client_id="c",
        client_secret=secret or auth.AsyncStaticCredentialProvider(auth.ApiKeyCredential("s")),
        options=auth.OAuthProviderOptions(**limits),
        token_transport=adapter,
    )
    return provider, adapter


async def _entered(adapter: Adapter) -> None:
    deadline = time.monotonic() + LIMIT
    while not adapter.entered.is_set() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)


async def _async_single_flight(auth: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    """Join one asyncio acquisition from every task, leave it by deadline, cancel token, or cancellation."""
    hold = asyncio.Event()
    provider, adapter = _async_provider(auth, transports, _areply(responses, _ISSUED), hold=hold, max_waiters=3)
    first = asyncio.create_task(_aoutcome(lambda: provider.get(credential_context(auth))))
    await _entered(adapter)
    token = options.CancelToken()
    asyncio.get_running_loop().call_later(0.1, token.cancel)
    lines.append(f"  async joiner out of time = {await _aoutcome(lambda: provider.get(credential_context(auth, deadline=options.Deadline.after(0.1))))}")
    lines.append(f"  async joiner cancelled by its token = {await _aoutcome(lambda: provider.get(credential_context(auth, cancel_token=token)))}")
    cancelled = asyncio.create_task(provider.get(credential_context(auth)))
    await asyncio.sleep(0)
    cancelled.cancel()
    try:
        await cancelled
    except asyncio.CancelledError:
        lines.append("  async joiner cancelled = CancelledError")
    joiners = [asyncio.create_task(_aoutcome(lambda: provider.get(credential_context(auth)))), asyncio.create_task(_aoutcome(lambda: provider.refresh(credential_context(auth))))]
    await asyncio.sleep(0)
    limited = await _afailure(lambda: provider.get(credential_context(auth)))
    lines.append(f"  async waiter beyond the limit = {failure_line(limited)}")
    lines.append(f"    snapshot while sending = {provider.refresh_snapshot(limited.refresh_id)}")
    hold.set()
    lines.append(f"  async first task and joiners = {[await task for task in (first, *joiners)]} sends={adapter.sends}")
    lines.append(f"    snapshot = {provider.refresh_snapshot(limited.refresh_id)}")
    await provider.aclose()


async def _async_stuck(auth: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    """Expire a job ignoring its cancellation by its own timer, then admit or end the job queued behind it."""
    limits = {"refresh_timeout": 0.5, "max_pending_refreshes": 2}
    admitted_secret, closed_secret = _Stubborn(auth.ApiKeyCredential("s")), _Stubborn(auth.ApiKeyCredential("s"))
    admitted, admitted_adapter = _async_provider(auth, transports, _areply(responses, _ISSUED), secret=admitted_secret, **limits)
    closed, _ = _async_provider(auth, transports, secret=closed_secret, **limits)
    expired = await asyncio.gather(*(_aoutcome(lambda provider=provider: provider.get(credential_context(auth))) for provider in (admitted, closed)))
    lines.append(f"  async job outliving its session = {expired}")
    behind = [asyncio.create_task(_aoutcome(lambda provider=provider: provider.get(credential_context(auth)))) for provider in (admitted, closed)]
    await asyncio.sleep(0)
    admitted_secret.release.set()
    lines.append(f"  async queued job once the expired one returns = {await behind[0]} sends={admitted_adapter.sends}")
    await admitted.aclose()
    await closed.aclose()
    lines.append(f"  async queued job when the provider closes = {await behind[1]}")
    closed_secret.release.set()


async def _async_close(auth: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    """Refuse new work while closing, let a running job finish, and share one finalizer between close calls."""
    hold = asyncio.Event()
    provider, adapter = _async_provider(auth, transports, _areply(responses, _ISSUED), hold=hold)
    running = asyncio.create_task(_aoutcome(lambda: provider.get(credential_context(auth))))
    await _entered(adapter)
    first = asyncio.create_task(provider.aclose())
    await asyncio.sleep(0)
    lines.append(f"  async get while closing = {await _aoutcome(lambda: provider.get(credential_context(auth)))}")
    first.cancel()
    try:
        await first
    except asyncio.CancelledError:
        lines.append("  async close cancelled = CancelledError")
    asyncio.get_running_loop().call_later(0.05, hold.set)
    await provider.aclose()
    lines.append(f"  async close waits for the running job = {await running}")
    failing = _AsyncClosingFailure(transports)
    provider = auth.AsyncClientCredentialsProvider(
        _TOKEN,
        client_id="c",
        client_secret=auth.AsyncStaticCredentialProvider(auth.ApiKeyCredential("s")),
        token_transport=transports.OwnedTransportAdapter(failing),
    )
    lines.append(f"  async owned transport close failure = {type(await _afailure(provider.aclose)).__name__}")
    lines.append(f"    close again = {type(await _afailure(provider.aclose)).__name__} closes={failing.closes}")


def oauth_shared(package: ModuleType, lines: list[str]) -> None:
    """Exercise shared acquisition: joining, leaving, limits, expiry, queueing, snapshots, and closing."""
    auth, options, transports, responses = (
        importlib.import_module(f"{package.__name__}.{name}") for name in ("auth", "options", "transports", "responses")
    )
    _single_flight(auth, options, transports, responses, lines)
    _stuck(auth, options, transports, responses, lines)
    _receipts(auth, transports, lines)

    async def flows() -> None:
        await _async_single_flight(auth, options, transports, responses, lines)
        await _async_stuck(auth, transports, responses, lines)
        await _async_close(auth, transports, responses, lines)

    run(flows)
    _loops(auth, transports, responses, lines)


def _cancel_others() -> None:
    current = asyncio.current_task()
    for task in asyncio.all_tasks():
        if task is not current:
            task.cancel()


def _loops(auth: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    """Fail jobs their event loop cancels, before or while they run, restart a cancelled close, and allow eager tasks."""
    abandoned: list[Any] = []

    async def abandon() -> None:
        provider, adapter = _async_provider(
            auth, transports, _areply(responses, _ISSUED), hold=asyncio.Event(), max_waiters=1
        )
        abandoned.append(asyncio.create_task(provider.get(credential_context(auth))))
        await _entered(adapter)
        abandoned.extend((provider, await _afailure(lambda: provider.get(credential_context(auth)))))

    asyncio.run(abandon())
    _, provider, limited = abandoned
    lines.append(f"  job cancelled with its event loop = {provider.refresh_snapshot(limited.refresh_id)}")

    async def unstarted() -> str:
        provider, adapter = _async_provider(auth, transports, _areply(responses, _ISSUED), max_waiters=1)
        waiting = asyncio.create_task(provider.get(credential_context(auth)))
        await asyncio.sleep(0)
        limited = await _afailure(lambda: provider.get(credential_context(auth)))
        _cancel_others()
        await asyncio.gather(waiting, return_exceptions=True)
        await asyncio.sleep(0)
        snapshot = provider.refresh_snapshot(limited.refresh_id)
        await provider.aclose()
        return f"{snapshot} sends={adapter.sends}"

    lines.append(f"  job cancelled before it started = {asyncio.run(unstarted())}")

    async def restarted() -> str:
        provider, adapter = _async_provider(auth, transports, _areply(responses, _ISSUED), hold=asyncio.Event())
        running = asyncio.create_task(provider.get(credential_context(auth)))
        await _entered(adapter)
        closing = asyncio.create_task(provider.aclose())
        await asyncio.sleep(0)
        _cancel_others()
        ended = await asyncio.gather(running, closing, return_exceptions=True)
        return f"{[type(result).__name__ for result in ended]}, close again = {await _afailure(provider.aclose)}"

    lines.append(f"  close whose finalizer was cancelled = {asyncio.run(restarted())}")

    async def eager() -> str:
        if (factory := getattr(asyncio, "eager_task_factory", None)) is not None:
            asyncio.get_running_loop().set_task_factory(factory)
        provider, _ = _async_provider(auth, transports, _areply(responses, _ISSUED))
        line = await _aoutcome(lambda: provider.get(credential_context(auth)))
        return f"{line}, close = {await _afailure(provider.aclose)}"

    lines.append(f"  eager tasks = {asyncio.run(eager())}")
