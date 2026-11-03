"""Call the bound parts server through its client: multipart files by the types the models give the members.

A member of a format a type mapping makes bytes, a nullable binary member and a member that one allOf branch writes as
binary are files, in either branch order; a binary member the models type as an enum is text. A form closed by the
schema its allOf references takes no other part, and a multipart response equal to its request is read by the types
of its members. Requests the client never sends show what the server reads of them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "client/parts-bound-types.yaml"
MODEL = {"type_mappings": ["string+mybin=binary"]}
_PNG = b"\x89PNG\xff\x00"


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return a service whose every method records the body it received."""
    del models
    return {"default": service(server.services.DefaultService, record)}


def _raw(kit: Kit, path: str, files: dict[str, tuple[str | None, bytes]]) -> object:
    """Post form-data parts the client never writes, and return the status with the records' types and locations."""
    return kit.then(
        kit.http.post(f"https://parts.example.com{path}", files=files),
        lambda response: (
            response.status_code,
            [(record["type"], record["loc"]) for record in response.json()["detail"]]
            if response.status_code == 422
            else None,
        ),
    )


def calls(kit: Kit) -> Calls:
    """Return a call for each multipart body, and parts the client never sends."""
    api, models, bodies = kit.api.default, kit.models, kit.module("bodies")
    multipart = bodies.AsyncMultipartBody if kit.asynchronous else bodies.MultipartBody
    return [
        (
            "send_upload",
            lambda: api.send_upload(
                body=multipart((
                    bodies.FilePart("mapped", _PNG, filename="m.png", content_type="image/png"),
                    bodies.FieldPart("stage", models.Stage.draft),
                    bodies.FilePart("doc", b"\x00\xff", filename="d.bin"),
                ))
            ),
        ),
        ("send_merged", lambda: api.send_merged(body=multipart((bodies.FilePart("f", _PNG, filename="f.png"),)))),
        (
            "send_merged_swapped",
            lambda: api.send_merged_swapped(body=multipart((bodies.FilePart("f", _PNG, filename="f.png"),))),
        ),
        ("send_closed", lambda: api.send_closed(body=models.ClosedPostRequest(name="n"))),
        ("send_closed raw", lambda: _raw(kit, "/closed", {"name": (None, b"n"), "extra": (None, b"1")})),
        (
            "attach_files",
            lambda: api.attach_files(
                body=multipart((bodies.FieldPart("note", "n"), bodies.FilePart("file", _PNG, filename="f.png")))
            ),
        ),
        ("send_copy", lambda: api.send_copy(body=multipart((bodies.FilePart("f", _PNG, filename="f.png"),)))),
    ]
