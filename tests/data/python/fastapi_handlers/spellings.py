"""Services of the type spelling server: they record the values the endpoints validate and answer with plain values."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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
    """Return services that record their keywords and answer each pet as a cat or a dog."""
    del models

    class Pets(server.services.PetsService):
        def list_pets(self, *, invalid: bool | None) -> object:
            calls.append(f"list_pets(invalid={invalid!r})")
            return [{"petType": "dog", "bark": True}, {"petType": "dog" if invalid else "cat", "name": "Mimi"}]

        def get_code(self, *, invalid: bool | None) -> object:
            calls.append(f"get_code(invalid={invalid!r})")
            return "a" if invalid else "ab"

        def literal(self, *, modes: object, named: object, listed: object) -> None:
            calls.append(f"literal(modes={modes!r}, named={named!r}, listed={listed!r})")

        def list_counts(self, *, invalid: bool | None) -> object:
            calls.append(f"list_counts(invalid={invalid!r})")
            return [1, -1 if invalid else 2]

        def get_pet(self, **arguments: object) -> object:
            calls.append(f"get_pet({', '.join(f'{key}={_plain(value)!r}' for key, value in arguments.items())})")
            if arguments["pet_id"] == 1:
                return {"petType": "cat", "name": "Mimi"}
            return {"petType": "dog", "bark": True}

    class Fields(server.services.FieldsService):
        def post_field(self, *, body: object) -> object:
            calls.append(f"post_field(body={_plain(body)!r})")
            return {"echo": "x"}

        def post_thing(self, *, body: object) -> object:
            calls.append(f"post_thing(body={body!r})")
            return body

    class Days(server.services.DaysService[object]):
        def post_day(self, *, principal: object, body: object) -> object:
            calls.append(f"post_day(principal={principal!r}, body={body!r})")
            return body

    return {"default": {"pets": Pets(), "fields": Fields(), "days": Days()}}


def settings(server: ModuleType, models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return the authorize callback, which names the schemes that presented a credential."""
    del server, models

    def authorize(requirement_sets: tuple[Any, ...], credentials: dict[str, Any]) -> str:
        calls.append(f"authorize {sorted(credentials)}")
        return "-".join(sorted(credentials)) or "anonymous"

    return {"default": {"authorize": authorize}}
