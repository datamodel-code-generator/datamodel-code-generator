"""Upload raw binary content that a string with a content media type and no content encoding names, as a file.

OpenAPI 3.1 writes raw binary content as such a string, and the client sends it as a file part. The models type it
`str` by default, so the server refuses an image that is not UTF-8 and reads a UTF-8 one as text; a string with a
content encoding is text the client sends as a field.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "client/content-media-type-uploads.yaml"
_PNG = b"\x89PNG\xff\x00"


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return a service whose every method records the body it received."""
    del models
    return {"default": service(server.services.DefaultService, record)}


def calls(kit: Kit) -> Calls:
    """Return uploads of an image that is not UTF-8 and of one that is, each beside base64 text."""
    api, bodies = kit.api.default, kit.module("bodies")
    multipart = bodies.AsyncMultipartBody if kit.asynchronous else bodies.MultipartBody
    return [
        (
            f"upload {label}",
            lambda image=image: api.upload(
                body=multipart((
                    bodies.FilePart("image", image, filename="i.png", content_type="image/png"),
                    bodies.FieldPart("encoded", "aGk="),
                ))
            ),
        )
        for label, image in (("binary image", _PNG), ("UTF-8 image", b"PNG"))
    ]
