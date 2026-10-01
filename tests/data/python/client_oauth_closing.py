"""Close clients owning the SDK's OAuth providers: release in the background, bounded by the cleanup time."""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, Final
from unittest.mock import patch

from tests.data.python.client_oauth import (
    LIMIT,
    Adapter,
    AsyncAdapter,
    AsyncResponse,
    Response,
    credential_context,
    failure_line,
    started,
)
from tests.data.python.client_runtime import Exchange, raw_response, run

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_TOKEN: Final = "https://auth.example.com/token"
_ISSUED: Final = json.dumps({"access_token": "access-1", "token_type": "Bearer", "expires_in": 3600}).encode()


def _ok() -> Callable[[Any], Any]:
    return raw_response(200, b"ok", "application/octet-stream")


def _outcome(call: Callable[[], Any]) -> str:
    try:
        result = call()
    except Exception as error:  # noqa: BLE001
        return failure_line(error)
    return repr(result) if result is None else getattr(getattr(result, "token", None), "value", "ok")


async def _aoutcome(call: Callable[[], Any]) -> str:
    try:
        result = await call()
    except Exception as error:  # noqa: BLE001
        return failure_line(error)
    return repr(result) if result is None else getattr(getattr(result, "token", None), "value", "ok")


def _cleanup(call: Callable[[], Any]) -> str:
    try:
        call()
    except Exception as error:  # noqa: BLE001
        return _described(error)
    return "closed"


async def _acleanup(call: Callable[[], Any]) -> str:
    try:
        await call()
    except Exception as error:  # noqa: BLE001
        return _described(error)
    return "closed"


def _described(error: BaseException) -> str:
    cause = getattr(error, "cause", None)
    secondary = [type(failure).__name__ for failure in getattr(error, "secondary_errors", ())]
    return (
        f"{type(error).__name__} pending_calls={getattr(error, 'pending_calls', None)}"
        f" pending_providers={getattr(error, 'pending_providers', None)}"
        f" cause={type(cause).__name__}"
    ) + (f" secondary={secondary}" if secondary else "")


class _Waits:
    """A hook signalling once its call waits for an acquisition another caller started."""

    def __init__(self) -> None:
        self.waiting = threading.Event()

    def on_event(self, event: Any) -> None:
        if event.name == "auth_wait":
            self.waiting.set()


class _FailingClose(Adapter):
    failure: type[Exception] = RuntimeError

    def close(self) -> None:
        super().close()
        raise self.failure


class _InvalidClose(_FailingClose):
    failure = ValueError


class _BlockingClose(Adapter):
    """An owned transport whose close waits until released."""

    def __init__(self, transports: ModuleType) -> None:
        super().__init__(transports)
        self.released = threading.Event()

    def close(self) -> None:
        self.released.wait(LIMIT)
        super().close()


class _InterruptedClose(Adapter):
    def close(self) -> None:
        super().close()
        raise KeyboardInterrupt


def _interrupted(call: Callable[[], object]) -> str:
    try:
        call()
    except KeyboardInterrupt:
        return "KeyboardInterrupt"
    return "closed"


class _AsyncFailingClose(AsyncAdapter):
    async def aclose(self) -> None:
        await super().aclose()
        raise RuntimeError


def _closing(package: ModuleType, lines: list[str]) -> None:
    """Release owned providers at once when idle, else once their running acquisition returns, within cleanup time."""
    auth, options, transports, responses = (
        importlib.import_module(f"{package.__name__}.{name}") for name in ("auth", "options", "transports", "responses")
    )
    exchange = Exchange(lines)
    secret = auth.StaticCredentialProvider(auth.ApiKeyCredential("s"))

    def provider(adapter: Adapter) -> Any:
        return auth.ClientCredentialsProvider(
            _TOKEN, client_id="c", client_secret=secret, token_transport=transports.OwnedTransportAdapter(adapter)
        )

    def client(native: Any, credentials: Any, alias: Any = None, **settings: Any) -> Any:
        schemes = {"bearer": credentials} if alias is None else {"bearer": credentials, "bearer_alias": alias}
        return package.Client(
            http_client=native, options=options.ClientOptions(auth=auth.AuthConfig(schemes), **settings)
        )

    idle_adapter = Adapter(transports)
    idle = provider(idle_adapter)
    with exchange.client() as native:
        api = client(native, auth.OwnedCredentialProvider(idle))
        lines.append(f"  idle owned provider = {_cleanup(api.close)} transport closes={idle_adapter.closes}")
    lines.append(f"    get afterwards = {_outcome(lambda: idle.get(credential_context(auth)))}")
    blocking_adapter = _BlockingClose(transports)
    with exchange.client() as native:
        api = client(native, auth.OwnedCredentialProvider(provider(blocking_adapter)), cleanup_timeout=0.2)
        lines.append(f"  idle owned provider whose transport close blocks = {_cleanup(api.close)}")
        blocking_adapter.released.set()
        lines.append(f"    close again = {_cleanup(api.close)} transport closes={blocking_adapter.closes}")
    unthreaded_adapter = Adapter(transports)
    unthreaded = provider(unthreaded_adapter)
    with patch.object(threading.Thread, "start", side_effect=RuntimeError("can't start new thread")):
        released = unthreaded.request_close()
    lines.append(
        f"  idle provider unable to start a thread = {_outcome(lambda: released.result(LIMIT))}"
        f" transport closes={unthreaded_adapter.closes}"
    )
    used_adapter = Adapter(transports, Response(responses, 200, _ISSUED))
    used = provider(used_adapter)
    exchange.respond(_ok())
    with exchange.client() as native:
        api = client(native, auth.OwnedCredentialProvider(used))
        lines.append(f"  call = {_outcome(api.auth.with_response.bearer)}")
        lines.append(f"  used owned provider = {_cleanup(api.close)} transport closes={used_adapter.closes}")
    refused_adapter = Adapter(transports, Response(responses, 200, _ISSUED))
    refused = provider(refused_adapter)
    exchange.respond(_ok())
    with exchange.client() as native:
        api = client(native, auth.OwnedCredentialProvider(refused))
        lines.append(f"  call = {_outcome(api.auth.with_response.bearer)}")
        with patch.object(ThreadPoolExecutor, "submit", side_effect=RuntimeError("cannot schedule new futures")):
            lines.append(f"  worker pool refusing the release = {_cleanup(api.close)} transport closes={refused_adapter.closes}")
    failing_adapter = _FailingClose(transports)
    failing = provider(failing_adapter)
    with exchange.client() as native:
        api = client(native, auth.OwnedCredentialProvider(failing))
        lines.append(f"  failing release = {_cleanup(api.close)}")
        lines.append(f"    close again = {_cleanup(api.close)} transport closes={failing_adapter.closes}")
    concurrent_adapter = _FailingClose(transports)
    concurrent = provider(concurrent_adapter)
    with exchange.client() as native:
        api = client(native, auth.OwnedCredentialProvider(concurrent))
        results: list[str] = []
        closers = [threading.Thread(target=lambda: results.append(_cleanup(api.close))) for _ in range(2)]
        for closer in closers:
            closer.start()
        for closer in closers:
            closer.join(LIMIT)
        lines.append(f"  concurrent closes = {sorted(results)} transport closes={concurrent_adapter.closes}")
    first_adapter, second_adapter = _FailingClose(transports), _InvalidClose(transports)
    with exchange.client() as native:
        api = client(
            native,
            auth.OwnedCredentialProvider(provider(first_adapter)),
            auth.OwnedCredentialProvider(provider(second_adapter)),
        )
        lines.append(f"  failing releases in adoption order = {_cleanup(api.close)}")
    interrupting_adapter, beside_adapter = _InterruptedClose(transports), Adapter(transports)
    with exchange.client() as native:
        api = client(
            native,
            auth.OwnedCredentialProvider(provider(interrupting_adapter)),
            auth.OwnedCredentialProvider(provider(beside_adapter)),
        )
        closes = (interrupting_adapter, beside_adapter)
        lines.append(
            f"  interrupted release beside another = {_interrupted(api.close)}"
            f" transport closes={[adapter.closes for adapter in closes]}"
        )
        lines.append(f"    close again = {_cleanup(api.close)}")
    interrupted_adapter = _InterruptedClose(transports)
    interrupted = provider(interrupted_adapter)
    lines.append(f"  interrupted release = {_interrupted(interrupted.close)}")
    lines.append(f"    close again = {_interrupted(interrupted.close)} transport closes={interrupted_adapter.closes}")
    lines.append(f"    get afterwards = {_outcome(lambda: interrupted.get(credential_context(auth)))}")
    gate = threading.Event()
    running_adapter = Adapter(transports, Response(responses, 200, _ISSUED), gate=gate)
    running = provider(running_adapter)
    starter = started(lambda: running.get(credential_context(auth)), running_adapter.entered, _outcome)
    waits = _Waits()
    with exchange.client() as native:
        api = client(native, auth.OwnedCredentialProvider(running), cleanup_timeout=0.2, hooks=(waits,))
        joiner = started(api.auth.with_response.bearer, waits.waiting, _outcome)
        lines.append(f"  provider with a running acquisition = {_cleanup(api.close)}")
        lines.append(f"    release cancelled = {running.request_close().cancel()}")
        joiner.join(LIMIT)
        lines.append(f"    waiting call = {joiner.line}")
        gate.set()
        starter.join(LIMIT)
        lines.append(f"    starter = {starter.line}")
        lines.append(f"    provider close = {_outcome(running.close)}")
        lines.append(f"    close again = {_cleanup(api.close)} transport closes={running_adapter.closes}")
    opening = threading.Event()
    unbounded_adapter = Adapter(transports, Response(responses, 200, _ISSUED), gate=opening)
    unbounded = provider(unbounded_adapter)
    starter = started(lambda: unbounded.get(credential_context(auth)), unbounded_adapter.entered, _outcome)
    with exchange.client() as native:
        api = client(native, auth.OwnedCredentialProvider(unbounded), cleanup_timeout=sys.float_info.max)
        opener = threading.Timer(0.2, opening.set)
        opener.start()
        lines.append(f"  unbounded cleanup time = {_cleanup(api.close)} transport closes={unbounded_adapter.closes}")
        opener.join(LIMIT)
        starter.join(LIMIT)
        lines.append(f"    starter = {starter.line}")
    unstuck = threading.Event()
    stuck_adapter = Adapter(transports, Response(responses, 200, _ISSUED), gate=unstuck)
    stuck = auth.ClientCredentialsProvider(
        _TOKEN,
        client_id="c",
        client_secret=secret,
        token_transport=transports.OwnedTransportAdapter(stuck_adapter),
        options=auth.OAuthProviderOptions(refresh_timeout=0.5),
    )
    starter = started(lambda: stuck.get(credential_context(auth)), stuck_adapter.entered, _outcome)
    with exchange.client() as native:
        api = client(native, auth.OwnedCredentialProvider(stuck), cleanup_timeout=0.05)
        lines.append(f"  provider with a stuck acquisition = {_cleanup(api.close)}")
        released = _outcome(lambda: stuck.request_close().result(LIMIT))
        lines.append(f"    released once its session ends = {released} transport closes={stuck_adapter.closes}")
        starter.join(LIMIT)
        lines.append(f"    starter = {starter.line}")
        unstuck.set()
        lines.append(f"    close again = {_cleanup(api.close)}")
    borrowed_adapter = Adapter(transports, Response(responses, 200, _ISSUED))
    borrowed = auth.ClientCredentialsProvider(
        _TOKEN, client_id="c", client_secret=secret, token_transport=borrowed_adapter
    )
    with exchange.client() as native:
        api = client(native, borrowed)
        lines.append(f"  borrowed provider = {_cleanup(api.close)}")
    lines.append(f"    get afterwards = {_outcome(lambda: borrowed.get(credential_context(auth)))}")
    borrowed.close()


async def _aclosing(package: ModuleType, lines: list[str]) -> None:
    """Close asyncio clients owning the SDK's providers within their cleanup time as well."""
    auth, options, transports, responses = (
        importlib.import_module(f"{package.__name__}.{name}") for name in ("auth", "options", "transports", "responses")
    )
    exchange = Exchange(lines)
    secret = auth.AsyncStaticCredentialProvider(auth.ApiKeyCredential("s"))

    def provider(adapter: AsyncAdapter) -> Any:
        return auth.AsyncClientCredentialsProvider(
            _TOKEN, client_id="c", client_secret=secret, token_transport=transports.OwnedTransportAdapter(adapter)
        )

    def client(native: Any, credentials: Any, **settings: Any) -> Any:
        return package.AsyncClient(
            http_client=native, options=options.ClientOptions(auth=auth.AuthConfig({"bearer": credentials}), **settings)
        )

    hold = asyncio.Event()
    running_adapter = AsyncAdapter(transports, AsyncResponse(responses, 200, _ISSUED), hold=hold)
    running = provider(running_adapter)
    starter = asyncio.create_task(_aoutcome(lambda: running.get(credential_context(auth))))
    while not running_adapter.entered.is_set():
        await asyncio.sleep(0.01)
    async with exchange.async_client() as native:
        api = client(native, auth.OwnedCredentialProvider(running), cleanup_timeout=0.2)
        lines.append(f"  async provider with a running acquisition = {await _acleanup(api.aclose)}")
        hold.set()
        lines.append(f"    starter = {await starter}")
        lines.append(f"    close again = {await _acleanup(api.aclose)} transport closes={running_adapter.closes}")
    failing_adapter = _AsyncFailingClose(transports)
    failing = provider(failing_adapter)
    async with exchange.async_client() as native:
        api = client(native, auth.OwnedCredentialProvider(failing))
        lines.append(f"  async failing release = {await _acleanup(api.aclose)}")
        lines.append(f"    close again = {await _acleanup(api.aclose)} transport closes={failing_adapter.closes}")


def oauth_closing(package: ModuleType, lines: list[str]) -> None:
    """Exercise client close with owned and borrowed OAuth providers of the SDK in both execution modes."""
    _closing(package, lines)

    async def flows() -> None:
        await _aclosing(package, lines)

    run(flows)
