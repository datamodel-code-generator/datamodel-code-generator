"""Handlers of the result server: each request's case names the value, result, or Response it returns."""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.responses import Response

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType


def handlers(server: ModuleType, models: ModuleType, calls: list[str]) -> dict[str, dict[str, Callable[..., object]]]:
    """Return handlers whose results cover every declared-response check."""
    result = server.HTTPResult
    thing = models.Thing(id=1)
    results: dict[str, Callable[[], object]] = {
        "bare": lambda: thing,
        "model-value": lambda: server.responses.GetResultResponseCodecs.body(status_code=200).snapshot(thing),
        "csv": lambda: result(status_code=200, body=models.FieldResultsGetResponse("a,b"), media_type="text/csv"),
        "binary": lambda: result(status_code=200, body=b"\x00\x01", media_type="application/octet-stream"),
        "image": lambda: result(status_code=200, body=b"png", media_type="image/png"),
        "html": lambda: result(status_code=200, body="<p>", media_type="text/html"),
        "bad-media": lambda: result(status_code=200, body="x", media_type="not a media type"),
        "csv-bytes": lambda: result(status_code=200, body=b"x", media_type="text/csv"),
        "binary-text": lambda: result(status_code=200, body="x", media_type="application/octet-stream"),
        "headers": lambda: result(status_code=200, body=thing, headers=(("X-Rate", "5"), ("X-Tag", "t"), ("X-Other", "o"))),
        "negative-rate": lambda: result(status_code=200, body=thing, headers=(("X-Rate", "-1"),)),
        "bad-name": lambda: result(status_code=200, body=thing, headers=(("Bad Name", "v"),)),
        "crlf": lambda: result(status_code=200, body=thing, headers=(("X-Rate", "1\r\n"),)),
        "owned": lambda: result(status_code=200, body=thing, headers=(("Content-Type", "text/plain"),)),
        "header-list": lambda: result(status_code=200, body=thing, headers=[("X-Rate", "1")]),
        "header-item": lambda: result(status_code=200, body=thing, headers=(("X-Rate",),)),
        "created": lambda: result(status_code=201, body=thing, headers=(("Location", "/things/1"),)),
        "created-without-location": lambda: result(status_code=201, body=thing),
        "no-content": lambda: result(status_code=204),
        "no-content-body": lambda: result(status_code=204, body=thing),
        "range": lambda: result(status_code=202, body={"any": [1, 2.5, None]}),
        "range-object": lambda: result(status_code=202, body=object()),
        "client-error": lambda: result(status_code=404, body="missing", media_type="text/plain"),
        "client-error-bytes": lambda: result(status_code=404, body=b"missing", media_type="text/plain"),
        "problem": lambda: result(status_code=500, body=models.Problem(title="Broken")),
        "status-bool": lambda: result(status_code=True),
        "status-low": lambda: result(status_code=99),
        "missing-body": lambda: result(status_code=200),
        "response": lambda: Response(content=b'{"id":3}', status_code=200, media_type="application/json"),
        "response-html": lambda: Response(content=b"<p>", status_code=200, media_type="text/html"),
        "response-header": lambda: Response(content=b"{}", status_code=200, headers={"Bad Name": "v"}),
        "head-empty": lambda: result(status_code=200),
        "head-body": lambda: result(status_code=200, body=thing),
        "head-bare": lambda: thing,
        "head-none": lambda: None,
        "text": lambda: models.FieldPlainGetResponse("abc"),
        "too-long": lambda: models.FieldPlainGetResponse.model_construct(root="abcd"),
        "undeclared": lambda: result(status_code=404),
        "response-undeclared": lambda: Response(status_code=500),
        "anything": lambda: result(status_code=200, body=models.FieldNothingGetResponse()),
        "none": lambda: None,
        "value": lambda: {"a": 1},
    }

    def responder(name: str) -> Callable[..., object]:
        def respond(*, case: str) -> object:
            calls.append(f"{name}({case})")
            return results[case]()

        return respond

    return {"default": {name: responder(name) for name in ("get_result", "head_result", "get_plain", "get_nothing", "post_empty")}}
