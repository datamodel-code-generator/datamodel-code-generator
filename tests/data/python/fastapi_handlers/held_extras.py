"""Services of the held extras server: they record the objects each endpoint receives, with the members they keep.

A dataclass keeps other members as attributes beside its fields, and a model in its extra members. `AllowBase` and
`AllowDataclassBase` are base classes outside the schema (`--base-class`) whose configuration allows extra members.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict
from pydantic.dataclasses import dataclass

if TYPE_CHECKING:
    from types import ModuleType


class AllowBase(BaseModel):
    model_config = ConfigDict(extra="allow")


@dataclass(config=ConfigDict(extra="allow"))
class AllowDataclassBase:
    pass


def _describe(value: object) -> str:
    if value is None:
        return "None"
    if is_dataclass(value) and not isinstance(value, type):
        fields = asdict(value)
        fields.update((key, item) for key, item in vars(value).items() if key not in fields)
    else:
        fields = value.model_dump()
    return f"{type(value).__name__}({', '.join(f'{key}={item!r}' for key, item in fields.items())})"


def services(server: ModuleType, _models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return a service that records the objects of each operation's parameters and body."""

    class Default(server.services.DefaultService):
        def get_plain(self, *, count: int | None, obj: object) -> None:
            calls.append(f"get_plain(count={count!r}, obj={_describe(obj)})")

        def get_closed(self, *, count: int | None, obj: object) -> None:
            calls.append(f"get_closed(count={count!r}, obj={_describe(obj)})")

        def get_open(self, *, count: int | None, obj: object) -> None:
            calls.append(f"get_open(count={count!r}, obj={_describe(obj)})")

        def get_required(self, *, obj: object) -> None:
            calls.append(f"get_required(obj={_describe(obj)})")

        def get_cookie(self, *, session: str | None, prefs: object) -> None:
            calls.append(f"get_cookie(session={session!r}, prefs={_describe(prefs)})")

        def get_two(self, *, a: object, b: object) -> None:
            calls.append(f"get_two(a={_describe(a)}, b={_describe(b)})")

        def post_plain_parts(self, *, body: object) -> None:
            calls.append(f"post_plain_parts({_describe(body)})")

        def post_closed_parts(self, *, body: object) -> None:
            calls.append(f"post_closed_parts({_describe(body)})")

        def post_open_parts(self, *, body: object) -> None:
            calls.append(f"post_open_parts({_describe(body)})")

        def post_typed(self, *, body: object) -> None:
            calls.append(f"post_typed({_describe(body)})")

        def post_typed_parts(self, *, body: object) -> None:
            calls.append(f"post_typed_parts({_describe(body)})")

        def post_located_parts(self, *, body: object) -> None:
            calls.append(f"post_located_parts({_describe(body)})")

        def send_upload(self, *, body: object) -> None:
            calls.append(f"send_upload({_describe(body)})")

    return {"default": {"default": Default()}}
