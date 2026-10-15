"""Services of the responses server: bare values and HTTP results under each default response class.

Bare values take the types of the chosen primary responses, such as text or bytes of a response that also has JSON.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, Response

if TYPE_CHECKING:
    from types import ModuleType


def _root(model: object, value: object) -> object:
    """Wrap a value in its root model, or keep it for the type alias a dataclass backend declares."""
    return model(value) if isinstance(model, type) else value


def services(server: ModuleType, models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return a service that answers with a bare value or an HTTP result, as the mode asks."""
    greeting = models.GreetingsGetResponse

    class Default(server.services.DefaultService):
        def get_greeting(self, *, mode: object) -> object:
            calls.append(f"get_greeting(mode={'absent' if mode is None else repr(mode)})")
            match mode:
                case "result":
                    return server.HTTPResult(200, _root(greeting, "ok"))
                case _:
                    return _root(greeting, "hello")

        def get_document(self, *, id: int) -> object:  # ruff: ignore[builtin-argument-shadowing]
            calls.append(f"get_document(id={id!r})")
            if id == 1:
                return server.HTTPResult(200, models.Pet(id=1, name="Mimi"))
            return Response(b"raw", media_type="text/plain")

        def get_pet(self, *, id: int) -> object:  # ruff: ignore[builtin-argument-shadowing]
            calls.append(f"get_pet(id={id!r})")
            if id == 1:
                return models.Pet(id=1, name="Mimi")
            return server.HTTPResult(status_code=404, body=models.Error(message="Missing"))

        def create_thing(self, *, body: object) -> object:
            calls.append(f"create_thing(body={body!r})")
            return body

        def get_text(self) -> object:
            calls.append("get_text()")
            return _root(models.TextGetResponse, "hello")

        def head_text(self) -> object:
            calls.append("head_text()")
            return None

        def get_latin(self) -> object:
            calls.append("get_latin()")
            return "caf\u00e9"

        def get_bytes(self) -> object:
            calls.append("get_bytes()")
            return b"\x00\x01"

        def get_problem(self, *, mode: object) -> object:
            calls.append(f"get_problem(mode={'absent' if mode is None else repr(mode)})")
            match mode:
                case "result":
                    return server.HTTPResult(200, models.Pet(id=2, name="Rex"))
                case "invalid":
                    return models.Pet(id=3, name="Tama")
                case _:
                    return models.Error(message="Gone")

        def get_note(self, *, empty: object) -> object:
            calls.append(f"get_note(empty={empty!r})")
            return _root(models.NotesGetResponse, None if empty else "note")

        def get_chosen(self) -> object:
            calls.append("get_chosen()")
            return models.Pet(id=4, name="Pochi")

        def get_tally(self) -> object:
            calls.append("get_tally()")
            return models.Tally(total=2, nested={"a": [1]})

    default = {"default": Default()}
    return {"standard": default, "plain": default, "included": default}


def settings(_server: ModuleType, _models: ModuleType, _calls: list[str]) -> dict[str, dict[str, object]]:
    """Give the plain application a plain-text default response class."""
    return {"plain": {"default_response_class": PlainTextResponse}}


def applications(server: ModuleType, sets: dict[str, dict[str, object]]) -> dict[str, FastAPI]:
    """Include the router in a user application with a plain-text default."""
    included = FastAPI()
    included.include_router(server.build_router(**sets["included"]), default_response_class=PlainTextResponse)
    return {"included": included}
