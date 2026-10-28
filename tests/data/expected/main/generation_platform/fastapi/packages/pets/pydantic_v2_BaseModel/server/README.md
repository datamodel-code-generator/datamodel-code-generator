# Pets

The `server` package serves the Pets API with FastAPI.
datamodel-code-generator generated it from the OpenAPI document, together with the `models` models of the `pydantic_v2.BaseModel` backend.
Generation overwrites every file of the package, as it does the models: keep your code outside the package, in the `services` modules of [Next steps](#next-steps), and regenerate the package instead of editing it; see [Regenerating](#regenerating).

FastAPI server generation is experimental.
The server supports the `pydantic_v2.BaseModel` and `pydantic_v2.dataclass` model backends, while client generation supports all five.

## Next steps

`main.py` serves the application: it imports your implementation of each router group from the `services` package beside this package, and passes them to `create_app`.
Generation writes each of these modules that is missing, with a stub for each method that raises `NotImplementedError`, and never overwrites or deletes one.

| Module | File | Defines |
| --- | --- | --- |
| `services.pets` | `../services/pets.py` | `class Pets(PetsService)` |
| `services.store` | `../services/store.py` | `class Store(StoreService)` |

The file paths are relative to this README.
Replace each stub with your code, then run the application with an ASGI server, from the directory that holds the top-level package (add `--app-dir src` for a `src` layout):

```console
uv add uvicorn
uv run uvicorn server.main:app --reload
```

`fastapi dev` runs it too: `uv add "fastapi[standard]"`, then `uv run fastapi dev` with the path of `main.py`, beside this README.
To pass other instances, such as fakes in tests, call `create_app` yourself; see [Implementing the services](#implementing-the-services).

### `services.pets`

```python
"""Implement PetsService of server.services; server.main imports Pets from this module.

datamodel-code-generator wrote this module once, with a stub for each method, and never overwrites it. Generating the
server again updates the Protocol, so type checkers point at the methods to change.
"""

from __future__ import annotations

from uuid import UUID

import models
from fastapi.responses import Response
from pydantic import AwareDatetime
from server import HTTPResult
from server.services import PetsService


class Pets(PetsService):
    """Implement the pets operations."""

    def list_pets(
        self,
        *,
        limit: int,
        tags: list[str] | None,
        kind: models.PetsGetQueryKindParameter | None,
        X_Request_Id: UUID | None,
        since: AwareDatetime | None,
        session: str | None,
    ) -> models.PetsGetResponse | HTTPResult[models.PetsGetResponse | models.Error] | Response:
        """
        Handle GET /pets.
        """
        raise NotImplementedError

    def create_pet(
        self,
        *,
        body: models.NewPet,
    ) -> models.Pet | HTTPResult[models.Pet] | Response:
        """
        Handle POST /pets.
        """
        raise NotImplementedError

    def list_my_pets(
        self,
    ) -> models.PetsMineGetResponse | HTTPResult[models.PetsMineGetResponse] | Response:
        """
        Handle GET /pets/mine.
        """
        raise NotImplementedError

    def get_pet(
        self,
        *,
        petId: int,
    ) -> models.Pet | HTTPResult[models.Pet | models.Error] | Response:
        """
        Handle GET /pets/{petId}.
        """
        raise NotImplementedError

    def delete_pet(
        self,
        *,
        petId: int,
    ) -> None | HTTPResult[None] | Response:
        """
        Handle DELETE /pets/{petId}.
        """
        raise NotImplementedError
```

### `services.store`

```python
"""Implement StoreService of server.services; server.main imports Store from this module.

datamodel-code-generator wrote this module once, with a stub for each method, and never overwrites it. Generating the
server again updates the Protocol, so type checkers point at the methods to change.
"""

from __future__ import annotations

import models
from fastapi.responses import Response
from server import HTTPResult
from server.services import StoreService


class Store(StoreService):
    """Implement the store operations."""

    def get_inventory(
        self,
    ) -> models.StoreInventoryGetResponse | HTTPResult[models.StoreInventoryGetResponse] | Response:
        """
        Handle GET /store/inventory.
        """
        raise NotImplementedError
```

## Dependencies

The package needs these distributions at run time:

- `fastapi>=0.141.1`
- `pydantic>=2.14.0`
- `typing-extensions>=4.16`

Add them to your project with the command each generation prints:

```console
uv add "fastapi>=0.141.1" "pydantic>=2.14.0" "typing-extensions>=4.16"
```

Running the application also needs an ASGI server: `uvicorn`, or `fastapi[standard]` for `fastapi dev`, as in [Next steps](#next-steps).

## Operations

| Service | Method | HTTP method | Path | Operation reference |
| --- | --- | --- | --- | --- |
| `PetsService` | `list_pets` | GET | `/pets` | `/paths/~1pets/get` |
| `PetsService` | `create_pet` | POST | `/pets` | `/paths/~1pets/post` |
| `PetsService` | `list_my_pets` | GET | `/pets/mine` | `/paths/~1pets~1mine/get` |
| `PetsService` | `get_pet` | GET | `/pets/{petId}` | `/paths/~1pets~1{petId}/get` |
| `PetsService` | `delete_pet` | DELETE | `/pets/{petId}` | `/paths/~1pets~1{petId}/delete` |
| `StoreService` | `get_inventory` | GET | `/store/inventory` | `/paths/~1store~1inventory/get` |

`operation_dependencies` takes the method names, the keys of `OperationDependencies`; the per-operation generation settings, such as `--server-handler-modes`, take the operation references.

## Implementing the services

`services.py` declares a Protocol for each router group, with an abstract method for each operation of the group.
A method takes the operation's arguments as keywords, with `None` for an optional parameter or body the request omits, and returns the value to send, an `HTTPResult` with another declared status, headers, or both, or a `Response` sent as it is.
`main.py` passes an instance of each group's class to `create_app` under the group's name.
Subclass the Protocol, so that type checkers report a missing or mismatched method and Python refuses to create an instance while a method is missing; any other object with the same methods is checked where you pass it.
The package looks each method up when it registers the routes, and refuses a coroutine function where the operation's handler mode needs a plain method, or the reverse, and a method that cannot take the keyword arguments of its operation; type checkers check their types.

Call `create_app` yourself to pass other instances.
Tests pass fakes, as this pytest test does with FastAPI's `TestClient`, which needs `httpx2`: each fake subclasses your class of its group and overrides the methods the test calls.
An application that builds its services with arguments, such as a database pool or settings, calls `create_app` in a module of its own and runs that module's application instead of `main.py`'s.

```python
from fastapi.testclient import TestClient

from server import create_app
from services.pets import Pets
from services.store import Store


class FakePets(Pets):
    """Override the methods the test calls, such as `list_pets`."""


class FakeStore(Store):
    """Override the methods the test calls, such as `get_inventory`."""


def test_app() -> None:
    with TestClient(create_app(pets=FakePets(), store=FakeStore())) as client:
        ...
```

`create_app` passes its other keyword arguments, such as `lifespan`, `middleware`, or `dependencies`, to `FastAPI`, over the title, version, and other metadata of the source document.
To keep your own `FastAPI` application, include the router:

```python
from fastapi import FastAPI

from server import build_router
from services.pets import Pets
from services.store import Store

app = FastAPI()
app.include_router(build_router(pets=Pets(), store=Store()))
```

## Served OpenAPI document

`create_app` serves the part of the source document the selected operations use, which generation writes into `_generated/openapi.json`; the application reads it on the first request for it.
`_generated/openapi.json` is package data, so a distribution that holds the package must include it: Hatchling, uv_build, poetry-core, pdm-backend, and flit_core include it by default, while setuptools needs it in `package-data` or `MANIFEST.in`.
`create_app(..., source_openapi=False)` serves FastAPI's own document instead, built from the routes and the models they declare, in which an optional parameter or request body is `anyOf` of its type and `null`.
An application of your own that includes `build_router(...)` serves FastAPI's document unless it calls `serve_source_openapi(app)`, imported from `server`.

## Validation

The models are the contract: FastAPI validates parameters, bodies, and responses with the generated model types, so the model generation settings decide how strictly a value follows the OpenAPI document.
Parameter styles, parameter types, and media types that FastAPI cannot read, such as deepObject parameters or cookie arrays, strict integers, numbers, and booleans, text and binary bodies, and multipart forms, pass through generated adapters.
An adapter parses the HTTP syntax, then validates the value with the model's `TypeAdapter`; it answers `400` for malformed syntax, `415` for an undeclared media type, and `422`, with FastAPI's error records, for an invalid value.
A repeated single value is taken as FastAPI takes it, by adapters too: the last query value, cookie, form field, and object member, and the first header.
A cookie parameter with a single value arrives as the request sent it, without percent-decoding.
Adapters accept integers and numbers as JSON writes them and booleans as `true` or `false`, which is fewer spellings than FastAPI accepts for the parameters it reads.

## Production settings

A request that fails validation answers `422` with FastAPI's `{"detail": [...]}` records.
`create_app` keeps the `type`, `loc`, and `msg` of each record and leaves out the `input` and `ctx` FastAPI adds, and pydantic's `url`, so the response does not return the rejected value as such.
`loc` and `msg` are pydantic's and can still hold text the client chose: a mapping key or an unexpected field name, a discriminator tag, or the message of a validator of your own.
An application of your own that includes `build_router(...)` keeps FastAPI's records; register the same handler with `app.add_exception_handler(RequestValidationError, validation_error_handler)`, importing `validation_error_handler` from `server`.
To send other error bodies, pass FastAPI's `exception_handlers` to `create_app`; its entry for `RequestValidationError` replaces the package's.
A result that fails its response model answers `500` without details, and the error, with the values, goes to the server log, as in any FastAPI application.
`create_app(..., docs_url=None, redoc_url=None, openapi_url=None)` removes `/docs`, `/redoc`, and `/openapi.json`.
The `server` response header belongs to the ASGI server, not to the application: uvicorn leaves it out with `--no-server-header`.

## Regenerating

Run the same generation again whenever the OpenAPI document changes; the `--check` option reports the files it would change without writing them.
Generation overwrites every generated file, restoring any you edited or deleted, and never deletes one: delete the modules of removed operations yourself, which `--check` lists as extra files.
When an operation is added, removed, or changed, its method in `services.py` changes with it, so type checkers point at the implementations to update, and Python refuses to create an instance of a subclass that lacks a new method.
Generation writes only the missing modules of `services`: a new router group gets its module with stubs, and the module of a renamed or removed group stays for you to move or delete.
The `services` package belongs to this server alone, as the docstring of its `__init__.py` records: generation refuses another server package beside this one, in a later run or in the same batch, since that package would import its implementations from it too, whatever the docstring says.
Put each server package under a parent package of its own.
A server package renamed or moved within the parent package, or spelled from another import root, takes the package over while no other generated server package is beside it, and generation rewrites the docstring to name it, as `--check` reports and a notice says.
After a take-over, the modules of `services` still import the server package they were written for, as in `from <old package>.services import ...`: update those imports to `server` yourself, since generation never rewrites them.
Each generation prints the `uv add` command that adds the runtime dependencies of the package to your project;
run it again when a new version of datamodel-code-generator changes them.
