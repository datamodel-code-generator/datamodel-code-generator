"""Shared pieces of the OAuth provider scenarios: outcome lines, token responses, scripted transports and secrets."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import httpx2

from tests.data.python.client_runtime import raw_response

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator

    from tests.data.python.client_runtime import Exchange

_FIELDS: Final = (
    "reason",
    "delivery_state",
    "phase",
    "status_code",
    "oauth_error",
    "effective_timeout",
    "field_path",
    "loop_mismatch",
    "source",
)
_SECRETS: Final = ("access-", "refresh-", "se:cr et", "async secret", "upload-control")


def failure_line(error: BaseException) -> str:
    """Describe a failure by its safe fields, naming any token or secret its representations reveal."""
    parts = [type(error).__name__]
    for name in _FIELDS:
        value = getattr(error, name, None)
        if value not in (None, (), False):
            parts.append(f"{name}={getattr(value, 'value', value)}")
    if (cause := getattr(error, "cause", None)) is not None:
        parts.append(f"cause={type(cause).__name__}")
        if (reason := getattr(cause, "reason", None)) is not None:
            parts.append(f"reason={reason}")
    if (context := error.__cause__) is not None:
        parts.append(f"from={type(context).__name__}")
    text = f"{error!r} {error} {getattr(error, 'cause', None)!r} {context!r}"
    if leaked := [secret for secret in _SECRETS if secret in text]:
        parts.append(f"leaked={leaked}")
    return " ".join(parts)


def json_reply(
    status: int, payload: object, content_type: str = "application/json", **headers: str
) -> Callable[[httpx2.Request], httpx2.Response]:
    content = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return raw_response(status, content, content_type, **headers)


def delayed(delay: float, reply: Callable[[httpx2.Request], httpx2.Response]) -> Callable[[httpx2.Request], httpx2.Response]:
    def answer(request: httpx2.Request) -> httpx2.Response:
        time.sleep(delay)
        return reply(request)

    return answer


LIMIT: Final = 10.0


class Caller(threading.Thread):
    """A thread running one call, keeping the line that reports its outcome."""

    def __init__(self, call: Callable[[], object], report: Callable[[Callable[[], object]], str]) -> None:
        super().__init__()
        self.call = call
        self.report = report
        self.line = ""

    def run(self) -> None:
        self.line = self.report(self.call)


class _Halves(httpx2.SyncByteStream, httpx2.AsyncByteStream):
    """A token response body arriving in two halves, maybe slowly, that may fail while it streams or closes."""

    def __init__(self, response: Response) -> None:
        self.response = response

    def __iter__(self) -> Iterator[bytes]:
        response = self.response
        half = len(response.body) // 2
        yield response.body[:half]
        time.sleep(response.pause)
        yield response.body[half:]
        time.sleep(response.tail)
        if response.failure is not None:
            raise response.failure

    async def __aiter__(self) -> AsyncIterator[bytes]:
        response = self.response
        half = len(response.body) // 2
        yield response.body[:half]
        await self._pause(response.pause)
        yield response.body[half:]
        await self._pause(response.tail)
        if response.failure is not None:
            raise response.failure

    async def _pause(self, delay: float) -> None:
        if self.response.blocking:
            time.sleep(delay)
        else:
            await asyncio.sleep(delay)

    def close(self) -> None:
        if self.response.close_failure is not None:
            raise self.response.close_failure

    async def aclose(self) -> None:
        self.close()


@dataclass(frozen=True)
class Response:
    """A JSON token response of a scripted transport: its body arrives in two halves, maybe slowly, and may fail.

    A blocking response pauses with the event loop blocked, so only the endpoint's own checks see the pause end.
    """

    status: int = 200
    body: bytes = b""
    failure: BaseException | None = None
    close_failure: BaseException | None = None
    pause: float = 0
    tail: float = 0
    blocking: bool = False

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            self.status, headers={"content-type": "application/json"}, stream=_Halves(self), request=request
        )


@dataclass(frozen=True)
class Late:
    """A scripted reply the transport gives only after a delay."""

    delay: float
    reply: object


class Script(httpx2.BaseTransport, httpx2.AsyncBaseTransport):
    """A token transport answering from a script of replies and failures, maybe after a gate opens.

    An asyncio request held back waits at most its read timeout, as a native transport does, and then times out.
    """

    def __init__(
        self, *replies: object, gate: threading.Event | None = None, hold: asyncio.Event | None = None
    ) -> None:
        self.replies = list(replies)
        self.sends = 0
        self.gate = gate
        self.hold = hold
        self.entered = threading.Event()

    def _reply(self, request: httpx2.Request) -> httpx2.Response:
        self.sends += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Late):
            time.sleep(reply.delay)
            reply = reply.reply
        if isinstance(reply, BaseException):
            raise reply
        return reply(request)

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(LIMIT)
        return self._reply(request)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.entered.set()
        if (hold := self.hold) is not None:
            try:
                await asyncio.wait_for(hold.wait(), request.extensions["timeout"]["read"])
            except TimeoutError:
                msg = "held past the read timeout"
                raise httpx2.ReadTimeout(msg, request=request) from None
        return self._reply(request)

    def client(self) -> httpx2.Client:
        """Return a native client sending through this script."""
        return httpx2.Client(transport=self)

    def async_client(self) -> httpx2.AsyncClient:
        """Return a native asyncio client sending through this script."""
        return httpx2.AsyncClient(transport=self)


class Secret:
    """A client secret provider that answers with its material, fails, or first waits."""

    def __init__(self, material: object = None, *, failure: BaseException | None = None, delay: float = 0) -> None:
        self.material = material
        self.failure = failure
        self.delay = delay

    def get(self, context: object) -> object:
        del context
        time.sleep(self.delay)
        if self.failure is not None:
            raise self.failure
        return self.material


class AsyncSecret(Secret):
    async def get(self, context: object) -> object:  # ty: ignore[invalid-method-override]
        del context
        await asyncio.sleep(self.delay)
        if self.failure is not None:
            raise self.failure
        return self.material


def closed_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def stop(exchange: Exchange) -> None:
    if (server := exchange.server) is not None:
        exchange.server = None
        server.stop()


