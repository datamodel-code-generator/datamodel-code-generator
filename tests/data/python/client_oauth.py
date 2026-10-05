"""Shared pieces of the OAuth provider scenarios: outcome lines, token responses, and injected transports and secrets."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from tests.data.python.client_runtime import raw_response

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator
    from types import ModuleType

    import httpx2

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


def failure_line(error: BaseException) -> str:
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


class Response:
    """A token response of an injected transport: its body arrives in two halves, maybe slowly, and may fail."""

    def __init__(  # noqa: PLR0913
        self,
        responses: ModuleType,
        status: object = 200,
        body: bytes = b"",
        *,
        failure: BaseException | None = None,
        close_failure: BaseException | None = None,
        reported: bool = False,
        pause: float = 0,
        tail: float = 0,
    ) -> None:
        self.status_code = status
        self.headers = responses.HeadersView((("content-type", "application/json"),))
        self.body = body
        self.failure = failure
        self.close_failure = close_failure
        self.reported = reported
        self.pause = pause
        self.tail = tail

    def iter_raw_bytes(self) -> Iterator[bytes]:
        half = len(self.body) // 2
        yield self.body[:half]
        time.sleep(self.pause)
        yield self.body[half:]
        time.sleep(self.tail)
        if self.failure is not None:
            raise self.failure

    def close(self) -> None:
        if self.close_failure is not None:
            raise self.close_failure


class AsyncResponse(Response):
    async def iter_raw_bytes(self) -> AsyncIterator[bytes]:  # ty: ignore[invalid-method-override]
        half = len(self.body) // 2
        yield self.body[:half]
        await asyncio.sleep(self.pause)
        yield self.body[half:]
        await asyncio.sleep(self.tail)
        if self.failure is not None:
            raise self.failure

    async def aclose(self) -> None:
        Response.close(self)


@dataclass(frozen=True)
class Late:
    """A scripted reply the adapter gives only after a delay."""

    delay: float
    reply: object


@dataclass(frozen=True)
class Reported:
    """A scripted failure the adapter raises after reporting that the response started with these headers."""

    error: BaseException
    headers: object


class Adapter:
    """An injected token transport answering from a script of responses and failures, maybe after a gate opens."""

    def __init__(
        self,
        transports: ModuleType,
        *replies: object,
        retries: int | None = 0,
        evidence: bool = False,
        gate: threading.Event | None = None,
    ) -> None:
        self.capabilities = transports.TransportCapabilities(
            internal_retry_limit=retries, delivery_evidence=evidence, http_versions=("HTTP/1.1",)
        )
        self.replies = list(replies)
        self.closes = 0
        self.sends = 0
        self.gate = gate
        self.entered = threading.Event()

    def _reply(self, context: Any) -> object:
        self.sends += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Late):
            time.sleep(reply.delay)
            reply = reply.reply
        if isinstance(reply, Reported):
            context.trace.response_headers_received(http_version="HTTP/1.1", status_code=200, headers=reply.headers)
            raise reply.error
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, Response) and reply.reported:
            context.trace.response_headers_received(http_version="HTTP/1.1", status_code=200, headers=reply.headers)
        return reply

    def send(self, request: object, context: object) -> object:
        del request
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(30)
        return self._reply(context)

    def close(self) -> None:
        self.closes += 1


class AsyncAdapter(Adapter):
    def __init__(self, transports: ModuleType, *replies: object, hold: asyncio.Event | None = None) -> None:
        super().__init__(transports, *replies)
        self.hold = hold

    async def send(self, request: object, context: object) -> object:  # ty: ignore[invalid-method-override]
        del request
        self.entered.set()
        if self.hold is not None:
            await self.hold.wait()
        return self._reply(context)

    async def aclose(self) -> None:
        self.closes += 1


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


