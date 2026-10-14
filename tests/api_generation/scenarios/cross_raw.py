"""Send and receive whole text bodies of binary strings as raw bytes, through a client of every model backend.

A `text/*` body whose schema is a binary string travels as the raw body, like `application/octet-stream`: the server
writes and reads its bytes as they are, bytes that are not UTF-8 included, and every client returns and sends them
the same way. So does a body whose schema reaches the string through wrappers the models type as the string itself: a
reference, one `allOf`, `anyOf` or `oneOf` branch, a constraint, or a null beside it; and one of several such strings,
which the backends that keep a constraint in the type hold as a union and the others as one `bytes`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return a service that answers the report's bytes and echoes the bytes of every other body."""
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
        )
    }


def calls(kit: Kit) -> Calls:
    """Download the report, then upload a note and each wrapped body that are not UTF-8."""
    api = kit.api.default
    return [
        ("download_report", api.download_report),
        ("upload_note", lambda: api.upload_note(body=b"note \xff\x00 caf\xc3\xa9")),
        ("upload_draft", lambda: api.upload_draft(body=b"draft \xff\x00 caf\xc3\xa9")),
        ("upload_letter", lambda: api.upload_letter(body=b"letter \xff\x00 caf\xc3\xa9")),
        ("upload_memo", lambda: api.upload_memo(body=b"memo \xff\x00")),
        ("upload_page", lambda: api.upload_page(body=b"page \xff\x00")),
    ]
