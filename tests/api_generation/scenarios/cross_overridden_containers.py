"""Send and receive arrays and maps whose abstract classes `--import-overrides` imports from `typing_extensions`.

Under `--use-generic-container-types` an array is written `Sequence` and a map `Mapping`; an import override only moves
where each is imported from, so both targets still read them as an array and a map: the array parameters keep their
form and simple styles, and the body and result keep their members.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "cross/overridden-containers.yaml"
MODEL = {
    "use_generic_container_types": True,
    "import_overrides": {"Sequence": "typing_extensions", "Mapping": "typing_extensions"},
}


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return a service that answers with a pet of the arguments it receives."""

    def post_pets(**arguments: object) -> object:
        body = arguments["body"]
        return models.Pet(tags=arguments["tags"], counts=getattr(body, "root", body))

    return {"default": service(server.services.DefaultService, record, post_pets=post_pets)}


def calls(kit: Kit) -> Calls:
    """Return a call with an array in the query and a header, and a map body."""
    body = kit.models.PetsPostRequest({"a": 1, "b": 2})
    return [("post_pets", lambda: kit.api.default.post_pets(tags=["x", "y"], X_Ids=[1, 2], body=body))]
