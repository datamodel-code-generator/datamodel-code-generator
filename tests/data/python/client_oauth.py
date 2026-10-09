"""Shared pieces of the OAuth provider scenarios: safe failure lines, token responses, and scripted token clients."""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import httpx2

from tests.data.python.client_runtime import json_response

if TYPE_CHECKING:
    from collections.abc import Callable

_FIELDS: Final = ("reason", "delivery_state", "phase", "status_code", "oauth_error", "field_path")
_SECRETS: Final = ("access-", "refresh-", "se:cr et", "upload-control")


def failure_line(error: BaseException) -> str:
    """Describe a failure by its safe fields, naming any token or secret its representations reveal."""
    parts = [type(error).__name__]
    for name in _FIELDS:
        value = getattr(error, name, None)
        if value not in (None, (), False):
            parts.append(f"{name}={getattr(value, 'value', value)}")
    if (cause := getattr(error, "cause", None)) is not None:
        parts.append(f"cause={type(cause).__name__}")
    text = f"{error!r} {error}"
    if leaked := [secret for secret in _SECRETS if secret in text]:
        parts.append(f"leaked={leaked}")
    return " ".join(parts)


def issued(access: str, *, expires_in: object = 3600, **members: object) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return a responder of a successful token response."""
    payload = {
        "access_token": access,
        "token_type": "Bearer",
        **({} if expires_in is None else {"expires_in": expires_in}),
    }
    return json_response(200, {**payload, **members})


@dataclass(frozen=True)
class Response:
    """A JSON token response of a scripted token client."""

    status: int = 200
    body: bytes = b""

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            self.status, headers={"content-type": "application/json"}, content=self.body, request=request
        )


def token(access: str, *, expires_in: int = 3600, **members: object) -> Response:
    """Return a scripted successful token response."""
    payload = {"access_token": access, "token_type": "Bearer", "expires_in": expires_in, **members}
    return Response(200, json.dumps(payload).encode())


class Script(httpx2.BaseTransport, httpx2.AsyncBaseTransport):
    """A token client's transport answering from a script of replies and failures, recording each token request.

    An asyncio request waits for `hold` when one is given, so a caller can be cancelled while its token request is
    in flight.
    """

    def __init__(
        self, *replies: Callable[[httpx2.Request], httpx2.Response] | BaseException, hold: asyncio.Event | None = None
    ) -> None:
        self.replies = list(replies)
        self.sends = 0
        self.hold = hold
        self.entered = threading.Event()
        self.forms: list[str] = []

    def _reply(self, request: httpx2.Request) -> httpx2.Response:
        self.sends += 1
        request.read()
        self.forms.append(f"{request.url} {request.content.decode()}")
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply(request)

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        self.entered.set()
        return self._reply(request)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.entered.set()
        if (hold := self.hold) is not None:
            await hold.wait()
        return self._reply(request)

    def client(self) -> httpx2.Client:
        """Return a native client sending through this script."""
        return httpx2.Client(transport=self)

    def async_client(self) -> httpx2.AsyncClient:
        """Return a native asyncio client sending through this script."""
        return httpx2.AsyncClient(transport=self)
