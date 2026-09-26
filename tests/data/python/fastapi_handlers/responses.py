"""Services of the responses server: bare values, HTTP results, and codec values under each default response class."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

if TYPE_CHECKING:
    from types import ModuleType


def services(server: ModuleType, models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return a service that answers with a bare value, an HTTP result, or a codec value, as the mode asks."""
    codecs = server.responses.GetGreetingResponseCodecs.body(status_code=200)
    greeting = models.FieldGreetingsGetResponse
    make = greeting if isinstance(greeting, type) else str

    class Untagged(server.services.UntaggedService):
        def get_greeting(self, *, mode: object) -> object:
            absent = mode is None or type(mode).__name__ == "Unset"
            calls.append(f"get_greeting(mode={'absent' if absent else repr(mode)})")
            match mode:
                case "result":
                    return server.HTTPResult(status_code=200, body=make("ok"))
                case "value":
                    return codecs.from_wire("wire")
                case _:
                    return make("hello")

        def get_pet(self, *, id: int) -> object:  # noqa: A002
            calls.append(f"get_pet(id={id!r})")
            if id == 1:
                return models.Pet(id=1, name="Mimi")
            return server.HTTPResult(status_code=404, body=models.Error(message="Missing"))

    default = {"untagged": Untagged()}
    return {"standard": default, "plain": default, "included": default}


def settings(_server: ModuleType, _models: ModuleType, _calls: list[str]) -> dict[str, dict[str, object]]:
    """Give the plain application a plain-text default response class."""
    return {"plain": {"fastapi_options": {"default_response_class": PlainTextResponse}}}


def applications(server: ModuleType, sets: dict[str, dict[str, object]]) -> dict[str, FastAPI]:
    """Include the router in a user application with a plain-text default, then install the served document."""
    included = FastAPI()
    included.include_router(server.build_router(**sets["included"]), default_response_class=PlainTextResponse)
    server.install_openapi(included)
    return {"included": included}
