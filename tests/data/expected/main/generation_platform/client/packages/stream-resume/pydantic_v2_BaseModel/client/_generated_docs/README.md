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

- `searches.search_feed`: `POST /search-feed`, `body=` only (`application/json`)
- `events.stream_events`: `GET /events`, no request body
- `streams.reopen_stream`: `GET /streams/{streamId}`, no request body
- `rooms.stream_room`: `GET /rooms/{room}{shard}`, no request body
- `replay.replay_events`: `GET /replay`, no request body
- `records.stream_records`: `GET /records`, no request body
- `feed.stream_feed`: `POST /feed`, `body=` only (`application/json`)
- `marks.stream_marks`: `GET /marks`, no request body
- `marks.stream_named_marks`: `GET /named-marks`, no request body
- `marks.stream_deep_marks`: `GET /deep-marks`, no request body
- `marks.stream_keyed_marks`: `GET /keyed-marks`, no request body
- `status.get_status`: `GET /status`, no request body

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
    "operation": "searches.search_feed",
    "method": "POST",
    "path": "/search-feed",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "events.stream_events",
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
    "operation": "streams.reopen_stream",
    "method": "GET",
    "path": "/streams/{streamId}",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "rooms.stream_room",
    "method": "GET",
    "path": "/rooms/{room}{shard}",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "replay.replay_events",
    "method": "GET",
    "path": "/replay",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "records.stream_records",
    "method": "GET",
    "path": "/records",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "feed.stream_feed",
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
    "operation": "marks.stream_marks",
    "method": "GET",
    "path": "/marks",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "marks.stream_named_marks",
    "method": "GET",
    "path": "/named-marks",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "marks.stream_deep_marks",
    "method": "GET",
    "path": "/deep-marks",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "marks.stream_keyed_marks",
    "method": "GET",
    "path": "/keyed-marks",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "status.get_status",
    "method": "GET",
    "path": "/status",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {
        "query_key": []
      }
    ],
    "auth_challenge_less_401": false
  }
]
```

## Protocol helpers

`client.protocols` holds the protocol helpers below by their dotted names, on `Client` and `AsyncClient` alike.
An SSE or NDJSON helper's `open` sends its operation in a session of its own and returns an event stream once the
response is a declared success; the stream reads only the bytes each event needs, and `close()` or `aclose()` releases
the response. A helper that declares `resume` also has `resume`, which reopens a stream after its
`checkpoint()`, and its streams reconnect when `StreamOptions(reconnect=True)`.
See the runtime reference for their limits.

```json
[
  {
    "helper": "searches.ticks",
    "kind": "sse",
    "operation": "searches.search_feed",
    "method": "POST",
    "path": "/search-feed"
  },
  {
    "helper": "feed.live",
    "kind": "sse",
    "operation": "feed.stream_feed",
    "method": "POST",
    "path": "/feed"
  },
  {
    "helper": "events.live",
    "kind": "sse",
    "operation": "events.stream_events",
    "method": "GET",
    "path": "/events"
  },
  {
    "helper": "events.plain",
    "kind": "sse",
    "operation": "events.stream_events",
    "method": "GET",
    "path": "/events"
  },
  {
    "helper": "events.tracked",
    "kind": "sse",
    "operation": "events.stream_events",
    "method": "GET",
    "path": "/events"
  },
  {
    "helper": "rooms.live",
    "kind": "sse",
    "operation": "rooms.stream_room",
    "method": "GET",
    "path": "/rooms/{room}{shard}"
  },
  {
    "helper": "records.all",
    "kind": "ndjson",
    "operation": "records.stream_records",
    "method": "GET",
    "path": "/records"
  },
  {
    "helper": "feed.ticks",
    "kind": "sse",
    "operation": "feed.stream_feed",
    "method": "POST",
    "path": "/feed"
  },
  {
    "helper": "topics.marks",
    "kind": "sse",
    "operation": "events.stream_events",
    "method": "GET",
    "path": "/events"
  },
  {
    "helper": "marks.scoped",
    "kind": "sse",
    "operation": "marks.stream_marks",
    "method": "GET",
    "path": "/marks"
  },
  {
    "helper": "marks.named",
    "kind": "sse",
    "operation": "marks.stream_named_marks",
    "method": "GET",
    "path": "/named-marks"
  },
  {
    "helper": "marks.deep",
    "kind": "sse",
    "operation": "marks.stream_deep_marks",
    "method": "GET",
    "path": "/deep-marks"
  },
  {
    "helper": "marks.bound",
    "kind": "sse",
    "operation": "marks.stream_marks",
    "method": "GET",
    "path": "/marks"
  },
  {
    "helper": "marks.deepbound",
    "kind": "sse",
    "operation": "marks.stream_deep_marks",
    "method": "GET",
    "path": "/deep-marks"
  },
  {
    "helper": "searches.keyed",
    "kind": "sse",
    "operation": "searches.search_feed",
    "method": "POST",
    "path": "/search-feed"
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
| Security | `api_key` |
| Helpers | `streams` |

The package copies the runtime modules its capabilities and model backend need:

- `_runtime`: `__init__.py`
- `_runtime/client`: `__init__.py`, `auth.py`, `client.py`, `codecs.py`, `content.py`, `errors.py`, `logical.py`, `media.py`, `native.py`, `operations.py`, `options.py`, `paths.py`, `positions.py`, `raw.py`, `responses.py`, `retry.py`, `security.py`, `timing.py`, `urls.py`
- `_runtime/model_codecs`: `__init__.py`, `errors.py`, `media.py`, `native.py`, `parameter_reads.py`, `parameters.py`, `unset.py`
- `_runtime/protocols`: `__init__.py`, `caches.py`, `client.py`, `errors.py`, `links.py`, `options.py`, `origins.py`, `pagination.py`, `records.py`, `references.py`, `resume.py`, `streams.py`, `values.py`, `writes.py`
