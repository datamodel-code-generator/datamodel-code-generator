# client

The `client` package is an HTTPX2 client of the API the OpenAPI document describes.
datamodel-code-generator generated it from the document, together with the `models` models of the `pydantic_v2.BaseModel` backend.
Generation overwrites every file of the package, as it does the models: keep your code outside the package and regenerate the package instead of editing it; see [Regenerating](#regenerating).

HTTPX2 client generation is experimental.
The [runtime reference](_generated_docs/runtime.md) describes the settings, defaults, ownership, cancellation, replay, redirects, and transport costs of the clients.

## Quick start

```python
from client import Client

with Client(base_url="https://api.example.com") as client:
    result = client.default.create_pet(body=...)
```

`AsyncClient` has the same resources and methods, and each method is a coroutine:

```python
from client import AsyncClient


async def main() -> None:
    async with AsyncClient(base_url="https://api.example.com") as client:
        result = await client.default.create_pet(body=...)
```

Replace each `...` with a value of the type its argument declares; [Operations](#operations) lists the arguments of each method.
A method returns the decoded body of a success response, of the type its signature declares.
Leaving the `with` or `async with` block closes the client, as `close()` or `aclose()` does; keep a streaming response only inside its own context.
Import options, bodies, errors, and response types from the public modules of the package.
`request_raw(method, url)` of a client sends a request outside the operations to an absolute URL and returns its raw response.

## Base URL and servers

Without `base_url`, a call sends to the first server generation resolved for its operation, with the default of each server variable: `https://api.example.com` for `default.create_pet`.
An operation's servers are the OpenAPI `servers` of the operation, its path item, or the document; one without them takes the `--client-default-base-url` of the generation.
`server=ServerSelection(index=..., variables={...})`, with `ServerSelection` from `client.options`, picks another declared server by position and supplies its variables; `base_url=` replaces the servers with one URL.
`Client`, `AsyncClient`, `with_options`, and `RequestOptions` take both, and the nearest one given wins: a call's over its view's, and a view's over the client's.
Giving both on one of them raises `ConfigurationError`.

## Errors

```python
from client import Client
from client.errors import APIConnectionError, APIStatusError, NotFoundError

with Client(base_url="https://api.example.com") as client:
    try:
        result = client.default.create_pet(body=...)
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
Each method declares its parameters, its body, and its options as keyword arguments.
A request body whose media is JSON or a URL-encoded form of an object model is given as `body=` or as the keyword arguments of its fields, unless an operation's `body_arguments` is `body`.
An optional argument defaults to its schema's default, or to `UNSET`, which leaves it out of the request; `options=RequestOptions(...)` sets the settings of one call.
The signatures spell types as `resources/<resource>/_sync.py` imports them: from `client.options`, the result types from `client.types.<resource>`, `JSONValue`, the type of any JSON value, from `client.types`, the `models` models, and the other public modules.
`--client-operations` takes the operation reference of each as its key.
The `with_response`, `with_raw_response`, and `with_streaming_response` attributes of a resource hold the same methods, returning the result with its response metadata, the raw response, or a block that streams the response.

### `client.default.create_pet`

`POST /pets`, operation reference `/paths/~1pets/post`, request body arguments `both`:

- `application/json`: `body=` or the fields `name` (required), `kind` (required), `pet_tag`, `birthDate`, `owner`, `secret`
- `application/x-www-form-urlencoded`: `body=` or the fields `name` (required), `pet_tag`

```python
def create_pet(
    *,
    tag: str | UNSET = UNSET,
    body: models.NewPet | models.PetForm | UNSET = UNSET,
    name: str | UNSET = UNSET,
    kind: models.Kind | UNSET = UNSET,
    pet_tag: str | None | UNSET = UNSET,
    birthDate: date | UNSET = UNSET,
    owner: models.Owner | UNSET = UNSET,
    secret: str | UNSET = UNSET,
    media_type: Literal['application/json', 'application/x-www-form-urlencoded'] | None = None,
    response_media_type: Literal['application/json'] | None = None,
    options: RequestOptions | None = None,
) -> CreatePetResponse: ...
```

`resources/default/_sync.py` also declares overloads of the method, which narrow its body or its result by the media types a call chooses.

### `client.default.update_pet`

`PATCH /pets/{petId}`, operation reference `/paths/~1pets~1{petId}/patch`, request body arguments `both`:

- `application/json`: `body=` or the fields `name`, `tag`

```python
def update_pet(
    *,
    petId: int,
    body: models.PetPatch | UNSET = UNSET,
    name: str | UNSET = UNSET,
    tag: str | None | UNSET = UNSET,
    media_type: Literal['application/json'] | None = None,
    options: RequestOptions | None = None,
) -> UpdatePetResponse: ...
```

`resources/default/_sync.py` also declares overloads of the method, which narrow its body or its result by the media types a call chooses.

### `client.default.log_visit`

`POST /pets/{petId}/visits`, operation reference `/paths/~1pets~1{petId}~1visits/post`, request body arguments `both`:

- `application/json`: `body=` or the fields `note`, `visit_options`
- `text/plain`: `body=` only, since only JSON and URL-encoded form bodies have field arguments

```python
def log_visit(
    *,
    petId: int,
    body: models.Visit | str | UNSET = UNSET,
    note: str | UNSET = UNSET,
    visit_options: list[str] | UNSET = UNSET,
    media_type: Literal['application/json', 'text/plain'] | None = None,
    options: RequestOptions | None = None,
) -> LogVisitResponse: ...
```

`resources/default/_sync.py` also declares overloads of the method, which narrow its body or its result by the media types a call chooses.

### `client.default.set_owner`

`PUT /pets/{petId}/owner`, operation reference `/paths/~1pets~1{petId}~1owner/put`, request body as `body=` only (`application/json`)

```python
def set_owner(
    *,
    petId: int,
    body: models.Owner | UNSET = UNSET,
    media_type: Literal['application/json'] | None = None,
    options: RequestOptions | None = None,
) -> SetOwnerResponse: ...
```

### `client.default.create_owner`

`POST /owners`, operation reference `/paths/~1owners/post`, request body arguments `both`:

- `application/json`: `body=` or the fields `email` (required), `nickName`

```python
def create_owner(
    *,
    body: models.OwnersPostRequest | UNSET = UNSET,
    email: str | UNSET = UNSET,
    nickName: str | UNSET = UNSET,
    media_type: Literal['application/json'] | None = None,
    options: RequestOptions | None = None,
) -> CreateOwnerResponse: ...
```

`resources/default/_sync.py` also declares overloads of the method, which narrow its body or its result by the media types a call chooses.

### `client.default.put_labels`

`PUT /pets/{petId}/labels`, operation reference `/paths/~1pets~1{petId}~1labels/put`, request body arguments `both`:

- `application/json`: `body=` or the fields `size`

```python
def put_labels(
    *,
    petId: int,
    body: models.Labels | UNSET = UNSET,
    size: str | UNSET = UNSET,
    media_type: Literal['application/json'] | None = None,
    options: RequestOptions | None = None,
) -> PutLabelsResponse: ...
```

`resources/default/_sync.py` also declares overloads of the method, which narrow its body or its result by the media types a call chooses.

### `client.default.put_photo`

`PUT /pets/{petId}/photo`, operation reference `/paths/~1pets~1{petId}~1photo/put`, request body arguments `both`:

- `multipart/form-data`: `body=` only, since only JSON and URL-encoded form bodies have field arguments

```python
def put_photo(
    *,
    petId: int,
    body: MultipartBody[str | JSONValue],
    media_type: Literal['multipart/form-data'] | None = None,
    options: RequestOptions | None = None,
) -> PutPhotoResponse: ...
```

### `client.default.replace_pet`

`PUT /pets/{petId}/records`, operation reference `/paths/~1pets~1{petId}~1records/put`, request body arguments `both`:

- `application/json`: `body=` or the fields `name` (required), `tag`

```python
def replace_pet(
    *,
    petId: int,
    body: models.Pet | UNSET = UNSET,
    name: str | UNSET = UNSET,
    tag: str | None | UNSET = UNSET,
    media_type: Literal['application/json'] | None = None,
    options: RequestOptions | None = None,
) -> ReplacePetResponse: ...
```

`resources/default/_sync.py` also declares overloads of the method, which narrow its body or its result by the media types a call chooses.

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
| `signature_style` | `explicit` |
| `body_arguments` | `both` |
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
    "operation": "default.create_pet",
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
    "operation": "default.update_pet",
    "method": "PATCH",
    "path": "/pets/{petId}",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "default.log_visit",
    "method": "POST",
    "path": "/pets/{petId}/visits",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "default.set_owner",
    "method": "PUT",
    "path": "/pets/{petId}/owner",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "default.create_owner",
    "method": "POST",
    "path": "/owners",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "default.put_labels",
    "method": "PUT",
    "path": "/pets/{petId}/labels",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "default.put_photo",
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
    "operation": "default.replace_pet",
    "method": "PUT",
    "path": "/pets/{petId}/records",
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
- `_runtime/client`: `__init__.py`, `bodies.py`, `body_sources.py`, `client.py`, `content.py`, `errors.py`, `logical.py`, `media.py`, `multipart.py`, `native.py`, `operations.py`, `options.py`, `parts.py`, `paths.py`, `positions.py`, `raw.py`, `responses.py`, `retry.py`, `timing.py`, `urls.py`
- `_runtime/model_codecs`: `__init__.py`, `errors.py`, `media.py`, `parameters.py`, `pydantic_adapters.py`, `pydantic_models.py`, `unset.py`
