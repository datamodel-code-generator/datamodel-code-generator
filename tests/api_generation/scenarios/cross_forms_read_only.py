"""Call a server whose form-data body explodes a member that writes the name of a read-only property.

The request variant leaves the read-only `id` out, so only the exploded `filter` writes `id`: neither target refuses the
body, and the server reads back the member the client sent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "client/multipart-read-only-exploded.yaml"
MODEL = {"read_only_write_only_model_type": "request-response"}


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return a service whose every method records the body it received."""
    del models
    return {"default": service(server.services.DefaultService, record)}


def calls(kit: Kit) -> Calls:
    """Return a call that sends the exploded member and one that leaves it out."""
    api, models = kit.api.default, kit.models
    return [
        ("create_item", lambda: api.create_item(body=models.ItemRequest(filter=models.Filter(id="f-1")))),
        ("create_item empty", lambda: api.create_item(body=models.ItemRequest())),
    ]
