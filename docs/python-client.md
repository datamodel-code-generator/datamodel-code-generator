# Python Client Generation

!!! warning "Experimental"

    HTTPX2 client generation is experimental. Its options, generated package, and template values may change. See
    [Experimental Features](experimental.md).

`--generate-client httpx2` turns one OpenAPI document into the models and a Python client package in the same run.
The package has a `Client` and an `AsyncClient`, with a *resource* for each tag and a typed method for each
operation, such as `client.pets.get_pet(pet_id=1)`. It sends requests with
[HTTPX2](https://github.com/pydantic/httpx2), the `httpx2` package on PyPI, and handles what the document declares:
servers, parameters, bodies, responses, errors, and authentication.

```text
openapi.yaml ──► datamodel-codegen ──► app/models.py, app/client/    generated, overwritten on every run
                                            ▲
                                            │ import, call client.<resource>.<operation>(...)
                                       your code and tests    yours, never touched
```

This page covers a tutorial, how-tos for calling operations, configuring the client, authentication, errors, and
tests, and the generation settings. Two more pages go further: [Python Client Protocol Helpers](python-client-helpers.md)
for pagination, polling, streams, uploads, caching, and webhooks, and the
[Python Client Reference](python-client-reference.md) for the precise rules. For the server of the same document, see
[FastAPI Server Generation](fastapi-server.md); for every option with examples, see
[CLI Reference: Target Generation Options](cli-reference/target-generation-options.md).

## Quick start

This tutorial generates a client for the pet store of the [server quick start](fastapi-server.md#quick-start) and
calls it. Start in an empty directory of a uv project, with `datamodel-codegen`
[installed](getting-started.md#installation). Client generation needs Python 3.11 or later; the preset below targets
Python 3.12.

### 1. Describe the API

Save the document as `openapi.yaml`. It is the server quick start's document:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.tutorial.openapi -->
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
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.tutorial.openapi -->

The client sends to the document's `servers`, here `http://localhost:8000`. Each operation becomes a method named after
its `operationId`, on the resource of its first tag, `pets`.

### 2. Generate the client

Everything goes into one application package, `app`, as in the [server quick start](fastapi-server.md#2-generate-the-server):
create it with an empty `app/__init__.py`.

```bash
mkdir -p app && touch app/__init__.py
```

Put the settings in `pyproject.toml` and run `datamodel-codegen` without arguments, or pass them as options:

=== "pyproject.toml"

    <!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.tutorial.pyproject -->
    <!-- fmt: off -->

    ```toml
    [tool.datamodel-codegen]
    input = "openapi.yaml"
    input-file-type = "openapi"
    openapi-scopes = ["schemas", "api"]
    output-model-type = "pydantic_v2.BaseModel"
    preset = "standard-py312-20261017"
    output = "app/models.py"
    generate-client = "httpx2"
    client-output = "app/client"
    client-package = "app.client"
    client-model-package = "app.models"
    ```

    <!-- fmt: on -->

    <!-- END AUTO-GENERATED DOC EXAMPLE: python-client.tutorial.pyproject -->

=== "Command line"

    <!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.tutorial.command -->
    <!-- fmt: off -->

    ```bash
    datamodel-codegen \
      --input openapi.yaml \
      --input-file-type openapi \
      --openapi-scopes schemas api \
      --output-model-type pydantic_v2.BaseModel \
      --preset standard-py312-20261017 \
      --output app/models.py \
      --generate-client httpx2 \
      --client-output app/client \
      --client-package app.client \
      --client-model-package app.models
    ```

    <!-- fmt: on -->

    <!-- END AUTO-GENERATED DOC EXAMPLE: python-client.tutorial.command -->

The [preset](presets.md) supplies the model options, as in the model [quick start](getting-started.md), and
`--openapi-scopes schemas api` makes models for the parameters, bodies, and responses of the operations too.

Generation ends by printing the command that adds the package's runtime dependencies to your project, and the next
steps: a call of the first operation in a client's context, and the README of the package:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.tutorial.notice -->
<!-- fmt: off -->

```text
Add the runtime dependencies of app.client to your project:
  uv add "httpx2>=2.13.0" "typing-extensions>=4.16" "pydantic>=2.14.0"
Next steps: call the API with the client:
  from app.client import Client

  with Client(base_url=...) as client:
      client.pets.list_pets(...)
See app/client/README.md.
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.tutorial.notice -->

Run the `uv add` command. `httpx2` is [HTTPX2](https://github.com/pydantic/httpx2), the HTTP library the client sends with; it is not
the `datamodel-code-generator[httpx2]` extra, which only fetches remote schemas while generating. The command names
only the minimum version of each package, so uv adds the latest release, and it grows with what the document uses,
such as `httpx2[ws]` for WebSocket helpers or `cryptography` for public-key webhook signatures.

The run wrote the models and the `app.client` package:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.tutorial.tree -->
<!-- fmt: off -->

```text
app/
├── __init__.py
├── client/
│   ├── README.md
│   ├── __init__.py
│   ├── _generated/
│   ├── _generated_docs/
│   ├── _runtime/
│   ├── errors.py
│   ├── options.py
│   ├── py.typed
│   ├── resources/
│   │   ├── __init__.py
│   │   └── pets/
│   │       └── __init__.py
│   ├── responses.py
│   └── types/
│       ├── __init__.py
│       └── pets/
│           └── __init__.py
└── models.py
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.tutorial.tree -->

`app/client/__init__.py` exports `Client` and `AsyncClient`, `resources/` and `types/` hold a package for each resource,
`types/__init__.py` exports `JSONValue`, the type of an argument that takes any JSON value, and `errors.py`,
`options.py`, and `responses.py` are the public modules you import from. `_generated/` and `_runtime/`
hold the plans and the runtime the package imports. `README.md` shows how to call the package and lists the method of
every operation with its signature, and `_generated_docs/runtime.md` is the runtime reference it links to.
Every file here is overwritten on each run, so keep your own code outside `app/client/`.

### 3. Call the API

Start the server of the [server quick start](fastapi-server.md#5-run-it) with `uv run uvicorn app.server.main:app`, or any server
that implements the document at `http://localhost:8000`. Then save `call.py`:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.tutorial.call -->
<!-- fmt: off -->

```python
from app.client import Client
from app.client.errors import NotFoundError
from app.models import NewPet

with Client() as client:
    pet = client.pets.create_pet(body=NewPet(name="Mimi", tag="cat"))
    print(pet)
    for listed in client.pets.list_pets(limit=10).root:
        print(listed.name)
    try:
        client.pets.get_pet(pet_id=99)
    except NotFoundError as error:
        print(error.status_code, error.body)
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.tutorial.call -->

```bash
uv run python call.py
```

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.tutorial.call-output -->
<!-- fmt: off -->

```text
id=1 name='Mimi' tag='cat'
Mimi
404 message='Pet 99 not found'
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.tutorial.call-output -->

`Client()` sends to the document's server; `with` closes its connections at the end. Each method takes the
operation's parameters as keyword arguments and its body as `body=`, sends the request, and returns the response
decoded into its model, such as `Pet`. A declared error status raises an exception that carries the decoded error
body, such as `NotFoundError` for the `404` of `get_pet`.

The method of `get_pet`, from `app/client/resources/pets/_sync.py`, shows what a call takes:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.tutorial.method -->
<!-- fmt: off -->

```python
    def get_pet(
        self,
        *,
        pet_id: int,
        response_media_type: Literal['application/json'] | None = None,
        options: RequestOptions | None = None,
    ) -> GetPetResponse:
        """
        Call GET /pets/{petId}.
        """
        return self._core.execute(
            _operations.OPERATION_2,
            (pet_id,),
            options=options,
            response_media_type=response_media_type,
        ).data
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.tutorial.method -->

The `petId` path parameter is the argument `pet_id`, since the preset turns on snake-case field names.
`response_media_type` and `options` are on every method: the first picks one of the declared response media types,
and the second takes the [settings of one call](#configure-the-client).

### 4. Use asyncio

`AsyncClient` has the same resources and methods, as coroutines:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.tutorial.async -->
<!-- fmt: off -->

```python
import asyncio

from app.client import AsyncClient
from app.models import NewPet


async def main() -> None:
    async with AsyncClient() as client:
        pet = await client.pets.create_pet(body=NewPet(name="Rex", tag="dog"))
        print(await client.pets.get_pet(pet_id=pet.id))


asyncio.run(main())
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.tutorial.async -->

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.tutorial.async-output -->
<!-- fmt: off -->

```text
id=2 name='Rex' tag='dog'
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.tutorial.async-output -->

Use `async with` to close it, and await each call.

### Recap

- The OpenAPI document is the source: the models, the resources, and the methods come from it.
- `datamodel-codegen` regenerates every file of `app/models.py` and `app/client/` on each run.
- `Client` and `AsyncClient` send with HTTPX2 to the document's servers; each tag is a resource and each operation a
  method with keyword arguments.
- A method returns the decoded model and raises a typed exception for an error status.

## Call operations

The how-tos below use a second document, a pet shelter whose operations need a bearer token:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.howto.openapi -->
<details>
<summary>openapi.yaml</summary>

<!-- fmt: off -->

```yaml
openapi: 3.1.0
info:
  title: Pet shelter
  version: "1.0"
servers:
  - url: https://{region}.shelter.example/v1
    variables:
      region: {default: eu, enum: [eu, us]}
security:
  - bearer: []
  - oauth: [pets.read]
paths:
  /pets:
    get:
      operationId: listPets
      tags: [pets]
      parameters:
        - name: limit
          in: query
          schema: {type: integer, minimum: 1, maximum: 100, default: 20}
        - name: tag
          in: query
          schema: {type: string}
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
  /pets/{petId}/adoption:
    post:
      operationId: adoptPet
      tags: [adoptions]
      parameters:
        - name: petId
          in: path
          required: true
          schema: {type: integer}
      responses:
        "200":
          description: Adopted.
          content:
            application/json:
              schema: {$ref: "#/components/schemas/Pet"}
        "409":
          description: The pet is already adopted.
          content:
            application/json:
              schema: {$ref: "#/components/schemas/Error"}
  /health:
    get:
      operationId: getHealth
      security: []
      responses:
        "200":
          description: The service is up.
          content:
            application/json:
              schema: {$ref: "#/components/schemas/Health"}
components:
  securitySchemes:
    bearer:
      type: http
      scheme: bearer
    oauth:
      type: oauth2
      flows:
        clientCredentials:
          tokenUrl: https://auth.shelter.example/token
          scopes:
            pets.read: Read pets.
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
    Health:
      type: object
      required: [status]
      properties:
        status: {type: string}
    Error:
      type: object
      required: [message]
      properties:
        message: {type: string}
```

<!-- fmt: on -->
</details>
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.howto.openapi -->

Its client adds two settings to the quick start's table, explained in [Request bodies](#request-bodies) and
[Responses](#responses):

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.howto.pyproject -->
<!-- fmt: off -->

```toml
[tool.datamodel-codegen]
client-body-arguments = "both"
client-operations = '{"/paths/~1pets/get": {"runtime": {"request_id_header": "X-Request-Id"}}}'
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.howto.pyproject -->

The shelter is not a real service, so each example passes `http_client=http_client()`, an HTTPX2 client from
`fake.py` whose transport prints every request and answers from memory. Leave out `http_client=` to call a real
server; [Test code that uses the client](#test-code-that-uses-the-client) shows the technique.

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.howto.fake -->
<details>
<summary>fake.py</summary>

<!-- fmt: off -->

```python
"""A stand-in for the shelter API: an HTTPX2 client whose transport prints each request and answers from memory."""

import json

import httpx2

PETS = {"1": {"id": 1, "name": "Mimi", "tag": "cat"}, "2": {"id": 2, "name": "Rex", "tag": "dog"}}
SHOWN = ("authorization", "content-type", "x-app", "x-trace", "x-signature")
health_checks: list[int] = []


def answer(request: httpx2.Request) -> httpx2.Response:
    path = request.url.path.removeprefix("/v1")
    match request.method, path.split("/")[1:]:
        case "POST", ["token"]:
            return httpx2.Response(200, json={"access_token": "issued-1", "token_type": "Bearer", "expires_in": 3600})
        case "GET", ["pets"]:
            return httpx2.Response(200, json=list(PETS.values()), headers={"X-Request-Id": "req-7"})
        case "POST", ["pets"]:
            return httpx2.Response(201, json={"id": 3, **json.loads(request.content)})
        case "GET", ["pets", "1" | "2"]:
            return httpx2.Response(200, json=PETS[path.split("/")[2]])
        case "POST", ["pets", "1", "adoption"]:
            return httpx2.Response(409, json={"message": "Mimi is already adopted"})
        case "GET", ["health"]:
            health_checks.append(len(health_checks))
            return httpx2.Response(503 if len(health_checks) < 3 else 200, json={"status": "up"})
        case _:
            return httpx2.Response(404, json={"message": f"{path} not found"})


def handle(request: httpx2.Request) -> httpx2.Response:
    if request.url.host == "offline.example":
        raise httpx2.ConnectError("connection refused", request=request)
    headers = "".join(f" {name}: {value}" for name, value in request.headers.items() if name in SHOWN)
    print(f"> {request.method} {request.url}{headers} {request.content.decode()}".rstrip())
    return answer(request)


def http_client() -> httpx2.Client:
    return httpx2.Client(transport=httpx2.MockTransport(handle))
```

<!-- fmt: on -->
</details>
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.howto.fake -->

### Arguments and naming

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.howto.calls -->
<!-- fmt: off -->

```python
from app.client import Client
from app.client.options import UNSET
from fake import http_client
from app.models import NewPet

with Client(bearer="s3cret", http_client=http_client()) as client:
    print(client.pets.get_pet(pet_id=1))

    client.pets.list_pets()
    client.pets.list_pets(limit=5, tag="cat")
    client.pets.list_pets(tag=UNSET)

    print(client.pets.create_pet(body=NewPet(name="Kiki")))
    print(client.pets.create_pet(name="Kiki", tag="bird"))
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.howto.calls -->

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.howto.calls-output -->
<!-- fmt: off -->

```text
> GET https://eu.shelter.example/v1/pets/1 authorization: Bearer s3cret
id=1 name='Mimi' tag='cat'
> GET https://eu.shelter.example/v1/pets?limit=20 authorization: Bearer s3cret
> GET https://eu.shelter.example/v1/pets?limit=5&tag=cat authorization: Bearer s3cret
> GET https://eu.shelter.example/v1/pets?limit=20 authorization: Bearer s3cret
> POST https://eu.shelter.example/v1/pets content-type: application/json authorization: Bearer s3cret {"name":"Kiki"}
id=3 name='Kiki' tag=None
> POST https://eu.shelter.example/v1/pets content-type: application/json authorization: Bearer s3cret {"name":"Kiki","tag":"bird"}
id=3 name='Kiki' tag='bird'
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.howto.calls-output -->

Every argument is a keyword. Methods, resource namespaces, and modules are named in snake case, and classes in
PascalCase, whatever the model naming options say: a method after its operationId, or after its method and path
without one, such as `get_pets_by_pet_id` for `GET /pets/{petId}`, and a resource after its operation's first tag.
Operations without a tag join the resource `default`, as `get_health` does here: `client.default.get_health()`.

Arguments are named after their wire names as model fields are, so `--snake-case-field`, which the quick start's
preset turns on, `--aliases`, `--special-field-name-prefix`, `--remove-special-field-name-prefix`, and
`--original-field-name-delimiter` apply to them. Without the preset, a `petId` parameter is the argument `petId` and an
`X-Request-Id` header `X_Request_Id`; pass `--snake-case-field` (`snake-case-field = true`) for snake_case arguments
such as `pet_id`. It renames the model fields too, behind their aliases. A derived name that its namespace already
holds takes the model's suffix, such as `get_pet_1` for a second `get_pet`, `id_1` for a query `id` beside a path `id`,
or `options_1` for a parameter named after a call option. The model's `--naming-strategy` rules apply to these names
too, with the resource as the parent of a method and the method as the parent of an argument, and its
`--duplicate-name-suffix` (the `model` entry, or `default`) tells apart a derived class name, such as an operation's
PascalCase name, as it does a model class; method and argument names keep the number. An explicit name, a `name`,
`parameter_names`, `body_field_names` or `--client-resource-names` entry or an `--aliases` entry, must be a new Python
identifier and is never renamed.

### Request bodies

A body is the argument `body`, the model of its schema, such as `body=NewPet(name="Kiki")`; `media_type` picks one of
the declared request media types when the operation declares several. With
[`--client-body-arguments both`](cli-reference/target-generation-options.md#client-body-arguments), as in this
project, a call may give the fields of an object body as keyword arguments instead, as the last call above does with
`name="Kiki", tag="bird"`; the client builds the model from them and sends only the fields given. A call gives the body
or its fields, never both. See [Body field arguments](python-client-reference.md#body-field-arguments) for which
bodies take fields and how they are named.

A `$ref` beside only annotations, such as `description` or `x-` extensions, takes the type and constraints of the
referenced component, as a bare `$ref` does, for a body and for a parameter. The client checks a plain value the
component's constraints refuse before sending: a call raises a `DecodeError` with the reason `unencodable` instead of
the server answering 422.

A `format: binary` string is raw bytes, which only a whole body, such as `application/octet-stream`, `image/png` or
`text/csv`, or a file part of a multipart form can carry; a `text/*` body whose model types its whole value as one
binary string travels as its raw bytes, without a charset decoding or base64, as an `application/octet-stream` one
does; the method takes such a body or part as `bytes`. The model type decides: the schema is such a string, or reaches
one through a `$ref`, a single `allOf`, `anyOf` or `oneOf` branch, or `allOf` branches that merge into one, such as
`allOf: [{type: string}, {format: binary}]`, also beside a `null`, a length limit or other binary strings. When the models
constrain those bytes, such as `maxLength: 16` as Pydantic's `conbytes(max_length=16)`, the client checks them by the
model: a `bytes` body the model refuses raises a `DecodeError` with the reason `unencodable` before anything is sent,
and a response body it refuses one with the reason `invalid_value`; a file, a path or an iterable is sent as it streams,
for the server to check. Models that keep no such constraint, such as dataclasses and TypedDicts, check nothing, and
unconstrained bytes pass without a model call. An `enum` or a `const` of binary strings, also one that leaves out
`type: string`, is text in the models, so the method takes a body of one, also an `application/octet-stream` one, as
its model, such as an `Enum`, and sends its text. Generation stops with an error when
a binary string, at any depth of its schema and through references, `$dynamicRef` and `allOf`, would be sent as text:
inside a JSON or URL-encoded body or response, also one whose model types its whole value as bytes, a `text/*` body
whose schema is an object, an array, or an `anyOf` or `oneOf` with a branch that is not a binary string, a binary body,
such as `application/octet-stream`, whose model types its whole value as bytes only in part, such as
`type: [string, integer]` or an `anyOf` of a binary string and an integer (`bytes | int`), a multipart part that is
not a file, an item of a streamed response, or a parameter or header. The model backends write `bytes` in JSON differently, Pydantic as raw UTF-8 text and the
others as base64, so such a value would not reach a server, such as a generated FastAPI one, intact. The error names the
operation, the media or parameter, and where the schema declares the string:

```text
Error: /components/schemas/Blob/properties/data: The application/json request body of POST /blobs carries a binary
string as text; declare it with format: byte for base64 text, optionally with --type-mappings string+byte=binary to
keep bytes, or send it as a whole binary body or a file part
```

Declare such a value `format: byte`, which every backend sends as the same base64 text: Pydantic models hold it as a
`Base64Str` and the other backends as the base64 `str`. With
[`--type-mappings string+byte=binary`](cli-reference/typing-customization.md#type-mappings) every backend holds the
decoded `bytes` instead (`Base64Bytes` in Pydantic models) and still sends base64. Build a `Base64Bytes` field in
Python from the base64 text, such as `base64.b64encode(data)`, not from the raw bytes: Pydantic decodes raw bytes
leniently, so `b'\xff\x00'` becomes `b''` without an error. A `--type-mappings` entry that maps a format to `binary`,
such as `password=binary`, makes its strings binary too, and one that maps `binary` to another format makes them
text. A string with an OpenAPI 3.1 `contentMediaType` and no `contentEncoding` is a `str` in JSON and
is sent as it is.

### Optional arguments, `UNSET`, and `None`

An optional parameter with a default its schema declares takes that default, such as `limit: int = 20`, and is
always sent. Any other optional argument defaults to `UNSET`, as `tag: str | UNSET = UNSET` does: the request leaves it
out, and passing `UNSET` is the same as leaving the argument out, as the third `list_pets` call above shows.

`UNSET`, exported from `client.options`, is a `typing_extensions.Sentinel` ([PEP 661](https://peps.python.org/pep-0661/)),
not an `Enum` member: it marks an omitted value, distinct from `None`, and is both the default and its own type, as in
`limit: int | UNSET = UNSET`; test for it with `is UNSET`. A type checker must support PEP 661 sentinels to accept these
annotations: mypy 2.4 or later (mypy 2.3 reports `valid-type`), or Pyright and ty, which the generated packages are
checked with in strict mode.

A parameter style has no spelling of null, so `None` for an optional nullable query, header, or cookie parameter is
sent as `UNSET` is: the parameter is left out, which a server reads as absent. Forms cannot write null either, so a
`None` member of an object parameter, of a URL-encoded or multipart form body, and a `FieldPart` whose value is `None`
are left out too. A parameter declared with `content`
sends JSON `null`. A required parameter, including a path parameter, and an array item refuse `None` with a
`DecodeError` whose reason is `unencodable`. [Model codecs](python-client-reference.md#model-codecs) has the full
conversion rules.

`None` for an optional request body whose schema rejects JSON `null` is sent as `UNSET` is: the request has no body
and no `Content-Type`, which a server reads as an absent body, and a `media_type` beside it is refused with a
`ConfigurationError` whose reason is `without_body`. A JSON body whose type admits `null`, such as a nullable schema,
one without a schema, or `Any`, sends `null`. Generation decides this from the type the models give the body, by the
same rule the generated FastAPI server reads a body and sends a response by, so every model backend sends the same
request: an `x-python-type` annotation admits `null` when it writes `None`, `object`, `Any` under any alias, or a
`Literal` with `None`. The media a call names decides, or the default media without one; an operation with several
media and no default leaves `None` out when none of them takes `null`, and otherwise needs `media_type`. A text, form,
or binary body has no `null`, so its `None` is left out.

### Responses

A method returns the decoded body. Each resource also has three *response views*, the same methods returning more:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.howto.responses -->
<!-- fmt: off -->

```python
from app.client import Client
from fake import http_client

with Client(bearer="s3cret", http_client=http_client()) as client:
    response = client.pets.with_response.list_pets(tag="cat")
    info = response.info
    print(len(response.data.root), info.status_code, info.content_type, info.request_id, info.attempt_count)
    print(info.headers.get("x-request-id"), info.headers)

    raw = client.pets.with_raw_response.get_pet(pet_id=99)
    print(raw.info.status_code, raw.json())

    with client.pets.with_streaming_response.list_pets() as streamed:
        body = b"".join(streamed.iter_bytes())
    print(streamed.info.status_code, len(body))
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.howto.responses -->

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.howto.responses-output -->
<!-- fmt: off -->

```text
> GET https://eu.shelter.example/v1/pets?limit=20&tag=cat authorization: Bearer s3cret
2 200 application/json req-7 1
req-7 HeadersView(<3 headers>)
> GET https://eu.shelter.example/v1/pets/99 authorization: Bearer s3cret
404 {'message': '/pets/99 not found'}
> GET https://eu.shelter.example/v1/pets?limit=20 authorization: Bearer s3cret
200 70
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.howto.responses-output -->

| View | Returns |
|---|---|
| `client.pets.list_pets(...)` | The decoded body, such as `Pets` |
| `client.pets.with_response.list_pets(...)` | A `Response` with the decoded `data` and the `info` of the response |
| `client.pets.with_raw_response.list_pets(...)` | A `RawResponse` with the body read into memory but not decoded; an error status does not raise |
| `client.pets.with_streaming_response.list_pets(...)` | A context manager that sends on entry and yields a `RawResponse` whose body you read as it arrives |

`Response`, `ResponseInfo`, `RawResponse`, `AsyncRawResponse`, and `HeadersView` come from `client.responses`.
`ResponseInfo` describes one completed call:

| Field | Value |
|---|---|
| `status_code` | The final status |
| `headers` | The final response's headers as a `HeadersView`: `get(name)` returns the first value, `get_all(name)` every value, in received order and compared without case; its representation shows no value |
| `content_type` | The response's media type, or `None` |
| `request_id` | The value of the operation's `request_id_header`, here `X-Request-Id`, which a [per-operation setting](#per-operation-settings) declares; otherwise `None` |
| `attempt_count` | How many attempts the call sent, retries included |
| `elapsed` | The seconds the call took, retries and waits included |

A `RawResponse` has `info`, `read()`, `text()`, `json()`, `iter_bytes()`, `iter_raw_bytes()`, `stream_to(path or file)`,
and `raise_for_status()`, which raises the error a typed call would; `AsyncRawResponse` has the same as coroutines and
async iterators. Leave a streaming response's `with` block to release it, also when you stop reading early. The client
and its views also have `request_raw(method, url, ...)`, which sends a request outside the operations and returns a
`RawResponse`.

The text of `ResponseInfo.headers` is what HTTPX2 decodes, with one codec for the whole header set of the response:
ASCII, else UTF-8 when every header is UTF-8, else Latin-1. A declared response header that a typed call reads is ASCII
header text, whatever the codec chose; see [model codecs](python-client-reference.md#model-codecs).

## Configure the client

### Client settings

`Client` and `AsyncClient` take their settings as keywords. `client.with_options(...)` returns a [view](#views) with
some of them replaced, and a method's `options=RequestOptions(...)` replaces them for one call. `RequestOptions`,
`RetryOptions`, `ServerSelection`, `Clock`, and `UNSET` come from `client.options`.

| Keyword | `Client` | `with_options` | `RequestOptions` | Meaning |
|---|---|---|---|---|
| `base_url` | Yes | Yes | Yes | One URL that replaces the document's servers; see [Base URLs and servers](#base-urls-and-servers) |
| `server` | Yes | Yes | Yes | A `ServerSelection` of a declared server and its variables |
| `default_headers`, `default_query` | Yes | Yes | `extra_headers`, `extra_query` | Headers and query fields added to every request; see [Headers and query defaults](#headers-and-query-defaults) |
| `timeout` | Yes | Yes | Yes | The HTTPX2 phase limits; see [Timeouts](#timeouts) |
| `total_timeout` | Yes | Yes | Yes | A budget for the whole call, retries included |
| `max_retries`, `retry` | Yes | Yes | Yes | The retry count and a `RetryOptions`; see [Retries](#retries) |
| `follow_redirects` | Yes | Yes | Yes | Whether HTTPX2 follows redirects; see [Redirects](python-client-reference.md#redirects-and-transport-construction) |
| `auth` | Yes | Yes | Yes | An `httpx2.Auth` that replaces the credentials; see [Authenticate](#authenticate) |
| `idempotency_key` | No | No | Yes | The key of one call; see [idempotency keys](python-client-reference.md#supply-and-retain-an-idempotency-key) |
| Credentials, such as `bearer` | Yes | No | No | One keyword per security scheme; see [Authenticate](#authenticate) |
| `clock` | Yes | No | No | The time, jitter, and wait sources; see [Clocks](python-client-reference.md#clocks-and-retry-jitter) |
| `http_client` | Yes | No | No | An HTTPX2 client of your own; see [Use your own HTTPX2 client](#use-your-own-httpx2-client) |
| `compression` | Yes | Yes | Yes | Only in a package with an operation that accepts gzip; see [Request compression](python-client-reference.md#request-compression) |
| `helper_defaults`, `cache_stores`, `allowed_origins` | Yes | No | No | Only in a package with protocol helpers; see [Helper settings](python-client-helpers.md#helper-settings-on-the-client) |

A setting a call leaves unset inherits from the nearest view, then the client. The example below sets several of them:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.howto.options -->
<!-- fmt: off -->

```python
import httpx2

from app.client import Client
from app.client.options import RequestOptions, ServerSelection
from fake import http_client

with Client(
    bearer="s3cret",
    server=ServerSelection(variables={"region": "us"}),
    default_headers={"X-App": "shelter-cli"},
    timeout=httpx2.Timeout(10, connect=2),
    http_client=http_client(),
) as client:
    client.pets.list_pets()

    traced = client.with_options(default_headers={"X-Trace": "on"}, total_timeout=30)
    traced.pets.list_pets()

    staging = RequestOptions(base_url="https://staging.shelter.example/v1", extra_headers={"X-App": None})
    client.pets.list_pets(options=staging)
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.howto.options -->

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.howto.options-output -->
<!-- fmt: off -->

```text
> GET https://us.shelter.example/v1/pets?limit=20 x-app: shelter-cli authorization: Bearer s3cret
> GET https://us.shelter.example/v1/pets?limit=20 x-app: shelter-cli x-trace: on authorization: Bearer s3cret
> GET https://staging.shelter.example/v1/pets?limit=20 authorization: Bearer s3cret
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.howto.options-output -->

### Base URLs and servers

Each operation sends to its effective OpenAPI `servers`, those of the operation, its path item, or the document. By
default a call uses the first of them with the default of every server variable. A relative server URL resolves
against `--client-server-base-url`, or against the document's own URL when it was read over HTTP or HTTPS. An
operation without servers uses `--client-default-base-url`; without one, generation fails, for a `GET /pets` with
`Error: /paths/~1pets/get: A server of GET /pets does not resolve to an absolute http or https URL`.

`server=ServerSelection(index=, variables=)` picks another declared server by position and supplies its variables, as
the example picks the `us` region of the shelter's server; `base_url=` replaces the servers with one URL, as its last
call does. `Client`, `with_options`, and `RequestOptions` take the same two
keywords, and the nearest one given wins: a call's over its view's, a view's over the client's, so a view's `server`
also replaces the client's `base_url`. Giving both on one of them raises `ConfigurationError` with the reason
`conflicts_with_server`. An index the operation does not declare raises `ConfigurationError` with `out_of_range`, a
variable it does not declare `undeclared`, and a value outside a variable's `enum` `not_allowed`, when the call is
made.

### Headers and query defaults

`default_headers` and `default_query` of the client and its views, and a call's `extra_headers` and `extra_query`, give
each name they list their value over the generated headers and query, the client's first, then the views', the call's
parameters', and the call's extra ones last; None removes that name. Header names compare case-insensitively, query
names exactly. A body's media type ranks above every layer but the call's `extra_headers`: only a call's Content-Type
relabels its body, or with None sends it unlabelled, and a multipart one without `boundary=` keeps the body's boundary.
A header's name is an HTTP token and its value [header text](python-client-reference.md#model-codecs), HTAB, SP, and visible ASCII without
whitespace at either end, or None: for any other, such as a value with a line break, `café`, or a space at its end,
`Client(...)`, `with_options(...)`, and `RequestOptions(...)` raise `ConfigurationError` with the reason
`invalid_value` and the `field_path` `("headers", name)`, so nothing is sent.

### Timeouts

The example above limits each HTTPX2 phase of a request to 10 seconds and connecting to 2, and gives every call of
the `traced` view a budget of 30 seconds, retries and waits included.

| Setting | Effective default | Meaning |
|---|---|---|
| `timeout` | Owned client: connect 5, read/write/pool 600 seconds; injected client: its native timeouts | Native I/O phase limits |
| `total_timeout` | `None` | Optional budget across attempts and retry waits, starting at call entry |
| `clock` | `Clock()`, the system clock | Client only: time, jitter, and wait sources |

Each setting a call leaves None inherits from the nearest `with_options` view, then the client. Where None has a
meaning of its own, UNSET is the default and inherits instead: `timeout=None` clears all four phase limits, and
`total_timeout=None` on a view or a call removes an inherited total budget. A number as `timeout` limits every phase,
and an `httpx2.Timeout` gives each phase its own limit. An injected HTTP client's native timeouts supply the initial
phase settings unless the client is given `timeout`. Durations must be finite and nonnegative; booleans are rejected.
Invalid values raise `ConfigurationError` with the setting's `field_path`. `total_timeout=0` is valid configuration;
starting a call with it raises `APITimeoutError` before preparing or sending a request.

See [Timeouts and cancellation](python-client-reference.md#timeouts-and-cancellation) for the budget across attempts,
cancellation, and streams.

### Retries

A call is sent again after a failure that may pass, such as a connection that could not open or a `503`, as long as the
operation is safe to send again. The waits grow exponentially. This client waits without jitter and through a
[`Clock`](python-client-reference.md#clocks-and-retry-jitter) that records each wait instead of sleeping, so the
example shows them:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.howto.retries -->
<!-- fmt: off -->

```python
from app.client import Client
from app.client.options import Clock, RetryOptions
from fake import http_client

waits: list[float] = []
clock = Clock(monotonic=lambda: sum(waits), sleep=waits.append)

with Client(retry=RetryOptions(jitter="none"), clock=clock, http_client=http_client()) as client:
    response = client.default.with_response.get_health()
    print(response.data, response.info.attempt_count, waits)
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.howto.retries -->

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.howto.retries-output -->
<!-- fmt: off -->

```text
> GET https://eu.shelter.example/v1/health
> GET https://eu.shelter.example/v1/health
> GET https://eu.shelter.example/v1/health
status='up' 3 [0.5, 1.0]
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.howto.retries-output -->

`max_retries` and `retry=RetryOptions(...)` apply on clients, views, and calls. The fields of `RetryOptions` merge
independently; omitted fields inherit, while a status set replaces the inherited set, and `retry=None` inherits the
whole record. Automatic retries require a candidate failure or status, operation safety, replayable input, and enough
time. JSON/model decoding, arbitrary callbacks,
cancellation, and logical deadlines never restart a request.

| Setting | Effective default | Meaning |
|---|---|---|
| `max_retries` | `2` | Retries after the initial attempt; `0` disables retries; a setting of its own, not a `RetryOptions` field |
| `initial_delay`, `max_delay` | `0.5`, `8` seconds | Exponential backoff, with maximum at least the initial delay |
| `jitter` | `"full"` | Uniform delay below the exponential cap; `"none"` uses the cap |
| `statuses` | `{408, 429, 500, 502, 503, 504}` | Replace with an integer set in 400–599; 401/403/407 are forbidden |
| `max_retry_after` | `60` seconds | Positive cap on accepted server delay; `None` removes this cap |
| `respect_retry_after` | `True` | Explicit `False` ignores server delay hints |
| `retry_after_ms_header`, `should_retry_header` | Operation declaration, otherwise `None` | Vendor controls require generated metadata; explicit `None` disables one |
| `retry_on_pool_timeout` | `False` | Avoid amplifying pool contention unless explicitly enabled |

GET, HEAD, OPTIONS, PUT, and DELETE are eligible for retries by default. POST, PATCH, and other methods require an explicit
`retry_safety="idempotent"` declaration or a valid server key contract. A native `ConnectError` (TLS and DNS failures
included), `ConnectTimeout`, or `PoolTimeout` proves the request unsent and can permit otherwise unsafe methods,
unless an earlier attempt of the call, or a response HTTPX2 followed a redirect from, reached the server; every other
failure after the send started may have been sent and is never sent again. `retry_safety="never"` prohibits every
resend, including an unsent request.

See [Retries and operation contracts](python-client-reference.md#retries-and-operation-contracts) for server delays,
idempotency keys, and the retry settings an operation declares.

### Views

A *view* is what `with_options(...)` returns: an object with the same resources and methods as its client, whose
settings are the client's with the given ones replaced. In the example above, `traced` sends an extra header and has a
30-second budget, while `client` is unchanged. A view shares its client's connections and needs no closing; closing the
client closes it too. Use a view for a group of calls with other settings, and `options=RequestOptions(...)` for one
call. A view's `default_headers` and `default_query` add to the client's, and its other settings replace them.

### Use your own HTTPX2 client

`http_client=` takes an `httpx2.Client`, or an `httpx2.AsyncClient` for `AsyncClient`, configured as you need: TLS,
proxies, connection limits, HTTP/2, event hooks, or a transport such as the `MockTransport` of the examples. The client
borrows it and never closes it, so close it after the client. Without one, the client creates
`httpx2.Client(timeout=httpx2.Timeout(600, connect=5))` and closes it on `close()` or at the end of `with`. See
[Instrumentation](python-client-reference.md#instrumentation) and
[Redirects and transport construction](python-client-reference.md#redirects-and-transport-construction).

## Authenticate

Generated clients compile root security inheritance and operation overrides from OpenAPI. `Client` and `AsyncClient`
take one keyword argument per security scheme an operation requires, named after the scheme in snake case; a package
whose operations have no credentialed security alternative takes no credentials, copies no credential runtime, and
exports no `AuthError`. An argument whose name another constructor argument or scheme takes is suffixed, such as
`timeout_1` for a scheme named `timeout`, and so is the PascalCase prefix of OAuth provider classes that another
scheme's prefix takes, as model class names are.

An API key or bearer argument takes a string, and an HTTP Basic one a `(username, password)` tuple, which is encoded
as UTF-8, the charset of RFC 7617, which the generated server reads. Each also takes a callable returning its value, which the client calls for each request, so a rotating
token or a key read from a secret store stays current; a bearer argument also takes an OAuth provider. A callable's
failure raises `AuthError` with the reason `provider_failed` before the request is sent, and a value of the wrong type
`ConfigurationError` with the reason `invalid_type`. A credential sent in a header, such as `Bearer <token>`, must be
[header text](python-client-reference.md#model-codecs), as every header value: HTAB, SP, and visible ASCII without
whitespace at either end, so an empty bearer token, a key with a space at its end, a line break, or `café` raises
`ConfigurationError` with the reason `invalid_value` and the scheme's argument as `field_path` before sending; a key
with an inner tab is sent. A cookie credential must be a cookie value: visible ASCII without `"`, `,`, `;`, or `\`. No
environment variables or credentials are discovered by default.

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.howto.auth -->
<!-- fmt: off -->

```python
from app.client import Client
from app.client.errors import ConfigurationError
from fake import http_client

with Client(bearer="s3cret", http_client=http_client()) as client:
    client.pets.get_pet(pet_id=1)

tokens = iter(["token-1", "token-2"])
with Client(bearer=lambda: next(tokens), http_client=http_client()) as client:
    client.pets.get_pet(pet_id=1)
    client.pets.get_pet(pet_id=2)

with Client(http_client=http_client()) as client:
    try:
        client.pets.get_pet(pet_id=1)
    except ConfigurationError as error:
        print(error.reason)
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.howto.auth -->

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.howto.auth-output -->
<!-- fmt: off -->

```text
> GET https://eu.shelter.example/v1/pets/1 authorization: Bearer s3cret
> GET https://eu.shelter.example/v1/pets/1 authorization: Bearer token-1
> GET https://eu.shelter.example/v1/pets/2 authorization: Bearer token-2
missing_credentials
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.howto.auth-output -->

The bearer token goes to the `Authorization` header, a callable is called for each request, and a call that needs
credentials that the client lacks raises `ConfigurationError` with the reason `missing_credentials` before sending.
`get_health` declares `security: []`, so it needs no credentials.

A call sends the credentials of its operation's first security alternative they all satisfy: an AND alternative
needs all its schemes, and an OR list is tried in declared order. Each credential goes to the header, query field, or
cookie its scheme declares, replacing a value a client, view, or call header or a parameter put there, and only to the server's
origin: a page a server links at another origin, and `request_raw`, carry none of them. Absent security, explicit
`[]`, and an empty alternative are anonymous choices that apply only when no other alternative is satisfied, so an
optional operation sends a credential given for a listed scheme and, with none, sends nothing. A required operation that no credential satisfies raises `ConfigurationError` with the reason
`missing_credentials` before sending, unless the HTTP client has an Auth of its own, which it then uses. The client
authorizes no scopes: the resource server decides, and a 403 is terminal.

`auth` of the client, a view, or a call's `RequestOptions` takes any `httpx2.Auth`, which replaces the credentials
for those calls, and `auth=None` sends without an Auth; it cannot make a required operation anonymous. Unset, the
credentials apply, or else the HTTP client's own `auth`. Credentials given beside `Client(auth=...)` or an injected
HTTP client with an `auth` raise `ConfigurationError` with the reason `conflicting_auth`, since one
request runs one Auth flow.

Declare an API-specific challenge-less 401 contract in the operation's `runtime` setting of
[`--client-operations`](#per-operation-settings). It is false unless explicitly set and is recorded under
`client-operations."<pointer>".runtime.auth_challenge_less_401` in the generated README. It lets an OAuth provider
renew its token after a 401 without a `WWW-Authenticate` challenge. It is not a client option or an inferred response
behavior.

```toml
[tool.datamodel-codegen.client-operations]
"/paths/~1challenge-less/get" = { runtime = { auth_challenge_less_401 = true } }
```

### Sign requests with an HTTPX2 Auth

Request signing is an `httpx2.Auth` of your own, given as `auth`. Its flow sees the final request, body included,
after the client framed it; set `requires_request_body = True` to read a streamed body first. HTTPX2 runs it once per
send, and every retry sends the original request through it again. Here the signer also places the bearer token, since `auth` replaces the client's credentials:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.howto.signing -->
<!-- fmt: off -->

```python
import hashlib
import hmac
from collections.abc import Generator

import httpx2

from app.client import Client
from fake import http_client


class BodySigner(httpx2.Auth):
    requires_request_body = True

    def __init__(self, key: bytes, token: str) -> None:
        self.key = key
        self.token = token

    def auth_flow(self, request: httpx2.Request) -> Generator[httpx2.Request, httpx2.Response, None]:
        digest = hmac.digest(self.key, request.method.encode() + b"\n" + request.content, hashlib.sha256)
        request.headers["Authorization"] = f"Bearer {self.token}"
        request.headers["X-Signature"] = digest.hex()
        yield request


with Client(http_client=http_client()) as client:
    signed = client.with_options(auth=BodySigner(b"signing-key", "s3cret"))
    print(signed.pets.create_pet(name="Kiki"))
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.howto.signing -->

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.howto.signing-output -->
<!-- fmt: off -->

```text
> POST https://eu.shelter.example/v1/pets content-type: application/json authorization: Bearer s3cret x-signature: aebceadaa214619bd8402dec14456b4df1c8fd2fe8f7b399365e8b594e6aa679 {"name":"Kiki"}
id=3 name='Kiki' tag=None
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.howto.signing-output -->

A signature header other than `Authorization` is copied to a redirect HTTPX2 follows; set `follow_redirects=False`
for signed calls whose redirects may leave the server's origin.

Credential values do not appear in repr. A credential placed in the query is part of the request URL,
which HTTPX2 logs at INFO level on its `httpx2` logger, so keep that logger above INFO wherever URLs must stay
private. Errors retain safe metadata and their causes, which can hold an exception a credential callable raised;
apply your own policy before logging causes.

### OAuth providers

A package whose operations require an OAuth 2 or OpenID Connect scheme exports `ClientCredentials`, `RefreshToken`,
and `TokenSet` from its `auth` module, and one subclass for each declared flow whose token URL the API declares, such
as `OauthClientCredentials` for the `oauth` scheme's client credentials flow. A subclass requests tokens from its
declared URL unless `token_url` names another; the base classes need `token_url`. A provider is a bearer credential:
the call that needs a token requests it inline while holding the provider's lock, a `threading.Lock` or an
`asyncio.Lock` on the event loop the provider is used on, concurrent calls wait for that token, and nothing runs in
the background or is persisted. The shelter's `oauth` scheme declares a client credentials flow, so its package has
`OauthClientCredentials`:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.howto.oauth -->
<!-- fmt: off -->

```python
from app.client import Client
from app.client.auth import OauthClientCredentials
from fake import http_client

provider = OauthClientCredentials(client_id="shelter-cli", client_secret="pa55", scopes=("pets.read",))
with Client(oauth=provider, http_client=http_client()) as client:
    client.pets.get_pet(pet_id=1)
    client.pets.get_pet(pet_id=2)
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.howto.oauth -->

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.howto.oauth-output -->
<!-- fmt: off -->

```text
> POST https://auth.shelter.example/token content-type: application/x-www-form-urlencoded authorization: Basic c2hlbHRlci1jbGk6cGE1NQ== grant_type=client_credentials&scope=pets.read
> GET https://eu.shelter.example/v1/pets/1 authorization: Bearer issued-1
> GET https://eu.shelter.example/v1/pets/2 authorization: Bearer issued-1
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.howto.oauth-output -->

The provider requested one token and sent it with both calls.

`ClientCredentials` requests a confidential client's own token. Its request sends the `scopes` in order and the
`audience` when one is given, and authenticates the client with `client_secret_basic` (the default) or
`client_secret_post`; the `client_secret` is a string or a callable returning one, called for each token request.

`RefreshToken` keeps an access token current with the refresh token grant, starting from the `TokenSet` an authorization
produced. The provider alone refreshes it: no other provider or process may send the same refresh token, and since
synchronous calls and each event loop renew under a lock of their own, use it from one of them at a time. Its client
authentication defaults to `none`, which sends the `client_id` of a public client. For a scheme with an authorization code flow, construct the subclass from the `TokenSet` your
authorization produced, as `OauthRefreshToken(tokens, client_id="pets-app", on_token_refreshed=save)`, and pass it as
the scheme's credential, such as `Client(oauth=provider)`.

Each successful refresh makes a new `TokenSet` current, keeping the refresh token sent when the response gives none,
and then passes it to `on_token_refreshed` while the provider still holds its lock, so an application can persist each
token set in order; for an async client the callback may return an awaitable, which is awaited. An exception the
callback raises reaches the caller, and the refreshed token set stays current. The callback must not call its own
provider. A `TokenSet` needs a timezone-aware `expires_at` when its access token expires, and its repr omits both
tokens. `invalid_grant` raises `AuthError` with the reason `reauthorization_required`, and the application starts a
new authorization; a token set without a refresh token serves its access token until it expires or a resource rejects
it, and then raises the same error without a request.

A provider serves its token until a tenth of its lifetime, at most thirty seconds, remains, and then the call that
finds it due requests another; a token without `expires_in` is kept until a resource rejects it. When an early renewal
fails, the call keeps using the current token until it expires, and each later call in that margin tries the renewal
again. A 401 whose `WWW-Authenticate` has a Bearer
`invalid_token` error, or a 401 without a challenge on an operation declaring `auth_challenge_less_401`, renews the
token once, unless another call already replaced it, and sends the request again when its body can be sent again.
A 401 after a redirect, and a WebSocket handshake's, renews nothing.

Token requests go through the client's HTTP client without its Auth and without following redirects, with the calling
request's timeouts, or through the provider's own `http_client` when one is given, which keeps its own settings. The
client's HTTP client runs its event hooks on these token requests and responses, which carry the client authentication
and tokens. Used as an `httpx2.Auth` elsewhere, a provider needs its own `http_client`. The token URL must be HTTPS, or
HTTP to a loopback host. A rejection, an unexpected status, or a response that is not a JSON object with a Bearer
`access_token` raises `AuthError` with the reason `oauth_error`, a rejection keeping its `status_code` and standard
`oauth_error` code, and an `expires_in` that is not a positive number, or a refreshed one past the last date Python can
hold, the reason `invalid_expiry`. A transport failure
raises `AuthError` with the reason `oauth_error`, or `timeout`, and the native exception as its cause, which can hold
the token request and its client authentication. A provider's `clock=Clock(...)` times its token expiry.

## Handle errors

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.howto.errors -->
<!-- fmt: off -->

```python
from app.client import Client
from app.client.errors import APIConnectionError, APIStatusError, NotFoundError
from fake import http_client

with Client(bearer="s3cret", http_client=http_client()) as client:
    try:
        client.pets.get_pet(pet_id=99)
    except NotFoundError as error:
        print(error.status_code, repr(error.body))

    try:
        client.adoptions.adopt_pet(pet_id=1)
    except APIStatusError as error:
        print(type(error).__name__, error)

    offline = client.with_options(base_url="https://offline.example/v1", max_retries=0)
    try:
        offline.pets.list_pets()
    except APIConnectionError as error:
        print(type(error).__name__, error.reason, repr(error.cause))
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.howto.errors -->

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.howto.errors-output -->
<!-- fmt: off -->

```text
> GET https://eu.shelter.example/v1/pets/99 authorization: Bearer s3cret
404 Error(message='/pets/99 not found')
> POST https://eu.shelter.example/v1/pets/1/adoption authorization: Bearer s3cret
ConflictError Error code: 409 - {"message":"Mimi is already adopted"}
APIConnectionError None ConnectError('connection refused')
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.howto.errors-output -->

Every exception the client defines derives from `SDKError` and comes from `client.errors`; Python's own exceptions,
such as `TypeError` for an unknown keyword, pass through unchanged. A declared error status raises the subclass of
its status with the body decoded by the declared error schema, as `NotFoundError` and `ConflictError` do above; catch
`APIStatusError` for any of them. A failure to reach the server raises `APIConnectionError` with the HTTPX2 exception
as `cause`.

| Exception | Raised for |
|---|---|
| `APIStatusError` | A final status the operation does not declare as a success. Its subclasses are `BadRequestError` (400), `AuthenticationError` (401), `PermissionDeniedError` (403), `NotFoundError` (404), `ConflictError` (409), `UnprocessableEntityError` (422), `RateLimitError` (429), and `InternalServerError` (500 and above); it has `status_code`, `headers`, and `body` |
| `APIConnectionError` | A transport failure, after the retries it allows |
| `APITimeoutError` | A subclass of `APIConnectionError`: a phase timeout, or the call's `total_timeout` |
| `DecodeError` | An argument that cannot be sent, or a response that does not decode into its model |
| `ConfigurationError` | A setting, argument, or call the client refused before sending, with a `reason` and a `field_path` |
| `AuthError` | A credential callable or an OAuth token request that failed |

Each also has `reason`, `operation_id`, `info` (the `ResponseInfo` when a response arrived), `cause`, `attempt_count`,
`elapsed`, and `request_id`. Messages name no payload, header, or credential. See
[Errors](python-client-reference.md#errors) for every field and reason.

## Test code that uses the client

Test your own code against an `httpx2.MockTransport` instead of a server: pass an HTTPX2 client with that transport as
`http_client`, and answer each request in a function. Given a module that uses the client:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.howto.shelter -->
<!-- fmt: off -->

```python
from app.client import Client
from app.client.errors import NotFoundError


def describe(client: Client, pet_id: int) -> str:
    try:
        pet = client.pets.get_pet(pet_id=pet_id)
    except NotFoundError:
        return f"No pet {pet_id}"
    return f"{pet.name} ({pet.tag or 'no tag'})"
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.howto.shelter -->

its tests build the client in a fixture:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.howto.test -->
<!-- fmt: off -->

```python
from collections.abc import Iterator

import httpx2
import pytest

from app.client import Client
from shelter import describe

PETS = {"/v1/pets/1": {"id": 1, "name": "Mimi", "tag": "cat"}}


def answer(request: httpx2.Request) -> httpx2.Response:
    assert request.headers["Authorization"] == "Bearer test-token"
    if (pet := PETS.get(request.url.path)) is None:
        return httpx2.Response(404, json={"message": "Not found"})
    return httpx2.Response(200, json=pet)


@pytest.fixture
def client() -> Iterator[Client]:
    with (
        httpx2.Client(transport=httpx2.MockTransport(answer)) as http,
        Client(bearer="test-token", http_client=http) as client,
    ):
        yield client


def test_describe(client: Client) -> None:
    assert describe(client, 1) == "Mimi (cat)"


def test_describe_missing(client: Client) -> None:
    assert describe(client, 2) == "No pet 2"
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.howto.test -->

```bash
uv add --dev pytest
uv run pytest test_client.py
```

When the API is a FastAPI application you can import, such as one [generated from the same document](fastapi-server.md),
FastAPI's `TestClient` is an `httpx2.Client` too: pass `http_client=TestClient(app)` and
`base_url="http://testserver"` to call the application in the test process, without a server.

## Regenerate after the document changes

Run the same command again after the OpenAPI document changes. The methods change with the operations, so type
checkers point at the calls to update. As for models, every generation overwrites every generated file of the package, also one you edited, and never
deletes one. The modules of a resource that is no longer generated stay until you delete them: `--check` lists
them as extra files, and a generation that leaves such `.py` files in place names them in one
`LeftoverFilesWarning`, which Python shows once per process for the same text, also under `--watch` and repeated
`generate()` calls. Silence exactly this warning with
`warnings.simplefilter("ignore", datamodel_code_generator.LeftoverFilesWarning)` around `generate()`, or with
`ignore::datamodel_code_generator.LeftoverFilesWarning` in pytest's `filterwarnings`. Keep your own code outside
the package, because `--check` treats every `.py` file under `--client-output` as generated. The models can live inside the package, such as `--output app/client/models.py` with
`--client-model-package app.client.models`, as for the [server package](fastapi-server.md#the-generated-package).

`--check` compares without writing and prints the differences as for models, and `--output-format json`
emits the model generation and check payloads, as with `--generate-server`; their `target` names the package and
the runtime dependencies of the `uv add` notice, such as `{"package": "app.client", "dependencies": ["httpx2>=2.13.0",
"typing-extensions>=4.16", "pydantic>=2.14.0"]}`. The generation payload adds the next steps of the notice: the
package `readme`, with a path relative like those of `files`, and `usage`, the code that calls the first operation.
`--diff-against` and `--watch` also work
as they do with `--generate-server`. Use `--check` in CI to fail when the package is out of date; see
[Check in CI](fastapi-server.md#check-in-ci), which applies to the client as well.

## Configure generation

### Requirements

Like the server, the client needs Python 3.11 or later, both to run `datamodel-codegen` and as the target Python
version. As for the server, a setting the client needs that you leave unset takes the value it needs, and a value you
set that it cannot use is refused; see the server's [Requirements](fastapi-server.md#requirements), which also
describes the notice that shows how to set them and `target.implied`. An unset target Python version is `3.11`; one
below 3.11 that you or a preset set is refused.

Settings of the generated client that change how its methods are declared or checked do not change the models: the
same OpenAPI document and model settings produce the same model files whichever client settings you choose.

The model's `openapi_scopes` must include `api`, so that the models cover the parameters, bodies, and responses of
the operations: unset, it is `schemas api`; scopes you give without `api` stop the run with
`Error: --generate-client requires --openapi-scopes to include api` and exit code 2, and the Python API raises
`datamodel_code_generator.Error`. The client keeps every model setting
as given, including the generation timestamp, which `disable_timestamp` leaves out. The client files are headed,
formatted, and encoded like the model files, by the same model settings: `formatters`, `custom_formatters` and
`custom_formatters_kwargs`, `custom_file_header`, `custom_file_header_path` and `custom_file_header_mode`,
`disable_timestamp`, `enable_version_header`, `enable_command_header`, `use_double_quotes`,
`builtin_format_line_length`, `encoding` (for the Python files; the other files are UTF-8), `settings_path`, and
`use_type_checking_imports`. As for the models, Ruff moves imports only an annotation uses into `TYPE_CHECKING`
blocks when `ruff-check` runs with its TC rules selected and `use_type_checking_imports` allows it: by default for
the dataclass, TypedDict, and msgspec backends, and with `use_type_checking_imports` for the Pydantic ones; the
client reads no annotation at run time. As for the models, `typing.get_type_hints` on a generated class or function
whose annotations name a moved import then raises `NameError`: pass `--no-use-type-checking-imports`, or do not
select the TC rules, to introspect the annotations at run time. Custom
templates and custom formatters must keep the class and field names of the models: the client binds each operation
to the names in the generated model graph and does not read the rendered model source.

The generated package needs `httpx2>=2.13.0` and the other requirements of the printed `uv add` command. With Pydantic
models the minimum is `pydantic>=2.14.0`. An explicit `--target-pydantic-version` older than that minimum (`2`, `2.11`
or `2.12`) is refused with `Error:` and exit code 2 for those models; leave it unset or use `2.14`. The other model
backends do not use the option.

`--use-missing-sentinel` needs `--target-pydantic-version 2.14`, with which the models import `MISSING` from
`pydantic`; with the sentinel, an unset target is `2.14`. Model generation alone imports it from
`pydantic.experimental.missing_sentinel`, which pydantic 2.14 deprecates, when the target is unset or `2.12`, and
refuses the option for `2` and `2.11`. With a target of `2`, `2.11`, or `2.12` that you set, client generation
stops with `Error: The client target needs --target-pydantic-version 2.14 for --use-missing-sentinel, so that the
models import MISSING from pydantic` and exit code 2.

A `pydantic_v2.dataclass` field cannot tell an unset member from one set to `None`, so client generation with
`--output-model-type pydantic_v2.dataclass` needs `--use-missing-sentinel`, and with it
`--target-pydantic-version 2.14`: unset, the sentinel is on and the target is `2.14`. With the sentinel turned off,
`use-missing-sentinel = false` in `pyproject.toml` or `use_missing_sentinel=False` for `generate()`, generation
stops with `Error: The client target needs --use-missing-sentinel with --target-pydantic-version 2.14 for
'pydantic_v2.dataclass' models, whose fields cannot tell an unset member from None` and exit code 2. With both, an optional field without a default holds `MISSING`, as it
does for `pydantic_v2.BaseModel` when the options are set:

```python
from app.client import Client
from app.models import NewPet
from pydantic import MISSING

with Client() as client:
    client.pets.create_pet(body=NewPet(name="Mimi"))  # tag holds MISSING, so the request leaves it out
    if client.pets.get_pet(pet_id=1).tag is MISSING:
        print("the response left tag out")
```

A field the constructor is not given holds `MISSING` and is left out of a request; give `None` to send `null` where
the property is nullable. A member a response leaves out decodes to `MISSING`, and a `null` that its property does not
allow is refused with a `DecodeError` whose reason is `invalid_value`.

msgspec decodes a union of structs only when every struct in it is tagged, and an Enum only when its members are
all `str` or all `int` values, so client generation with `--output-model-type msgspec.Struct` needs
[`--use-msgspec-tagged-unions`](cli-reference/model-customization.md#use-msgspec-tagged-unions) and
[`--use-msgspec-nullable-enum-alias`](cli-reference/model-customization.md#use-msgspec-nullable-enum-alias), whether
or not the document declares a discriminated union or an enum that lists `null`: unset, both are on. With one turned
off in `pyproject.toml` or for `generate()`, generation stops with exit code 2 and an error that names every one
turned off, such as `Error: The client target needs
--use-msgspec-tagged-unions for 'msgspec.Struct' models, so that their discriminated unions can be decoded; The
client target needs --use-msgspec-nullable-enum-alias for 'msgspec.Struct' models, so that their enums that list null
can be decoded`, and the Python API raises `datamodel_code_generator.Error`. A union of structs that has no
discriminator, or that the option still leaves untagged (with a warning that names the reason), cannot be decoded: a
call that receives it fails with an `APIConnectionError` whose cause is msgspec's `TypeError`.

### Generator options

Every client setting is an option, a key of `[tool.datamodel-codegen]` in `pyproject.toml`, and an option of
`generate()` for the [Python API](#python-api):

| Option | `pyproject.toml` key | `generate()` option | Default |
|---|---|---|---|
| [`--generate-client httpx2`](cli-reference/target-generation-options.md#generate-client) | `generate-client` | `generate_client` | off |
| [`--client-output`](cli-reference/target-generation-options.md#client-output) | `client-output` | `client_output` | required |
| [`--client-package`](cli-reference/target-generation-options.md#client-package) | `client-package` | `client_package` | required |
| [`--client-model-package`](cli-reference/target-generation-options.md#client-model-package) | `client-model-package` | `client_model_package` | required |
| [`--client-signature-style`](cli-reference/target-generation-options.md#client-signature-style) | `client-signature-style` | `client_signature_style` | `"explicit"`; see [Signature style](python-client-reference.md#signature-style) |
| [`--client-body-arguments`](cli-reference/target-generation-options.md#client-body-arguments) | `client-body-arguments` | `client_body_arguments` | `"body"`; see [Request bodies](#request-bodies) |
| [`--client-resource-names`](cli-reference/target-generation-options.md#client-resource-names) | `client-resource-names` | `client_resource_names` | none: a resource for each first tag, and `default` for operations without a tag |
| [`--client-operations`](cli-reference/target-generation-options.md#client-operations) | `client-operations` | `client_operations` | none; see [Per-operation settings](#per-operation-settings) |
| [`--client-default-base-url`](cli-reference/target-generation-options.md#client-default-base-url) | `client-default-base-url` | `client_default_base_url` | none; see [Base URLs and servers](#base-urls-and-servers) |
| [`--client-server-base-url`](cli-reference/target-generation-options.md#client-server-base-url) | `client-server-base-url` | `client_server_base_url` | none; see [Base URLs and servers](#base-urls-and-servers) |
| [`--client-protocols`](cli-reference/target-generation-options.md#client-protocols) | `client-protocols` | `client_protocols` | none; see [Protocol Helpers](python-client-helpers.md) |

`--client-resource-names`, `--client-operations`, and `--client-protocols` take a JSON object, inline or as the path
of a JSON file, as `--aliases` does; in `pyproject.toml` they are tables, or the path of a JSON file, and in
`generate()` mappings of the same shape. A value on the
command line takes precedence over the selected profile, which takes precedence over the base table, and a
command-line table replaces the whole table of `pyproject.toml`. An operation's own `body_arguments` takes precedence
over `--client-body-arguments`, wherever each comes from. Paths in `pyproject.toml`, including the JSON files of these
settings, are relative to its directory, and command-line paths are relative to the working directory. The documents
that operation references and helpers name are relative to the JSON file that holds them, however the file is given,
as a `$ref` is relative to its document; a file given through a symbolic link uses the link's directory. Without a
file, they are relative to the `pyproject.toml` directory for its tables, and to the working directory for inline JSON
on the command line and for mappings given to `generate()`.

`--client-resource-names` maps a tag to the dotted namespace of the resource its operations join, such as
`{"pets": "store.pets"}`; a tag that is the first tag of no operation stops the run with an error, as a group key of
`--server-router-names` that matches no group does on the server. Operations without a tag join the resource
`default`, as in `client.default.get_health()`, the name the generated FastAPI server gives their group. Its name is
derived like a tag's: beside a tag that also derives `default`, such as `default` or `Default`, the one declared first
keeps `default` and the other takes a number, such as `default_1`, as on the server. No `--client-resource-names` entry
names the operations without a tag, so the `resource` of a `--client-operations` entry moves one of them to another
resource. Such a move leaves the derived names as they are: a tag, or the operations without one, takes its place among
them from its first operation, moved or not, as the server's groups do. An explicit namespace is shared by everything
that names it: a tag mapped to `default`, or an operation whose `resource` is `default`, joins the resource of that
name.

`--generate-client` requires `--client-output`, `--client-package`, and `--client-model-package`, and a `--client-*`
option given on the command line requires `--generate-client`; both stop with `Error:` and exit code 2.
`--generate-client` also requires `--input-file-type openapi`: an input type left to detection, `auto`, or any other
type stops with `Error: --generate-client requires --input-file-type openapi, not auto` (naming the given type) and
exit code 2 before the input is read.
`--generate-client` cannot be used with `--generate-server`, and one selected on the command line replaces the other
selected in `pyproject.toml`. Client keys of `pyproject.toml` are validated like model keys, but have no effect while
no client is selected.

### Per-operation settings

`--client-operations` maps an operation reference, the JSON pointer of the path item method such as
`/paths/~1pets/get`, or a document and a pointer joined by `#`, to the settings of that operation, as the
[how-to project](#call-operations) does for `listPets`. A pointer escapes `~` as `~0` and `/` as `~1`, so the operation
`GET /pets/{petId}` is `/paths/~1pets~1{petId}/get`; the generated README lists the reference of every operation.

| Member | Value |
|---|---|
| `resource` | The dotted namespace of the operation's resource |
| `name` | The method name |
| `parameter_names` | Argument names keyed by location and name, such as `{"query:limit": "page_size"}` |
| `request_media_type`, `response_media_type` | The default media types of the body and of the result |
| `description` | The method docstring, over the operation's route or, with `--use-schema-description`, its summary and description |
| `body_arguments` | `"body"` or `"both"`, over `--client-body-arguments` |
| `body_field_names` | Keyword names of body fields, keyed by media type, then property, such as `{"application/json": {"petName": "pet_name"}}` |
| `runtime` | `request_id_header`, `success_statuses`, `retry_safety`, `idempotency` (`{"header_name": "Idempotency-Key"}`), `retry_after_ms_header`, `should_retry_header`, `auth_challenge_less_401`, and `accepted_content_encodings` |

```toml
[tool.datamodel-codegen.client-operations]
"/paths/~1pets/get" = { name = "list_all", parameter_names = { "query:limit" = "page_size" } }
"/paths/~1pets/post" = { runtime = { idempotency = { header_name = "Idempotency-Key" } } }
```

```bash
datamodel-codegen --client-operations '{"/paths/~1pets/get": {"name": "list_all"}}'
```

A member the client does not know, or a value it cannot use, stops generation with `Error:` and exit code 2, naming
the setting by the key it was given under, such as `--client-operations['/paths/~1pets/get'].runtime.retry_safety`;
`generate()` raises `datamodel_code_generator.Error` with the same message.

### Python API

`generate()` takes the client options with the model options. The choices are the enums `ClientType`,
`ClientSignatureStyle`, and `ClientBodyArguments` of `datamodel_code_generator`, whose string values also work, and
`client_resource_names`, `client_operations`, and `client_protocols` take mappings shaped like their JSON. The
how-to project's settings:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.howto.generate -->
<!-- fmt: off -->

```python
from pathlib import Path

from datamodel_code_generator import (
    ClientBodyArguments,
    ClientType,
    DataModelType,
    InputFileType,
    OpenAPIScope,
    generate,
)

generate(
    Path("openapi.yaml"),
    input_file_type=InputFileType.OpenAPI,
    openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
    output_model_type=DataModelType.PydanticV2BaseModel,
    preset="standard-py312-20261017",
    output=Path("app/models.py"),
    generate_client=ClientType.HTTPX2,
    client_output=Path("app/client"),
    client_package="app.client",
    client_model_package="app.models",
    client_body_arguments=ClientBodyArguments.Both,
    client_operations={"/paths/~1pets/get": {"runtime": {"request_id_header": "X-Request-Id"}}},
)
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.howto.generate -->

It publishes every change together and returns `None`, like model generation. Without an `output`, it writes nothing
but the model metadata and a remote lock update and returns every model and client file as `GeneratedModules`, under
the paths their import paths imply; see
[Generating a Server or Client Package](using_as_module.md#generating-a-server-or-client-package).
`load_pyproject_config()` reads the client keys of `pyproject.toml`, whose paths and documents resolve as they do on
the command line. The `uv add` notice and the next steps are command-line messages only.

### Batch jobs

A [named job](pyproject_toml.md#named-jobs-experimental) whose settings select the client generates it as a single run
does, under the rules of [server jobs](fastapi-server.md#batch-jobs): `client-output` must not overlap the outputs of
other jobs or contain the remote lock file, and `--all-jobs --watch` regenerates client jobs too. Jobs that each
select a server or a client may name the same models `output`, which the batch writes once; they must generate the
same models for it.

## Next steps

- [Python Client Protocol Helpers](python-client-helpers.md): generated pagers, polled long-running operations,
  resumable uploads, SSE and NDJSON streams, WebSockets, a response cache, and webhook verification, declared in a helper
  file.
- [Python Client Reference](python-client-reference.md): signature styles, body field arguments, model conversion,
  timeouts and clocks, instrumentation, retries and operation contracts, request compression, body replay, redirects,
  templates, and every error.
