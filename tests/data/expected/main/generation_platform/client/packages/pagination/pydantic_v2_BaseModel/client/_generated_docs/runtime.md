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
An OAuth provider takes `clock=Clock(...)` for its own token expiry.

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

## Authentication

```python
from client import Client


def authenticated_client(token: str) -> Client:
    return Client(oauth=token)
```

`Client` and `AsyncClient` take one keyword argument per security scheme an operation requires: `oauth` (bearer token).
A value is a string, a `(username, password)` tuple for HTTP Basic, or a callable returning one, which is called for each request; a bearer argument also takes an OAuth provider.
A call sends the credentials of its operation's first security alternative they all satisfy, at the positions its schemes declare, and only to its server's origin.
An anonymous alternative applies only when no other alternative is satisfied, so an optional operation sends a credential given for a listed scheme; an operation declaring empty security and `request_raw` send none.
A required operation no credential satisfies raises `ConfigurationError` with the reason `missing_credentials` before sending, unless the HTTP client has an Auth of its own.
A callable's failure raises `AuthError` with the reason `provider_failed`.
Credentials beside the client's `auth` or an Auth of an injected HTTP client raise `ConfigurationError` with the reason `conflicting_auth`.
`auth` of the client, a view, or a call's `RequestOptions` takes any `httpx2.Auth`, which replaces the credentials for its calls, and `auth=None` sends without an Auth; unset, the credentials apply, or else the HTTP client's own Auth.
Sign requests with an `httpx2.Auth` of your own, which reads the body natively.
Credential values do not appear in repr; a query credential is part of the request URL, which the `httpx2` logger records at INFO level.

`client.auth` exports `ClientCredentials` and `RefreshToken`, and a subclass of either for each declared flow whose token URL is declared, which `token_url=` overrides.
They are bearer credentials: `ClientCredentials(client_id=..., client_secret=..., scopes=..., audience=...)` requests a client's own token, and `RefreshToken(token_set, client_id=..., on_token_refreshed=...)` renews a `TokenSet` with its refresh token and hands each refreshed set to the callback.
The call needing a token requests it inline under the provider's lock, which concurrent calls wait for, through the client's HTTP client without its Auth and without redirects, or through the provider's own `http_client`.
A token is renewed once a tenth of its lifetime, at most thirty seconds, remains; a 401 with a Bearer `invalid_token` challenge, or without a challenge on an operation declaring `auth_challenge_less_401`, renews it once and sends a replayable request again.
A rejected token request raises `AuthError` with the reason `oauth_error`, and `invalid_grant` on a refresh the reason `reauthorization_required`.
A `TokenSet` omits its tokens from repr, and the SDK persists nothing.

## Errors and cleanup

Every exception derives from `SDKError`, which keeps a short `reason`, the `operation_id`, the call's
`attempt_count`, `elapsed`, and `request_id`, and the original failure as `cause`. `APIConnectionError` is an I/O
failure and `APITimeoutError` a phase or total timeout. A final status the operation does not declare as a success
raises `APIStatusError`, or its subclass for 400, 401, 403, 404, 409, 422, 429, and 5xx, with its decoded error body
or bounded raw bytes. `AuthError` names its `reason`, `DecodeError` names a request argument or response that does not fit its declaration,
and `ConfigurationError` a refused setting or call; messages never carry key, header, or body values. Secondary
cleanup failures are notes of the primary failure, and native cancellation propagates unchanged.

## Pagination sessions

A pager, and each `page` or `next_page` call, is one session. Every page is its own logical call with its own retries,
total timeout, and idempotency key; the session bounds all of them. Each limit comes from the call's
`pagination_options`, then the client's `helper_defaults` for the helper, then the default below. Defaults naming a
helper the package lacks, or another kind's options, fail construction. The session types are imported from
`client.protocols`: `PaginationOptions`, `Page`, `Pager`, and `AsyncPager`.

| Limit | Effective default |
|---|---|
| pages per session | None (no limit) |
| items per session | None (no limit); 0 ends a pager at once |
| session total timeout (`total_timeout`) | None (no limit) |

A page's items must be a JSON array; an empty array does not end the traversal. A server's cursor, and each value a
binding reads, is sent as it came, without its target's schema checks; a dot segment for a path parameter raises
`ProtocolDataError`. The cursor ends the traversal only through the declared end conditions, and a missing or null
cursor that no condition covers, or a missing binding value, raises `ProtocolDataError`. A continuation seen earlier in
the session ends it with `ProtocolDataError` with the reason `pagination_cycle` after the repeating page. A limit
reached while pages remain raises `SessionLimitError` with the progress so far; a pager then refuses further steps. A
call's options must not give extra headers or query names of a parameter the helper writes.

A pager's `checkpoint()` returns the continuation it fetches its next page with, the server's cursor, the next offset
or page number, or the resolved next URL, without sending; it is None before the first page and after the last. The
helper's `resume(state, ...)` takes that value with the operation's arguments and any body again and returns a pager
in a session of its own that sends nothing until it is iterated. A continuation holds no call arguments, credentials,
body, or progress. A pager stopped in the middle of a page gives the continuation before that page, so a resumed pager
repeats the items already delivered from it. A resumed pager counts pages and items from zero against its own limits,
starts its session's timeout afresh, and detects cycles from the given continuation on. Its first request writes the
continuation and the helper's literal bindings, a binding that reads a response takes the caller's argument, and an
`initial` binding is read from the first resumed page. A next URL is checked as a server's, at the same origins, and
kept without the credentials the client places itself. A pager stopped by `SessionLimitError` or a
`ProtocolDataError` with the reason `pagination_cycle` resumes from its `checkpoint()`; the errors carry no
continuation.
