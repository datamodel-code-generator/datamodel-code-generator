# client

The `client` package is an HTTPX2 client of the API the AsyncAPI document describes.
datamodel-code-generator generated it from the document, together with the `models` models of the `pydantic_v2.BaseModel` backend.
Generation overwrites every file of the package, as it does the models: keep your code outside the package and regenerate the package instead of editing it; see [Regenerating](#regenerating).

HTTPX2 client generation is experimental.
The [runtime reference](_generated_docs/runtime.md) describes the settings, defaults, ownership, cancellation, replay, redirects, and transport costs of the clients.

## Quick start

```python
from client import Client

with Client(base_url="https://feed.example.com/ws") as client:
    with client.market.connect(pair="...") as session:
        for message in session:
            print(message.data)
```

`AsyncClient` has the same protocol helpers, used with `async with` and `async for`:

```python
from client import AsyncClient


async def main() -> None:
    async with AsyncClient(base_url="https://feed.example.com/ws") as client:
        async with client.market.connect(pair="...") as session:
            async for message in session:
                print(message.data)
```

Replace each `...` with a value of the type its argument declares.
Each WebSocket channel of the API is an attribute of the client, such as `market`, whose `connect` opens it, as [Protocol helpers](#protocol-helpers) describes.
Leaving the `with` or `async with` block closes the client, as `close()` or `aclose()` does; keep a streaming response only inside its own context.
Import options, errors, and response types from the public modules of the package.
`request_raw(method, url)` of a client sends a request outside the operations to an absolute URL and returns its raw response.

## Base URL and servers

Without `base_url`, a call sends to the first server generation resolved for its operation, with the default of each server variable: `https://feed.example.com/ws` for `/channels/market`.
A channel's servers are the `ws` and `wss` servers of the AsyncAPI document that serve it, as `http` and `https` URLs, which its handshake is sent to.
`server=ServerSelection(index=..., variables={...})`, with `ServerSelection` from `client.options`, picks another declared server by position and supplies its variables; `base_url=` replaces the servers with one URL.
`Client`, `AsyncClient`, `with_options`, and `RequestOptions` take both, and the nearest one given wins: a call's over its view's, and a view's over the client's.
Giving both on one of them raises `ConfigurationError`.

## Errors

```python
from client import Client
from client.errors import APIConnectionError, APIStatusError, NotFoundError

with Client(base_url="https://feed.example.com/ws") as client:
    try:
        with client.market.connect(pair="...") as session:
            for message in session:
                print(message.data)
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

## Protocol helpers

`client.protocols` holds the protocol helpers below by their dotted names, on `Client` and `AsyncClient` alike.
Each is the helper of an AsyncAPI channel, which the client also holds as `client.<name>`; generation read the document as its server's (`--client-asyncapi-perspective server`), so on a WebSocket channel the client sends what the server receives and receives what it sends.
A WebSocket helper's `connect` sends its operation's handshake in a session of its own once the server answers 101:
`with client.protocols.<name>.connect(...) as session:` on `Client`, or `async with` on `AsyncClient`, whose session
lives for its block. The session sends and receives typed messages, and leaving the block, `close()`, or `aclose()`
closes the connection.
See the runtime reference for their limits.

```json
[
  {
    "helper": "market",
    "kind": "websocket",
    "operation": "/channels/market",
    "method": "GET",
    "path": "/v1/market/{pair}"
  },
  {
    "helper": "ticker",
    "kind": "websocket",
    "operation": "/channels/ticker",
    "method": "GET",
    "path": "/v1/ticker"
  },
  {
    "helper": "audio",
    "kind": "websocket",
    "operation": "/channels/audio",
    "method": "GET",
    "path": "/v1/audio"
  },
  {
    "helper": "status",
    "kind": "websocket",
    "operation": "/channels/status",
    "method": "GET",
    "path": "/v1/status"
  }
]
```

## Dependencies

The package needs these distributions at run time:

- `httpx2[ws]>=2.13.0`
- `typing-extensions>=4.16`
- `pydantic>=2.14.0`

Add them to your project with the command each generation prints:

```console
uv add "httpx2[ws]>=2.13.0" "typing-extensions>=4.16" "pydantic>=2.14.0"
```

## Regenerating

Run the same generation again whenever the AsyncAPI document changes; the `--check` option reports the files it would change without writing them.
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
    "operation": "/channels/market",
    "method": "GET",
    "path": "/v1/market/{pair}",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "/channels/ticker",
    "method": "GET",
    "path": "/v1/ticker",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "/channels/audio",
    "method": "GET",
    "path": "/v1/audio",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "/channels/status",
    "method": "GET",
    "path": "/v1/status",
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
| Helpers | `websocket` |

The package copies the runtime modules its capabilities and model backend need:

- `_runtime`: `__init__.py`
- `_runtime/client`: `__init__.py`, `client.py`, `content.py`, `errors.py`, `logical.py`, `media.py`, `native.py`, `operations.py`, `options.py`, `paths.py`, `positions.py`, `raw.py`, `responses.py`, `retry.py`, `timing.py`, `urls.py`
- `_runtime/model_codecs`: `__init__.py`, `errors.py`, `media.py`, `parameters.py`, `pydantic_adapters.py`, `pydantic_models.py`, `shared.py`, `unset.py`
- `_runtime/protocols`: `__init__.py`, `client.py`, `errors.py`, `options.py`, `origins.py`, `records.py`, `references.py`, `routes.py`, `values.py`, `websocket.py`
