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
decoding; `follow_redirects` of the client, a view, or a call overrides the redirect setting per call.  An SDK-created
client has HTTPX2's defaults, a 600 second timeout, and 5 seconds to connect, and root close closes it once; an HTTPX2
client passed as `http_client` keeps its own construction and stays caller owned. `auth` of the client, a view, or a
call's `RequestOptions` takes any `httpx2.Auth`, and `auth=None` sends without one; unset, the HTTP client's own Auth
applies.

## Errors and cleanup

Every exception derives from `SDKError`, which keeps a short `reason`, the `operation_id`, the call's
`attempt_count`, `elapsed`, and `request_id`, and the original failure as `cause`. `APIConnectionError` is an I/O
failure and `APITimeoutError` a phase or total timeout. A final status the operation does not declare as a success
raises `APIStatusError`, or its subclass for 400, 401, 403, 404, 409, 422, 429, and 5xx, with its decoded error body
or bounded raw bytes. `DecodeError` names a request argument or response that does not fit its declaration,
and `ConfigurationError` a refused setting or call; messages never carry key, header, or body values. Secondary
cleanup failures are notes of the primary failure, and native cancellation propagates unchanged.

## SSE streams

An SSE helper's `open` is one session holding one logical call. The call's total timeout bounds only
acquiring the response, which must be a declared success of the helper's media type. Native read timeouts bound
idle I/O; an optional session total timeout is checked before the next step. Each limit comes from the call's
`stream_options`, then the client's `helper_defaults` for the helper, then the default below. The stream types are
imported from `client.protocols`: `StreamOptions`, `EventStream`, `AsyncEventStream`, `StreamEvent`, and
`UnknownEvent`.

| Limit | Effective default |
|---|---|
| idle timeout | the native read timeout; None removes it |
| session total timeout (`total_timeout`) | None |

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
