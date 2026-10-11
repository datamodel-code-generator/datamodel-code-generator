"""Call the results server through its client: statuses, ranges, `default`, typed errors, headers, and media types.

The server answers each call's `case` with its primary value, an `HTTPResult` of another status, or a Starlette
response of another media type; the client reports the value and status it decoded, or the error it raised.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.responses import Response

from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "fastapi/server-results.yaml"
CLIENT = {"default_base_url": "https://results.example.com"}


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return a service that answers each case of each operation."""
    result = server.HTTPResult
    results = {
        "thing": lambda: models.Thing(id=1),
        "rate": lambda: result(200, models.Thing(id=2), headers={"X-Rate": "5", "X-Tag": "t"}),
        "created": lambda: result(201, models.Thing(id=3), headers={"Location": "/results/3"}),
        "nothing": lambda: result(204, None),
        "accepted": lambda: result(202, {"queued": True}),
        "csv": lambda: Response("a,b\n1,2\n", media_type="text/csv"),
        "octets": lambda: Response(b"\x00\x01", media_type="application/octet-stream"),
        "png": lambda: Response(b"\x89PNG", media_type="image/png"),
        "teapot": lambda: result(418, "short and stout"),
        "problem": lambda: result(500, models.Problem(title="broken")),
    }
    plain = {"short": lambda: "abc", "long": lambda: "abcd"}
    return {
        "untagged": service(
            server.services.UntaggedService,
            record,
            get_result=lambda case: results[case](),
            get_plain=lambda case: plain[case](),
            get_latin=lambda **_: "caf\u00e9",
            get_nothing=lambda case: result(200 if case == "ok" else 400, {"case": case}),
            get_blob=lambda case: case.encode() * 2,
        )
    }


def calls(kit: Kit) -> Calls:
    """Return a call for each case, through the response view where the status and media type matter."""
    api = kit.api.default
    view = api.with_response
    once = kit.connect(max_retries=0).default
    header = kit.module("types.default").decode_get_result_header
    return [
        ("get_result thing", lambda: view.get_result(case="thing")),
        (
            "get_result headers",
            lambda: kit.then(view.get_result(case="rate"), lambda result: (result, header(result.info, name="X-Rate"))),
        ),
        (
            "get_result created",
            lambda: kit.then(
                view.get_result(case="created"), lambda result: (result, header(result.info, name="Location"))
            ),
        ),
        ("get_result nothing", lambda: view.get_result(case="nothing")),
        ("get_result accepted", lambda: view.get_result(case="accepted")),
        ("get_result csv", lambda: view.get_result(case="csv", response_media_type="text/csv")),
        (
            "get_result octets",
            lambda: view.get_result(case="octets", response_media_type="application/octet-stream"),
        ),
        ("get_result image", lambda: view.get_result(case="png", response_media_type="image/png")),
        ("get_result 4XX", lambda: view.get_result(case="teapot")),
        ("get_result default", lambda: once.with_response.get_result(case="problem")),
        ("head_result", lambda: view.head_result(case="thing")),
        ("get_plain", lambda: api.get_plain(case="short")),
        ("get_plain past its maxLength", lambda: once.get_plain(case="long")),
        ("get_latin", lambda: view.get_latin(case="latin")),
        ("get_nothing success", lambda: view.get_nothing(case="ok")),
        ("get_nothing error", lambda: api.get_nothing(case="bad")),
        ("get_blob", lambda: view.get_blob(case="ab")),
        ("post_empty", lambda: api.post_empty(case="x")),
    ]
