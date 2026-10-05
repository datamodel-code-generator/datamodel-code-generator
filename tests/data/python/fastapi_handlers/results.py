"""Services of the result server: each request's case names the value, result, or Response it returns."""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.responses import Response

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType


def services(server: ModuleType, models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return a service whose results cover bare values, HTTP results, and Responses under each declared response."""
    result = server.HTTPResult
    thing = models.Thing(id=1)
    results: dict[str, Callable[[], object]] = {
        "bare": lambda: thing,
        "headers": lambda: result(200, thing, {"X-Rate": "5", "X-Tag": "t"}),
        "created": lambda: result(201, thing, {"Location": "/things/1"}),
        "no-content": lambda: result(204),
        "no-content-body": lambda: result(204, thing),
        "range": lambda: result(202, {"any": [1, 2.5, None]}),
        "invalid-body": lambda: result(201, {"id": "x"}, {"Location": "/things/x"}),
        "client-error": lambda: result(404, "missing"),
        "client-error-number": lambda: result(404, 1),
        "blob": lambda: b"\x00\x01",
        "problem": lambda: result(500, models.Problem(title="Broken")),
        "missing-body": lambda: result(200),
        "response": lambda: Response(content=b'{"id":3}', status_code=200, media_type="application/json"),
        "response-html": lambda: Response(content=b"<p>", status_code=200, media_type="text/html"),
        "head-empty": lambda: result(200),
        "head-body": lambda: result(200, thing),
        "head-bare": lambda: thing,
        "head-none": lambda: None,
        "text": lambda: models.FieldPlainGetResponse("abc"),
        "too-long": lambda: models.FieldPlainGetResponse.model_construct(root="abcd"),
        "latin": lambda: models.FieldLatinGetResponse("café"),
        "euro": lambda: models.FieldLatinGetResponse("€"),
        "undeclared": lambda: result(404),
        "response-undeclared": lambda: Response(status_code=500),
        "anything": lambda: result(200, models.FieldNothingGetResponse()),
        "none": lambda: None,
        "value": lambda: {"a": 1},
    }

    def respond(name: str, case: str) -> object:
        calls.append(f"{name}({case})")
        return results[case]()

    class Untagged(server.services.UntaggedService):
        def get_result(self, *, case: str) -> object:
            return respond("get_result", case)

        def head_result(self, *, case: str) -> object:
            return respond("head_result", case)

        def get_plain(self, *, case: str) -> object:
            return respond("get_plain", case)

        def get_latin(self, *, case: str) -> object:
            return respond("get_latin", case)

        def get_blob(self, *, case: str) -> object:
            return respond("get_blob", case)

        def get_nothing(self, *, case: str) -> object:
            return respond("get_nothing", case)

        def post_empty(self, *, case: str) -> object:
            return respond("post_empty", case)

    return {"default": {"untagged": Untagged()}}
