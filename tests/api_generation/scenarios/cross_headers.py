"""Call the headers server through its client and directly: header text is ASCII on both sides, as RFC 9110 writes it.

The service answers with the headers each call asks for in its `X-Name` header: ASCII header text the client decodes,
a result header the server refuses to send, whose cause an exception handler answers with, or a response of its own
whose Latin-1 header text the client refuses.
Requests whose header bytes are not ASCII are refused with `400` whether FastAPI reads the header (`X-Name`) or an
adapter does (`X-Tags`), and so are cookies the operation reads (`session` and `ids`), RFC 6265 cookie octets being
ASCII too; another cookie is not read. The calls run over an ASGI transport and a socket only: Starlette's test
client reads response headers as UTF-8 and re-encodes request header bytes, which is not what goes over the wire.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastapi.exceptions import ResponseValidationError
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse, Response

from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    from fastapi import Request

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "cross/response-headers.yaml"
SOCKET = True


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return a service that answers with the headers the request's `X-Name` asks for."""
    del models
    answers: dict[str | None, Callable[[], object]] = {
        None: lambda: server.HTTPResult(204, None, headers={"X-Name": "cafe", "X-Tags": "cafe,b"}),
        "beyond-ascii": lambda: server.HTTPResult(204, None, headers={"X-Name": "café"}),
        "not-a-token": lambda: server.HTTPResult(204, None, headers={"X Name": "cafe"}),
        "own-response": lambda: Response(status_code=204, headers={"X-Name": "café", "X-Tags": "café,b"}),
    }

    def get_text(**arguments: object) -> object:
        asked = arguments["X_Name"]
        return answers[getattr(asked, "root", asked)]()

    return {
        "default": service(server.services.DefaultService, record, get_text=get_text),
        "exception_handlers": {ResponseValidationError: _refused},
    }


def _refused(request: Request, error: Exception) -> PlainTextResponse:
    """Answer a result the server refuses to send with the messages of its records."""
    del request
    messages = (record["msg"] for record in cast("ResponseValidationError", error).errors())
    return PlainTextResponse("; ".join(messages), status_code=500)


def calls(kit: Kit) -> Calls:
    """Return calls that decode each declared header, and requests whose header bytes are not ASCII."""
    if isinstance(kit.http, TestClient):
        return []
    header = kit.module("types.default").decode_get_text_header
    text = kit.connect(max_retries=0).default.with_response.get_text
    url = f"{kit.options.get('base_url', 'https://headers.example.com')}/text"

    def raw(headers: list[tuple[bytes, bytes]]) -> Callable[[], object]:
        return lambda: kit.then(kit.http.get(url, headers=headers), lambda response: response.status_code)

    return [
        (
            "get_text",
            lambda: kit.then(
                text(),
                lambda result: (
                    header(result.info, name="X-Name"),
                    header(result.info, name="X-Tags"),
                    result.info.headers.get_all("x-name"),
                    result.info.headers.get_all("x-tags"),
                ),
            ),
        ),
        ("get_text with a result header beyond ASCII", lambda: text(X_Name="beyond-ascii")),
        ("get_text with a result header name that is not a token", lambda: text(X_Name="not-a-token")),
        (
            "get_text with Latin-1 headers of the service's own response",
            lambda: kit.then(text(X_Name="own-response"), lambda result: result.info.headers.get_all("x-name")),
        ),
        (
            "decode its X-Name",
            lambda: kit.then(text(X_Name="own-response"), lambda result: header(result.info, name="X-Name")),
        ),
        (
            "decode its X-Tags",
            lambda: kit.then(text(X_Name="own-response"), lambda result: header(result.info, name="X-Tags")),
        ),
        ("get_text with X-Name beyond ASCII", lambda: text(X_Name="café")),
        ("X-Name in Latin-1", raw([(b"X-Name", b"caf\xe9")])),
        ("X-Name in UTF-8", raw([(b"X-Name", "café".encode())])),
        ("X-Name repeated in Latin-1", raw([(b"X-Name", b"cafe"), (b"X-Name", b"caf\xe9")])),
        ("X-Tags in Latin-1", raw([(b"X-Tags", b"caf\xe9,b")])),
        ("X-Tags in UTF-8", raw([(b"X-Tags", "café,b".encode())])),
        ("get_text with a session cookie beyond ASCII", lambda: text(session="café")),
        ("session cookie in Latin-1", raw([(b"Cookie", b"session=caf\xe9")])),
        ("session cookie in UTF-8", raw([(b"Cookie", "a=b; session=café".encode())])),
        ("ids cookie in Latin-1", raw([(b"Cookie", b"ids=caf\xe9")])),
        ("ids cookie in UTF-8", raw([(b"Cookie", "ids=a; ids=café".encode())])),
        ("another cookie in UTF-8", raw([(b"Cookie", "other=café; session=s; ids=a".encode())])),
    ]
