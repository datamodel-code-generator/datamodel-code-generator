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
- `users.search_users`: `POST /users/search`, `body=` only (`application/json`)
- `loose.list_loose`: `GET /loose`, no request body
- `nested.list_nested`: `GET /nested`, no request body
- `labels.list_labels`: `GET /labels`, no request body
- `labels.list_label_sets`: `GET /label-sets`, no request body
- `archive.list_archive`: `GET /archive/{cursor}`, no request body
- `statuses.list_statuses`: `GET /statuses`, no request body
- `secure.list_secure_users`: `GET /secure/users`, no request body

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
    "operation": "users.search_users",
    "method": "POST",
    "path": "/users/search",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "loose.list_loose",
    "method": "GET",
    "path": "/loose",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "nested.list_nested",
    "method": "GET",
    "path": "/nested",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "labels.list_labels",
    "method": "GET",
    "path": "/labels",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "labels.list_label_sets",
    "method": "GET",
    "path": "/label-sets",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "archive.list_archive",
    "method": "GET",
    "path": "/archive/{cursor}",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "statuses.list_statuses",
    "method": "GET",
    "path": "/statuses",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "secure.list_secure_users",
    "method": "GET",
    "path": "/secure/users",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {
        "oauth": [
          "users.read"
        ]
      }
    ],
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
    "helper": "users.all",
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
    "helper": "users.search",
    "kind": "pagination",
    "operation": "users.search_users",
    "method": "POST",
    "path": "/users/search"
  },
  {
    "helper": "loose.all",
    "kind": "pagination",
    "operation": "loose.list_loose",
    "method": "GET",
    "path": "/loose"
  },
  {
    "helper": "loose.tokens",
    "kind": "pagination",
    "operation": "loose.list_loose",
    "method": "GET",
    "path": "/loose"
  },
  {
    "helper": "nested.all",
    "kind": "pagination",
    "operation": "nested.list_nested",
    "method": "GET",
    "path": "/nested"
  },
  {
    "helper": "labels.all",
    "kind": "pagination",
    "operation": "labels.list_labels",
    "method": "GET",
    "path": "/labels"
  },
  {
    "helper": "labels.sets",
    "kind": "pagination",
    "operation": "labels.list_label_sets",
    "method": "GET",
    "path": "/label-sets"
  },
  {
    "helper": "archive.all",
    "kind": "pagination",
    "operation": "archive.list_archive",
    "method": "GET",
    "path": "/archive/{cursor}"
  },
  {
    "helper": "statuses.all",
    "kind": "pagination",
    "operation": "statuses.list_statuses",
    "method": "GET",
    "path": "/statuses"
  },
  {
    "helper": "secure.users",
    "kind": "pagination",
    "operation": "secure.list_secure_users",
    "method": "GET",
    "path": "/secure/users"
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
| Security | `bearer`, `client_credentials` |
| Helpers | `pagination` |

The package copies the runtime modules its capabilities and model backend need:

- `_runtime`: `__init__.py`
- `_runtime/client`: `__init__.py`, `auth.py`, `client.py`, `codecs.py`, `content.py`, `errors.py`, `logical.py`, `media.py`, `native.py`, `oauth.py`, `operations.py`, `options.py`, `paths.py`, `positions.py`, `raw.py`, `responses.py`, `retry.py`, `security.py`, `timing.py`, `urls.py`
- `_runtime/model_codecs`: `__init__.py`, `errors.py`, `media.py`, `native.py`, `parameter_reads.py`, `parameters.py`, `unset.py`
- `_runtime/protocols`: `__init__.py`, `caches.py`, `client.py`, `errors.py`, `links.py`, `options.py`, `origins.py`, `pagination.py`, `records.py`, `references.py`, `values.py`, `writes.py`
