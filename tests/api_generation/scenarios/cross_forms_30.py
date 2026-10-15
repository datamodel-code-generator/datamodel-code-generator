"""Call an OpenAPI 3.0 form encodings server through its client: multipart members whose encodings give a style.

OpenAPI 3.0 applies an encoding's style, explode, and allowReserved to a URL-encoded body alone and ignores them for
multipart/form-data, so both targets write and read such a member as a part without them: JSON for an object, text in
its contentType for a scalar, and a part for each item of an array. Requests the client never sends show that a
delimited part stays one item.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "cross/form-encodings-3-0.yaml"


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return a service whose every method records the body it received."""
    del models
    return {"default": service(server.services.DefaultService, record)}


def calls(kit: Kit) -> Calls:
    """Return calls that send each ignored style, and parts the client never writes."""
    api, models = kit.api.default, kit.models
    parts = models.PartsPostRequest(point=models.Point(x=1, y=2), ids=[1, 2], note="café, b", tags=["a,b", "c"])
    return [
        ("post_parts", lambda: api.post_parts(body=parts)),
        ("post_form", lambda: api.post_form(body=models.FormPostRequest(ids=[1, 2]))),
        (
            "/parts a delimited part",
            lambda: kit.then(
                kit.http.post("https://encodings.example.com/parts", files={"ids": (None, b"1|2")}),
                lambda response: (response.status_code, [record["loc"] for record in response.json()["detail"]]),
            ),
        ),
    ]
