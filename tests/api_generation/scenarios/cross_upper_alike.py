"""Decode the response headers of two operations whose names upper-case alike through their own decoders.

`straße` and `strasse` both upper-case to `STRASSE`, so the types module claims the constant holding each
operation's header decoders in its scope: the second takes `_STRASSE_HEADERS_1`, and each accessor decodes its own
operation's headers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "cross/upper-alike.yaml"
_SHARP = "straße"


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return a service that answers each operation with its declared header."""
    del models
    answers = {
        _SHARP: lambda: server.HTTPResult(200, "a", headers={"X-A": "7"}),
        "strasse": lambda: server.HTTPResult(200, "b", headers={"X-B": "text"}),
    }
    return {"items": service(server.services.ItemsService, record, **answers)}


def calls(kit: Kit) -> Calls:
    """Return a call of each operation that decodes its declared header with its own accessor."""
    types, api = kit.module("types.items"), kit.api.items.with_response

    def decoded(method: str, header: str) -> Callable[[], object]:
        decode = getattr(types, f"decode_{method}_header")
        return lambda: kit.then(getattr(api, method)(), lambda result: decode(result.info, name=header))

    return [(f"{_SHARP} X-A", decoded(_SHARP, "X-A")), ("strasse X-B", decoded("strasse", "X-B"))]
