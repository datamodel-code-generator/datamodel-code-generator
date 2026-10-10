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

`Client(clock=Clock(monotonic=..., time=..., random=..., sleep=..., asleep=...))` replaces the time, jitter, and wait sources of every call: each retry, poll, and reconnection wait goes through `sleep`, or `asleep` in an asyncio client.

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

Requests go through the native HTTPX2 client with its `auth`, event hooks, redirect setting, framing, and content decoding; `follow_redirects` of the client, a view, or a call overrides the redirect setting per call.
A request carrying a credential at a declared scheme's position other than `Authorization` is never redirected: its 3xx is the final response.
An SDK-created client has HTTPX2's defaults, a 600 second timeout, and 5 seconds to connect, and root close closes it once; an HTTPX2 client passed as `http_client` keeps its own construction and stays caller owned.
`auth` of the client, a view, or a call's `RequestOptions` takes any `httpx2.Auth`, and `auth=None` sends without one; unset, the HTTP client's own Auth applies.
## Errors and cleanup

Every exception derives from `SDKError`, which keeps a short `reason`, the `operation_id`, the call's
`attempt_count`, `elapsed`, and `request_id`, and the original failure as `cause`. `APIConnectionError` is an I/O
failure and `APITimeoutError` a phase or total timeout. A final status the operation does not declare as a success
raises `APIStatusError`, or its subclass for 400, 401, 403, 404, 409, 422, 429, and 5xx, with its decoded error body
or bounded raw bytes. `DecodeError` names a request argument or response that does not fit its declaration,
and `ConfigurationError` a refused setting or call; messages never carry key, header, or body values. Secondary
cleanup failures are notes of the primary failure, and native cancellation propagates unchanged.

## Polling sessions

A polling helper's `start` and the handle it returns are one session. The create call, every poll, the result fetch,
and a remote cancel are logical calls of their own, with their own retries, total timeout, and idempotency key; the
session bounds all of them. Each limit comes from the call's `poll_options`, then the client's `helper_defaults` for
the helper, then the default below. The session types are imported from
`client.protocols`: `PollOptions`, `PollSnapshot`, `CancelReceipt`, `LroHandle`, and `AsyncLroHandle`.

| Limit | Effective default |
|---|---|
| polls per session | 1000; None removes it |
| poll interval | the helper's declared interval, 1 second unless declared |
| allowed wait before a poll | 60 seconds; None removes it |
| session total timeout (`total_timeout`) | 600 seconds; None removes it |

`start` sends the create request once, resent only as shared retries allow. An accepted status returns a pending
handle, a declared immediate status a handle that already holds the result, and any other success status raises
`ProtocolDataError`. An interval longer than the allowed wait, or not shorter than the session, raises
`ConfigurationError` before the create request. Each poll waits until the interval after the last response, an
error response included, has passed, or the longer delay the helper's declared delay header gives, and a result fetch
after a failed one waits the same way; nothing is sent early: a server delay longer than the allowed wait, or not
shorter than what remains of the session, raises `SessionLimitError` with the reason `wait` or `deadline` and the
server's delay as `required_wait` without sending. A resumed handle polls at once, since a checkpoint keeps no server
delay. A poll's state must equal a declared state value, JSON type included; any other value raises
`ProtocolDataError`, and success is never inferred.

`wait` returns the result: read from the final poll, fetched once by the result operation, or None. A failed or
cancelled operation raises `ProtocolDataError` with the reason `operation_failed` or `operation_cancelled`, its last
poll's decoded data as `data` and its response as `info`, on every later `wait` too. An error that settles nothing, such
as a transport error, a deadline, a cancellation, or a limit, leaves the handle as it was: pending, so a later `status`
or `wait` polls again without creating the operation again, or succeeded with its result fetch still due, which a later
`wait` retries alone. `status` and `wait` at once raise `ConfigurationError` with the reason `invalid_state`, and so
does every step after `close()` or `aclose()`, which stops only local polling. A call's options must not fix an
idempotency key or give extra headers or query names of a parameter the helper writes.

`checkpoint()` returns plain JSON without sending, also after closing and while another thread or task polls: an object
whose `phase` is `pending`, with the values the next poll (`bound`) and a remote cancel (`cancel`) write and those the
create response gave the result fetch (`seed`), or `fetch`, with the values a due result fetch writes, and the server's
`expires_at`, never polls, results, model objects, the session, or the call's options; a settled operation has nothing
left to continue, and its `checkpoint()` raises `ConfigurationError`. The helper's `resume` is never awaited and returns
a handle in a session of its own that sends nothing until `status` or `wait`: a pending one polls again at once, and one
whose fetch is due fetches the result; polls, the session's timeout, and deadline start afresh. Before returning, it
refuses a value that is not JSON or does not fit the helper with `ConfigurationError`, an expired one with the reason
`expired`, and a dot segment the next poll or remote cancel would write to a path parameter with `ProtocolDataError`;
any other saved value is checked and encoded when its request is built, as a server's is. After a `SessionLimitError`,
the handle's `checkpoint()` continues the operation; the errors carry no checkpoint. A helper that declares `expires_at`
reads the server's expiry, an RFC 3339 date-time with an offset or an HTTP date, from the accepted create response, and
its checkpoints expire then; a create response without a valid one fails `start` with `ProtocolDataError`, though the
remote operation was created.

A helper that declares `remote_cancel` returns a handle of its own class whose `cancel_remote()` sends the cancel
request once, while the operation is pending, and returns a `CancelReceipt` of its response; it also runs while
another thread or task waits in `status` or `wait`. It does not change the handle, which keeps its last poll until it
polls again; closing sends nothing.
