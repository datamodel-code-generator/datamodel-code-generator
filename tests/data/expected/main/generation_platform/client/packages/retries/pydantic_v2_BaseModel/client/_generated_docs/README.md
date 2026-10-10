# client

This generated package exposes `Client` and `AsyncClient`. Import options, bodies, errors, and response types from
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

- `retry.get_safe`: `GET /safe`, no request body
- `retry.post_unsafe`: `POST /unsafe`, `body=` only (`application/octet-stream`)
- `retry.post_idempotent`: `POST /idempotent`, `body=` only (`application/octet-stream`)
- `retry.post_keyed`: `POST /keyed`, `body=` only (`application/octet-stream`)
- `retry.post_key_only`: `POST /key-only`, `body=` only (`application/octet-stream`)
- `retry.get_never`: `GET /never`, no request body
- `retry.post_never`: `POST /never`, `body=` only (`application/octet-stream`)
- `retry.get_vendor`: `GET /vendor`, no request body
- `retry.get_keyed_safe`: `GET /keyed-safe`, no request body

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
    "operation": "retry.get_safe",
    "method": "GET",
    "path": "/safe",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "retry.post_unsafe",
    "method": "POST",
    "path": "/unsafe",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "retry.post_idempotent",
    "method": "POST",
    "path": "/idempotent",
    "retry_safety": "idempotent",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "retry.post_keyed",
    "method": "POST",
    "path": "/keyed",
    "retry_safety": "method_default",
    "idempotency": {
      "header_name": "Idempotency-Key"
    },
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "retry.post_key_only",
    "method": "POST",
    "path": "/key-only",
    "retry_safety": "method_default",
    "idempotency": {
      "header_name": "Idempotency-Key"
    },
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "retry.get_never",
    "method": "GET",
    "path": "/never",
    "retry_safety": "never",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "retry.post_never",
    "method": "POST",
    "path": "/never",
    "retry_safety": "never",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "retry.get_vendor",
    "method": "GET",
    "path": "/vendor",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": "X-Retry-In-Ms",
    "should_retry_header": "X-Retry-Permitted",
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "retry.get_keyed_safe",
    "method": "GET",
    "path": "/keyed-safe",
    "retry_safety": "method_default",
    "idempotency": {
      "header_name": "Idempotency-Key"
    },
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
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
| Helpers | none |

The package copies the runtime modules its capabilities and model backend need:

- `_runtime`: `__init__.py`
- `_runtime/client`: `__init__.py`, `bodies.py`, `body_sources.py`, `client.py`, `content.py`, `errors.py`, `logical.py`, `media.py`, `native.py`, `operations.py`, `options.py`, `paths.py`, `positions.py`, `raw.py`, `responses.py`, `retry.py`, `timing.py`, `urls.py`
- `_runtime/model_codecs`: `__init__.py`, `errors.py`, `media.py`, `native.py`, `parameters.py`, `unset.py`
