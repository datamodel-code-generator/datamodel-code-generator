"""Call the merged nullable references server through its client: a schema rejecting null merged into a nullable one.

With `--strict-nullable --reuse-model --collapse-reuse-models`, `Work` and an inline response schema of the same
shape are merged into the nullable `Home`. The operations that name them reject null as the models do (#5412): the
server answers a bare `None` with a 500 and the client refuses to send `None` as their body, while the operation that
names `Home` takes `None` as its result. A `Person` holding both as fields sends and receives `None` in its home, for
the server and for the client of every backend; the client codecs of its work reject it, which the bindings of the
client case pin.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from datamodel_code_generator import DataModelType
from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "client/merged-nullable-reference.yaml"
MODEL = {"strict_nullable": True, "reuse_model": True, "collapse_reuse_models": True}
BACKENDS = (
    DataModelType.PydanticV2Dataclass,
    DataModelType.DataclassesDataclass,
    DataModelType.TypingTypedDict,
    DataModelType.MsgspecStruct,
)
CLIENT = {"default_base_url": "https://merged.example.com"}


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return a service whose operations answer `none` with a bare `None` and any other case with an address."""

    def answer(case: str, **_: object) -> object:
        return None if case == "none" else models.Home(street=case)

    return {
        "default": service(
            server.services.DefaultService,
            record,
            get_work=answer,
            put_work=lambda **_: None,
            get_office=answer,
            get_home=answer,
            put_person=lambda body, **_: body,
        )
    }


def calls(kit: Kit) -> Calls:
    """Return a call for each operation with an address and with None."""
    api, models = kit.api.default, kit.models
    once = kit.connect(max_retries=0).default
    main = models.Home(street="main")
    return [
        ("get_work value", lambda: api.get_work(case="main")),
        ("get_work None", lambda: once.get_work(case="none")),
        ("get_office None", lambda: once.get_office(case="none")),
        ("get_home value", lambda: api.get_home(case="main")),
        ("get_home None", lambda: api.get_home(case="none")),
        ("put_work value", lambda: api.put_work(case="main", body=main)),
        ("put_work None", lambda: api.put_work(case="none", body=None)),
        ("put_person home None", lambda: api.put_person(case="main", body=models.Person(work=main, home=None))),
    ]
