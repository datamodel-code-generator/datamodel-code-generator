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

- `rooms.room_socket`: `GET /rooms/{room}/socket`, no request body
- `feed.feed_socket`: `GET /feed/socket`, no request body
- `secure.secure_socket`: `GET /secure/socket`, no request body

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
    "operation": "rooms.room_socket",
    "method": "GET",
    "path": "/rooms/{room}/socket",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "feed.feed_socket",
    "method": "GET",
    "path": "/feed/socket",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "secure.secure_socket",
    "method": "GET",
    "path": "/secure/socket",
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

## Protocol helpers

`client.protocols` holds the protocol helpers below by their dotted names, on `Client` and `AsyncClient` alike.
A WebSocket helper's `connect` sends its operation's handshake in a session of its own once the server answers 101:
`with client.protocols.<name>.connect(...) as session:` on `Client`, or `async with` on `AsyncClient`, whose session
lives for its block. The session sends and receives typed messages, and leaving the block, `close()`, or `aclose()`
closes the connection.
See the runtime reference for their limits.

```json
[
  {
    "helper": "rooms.chat",
    "kind": "websocket",
    "operation": "rooms.room_socket",
    "method": "GET",
    "path": "/rooms/{room}/socket"
  },
  {
    "helper": "feed.text",
    "kind": "websocket",
    "operation": "feed.feed_socket",
    "method": "GET",
    "path": "/feed/socket"
  },
  {
    "helper": "secure.chat",
    "kind": "websocket",
    "operation": "secure.secure_socket",
    "method": "GET",
    "path": "/secure/socket"
  }
]
```

## Dependencies

The package needs these distributions at run time:

- `httpx2[ws]>=2.13.0`
- `typing-extensions>=4.16`
- `pydantic>=2.13.5`

Each generation reports them; add them to your project, for example with `uv add`.

## Capabilities

| Capability | Declared |
| --- | --- |
| Security | `bearer` |
| Helpers | `websocket` |

The package copies the runtime modules its capabilities and model backend need:

- `_runtime`: `__init__.py`
- `_runtime/client`: `__init__.py`, `auth.py`, `client.py`, `content.py`, `errors.py`, `logical.py`, `media.py`, `native.py`, `operations.py`, `options.py`, `paths.py`, `positions.py`, `raw.py`, `responses.py`, `retry.py`, `security.py`, `timing.py`, `urls.py`
- `_runtime/model_codecs`: `__init__.py`, `errors.py`, `media.py`, `native.py`, `parameters.py`, `unset.py`
- `_runtime/protocols`: `__init__.py`, `caches.py`, `client.py`, `errors.py`, `options.py`, `origins.py`, `records.py`, `references.py`, `websocket.py`
