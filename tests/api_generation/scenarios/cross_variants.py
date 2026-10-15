"""Call the form variants server through its client: forms whose schema has read-only and write-only members.

With request and response models, a form's request model leaves the read-only members out, one that is an object
included, so neither target plans them: the client writes the others, and the server reads the others and passes
over a read-only member a request sends anyway.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from datamodel_code_generator import ReadOnlyWriteOnlyModelType

if TYPE_CHECKING:
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "fastapi/server-form-variants.yaml"
CLIENT = {"default_base_url": "https://variants.example.com"}
MODEL = {"read_only_write_only_model_type": ReadOnlyWriteOnlyModelType.RequestResponse}
_SENT = {"id": "5", "origin": '{"country": "jp"}', "name": "raw", "secret": "s"}


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return a service that records the request model it received and answers with the response model."""

    def answer(name: str, arguments: dict[str, object]) -> object:
        record(name, arguments)
        return models.PetResponse(id=1, origin=models.Origin(country="jp"), name="Rex", tags=["a"])

    class Default(server.services.DefaultService):
        def post_form(self, **arguments: object) -> object:
            return answer("post_form", arguments)

        def post_parts(self, **arguments: object) -> object:
            return answer("post_parts", arguments)

    return {"default": Default()}


def calls(kit: Kit) -> Calls:
    """Return each form's calls: through the client, and with the read-only members a client never writes."""
    api, models = kit.api.default, kit.models
    body = models.PetRequest(name="Rex", secret="s", tags=["a", "b c"])
    return [
        ("post_form", lambda: api.post_form(body=body)),
        ("post_parts", lambda: api.post_parts(body=body)),
        *(
            (
                f"{path} with read-only members",
                lambda path=path, sent=sent: kit.then(
                    kit.http.post(f"https://variants.example.com{path}", **sent),
                    lambda response: (response.status_code, response.json()),
                ),
            )
            for path, sent in (
                ("/forms", {"data": _SENT}),
                ("/parts", {"files": {name: (None, value) for name, value in _SENT.items()}}),
            )
        ),
    ]
