# client

This generated package exposes `Client` and `AsyncClient`. Import options, errors, and response types from
its public modules. Operations are grouped into the resource attributes listed below; `request_raw` accepts an
explicit URL. Close clients with `with` or `async with`, and retain streaming responses only inside their context.

```python
from client import Client

with Client(max_retries=0) as client:
    response = client.request_raw("GET", "https://api.example.com/health")
    status = response.info.status_code
```

Replace the example URL with your service. The explicit `max_retries=0` disables resends for this example.
The default is two retries, subject to operation safety, replayable input, delay, and the shared deadline.
The default status set is 408, 429, 500, 502, 503, and 504. Server retry delays are respected by default.
`retry=RetryOptions(respect_retry_after=False)` is an explicit application override that ignores those server hints;
give it deliberately to the client, a view, or a call. This package does not embed that override.

See the [runtime reference](runtime.md) for defaults, ownership, cancellation, replay, redirects, and transport costs.

## Profile

| Setting | Value |
| --- | --- |
| `signature_style` | `explicit` |
| `body_arguments` | `body` |
| Model backend | `pydantic_v2.BaseModel` |
| Models | `models` |

Each operation method declares its parameters, its body, and its options as keyword arguments.
A request body is given as `body=`, unless an operation's `body_arguments` is `both`.

## Operations

- `users.list_users`: `GET /users`, no request body
- `users.create_user`: `POST /users`, `body=` only (`application/json`)
- `searches.search`: `POST /searches`, `body=` only (`application/json`)
- `finds.find`: `GET /finds`, no request body

## Selected operation contracts

These declarations come from the finalized operation selection and generation configuration. A key contract does
not guarantee exactly-once execution. An unsafe method needs a declared header and an active key to be replay-safe.
No vendor retry-header name is inferred. Security requirements preserve ordered OR
alternatives and their AND members; `[]` and `[{}]` remain distinct declared anonymous choices. Missing required
credentials fail before sending. `auth_challenge_less_401` is the explicit generation declaration at
`operations[].runtime.auth_challenge_less_401`; it is false by default and is not a client option.

```json
[
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
    "operation": "searches.search",
    "method": "POST",
    "path": "/searches",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "finds.find",
    "method": "GET",
    "path": "/finds",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  }
]
```

## Protocol helpers

`client.protocols` holds the protocol helpers below by their dotted names, on `Client` and `AsyncClient` alike.
A pagination helper's `page` fetches the first page and `next_page` the page after one it returned, each in a
session of its own; `iterate` returns a pager, which sends nothing until it is iterated and fetches each page only
once the previous one is consumed, and `resume` returns one continuing a pager's `checkpoint()`. See the runtime
reference for their limits and checkpoints.

```json
[
  {
    "helper": "users.offsets",
    "kind": "pagination",
    "operation": "users.list_users",
    "method": "GET",
    "path": "/users"
  },
  {
    "helper": "users.items",
    "kind": "pagination",
    "operation": "users.list_users",
    "method": "GET",
    "path": "/users"
  },
  {
    "helper": "users.loose",
    "kind": "pagination",
    "operation": "users.list_users",
    "method": "GET",
    "path": "/users"
  },
  {
    "helper": "users.pages",
    "kind": "pagination",
    "operation": "users.list_users",
    "method": "GET",
    "path": "/users"
  },
  {
    "helper": "users.counted",
    "kind": "pagination",
    "operation": "users.list_users",
    "method": "GET",
    "path": "/users"
  },
  {
    "helper": "users.by_header",
    "kind": "pagination",
    "operation": "users.list_users",
    "method": "GET",
    "path": "/users"
  },
  {
    "helper": "searches.all",
    "kind": "pagination",
    "operation": "searches.search",
    "method": "POST",
    "path": "/searches"
  },
  {
    "helper": "finds.all",
    "kind": "pagination",
    "operation": "finds.find",
    "method": "GET",
    "path": "/finds"
  },
  {
    "helper": "users.positions",
    "kind": "pagination",
    "operation": "users.list_users",
    "method": "GET",
    "path": "/users"
  }
]
```

## Dependencies

The package needs these distributions at run time:

- `httpx2>=2.13.0`
- `typing-extensions>=4.16`
- `pydantic>=2.13.5`

Each generation reports them; add them to your project, for example with `uv add`.

## Capabilities

| Capability | Declared |
| --- | --- |
| Security | none |
| Helpers | `pagination` |

The package copies the runtime modules its capabilities and model backend need:

- `_runtime`: `__init__.py`
- `_runtime/client`: `__init__.py`, `client.py`, `codecs.py`, `content.py`, `errors.py`, `logical.py`, `media.py`, `native.py`, `operations.py`, `options.py`, `paths.py`, `positions.py`, `raw.py`, `responses.py`, `retry.py`, `timing.py`, `urls.py`
- `_runtime/model_codecs`: `__init__.py`, `errors.py`, `media.py`, `native.py`, `parameter_reads.py`, `parameters.py`, `unset.py`
- `_runtime/protocols`: `__init__.py`, `caches.py`, `client.py`, `errors.py`, `links.py`, `options.py`, `origins.py`, `pagination.py`, `records.py`, `references.py`, `values.py`, `writes.py`
