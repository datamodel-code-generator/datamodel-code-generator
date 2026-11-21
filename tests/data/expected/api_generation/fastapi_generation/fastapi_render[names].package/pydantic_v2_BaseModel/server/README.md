# Names

The `server` package serves the Names API with FastAPI.
datamodel-code-generator generated it from the OpenAPI document, together with the `models` models of the `pydantic_v2.BaseModel` backend.
Generation overwrites every file of the package, as it does the models: keep your code outside the package, in the `services` modules of [Next steps](#next-steps), and regenerate the package instead of editing it; see [Regenerating](#regenerating).

FastAPI server generation is experimental.
The server supports the `pydantic_v2.BaseModel` and `pydantic_v2.dataclass` model backends, while client generation supports all five.

## Next steps

`main.py` serves the application: it imports your implementation of each router group from the `services` package beside this package, and passes them to `create_app`.
Generation writes each of these modules that is missing, with a stub for each method that raises `NotImplementedError`, and never overwrites or deletes one.

| Module | File | Defines |
| --- | --- | --- |
| `services.users` | `../services/users.py` | `class Users(UsersService)` |
| `services.field_2024_files` | `../services/field_2024_files.py` | `class Field2024Files(Field2024FilesService)` |
| `services.misc` | `../services/misc.py` | `class Misc(MiscService)` |

The file paths are relative to this README.
Replace each stub with your code, then run the application with an ASGI server, from the directory that holds the top-level package (add `--app-dir src` for a `src` layout):

```console
uv add uvicorn
uv run uvicorn server.main:app --reload
```

`fastapi dev` runs it too: `uv add "fastapi[standard]"`, then `uv run fastapi dev` with the path of `main.py`, beside this README.
To pass other instances, such as fakes in tests, call `create_app` yourself; see [Implementing the services](#implementing-the-services).

### `services.users`

```python
"""Implement UsersService of server.services; server.main imports Users from this module.

datamodel-code-generator wrote this module once, with a stub for each method, and never overwrites it. Generating the
server again updates the Protocol, so type checkers point at the methods to change.
"""

from __future__ import annotations

import models
from fastapi.responses import Response
from server import HTTPResult
from server.services import UsersService


class Users(UsersService):
    """Implement the users operations."""

    def get_users_me(
        self,
    ) -> None | HTTPResult[None] | Response:
        """
        Handle GET /users/me.
        """
        raise NotImplementedError

    def get_user_by_id(
        self,
        *,
        id: int,
        id_1: int | None,
        body_1: str | None,
        media_type_1: str | None,
        dcg_p0: str | None,
        parameters: str | None,
        tag: str | None,
        session: str | None,
        get_user_by_id_handler: str | None,
    ) -> None | HTTPResult[None] | Response:
        """
        Handle GET /users/{id}.
        """
        raise NotImplementedError

    def class_(
        self,
        *,
        id: int,
        principal_1: str | None,
        body: models.UsersIdPutRequest,
    ) -> None | HTTPResult[None] | Response:
        """
        Handle PUT /users/{id}.
        """
        raise NotImplementedError
```

### `services.field_2024_files`

```python
"""Implement Field2024FilesService of server.services; server.main imports Field2024Files from this module.

datamodel-code-generator wrote this module once, with a stub for each method, and never overwrites it. Generating the
server again updates the Protocol, so type checkers point at the methods to change.
"""

from __future__ import annotations

from fastapi.responses import Response
from server import HTTPResult
from server.services import Field2024FilesService


class Field2024Files(Field2024FilesService):
    """Implement the field_2024_files operations."""

    def get_file(
        self,
        *,
        file_name: str,
        dcg_p0: str,
        page_size: int | None,
    ) -> None | HTTPResult[None] | Response:
        """
        Handle GET /files/{file.name}/{dcg_p0}.
        """
        raise NotImplementedError
```

### `services.misc`

```python
"""Implement MiscService of server.services; server.main imports Misc from this module.

datamodel-code-generator wrote this module once, with a stub for each method, and never overwrites it. Generating the
server again updates the Protocol, so type checkers point at the methods to change.
"""

from __future__ import annotations

from fastapi.responses import Response
from server import HTTPResult
from server.services import MiscService


class Misc(MiscService):
    """Implement the misc operations."""

    def health_check(
        self,
    ) -> None | HTTPResult[None] | Response:
        """
        Handle GET /health.
        """
        raise NotImplementedError
```

## Dependencies

The package needs these distributions at run time:

- `fastapi>=0.141.1`
- `pydantic>=2.14.0`
- `typing-extensions>=4.16`
- `python-multipart>=0.0.32`

Add them to your project with the command each generation prints:

```console
uv add "fastapi>=0.141.1" "pydantic>=2.14.0" "typing-extensions>=4.16" "python-multipart>=0.0.32"
```

Running the application also needs an ASGI server: `uvicorn`, or `fastapi[standard]` for `fastapi dev`, as in [Next steps](#next-steps).

## Operations

| Service | Method | HTTP method | Path | Operation reference |
| --- | --- | --- | --- | --- |
| `UsersService` | `get_users_me` | GET | `/users/me` | `/paths/~1users~1me/get` |
| `UsersService` | `get_user_by_id` | GET | `/users/{id}` | `/paths/~1users~1{id}/get` |
| `UsersService` | `class_` | PUT | `/users/{id}` | `/paths/~1users~1{id}/put` |
| `Field2024FilesService` | `get_file` | GET | `/files/{file.name}/{dcg_p0}` | `/paths/~1files~1{file.name}~1{dcg_p0}/get` |
| `MiscService` | `health_check` | GET | `/health` | `/paths/~1health/get` |

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
from services.users import Users
from services.field_2024_files import Field2024Files
from services.misc import Misc


class FakeUsers(Users):
    """Override the methods the test calls, such as `get_users_me`."""


class FakeField2024Files(Field2024Files):
    """Override the methods the test calls, such as `get_file`."""


class FakeMisc(Misc):
    """Override the methods the test calls, such as `health_check`."""


def test_app() -> None:
    with TestClient(create_app(users=FakeUsers(), field_2024_files=FakeField2024Files(), misc=FakeMisc())) as client:
        ...
```

`create_app` passes its other keyword arguments, such as `lifespan`, `middleware`, or `dependencies`, to `FastAPI`, over the title, version, and other metadata of the source document.
To keep your own `FastAPI` application, include the router:

```python
from fastapi import FastAPI

from server import build_router
from services.users import Users
from services.field_2024_files import Field2024Files
from services.misc import Misc

app = FastAPI()
app.include_router(build_router(users=Users(), field_2024_files=Field2024Files(), misc=Misc()))
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
URL-encoded form bodies are FastAPI form models, so FastAPI's form extraction applies; set the operation's `body_modes` entry to `request` when the exact submitted values matter.

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
