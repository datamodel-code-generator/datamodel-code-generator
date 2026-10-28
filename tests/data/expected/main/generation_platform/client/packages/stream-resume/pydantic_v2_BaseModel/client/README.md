# client

The `client` package is an HTTPX2 client of the API the OpenAPI document describes.
datamodel-code-generator generated it from the document, together with the `models` models of the `pydantic_v2.BaseModel` backend.
Generation overwrites every file of the package, as it does the models: keep your code outside the package and regenerate the package instead of editing it; see [Regenerating](#regenerating).

HTTPX2 client generation is experimental.
The [runtime reference](_generated_docs/runtime.md) describes the settings, defaults, ownership, cancellation, replay, redirects, and transport costs of the clients.

## Quick start

```python
from client import Client

with Client(base_url="https://api.example.com", query_key="...") as client:
    result = client.status.get_status()
```

`AsyncClient` has the same resources and methods, and each method is a coroutine:

```python
from client import AsyncClient


async def main() -> None:
    async with AsyncClient(base_url="https://api.example.com", query_key="...") as client:
        result = await client.status.get_status()
```

Replace each `...` with a value of the type its argument declares; [Operations](#operations) lists the arguments of each method.
A method returns the decoded body of a success response, of the type its signature declares.
Leaving the `with` or `async with` block closes the client, as `close()` or `aclose()` does; keep a streaming response only inside its own context.
Import options, errors, and response types from the public modules of the package.
`request_raw(method, url)` of a client sends a request outside the operations to an absolute URL and returns its raw response.

`Client` and `AsyncClient` take one keyword argument per security scheme an operation requires: `query_key` (API key). The example gives those of the first security alternative of `status.get_status`.
See [Authentication](_generated_docs/runtime.md#authentication) in the runtime reference.

## Base URL and servers

Without `base_url`, a call sends to the first server generation resolved for its operation, with the default of each server variable: `https://api.example.com` for `status.get_status`.
An operation's servers are the OpenAPI `servers` of the operation, its path item, or the document; one without them takes the `--client-default-base-url` of the generation.
`server=ServerSelection(index=..., variables={...})`, with `ServerSelection` from `client.options`, picks another declared server by position and supplies its variables; `base_url=` replaces the servers with one URL.
`Client`, `AsyncClient`, `with_options`, and `RequestOptions` take both, and the nearest one given wins: a call's over its view's, and a view's over the client's.
Giving both on one of them raises `ConfigurationError`.

## Errors

```python
from client import Client
from client.errors import APIConnectionError, APIStatusError, NotFoundError

with Client(base_url="https://api.example.com", query_key="...") as client:
    try:
        result = client.status.get_status()
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

### `client.searches.search_feed`

`POST /search-feed`, operation reference `/paths/~1search-feed/post`, request body as `body=` only (`application/json`)

```python
def search_feed(
    *,
    criteria: dict[str, str] | UNSET = UNSET,
    body: models.FeedQuery,
    media_type: Literal['application/json'] | None = None,
    response_media_type: Literal['text/event-stream'] | None = None,
    options: RequestOptions | None = None,
) -> SearchFeedResponse: ...
```

### `client.events.stream_events`

`GET /events`, operation reference `/paths/~1events/get`

```python
def stream_events(
    *,
    topic: str | UNSET = UNSET,
    Last_Event_ID: str | UNSET = UNSET,
    session: str | UNSET = UNSET,
    response_media_type: Literal['text/event-stream'] | None = None,
    options: RequestOptions | None = None,
) -> StreamEventsResponse: ...
```

### `client.streams.reopen_stream`

`GET /streams/{streamId}`, operation reference `/paths/~1streams~1{streamId}/get`

```python
def reopen_stream(
    *,
    streamId: str,
    Last_Event_ID: str | UNSET = UNSET,
    token: str | UNSET = UNSET,
    mode: str | UNSET = UNSET,
    response_media_type: Literal['text/event-stream; charset=utf-8'] | None = None,
    options: RequestOptions | None = None,
) -> ReopenStreamResponse: ...
```

### `client.rooms.stream_room`

`GET /rooms/{room}{shard}`, operation reference `/paths/~1rooms~1{room}{shard}/get`

```python
def stream_room(
    *,
    room: str,
    shard: str,
    Last_Event_ID: str | UNSET = UNSET,
    response_media_type: Literal['text/event-stream'] | None = None,
    options: RequestOptions | None = None,
) -> StreamRoomResponse: ...
```

### `client.replay.replay_events`

`GET /replay`, operation reference `/paths/~1replay/get`

```python
def replay_events(
    *,
    from_: str,
    response_media_type: Literal['text/event-stream'] | None = None,
    options: RequestOptions | None = None,
) -> ReplayEventsResponse: ...
```

### `client.records.stream_records`

`GET /records`, operation reference `/paths/~1records/get`

```python
def stream_records(
    *,
    after: str | UNSET = UNSET,
    response_media_type: Literal['application/x-ndjson'] | None = None,
    options: RequestOptions | None = None,
) -> StreamRecordsResponse: ...
```

### `client.feed.stream_feed`

`POST /feed`, operation reference `/paths/~1feed/post`, request body as `body=` only (`application/json`)

```python
def stream_feed(
    *,
    Last_Event_ID: str | UNSET = UNSET,
    body: models.FeedQuery,
    media_type: Literal['application/json'] | None = None,
    response_media_type: Literal['text/event-stream'] | None = None,
    options: RequestOptions | None = None,
) -> StreamFeedResponse: ...
```

### `client.marks.stream_marks`

`GET /marks`, operation reference `/paths/~1marks/get`

```python
def stream_marks(
    *,
    tag: str | UNSET = UNSET,
    Last_Event_ID: str | UNSET = UNSET,
    scope: models.MarksGetQueryScopeParameter | UNSET = UNSET,
    response_media_type: Literal['text/event-stream'] | None = None,
    options: RequestOptions | None = None,
) -> StreamMarksResponse: ...
```

### `client.marks.stream_named_marks`

`GET /named-marks`, operation reference `/paths/~1named-marks/get`

```python
def stream_named_marks(
    *,
    tag: str | UNSET = UNSET,
    Last_Event_ID: str | UNSET = UNSET,
    scope: models.NamedMarksGetQueryScopeParameter | UNSET = UNSET,
    response_media_type: Literal['text/event-stream'] | None = None,
    options: RequestOptions | None = None,
) -> StreamNamedMarksResponse: ...
```

### `client.marks.stream_deep_marks`

`GET /deep-marks`, operation reference `/paths/~1deep-marks/get`

```python
def stream_deep_marks(
    *,
    tag: str | UNSET = UNSET,
    Last_Event_ID: str | UNSET = UNSET,
    scope: models.DeepMarksGetQueryScopeParameter | UNSET = UNSET,
    response_media_type: Literal['text/event-stream'] | None = None,
    options: RequestOptions | None = None,
) -> StreamDeepMarksResponse: ...
```

### `client.marks.stream_keyed_marks`

`GET /keyed-marks`, operation reference `/paths/~1keyed-marks/get`

```python
def stream_keyed_marks(
    *,
    keyed: models.KeyedMarksGetQueryKeyedParameter | UNSET = UNSET,
    response_media_type: Literal['text/event-stream'] | None = None,
    options: RequestOptions | None = None,
) -> StreamKeyedMarksResponse: ...
```

### `client.status.get_status`

`GET /status`, operation reference `/paths/~1status/get`

```python
def get_status(
    *,
    response_media_type: Literal['application/json'] | None = None,
    options: RequestOptions | None = None,
) -> GetStatusResponse: ...
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

### Capabilities

| Capability | Declared |
| --- | --- |
| Security | `api_key` |
| Helpers | `streams` |

The package copies the runtime modules its capabilities and model backend need:

- `_runtime`: `__init__.py`
- `_runtime/client`: `__init__.py`, `auth.py`, `client.py`, `codecs.py`, `content.py`, `errors.py`, `logical.py`, `media.py`, `native.py`, `operations.py`, `options.py`, `paths.py`, `positions.py`, `raw.py`, `responses.py`, `retry.py`, `security.py`, `timing.py`, `urls.py`
- `_runtime/model_codecs`: `__init__.py`, `errors.py`, `media.py`, `parameter_reads.py`, `parameters.py`, `pydantic_adapters.py`, `pydantic_models.py`, `unset.py`
- `_runtime/protocols`: `__init__.py`, `client.py`, `errors.py`, `links.py`, `options.py`, `origins.py`, `pagination.py`, `records.py`, `references.py`, `resume.py`, `streams.py`, `values.py`, `writes.py`
