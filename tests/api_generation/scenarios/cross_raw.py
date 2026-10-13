"""Send and receive whole text bodies of binary strings as raw bytes, through a client of every model backend.

A `text/*` body whose schema is a binary string travels as the raw body, like `application/octet-stream`: the server
writes and reads its bytes as they are, bytes that are not UTF-8 included, and every client returns and sends them
the same way.
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
    """Return a service that answers the report's bytes and echoes a note's bytes."""
    del models
    return {
        "default": service(
            server.services.DefaultService,
            record,
            download_report=lambda: REPORT,
            upload_note=lambda body: body,
        )
    }


def calls(kit: Kit) -> Calls:
    """Download the report, then upload a note that is not UTF-8."""
    api = kit.api.default
    return [
        ("download_report", api.download_report),
        ("upload_note", lambda: api.upload_note(body=b"note \xff\x00 caf\xc3\xa9")),
    ]
