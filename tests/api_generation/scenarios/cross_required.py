"""Call a server whose parameters and body give `required` as text or a number through its client.

The targets read `required` as the parser does: `'yes'`, `1` and `'true'` are true and `'off'` is false, so `q`, `n`
and the body are required on both sides, and `X-Off` is optional. The response header `X-Rate` names a location, which
a Header Object does not take: it stays a response header, which the client decodes as a simple-style array.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "cross/required-text.yaml"


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return a service that answers with the `X-Rate` response header."""
    del models

    def post_items(**_: object) -> object:
        return server.HTTPResult(200, "ok", headers={"X-Rate": "1,2"})

    return {"default": service(server.services.DefaultService, record, post_items=post_items)}


def calls(kit: Kit) -> Calls:
    """Return calls with every required argument, and requests that leave one out."""
    api, header = kit.api.default, kit.module("types.default").decode_post_items_header
    url = f"{kit.options.get('base_url', 'https://required.example.com')}/items"

    def raw(query: str, body: bytes | None) -> Callable[[], object]:
        headers = {} if body is None else {"content-type": "application/json"}
        return lambda: kit.then(
            kit.http.post(f"{url}{query}", content=body, headers=headers), lambda response: response.status_code
        )

    return [
        ("post_items", lambda: api.post_items(q="a", n=1, body=kit.models.ItemsPostRequest(name="x"))),
        (
            "post_items X-Rate",
            lambda: kit.then(
                api.with_response.post_items(q="a", n=1, body=kit.models.ItemsPostRequest(name="x")),
                lambda result: header(result.info, name="X-Rate"),
            ),
        ),
        ("post_items without q", lambda: api.post_items(n=1, body=kit.models.ItemsPostRequest(name="x"))),
        ("post without q", raw("?n=1", b"{}")),
        ("post without n", raw("?q=a", b"{}")),
        ("post without a body", raw("?q=a&n=1", None)),
    ]
