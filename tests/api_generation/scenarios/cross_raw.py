"""Send and receive whole text bodies of binary strings as raw bytes, through a client of every model backend.

A `text/*` body whose schema is a binary string travels as the raw body, like `application/octet-stream`: the server
writes and reads its bytes as they are, bytes that are not UTF-8 included, and every client returns and sends them
the same way. So does a body whose schema reaches the string through wrappers the models type as the string itself: a
reference, one `allOf`, `anyOf` or `oneOf` branch, a constraint, or a null beside it; and one of several such strings,
which the Pydantic backends hold as a union and the others as one `bytes`, msgspec with a warning because it would
check every member against the constraint of one, or a schema whose branches merge into one, which the models type
as `bytes` as well. An `application/octet-stream` body written as
an enum of binary strings, with or without its string type, which the models type as an enum, travels as the enum's
text instead, which the server reads by the model and refuses when it names no member. Bytes the models constrain,
such as to four bytes, are checked by the model wherever they travel: the server refuses a longer request and will
not send a longer response, and a client whose models keep the constraint, the Pydantic and msgspec ones here, refuses
to send or to return longer bytes; the others send them for the server to refuse and return them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.responses import Response

from datamodel_code_generator import DataModelType
from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "fastapi/raw-text.yaml"
CLIENT = {"default_base_url": "https://raw.example.com"}
BACKENDS = (
    DataModelType.PydanticV2Dataclass,
    DataModelType.DataclassesDataclass,
    DataModelType.TypingTypedDict,
    DataModelType.MsgspecStruct,
)
REPORT = b"id,name\n1,caf\xc3\xa9\n2,\xff\x00\n"
LONG = b"far too long"


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return a service that answers the report's bytes, a stamp longer than its model allows, and echoes each body.

    The raw stamp is sent as a Starlette response, which the server passes on unchecked.
    """
    del models
    return {
        "default": service(
            server.services.DefaultService,
            record,
            download_report=lambda: REPORT,
            upload_note=lambda body: body,
            upload_draft=lambda body: body,
            upload_letter=lambda body: body,
            upload_memo=lambda body: body,
            upload_page=lambda body: body,
            upload_sketch=lambda body: body,
            upload_mark=lambda body: body,
            upload_tag=lambda body: body,
            upload_stamp=lambda body: body,
            download_stamp=lambda: LONG,
            download_raw_stamp=lambda: Response(LONG, media_type="application/octet-stream"),
        )
    }


def _final(models: ModuleType, name: str = "Mark") -> object:
    """Return the final mark or tag as the models type it: an enum member, or its text in TypedDict models' literal."""
    mark = getattr(models, name)
    return mark("final") if isinstance(mark, type) else "final"


def _sent(kit: Kit, path: str, content: bytes) -> object:
    """Send bytes as an application/octet-stream body past the client, and return the status and body answered."""
    return kit.then(
        kit.http.post(
            f"https://raw.example.com{path}", content=content, headers={"content-type": "application/octet-stream"}
        ),
        lambda response: (
            response.status_code,
            response.json() if response.headers["content-type"] == "application/json" else response.content,
        ),
    )


def calls(kit: Kit) -> Calls:
    """Download the report, upload a note and each wrapped body that are not UTF-8, a mark as its text, and stamps."""
    api = kit.api.default
    return [
        ("download_report", api.download_report),
        ("upload_note", lambda: api.upload_note(body=b"note \xff\x00 caf\xc3\xa9")),
        ("upload_draft", lambda: api.upload_draft(body=b"draft \xff\x00 caf\xc3\xa9")),
        ("upload_letter", lambda: api.upload_letter(body=b"letter \xff\x00 caf\xc3\xa9")),
        ("upload_memo", lambda: api.upload_memo(body=b"memo \xff\x00")),
        ("upload_page", lambda: api.upload_page(body=b"page \xff\x00")),
        ("upload_sketch", lambda: api.upload_sketch(body=b"sketch \xff\x00")),
        ("upload_mark", lambda: api.upload_mark(body=_final(kit.models))),
        ("upload_mark refused", lambda: _sent(kit, "/marks", b"other")),
        ("upload_tag", lambda: api.upload_tag(body=_final(kit.models, "Tag"))),
        ("upload_tag refused", lambda: _sent(kit, "/tags", b"other")),
        ("upload_stamp", lambda: api.upload_stamp(body=b"\xff\x00ok")),
        ("upload_stamp too long", lambda: api.upload_stamp(body=LONG)),
        ("upload_stamp too long past the client", lambda: _sent(kit, "/stamps", LONG)),
        ("download_stamp too long", api.download_stamp),
        ("download_raw_stamp too long", api.download_raw_stamp),
    ]
