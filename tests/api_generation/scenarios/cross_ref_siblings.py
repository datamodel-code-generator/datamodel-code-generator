"""Call a server whose parameter schemas write a `$ref` beside other keywords through its client.

In this OpenAPI 3.1 document the keywords beside a `$ref` are parts of the schema, as the parser reads them: `limit`
keeps the default of `Limit` beside its own description, as `offset` does without one, and `page` takes the default
written beside its `$ref`. Client and server agree on each default, so a call that leaves them out sends them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "cross/ref-siblings.yaml"


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return a service that records the parameters it received."""
    del models
    return {"default": service(server.services.DefaultService, record)}


def calls(kit: Kit) -> Calls:
    """Return a call that leaves every parameter out and one that gives each a value."""
    api = kit.api.default
    return [
        ("list_items defaults", api.list_items),
        ("list_items values", lambda: api.list_items(limit=5, offset=6, page=8)),
    ]
