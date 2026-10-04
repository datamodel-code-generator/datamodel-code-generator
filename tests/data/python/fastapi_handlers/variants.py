"""Services of the model settings variants: they answer with plain values, whatever the model names."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType


def _plain(value: object) -> object:
    match value:
        case list():
            return [_plain(item) for item in value]
        case _ if hasattr(value, "__dict__") and not isinstance(value, type):
            fields = {key: _plain(item) for key, item in vars(value).items() if not key.startswith("_")}
            return f"{type(value).__name__}{fields}"
    return value


def services(server: ModuleType, models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return a service that records the request models it receives and answers with plain values."""
    del models

    class Untagged(server.services.UntaggedService):
        def create_pet(self, *, body: object) -> object:
            calls.append(f"create_pet(body={_plain(body)})")
            return {"id": 1, "name": "Rex"}

        def get_pet(self, *, id: object) -> object:  # noqa: A002
            calls.append(f"get_pet(id={_plain(id)})")
            return {"id": 1, "name": "Mimi", "owner": {"name": "Ann"}}

        def list_owners(self) -> object:
            calls.append("list_owners()")
            return [{"name": "Ann"}]

    return {"default": {"untagged": Untagged()}}
