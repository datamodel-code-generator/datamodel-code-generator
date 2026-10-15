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
    result = client.users.get_user(userId=1)
```

`AsyncClient` has the same resources and methods, and each method is a coroutine:

```python
from client import AsyncClient


async def main() -> None:
    async with AsyncClient(base_url="https://api.example.com") as client:
        result = await client.users.get_user(userId=1)
```

A method returns the decoded body of a success response, of the type its signature declares.
Leaving the `with` or `async with` block closes the client, as `close()` or `aclose()` does; keep a streaming response only inside its own context.
Import options, errors, and response types from the public modules of the package.
`request_raw(method, url)` of a client sends a request outside the operations to an absolute URL and returns its raw response.

`Client` and `AsyncClient` take one keyword argument per security scheme an operation requires: `bearer` (bearer token).
See [Authentication](_generated_docs/runtime.md#authentication) in the runtime reference.

## Base URL and servers

Without `base_url`, a call sends to the first server generation resolved for its operation, with the default of each server variable: `https://api.example.com` for `users.get_user`.
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
        result = client.users.get_user(userId=1)
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
A request body is given as `body=`, unless an operation's `body_arguments` is `both`.
An optional argument defaults to its schema's default, or to `UNSET`, which leaves it out of the request; `options=RequestOptions(...)` sets the settings of one call.
The signatures spell types as `resources/<resource>/_sync.py` imports them: from `client.options`, the result types from `client.types.<resource>`, `JSONValue`, the type of any JSON value, from `client.types`, the `models` models, and the other public modules.
`--client-operations` takes the operation reference of each as its key.
The `with_response`, `with_raw_response`, and `with_streaming_response` attributes of a resource hold the same methods, returning the result with its response metadata, the raw response, or a block that streams the response.

### `client.users.get_user`

`GET /users/{userId}`, operation reference `/paths/~1users~1{userId}/get`

```python
def get_user(
    *,
    fields: str | UNSET = UNSET,
    Accept_Language: str | UNSET = UNSET,
    userId: int,
    response_media_type: Literal['application/json'] | None = None,
    options: RequestOptions | None = None,
) -> GetUserResponse: ...
```

### `client.users.check_user`

`HEAD /users/{userId}`, operation reference `/paths/~1users~1{userId}/head`

```python
def check_user(
    *,
    userId: int,
    options: RequestOptions | None = None,
) -> CheckUserResponse: ...
```

### `client.users.rename_user`

`PATCH /users/{userId}`, operation reference `/paths/~1users~1{userId}/patch`, request body as `body=` only (`application/json`)

```python
def rename_user(
    *,
    userId: int,
    body: models.UserPatch,
    media_type: Literal['application/json'] | None = None,
    response_media_type: Literal['application/json'] | None = None,
    options: RequestOptions | None = None,
) -> RenameUserResponse: ...
```

### `client.users.delete_user`

`DELETE /users/{userId}`, operation reference `/paths/~1users~1{userId}/delete`

```python
def delete_user(
    *,
    userId: int,
    options: RequestOptions | None = None,
) -> DeleteUserResponse: ...
```

### `client.users.list_users`

`GET /users`, operation reference `/paths/~1users/get`

```python
def list_users(
    *,
    page: int | UNSET = UNSET,
    role: list[str] | UNSET = UNSET,
    response_media_type: Literal['application/json'] | None = None,
    options: RequestOptions | None = None,
) -> ListUsersResponse: ...
```

### `client.users.create_user`

`POST /users`, operation reference `/paths/~1users/post`, request body as `body=` only (`application/json`)

```python
def create_user(
    *,
    body: models.UserPatch,
    media_type: Literal['application/json'] | None = None,
    response_media_type: Literal['application/json'] | None = None,
    options: RequestOptions | None = None,
) -> CreateUserResponse: ...
```

### `client.carts.get_current_cart`

`GET /carts/current`, operation reference `/paths/~1carts~1current/get`

```python
def get_current_cart(
    *,
    cart: str,
    response_media_type: Literal['application/json'] | None = None,
    options: RequestOptions | None = None,
) -> GetCurrentCartResponse: ...
```

### `client.secure.get_secure_user`

`GET /secure/users/{userId}`, operation reference `/paths/~1secure~1users~1{userId}/get`

```python
def get_secure_user(
    *,
    userId: int,
    response_media_type: Literal['application/json'] | None = None,
    options: RequestOptions | None = None,
) -> GetSecureUserResponse: ...
```

## Protocol helpers

`client.protocols` holds the protocol helpers below by their dotted names, on `Client` and `AsyncClient` alike.
A cache helper's `fetch` answers from a fresh entry of the store the client's `cache_stores` lends it, or of a memory
store the client creates, or sends the request, revalidating a stale entry. See the runtime reference for their limits
and keys.

```json
[
  {
    "helper": "users.profile",
    "kind": "cache",
    "operation": "users.get_user",
    "method": "GET",
    "path": "/users/{userId}"
  },
  {
    "helper": "users.dated",
    "kind": "cache",
    "operation": "users.get_user",
    "method": "GET",
    "path": "/users/{userId}"
  },
  {
    "helper": "users.listing",
    "kind": "cache",
    "operation": "users.list_users",
    "method": "GET",
    "path": "/users"
  },
  {
    "helper": "carts.current",
    "kind": "cache",
    "operation": "carts.get_current_cart",
    "method": "GET",
    "path": "/carts/current"
  },
  {
    "helper": "secure.profile",
    "kind": "cache",
    "operation": "secure.get_secure_user",
    "method": "GET",
    "path": "/secure/users/{userId}"
  }
]
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
| `signature_style` | `explicit` |
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
    "operation": "users.get_user",
    "method": "GET",
    "path": "/users/{userId}",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "users.check_user",
    "method": "HEAD",
    "path": "/users/{userId}",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "users.rename_user",
    "method": "PATCH",
    "path": "/users/{userId}",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "users.delete_user",
    "method": "DELETE",
    "path": "/users/{userId}",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "users.list_users",
    "method": "GET",
    "path": "/users",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "users.create_user",
    "method": "POST",
    "path": "/users",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "carts.get_current_cart",
    "method": "GET",
    "path": "/carts/current",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "secure.get_secure_user",
    "method": "GET",
    "path": "/secure/users/{userId}",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {
        "bearer": []
      }
    ],
    "auth_challenge_less_401": false
  }
]
```

### Capabilities

| Capability | Declared |
| --- | --- |
| Security | `bearer` |
| Helpers | `cache` |

The package copies the runtime modules its capabilities and model backend need:

- `_runtime`: `__init__.py`
- `_runtime/client`: `__init__.py`, `auth.py`, `client.py`, `content.py`, `errors.py`, `logical.py`, `media.py`, `native.py`, `operations.py`, `options.py`, `paths.py`, `positions.py`, `raw.py`, `responses.py`, `retry.py`, `security.py`, `timing.py`, `urls.py`
- `_runtime/model_codecs`: `__init__.py`, `errors.py`, `media.py`, `parameters.py`, `pydantic_adapters.py`, `pydantic_models.py`, `unset.py`
- `_runtime/protocols`: `__init__.py`, `cache.py`, `cache_stores.py`, `caches.py`, `client.py`, `errors.py`, `options.py`, `origins.py`, `records.py`, `references.py`
