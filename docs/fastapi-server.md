# FastAPI Server Generation

!!! warning "Experimental"

    FastAPI server generation is experimental. Its options, generated package, template values, and served
    OpenAPI document may change. See [Experimental Features](experimental.md).

`--generate-server fastapi` turns one OpenAPI document into the models and a FastAPI server package in the same run.
The package holds everything the document decides: the routes, a service Protocol that lists the operations you
implement, the code that reads requests and writes responses, authentication, and the OpenAPI document the server
publishes.

You write only the business logic, in modules of your own that the generator never touches. Type checkers and Python
check that your code implements every operation, so when the document changes, you regenerate the package and fix
what they report.

```text
openapi.yaml ──► datamodel-codegen ──► models.py, server/    generated, overwritten on every run
                                            ▲
                                            │ import, subclass the service Protocols
                                       app.py, test_app.py    yours, never touched
```

Coming from fastapi-code-generator? See [Migrating from fastapi-code-generator](fastapi-code-generator-migration.md).

This page covers a tutorial, how-tos for implementing the services, and the reference of the generated package. For
the client of the same document, see [Python Client Generation](python-client.md); for every option with examples,
see [CLI Reference: Target Generation Options](cli-reference/target-generation-options.md).

## Quick start

This tutorial builds a small pet store that lists, creates, and finds pets. Start in an empty directory of a uv
project, with `datamodel-codegen` [installed](getting-started.md#installation). Server generation needs Python 3.11 or
later; the preset below targets Python 3.12.

### 1. Describe the API

Save the document as `openapi.yaml`:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: fastapi-server.tutorial.openapi -->
<details>
<summary>openapi.yaml</summary>

<!-- fmt: off -->

```yaml
openapi: 3.1.0
info:
  title: Pets
  version: "1.0"
servers:
  - url: http://localhost:8000
paths:
  /pets:
    get:
      operationId: listPets
      tags: [pets]
      parameters:
        - name: limit
          in: query
          schema: {type: integer, minimum: 1, maximum: 100, default: 20}
      responses:
        "200":
          description: The pets.
          content:
            application/json:
              schema: {$ref: "#/components/schemas/Pets"}
    post:
      operationId: createPet
      tags: [pets]
      requestBody:
        required: true
        content:
          application/json:
            schema: {$ref: "#/components/schemas/NewPet"}
      responses:
        "201":
          description: Created.
          content:
            application/json:
              schema: {$ref: "#/components/schemas/Pet"}
  /pets/{petId}:
    get:
      operationId: getPet
      tags: [pets]
      parameters:
        - name: petId
          in: path
          required: true
          schema: {type: integer}
      responses:
        "200":
          description: The pet.
          content:
            application/json:
              schema: {$ref: "#/components/schemas/Pet"}
        "404":
          description: No pet has this ID.
          content:
            application/json:
              schema: {$ref: "#/components/schemas/Error"}
components:
  schemas:
    NewPet:
      type: object
      required: [name]
      properties:
        name: {type: string}
        tag: {type: string}
    Pet:
      type: object
      required: [id, name]
      properties:
        id: {type: integer}
        name: {type: string}
        tag: {type: string}
    Pets:
      type: array
      items: {$ref: "#/components/schemas/Pet"}
    Error:
      type: object
      required: [message]
      properties:
        message: {type: string}
```

<!-- fmt: on -->
</details>
<!-- END AUTO-GENERATED DOC EXAMPLE: fastapi-server.tutorial.openapi -->

Each operation has an `operationId`, which names the Python method that implements it, and a tag. Operations that
share their first tag form a *router group*: here the three `pets` operations, which the server registers on one
FastAPI router.

The bodies and responses refer to schemas under `components`, so their models take the names you chose, such as
`Pet` and `NewPet`.

### 2. Generate the server

Put the settings in `pyproject.toml` and run `datamodel-codegen` without arguments, or pass them as options:

=== "pyproject.toml"

    <!-- BEGIN AUTO-GENERATED DOC EXAMPLE: fastapi-server.tutorial.pyproject -->
    <!-- fmt: off -->

    ```toml
    [tool.datamodel-codegen]
    input = "openapi.yaml"
    input-file-type = "openapi"
    openapi-scopes = ["schemas", "api"]
    output-model-type = "pydantic_v2.BaseModel"
    preset = "standard-py312-20260909"
    output = "models.py"
    generate-server = "fastapi"
    server-output = "server"
    server-package = "server"
    server-model-package = "models"
    ```

    <!-- fmt: on -->

    <!-- END AUTO-GENERATED DOC EXAMPLE: fastapi-server.tutorial.pyproject -->

=== "Command line"

    <!-- BEGIN AUTO-GENERATED DOC EXAMPLE: fastapi-server.tutorial.command -->
    <!-- fmt: off -->

    ```bash
    datamodel-codegen \
      --input openapi.yaml \
      --input-file-type openapi \
      --openapi-scopes schemas api \
      --output-model-type pydantic_v2.BaseModel \
      --preset standard-py312-20260909 \
      --output models.py \
      --generate-server fastapi \
      --server-output server \
      --server-package server \
      --server-model-package models
    ```

    <!-- fmt: on -->

    <!-- END AUTO-GENERATED DOC EXAMPLE: fastapi-server.tutorial.command -->

The [preset](presets.md) supplies the model options, as in the model [quick start](getting-started.md), and
`--openapi-scopes schemas api` makes models for the parameters, bodies, and responses of the operations too.

Generation ends by printing the command that adds the package's runtime dependencies to your project:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: fastapi-server.tutorial.notice -->
<!-- fmt: off -->

```text
Add the runtime dependencies of server to your project:
  uv add "fastapi>=0.141.1" "pydantic>=2.14.0" "typing-extensions>=4.16"
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: fastapi-server.tutorial.notice -->

Run it. The command names only the minimum version of each package, so uv adds the latest release. It grows with
what the document uses: `python-multipart` for forms, `email-validator` for `format: email`, and `python-ulid` for
`format: ulid`. An explicit `--target-pydantic-version` older than the `pydantic` minimum (`2`, `2.11` or `2.12`) is
refused with `Error:` and exit code 2; leave it unset or use `2.14`.

The run wrote the models and the `server` package:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: fastapi-server.tutorial.tree -->
<!-- fmt: off -->

```text
models.py
server/
├── README.md
├── __init__.py
├── _generated/
├── _runtime/
├── application.py
├── py.typed
├── routers/
│   ├── __init__.py
│   └── pets.py
└── services.py
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: fastapi-server.tutorial.tree -->

`models.py` holds the models, `server/services.py` the service Protocol you implement next, and `server/routers/`
the routes. The rest is described in [The generated package](#the-generated-package). Every file here is overwritten
on each run, so keep your own code outside `server/`.

### 3. Read the service Protocol

`server/services.py` declares one *service Protocol* for each router group: a `typing.Protocol` class with an
abstract method for each operation.

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: fastapi-server.tutorial.services -->
<!-- fmt: off -->

```python
"""Service interfaces of this package's router groups; regenerate them instead of editing."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol

import models
from fastapi.responses import Response

from ._runtime.server.responses import HTTPResult


class PetsService(Protocol):
    """Implement the pets operations: subclass this Protocol, or give an object its methods."""

    @abstractmethod
    def list_pets(
        self,
        *,
        limit: int,
    ) -> models.Pets | HTTPResult[models.Pets] | Response:
        """
        Handle GET /pets.
        """

    @abstractmethod
    def create_pet(
        self,
        *,
        body: models.NewPet,
    ) -> models.Pet | HTTPResult[models.Pet] | Response:
        """
        Handle POST /pets.
        """

    @abstractmethod
    def get_pet(
        self,
        *,
        pet_id: int,
    ) -> models.Pet | HTTPResult[models.Pet | models.Error] | Response:
        """
        Handle GET /pets/{petId}.
        """
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: fastapi-server.tutorial.services -->

Each method takes the operation's parameters and body as keyword arguments, already validated by the models.
`limit` arrives as `20` when the request omits it, the default its schema declares, and the `petId` path parameter
arrives as `pet_id`, since the preset turns on snake-case field names.

A method returns one of three things:

- the model of its *primary response*, the response a bare return value takes: `200` when the operation declares
  it, or else its lowest success status, such as `201` for `create_pet`;
- an `HTTPResult` for another status or extra headers, such as the `404` that `get_pet` declares;
- a Starlette `Response`, sent as it is.

### 4. Implement the operations

Write the business logic in `app.py`, beside the package:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: fastapi-server.tutorial.app -->
<!-- fmt: off -->

```python
from models import Error, NewPet, Pet, Pets
from server import HTTPResult, create_app
from server.services import PetsService


class PetStore(PetsService):
    def __init__(self) -> None:
        self.pets: dict[int, Pet] = {}

    def list_pets(self, *, limit: int) -> Pets:
        return Pets(list(self.pets.values())[:limit])

    def create_pet(self, *, body: NewPet) -> Pet:
        pet = Pet(id=len(self.pets) + 1, name=body.name, tag=body.tag)
        self.pets[pet.id] = pet
        return pet

    def get_pet(self, *, pet_id: int) -> Pet | HTTPResult[Error]:
        if (pet := self.pets.get(pet_id)) is None:
            return HTTPResult(404, Error(message=f"Pet {pet_id} not found"))
        return pet


app = create_app(pets=PetStore())
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: fastapi-server.tutorial.app -->

`PetStore` subclasses `PetsService`, so type checkers report a missing method or one whose arguments or result do not
match its operation, and Python refuses to create `PetStore()` while a method is missing.

`create_app` takes one service for each router group, under the group's name, and returns a FastAPI application.
`HTTPResult(404, Error(...))` sends the `404` response that `get_pet` declares, validated by its `Error` model.

### 5. Run it

```bash
uv add uvicorn
uv run uvicorn app:app
```

Open <http://127.0.0.1:8000/docs>. The page shows your `openapi.yaml`, not a document FastAPI derives from the
routes: the application serves the parts of the source document its operations use, the *served document*, at
`/openapi.json`.

### 6. Test it

FastAPI's `TestClient` calls the application without a server:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: fastapi-server.tutorial.test -->
<!-- fmt: off -->

```python
from fastapi.testclient import TestClient

from app import app

client = TestClient(app)


def test_create_and_get_pet() -> None:
    created = client.post("/pets", json={"name": "Mimi", "tag": "cat"})
    assert created.status_code == 201
    assert client.get(f"/pets/{created.json()['id']}").json() == created.json()


def test_missing_pet() -> None:
    response = client.get("/pets/99")
    assert response.status_code == 404
    assert response.json() == {"message": "Pet 99 not found"}
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: fastapi-server.tutorial.test -->

```bash
uv add --dev pytest httpx2
uv run pytest test_app.py
```

### Recap

- The OpenAPI document is the source: the models, the routes, and the service Protocols come from it.
- `datamodel-codegen` regenerates every file of `models.py` and `server/` on each run.
- Your code lives outside the package and subclasses the service Protocols, so type checkers keep it in step with
  the document.
- `create_app` builds the application from your services.
- To call the server from Python, generate a client from the same document; see the
  [overview](index.md#generate-a-fastapi-server-or-an-http-client-experimental) and
  [Python Client Generation](python-client.md).

## Implement the services

The how-tos below build on a second project, a photo service, whose upload needs a bearer token:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: fastapi-server.howto.openapi -->
<details>
<summary>openapi.yaml</summary>

<!-- fmt: off -->

```yaml
openapi: 3.1.0
info:
  title: Pet photos
  version: "1.0"
paths:
  /pets/{petId}/photo:
    put:
      operationId: uploadPhoto
      tags: [photos]
      security:
        - bearer: []
      parameters:
        - name: petId
          in: path
          required: true
          schema: {type: integer}
      requestBody:
        required: true
        content:
          multipart/form-data:
            schema: {$ref: "#/components/schemas/PhotoUpload"}
      responses:
        "201":
          description: Stored.
          content:
            application/json:
              schema: {$ref: "#/components/schemas/Photo"}
    get:
      operationId: getPhoto
      tags: [photos]
      parameters:
        - name: petId
          in: path
          required: true
          schema: {type: integer}
      responses:
        "200":
          description: The photo.
          content:
            application/json:
              schema: {$ref: "#/components/schemas/Photo"}
components:
  securitySchemes:
    bearer:
      type: http
      scheme: bearer
  schemas:
    PhotoUpload:
      type: object
      required: [file]
      properties:
        file: {type: string, format: binary}
        caption: {type: string}
    Photo:
      type: object
      required: [petId, size, owner]
      properties:
        petId: {type: integer}
        size: {type: integer}
        owner: {type: string}
        caption: {type: string}
```

<!-- fmt: on -->
</details>
<!-- END AUTO-GENERATED DOC EXAMPLE: fastapi-server.howto.openapi -->

It adds two settings to the quick start's table, explained in [Async methods](#async-methods) and
[The request and per-request dependencies](#the-request-and-per-request-dependencies):

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: fastapi-server.howto.pyproject -->
<!-- fmt: off -->

```toml
[tool.datamodel-codegen]
server-include-request = true
server-handler-modes = '{"/paths/~1pets~1{petId}~1photo/put": "async"}'
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: fastapi-server.howto.pyproject -->

Its service Protocol takes the request, the authenticated principal, and an async upload:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: fastapi-server.howto.services -->
<!-- fmt: off -->

```python
"""Service interfaces of this package's router groups; regenerate them instead of editing."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol

import models
from fastapi import Request
from fastapi.responses import Response
from typing_extensions import TypeVar

from ._runtime.server.responses import HTTPResult

PrincipalT_contra = TypeVar("PrincipalT_contra", contravariant=True)


class PhotosService(Protocol[PrincipalT_contra]):
    """Implement the photos operations: subclass this Protocol, or give an object its methods."""

    @abstractmethod
    async def upload_photo(
        self,
        *,
        request: Request,
        principal: PrincipalT_contra,
        pet_id: int,
        body: models.PhotoUpload,
    ) -> models.Photo | HTTPResult[models.Photo] | Response:
        """
        Handle PUT /pets/{petId}/photo.
        """

    @abstractmethod
    def get_photo(
        self,
        *,
        request: Request,
        pet_id: int,
    ) -> models.Photo | HTTPResult[models.Photo] | Response:
        """
        Handle GET /pets/{petId}/photo.
        """
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: fastapi-server.howto.services -->

### How methods and arguments are named

Methods and router modules are named in snake case, and Protocols in PascalCase, whatever the model naming options
say: a method after its operationId, or after its method and path without one, such as `get_pets_by_pet_id`.

A method's arguments are named after their wire names as model fields are, so the quick start's preset, which turns on
`--snake-case-field` for Pydantic models, names the `petId` path parameter `pet_id`. Without the preset, pass
`--snake-case-field` explicitly for snake_case arguments, or the argument keeps the wire name, `petId`. `--aliases` and
the special-field prefix options apply to arguments too.

A derived name that its namespace already holds takes the model's suffix, such as `id_1` for a query `id` beside a
path `id`, `body_1` for a parameter named `body`, or `pets_1` for a second tag that also becomes `pets`. The model's
`--naming-strategy` rules apply to these names too, with the router group as the parent of a method and the method as
the parent of an argument.

An explicit name of [`--server-operation-names`](cli-reference/target-generation-options.md#server-operation-names),
[`--server-router-names`](cli-reference/target-generation-options.md#server-router-names), or
[`--server-parameter-names`](cli-reference/target-generation-options.md#server-parameter-names) must be new and is
never renamed.

A schema written inline, rather than under `components`, takes a model name derived from where it is: its path, its
method, and its location. The tutorial's inline parameter schemas become these type aliases in `models.py`:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: fastapi-server.tutorial.inline-names -->
<!-- fmt: off -->

```python
type FieldPetsGetQueryLimitParameter = Annotated[int, Field(ge=1, le=100)]


type FieldPetsPetIdGetPathPetIdParameter = int
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: fastapi-server.tutorial.inline-names -->

An inline response schema of `GET /pets` would likewise become `FieldPetsGetResponse`. The method arguments keep their
own names, `limit` and `pet_id`, but a body or response model is a class you import. To name such a model, declare its
schema under `components.schemas` and refer to it with `$ref`, as the tutorial does for `Pet`, `NewPet`, and `Error`.

### Return values and `HTTPResult`

A service method returns the value of its primary response. FastAPI validates and serializes it with the route's
`response_model` when the response is JSON, by alias and leaving out unset fields.

`HTTPResult(status_code, body, headers)`, imported from the generated package (`from server import HTTPResult`), sends
another status or extra headers. For a status the operation declares, exactly, by its range such as `4XX`, or by
`default`, the package validates and serializes the body with that response's model and default media type, as
`get_pet` in the [quick start](#4-implement-the-operations) does.

A status the operation does not declare is sent unchecked: the body is encoded as JSON without validation, and
`HTTPResult(404)` without a body sends an empty `404`.

A Starlette `Response` is sent as it is, for anything else, such as another media type or repeated headers. See
[Responses](#responses) for `null` bodies and empty responses.

### Raise errors

Raise FastAPI's `HTTPException` for an error the document does not describe. FastAPI answers with its own
`{"detail": ...}` body:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: fastapi-server.howto.errors -->
<!-- fmt: off -->

```python
    def get_photo(self, *, request: Request, pet_id: int) -> Photo:
        if (photo := self.store.photos.get((request.state.tenant, pet_id))) is None:
            raise HTTPException(404, f"Pet {pet_id} has no photo")
        return photo
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: fastapi-server.howto.errors -->

For an error the document declares, return an `HTTPResult` instead, so the body follows the declared model. Any other
exception answers an opaque `500`, as in any FastAPI application.

### Async methods

An operation's *handler mode*, `sync` or `async`, says whether its service method is a plain method or a coroutine
function. [`--server-handler-mode async`](cli-reference/target-generation-options.md#server-handler-mode) makes every
method a coroutine function, and
[`--server-handler-modes`](cli-reference/target-generation-options.md#server-handler-modes) sets the mode of single
operations, as the photo service does for its upload:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: fastapi-server.howto.async -->
<!-- fmt: off -->

```python
    async def upload_photo(self, *, request: Request, principal: str, pet_id: int, body: PhotoUpload) -> Photo:
        photo = Photo(pet_id=pet_id, size=len(body.file), owner=principal, caption=body.caption)
        self.store.photos[request.state.tenant, pet_id] = photo
        return photo
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: fastapi-server.howto.async -->

The setting names the operation by its *operation reference*, `/paths/~1pets~1{petId}~1photo/put`; see
[Per-operation settings](#per-operation-settings) for how to spell one. `create_app` and `build_router` refuse a
plain method for an `async` operation and a coroutine function for a `sync` one.

### The request and per-request dependencies

`build_router` takes FastAPI dependencies for every route as `dependencies`, which `create_app` passes to `FastAPI`,
and both take `operation_dependencies` for single operations, keyed by service method name, such as
`{"get_photo": [Depends(audit)]}`. The dependencies run before the method, but what they return never reaches it.

Give a service what it needs in one of two ways. Pass long-lived objects, such as a store or a database client, to
its constructor. For values of the request, generate with
[`--server-include-request`](cli-reference/target-generation-options.md#server-include-request), so that every method
receives the Starlette request as the keyword `request`, and let a dependency put values in `request.state`:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: fastapi-server.howto.dependencies -->
<!-- fmt: off -->

```python
def tenant(request: Request, x_tenant: Annotated[str, Header()] = "public") -> None:
    request.state.tenant = x_tenant


class PhotoStore:
    def __init__(self) -> None:
        self.photos: dict[tuple[str, int], Photo] = {}


class Photos(PhotosService[str]):
    def __init__(self, store: PhotoStore) -> None:
        self.store = store


app = FastAPI()
app.include_router(build_router(photos=Photos(PhotoStore()), authorize=authorize, dependencies=[Depends(tenant)]))
app.add_exception_handler(RequestValidationError, validation_error_handler)
serve_source_openapi(app)
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: fastapi-server.howto.dependencies -->

`tenant` reads a header the document does not declare and stores it for the methods, which keep the photos of each
tenant apart.

### Authenticate requests

The package of a document with secured operations declares one FastAPI security dependency for each scheme, in
`server/security.py`, and `create_app` and `build_router` take an `authorize` callback:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: fastapi-server.howto.authorize -->
<!-- fmt: off -->

```python
USERS = {"secret-token": "alice"}


def authorize(requirement_sets: RequirementSets, credentials: Credentials) -> str:
    bearer = credentials["bearer"]
    if isinstance(bearer, HTTPAuthorizationCredentials) and (user := USERS.get(bearer.credentials)):
        return user
    raise HTTPException(403, "Unknown token")


app = FastAPI()
app.include_router(build_router(photos=Photos(PhotoStore()), authorize=authorize, dependencies=[Depends(tenant)]))
app.add_exception_handler(RequestValidationError, validation_error_handler)
serve_source_openapi(app)
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: fastapi-server.howto.authorize -->

A secured operation calls `authorize` with its requirement sets and the credentials each scheme presented, here an
`HTTPAuthorizationCredentials` under `bearer`. What it returns reaches the method as the keyword `principal`, and an
`HTTPException` it raises rejects the request. A request without credentials answers `401` before `authorize` runs.

The service Protocol of secured operations is generic in the principal type, so `Photos(PhotosService[str])` and an
`authorize` that returns `str` agree. [Security](#security) describes the schemes and the callback types in full.

Test it with headers, as for any FastAPI application:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: fastapi-server.howto.tests -->
<!-- fmt: off -->

```python
def test_upload_and_get_photo() -> None:
    uploaded = client.put("/pets/1/photo", headers=ALICE, files={"file": b"\x89PNG"}, data={"caption": "Rex"})
    assert uploaded.status_code == 201
    assert uploaded.json() == {"petId": 1, "size": 4, "owner": "alice", "caption": "Rex"}
    assert client.get("/pets/1/photo").json() == uploaded.json()


def test_credentials() -> None:
    assert client.put("/pets/1/photo", files={"file": b""}).status_code == 401
    wrong = {"Authorization": "Bearer wrong"}
    assert client.put("/pets/1/photo", headers=wrong, files={"file": b""}).status_code == 403
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: fastapi-server.howto.tests -->

### Accept uploads

A `multipart/form-data` body reaches the method as its model, with each file part read to `bytes`:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: fastapi-server.howto.models -->
<!-- fmt: off -->

```python
class PhotoUpload(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
    )
    file: bytes
    caption: str | None = None
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: fastapi-server.howto.models -->

FastAPI reads forms with `python-multipart`, which the dependency notice adds for a document with forms:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: fastapi-server.howto.notice -->
<!-- fmt: off -->

```text
Add the runtime dependencies of server to your project:
  uv add "fastapi>=0.141.1" "pydantic>=2.14.0" "typing-extensions>=4.16" "python-multipart>=0.0.32"
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: fastapi-server.howto.notice -->

A `format: binary` string is raw bytes, which only a whole body, such as `application/octet-stream`, `image/png` or
`text/csv`, or a file part of a multipart form can carry; a `text/*` body whose schema is such a string travels as its
raw bytes, without a charset decoding or base64, as an `application/octet-stream` one does. Generation stops with an
error that names the operation, the media or parameter, and where the schema declares the string, when a binary string,
at any depth of its schema and through references, `$dynamicRef` and `allOf`, would be read or sent as text: inside a
JSON or URL-encoded body or response, a `text/*` body whose schema is an object or array, a multipart part that is not a
file, an item of a streamed response, or a parameter or header. Pydantic writes `bytes` in JSON as raw UTF-8 text, which
other clients, such as a generated one with dataclass, TypedDict or msgspec models, write as base64, and it cannot write
bytes that are not UTF-8. Declare such a value `format: byte`, which every backend sends as the same base64 text and
Pydantic models hold as a `Base64Str`, or add [`--type-mappings
string+byte=binary`](cli-reference/typing-customization.md#type-mappings) to hold the decoded bytes as a `Base64Bytes`,
which also accepts bytes that are not UTF-8. Build a `Base64Bytes` field in Python from the base64 text, such as
`base64.b64encode(data)`, not from the raw bytes: Pydantic decodes raw bytes leniently, so `b'\xff\x00'` becomes `b''`
without an error.

To stream a large upload or read the body yourself, generate the operation with
[`--server-body-modes`](cli-reference/target-generation-options.md#server-body-modes) set to `request`, or
[`--server-body-mode request`](cli-reference/target-generation-options.md#server-body-mode) for every operation: the
method then receives the raw Starlette request as the keyword `request` instead of a validated body.

### Use your own FastAPI app

`create_app` builds a FastAPI application and passes its other keyword arguments, such as `lifespan` or
`middleware`, to `FastAPI`. To keep an application of your own, include `build_router(...)`, as the photo service does:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: fastapi-server.howto.app -->
<!-- fmt: off -->

```python
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPAuthorizationCredentials

from models import Photo, PhotoUpload
from server import Credentials, RequirementSets, build_router, serve_source_openapi, validation_error_handler
from server.services import PhotosService


app = FastAPI()
app.include_router(build_router(photos=Photos(PhotoStore()), authorize=authorize, dependencies=[Depends(tenant)]))
app.add_exception_handler(RequestValidationError, validation_error_handler)
serve_source_openapi(app)
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: fastapi-server.howto.app -->

`build_router` takes the services, `authorize`, `dependencies`, `operation_dependencies`, and `prefix`. Two things
that `create_app` does are then yours to add:

- `serve_source_openapi(app, prefix=..., metadata=...)` serves the source document instead of FastAPI's own; pass the
  prefix the router is included under and the description settings to replace.
- `app.add_exception_handler(RequestValidationError, validation_error_handler)` keeps the rejected values out of
  validation errors; see [Production settings](#production-settings).

## Regenerate after the document changes

Run the same command again after the OpenAPI document changes. The service Protocols change with the operations, so
type checkers point at the implementations to update, and Python refuses to create an instance of a subclass that
lacks a new method; modules outside the package are never touched.

Generation renders every file first and then *publishes* them: it writes the changed files together and rolls back
earlier changes if a write fails. A file that already holds its generated bytes is not rewritten. Like the models,
every file of the package is written as text with the line ending of the platform, CRLF on Windows and LF elsewhere.

Every generation overwrites every generated file of the package, restoring any you edited or deleted. A file that is
no longer generated, such as the router module of a removed tag, stays until you delete it, and `--check` lists it as
an extra file. A generation that leaves such `.py` files in place names them in one `LeftoverFilesWarning`, which Python
shows once per process for the same text, also under `--watch` and repeated `generate()` calls. Silence exactly this
warning with `warnings.simplefilter("ignore", datamodel_code_generator.LeftoverFilesWarning)` around `generate()`, or
with `ignore::datamodel_code_generator.LeftoverFilesWarning` in pytest's `filterwarnings`.

### Check in CI

`--check` renders everything without writing it and exits with 0 when nothing would change, 1 when a file would
change, such as a generated file you edited or deleted or a file that is no longer generated, and 2 for an error. In
Python, compare the files `generate()` returns without an `output`.

`--check` uses the model output comparison: it prints unified diffs and missing or extra file messages to stdout.
It compares the rendered model and server text, including the README and `py.typed`, plus extra `.py` files under
the model and server output directories. CRLF and LF compare equally. Unrelated text files are outside this scope.
`--check --output-format json` uses the same check payload as model generation. Checking publishes nothing.

The models and the server files keep the generation timestamp unless `--disable-timestamp` or a preset that sets it
leaves it out, as for model generation; one run heads every file with the same timestamp. Without the timestamp, a
run on unchanged inputs reproduces every byte: it writes nothing, and `--check` exits with 0, also from another
directory when the paths name the same input and outputs.

### Compare and watch

`--diff-against OLD_INPUT` renders the models and the server from both inputs without writing anything, and prints
what changes between them with the model input diff: unified diffs labeled `(baseline input)` and `(current input)`,
and files generated only from one of the inputs. It exits with 0 when both render the same files and 1 otherwise;
`--output-format json` emits the model input-diff payload.

`--watch` regenerates the models and the server whenever the input, a document it references, or a file an option
names changes, as for model generation. It needs the `watch` extra, `datamodel-code-generator[watch]`, and has the same
limits: it needs a local `--input` and cannot be combined with `--check` or `--output-format json`.

### JSON output and the lock file

`--output-format json` publishes the files and emits the existing generation payload as one JSON document on
stdout. Its `output` is the configured model output; `files` contains the rendered model and server text. As in
the model payload, a file path is relative to the model output directory, which is `output` for a model directory
and its parent for a single model file, so `output` and the path give the file on disk. A file outside that
directory, such as a server package beside a model directory, has an absolute path. Paths use forward slashes.
`--check --output-format json` names the files the same way in `differences[].path`, while the diff labels stay
relative to the working directory. Model metadata and the remote lock are outside the payload, as for model
generation. The dependency notice goes to stderr, along with errors and Python warnings.

The remote lock file takes part as it does for model generation, when `--lockfile` names it, `--update-lock` or
`--locked` uses it, or the default `datamodel-codegen.lock` exists. As for model generation, it must not overlap the
input or the model output, and it must lie outside the server output directory; a run with such a lock path stops
before it generates anything.

Several targets can share one models module: with the same input, whose file name the model header records, the
same model options, including the same `--openapi-include-paths`, and `--disable-timestamp`, each run writes the same
bytes, and `--check` reports a module that differs.

## Configure generation

### Requirements

- Server generation needs Python 3.11 or later, both to run `datamodel-codegen` and as the target Python version.
  Set the target with `--target-python-version` or a preset that sets it, as the quick start does; the default
  target, 3.10, and running on Python 3.10 are both refused with `Error:` and exit code 2.
- The input is one OpenAPI 3.0, 3.1, or 3.2 document, from a file, a URL, or stdin; a directory or a list of
  files is refused.
- `--output-model-type` is `pydantic_v2.BaseModel` or `pydantic_v2.dataclass`; the other backends are
  refused. Custom templates, base classes, and types do not add backends.
- `pydantic_v2.dataclass` models need `--use-missing-sentinel`, since a dataclass field cannot tell an unset member
  from one set to `None`. Without it, generation stops with `Error: The fastapi target needs --use-missing-sentinel
  for 'pydantic_v2.dataclass' models, whose fields cannot tell an unset member from None` and exit code 2. With it,
  an optional field without a default holds `MISSING`, imported from `pydantic` (`from pydantic import MISSING`): a
  member a request leaves out reaches the method as `MISSING`, and a field the method leaves unset is left out of the
  response.
- `--openapi-scopes` includes `api`, so that the models cover the parameters, bodies, and responses of the
  operations: `--openapi-scopes schemas api`. Without it, generation stops with
  `Error: --generate-server requires --openapi-scopes to include api` and exit code 2, and the Python API raises
  `datamodel_code_generator.Error` with the same message.
- The model options apply as they do without `--generate-server`, so the same options write the same model files
  (only the command `--enable-command-header` records differs), and a model option the model generation refuses,
  such as `--collapse-root-models-name-strategy` without `--collapse-root-models`, is refused before anything is
  written.
- The server files are headed, formatted, and encoded like the model files, by the same model options:
  `--formatters` (including its default and warning), `--custom-formatters` and `--custom-formatters-kwargs`,
  `--custom-file-header`, `--custom-file-header-path` and `--custom-file-header-mode`, `--disable-timestamp`,
  `--enable-version-header`, `--enable-command-header`, `--use-double-quotes`, `--builtin-format-line-length`,
  `--encoding` (for the Python files; the other files are UTF-8), `--use-type-checking-imports`, and the formatter
  settings found from `--output`. The target settings have no output options of their own. As for the models, Ruff
  moves imports only an annotation uses into `TYPE_CHECKING` blocks when `ruff-check` runs with its TC rules
  selected and `--use-type-checking-imports` is given, since the server's Pydantic backends keep them by default. The
  router and application modules keep their imports: they have no postponed annotations, because FastAPI reads them
  at run time. As for the models, `typing.get_type_hints` on a generated class or function whose annotations name a
  moved import then raises `NameError`: pass `--no-use-type-checking-imports`, or do not select the TC rules, to
  introspect the annotations at run time.
- Custom templates and custom formatters must keep the class and field names of the models: the server binds each
  operation to the names in the generated model graph and does not read the rendered model source.
- `--output` names the model file or package, and `--server-model-package` is its import path. The models can
  live inside the server package, and the server package inside a models directory; see
  [The generated package](#the-generated-package).
- The generated package needs `fastapi>=0.141.1` and the other requirements of the printed `uv add` command.

### Server options

Every server setting is a command-line option and a `[tool.datamodel-codegen]` key of the same name, like the
model options, so profiles, `--ignore-pyproject`, `--generate-pyproject-config`, and `--generate-cli-command`
work for them as well. Each option links to its examples in the CLI reference.

| Option | Default | Meaning |
| --- | --- | --- |
| [`--generate-server fastapi`](cli-reference/target-generation-options.md#generate-server) | off | Generate the server package with the models |
| [`--server-output`](cli-reference/target-generation-options.md#server-output) | required | Directory of the generated package |
| [`--server-package`](cli-reference/target-generation-options.md#server-package) | required | Import path of the generated package |
| [`--server-model-package`](cli-reference/target-generation-options.md#server-model-package) | required | Import path of the models `--output` generates |
| [`--server-layout`](cli-reference/target-generation-options.md#server-layout) | `routers` | One router module per group, or `single` for one `routes.py` |
| [`--server-handler-mode`](cli-reference/target-generation-options.md#server-handler-mode) | `sync` | `async` makes service methods coroutine functions |
| [`--server-handler-modes`](cli-reference/target-generation-options.md#server-handler-modes) | none | The handler mode of single operations, over `--server-handler-mode` |
| [`--server-include-request`](cli-reference/target-generation-options.md#server-include-request) | off | Pass the Starlette request to every service method as the keyword `request: Request` |
| [`--server-body-mode`](cli-reference/target-generation-options.md#server-body-mode) | `typed` | `request` passes the raw request, as the keyword `request: Request`, instead of a validated body |
| [`--server-body-modes`](cli-reference/target-generation-options.md#server-body-modes) | none | The body mode of single operations, over `--server-body-mode` |
| [`--server-primary-responses`](cli-reference/target-generation-options.md#server-primary-responses) | inferred | The response a bare return value takes: `status_code` and an optional `media_type` |
| [`--server-operation-names`](cli-reference/target-generation-options.md#server-operation-names) | inferred | Service method names of single operations |
| [`--server-router-names`](cli-reference/target-generation-options.md#server-router-names) | inferred | Group keys, such as `tag:pets` or `default`, to group names: router modules, service arguments, and `<Name>Service` Protocols |
| [`--server-parameter-names`](cli-reference/target-generation-options.md#server-parameter-names) | inferred | Method argument names of single operations, keyed by location and name such as `query:limit` |

On the command line, `--generate-server` requires `--server-output`, `--server-package`, and `--server-model-package`,
and a `--server-*` option requires `--generate-server`; both stop with `Error:` and exit code 2. In Python,
`generate()` needs `server_output` only with an `output`; see [Python API](#python-api). Server keys of
`pyproject.toml` are validated like model keys, but have no effect while no server is selected.
`--generate-server` also requires `--input-file-type openapi`: an input type left to detection, `auto`, or any other
type stops with `Error: --generate-server requires --input-file-type openapi, not auto` (naming the given type) and
exit code 2 before the input is read.

Operations are grouped by their first tag, under the key `tag:<tag>`, and operations without a tag under the key
`default`, which makes the `default` router module, the `DefaultService` Protocol, and the `default=` argument of
`create_app` and `build_router`; the generated client calls the same operations `client.default`. A tag whose name
also derives `default`, such as `default` or `Default`, is another group: on the server and the client alike, the
one declared first keeps `default` and the other takes a number, such as `default_1`, as other derived names do.

With `--server-layout single`, every operation is in the group `all`: one `routes.py`, one `Service` Protocol, and the
argument `service=`. `--server-router-names '{"all": "store"}'` renames it, as other group keys are renamed, to
`StoreService` and `store=`. A group key that matches no group is ignored, while an operation reference that names no
operation stops the run with an error.

`--server-parameter-names` keys an argument by its location, `path`, `query`, `querystring`, `header`, or `cookie`,
and its wire name, such as `query:limit`.

`--server-handler-modes`, `--server-body-modes`, `--server-primary-responses`, `--server-operation-names`,
`--server-router-names`, and `--server-parameter-names` take a JSON object, inline or as the path of a JSON file, as
`--aliases` does. In `pyproject.toml` they are tables, or JSON strings, as the [how-to project](#implement-the-services)
writes `server-handler-modes`; `--generate-pyproject-config` writes them as JSON strings, and `--generate-cli-command`
prints them as JSON, as for the model options that take JSON objects.

A value on the command line takes precedence over the selected profile, which takes precedence over the base table,
and a command-line table replaces the whole table of `pyproject.toml`. An operation's own entry takes precedence over
the global setting, wherever each comes from. Paths in `pyproject.toml`, including the JSON files, are relative to its
directory, and command-line paths are relative to the working directory. The documents of operation references are
relative to the JSON file that holds them, however the file is given, and to a symbolic link's directory when the
file is given through one; without a file, to the `pyproject.toml` directory for its tables, and to the working
directory for inline JSON on the command line.

The package serves every operation under `paths` of the input; a path item under `components.pathItems` that no
path references has no URL and is not served. To leave paths out, pass
[`--openapi-include-paths`](cli-reference/openapi-only-options.md#openapi-include-paths). The models follow the
`api` scope as in a model-only run: inline schemas of the paths left out are not generated, while the schemas and
path items under `components` still are. A per-operation setting that names a path left out fails with
an error before generation.

Callbacks and webhooks of the document appear only in the [served document](#served-openapi-document): the package
generates no routes and no service methods for them.

### Per-operation settings

Per-operation settings are keyed by *operation reference*: the JSON pointer of the path item method in the input
document, or a document and a pointer joined by `#`, such as `pets.yaml#/paths/~1pets/get`.

A JSON pointer joins the keys from the document root with `/`, so a key that itself contains `/` or `~` is escaped:
`~` becomes `~0`, and then `/` becomes `~1`. The path `/pets/{petId}` is the key under `paths`, so the `get` operation
of it is `/paths/~1pets~1{petId}/get`. The README of the generated package lists the reference of every operation.

These settings decide the generated code, including each method name, so they name an operation as the input
document does; `operation_dependencies`, an argument of the generated package, names it by its method.

```toml
[tool.datamodel-codegen.server-handler-modes]
"/paths/~1pets/get" = "async"

[tool.datamodel-codegen.server-body-modes]
"/paths/~1upload/post" = "request"
```

```bash
datamodel-codegen --server-handler-modes '{"/paths/~1pets/get": "async"}'
```

### Python API

`generate()` takes the settings of the [quick start](#2-generate-the-server) as keyword arguments: the server
options under their Python names, with the model options; the choices are enums of `datamodel_code_generator` such as
`ServerType` and `ServerHandlerMode`, whose string values also work, and the JSON options take mappings keyed by the
same operation references:

<!-- BEGIN AUTO-GENERATED FASTAPI PYTHON API -->
```python
from pathlib import Path

from datamodel_code_generator import DataModelType, InputFileType, OpenAPIScope, ServerType, generate

generate(
    Path("openapi.yaml"),
    input_file_type=InputFileType.OpenAPI,
    openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
    output_model_type=DataModelType.PydanticV2BaseModel,
    preset="standard-py312-20260909",
    output=Path("models.py"),
    generate_server=ServerType.FastAPI,
    server_output=Path("server"),
    server_package="server",
    server_model_package="models",
)
```
<!-- END AUTO-GENERATED FASTAPI PYTHON API -->

It publishes the models and the package together and returns `None`, like model generation. Without an `output`, it
needs no `server_output`, returns every model and server file as `GeneratedModules`, under the paths their import paths
imply, such as `("models.py",)` and `("server", "application.py")`, and writes nothing but the model metadata and a
remote lock update; see [Generating a Server or Client Package](using_as_module.md#generating-a-server-or-client-package).

`load_pyproject_config()` reads the server keys of `pyproject.toml`, whose paths and operation documents resolve
against its directory. Invalid settings, bindings, and template failures raise `datamodel_code_generator.Error` with
the message the command line prints. The `uv add` notice is a command-line message only.

### Batch jobs

A [named job](pyproject_toml.md#named-jobs-experimental) whose settings select the server generates it as a single run
with the same settings does, so one `pyproject.toml` can describe model-only jobs and server jobs together. Put
`generate-server` in each server job or in a profile it selects, not in the base table, when other jobs generate
models only.

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: fastapi-server.batch-jobs -->
<!-- fmt: off -->

```toml
[tool.datamodel-codegen]
input-file-type = "openapi"
output-model-type = "pydantic_v2.BaseModel"
openapi-scopes = ["schemas", "api"]
preset = "standard-py312-20260909"
disable-timestamp = true

[tool.datamodel-codegen.profiles.server]
generate-server = "fastapi"
server-package = "app.server"
server-model-package = "app.models"

[tool.datamodel-codegen.jobs.schemas]
input = "openapi.yaml"
output = "client/schemas.py"

[tool.datamodel-codegen.jobs.server]
profile = "server"
input = "openapi.yaml"
output = "app/models.py"
server-output = "app/server"
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: fastapi-server.batch-jobs -->

```bash
datamodel-codegen --all-jobs
datamodel-codegen --job server --server-handler-mode async
datamodel-codegen --all-jobs --check
```

A server option on the command line applies to every selected job and takes precedence over the job table, its
profile, and the base table, as a model option does; every selected job must then select the server.

`server-output`, like `output`, must not overlap the outputs of other jobs or contain the remote lock file. Server
jobs can share their models, though: jobs that each select the server may name the same `output`, which the batch
writes once. They must generate the same models for it, so give them the same input and model settings; a model-only
job cannot share its `output`. The server jobs of a batch write one generation timestamp, unless
`--disable-timestamp` leaves it out, as it does here so that `--check` compares equal.

The batch publishes the server packages together with the models of every job once all jobs succeed, and publishes
nothing when a job fails or when jobs generate different models for a shared `output`; the `uv add` notice of each
package follows the publication on stderr. `--check` compares every job as a single run does and exits with 1 when
any of them would change. With `--output-format json`, a server job adds the generation or check payload of a single
server run to the batch document, with file paths relative to that job's model output directory. `--all-jobs
--watch` regenerates the batch, server jobs included, whenever an input any job read changes.

### The generated package

| Path | Contents |
| --- | --- |
| `__init__.py`, `application.py` | `create_app`, `build_router`, `HTTPResult`, and the other public types |
| `services.py` | One service Protocol per router group, with an abstract method per operation |
| `routers/` or `routes.py` | Route registrations |
| `security.py` | One FastAPI security dependency for each scheme, only when a selected operation uses a security scheme |
| `_generated/`, `_runtime/` | Plans and the runtime the package imports |
| `README.md`, `py.typed` | Documentation and typing marker |

Keep the service implementations, the `authorize` callback, and the application in modules outside the package:
`--check` treats every `.py` file under `--server-output` as generated, lists a file that is no longer generated as
an extra file, and a file at a path the package needs is overwritten.

The models can be part of the package. Name a model file or directory inside `--server-output` and its import path:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: fastapi-server.nested-models.command -->
<!-- fmt: off -->

```bash
datamodel-codegen \
  --input api.yaml \
  --input-file-type openapi \
  --output app/server/models.py \
  --output-model-type pydantic_v2.BaseModel \
  --preset standard-py312-20260909 \
  --openapi-scopes schemas api \
  --generate-server fastapi \
  --server-output app/server \
  --server-package app.server \
  --server-model-package app.server.models
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: fastapi-server.nested-models.command -->

The server package can also sit inside a models directory. Either way `--check` compares the outer directory once,
so it reports each file once. A model file and a server file that would take the same path stop the run with an
error, and so does a module with the name of a generated package directory beside it, such as `server/routers.py`
beside `server/routers/`, which Python would import instead of the module. A job can nest its own `output` and
`server-output` in the same way.

## Reference

### Request handling

The generated models are the runtime contract. Routes declare the model types directly: a JSON body is
`Annotated[Model, Body()]` unless an adapter reads it, as below, a URL-encoded form of a `BaseModel` is a FastAPI form
model, a primary JSON response is `response_model=Model`, and path, query, header, and cookie parameters are
`Annotated` FastAPI parameters of the parameter's model type. A parameter's root model or type alias is unwrapped to
its type when that type alone validates the same, so the method receives the value.

An optional parameter or request body that the request omits arrives as `None`, and a parameter with a default as its
default. The routes declare such an input as FastAPI applications do, `T | None = None`, so FastAPI's own document
shows its schema as `anyOf` of the type and `null`. A default that the parameter's schema declares reaches the method
whenever the request omits the parameter, however the model options spell the type, such as with `--use-annotated` or
`--field-constraints`; a parameter whose type stays a root model receives that model with its own default. How
strictly a value follows the OpenAPI document is decided by the model generation options, not by the server.

A path parameter is required, as OpenAPI requires, even when the document leaves out `required` or declares it
`false`, and a default it declares is not used; the served document keeps the declaration as written. A document with
a path parameter that names no placeholder of its path is refused.

FastAPI cannot read some inputs, so a generated *adapter* reads them and validates the result with the model's
`TypeAdapter`: deepObject, label, matrix, spaceDelimited, and pipeDelimited parameters, parameters with `content`,
cookie arrays, header and path arrays, objects, enums and literals of non-string values, strict integers,
numbers, and booleans (`--strict-types`), repeated path placeholders, request bodies with several media types, text
and binary bodies, multipart forms, and forms of the `pydantic_v2.dataclass` backend. A multipart form body reaches
the method as its model, with uploads read to `bytes`.

FastAPI takes the JSON `null` of a required body for an omitted one, so an adapter also reads a required JSON body
whose model type accepts `null`, such as a `nullable: true` string, a union with `null`, or a schema of any value: the
method receives what the model validates `null` as, `None` or the root model that holds it. `--force-optional` makes
every root model and type alias accept `null`, so every required JSON body of one, such as a string, an array, or a
union, is read this way. A required JSON body whose type rejects `null` stays FastAPI's and answers `null` with a
`missing` record at `["body"]`, and an optional one stays FastAPI's too: its method receives `None` for `null` as for
an omitted body.

A body that an adapter reads needs a Content-Type the operation declares: `application/json` with or without
parameters for a body declared as `application/json`, but not another `+json` type, which FastAPI's own reading
accepts. A request with any other Content-Type, or with a body and none, answers `415`. For a required body, so does
an empty body with an undeclared Content-Type, while an empty body without a Content-Type, or with a declared JSON
one, answers `422` with a `missing` record at `["body"]`. The JSON is read as UTF-8 without a byte order mark: a body
that starts with one, or is UTF-16, answers `422` with `json_invalid`, where FastAPI's own reading accepts both.

A multipart part is text when the type of its member, or of the member's items when that type is an array, has one
scalar kind, with one part for each item of an array. A part of any other member is JSON, as the generated client
sends it: an object, a mapping, each item of an array of objects, a union of a string and an object, an enum of mixed
kinds, or a union of an array and another type, whose array is one part. So is a part of a member whose `encoding`
names one JSON media type and no style, such as `contentType: application/json`, a scalar included. A JSON part is
parsed by `TypeAdapter.validate_json` of the member's type, or of its items' type, whatever media type the part names
and whether it arrives as a field or as a file. A part that is not JSON, or that the type rejects, answers `422` with
its records at `["body", name]`, or at `["body", name, index]` for a member that repeats, before the rest of the body
is validated.

A schema can type the properties it does not declare, with `additionalProperties: {...}`. Their parts come under any
name no declared member has and are read by the same rule, by the type the model keeps their values as, in a field of
its own or one it inherits from a schema combined with `allOf`: text of that type's kind, a list with one part for
each item of an array, or JSON, whose records name the part as a declared member's do. So are their fields in a
URL-encoded form, which an adapter then reads. Without such a schema, a part the schema does not declare is text,
the last of its name, and the model keeps it, ignores it, or refuses it, as its options and
`additionalProperties: false` say. The `pydantic_v2.dataclass` models have no field for other properties, so the
server reads typed ones by the type their schema gives them, the form's own or that of a schema it combines with
`allOf`: a JSON part is checked by that type, text is passed on as it is read, and the model keeps each value as an
attribute beside its fields, which its `repr` leaves out.

A URL-encoded form carries text alone. For a member without one scalar kind, such as an object, an array of objects,
or `type: [integer, boolean]`, declared by the schema or by a schema it combines with `allOf`, generation stops with
`A parameter value needs one unambiguous scalar kind`, as it does for the generated client and for a parameter,
unless an `encoding` gives the member a style. It stops the same way, at the `additionalProperties` that types them,
for other properties of such a type, with either backend. The generated client stops for these members and other
properties with the same message at the same place. It also refuses a URL-encoded body whose schema has
`patternProperties` or is not an object, which the server reads.

The adapters read no `encoding` that names another media type, part headers, `allowReserved`, or a style other than
the default `form` with `explode: true`, and none that gives any style to a member without one scalar kind, which
the client spreads over several pairs or parts: generation stops with an error that names the encoding.
`--server-body-mode request` passes any such body to the method unread.

The router splits a request path at the slashes the request sends, so a percent-encoded slash (`%2F`) stays inside
its path parameter, whose value receives `/`, of whatever style; FastAPI alone routes by the decoded path, where
`%2F` would end the segment. A path without an encoded slash is routed as FastAPI routes it, and so is one that
selects a route you add to the generated router yourself, such as a mount or a Starlette route.

!!! warning
    A path parameter value can therefore contain `/` and `..`: `..%2F..%2Fetc` arrives as `../../etc`. Validate such
    a value, for instance with a `pattern` in the schema, before using it as a file system path or in another URL.

A cookie parameter with a single value is a FastAPI `Cookie`, whatever its style, so the method receives the value
as the request sent it: nothing is percent-decoded (`a%20b` stays `a%20b`), a value in double quotes loses the
quotes, a `;` ends the value, and the last of several cookies of one name is taken. The generated client sends such
values as they are, too, and generation stops with an error for such a cookie whose name is not a token. An adapter
that reads a single-valued cookie, for a strict integer for instance, does not percent-decode it either; cookie arrays
and objects, one cookie for each item or property, keep the `form` style's percent-encoding.

An adapter converts a parameter's text by the schema's type before the model validates it, and accepts fewer
spellings than FastAPI does for a parameter it reads itself: integers and numbers as JSON writes them, so `5`, `-5`,
`2.5`, and `1e3` but not `05`, `+1`, or `1_000`, and booleans only as `true` and `false`. Turning on
`--strict-types int float bool` therefore narrows what those parameters accept. A repeated single value is taken as
FastAPI takes it, by adapters too: the last query value, cookie, form field, and object member, and the first header.

A parameter with JSON `content`, like a JSON part of a multipart form, is parsed by its model's
`TypeAdapter.validate_json`, as a JSON body is, so pydantic's JSON rules apply: a number is read as a float first, so
a `Decimal` field gets the float's value (`0.1000000000000000000000000001` becomes `Decimal("0.1")`) and `NaN` and
`Infinity` reach a `float` field; an object that repeats a member keeps the last value; and text that is not JSON, or
JSON past pydantic's limits such as an integer of thousands of digits or deep nesting, answers `422` with
`json_invalid`. With pydantic older than 2.14, an enum that has a `null` member accepts any JSON value as that member
when it is read from JSON, in bodies, JSON parts, and parameters with JSON `content` that adapters read.

A strict bytes type (`--strict-types bytes` with `format: byte` and `--type-mappings string+byte=binary`) cannot be a
path, query, header, or cookie parameter, since a parameter carries text: generation stops with an error that names it.
A path, query, header, or cookie parameter whose schema mixes scalar kinds keeps every member when one of them is a
string, such as `int | str` for `type: [integer, string]`. Without a string member, such as `type: [integer, boolean]`,
and for an enum whose values mix kinds, such as `enum: [1, a]`, generation stops with
`A parameter value needs one unambiguous scalar kind`.

### Responses

A service method returns the value of its primary response, which FastAPI validates and serializes with the route's
`response_model` when the response is JSON (`by_alias=True`, `exclude_unset=True`). Without a
[`--server-primary-responses`](cli-reference/target-generation-options.md#server-primary-responses) entry, the
primary response is `200` when the operation declares it, or else its lowest success status, or else its lowest
status. A bare value is sent with the `media_type` its entry names, such as `text/plain` of a response that also
declares JSON, and validated and serialized with that media type's model; without a `media_type`, it takes the
response's default media type.

An `HTTPResult(status_code, body, headers)` sends another status or extra headers. The package validates and
serializes its body with the model and default media type of the response that declares the status: exactly, by its
range such as `2XX`, or as `default`. A status that no response declares is sent unchecked, its body encoded as JSON
without validation, and without a body as an empty response. A Starlette `Response` is sent as it is, for anything
else, such as another media type or repeated headers. A response leaves out the fields its model leaves unset, so a
`pydantic_v2.dataclass` sends `null` only for a field set to `None`, and a field holding `MISSING` not at all.

A declared response whose default media type is JSON and whose model type accepts `null`, as for the request bodies
above, sends `None` as the JSON body `null` with that media type: a bare `None` for the primary response, and an
`HTTPResult` without a body or with `None` for an exact status, a range such as `2XX`, or `default`. Under any other
response an `HTTPResult` without a body is an empty response without a `Content-Type`, as it always is for a HEAD
operation and for the statuses `1XX`, `204`, `205`, and `304`.

### Registration

`create_app` and `build_router` look every method up when they register the routes; any object with the right
methods works, and type checkers check it where you pass it. Registration stops with an error for what would
otherwise go wrong without one: a missing method (`AttributeError`), a coroutine function for an operation in the
`sync` handler mode or a plain method for one in the `async` mode, a secured operation without a callable
`authorize`, and an `operation_dependencies` entry that is not a sequence of `Depends(...)` (`TypeError`) or that
names no method of the router (`ValueError`). The package does not check the arguments of a method: one whose
signature a type checker would reject fails with Python's own error when its operation is requested.

A method's docstring names its route, such as `Handle GET /pets/{petId}.`, or, with `--use-schema-description`, the
operation's summary and description, formatted as model docstrings are, on one line with
`--use-single-line-docstring`. The generated `server/README.md` lists the operations of each service and shows how to
connect an `authorize` callback and your own `FastAPI` application.

### Security

`security.py` declares one FastAPI dependency for each security scheme the selected operations use: `APIKeyHeader`,
`APIKeyQuery`, or `APIKeyCookie` for an API key, `HTTPBasic`, `HTTPBearer`, or `HTTPDigest` for an HTTP scheme,
and, for OAuth2 with the declared flows and for OpenID Connect, the runtime's subclasses of FastAPI's `OAuth2` and
`OpenIdConnect`, each with `auto_error=False`. A scheme no FastAPI class reads, such as `mutualTLS`, is a function
that returns `None`; replace it, or any other scheme, with `app.dependency_overrides[security.<scheme>]`. The
credentials are what the dependencies return: a string for an API key, the token of an `Authorization: Bearer`
header for OAuth2 and OpenID Connect, as `OAuth2PasswordBearer` reads it, `HTTPBasicCredentials`, or
`HTTPAuthorizationCredentials`. Each HTTP, OAuth2, and OpenID Connect dependency presents no credential for an
`Authorization` header of another scheme, so a Basic header satisfies no OAuth2 requirement.

`create_app` and `build_router` of a package with secured operations take an
`authorize(requirement_sets, credentials)` callback, which may be a coroutine function. Each secured operation calls
it once with its security requirement sets whose every scheme presented a credential, in declaration order, and the
credentials by scheme name; the handler receives the principal it returns as the keyword argument
`principal: PrincipalT_contra`, and it rejects a request by raising `HTTPException`. A request that presents no
complete requirement set answers `401`, unless the operation also accepts no credentials, in which case the handler
receives `None`, typed `PrincipalT_contra | None`.

A service Protocol with secured operations is generic, `PetsService(Protocol[PrincipalT_contra])`, so
`create_app(pets=..., authorize=...)` ties the principal type of the services to the return type of `authorize`. The
package exports the callback types `Authorize` and `AsyncAuthorize`, `Callable[[RequirementSets, Credentials], P]` and
the same returning `Awaitable[P]`, with `RequirementSets`, a tuple of requirement sets, each a tuple of
`(scheme name, scopes)` pairs, and `Credentials`, `Mapping[str, object]`. FastAPI's document lists each scheme of an
operation as a separate alternative, since FastAPI cannot document a requirement that combines several schemes.

### Served OpenAPI document

`create_app` serves the source document, not one FastAPI builds: generation writes the part of it the selected
paths use into `server/_generated/openapi.py`, and the application reads it on the first request for
`/openapi.json` and keeps it. The document keeps the source's OpenAPI version, `info`, `servers`, `tags`, security
schemes, webhooks, and the selected paths with all their operations as they are declared, callbacks included; the
callbacks and webhooks are documentation only, with no routes or service methods.

Components stay when something kept references them, and so do the `allOf` subtypes of a kept discriminator base.
What a reference names in another loaded document, or in a path that is left out, joins the components of the
kind the reference's position holds, under its own name or a numbered one when the name is taken; an OpenAPI 3.0
path item, or a media type before 3.2, has no component kind and is copied in place. Since the document publishes
what it references, a server generation reads a local file only inside the input document's directory, after `..`
and symbolic links resolve, and none for a document read from a URL: it fails on any other local file reference
before reading the file, with or without `--allow-remote-refs`. References are rewritten
relative to the document, so a schema's `$id` is left out; a reference resolves as the models resolve it,
through `$id`, `$anchor`, and `$dynamicAnchor`. A reference that names nothing the generation loaded stays as it
is, with a warning. A link whose `operationRef` names a path that is left out is left out, and so is a value without
a JSON form: a YAML `!!binary` value, NaN or infinity, or a YAML alias that contains itself. Each of them is reported
as a `DocumentationAnnotationWarning`.

FastAPI's `/docs` and `/redoc` show the document, and behind a root path FastAPI adds that path as the first
server, as it does for its own document. `create_app`'s `prefix` leads every path, and the references and link
`operationRef`s into the paths follow them. The description settings you pass to `create_app`, `title`, `version`,
`summary`, `description`, `terms_of_service`, `contact`, `license_info`, `servers`, `openapi_tags`, and
`openapi_external_docs`, replace the document's, as they do in FastAPI's own; one you leave unset or empty keeps
the document's. The document is documentation only: the generated models are what the server validates with, and
routes added to the application later do not appear in it.

`create_app(..., source_openapi=False)` serves FastAPI's own document instead, built from the routes and the
models they declare. It describes what FastAPI derives: a parameter, header, or body that only an adapter reads
does not appear in it. An application of your own that includes `build_router(...)` serves FastAPI's document
too, unless it calls `serve_source_openapi(app, prefix=..., metadata=...)`, imported from the generated package,
with the prefix the router is included under and the description settings to replace.

A path placeholder that is no ASCII identifier or is a keyword, that repeats, or whose name an argument of the
endpoint already takes, such as `{request}`, is registered under a name of the route's own, such as `{dcg_p0}` for
`{item-id}`, because Starlette matches a placeholder by an identifier. FastAPI's document shows that name for the
placeholder and its parameter, and so do `request.path_params`, `url_path_for`, and the route path in FastAPI's log
messages. The `loc` of a validation error does not: it names the parameter as the source document does.

### Production settings

The application keeps FastAPI's defaults for its documentation endpoints and for the shape of its error
responses, with one difference: a request that fails validation answers `422` with FastAPI's `{"detail": [...]}`
records without their `input` and `ctx`, and without pydantic's `url`, which FastAPI already leaves out: the
response carries each record's `type`, `loc`, and `msg`, so it does not return the rejected value as such. `loc`
and `msg` are still pydantic's and can hold text the client chose: a key of a mapping or an unexpected field in
`loc`, a discriminator tag in `msg`, and whatever a validator of your own puts in its error message. Parameters and
bodies that adapters read answer with the same three keys.

`loc` names what the request carries, not the method argument it becomes: a parameter by its location and its name
in the source document, such as `["path", "item-id"]`, `["query", "page[size]"]`, or `["header", "X-Trace-Id"]`,
and a body member by the name the model reads it under, its alias when it has one. That holds whatever
`--snake-case-field`, `--aliases`, or `--server-parameter-names` call the argument, and for a path placeholder the
route registers under a name of its own: the route puts the source name back into FastAPI's records before any
exception handler receives them, so FastAPI's default handler and a handler of your own see it too.

- `create_app` registers the handler, `validation_error_handler`, through FastAPI's `exception_handlers`. Pass
  your own `exception_handlers` to send other error bodies, for example ones without `loc` and `msg`; an entry
  for `RequestValidationError` replaces the package's.
- An application of your own that includes `build_router(...)` keeps FastAPI's default records, with `input` and
  `ctx`, unless you register the handler: `app.add_exception_handler(RequestValidationError,
  validation_error_handler)`, imported from the generated package.
- Other errors are FastAPI's: `404` and `405` bodies, and an opaque `500` for an exception in a service method or a
  result that fails its response model. That error goes to the server log with the values it rejected. Inputs that
  adapters read also answer `400 {"detail": "Invalid request"}` and `415 {"detail": "Unsupported media type"}`.
- FastAPI's own document (`source_openapi=False`) describes validation errors with FastAPI's `ValidationError`
  schema, in which `loc`, `msg`, and `type` are required and `input` and `ctx` are optional, so these bodies
  conform to it; the source document describes the responses the source declares.
- `create_app(..., docs_url=None, redoc_url=None, openapi_url=None)` removes `/docs`, `/redoc`, and
  `/openapi.json`.
- The `server` response header is the ASGI server's, not the application's; uvicorn leaves it out with
  `--no-server-header`.

### Templates

The server templates come from [`--custom-template-dir`](cli-reference/template-customization.md#custom-template-dir),
as model templates do: the files of its `fastapi` directory replace the builtin roles of the same name:
`facade.jinja2` (the package initializers), `application.jinja2`, `router.jinja2` (the routes module or each router
module), `services.jinja2`, `contract.jinja2` (`_generated/contract.py`), `security.jinja2`, `openapi.jinja2`
(`_generated/openapi.py`), and `readme.jinja2`. A role the directory does not hold,
or a custom template directory without a `fastapi` directory, keeps its builtin template. The builtin templates are
in the `_fastapi/templates` directory of the installed package; copy one to start from it. An override may include
or import the other builtin templates.

!!! warning "Overrides are not checked against the builtin roles"

    A file of the `fastapi` directory that is not one of these role names is ignored without a warning, so a
    misspelled or renamed role keeps its builtin template. A value that an override names and the builtin no longer
    provides, such as one that an older copy of a builtin template uses, prints as an empty string and iterates as
    nothing, as Jinja's default `Undefined` does; the values a role receives can change while server generation is
    experimental. Generation can then succeed and write a package that fails when it is imported. Reading an attribute
    of such a value, or calling it, fails the render instead, which stops the run with an `Error` that names the
    template. After each upgrade, compare your overrides with the
    current builtin templates, and in CI import the generated package, or run `datamodel-codegen --check` and your
    tests.

```text
templates/
├── fastapi/
│   └── services.jinja2
└── pydantic_v2/
    └── BaseModel.jinja2
```

Each role receives the values its builtin template uses and, as custom model templates do, the `#all#` entry of
[`--extra-template-data`](cli-reference/template-customization.md#extra-template-data); the role's own values take
precedence over the extra data:

```json
{"#all#": {"team": "the platform team"}}
```

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: fastapi-server.templates.command -->
<!-- fmt: off -->

```bash
datamodel-codegen \
  --input openapi.yaml \
  --input-file-type openapi \
  --openapi-scopes schemas api \
  --output-model-type pydantic_v2.BaseModel \
  --preset standard-py312-20260909 \
  --output models.py \
  --generate-server fastapi \
  --server-output server \
  --server-package server \
  --server-model-package models \
  --custom-template-dir templates \
  --extra-template-data extra.json
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: fastapi-server.templates.command -->

The templates write every module, class, function, signature, and call; they receive records of names, type
annotations, and the values of the runtime plans. Each role receives these top-level values, where `imports` is the
module's import block:

| Role | Values |
| --- | --- |
| `application.jinja2` | `imports`, `exports`, `final`, `options`, `routes`, `info`, `build_router`, `build`, `services`, `settings`, `create_app`, `arguments`, `fastapi`, `error_handlers`, `serve_source_openapi` |
| `contract.jinja2` | `imports`, `typed_dict`, `dependencies`, `plans`, `final`, `dataclass`, `parameter_adapter`, `body_adapter`, `operation_responses` |
| `facade.jinja2` | `module`, `docstring`, `imports`, `exports` |
| `openapi.jinja2` | `lines` |
| `readme.jinja2` | `title`, `package`, `model_package`, `backend`, `operations`, `services`, `protocols`, `arguments`, `schemes`, `forms`, `raw_request` |
| `router.jinja2` | `imports`, `docstring`, `routes`, `api_router`, `wiring`, `final`, `literal`, `templated`, `builder`, `group`, `build`, `services`, `settings` |
| `security.jinja2` | `imports`, `schemes`, `exports` |
| `services.jinja2` | `imports`, `typevar`, `abstract`, `protocol_type`, `protocols` |

`router.jinja2` receives one `route` for each operation, with its local names (`adder`, `router`, `wiring`,
`service`, `handler`), its `lookup` of the service method, its `principal` dependency or none, its `endpoint` with
the endpoint's `parameters` (`name`, `annotation`, `default`), the service method `call` with its `arguments`, and
its `registration`, whose `keywords` hold the optional `add_api_route` arguments. A builtin template writes each
parameter, argument, and route entry on its own line, and leaves the type annotations and plan values on one line for
the formatter.

A template that does not parse or render stops the run with an `Error` that names the file in the custom
template directory and, when Jinja knows it, the line. Generation also reads the model templates of the same
directory, so the model templates must keep the class and field names, as the [requirements](#requirements) describe.

### Errors and warnings

The command line prints failures to stderr and exits with 2. Most start with `Error: `, followed by the message; an
invalid JSON option prints `Invalid JSON for --server-handler-modes: ...` or, for a value its schema rejects,
`Invalid --server-handler-modes: ...`, naming the option. Target errors combine their messages in order, without
codes, severity or stage labels. Model generation errors keep their messages, including the class-name hint and input
encoding context. Unexpected exceptions print a traceback. Configuration, input, model, binding, planning, and
template errors stop the run before publication.

Warnings are Python `UserWarning` subclasses from `datamodel_code_generator`: `DocumentationAnnotationWarning` reports
a source value the served document cannot carry, and `LeftoverFilesWarning` names the `.py` files under the output
that the generation left in place and does not generate. Each message starts with the output path, spelled relative
to the working directory when it lies inside it, so every generated package reports its own files. They respect
`--disable-warnings` and Python warning filters, and are not returned as diagnostic records.
