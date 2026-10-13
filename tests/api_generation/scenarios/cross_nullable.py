"""Call the nullable results server through its client with type alias models: `null` under each declared response.

The server answers each call's `case` with a bare `None`, an `HTTPResult` without a body, or a value; the client
reports the `None` or value it decoded, or the error it raised. An inline object schema that admits null takes
`None` as its body and its result, and still refuses an object its schema does not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "fastapi/server-nullable-results.yaml"
MODEL = {"use_type_alias": True}
CLIENT = {"default_base_url": "https://nullable.example.com"}
OPERATIONS = ("get_text", "get_list", "get_name", "get_value", "get_anything", "get_pet", "get_thing", "post_memo")


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return a service whose operations answer each case alike."""
    result = server.HTTPResult
    answers = {
        "none": lambda: None,
        "result": lambda: result(200, None, headers={"X-Rate": "5"}),
        "created": lambda: result(201, None),
        "partial": lambda: result(206, None),
        "missing": lambda: result(404, None),
        "broken": lambda: result(500, None),
        "word": lambda: "abc",
        "numbers": lambda: [1, 2],
        "thing": lambda: models.Thing(id=1),
        "memo": lambda: models.FieldMemosPostResponse(text="m"),
    }
    return {
        "default": service(
            server.services.DefaultService, record, **dict.fromkeys(OPERATIONS, lambda case, **_: answers[case]())
        )
    }


def calls(kit: Kit) -> Calls:
    """Return a call for each response that may be null, through the response view where the media type matters."""
    api, models = kit.api.default, kit.models
    view = api.with_response
    once = kit.connect(max_retries=0).default
    rate = kit.module("types.default").decode_get_text_header
    return [
        ("get_text None", lambda: view.get_text(case="none")),
        ("get_text None value", lambda: api.get_text(case="none")),
        (
            "get_text null result",
            lambda: kit.then(view.get_text(case="result"), lambda result: (result, rate(result.info, name="X-Rate"))),
        ),
        ("get_text null result value", lambda: api.get_text(case="result")),
        ("get_text value", lambda: api.get_text(case="word")),
        ("get_text 201", lambda: view.get_text(case="created")),
        ("get_text 2XX", lambda: view.get_text(case="partial")),
        ("get_text 4XX", lambda: api.get_text(case="missing")),
        ("get_text default", lambda: once.get_text(case="broken")),
        ("head_text", lambda: view.head_text(case="none")),
        ("get_list None", lambda: api.get_list(case="none")),
        ("get_list value", lambda: api.get_list(case="numbers")),
        ("get_name None", lambda: api.get_name(case="none")),
        ("get_value None", lambda: view.get_value(case="none")),
        ("get_anything None", lambda: view.get_anything(case="none")),
        ("get_anything null result", lambda: api.get_anything(case="result")),
        ("get_pet None", lambda: view.get_pet(case="none")),
        ("get_thing", lambda: api.get_thing(case="thing")),
        ("post_memo None", lambda: api.post_memo(case="none", body=None)),
        ("post_memo value", lambda: api.post_memo(case="memo", body=models.FieldMemosPostRequest(text="a"))),
        ("post_memo refused", lambda: api.post_memo(case="none", body=models.FieldMemosPostRequest.model_construct())),
    ]
