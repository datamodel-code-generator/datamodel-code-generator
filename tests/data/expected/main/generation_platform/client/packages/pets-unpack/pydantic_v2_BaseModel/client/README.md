# client

The `client` package is an HTTPX2 client of the API the OpenAPI document describes.
datamodel-code-generator generated it from the document, together with the `models` models of the `pydantic_v2.BaseModel` backend.
Generation overwrites every file of the package, as it does the models: keep your code outside the package and regenerate the package instead of editing it; see [Regenerating](#regenerating).

HTTPX2 client generation is experimental.
The [runtime reference](_generated_docs/runtime.md) describes the settings, defaults, ownership, cancellation, replay, redirects, and transport costs of the clients.

## Quick start

```python
from client import Client

with Client(base_url="https://api.example.com/v1") as client:
    result = client.pets.list_pets(X_Trace="...")
```

`AsyncClient` has the same resources and methods, and each method is a coroutine:

```python
from client import AsyncClient


async def main() -> None:
    async with AsyncClient(base_url="https://api.example.com/v1") as client:
        result = await client.pets.list_pets(X_Trace="...")
```

Replace each `...` with a value of the type its argument declares; [Operations](#operations) lists the arguments of each method.
A method returns the decoded body of a success response, of the type its signature declares.
Leaving the `with` or `async with` block closes the client, as `close()` or `aclose()` does; keep a streaming response only inside its own context.
Import options, bodies, errors, and response types from the public modules of the package.
`request_raw(method, url)` of a client sends a request outside the operations to an absolute URL and returns its raw response.

## Base URL and servers

Without `base_url`, a call sends to the first server generation resolved for its operation, with the default of each server variable: `https://api.example.com/v1` for `pets.list_pets`.
An operation's servers are the OpenAPI `servers` of the operation, its path item, or the document; one without them takes the `--client-default-base-url` of the generation.
`server=ServerSelection(index=..., variables={...})`, with `ServerSelection` from `client.options`, picks another declared server by position and supplies its variables; `base_url=` replaces the servers with one URL.
`Client`, `AsyncClient`, `with_options`, and `RequestOptions` take both, and the nearest one given wins: a call's over its view's, and a view's over the client's.
Giving both on one of them raises `ConfigurationError`.

## Errors

```python
from client import Client
from client.errors import APIConnectionError, APIStatusError, NotFoundError

with Client(base_url="https://api.example.com/v1") as client:
    try:
        result = client.pets.list_pets(X_Trace="...")
    except NotFoundError as error:
        print("not found:", error.body)
    except APIStatusError as error:
        print(error.status_code, error.body)
    except APIConnectionError as error:
        print(error)
```

A final status the operation does not declare as a success raises `APIStatusError`, or its subclass for the status: `BadRequestError` (400), `AuthenticationError` (401), `PermissionDeniedError` (403), `NotFoundError` (404), `ConflictError` (409), `UnprocessableEntityError` (422), `RateLimitError` (429), or `InternalServerError` (500 and above).
Its `body` is the payload decoded with the error schema the operation declares for the status, such as that of a `default` response, or else the bounded raw bytes; `status_code` and `headers` give the rest of the response.
A failure to reach the server raises `APIConnectionError`, after the retries it allows, and a timeout its subclass `APITimeoutError`.
Every exception of `client.errors` derives from `SDKError`; the runtime reference describes the others.

## Operations

`Client` and `AsyncClient` hold the operations in resource attributes, as `client.<resource>.<method>`; an `AsyncClient` method takes the same arguments.
Each method takes `**kwargs` typed as `Unpack` of a `TypedDict`, which `_generated/client_arguments.py` declares, and checks the keywords it receives before sending; the signatures below list those keywords.
A request body is given as `body=`, unless an operation's `body_arguments` is `both`.
An optional argument defaults to its schema's default, or to `UNSET`, which leaves it out of the request; `options=RequestOptions(...)` sets the settings of one call.
The signatures spell types as `resources/<resource>/_sync.py` imports them: from `client.options`, the result types from `client.types.<resource>`, `JSONValue`, the type of any JSON value, from `client.types`, the `models` models, and the other public modules.
`--client-operations` takes the operation reference of each as its key.
The `with_response`, `with_raw_response`, and `with_streaming_response` attributes of a resource hold the same methods, returning the result with its response metadata, the raw response, or a block that streams the response.

### `client.pets.list_pets`

`GET /pets`, operation reference `/paths/~1pets/get`

```python
def list_pets(
    *,
    limit: int | UNSET = UNSET,
    labels: list[str] | UNSET = UNSET,
    X_Trace: str,
    session: str | UNSET = UNSET,
    response_media_type: Literal['application/json'] | None = None,
    options: RequestOptions | None = None,
) -> ListPetsResponse: ...
```

### `client.pets.create_pet`

`POST /pets`, operation reference `/paths/~1pets/post`, request body as `body=` only (`application/json`, `text/plain`)

```python
def create_pet(
    *,
    body: models.NewPet | models.PetsPostRequest,
    media_type: Literal['application/json', 'text/plain'] | None = None,
    response_media_type: Literal['application/json'] | None = None,
    options: RequestOptions | None = None,
) -> CreatePetResponse: ...
```

`resources/pets/_sync.py` also declares overloads of the method, which narrow its body or its result by the media types a call chooses.

### `client.pets.get_pet`

`GET /pets/{petId}`, operation reference `/paths/~1pets~1{petId}/get`

```python
def get_pet(
    *,
    petId: int,
    response_media_type: Literal['application/json', 'text/plain'] | None = None,
    options: RequestOptions | None = None,
) -> GetPetResponse: ...
```

`resources/pets/_sync.py` also declares overloads of the method, which narrow its body or its result by the media types a call chooses.

### `client.pets.delete_pets_by_pet_id`

`DELETE /pets/{petId}`, operation reference `/paths/~1pets~1{petId}/delete`

```python
def delete_pets_by_pet_id(
    *,
    petId: int,
    options: RequestOptions | None = None,
) -> DeletePetsByPetIdResponse: ...
```

### `client.pets.head_pet`

`HEAD /pets/{petId}`, operation reference `/paths/~1pets~1{petId}/head`

```python
def head_pet(
    *,
    petId: int,
    options: RequestOptions | None = None,
) -> HeadPetResponse: ...
```

### `client.pets.photos.upload`

`PUT /pets/{petId}/photo`, operation reference `/paths/~1pets~1{petId}~1photo/put`, request body as `body=` only (`application/octet-stream`)

```python
def upload(
    *,
    petId: int,
    body: SyncBinaryBody | UNSET = UNSET,
    media_type: Literal['application/octet-stream'] | None = None,
    response_media_type: str | None = None,
    options: RequestOptions | None = None,
) -> UploadResponse: ...
```

### `client.pets.attach_files`

`POST /pets/{petId}/files`, operation reference `/paths/~1pets~1{petId}~1files/post`, request body as `body=` only (`multipart/form-data`)

```python
def attach_files(
    *,
    petId: int,
    body: MultipartBody[str | list[str]],
    media_type: Literal['multipart/form-data'] | None = None,
    options: RequestOptions | None = None,
) -> AttachFilesResponse: ...
```

### `client.pets.read_files`

`GET /pets/{petId}/files`, operation reference `/paths/~1pets~1{petId}~1files/get`

```python
def read_files(
    *,
    petId: int,
    response_media_type: Literal['multipart/form-data'] | None = None,
    options: RequestOptions | None = None,
) -> ReadFilesResponse: ...
```

## Dependencies

The package needs these distributions at run time:

- `httpx2>=2.13.0`
- `typing-extensions>=4.16`
- `pydantic>=2.14.0`

Add them to your project with the command each generation prints:

```console
uv add "httpx2>=2.13.0" "typing-extensions>=4.16" "pydantic>=2.14.0"
```

## Regenerating

Run the same generation again whenever the OpenAPI document changes; the `--check` option reports the files it would change without writing them.
Generation overwrites every generated file, restoring any you edited or deleted, and never deletes one: delete the modules of removed resources yourself, which `--check` lists as extra files and each generation warns about.
Each generation prints the `uv add` command that adds the runtime dependencies of the package to your project;
run it again when a new version of datamodel-code-generator changes them.

## Generation details

### Retries

The default is two retries, subject to operation safety, replayable input, delay, and the shared deadline.
The default status set is 408, 429, 500, 502, 503, and 504. Server retry delays are respected by default.
`retry=RetryOptions(respect_retry_after=False)` is an explicit application override that ignores those server hints;
give it deliberately to the client, a view, or a call. This package does not embed that override.

### Profile

| Setting | Value |
| --- | --- |
| `signature_style` | `unpack` |
| `body_arguments` | `body` |
| Model backend | `pydantic_v2.BaseModel` |
| Models | `models` |

### Selected operation contracts

These declarations come from the finalized operation selection and generation configuration. A key contract does
not guarantee exactly-once execution. An unsafe method needs a declared header and an active key to be replay-safe.
No vendor retry-header name is inferred. Security requirements preserve ordered OR
alternatives and their AND members; `[]` and `[{}]` remain distinct declared anonymous choices. Missing required
credentials fail before sending. `auth_challenge_less_401` is the explicit generation declaration at
`operations[].runtime.auth_challenge_less_401`; it is false by default and is not a client option.

```json
[
  {
    "operation": "pets.list_pets",
    "method": "GET",
    "path": "/pets",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "pets.create_pet",
    "method": "POST",
    "path": "/pets",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "pets.get_pet",
    "method": "GET",
    "path": "/pets/{petId}",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "pets.delete_pets_by_pet_id",
    "method": "DELETE",
    "path": "/pets/{petId}",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "pets.head_pet",
    "method": "HEAD",
    "path": "/pets/{petId}",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "pets.photos.upload",
    "method": "PUT",
    "path": "/pets/{petId}/photo",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "pets.attach_files",
    "method": "POST",
    "path": "/pets/{petId}/files",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "pets.read_files",
    "method": "GET",
    "path": "/pets/{petId}/files",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  }
]
```

### Capabilities

| Capability | Declared |
| --- | --- |
| Security | none |
| Helpers | none |

The package copies the runtime modules its capabilities and model backend need:

- `_runtime`: `__init__.py`
- `_runtime/client`: `__init__.py`, `arguments.py`, `bodies.py`, `body_sources.py`, `client.py`, `codecs.py`, `content.py`, `errors.py`, `logical.py`, `media.py`, `multipart.py`, `multipart_responses.py`, `native.py`, `operations.py`, `options.py`, `parts.py`, `paths.py`, `positions.py`, `raw.py`, `responses.py`, `retry.py`, `timing.py`, `urls.py`
- `_runtime/model_codecs`: `__init__.py`, `errors.py`, `media.py`, `parameter_reads.py`, `parameters.py`, `pydantic_adapters.py`, `pydantic_models.py`, `unset.py`
