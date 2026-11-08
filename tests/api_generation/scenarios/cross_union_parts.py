"""Send and read form-data bodies whose schema is one of several objects, by the parts of their objects.

The value of a union of objects is one of them, whose properties are its parts: the client writes the parts of the
object a call gives, a voice from a sample as a file part beside its name and one from a prompt as text parts, and
settings of one of three closed objects, one of them in a nested union. The server reads the parts back into the
union's model, and refuses a part no object declares since each of them allows no other properties.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from datamodel_code_generator import DataModelType
from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "client/multipart-union-parts.yaml"
BACKENDS = (DataModelType.MsgspecStruct,)


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return a service that takes each body."""
    del models
    return {"default": service(server.services.DefaultService, record)}


def calls(kit: Kit) -> Calls:
    """Create a voice from a sample and from a prompt, update each kind of settings, and send an undeclared part."""
    api = kit.api.default
    bodies = importlib.import_module(f"{kit.client.__name__}.bodies")
    return [
        (
            "create_voice sample",
            lambda: api.create_voice(
                body=bodies.MultipartBody((
                    bodies.FieldPart("name", "calm"),
                    bodies.FilePart("audio_sample", b"\xff\x00", "sample.wav"),
                ))
            ),
        ),
        (
            "create_voice prompt",
            lambda: api.create_voice(
                body=bodies.MultipartBody((
                    bodies.FieldPart("type", kit.models.Type1("prompt")),
                    bodies.FieldPart("name", "calm"),
                    bodies.FieldPart("prompt", "low"),
                ))
            ),
        ),
        ("update_settings email", lambda: api.update_settings(body=kit.models.Notifications(email=True))),
        ("update_settings passcode", lambda: api.update_settings(body=kit.models.Security(passcode="0000"))),
        ("update_settings limit", lambda: api.update_settings(body=kit.models.Sessions(limit=3))),
        (
            "update_settings undeclared part",
            lambda: kit.then(
                kit.http.patch("https://api.example.com/settings", files={"other": (None, "x")}),
                lambda response: response.status_code,
            ),
        ),
    ]
