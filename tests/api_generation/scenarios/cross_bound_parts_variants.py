"""Call the bound parts server through its client when a read-only property holds raw bytes the request never sends.

The request variant leaves the property out, so neither target refuses the raw binary string below it, and the parts
the client sends are those of the request variant's members.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "client/parts-bound-variants.yaml"
MODEL = {"read_only_write_only_model_type": "request-response"}


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return a service whose every method records the body it received."""
    del models
    return {"default": service(server.services.DefaultService, record)}


def calls(kit: Kit) -> Calls:
    """Return a call that sends the request variant's members as parts."""
    api, models, bodies = kit.api.default, kit.models, kit.module("bodies")
    multipart = bodies.AsyncMultipartBody if kit.asynchronous else bodies.MultipartBody
    return [
        (
            "send_upload",
            lambda: api.send_upload(
                body=multipart((
                    bodies.FilePart("photo", b"\x89PNG\xff\x00", filename="p.png", content_type="image/png"),
                    bodies.FieldPart("note", models.NoteRequest(text="t", secret="s")),
                ))
            ),
        ),
    ]
