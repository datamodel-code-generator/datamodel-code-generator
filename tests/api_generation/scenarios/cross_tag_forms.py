"""Call a server whose two tags Python reads as one name through its client: each target names the second apart.

Python reads an identifier in NFKC form, where `xά` (with the oxia) is `xά` (with the tonos). Both targets
write the names of the first tag's resource, router and service in that form, so their modules import, and give the
second tag the duplicate suffix. The calls look each resource up by the NFKC spelling, as an attribute access does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "cross/tag-forms.yaml"
_TAG = "xά"


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return a service for each tag, named in NFKC form, that answers with the operation it serves."""
    del models
    return {
        _TAG: service(server.services.XάService, record, get_oxia=lambda: "oxia"),
        f"{_TAG}_1": service(server.services.Xά1Service, record, get_tonos=lambda: "tonos"),
    }


def calls(kit: Kit) -> Calls:
    """Return a call of each tag's operation through the resource of its NFKC name."""
    return [
        ("get_oxia", getattr(kit.api, _TAG).get_oxia),
        ("get_tonos", getattr(kit.api, f"{_TAG}_1").get_tonos),
    ]
