"""Call the forms server through its client when reuse makes forms of Pydantic dataclasses inherit an equal one.

A dataclass keeps no type for a form's other properties, so reuse makes forms that type them differently share one
model. Both targets write and read those members by the type of each form's own `additionalProperties`, and each
call reports the members of the model the server received, its extra members included.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from datamodel_code_generator import DataModelType
from tests.api_generation.support.backends import backend_settings
from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "fastapi/server-forms.yaml"
CLIENT = {"default_base_url": "https://forms.example.com"}
MODEL = {**backend_settings(DataModelType.PydanticV2Dataclass), "reuse_model": True}


def services(server: ModuleType, _models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return a service that records the members of each form with extra members it receives."""
    members = {
        name: lambda body, name=name: record(f"{name} members", vars(body))
        for name in ("post_extras", "post_extra_lists", "post_extra_scores")
    }
    return {"default": service(server.services.DefaultService, record, **members)}


def calls(kit: Kit) -> Calls:
    """Return a call for each form whose other properties reuse leaves to their own schema's type."""
    api, models = kit.api.default, kit.models
    blue = models.Filter(color="blue")
    return [
        ("post_extras", lambda: api.post_extras(body=models.ExtrasPostRequest(name="n", first=blue))),
        (
            "post_extra_lists",
            lambda: api.post_extra_lists(body=models.ExtraListsPostRequest(name="n", first=[blue, models.Filter()])),
        ),
        (
            "post_extra_scores",
            lambda: api.post_extra_scores(body=models.ExtraScoresPostRequest(name="n", first=[1, 2], second=[3])),
        ),
        (
            "post_extra_scores refused",
            lambda: kit.then(
                kit.http.post(
                    "https://forms.example.com/extra-scores",
                    content=b"first=1&first=two",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                ),
                lambda response: (
                    response.status_code,
                    [(record["type"], record["loc"]) for record in response.json()["detail"]],
                ),
            ),
        ),
    ]
