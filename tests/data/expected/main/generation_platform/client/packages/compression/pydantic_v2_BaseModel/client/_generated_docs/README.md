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

- `items.create_item`: `POST /items`, `body=` only (`application/json`)
- `items.list_items`: `GET /items`, no request body
- `items.create_note`: `POST /notes`, `body=` only (`application/json`)
- `items.put_blob`: `PUT /blobs`, `body=` only (`application/octet-stream`)
- `items.search`: `POST /search`, `body=` only (`application/json`)
- `items.feed`: `POST /feed`, `body=` only (`application/json`)
- `jobs.create_job`: `POST /jobs`, `body=` only (`application/json`)
- `jobs.get_job`: `GET /jobs/{jobId}`, no request body
- `checks.create_check`: `POST /checks`, no request body
- `checks.check_status`: `POST /checks/status`, `body=` only (`application/json`)
- `events.reopen_events`: `GET /events`, no request body
- `events.watch`: `POST /events`, `body=` only (`application/json`)

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
    "operation": "items.create_item",
    "method": "POST",
    "path": "/items",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "items.list_items",
    "method": "GET",
    "path": "/items",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "items.create_note",
    "method": "POST",
    "path": "/notes",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "items.put_blob",
    "method": "PUT",
    "path": "/blobs",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "items.search",
    "method": "POST",
    "path": "/search",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "items.feed",
    "method": "POST",
    "path": "/feed",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "jobs.create_job",
    "method": "POST",
    "path": "/jobs",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "jobs.get_job",
    "method": "GET",
    "path": "/jobs/{jobId}",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "checks.create_check",
    "method": "POST",
    "path": "/checks",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "checks.check_status",
    "method": "POST",
    "path": "/checks/status",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "events.reopen_events",
    "method": "GET",
    "path": "/events",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "events.watch",
    "method": "POST",
    "path": "/events",
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
An SSE helper's `open` sends its operation in a session of its own and returns an event stream once the
response is a declared success; the stream reads only the bytes each event needs, and `close()` or `aclose()` releases
the response. A helper that declares `resume` also has `resume`, which reopens a stream after its
`checkpoint()`, and its streams reconnect when `StreamOptions(reconnect=True)`.
A pagination helper's `page` fetches the first page and `next_page` the page after one it returned, each in a
session of its own; `iterate` returns a pager, which sends nothing until it is iterated and fetches each page only
once the previous one is consumed, and `resume` returns one continuing a pager's `checkpoint()`. See the runtime
reference for their limits and checkpoints.
A polling helper's `start` creates the operation and returns a handle: `status` polls it once and `wait` polls until
it settles and returns its result, each poll after the wait the last response requires; `close()` or `aclose()`
stops only local polling. `resume` returns a handle continuing a handle's `checkpoint()` without creating the
operation again, and a helper that declares a remote cancellation returns a handle whose `cancel_remote` sends it. See
the runtime reference for their limits and checkpoints.

```json
[
  {
    "helper": "items.search_all",
    "kind": "pagination",
    "operation": "items.search",
    "method": "POST",
    "path": "/search"
  },
  {
    "helper": "items.feed_all",
    "kind": "pagination",
    "operation": "items.feed",
    "method": "POST",
    "path": "/feed"
  },
  {
    "helper": "items.listing",
    "kind": "pagination",
    "operation": "items.list_items",
    "method": "GET",
    "path": "/items"
  },
  {
    "helper": "jobs.run",
    "kind": "polling",
    "operation": "jobs.create_job",
    "method": "POST",
    "path": "/jobs"
  },
  {
    "helper": "checks.run",
    "kind": "polling",
    "operation": "checks.create_check",
    "method": "POST",
    "path": "/checks"
  },
  {
    "helper": "events.watch",
    "kind": "sse",
    "operation": "events.watch",
    "method": "POST",
    "path": "/events"
  },
  {
    "helper": "events.resumable",
    "kind": "sse",
    "operation": "events.watch",
    "method": "POST",
    "path": "/events"
  },
  {
    "helper": "events.tail",
    "kind": "sse",
    "operation": "events.watch",
    "method": "POST",
    "path": "/events"
  },
  {
    "helper": "events.push",
    "kind": "sse",
    "operation": "events.reopen_events",
    "method": "GET",
    "path": "/events"
  }
]
```

## Request compression

These operations declare request compression. Their bodies are gzipped by default;
`Client(compression=None)` disables it for the client. See the runtime reference.

```json
{
  "items.create_item": [
    "gzip"
  ],
  "items.list_items": [
    "gzip"
  ],
  "items.put_blob": [
    "gzip"
  ],
  "items.search": [
    "gzip"
  ],
  "items.feed": [
    "gzip"
  ],
  "jobs.create_job": [
    "gzip"
  ],
  "checks.check_status": [
    "gzip"
  ],
  "events.watch": [
    "gzip"
  ]
}
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
| Helpers | `compression`, `pagination`, `polling`, `streams` |

The package copies the runtime modules its capabilities and model backend need:

- `_runtime`: `__init__.py`
- `_runtime/client`: `__init__.py`, `bodies.py`, `body_sources.py`, `client.py`, `compression.py`, `content.py`, `errors.py`, `logical.py`, `media.py`, `native.py`, `operations.py`, `options.py`, `paths.py`, `positions.py`, `raw.py`, `responses.py`, `retry.py`, `timing.py`, `urls.py`
- `_runtime/model_codecs`: `__init__.py`, `errors.py`, `media.py`, `native.py`, `parameters.py`, `unset.py`
- `_runtime/protocols`: `__init__.py`, `caches.py`, `client.py`, `errors.py`, `links.py`, `options.py`, `origins.py`, `pagination.py`, `polling.py`, `records.py`, `references.py`, `resume.py`, `streams.py`, `values.py`, `writes.py`
