"""Services of the result servers: each request's case names the value, result, or Response it returns."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from starlette.responses import Response

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType


def services(server: ModuleType, models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return a service whose results cover bare values, HTTP results, and Responses under each declared response.

    It holds the methods of the results document and of the nullable results documents, one of those asynchronous;
    those include operations that declare only a `2XX`, a `default`, or a `4XX` response.
    """
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
        "client-error-bytes": lambda: result(404, b"caf\xe9"),
        "blob": lambda: b"\x00\xff",
        "problem": lambda: result(500, models.Problem(title="Broken")),
        "missing-body": lambda: result(200),
        "response": lambda: Response(content=b'{"id":3}', status_code=200, media_type="application/json"),
        "response-html": lambda: Response(content=b"<p>", status_code=200, media_type="text/html"),
        "head-empty": lambda: result(200),
        "head-body": lambda: result(200, thing),
        "head-bare": lambda: thing,
        "head-none": lambda: None,
        "text": lambda: models.PlainGetResponse("abc"),
        "too-long": lambda: models.PlainGetResponse.model_construct(root="abcd"),
        "latin": lambda: models.LatinGetResponse("café"),
        "euro": lambda: models.LatinGetResponse("€"),
        "undeclared": lambda: result(404),
        "response-undeclared": lambda: Response(status_code=500),
        "anything": lambda: result(200, models.NothingGetResponse()),
        "none": lambda: None,
        "value": lambda: {"a": 1},
        "null": lambda: result(200, None, {"X-Rate": "5"}),
        "word": lambda: "abc",
        "long-word": lambda: result(200, "too long"),
        "numbers": lambda: [1, 2],
        **{f"null-{status}": partial(result, status, None) for status in (201, 202, 203, 206, 404, 500)},
    }

    def respond(name: str, case: str) -> object:
        calls.append(f"{name}({case})")
        return results[case]()

    class Default(server.services.DefaultService):
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

        def get_text(self, *, case: str) -> object:
            return respond("get_text", case)

        def head_text(self, *, case: str) -> object:
            return respond("head_text", case)

        async def get_list(self, *, case: str) -> object:
            return respond("get_list", case)

        def get_name(self, *, case: str) -> object:
            return respond("get_name", case)

        def get_value(self, *, case: str) -> object:
            return respond("get_value", case)

        def get_anything(self, *, case: str) -> object:
            return respond("get_anything", case)

        def get_pet(self, *, case: str) -> object:
            return respond("get_pet", case)

        def get_thing(self, *, case: str) -> object:
            return respond("get_thing", case)

        def get_note(self, *, case: str) -> object:
            return respond("get_note", case)

        def post_memo(self, *, case: str, body: object) -> object:
            del body
            return respond("post_memo", case)

        def get_range(self, *, case: str) -> object:
            return respond("get_range", case)

        def get_accepted(self, *, case: str) -> object:
            return respond("get_accepted", case)

        def get_default(self, *, case: str) -> object:
            return respond("get_default", case)

        def get_error(self, *, case: str) -> object:
            return respond("get_error", case)

        def get_ok(self, *, case: str) -> object:
            return respond("get_ok", case)

        def get_any_alias(self, *, case: str) -> object:
            return respond("get_any_alias", case)

        def post_ok(self, *, case: str, body: object) -> object:
            del body
            return respond("post_ok", case)

        def post_any_alias(self, *, case: str, body: object) -> object:
            del body
            return respond("post_any_alias", case)

        def post_count(self, *, case: str, body: object) -> object:
            del body
            return respond("post_count", case)

    return {"default": {"default": Default()}}
