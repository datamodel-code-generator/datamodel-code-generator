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


def authenticated_client(token: str) -> Client:
    return Client(bearer=token)
```

`Client` and `AsyncClient` take one keyword argument per security scheme an operation requires: `bearer` (bearer token).
A value is a string, a `(username, password)` tuple for HTTP Basic, or a callable returning one, which is called for
each request; a bearer argument also takes an OAuth provider. A call sends the credentials of its operation's first
security alternative they all satisfy, at the positions its schemes declare, and only to its server's origin. An
anonymous alternative applies only when no other alternative is satisfied, so an optional operation sends a credential
given for a listed scheme; an operation declaring empty security and `request_raw` send none. A required operation no
credential satisfies raises `ConfigurationError` with the reason `missing_credentials` before sending, unless the HTTP
client has an Auth of its own. A callable's failure raises `AuthError` with the reason `provider_failed`. Credentials
beside the client's `auth` or an Auth of an injected HTTP client raise `ConfigurationError` with the reason
`conflicting_auth`. `auth` of the client, a view, or a call's `RequestOptions` takes any `httpx2.Auth`, which replaces
the credentials for its calls, and `auth=None` sends without an Auth; unset, the credentials apply, or else the HTTP
client's own Auth. Sign requests with an `httpx2.Auth` of your own, which reads the body natively. Credential values do
not appear in repr; a query credential is part of the request URL, which the `httpx2` logger records at INFO level.

## Errors and cleanup

Every exception derives from `SDKError`, which keeps a short `reason`, the `operation_id`, the call's
`attempt_count`, `elapsed`, and `request_id`, and the original failure as `cause`. `APIConnectionError` is an I/O
failure and `APITimeoutError` a phase or total timeout. A final status the operation does not declare as a success
raises `APIStatusError`, or its subclass for 400, 401, 403, 404, 409, 422, 429, and 5xx, with its decoded error body
or bounded raw bytes. `AuthError` names its `reason`, `DecodeError` names a request argument or response that does not fit its declaration,
and `ConfigurationError` a refused setting or call; messages never carry key, header, or body values. Secondary
cleanup failures are notes of the primary failure, and native cancellation propagates unchanged.

## Cache helpers

A cache helper keeps entries in the store the client's `cache_stores` lends it under its name, a `MemoryCacheStore` or
`AsyncMemoryCacheStore` or another implementation of `CacheStore` or `AsyncCacheStore` of the client's mode. Without
one, the client's root creates a memory store of 128 entries for the helper at its first fetch, which the root's views
share and no other client does. The client never closes a store. The types are imported from
`client.protocols`: `CacheOptions`, `CacheResult`, `CacheEntry`, the store protocols, and the memory
stores.

| Limit | Effective default |
|---|---|
| stored body per entry | 2 MiB |
| freshness of any entry | 300 seconds |

`fetch` returns a `CacheResult` whose `source` is `fresh_cache` for a fresh entry, answered without sending or call
events, `revalidated` for a stale entry a 304 confirmed, and `network` otherwise; a stored body is decoded again every
time. An entry is keyed by the method, the Accept header, and the identity the request is sent as: before the lookup,
the call's Auth, which is its own, its credentials', or else the HTTP client's, places credentials on a copy of the
request without sending it, and the key takes the URL with any query credentials, the values of the credential headers,
and every header the Auth added or changed. A callable credential is therefore called, and a token may be requested,
for the lookup as well as for the send. A response is stored only when the request it answered was sent as the same
identity, so a rotated or one-time credential, a timestamped signature, or a renewed token is never stored and never
answers another identity. An entry is selected by the request headers its `Vary` names and those the client's, views',
or call's headers name or a declared parameter fills. A request carrying credentials needs a helper declared
authenticated and an anonymous one a helper declared anonymous; anything else raises `ConfigurationError` with the
reason `binding_mismatch`. Credentials a request does not carry, such as a client certificate or an authenticating
transport, are not in the key: give such a client a store of its own.
Freshness comes from `max-age` or `Expires` only, capped by `max_ttl`; a stale entry is revalidated with its validator,
and a 304 without a usable entry raises `ProtocolDataError`. A response is stored only when its status is cacheable, it
came without a redirect, Set-Cookie, `no-store`, or an unsupported Cache-Control directive, and its `Vary` names only
allowlisted headers; otherwise it removes the entry it supersedes. Store failures raise `SDKError` with the reason
`store_failed` and never resend a request.
The store keeps one representation per key. A Vary mismatch is a miss; a successful cacheable response replaces it.
Vary stores plain ordered header values in private process memory, excluding credential headers and cookies.
Memory stores are bounded by entry count (128 by default), with reads and writes marking a key recently used.
Concurrent custom-store writes use last-completing replacement.
