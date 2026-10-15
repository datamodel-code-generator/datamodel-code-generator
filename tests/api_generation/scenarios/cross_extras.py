"""Call the extras server through its client: Pydantic dataclasses whose extra members travel both ways.

The client writes each body's extra members as JSON or form members, the server sends back what it received, and
each call reports the members of the value the client decoded, nested dataclasses as their members too.
"""

from __future__ import annotations

from dataclasses import is_dataclass
from typing import TYPE_CHECKING

from datamodel_code_generator import DataModelType
from tests.api_generation.support.backends import backend_settings
from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "fastapi/server-extras.yaml"
CLIENT = {"default_base_url": "https://extras.example.com"}
MODEL = backend_settings(DataModelType.PydanticV2Dataclass)


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return a service that answers each body with itself, and a tree that holds no extra members."""
    return {
        "default": service(
            server.services.DefaultService,
            record,
            echo_stats=lambda body: body,
            echo_bag=lambda body: server.HTTPResult(201, body),
            echo_labels=lambda body: [body],
            get_tree=lambda: models.Tree(name="root", children=[models.Tree(name="leaf")]),
        )
    }


def _members(value: object) -> object:
    """Return a dataclass as the members it holds, its fields and extra members, and a list as its items' members."""
    if isinstance(value, list):
        return [_members(item) for item in value]
    if is_dataclass(value):
        return {name: _members(item) for name, item in vars(value).items()}
    return value


def calls(kit: Kit) -> Calls:
    """Return a call for each operation, with extra members at the top, nested, in lists, and as form members."""
    api, models = kit.api.default, kit.models
    bag = models.Bag(
        stats=models.Stats(a=1, nested={"b": [1, None]}),
        counts={"x": 2},
        named=models.Named(name="n", extra=True),
        namedCounts=models.NamedCounts(name="c", y=3),
        items=[models.Named(name="i", z="w"), models.Named()],
    )
    return [
        ("echo_stats", lambda: kit.then(api.echo_stats(body=models.Stats(a=1, b="x")), _members)),
        ("echo_stats empty", lambda: kit.then(api.echo_stats(body=models.Stats()), _members)),
        ("echo_bag", lambda: kit.then(api.echo_bag(body=bag), _members)),
        ("echo_labels", lambda: kit.then(api.echo_labels(body=models.Labels(name="n", color="red")), _members)),
        ("get_tree", lambda: kit.then(api.get_tree(), _members)),
    ]
