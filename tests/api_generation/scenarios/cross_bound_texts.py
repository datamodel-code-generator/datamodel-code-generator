"""Call the bound texts server through its client: parameter and form objects written as their models type them.

A query object or URL-encoded form whose schema declares properties without a type is an object, as its model is,
and one built with allOf writes the members of its merged model. A form closed by the schema its allOf references
writes no other member. Requests the client never sends show what the server reads of them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "client/text-bound-types.yaml"
_FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return a service whose every method records its typed arguments."""
    del models
    return {"default": service(server.services.DefaultService, record)}


def _raw(kit: Kit, method: str, path: str, content: str | None = None) -> object:
    """Send a request the client never writes, and return the status with the records' types and locations."""
    headers = {} if content is None else _FORM
    return kit.then(
        kit.http.request(method, f"https://texts.example.com{path}", content=content, headers=headers),
        lambda response: (
            response.status_code,
            [(record["type"], record["loc"]) for record in response.json()["detail"]]
            if response.status_code == 422
            else None,
        ),
    )


def calls(kit: Kit) -> Calls:
    """Return a call for each parameter and form object, and requests with members their models do not declare."""
    api, models = kit.api.default, kit.models
    return [
        (
            "list_untyped",
            lambda: api.list_untyped(filter=models.UntypedGetQueryFilterParameter(color="red", size=3)),
        ),
        ("list_untyped raw", lambda: _raw(kit, "GET", "/untyped?color=blue&size=x")),
        ("submit_untyped", lambda: api.submit_untyped(body=models.UntypedPostRequest(name="n", count=2))),
        ("submit_untyped raw", lambda: _raw(kit, "POST", "/untyped", "name=n&count=two")),
        (
            "list_merged",
            lambda: api.list_merged(filter=models.MergedGetQueryFilterParameter(color="red", size=3, flag=True)),
        ),
        ("list_merged raw", lambda: _raw(kit, "GET", "/merged?color=blue&size=4&flag=maybe")),
        (
            "list_merged_deep",
            lambda: api.list_merged_deep(deep=models.MergedDeepGetQueryDeepParameter(color="red", page=2)),
        ),
        ("submit_closed", lambda: api.submit_closed(body=models.ClosedPostRequest(name="n"))),
        ("submit_closed raw", lambda: _raw(kit, "POST", "/closed", "name=n&extra=1")),
        (
            "read_a",
            lambda: api.read_a(
                name=models.ANameGetPathNameParameter(kind="k"),
                label=models.ANameGetQueryLabelParameter(tone="t", level=1),
            ),
        ),
        (
            "read_b",
            lambda: api.read_b(
                name=models.BNameGetPathNameParameter(kind="k"),
                label=models.BNameGetQueryLabelParameter(tone="t", level=2),
            ),
        ),
    ]
