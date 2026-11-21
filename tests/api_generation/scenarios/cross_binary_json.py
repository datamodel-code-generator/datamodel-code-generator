"""Send and receive whole JSON bodies written as binary strings as the raw octets of their media type.

Raw binary is a whole body, and a whole body written as a binary string is the raw octets of its media type
(OpenAPI 3.1.1 "Working with Binary Data"): the server writes and reads an `application/json` body of such a schema as
the bytes it is, bytes that are no JSON included, and every client returns and sends them the same way, beside an
`image/png` body of the same schema.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.responses import Response

from datamodel_code_generator import DataModelType
from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "client/binary-json-bodies.yaml"
BACKENDS = (DataModelType.MsgspecStruct,)
EXPORT = b'{"kept": "as octets", "byte": "\xff"}'


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return a service that answers the export's octets, as JSON or, asked for, as a PNG, and takes each upload."""
    del models
    return {
        "default": service(
            server.services.DefaultService,
            record,
            get_export=lambda name: EXPORT if name == "json" else Response(b"\x89PNG", media_type="image/png"),
        )
    }


def calls(kit: Kit) -> Calls:
    """Download the export as JSON and as a PNG, and upload octets that are no JSON."""
    api = kit.api.default
    return [
        ("get_export", lambda: api.get_export(name="json")),
        ("get_export png", lambda: api.get_export(name="png", response_media_type="image/png")),
        ("put_export", lambda: api.put_export(name="json", body=b"not json \xff\x00")),
    ]
