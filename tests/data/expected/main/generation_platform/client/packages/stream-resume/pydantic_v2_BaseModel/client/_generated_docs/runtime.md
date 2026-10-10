# Runtime reference

Import `Client` and `AsyncClient` from `client` and the records below from
`client.options`. Async calls require asyncio.

## Layered options and budgets

A client takes its settings as keywords, a view as the keywords of `with_options(...)`, and one call as
`options=RequestOptions(...)`. The values a layer gives override those below it: the call's, then the nearest
view's, then the client's, then fixed defaults. A setting left None inherits, except where None has a meaning of its
own and UNSET inherits: `timeout=None` lifts every phase limit, `total_timeout=None` removes the optional budget
across attempts, and `auth=None` sends without an Auth. A number as `timeout` limits every phase and an
`httpx2.Timeout` each one. `RetryOptions` merges its fields independently, a set of statuses replacing the inherited
one; `max_retries` is a setting of its own. Set `total_timeout=60` on the client to bound every call to a minute.
The `default_headers` and `default_query` of the client and its views, and a call's `extra_headers` and `extra_query`,
give each name they list their value instead of the lower layers' ones, and None removes that name. They apply over
the generated headers in order: the client's, the views', the call's parameters', then the call's extra ones. Header
names compare case-insensitively and query names exactly; HTTPX2 refuses a malformed header as it sends the request.
A body's media type ranks above every layer but the call's extra headers, whose Content-Type relabels the body, and a
multipart one without a boundary keeps the body's.
Native phase timeouts bound each I/O wait rather than the total duration of a call that keeps making progress.

| Setting | Effective default |
|---|---|
| owned-client connect / read / write / pool timeout | 5 / 600 / 600 / 600 seconds |
| injected-client phase timeouts | inherited from the native client unless overridden |
| total timeout | None; opt in across attempts |
| retries / initial delay / maximum delay / jitter | 2 / 0.5 seconds / 8 seconds / full |
| retry statuses | 408, 429, 500, 502, 503, 504 |
| maximum accepted Retry-After | 60 seconds; explicit None removes this cap |
| respect Retry-After / retry pool timeout | True / False |
| follow redirects | the native client's: False for an SDK-created client |
| error body prefix | 64 KiB; response and stream bodies are not capped |

`Client(clock=Clock(monotonic=..., time=..., random=..., sleep=..., asleep=...))` replaces the time, jitter, and wait
sources of every call: each retry, poll, and reconnection wait goes through `sleep`, or `asleep` in an asyncio client.

## Retries and replayable input

GET, HEAD, OPTIONS, PUT, and DELETE retry by default; POST and PATCH need an idempotent declaration or a declared key
header. A connect failure, connect timeout, or pool timeout (with `RetryOptions(retry_on_pool_timeout=True)`) retries
any call no earlier attempt delivered; other transport failures, callbacks, decoding, cancellation, and deadlines never
retry, and `retry_safety="never"` forbids every resend. Full jitter samples up to the capped exponential delay. A
Retry-After delay is a minimum; its date is read by `email.utils.parsedate_to_datetime`, UTC without a known zone, and
an RFC 850 date's two-digit year is the latest at most 50 years after receipt; a year below 100 in another form is read
as 1969 to 2068. Raw calls return final statuses, retry exhaustion included; stream bodies never retry after handoff. A
call's `RequestOptions(idempotency_key="...")` gives a stable key and `idempotency_key=None` disables the key an
operation with a declared key header creates once per call; a header of that name among the call's extra headers or the
default headers is the call's key instead, which a protocol helper refuses. One call keeps its key, origin, and encoded
body across attempts.

No operation of this package sends a binary or multipart body, so `request_raw` takes `bytes`.

## Transport

Requests go through the native HTTPX2 client with its `auth`, event hooks, redirect setting, framing, and content
decoding; `follow_redirects` of the client, a view, or a call overrides the redirect setting per call. A request
carrying a credential at a declared scheme's position other than `Authorization` is never redirected: its 3xx is the
final response. An SDK-created client has HTTPX2's defaults, a 600 second timeout, and 5 seconds to connect, and root
close closes it once; an HTTPX2 client passed as `http_client` keeps its own construction and stays caller owned.

## Authentication

```python
from client import Client


def authenticated_client(key: str) -> Client:
    return Client(query_key=key)
```

`Client` and `AsyncClient` take one keyword argument per security scheme an operation requires: `query_key` (API key). A
value is a string, a `(username, password)` tuple for HTTP Basic, or a callable returning one, which is called for each
request; a bearer argument also takes an OAuth provider. A call sends the credentials of its operation's first security
alternative they all satisfy, at the positions its schemes declare, and only to its server's origin. An anonymous
alternative applies only when no other alternative is satisfied, so an optional operation sends a credential given for a
listed scheme; an operation declaring empty security and `request_raw` send none. A required operation no credential
satisfies raises `ConfigurationError` with the reason `missing_credentials` before sending, unless the HTTP client has
an Auth of its own. A callable's failure raises `AuthError` with the reason `provider_failed`. Credentials beside the
client's `auth` or an Auth of an injected HTTP client raise `ConfigurationError` with the reason `conflicting_auth`.
`auth` of the client, a view, or a call's `RequestOptions` takes any `httpx2.Auth`, which replaces the credentials for
its calls, and `auth=None` sends without an Auth; unset, the credentials apply, or else the HTTP client's own Auth. Sign
requests with an `httpx2.Auth` of your own, which reads the body natively. Credential values do not appear in repr; a
query credential is part of the request URL, which the `httpx2` logger records at INFO level.

## Errors and cleanup

Every exception derives from `SDKError`, which keeps a short `reason`, the `operation_id`, the call's
`attempt_count`, `elapsed`, and `request_id`, and the original failure as `cause`. `APIConnectionError` is an I/O
failure and `APITimeoutError` a phase or total timeout. A final status the operation does not declare as a success
raises `APIStatusError`, or its subclass for 400, 401, 403, 404, 409, 422, 429, and 5xx, with its decoded error body
or bounded raw bytes. `AuthError` names its `reason`, `DecodeError` names a request argument or response that does not fit its declaration,
and `ConfigurationError` a refused setting or call; messages never carry key, header, or body values. Secondary
cleanup failures are notes of the primary failure, and native cancellation propagates unchanged.

## SSE or NDJSON streams

An SSE or NDJSON helper's `open` is one session holding one logical call. The call's total timeout bounds only
acquiring the response, which must be a declared success of the helper's media type. Native read timeouts bound
idle I/O; an optional session total timeout is checked before the next step. Each limit comes from the call's
`stream_options`, then the client's `helper_defaults` for the helper, then the default below. The stream types are
imported from `client.protocols`: `StreamOptions`, `EventStream`, `AsyncEventStream`, `StreamEvent`, and
`UnknownEvent`.

| Limit | Effective default |
|---|---|
| idle timeout | the native read timeout; None removes it |
| session total timeout (`total_timeout`) | None |
| reconnections, counted across resumes | 5; None removes the limit, and 0 allows none |
| reconnection wait | 60 seconds; None removes it |

The idle timeout runs only while the next step waits for bytes. HTTPX2's `EventSource` parses server-sent events as
UTF-8 text without a leading byte order mark; an event without data is not delivered, though its `id` and `retry`
fields still count, and an event over HTTPX2's 1 MiB event size limit raises `ProtocolDataError` with the reason
`too_large` and the native `SSEError` as its cause. An event's data is JSON decoded by the schema its discriminator
maps it to; data that does not decode raises `DecodeError`, and a declared error event raises `ProtocolDataError` with
the reason `error_event` and the decoded event as `data`. The stream ends at its declared completion; an end before it
raises `StreamInterruptedError` with the reason `eof`, and a broken connection one with the reason `transport` and its
transport failure as the cause. A frame an SSE body ends in the middle of is
discarded, as the event-stream interpretation discards it. A helper that does not declare `resume`
never reconnects, and `StreamOptions(reconnect=True)` raises `ConfigurationError` for it. Close a stream with
`with`, `async with`, or `close()`; leaving a loop early does not release its response. A root close does not drain
active streams; each stream releases its own response.

An NDJSON body is read one line at a time: LF or CRLF ends a line, which is one record of strict UTF-8 JSON, so a
blank line or one that is not UTF-8 or JSON raises `DecodeError`. Bytes after the last line end raise
`StreamInterruptedError` with the reason `eof` unless the helper's `final_line` is `allow_eof`, which decodes them as
the last record. A record has the empty string as its event type and no event ID, and a declared error record raises
`ProtocolDataError` with the reason `error_event`.

A helper that declares `resume` tracks the cursor of the last event it delivered: the SSE event ID, or the value its
cursor pointer reads from an event's data, which an empty event ID or a null value clears. Once a cursor was delivered,
a stream's `checkpoint()` returns plain JSON without sending: the cursor, the bindings' values, and the server's expiry,
never events, counts, the caller's arguments, responses, the session, or the call's options. A stream that failed,
ended, or closed keeps its checkpoint. The helper's `resume` sends the reopen in a session of its own, writing the
cursor, and omitting a cleared one, and returns once its response is a declared success, counting events and
reconnections afresh; a reopen of the helper's own operation takes the operation's arguments and body again from the
caller. It refuses a state that is not JSON or does not fit with `ConfigurationError`, an expired one with the reason
`expired`, before sending, and a cursor written where a credential goes with the reason `wrong_capability`. Neither the
headers and query of the client, a view, or the call may name a parameter a reopen writes, nor the call fix a key.

With `StreamOptions(reconnect=True)` such a stream reopens itself as one more child call of its session after a
transport interruption, a read-phase failure classified as retryable or a read timeout the call's own
`timeout` set, or after an incomplete end when the helper declares `incomplete_eof`, once a cursor was
delivered and after the retry backoff and at least the last `retry` time. Running out of reconnections raises
`SessionLimitError` with the reason `reconnects`; a wait whose backoff cap or `retry` time is longer than allowed, or a
wait longer than the session has left, raises the interruption instead. Decode, size, remote, idle, and deadline
failures, the declared end, and closing never reconnect, and events the server sends again after a reopen are delivered
again.
