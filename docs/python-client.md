# Python Client (In Development)

!!! warning "Not available from the CLI yet"
    The HTTPX2 client generator is still being built. Its settings below are fields of the client's flat target
    configuration file and of `ClientGenerationConfig`; the `--generate-client` command line entry point and the
    matching `--client-*` options ship with its release, together with a link to this page from the navigation.

Like the server, the client needs Python 3.11 or later, both to run `datamodel-codegen` and as the target Python
version; a target below 3.11, including the default 3.10, is refused with `E_CONFIG_VALUE`.

Settings of the generated client that change how its methods are declared or checked do not change the models: the
same OpenAPI document and model settings produce the same model files whichever client settings you choose.

The client adds `api` to the model's `openapi_scopes`, keeping the scopes you give, so that the models cover the
parameters, bodies, and responses of the operations. Custom templates and custom formatters must keep the class and
field names of the models: the client binds each operation to the names in the generated model graph and does not read
the rendered model source.

## Webhook contracts

Generated packages expose webhook contracts from `pkg.protocols` and their exceptions from `pkg.errors`, where `pkg` is
the generated package name. These imports need no HTTP or cryptography library. The
[webhook verification helpers](#webhook-verification-helpers) verify HMAC, Ed25519, and RSA-PSS signatures, or call
your `Verifier[K]` for any other signature, and decode events; constructing a signature or event record yourself does not
verify received data.

All records below are immutable and keyword-only. `KeySet` retains the original tuple and key objects without
inspecting or copying the keys. Its representation excludes key material. `VerifiedWebhook` excludes event data
from its representation.

| Type | Fields |
|---|---|
| `OperationRef` | `pointer: str`, `document: str \| None = None` |
| `KeySet[K]` | `keys: tuple[K, ...]`; an empty tuple is valid; a list or another iterable raises `TypeError` |
| `VerifiedSignature` | `delivery_id: str \| None`, `timestamp: datetime \| None`, `matched_key_id: str`; all fields are required |
| `VerifiedWebhook[T]` | `data: T`, `delivery_id: str \| None`, `timestamp: datetime \| None`, `matched_key_id: str`; all fields are required |

`Verifier[K]` is a borrowed, synchronous protocol with this method:

```python
def verify(
    raw_body: bytes,
    ordered_headers: tuple[tuple[str, str], ...],
    keys: KeySet[K],
    now: datetime,
    limits: ResolvedWebhookOptions,
) -> VerifiedSignature: ...
```

The key type is invariant and must match `KeySet[K]`. Implementations authenticate the complete received body
and every returned fact, perform no networking, and retain ownership of their application keys. The same
synchronous verifier contract applies to asynchronous consumers. [Adapter signatures](#adapter-signatures) describe how
a generated helper calls a verifier and checks its result.

`WebhookOptions` has the fields below. Every constructor default is `UNSET`, imported from `pkg.options`;
the effective values are the standalone verification defaults. `ResolvedWebhookOptions` has the same fields with
`Unset` removed and every argument required. Both types are exported from `pkg.protocols`.
`ResolvedWebhookOptions` records limits already validated and resolved by the consuming helper. Helpers must
validate `WebhookOptions` before constructing this record and passing it to an application verifier.

| Field | Type | Effective default | Valid values |
|---|---|---|---|
| `max_body_bytes` | `int \| Unset` | `8388608` | Positive integer |
| `max_header_bytes` | `int \| Unset` | `16384` | Positive integer |
| `max_keys` | `int \| Unset` | `8` | Positive integer |
| `max_signatures` | `int \| Unset` | `8` | Positive integer |
| `past_tolerance` | `float \| Unset` | `300` seconds | Finite, nonnegative number |
| `future_tolerance` | `float \| Unset` | `30` seconds | Finite, nonnegative number |

Options reject `None`, booleans, negative values, nonfinite durations, and zero count limits with
`ProtocolConfigurationError`. For example, `WebhookOptions(past_tolerance=0)` is valid, while
`WebhookOptions(max_keys=0)` is invalid. Verification retains no delivery state. Applications can deduplicate
using the authenticated `delivery_id` when a signature scheme supplies one.

Webhook exceptions have keyword-only constructors and the following additional fields:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.error-fields -->
| Exception | Direct base | Fields |
|---|---|---|
| `ProtocolConfigurationError` | `ConfigurationError` | `field_path: tuple[str, ...]`, `condition: Literal['unknown_field', 'invalid_value', 'missing_metadata', 'missing_adapter', 'wrong_capability', 'security_partition', 'binding_mismatch']` |
| `WebhookVerificationError` | `ProtocolError` | `condition: Literal['malformed_signature', 'invalid_signature', 'missing_key', 'timestamp_window']` |
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.error-fields -->

All fields without a displayed default are required. They also accept the shared context fields
`helper_id: str | None = None`, `operation: OperationRef | None = None`, `info: ResponseInfo | None = None`,
`cause: BaseException | None = None`, and `secondary_errors: tuple[BaseException, ...] = ()`, together with inherited
SDK error fields. `reason_code` is the concrete class name in snake case. Error strings and representations exclude
delivery IDs, namespaces, operation references, entry IDs, and causes; callers may read those attributes explicitly.

## Protocol contracts

Generated packages also expose the shared contracts of the pagination, polling, stream, WebSocket, cache, upload, and
webhook helpers they declare, and copy only the runtime modules those helpers, the declared security schemes, and the
model backend need. Records, options, cache stores, and resume state come from `pkg.protocols`; `ProtocolClientOptions`
and `SessionOptions` come from `pkg.options` when a helper is declared; exceptions come from `pkg.errors`, each with the
helper that raises it. These imports need no HTTP library and start no threads. A client that uses no
protocol settings
loads none of these definitions: `pkg.options` and `pkg.errors` load them the first time one of their names is used. The
helpers that use these contracts are still being implemented; constructing a record or option sends nothing.

### Selectors, targets, and origins

All records below are immutable and keyword-only. A field of the wrong type raises `TypeError`, including a
non-string where a string or one of several strings is expected; an invalid value raises `ValueError`. Values are
kept as given: a record never rewrites a name, pointer, or host into another spelling.

| Type | Fields and accepted values |
|---|---|
| `BodySelector` | `pointer: str`, an RFC 6901 pointer over wire names: `""` or a value starting with `/` in which `~` is followed only by `0` or `1` |
| `HeaderSelector` | `name: str`, an HTTP token; `occurrence: Literal['single', 'all'] = 'single'` |
| `StatusSelector` | No fields; selects the final status code |
| `ParameterTarget` | `location: Literal['path', 'query', 'header', 'cookie']`; `name: str`, nonempty, and an HTTP token for headers and cookies |
| `QuerystringTarget` | `name: str`, the nonempty declaration identity that is never sent; `pointer: str`, an RFC 6901 pointer where `""` is the whole value |
| `BodyTarget` | `pointer: str`, an RFC 6901 pointer where `""` is the whole body |
| `Origin` | `scheme: str`, `host: str`, and `port: int`, exactly as the client's URL rule gives them back for an absolute `http` or `https` URL |

`Selector` is `BodySelector | HeaderSelector | StatusSelector`, and `RequestTarget` is
`ParameterTarget | QuerystringTarget | BodyTarget`.

An `Origin` accepts exactly the triples that the client's own URL rule gives back unchanged: the rule spells the
origin as an absolute URL, interprets that URL, and the resulting scheme, host, and effective port must equal the
given ones. Anything the rule would rewrite or refuse raises `ValueError`, such as `Origin(scheme="https",
host="API.example.com", port=443)`, whose host the rule lowercases, or a scheme other than `http` or `https`. The
host is in the rule's own form; for example, an IPv6 address has no brackets, as in
`Origin(scheme="https", host="::1", port=443)`. A field of the wrong type, including a boolean port, raises
`TypeError`. Constructing an `Origin` loads the client's HTTP library to apply the rule.

### Continuations, poll snapshots, and progress

`Continuation(*, kind, value)` records the position of a helper session. `kind` is one of `cursor`, `offset`, `page`,
`next_url`, and `link`; `value` is a JSON value. The continuation keeps only the value's canonical JSON, described
below, and exposes only `kind`. Its representation is `Continuation(kind='cursor')`. Each continuation equals only
itself, and it cannot be modified.

`PollSnapshot[P]` is an immutable poll result with `state: WireValue`, `terminal: bool`, `data: P`, and
`response: ResponseInfo`. The state is frozen, and the state and data are excluded from the representation. `P` is
covariant, so a `PollSnapshot[Pet]` is also a `PollSnapshot[object]`. `CancelReceipt[C]` is the immutable response of
a remote cancel request, with `data: C` and `response: ResponseInfo`; the data is excluded from the representation, and
`C` is covariant too.

`ProgressKey` is `Literal['pages', 'items', 'polls', 'reconnects', 'parts', 'confirmed_bytes', 'network_send_count',
'network_send_budget_used', 'messages_sent', 'messages_received']`, and `ProtocolProgress` is
`Mapping[ProgressKey, int]`.

Continuations and resume state encode their values as the same canonical JSON: UTF-8 without whitespace, object
members sorted by the code points of their names, and strings escaped as Python's `json.dumps` escapes them with
`ensure_ascii=False`, so only `"`, `\`, and U+0000–U+001F are escaped. An integer is written in plain decimal.
A float is first converted to the decimal of its shortest round-trip representation, so `1e16` becomes `1E+16`. A
decimal is written as its scientific string: no exponent when the exponent is at most 0 and the adjusted exponent is at
least -6, and otherwise one digit before the point and an `E` exponent with a sign. For example, `1.50`, `-0.0`,
`1E+2`, and `1E-7` keep those spellings. A decimal whose string has neither a point nor an exponent is written as an
integer, so `Decimal("-0")` becomes `0`. Decoding the result and encoding it again gives the same bytes. A value
nested beyond the interpreter recursion limit, and an integer beyond the interpreter's decimal conversion limit,
raise `ValueError`.

### Upload records and sources

`UploadProgress(*, confirmed_bytes: int, total_bytes: int, complete: bool = False)` is how far an upload is; the
confirmed bytes never exceed the total. `UploadSource` is `bytes | bytearray | memoryview | BinaryIO`, the content an
upload reads; see [upload helpers](#upload-helpers). The handles are loaded only when first requested.

### Resume state

`ResumeState(*, helper: str, state: WireValue)` is a small, opaque token: the identity of the helper it belongs to and
the state that helper continues from, such as a cursor, a next URL, an operation's poll values, or a Last-Event-ID. The
helper's identity must be a string without lone surrogates. The representation is `ResumeState(version=1)`, each
instance equals only itself, and nothing is written to disk automatically: the caller saves the token where it likes.
`copy.copy` and `copy.deepcopy` return the same instance, which cannot change, and `pickle.dumps` raises `TypeError`.
The same applies to `Continuation`. A token carries no credential, page, digest, or security binding; a resumed call
authenticates with the resuming client's own auth.

`state.export()` returns canonical JSON with the members `helper`, `state`, and `version`, which is `1`.
`import_state(data)` validates the bytes and rejects them in this order:

| Rejected input | Exception |
|---|---|
| Not bytes, or not a JSON object | `ResumeStateError(condition='malformed')` |
| An integer version other than 1, whatever the other members | `ResumeStateError(condition='version')` |
| Unknown, missing, or mistyped members, or a state nested too deeply | `ResumeStateError(condition='malformed')` |

```python
from pkg.protocols import ResumeState, import_state

state = ResumeState(helper="helper", state={"cursor": "c2"})
restored = import_state(state.export())
```

Errors never include the helper's identity or the state. `import_state` does not compare the helper; the helper that
resumes the token compares it with its own, and checks the state, before sending anything.

### Helper options

Every option field defaults to `UNSET`, imported from `pkg.options`. The effective defaults below apply after
merging. `None` removes a limit only where the table allows it. Counts and byte limits are integers, excluding
booleans. Durations are finite numbers of seconds, excluding booleans; integers are accepted. Invalid values raise
`ProtocolConfigurationError` with `condition='invalid_value'` and the field name as `field_path`.

| Type | Field | Effective default | Valid values |
|---|---|---|---|
| `PaginationOptions` | `max_pages` | `None` (no limit) | Positive integer or `None` |
| | `max_items` | `None` (no limit) | Nonnegative integer or `None`; `0` ends without sending |
| | `max_page_bytes` | `8388608` | Positive integer |
| | `max_cursor_bytes` | `65536` | Positive integer |
| `PollOptions` | `max_polls` | `1000` | Positive integer or `None` |
| | `interval` | The declared interval, `1` second by default | Positive duration |
| | `max_wait` | `60` seconds | Positive duration or `None` |
| `StreamOptions` | `idle_timeout` | The merged `stream_idle_timeout` | Positive duration or `None` |
| | `max_line_bytes` | `262144` | Positive integer |
| | `max_event_bytes` | `1048576` | Positive integer |
| | `reconnect` | `False` | `bool` |
| | `max_reconnects` | `5` | Nonnegative integer or `None` |
| | `max_reconnect_wait` | `60` seconds | Positive duration or `None` |
| `UploadOptions` | `chunk_bytes` | `8388608`, at most the helper's `max_chunk_bytes` | Positive integer |
| | `max_parts` | `10000` | Positive integer or `None` |
| | `max_uncertain_probes` | `3` | Nonnegative integer |
| `CacheOptions` | `max_entry_bytes` | `2097152` | Positive integer: the largest body a fetch stores |
| | `max_ttl` | `300` seconds | Positive duration: the cap on any entry's freshness |
| `WSOptions` | `open_timeout`, `idle_timeout`, `send_timeout`, `ping_interval`, `pong_timeout` | See [WebSocket limits](#websocket-limits) | Positive duration or `None` |
| | `close_timeout` | `5` seconds | Positive duration |
| | `max_message_bytes`, `max_queue` | `1048576` and `16` | Positive integer |
| | `compression` | `None` | `"deflate"` or `None` |


`ProtocolSecurityContext(*, credential_partition: str, allowed_origins: tuple[Origin, ...] = ())` names the
nonsecret credential partition of helper state and the origins permitted in addition to the same origin. The
partition must be nonempty and free of control characters, and it is excluded from the representation. A list of
origins is copied into a tuple; an origin listed twice raises `ProtocolConfigurationError`.

### Client protocol settings

Protocol settings belong to the client only; `RequestOptions` has no `protocols` field. `ProtocolDefaults` comes from
`pkg.protocols`.

```python
from pkg import Client
from pkg.options import ClientOptions, ProtocolClientOptions, SessionOptions
from pkg.protocols import PaginationOptions, ProtocolDefaults, ProtocolSecurityContext

options = ClientOptions(
    protocols=ProtocolClientOptions(
        security=ProtocolSecurityContext(credential_partition="tenant-a"),
        defaults={
            "users.all": ProtocolDefaults(
                session=SessionOptions(max_network_sends=100),
                options=PaginationOptions(max_pages=10),
            ),
        },
    ),
)
client = Client(options=options)
```

| Setting | Type | Meaning |
|---|---|---|
| `ClientOptions.protocols` | `ProtocolClientOptions \| None`, default `UNSET` | `None` or `UNSET` uses every protocol default. Another type raises `ConfigurationError(condition='invalid_type')` |
| `ProtocolClientOptions.security` | `ProtocolSecurityContext \| None`, default `UNSET` | `None` means anonymous use. Its `allowed_origins` are the origins beyond the server's that a next-URL or Link pagination helper may follow a URL to |
| `ProtocolClientOptions.defaults` | `Mapping[str, ProtocolDefaults]`, default `UNSET` | Keys are helper names: Python identifiers separated by dots, without keywords or empty parts. The mapping is copied into a read-only mapping, and its values keep their identity |
| `ProtocolDefaults.session` | `SessionOptions`, default `UNSET` | Session limits of that helper |
| `ProtocolDefaults.options` | `PaginationOptions \| PollOptions \| StreamOptions \| CacheOptions \| WSOptions \| UploadOptions`, default `UNSET` | Kind-specific options of that helper |
| `ProtocolClientOptions.cache_stores` | `Mapping[str, CacheStore \| AsyncCacheStore]`, default `UNSET` | The store each [cache helper](#cache-helpers) keeps its entries in, by helper name. The mapping is copied into a read-only mapping that keeps each store's identity; the client borrows the stores and never closes them |
| `ProtocolClientOptions.websocket_connector` | `WebSocketConnector \| AsyncWebSocketConnector \| None`, default `UNSET` | A borrowed connector that opens WebSocket connections; see [connectors and transports](#connectors-and-transports) |
| `ProtocolClientOptions.websocket_transport` | `WebSocketTransportOptions`, default `UNSET` | TLS contexts, proxy, and `trust_env` of WebSocket connections |

Invalid values inside `ProtocolClientOptions` and `ProtocolDefaults` raise `ProtocolConfigurationError`. Explicit
call options take precedence over these defaults, which take precedence over the effective defaults above.

### Protocol exceptions

Every constructor is keyword-only. Besides the fields below, each accepts `helper_id`, `operation`,
`operation_id`, `call_id`, `parent_session_id`, `info`, `cause`, `secondary_errors`, and the shared SDK counters.
Invalid field values raise `ValueError`.

| Exception | Direct base | Fields |
|---|---|---|
| `ProtocolDataError` | `ProtocolError` | `condition: Literal['missing', 'null', 'type', 'value', 'malformed', 'inconsistent'] = 'value'`, `location: Selector \| RequestTarget \| None = None` |
| `ProtocolStateError` | `ProtocolError` | `state: str`, `action: str` |
| `SessionLimitError` | `ProtocolError` | `kind: Literal['network_sends', 'pages', 'items', 'polls', 'reconnects', 'parts']`, `limit: int`, `progress: ProtocolProgress`, `resume_state: ResumeState \| None = None` |
| `StreamResumeExhaustedError` | `SessionLimitError` | The same fields; `kind` is `reconnects` or `network_sends` |
| `ResumeStateError` | `ProtocolError` | `condition: Literal['version', 'fingerprint', 'expired', 'malformed']` |
| `PaginationCycleError` | `ProtocolDataError` | `page_index: int`, `first_seen_page_index: int`, `resume_state: ResumeState \| None = None`; `condition` is always `inconsistent` |
| `PollingStateError` | `ProtocolDataError` | `condition: Literal['type', 'value'] = 'value'` |
| `PollWaitLimitError` | `ProtocolError` | `kind: Literal['wait', 'deadline']`, `required_wait: float`, `limit: float`, `resume_state: ResumeState \| None = None` |
| `OperationFailedError[P]` | `ProtocolError` | `snapshot: PollSnapshot[P]`, a read-only property |
| `OperationCancelledError[P]` | `ProtocolError` | `snapshot: PollSnapshot[P]`, a read-only property |
| `StreamDecodeError` | `ProtocolDataError` | `sequence: int`, `raw_prefix: bytes` of at most 65536 bytes, `truncated: bool`; `condition` defaults to `malformed` |
| `StreamInterruptedError` | `ProtocolError` | `condition: Literal['eof', 'transport']`, `sequence: int`, `resume_state: ResumeState \| None = None` |
| `IncompleteFrameError` | `StreamInterruptedError` | `buffered_bytes: int`; `condition` is always `eof` |
| `StreamRemoteError[E]` | `ProtocolError` | `event_type: str \| None`, `data: E`, a read-only property, `sequence: int` |
| `CacheStoreError` | `ProtocolStoreError` | `action`, the store method that failed, and `entry_id: str \| None = None` |
| `CacheProtocolError` | `ProtocolDataError` | No other fields; `condition` is always `inconsistent` |
| `CacheValidatorConflictError` | `ProtocolConfigurationError` | `header_name: Literal['If-None-Match', 'If-Modified-Since']`; `field_path` is the header's name and `condition` is always `binding_mismatch` |
| `ConcurrentReceiveError` | `ProtocolStateError` | None; `state` is always `receiving` and `action` always `receive` |
| `WebSocketClosedError` | `ProtocolError` | `code: int \| None`, `reason: str` of at most 123 UTF-8 bytes, `clean: bool` |
| `WebSocketHandshakeError` | `TransportError` | `condition: Literal['invalid_message', 'invalid_header', 'upgrade', 'negotiation', 'security', 'size']`, `delivery_state`, `retry_stop_reason = None`; `phase` is always `connect` |
| `WebSocketProxyError` | `TransportError` | `proxy_status_code: int \| None = None`, `retry_stop_reason = None`; `phase` is always `connect` and `delivery_state` `NOT_SENT` |
| `HandshakeResponse` | `ProtocolError` | `status_code: int`, `headers: HeadersView`, `body_prefix: bytes` of at most 65536 bytes, `truncated: bool`; raised only by connectors |
| `DeliveryUnknownError` | `ProtocolError` | `delivery_state`, `MAYBE_SENT` or `RESPONSE_STARTED`, `resume_state: ResumeState \| None = None`, `message_id: str \| None = None` |
| `NonResumableSourceError` | `ProtocolConfigurationError` | `source_kind: Literal['iterable', 'iterator', 'stream', 'reader']`; `field_path` is always `('source',)` and `condition` `wrong_capability` |
| `UploadDeliveryUnknownError` | `DeliveryUnknownError` | `phase: Literal['append', 'part', 'complete']`, `part` being reserved for the parts profile, `progress: UploadProgress`; no `message_id` |
| `UploadSourceChangedError` | `ProtocolDataError` | `expected_size: int`, the upload's size, and `actual_size: int`, the size the source has now; `condition` is always `inconsistent` |
| `UploadOffsetError` | `ProtocolDataError` | `confirmed_offset: int`, `expected_offset: int`, `remote_offset: int`, `size: int`, `resume_state: ResumeState \| None = None`; `condition` is always `inconsistent` |
| `UploadExpiredError` | `ResumeStateError` | `expires_at: datetime`, timezone-aware; `condition` is always `expired` |

A field whose value is fixed is not a constructor argument, so passing it raises `TypeError`. `progress` is copied into
a read-only mapping. Messages and representations exclude locations, progress, resume state, snapshots, raw bytes, event
types, event data, and tags; read those attributes explicitly. The payload type parameters are covariant and default to
`object`, so an unparameterized `OperationFailedError` has `object` snapshot data. Narrow the payload explicitly before
using it as a model.

## Protocol helper configuration

Pagination, polling, SSE or NDJSON stream, WebSocket, webhook, cache, and resumable upload helpers of an API are declared
in a helper configuration, which the client target reads through its `protocols` setting. The helpers are still being
implemented: generation validates every helper, resolves its references against the selected API, and records it in the
target manifest. An enabled pagination helper generates the [pagination helper](#pagination-helpers) below, an enabled
polling helper the [polling helper](#polling-helpers), an enabled `offset` upload helper the
[upload helper](#upload-helpers), an enabled SSE helper the [SSE stream helper](#sse-stream-helpers), an enabled NDJSON
helper the [NDJSON stream helper](#ndjson-stream-helpers), an enabled WebSocket helper the
[WebSocket helper](#websocket-helpers), an enabled cache helper the [cache helper](#cache-helpers), and an enabled webhook helper the
[webhook verification helper](#webhook-verification-helpers). A disabled helper generates nothing, so the package is the
same as without it. The `parts` profile of `resumable_upload`
fails with `E_CLIENT_UNSUPPORTED` whether the helper is enabled or not, and its settings are not read yet; an enabled
upload helper declaring `abort` or `create.session_url` fails with `E_CLIENT_UNSUPPORTED` too.

| Setting | Values | Default | Where |
|---|---|---|---|
| `protocols` | A helper file path, a `ProtocolConfiguration`, or none | None | Target file: `protocols = "client-protocols.yaml"`, relative to the target file; Python: a `Path`, relative to the working directory, or a `ProtocolConfiguration` record |

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.protocols.toml -->
<!-- fmt: off -->

```toml
schema_version = 1
output = "client"
package = "client"
model_package = "models"
protocols = "client-protocols.yaml"
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.protocols.toml -->

A setting that is neither a path nor a `ProtocolConfiguration` fails with `E_CONFIG_VALUE`. The helper file is read
only when the target is generated, so its problems are reported then, with the target's identity. Without
`protocols`, nothing is read and the manifest records no helpers.

### The helper file

The file is UTF-8 YAML holding one mapping with `schema_version: 1` and `helpers`, a mapping from each helper's name
to its definition, in declaration order:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.protocols.yaml -->
<!-- fmt: off -->

```yaml
schema_version: 1
helpers:
  users.all:
    kind: pagination
    enabled: false
    operation: /paths/~1users/get
    items: {from: body, pointer: /data}
    item_schema: {pointer: /components/schemas/User, document: ../helpers.yaml}
    continuation:
      kind: cursor
      read: {from: body, pointer: /next_cursor}
      write: {in: query, name: cursor}
      end: [{kind: missing}, {kind: "null"}]
      empty_string: value
    bindings:
      - target: {in: header, name: snapshot}
        value:
          source: initial
          selector: {from: header, name: Snapshot, occurrence: all}
  jobs.run:
    kind: polling
    enabled: false
    create: /paths/~1jobs/post
    accepted_statuses: [202]
    poll: /paths/~1jobs~1{jobId}/get
    bindings:
      - target: {in: path, name: jobId}
        value:
          source: initial
          selector: {from: body, pointer: /id}
    state: {from: body, pointer: /status}
    pending: [queued, running]
    succeeded: [done]
    failed: [failed]
    cancelled: [cancelled]
    result:
      kind: inline
      selector: {from: body, pointer: ""}
      schema: {pointer: /components/schemas/Job}
    interval: {seconds: 2, retry_after_header: Retry-After}
    remote_cancel:
      operation: /paths/~1jobs~1{jobId}/delete
      bindings:
        - target: {in: path, name: jobId}
          value:
            source: initial
            selector: {from: body, pointer: /id}
    immediate_result:
      statuses: [200, 201]
      selector: {from: body, pointer: ""}
      schema: {pointer: /components/schemas/Job}
    expires_at: {from: body, pointer: /expires}
  records.watch:
    kind: ndjson
    enabled: false
    operation: /paths/~1records/get
    media: application/x-ndjson
    event_schema: {pointer: /components/schemas/Record, document: ../helpers-external.yaml}
    completion: {kind: sentinel, value: "[DONE]"}
    final_line: allow_eof
    resume:
      enabled: true
      cursor: {from: body, pointer: /id}
      write: {in: query, name: after}
      reopen_operation: /paths/~1records/get
      delivery: at_least_once
      missing: inherit
      "null": clear
      expires_at: {from: header, name: X-Expires}
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.protocols.yaml -->

- A file that cannot be read, is not UTF-8 or YAML, holds more than one document, uses an anchor, an alias, or a
  merge key, nests collections more than 64 levels deep, repeats a key in one mapping, or is not a mapping fails with
  `E_CONFIG_VALUE`. Keys must be strings; quote `"null"`, which YAML otherwise reads as null.
- The file is read with the generator's own YAML rules: only `true` and `false` are booleans, in any of the cases
  `true`, `True`, and `TRUE`, so `yes` is a string; dates stay strings; and other scalars follow PyYAML's YAML 1.1
  rules, so `1_000` is an integer. A tag that builds another type, such as `!!omap`, is not a JSON value.
- Every key is checked at every level. A key the definition does not have, including a setting of another kind or
  one that the chosen variant does not take, fails with `E_CONFIG_UNKNOWN`; a missing required key or a value of
  the wrong type fails with `E_CONFIG_VALUE`; and settings that contradict each other fail with `E_CONFIG_CONFLICT`.
- A helper name is Python identifiers separated by dots, each in NFKC form, not a keyword, not starting with `_`, and
  not a Windows device name such as `con` or `com1`. Two names equal after NFC normalization and case folding, or
  one name that is a dotted prefix of another, such as `users` and `users.all`, fail with `E_NAME_COLLISION`.
- Every definition has `kind`, one of `pagination`, `polling`, `sse`, `ndjson`, `websocket`, `webhook`, `cache`,
  and `resumable_upload`, and `enabled`, a boolean that defaults to `true`.

### Shared values

| Value | Forms |
|---|---|
| Operation reference | An RFC 6901 pointer string to a root path operation, or `{pointer, document?}`. A document names the root input, relative to the helper file, or to the working directory for Python records |
| Schema reference | `{pointer, document?}` with an RFC 6901 pointer; an omitted document is the root input. A document, resolved as for operation references, must be one of the accepted input's documents |
| Selector | `{from: body, pointer}`, an RFC 6901 pointer over wire names; `{from: header, name, occurrence?}` with `occurrence` `single` (default) or `all`; or `{from: status}`. `all` is accepted only in a binding value |
| Request target | `{in: path\|query\|header\|cookie, name}`, `{in: querystring, name, pointer}` with the declared querystring's name, or `{in: body, pointer}` |
| Binding | `{target, value}`, where `value` is `{source, selector}` with `source` `input`, `initial`, or `previous`, or `{literal}` with any JSON value whose text is UTF-8 and whose containers nest at most 64 levels |
| End condition | `{kind: missing}`, `{kind: "null"}`, or `{kind: value, value}` with a JSON value other than null. A list of them is nonempty and names each condition once |

A reference must select a root path operation of the input, or generation fails with `E_OPERATION_REF`; webhooks and
path items of other documents are refused. An enabled helper whose operation the selection excludes fails with
`E_SELECTOR_DEPENDENCY`; a disabled one may keep it. Each request target must exist in the operation it writes to:
the declared parameter of that location and name, header names compared without case, the operation's own
querystring, or its request body. An operation that owns a querystring takes no `in: query` target.

### Helper kinds

| Kind | Required | Optional |
|---|---|---|
| `pagination` | `operation`, `items` (a body selector), `item_schema`, `continuation` | `bindings` (`[]`), such as a snapshot token each request carries |
| `polling` | `create`, `accepted_statuses`, `poll`, `bindings` (create to poll), `state`, `pending`, `succeeded`, `result` | `failed` and `cancelled` (`[]`), `interval` (`{seconds: 1, retry_after_header: null}`), `remote_cancel` (`{operation, bindings?}`), `immediate_result` (`{statuses, selector, schema}`), `expires_at` |
| `sse`, `ndjson` | `operation`, `media`, `event_schema`, `completion`, and for `ndjson` `final_line` (`require_newline` or `allow_eof`) | `unknown` (`error`, the default, or `raw`), `error_events` (`{}`), `resume` (`{enabled: false}`) |
| `websocket` | `operation` (a GET without a body), `send`, `receive` (each `{codec: json\|utf8\|bytes, frame?, schema?}`) | `subprotocols` (`[]`), `compression` (`false`) |
| `webhook` | `event_schema`, `signature` (see [webhook verification helpers](#webhook-verification-helpers)) | None |
| `cache` | `operation`, `validator` (`etag`, `last_modified`, or `both`), `authenticated` (a boolean) | `statuses` (`[200]`), `vary_allowlist` (`[]`); see [cache helpers](#cache-helpers) |
| `resumable_upload` | `profile` (`offset`), `create` (`{operation, size?, expires_at?}`), `probe` (`{operation, remote_offset, bindings?}`), `append` (`{operation, offset, length?, checksum?, bindings?}`, a `checksum` being `{target, algorithm, encoding?, algorithm_prefix?}`), `max_chunk_bytes` (a positive integer), `partial_commit` (`allowed` or `forbidden`), `completion` | `abort` (`{operation, bindings?}`) and `create.session_url`, both refused as not supported yet |

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.cache.fields -->
Cache helper fields: `operation`, `validator`, `authenticated`, `statuses`, `vary_allowlist`, `enabled`. See [cache helpers](#cache-helpers).
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.cache.fields -->

A pagination `continuation` is one of these:

| `kind` | Settings |
|---|---|
| `cursor` | `read`, `write`, `end`, and `empty_string` (`value` or `end`), which alone decides the empty string, so `end` cannot list it |
| `offset`, `page` | `write`, `first` (an integer of at least 0), `step` (`{literal: <positive integer>}` or `{page_items_count: true}`), and exactly one of `has_more` and `total` |
| `next_url` | `read`, `end`, and `repeat_request_body` (`false`) |
| `link` | `header`, the response header's name, and `rel` (`next`) |

Polling statuses are distinct integers from 100 to 599, and `immediate_result` cannot list an accepted status. The
four state lists hold JSON values: a list that repeats a value fails with `E_CONFIG_VALUE`, and a value that two lists
share fails with `E_CONFIG_CONFLICT`. `result` is `{kind: inline, selector, schema}`,
`{kind: operation, operation, bindings?}`, or `{kind: none}`, and `interval.seconds` is positive.

An upload's `completion` is `{kind: length}` or `{kind: operation, operation, result_schema, bindings?}`. The `parts`
profile is refused with `E_CLIENT_UNSUPPORTED` without reading its other settings.

A stream's `event_schema` is one schema reference, or `{discriminator, mapping}` where `discriminator` is
`{from: event_type}` (SSE only) or `{from: body, pointer}` and `mapping` names the schema of each event type.
`unknown: raw` and `error_events` need a discriminator, and an error event cannot also be a mapped event. `completion`
is `{kind: eof}`, `{kind: sentinel, value}`, or `{kind: event_type, value}` (SSE only). An enabled `resume` takes
`cursor` (a selector, or `event_id` for SSE), `write`, `reopen_operation`, `delivery: at_least_once`, `bindings`
(`[]`), `reconnect_on` (a nonempty list of `transport_interruption` and `incomplete_eof`, by default
`[transport_interruption]`), and optionally `expires_at`; a selector cursor also takes `missing` (`inherit` or
`error`) and `"null"` (`clear` or `error`). A disabled `resume` takes no other key.

Helpers reserve the arguments `session_options`, `pagination_options`, `poll_options`, `stream_options`,
`ws_options`, `cache_options`, `upload_options`, `source`, `items`, and `state`. When
an enabled helper's operation, or its `create` operation for polling and uploads, has a parameter or field argument
with one of these names, generation fails with `E_NAME_COLLISION` instead of renaming it; name the argument with
`parameter_names` or `body_field_names`:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.protocols.diagnostics -->
<!-- fmt: off -->

```text
E_NAME_COLLISION target protocols.helpers['tags.all'] /paths/~1tags/get: The pagination helper 'tags.all' reserves the argument 'items' of GET /tags; rename them with parameter_names or body_field_names
E_NAME_COLLISION target protocols.helpers['jobs.run'] /paths/~1jobs/post: The polling helper 'jobs.run' reserves the argument 'state' of POST /jobs; rename them with parameter_names or body_field_names
E_SELECTOR_DEPENDENCY selection protocols.helpers['audit.all'].operation /paths/~1admin~1audit/get: The enabled pagination helper 'audit.all' needs GET /admin/audit, which the selection excludes (excluded by exclude_tags 'admin')
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.protocols.diagnostics -->

### Python records and the manifest

`ProtocolConfiguration(schema_version=1, helpers={...})` takes `PaginationHelper`, `PollingHelper`, `StreamHelper`,
`WebSocketHelper`, whose `send` and `receive` are `WebSocketMessage` records, `WebhookHelper`, `CacheHelper`, and
`ResumableUploadHelper` records, which mirror the file: their fields have the file's names, with `from_` for `from`,
and they take the client's `Selector` and `RequestTarget` records, `OperationRef` or a pointer string, and `SchemaRef`.
A webhook uses `StandardWebhooksSignature()`, `StripeStyleSignature(header="Stripe-Signature")`, or
`BodyHmacSignature(header="X-Signature", algorithm="hmac-sha256", encoding="hex", prefix="")`.
`PublicKeySignature(kind="ed25519", header="X-Signature")` verifies raw-body public-key signatures.
`AdapterSignature(timestamp="required", delivery_id="none")` declares application verification, and `NoSignature()`
declares an unsigned webhook. An event mapping is an `EventMapping` with an `EventDiscriminator(from_="body",
pointer=...)`, and an upload takes
`UploadCreate`, `UploadProbe`, `UploadAppend` with an optional `UploadChecksum`, `LengthCompletion()` or
`OperationCompletion`, and `UploadAbort` records. They are validated as the file is, with the same diagnostics,
when the client configuration is constructed.

```python
ClientGenerationConfig(
    output=Path("client"),
    package="client",
    model_package="models",
    protocols=ProtocolConfiguration(
        helpers={
            "users.all": PaginationHelper(
                enabled=False,
                operation="/paths/~1users/get",
                items=BodySelector(pointer="/data"),
                item_schema=SchemaRef(pointer="/components/schemas/User"),
                continuation=LinkContinuation(header="Link"),
            ),
        },
    ),
)
```

The manifest lists every helper in declaration order in `protocol_helpers`, disabled ones included, with its name,
kind, `enabled`, the pointer of its metadata, and the digest of its contract. A generated helper's digest covers its
signature and settings, which spell its types, its operation, its item schema, and the schema of its page; a disabled
helper's contract is empty. `inputs.target_config.protocol_metadata` holds each helper's settings with the defaults filled in and each
reference replaced by the manifest's reference to the accepted input, so a file and the equal Python records record
the same metadata. The `protocols` setting itself is not recorded among the public options.

## Pagination helpers

An enabled pagination helper, whatever its continuation, is generated at `client.protocols.<name>` on `Client` and
`AsyncClient` alike; the property exists only when the package has a helper. `page` fetches the first
page, `next_page` the page after one the helper returned, and `iterate` returns a pager, which sends nothing until it is
iterated; `resume` returns a pager continuing a pager's checkpoint. A helper takes the operation's parameters and its
body as keywords, never field arguments, then `pagination_options`, `options`, and `session_options`:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.pagination.helper -->
<!-- fmt: off -->

```python
    def page(
        self,
        *,
        cursor: _dcg_type_0 | Unset = UNSET,
        limit: _dcg_type_1 | Unset = UNSET,
        x_snapshot: _dcg_type_2 | Unset = UNSET,
        pagination_options: PaginationOptions | None = None,
        options: RequestOptions | None = None,
        session_options: SessionOptions | None = None,
    ) -> Page[_dcg_type_3, ListUsersResponse]:
        """Fetch the first page of GET /users."""
        return first_page(
            self._core,
            _plans.PLAN_0,
            (cursor, limit, x_snapshot),
            pagination_options=pagination_options,
            options=options,
            session_options=session_options,
        )
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.pagination.helper -->

```python
with Client() as client:
    helper = client.protocols.users.all
    for user in helper.iterate():
        print(user.id)
    first = helper.page()
    following = helper.next_page(first)
```

A `Page[T, P]` holds its `items` as a tuple of the item type, the decoded response as `data`, the response's
`ResponseInfo`, and the `continuation` after it, or None on the last page. `Pager[T, P]` iterates over the items and
`iter_pages()` over the pages; `AsyncPager[T, P]` does the same with `async for`, and its `iter_pages()` is not
awaited. `Page`, `Pager`, and `AsyncPager` are imported from `pkg.protocols`, in every package. A pager fetches a page
only once the previous one is consumed and reads, decodes, and closes each page inside its own call, so leaving a loop
early holds no response. Its `progress` reports the pages fetched, the items delivered, and the session's sends.

### Cursors and end conditions

A page's items must be a JSON array at the `items` pointer; an empty array does not end the traversal. The cursor is
read from the page's body, a response header, or its status, and written to the target of `write`: a path, query, or
header parameter, a property of the querystring, or a member of the JSON request body, never a position that carries
credentials. A cursor the server
returned is sent as it came, without its target's codec, while a start cursor the caller passes is encoded like any
other argument. The traversal ends at a page whose cursor is missing
or null where the helper declares that end, is one of its end values, or is empty with `empty_string: end`. A missing
or null cursor that no end covers, missing or null items, a header repeated for a single cursor, and a value read for
a path parameter that makes its segment, encoded in the parameters' styles with the caller's own path arguments and
the helper's literals beside it, a dot segment (`.` or `..`, `%2E` in either case counting as `.`) raise
`ProtocolDataError`, and a cursor over its size limit raises `ProtocolSizeError`, each as a failure of the page's call.
The error names the first such read value whose encoded text is non-empty, or else the first one. A continuation
returned by an earlier page of the same pager, or an earlier page `next_page` continued from, ends it with
`PaginationCycleError` after the repeating page.

### Offsets and page numbers

An `offset` or `page` continuation counts positions instead of reading them. The first request is sent as the caller
gives it. The traversal starts at the position the caller passes for the `write` target, read from the first request
after its usual encoding: the parameter's value, or the member at the pointer of the querystring or JSON body. An
integral number such as `40.0` on a `number` target starts at its integer. Without one it starts at `first`, the offset
or page number the server gives a request without one. A starting value that is not an integer fails like any invalid
argument, with `RequestEncodingError` when its codec refuses it, and otherwise with `ProtocolDataError` (condition
`type`, location the target) before anything is sent. Each request after a page writes the page's position advanced by
the `literal` step, or for an offset by the page's item count with `page_items_count`; a page number always advances by
a literal step, and generation refuses `page_items_count` for it. The position is written to `write` like a cursor, so
its target must accept integers, and `Page.continuation` has the kind `offset` or `page`.

```yaml
helpers:
  users.by_offset:
    kind: pagination
    operation: /paths/~1users/get
    items: {from: body, pointer: /data}
    item_schema: {pointer: /components/schemas/User}
    continuation:
      kind: offset
      write: {in: query, name: offset}
      first: 0
      step: {page_items_count: true}
      total: {from: header, name: X-Total-Count}
```

The traversal ends at a page whose `has_more` reads `false`, or once the items counted before the next position reach
the `total` the page reads. For an offset they are the offsets past `first`, since a total counts the whole collection
wherever the traversal started; for a page number they are the items delivered since the start, the only count a page
number gives, and a page without items ends it too, since no later page can bring the count nearer. A `total` of 0 ends
at the first page, and a position past the total ends too. `has_more` must read a JSON boolean and `total` a JSON
integer of at least 0; a header spells `true` or `false`, or decimal digits with an optional `-`, and generation checks
the types its declared schema gives. A missing, null, or mistyped value raises `ProtocolDataError`, and a negative
total, from the body or a header, raises it with the condition `value`. Otherwise an empty page does not end the
traversal, but one that continues while `page_items_count` counts items would ask for the same position again, so it
raises `ProtocolDataError` with the condition `inconsistent`. Positions only grow, so these helpers never raise
`PaginationCycleError`, and `max_cursor_bytes` does not apply to them.

### Next URLs and Link headers

A `next_url` or `link` continuation follows the URL a page gives. The first request is sent as the caller gives it, and
each later page goes to the URL the last page gave, as it came: the caller's query and the query patches of the client,
a view, or the call are never laid over it, while the call's headers and the operation's header and cookie parameters
are sent again. A later page is a GET without a body, unless a next URL sets `repeat_request_body: true`, which sends
the operation's method with the caller's body again; generation refuses it, with `E_CONFIG_VALUE`, unless the body may
be sent again as a 307 or 308 redirect sends it: the operation's method is GET, HEAD, OPTIONS, PUT, or DELETE, or its
`retry_safety` is `idempotent`. A `next_url` reads the URL with its `read` selector and ends like a cursor: at a missing
or null value only where an `end` condition says so, or at one of its end values, and a missing or null value no end
covers, or a value that is not a string, raises `ProtocolDataError`. A `link` continuation reads the RFC 8288 links of
the `header` field, every value of it, and follows the one whose `rel` lists the relation, `next` unless `rel` names
another; a page without that link is the last. An empty page with a next URL does not end the traversal.

```yaml
helpers:
  users.linked:
    kind: pagination
    operation: /paths/~1users/get
    items: {from: body, pointer: /data}
    item_schema: {pointer: /components/schemas/User}
    continuation: {kind: link, header: Link}
```

The Link header is read strictly. Links are separated by commas, each a `<URI-Reference>` followed by `;` and its
parameters, a token or a quoted string with backslash escapes, so a comma or semicolon inside quotes separates nothing;
empty list elements and the whitespace around separators are skipped. Parameter names and relation types match
without regard to ASCII case, a `rel` lists several relation types separated by spaces, and only a link's first `rel`
counts. A link with an `anchor` parameter describes another resource and is skipped. Any other syntax raises
`ProtocolDataError` with the condition `malformed`, two links of the relation raise it with `inconsistent`, and Link
values whose UTF-8 bytes together exceed `max_cursor_bytes` raise `ProtocolSizeError` with the kind `headers`.

A URL, from either continuation, resolves against the URL of the request that returned the page, after any redirect, as
RFC 3986 resolves a reference. A reference with characters outside RFC 3986, or with brackets anywhere but around an
IPv6 host, raises `ProtocolDataError` with `malformed`, and one with a fragment, user information, even empty, a scheme
other than `http` or `https`, or an invalid port raises it with `value`. The resolved URL, without the query fields the
client's authentication places itself, must be at most 8 KiB of UTF-8, or `max_cursor_bytes` when that is smaller, or it
raises `ProtocolSizeError` with the kind `cursor`, so relative references cannot grow it page by page. The URL must name
the origin of the server the operation is sent to, its scheme, host, and port, or an origin
`ProtocolSecurityContext.allowed_origins` lists; any other origin raises `ProtocolDataError` with `value` before
anything is sent to it, and so does a `next_page` whose options select a server whose origin the URL no longer shares or
is allowed beside. A request to an allowed origin other than the server's, like a redirect to another origin, carries no
`Authorization`, `Proxy-Authorization`, `Cookie`, or `Cookie2` header, and none of the headers or query fields the
package's security schemes name, whether the authentication, a header patch, a parameter, a binding, or the server's URL
put them there; its other headers, the call's and the operation's, are sent as usual. The client's authentication sends
credentials and signatures there only when its `AuthConfig.allowed_origins` lists that origin too; otherwise the page
fails with `AuthConfigurationError(condition="origin_denied")` before sending. A query credential the URL repeats is
removed before the client places its own. `Page.continuation` has the kind `next_url` or `link`, and a URL an earlier
page of the session gave, or the URL of the first page itself, ends the traversal with `PaginationCycleError` after the
repeating page; URLs compare once resolved and without the authentication's query fields, with the host's case, the
default port, and dot segments normalized, but not their percent-encoding.

### Bindings and request targets

Each request after the first is the first request with the helper's `bindings` written in order, then the cursor or
position. A binding writes a `literal`, or what its selector reads from the `initial` page's response, kept for the
whole traversal, or from the `previous` page's; a header selector with `occurrence: all` reads every value of the header
as an array. The bindings are read from every page that has a next page, even one a caller never continues, and a value
its selector finds missing raises `ProtocolDataError`; a header none of whose values is present counts as missing under
`occurrence: all` too. A next-URL or Link helper writes no cursor; its bindings write headers, and the JSON body only
when a next URL repeats it. A `null` is written as it is to a JSON body or a querystring of JSON content, the
only targets that can carry it. A parameter target replaces the argument. A querystring or body target writes into the
caller's querystring or JSON body, encoded and checked as in any call, or into an empty object when the call gives none;
a missing, null, or other non-object value on its pointer is replaced by an empty object. The querystring is then
encoded once, so no query pair is added. The values a server gave are never checked against their targets' schemas. A
call's `options` must not patch a header or a query parameter the helper writes: `iterate`, `page`, and `next_page` raise `ProtocolConfigurationError` with a `field_path` of `("options",
"headers" or "query", <name>)` before sending. A binding with `source: input` is not supported yet.

### Limits and sessions

A pager, and each `page` or `next_page` call, is one session. Every page is its own call, with its own retries, total
timeout, and idempotency key, and the session bounds all of them: a page's deadline is the earlier of its own and the
session's, and each of its sends takes a slot of the session too; an OAuth token request takes none. Each limit comes
from the call's options, then the helper's `ProtocolDefaults` in `ProtocolClientOptions.defaults`, then the default
below:

| Limit | Default | None |
|---|---|---|
| `PaginationOptions.max_pages` | No limit | Removes the limit |
| `PaginationOptions.max_items` | No limit; 0 ends a pager at once without sending | Removes the limit |
| `PaginationOptions.max_page_bytes` | 8 MiB of decoded body per page | Not allowed |
| `PaginationOptions.max_cursor_bytes` | 64 KiB, UTF-8 for a string and canonical JSON otherwise; also a next URL's bytes, within 8 KiB, and a page's Link values | Not allowed |
| `SessionOptions.total_timeout` | No limit | Removes the limit |
| `SessionOptions.deadline` | None | No deadline |
| `SessionOptions.max_network_sends` | No limit | Removes the limit |

A limit reached while pages remain raises `SessionLimitError` with the progress so far; the last page ends normally even
exactly at a limit. An item limit is exact for items, while `iter_pages` checks it before each fetch, so a page that
crosses it is delivered whole, and `page` or `next_page` with `max_items=0` raises `SessionLimitError(kind="items",
limit=0)` without sending. A retry the session has no slot for is not made, and the page's error keeps
`retry_stop_reason="parent_budget_exhausted"`, as does a rejected token's recovery when the session has no slot for the
resend. Defaults naming a helper the package lacks, or giving it another kind's options, fail the client's construction
with `ProtocolConfigurationError`, and so do options of another type, and an idempotency key fixed by the client's
options, `with_options`, or the call, when a helper is called.

A pager is used by one consumer at a time and in one mode: stepping it while it fetches, closing it then, and
mixing items with pages raise `ProtocolStateError`. After a failure, cancellation included, and after `close()`,
every step raises `ProtocolStateError`, and nothing is fetched again. Hooks and limiters see each page's call with the
session's `parent_session_id`, which its errors carry too; an error `next_page` raises before its session starts, such
as for a page it did not return, has `parent_session_id=None`.

### Checkpoints and resume

`pager.checkpoint()` returns a `ResumeState` without sending, on `Pager` and `AsyncPager` alike, and on a pager that
failed or was closed; a pager fetching a page raises `ProtocolStateError`. The helper's
`resume(state, *, pagination_options=None, options=None, session_options=None)` is not awaited, even on `AsyncClient`,
and returns a pager in a session of its own that sends nothing until it is iterated:

```python
from pkg.errors import SessionLimitError
from pkg.protocols import PaginationOptions, import_state

with Client() as client:
    helper = client.protocols.users.all
    pager = helper.iterate()
    first = next(pager)
    saved = pager.checkpoint().export()
    for user in helper.resume(import_state(saved)):
        print(user.id)
    try:
        list(helper.iterate(pagination_options=PaginationOptions(max_items=100)))
    except SessionLimitError as error:
        if error.resume_state is not None:
            rest = list(helper.resume(error.resume_state, pagination_options=PaginationOptions(max_items=None)))
```

| Saved | Never saved |
|---|---|
| The wire values of the call's parameters, as its first request encoded and checked them | The call's `options`, headers, query patches, and cookies, and anything its auth adds |
| The JSON body, when the next request sends it, with its declared media type and any selector's concrete type | The body of a next-URL or Link helper that does not repeat it, once its first page is fetched |
| Where the pager continues: the index, item count, continuation, and binding values of the last page, or of the page before it while the last page has items left | The session, its deadline, and its send counters |
| How many items of the page it fetches next were already delivered | Pages, their bodies, the cycle history, and model objects |

A call that gives a cookie parameter, a header the client treats as a credential, a parameter at the position of a
declared security scheme, or a querystring with a field at such a position cannot be checkpointed: `checkpoint()` raises
`ProtocolConfigurationError` with a `field_path` of `("arguments", <location>, <name>)`, and its limit and cycle errors
keep `resume_state=None`. Otherwise
`SessionLimitError` and `PaginationCycleError` keep a checkpoint of where the pager stopped as `resume_state`, with the
item a limit refused still to come.

A resumed pager continues with the saved continuation, bindings, and request, whose arguments and body are built from
their wire values as their codecs build a caller's. A pager checkpointed in the middle of a page fetches that page again
and skips the items it delivered of it, which count as
delivered; when the server's data changed in between, it skips the same number of items of the page it gets now. A
literal binding sends the plan's value, never a saved one; a pager that skips items iterates items only, so
`iter_pages()` raises `ProtocolStateError`. Its pages continue with `next_page` as any other. Pages and items count on
from the checkpoint against the resumed call's limits, so a limit that stopped the pager stops it again unless it is
raised, while the session's timeout, deadline, and sends start afresh. Cycles are detected within a live pager only: a
resumed pager starts its history with the saved continuation, so a resumed cycle raises `PaginationCycleError` again
after one fetch.

`resume` checks the state before returning, without sending:

| Rejected state | Exception |
|---|---|
| Not a `ResumeState` | `ProtocolConfigurationError(field_path=("state",), condition="invalid_value")` |
| Another helper's, or one generated differently | `ResumeStateError(condition="fingerprint")` |
| A state, saved argument, or body that does not fit the helper: a value its codec refuses, a media type the operation's select method refuses, path arguments that make their segment a dot segment once encoded, a value for a parameter that is never saved, an offset or page number other than the one the saved pages reach from the first request's start, items to skip after the last page, or a saved value that cannot be encoded into the first or next request, such as one with CR, LF, or NUL in a header. A refusal of the resumed call's own options is raised as the call raises it | `ResumeStateError(condition="malformed")` |
| A cursor over the resumed call's `max_cursor_bytes`, a URL a server could not have given or at an origin the resuming client does not allow, or a server value that would make a path segment a dot segment once encoded | `ProtocolSizeError` or `ProtocolDataError`, as for a page |

A token carries no credential and is bound to no credential partition or auth: a resumed pager sends its requests with
the resuming client's own auth, only to that client's origins, and the server authorizes the saved cursor as it would
any caller's. The saved arguments are not encrypted, so store exported tokens as the call's own data.

### Generation checks

The operation must declare exactly one success response with one JSON media type and a schema, read natively, so an
operation that also declares a `204` or another success status is refused. The items pointer must name, through the
fields of the page's models, a JSON array whose items schema is `item_schema`. The cursor and each binding's selector
must read a declared property or header, and a querystring or body target's pointer must name a property its schema
declares, through `allOf` members and nullable `anyOf` or `oneOf` ones. The JSON types a cursor reads, other than null,
and those a binding reads or its literal has, null included, must be ones the target accepts, and a binding that can
give null must write a JSON body or a querystring of JSON content; a cursor that can be null needs a `null` end, and
each end value must be of a type the cursor reads. Literals that make a path segment whose every parameter they fill a
dot segment once encoded in their parameters' styles, such as `..` in the simple style, or the empty string in the
label style, are no path parameter values, reported at the segment's first literal binding, and a body target needs a
required body or one media type a call without `media_type` sends. A binding that writes the continuation's
target or another binding's, or a body or querystring member inside or around one, fails with `E_CONFIG_CONFLICT`. An
offset's or page number's target must accept integers, a page number advances by a literal step, `has_more` must read
booleans, and `total` must read integers from the body or a header, never the status. A next URL must read strings, or
null with a `null` end, its end values must be strings, and `repeat_request_body` needs an operation with a body that
may be sent again; a Link continuation's header must be one the response declares, and `rel` one relation type, a
registered name such as `next` or an absolute URI. A next-URL or Link helper's binding that writes a path, query, or
querystring parameter, which the URL replaces, or the body without `repeat_request_body`, fails with `E_CONFIG_VALUE`.
So does a cursor, position, or binding, a literal one included, that writes a cookie, the `Authorization`,
`Proxy-Authorization`, `Cookie`, or `Cookie2` header, or a header, query parameter, or querystring property at the
position of a declared security scheme: credentials are never written from what a server or a checkpoint gives. Every
cookie counts, declared as a scheme or not, because cookies commonly carry session state; this removes the cookie
cursors and cookie bindings earlier versions of the helpers wrote.
Helper names whose classes collide, such as `users.all_items` and `users_all.items`, fail with `E_NAME_COLLISION`.
Bindings that read the helper's input, request bodies other than JSON, and items or selector pointers that read
through a union or a map are not supported yet:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.pagination.diagnostics -->
<!-- fmt: off -->

```text
E_NAME_COLLISION target protocols.helpers['users_all.items'] /paths/~1users/get: The helper name 'users_all.items' gives the class name 'UsersAllItemsPagination', which 'users.all_items' already gives
E_CLIENT_UNSUPPORTED target protocols.helpers['bindings.input'].bindings[0].value.source /paths/~1users/get: The binding 0 of 'bindings.input' reads the helper's input, which is not supported yet
E_CONFIG_VALUE config protocols.helpers['bindings.reads'].bindings[2].target /paths/~1users/get: The binding 2 of 'bindings.reads' writes the cookie 'session', which carries credentials no helper writes
E_CONFIG_VALUE config protocols.helpers['bindings.reads'].bindings[0].value.selector /paths/~1users/get: The binding 0 pointer '/nothing' of 'bindings.reads' names no property of the GET /users response
E_CONFIG_VALUE config protocols.helpers['bindings.reads'].bindings[1].value.selector /paths/~1users/get: The binding 1 of 'bindings.reads' reads the header 'X-Missing', which GET /users does not declare
E_CLIENT_UNSUPPORTED target protocols.helpers['bindings.reads'].bindings[2].value.selector /paths/~1users/get: The binding 2 pointer '/grouped/admins' of 'bindings.reads' reads through a union or map, which is not supported yet
E_CONFIG_VALUE config protocols.helpers['bindings.types'].bindings[2].target /paths/~1users/get: The binding 2 of 'bindings.types' writes the cookie 'session', which carries credentials no helper writes
E_CONFIG_VALUE config protocols.helpers['bindings.types'].bindings[0].target /paths/~1users/get: The binding 0 of 'bindings.types' gives integer values, which the header parameter 'X-Cursor' of GET /users does not accept
E_CONFIG_VALUE config protocols.helpers['bindings.types'].bindings[1].target /paths/~1users/get: The binding 1 of 'bindings.types' gives string values, which the query parameter 'size' of GET /users does not accept
E_CONFIG_VALUE config protocols.helpers['bindings.types'].bindings[2].target /paths/~1users/get: The binding 2 of 'bindings.types' gives array values, which the cookie parameter 'session' of GET /users does not accept
E_CONFIG_VALUE config protocols.helpers['bindings.null'].bindings[0].target /paths/~1users/get: The binding 0 of 'bindings.null' can give null, which cannot be written to the header parameter 'X-Cursor' of GET /users
E_CONFIG_VALUE config protocols.helpers['bindings.null'].bindings[1].target /paths/~1users/get: The binding 1 of 'bindings.null' can give null, which cannot be written to the query parameter 'size' of GET /users
E_CONFIG_CONFLICT config protocols.helpers['bindings.conflicts'].bindings[0].target /paths/~1users/get: The binding 0 of 'bindings.conflicts' writes the same target as its cursor
E_CONFIG_CONFLICT config protocols.helpers['bindings.conflicts'].bindings[2].target /paths/~1users/get: The binding 2 of 'bindings.conflicts' writes the same target as its binding 1
E_CONFIG_CONFLICT config protocols.helpers['bindings.overlaps'].bindings[0].target /paths/~1bodies/post: The binding 0 of 'bindings.overlaps' writes a target overlapping that of its cursor
E_CONFIG_CONFLICT config protocols.helpers['bindings.overlaps'].bindings[2].target /paths/~1bodies/post: The binding 2 of 'bindings.overlaps' writes a target overlapping that of its cursor
E_CONFIG_VALUE config protocols.helpers['bindings.dots'].bindings[0].value.literal /paths/~1folders~1{folder}/get: The binding 0 of 'bindings.dots' gives '..', which makes the segment of the path parameter 'folder' of GET /folders/{folder} a dot segment
E_CONFIG_VALUE config protocols.helpers['bindings.label_empty'].bindings[0].value.literal /paths/~1styled~1{label}~1{reserved}~1{file}.json/get: The binding 0 of 'bindings.label_empty' gives '', which makes the segment of the path parameter 'label' of GET /styled/{label}/{reserved}/{file}.json a dot segment
E_CONFIG_VALUE config protocols.helpers['bindings.label_dot'].bindings[0].value.literal /paths/~1styled~1{label}~1{reserved}~1{file}.json/get: The binding 0 of 'bindings.label_dot' gives '.', which makes the segment of the path parameter 'label' of GET /styled/{label}/{reserved}/{file}.json a dot segment
E_CONFIG_VALUE config protocols.helpers['bindings.path_null'].bindings[0].target /paths/~1styled~1{label}~1{reserved}~1{file}.json/get: The binding 0 of 'bindings.path_null' can give null, which cannot be written to the path parameter 'reserved' of GET /styled/{label}/{reserved}/{file}.json
E_CONFIG_VALUE config protocols.helpers['bindings.joined_dots'].bindings[0].value.literal /paths/~1joined~1{first}{second}/get: The binding 0 of 'bindings.joined_dots' gives '.', which makes the segment of the path parameter 'first' of GET /joined/{first}{second} a dot segment
E_CONFIG_VALUE config protocols.helpers['write.optional_body'].continuation.write /paths/~1multi/post: The cursor of 'write.optional_body' writes the request body of POST /multi, which is optional and has no media type a call without one sends
E_CONFIG_VALUE config protocols.helpers['write.body_type'].continuation.write /paths/~1bodies/post: The cursor of 'write.body_type' reads string values, which the property '/count' of the request body of POST /bodies does not accept
E_CLIENT_UNSUPPORTED target protocols.helpers['body.form'] /paths/~1forms/post: The pagination helper 'body.form' sends a request body other than JSON, which is not supported yet
E_CONFIG_VALUE config protocols.helpers['responses.twice'].operation /paths/~1twice/get: GET /twice must declare exactly one JSON success response for the pagination helper 'responses.twice'
E_CONFIG_VALUE config protocols.helpers['responses.hidden'].continuation.write /paths/~1hidden/get: The cursor of 'responses.hidden' reads array values, which the query parameter 'cursor' of GET /hidden does not accept
E_CLIENT_UNSUPPORTED target protocols.helpers['items.union'].items /paths/~1users/get: The items pointer '/either/data' of 'items.union' reads through a union or map, which is not supported yet
E_CLIENT_UNSUPPORTED target protocols.helpers['items.map'].items /paths/~1users/get: The items pointer '/grouped/admins' of 'items.map' reads through a union or map, which is not supported yet
E_CONFIG_VALUE config protocols.helpers['items.absent'].items /paths/~1users/get: The items pointer '/data/id' of 'items.absent' names no property of the GET /users response
E_CONFIG_VALUE config protocols.helpers['items.scalar'].items /paths/~1users/get: The items pointer '/count' of 'items.scalar' selects no JSON array of the GET /users response
E_CONFIG_VALUE config protocols.helpers['items.unknown_schema'].item_schema /paths/~1users/get: The item_schema '/components/schemas/Nobody' of 'items.unknown_schema' does not exist in its document
E_CONFIG_VALUE config protocols.helpers['items.other_schema'].item_schema /paths/~1users/get: The item_schema '/components/schemas/Label' of 'items.other_schema' is not the item schema of the array its items pointer selects
E_CONFIG_VALUE config protocols.helpers['cursor.absent'].continuation.read /paths/~1users/get: The cursor pointer '/nothing' of 'cursor.absent' names no property of the GET /users response
E_CONFIG_VALUE config protocols.helpers['cursor.header'].continuation.read /paths/~1users/get: The cursor of 'cursor.header' reads the header 'X-Missing', which GET /users does not declare
E_CONFIG_VALUE config protocols.helpers['cursor.types'].continuation.write /paths/~1users/get: The cursor of 'cursor.types' reads array, boolean, integer, number, or object values, which the query parameter 'cursor' of GET /users does not accept
E_CONFIG_VALUE config protocols.helpers['cursor.null'].continuation.end /paths/~1users/get: The cursor of 'cursor.null' can read null, which no end condition covers
E_CONFIG_VALUE config protocols.helpers['cursor.null'].continuation.end[1].value /paths/~1users/get: The end value 5 of 'cursor.null' is integer, which its cursor never reads
E_CONFIG_VALUE config protocols.helpers['cursor.number'].continuation.end[1].value /paths/~1users/get: The end value 2.0 of 'cursor.number' is number, which its cursor never reads
E_CONFIG_VALUE config protocols.helpers['cursor.union'].continuation.write /paths/~1users/get: The cursor of 'cursor.union' reads integer values, which the query parameter 'cursor' of GET /users does not accept
E_CONFIG_VALUE config protocols.helpers['cursor.union'].continuation.end /paths/~1users/get: The cursor of 'cursor.union' can read null, which no end condition covers
E_CLIENT_UNSUPPORTED target protocols.helpers['cursor.map'].continuation.read /paths/~1users/get: The cursor pointer '/grouped/admins' of 'cursor.map' reads through a union or map, which is not supported yet
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.pagination.diagnostics -->

## Polling helpers

An enabled polling helper is generated at `client.protocols.<name>` on `Client` and `AsyncClient` alike. Its `start`
takes the `create` operation's parameters and its body as keywords, never field arguments, then `poll_options`,
`options`, and `session_options`, sends the create request, and returns a handle; the asyncio `start` is awaited:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.polling.helper -->
<!-- fmt: off -->

```python
    def start(
        self,
        *,
        body: _dcg_type_0,
        media_type: Literal['application/json'] | None = None,
        poll_options: PollOptions | None = None,
        options: RequestOptions | None = None,
        session_options: SessionOptions | None = None,
    ) -> LroHandle[GetReportResponse, GetJobResponse]:
        """Create the operation of POST /jobs and return the handle that polls it."""
        return start_operation(
            self._core,
            _plans.PLAN_0,
            (),
            body=body,
            media_type=media_type,
            poll_options=poll_options,
            options=options,
            session_options=session_options,
        )
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.polling.helper -->

```python
with Client() as client, client.protocols.jobs.run.start(body=job) as handle:
    snapshot = handle.status()
    report = handle.wait()
```

`LroHandle[T, P]` and `AsyncLroHandle[T, P]`, imported from `pkg.protocols`, hold one operation: `T` is the result
type and `P` the poll operation's response type. `status()` polls once and returns a `PollSnapshot[P]`; `wait()` polls
until the operation settles and returns its result. Both are coroutines on `AsyncLroHandle`. Each poll is its own call
of the `poll` operation, which writes each binding's value: what its selector reads from the create response for
`source: initial`, kept for the whole operation, or from the latest response, the create response and then each poll,
for `source: previous`, or a `literal`. A value is written as the server gave it, without its target's schema checks,
and a missing value, a header repeated for a single value, and read values that make a path segment `.` or `..` once
encoded, `%2E` in either case counting as `.`, raise `ProtocolDataError`. `progress` reports the polls sent, counting
none refused before sending, and the session's sends.

### States and results

`start` sends the create request once, resent only as shared retries allow. A status in `accepted_statuses` returns a
pending handle. A status in `immediate_result.statuses` returns a handle that already holds the result, read from the
create response at the immediate result's selector; its `wait` returns the result without sending, and its `status`
raises `ProtocolStateError` with `state='succeeded'`, since it has no poll. Any other success status raises
`ProtocolDataError` with a `StatusSelector` location, and an error status raises the operation's HTTP error.

A poll's `state` selector must read a value equal to one of the `pending`, `succeeded`, `failed`, or `cancelled`
values, JSON type included; a missing state raises `ProtocolDataError`, and any other value raises
`PollingStateError` with the condition `type` when no declared state has its JSON type and `value` otherwise. Success
is never inferred. A failed or cancelled operation makes `wait` raise `OperationFailedError` or
`OperationCancelledError`, whose `snapshot` is the last poll, and every later `wait` raises it again without sending;
`status` returns that poll. After a success, `wait` returns the result: the value an `inline` result's selector reads
from the final poll, the response of the result `operation`, fetched once with its bindings, whose `previous` source is
the final poll, or None for `kind: none`. Later `status` and `wait` calls send nothing. A missing or null result in the
final poll raises `ProtocolDataError` and leaves the handle pending, so a later `status` or `wait` polls again and
raises the same error while the result stays missing; a missing or null immediate result fails `start`.

An error that settles nothing, such as a transport error, an HTTP error, a deadline, a cancellation, a limit, or a
`ProtocolDataError` of a poll, leaves the handle as it was, so a later `status` or `wait` polls again without a new
create request, and a failed result fetch is retried alone. A spent limit stays spent for the handle's session, though:
once `max_polls` is reached, every later poll, and once `max_network_sends` is reached, every later poll or result
fetch, raises `SessionLimitError` again before sending. `close()` or `aclose()`, or leaving a `with` or
`async with` block, stops only local polling; the remote operation goes on, and every later step raises
`ProtocolStateError` with `state='closed'`. Calling `status`, `wait`, `close`, or `aclose` while another step runs
raises `ProtocolStateError` with `state='polling'`; a block that ends with an error while another step runs leaves the
handle open and lets its own error propagate. `checkpoint()` and `cancel_remote()` are not steps: they run while
another thread or task is in `status` or `wait`, such as one sleeping until its next poll.

### Waits and server delays

The first poll waits until `interval` seconds after the create response's receipt, and each later one until the
interval after the last poll's; with `interval.retry_after_header`, a valid delay in that header, in seconds or as an
HTTP date, makes the wait longer, never shorter. The delay counts from the final response of a call, after any retries
inside it, so a retry's own delay is never added to the poll interval. A poll or a result fetch that fails with a
response, such as a `503` after its retries, sets the next wait from that response the same way before its error is
raised, and the next poll or fetch waits for it; the first result fetch is sent at once. `status` waits the same way.
An interval longer than `PollOptions.max_wait`, or not shorter than the session's `total_timeout` or the time left
before its `deadline`, could never be waited out, so `start` raises `ProtocolConfigurationError` with the
`field_path` `("poll_options", "interval")` before sending the create request. A wait a server delay makes longer
than `max_wait`, or not shorter than what remains of the session's deadline or the options' deadline, raises
`PollWaitLimitError` with the kind `wait` or `deadline`, the `required_wait`, and the `limit` before anything is sent;
the handle stays as it was. A wait ends early for the options' `CancelToken` and the client's close, raising
`RequestCancelledError` or `ClientClosedError` with the `operation_id` of the operation it waits to call, the poll's
or the result fetch's, and the session's `parent_session_id`. Once the client is closed, every later `status` or
`wait` that needs a poll or a result fetch raises `ClientClosedError`. `PollWaitLimitError.resume_state` holds a
[checkpoint](#checkpoints-and-resume-of-operations) of the handle as it stood.

### Limits and sessions

`start` and its handle are one session. The create call, every poll, and the result fetch are calls of their own, with
their own retries, total timeout, and idempotency key, and the session bounds all of them: a call's deadline is the
earlier of its own and the session's, and each of its sends takes a slot of the session too. Each limit comes from the
call's options, then the helper's `ProtocolDefaults` in `ProtocolClientOptions.defaults`, then the default below:

| Limit | Default | None |
|---|---|---|
| `PollOptions.max_polls` | 1000 polls | Removes the limit |
| `PollOptions.interval` | The declared `interval.seconds`, 1 second by default | Not allowed |
| `PollOptions.max_wait` | 60 seconds | Removes the limit |
| `SessionOptions.total_timeout` | 600 seconds from `start` | Removes the limit |
| `SessionOptions.deadline` | None | No deadline |
| `SessionOptions.max_network_sends` | 2000 sends | Removes the limit |

A poll or a result fetch past a limit raises `SessionLimitError` with the kind `polls` or `network_sends` and the
progress so far, before sending, keeping a checkpoint of the handle as `resume_state`; a create request the session
has no slot for raises it too, with `resume_state=None`, since nothing was created. The options of `start` apply to
every call of the handle. They must not fix an idempotency key, from the client, a view, or the call, and
must not patch a header or a query parameter a binding writes; `start` raises `ProtocolConfigurationError` before
sending, as it does for options of another type.

### Checkpoints and resume of operations

`handle.checkpoint()` returns a `ResumeState` without sending, on `LroHandle` and `AsyncLroHandle` alike, also after
`close`. While another thread or task runs `status` or `wait`, it saves the handle as the last poll that settled left
it, never waiting for a step. A settled operation, one that failed, was cancelled, or holds its result, including
one that completed at once, has nothing left to continue: its `checkpoint()` raises `ProtocolStateError` with the
phase as `state`. The helper's `resume(state, *, poll_options=None, options=None, session_options=None)` is not
awaited, even on `AsyncClient`, and returns the handle type `start` returns, in a session of its own, without sending:
it never creates the operation again. A pending handle's `status` or `wait` sends its first poll at once; a handle
whose result fetch is due fetches it once in `wait`, and its `status` raises `ProtocolStateError`, since it holds no
poll. A checkpoint does not keep a server's delay,
so a caller resuming after `PollWaitLimitError` waits out its `required_wait` itself before polling.

```python
import time

from pkg.errors import PollWaitLimitError
from pkg.protocols import import_state

with Client() as client:
    helper = client.protocols.jobs.run
    handle = helper.start(body=job)
    saved = handle.checkpoint().export()
    report = helper.resume(import_state(saved)).wait()
    try:
        helper.start(body=job).wait()
    except PollWaitLimitError as error:
        if error.resume_state is not None:
            time.sleep(error.required_wait)
            later = helper.resume(error.resume_state)
```

| Saved | Never saved |
|---|---|
| Whether the operation is pending or its result fetch is due | The create request, its body, and its idempotency key, since resume never sends it |
| While pending, the values the next poll and a remote cancel write, read from the create response or the last pending poll, and those the create response gave the result fetch's `initial` bindings; while the fetch is due, the values it writes | Polls, results, their bodies, and model objects |
| The server's expiry an `expires_at` helper read | The session, its deadline, its send counters, the call's `options`, and anything its auth adds |

A resumed pending handle polls with the saved values, and one whose fetch is due fetches the result with them. Polls
count afresh against the resumed call's
`max_polls`, and the session's timeout, deadline, and sends start afresh. A literal binding sends the plan's value,
never a saved one.

`resume` checks the resumed call's options as `start` does, then the state, before returning:

| Rejected state | Exception |
|---|---|
| Not a `ResumeState` | `ProtocolConfigurationError(field_path=("state",), condition="invalid_value")` |
| Another helper's, or one generated differently | `ResumeStateError(condition="fingerprint")` |
| A state that does not fit the helper: an unknown or missing member, an unknown phase, a due fetch of a helper without one, values of another count than the bindings, an expiry `datetime.isoformat()` would spell differently, or a saved value that cannot be encoded into the next poll, result fetch, or remote cancel, such as one with CR, LF, or NUL in a header | `ResumeStateError(condition="malformed")` |
| An expiry that has passed | `ResumeStateError(condition="expired")` |
| A dot segment (`.` or `..`) a saved value would write to a path parameter | `ProtocolDataError`, as for a server's value |

A checkpoint saves the result fetch's `initial` values before the final poll gives its others: `resume` encodes each
one written to a parameter as the fetch encodes it, while one written into the fetch's querystring or body is checked
only when the fetch request is built, which refuses a value it cannot send before sending. As for pagers, a token holds
no credential and binds to no auth; store exported tokens as the call's own data.

### Remote cancellation and expiry

A helper that declares `remote_cancel` generates a handle class of its own, a subclass of `LroHandle` or
`AsyncLroHandle`, which `start` and `resume` return. Only it has `cancel_remote()`, a coroutine on the asyncio handle,
which returns a `CancelReceipt[C]`, imported from `pkg.protocols`, where `C` is the cancel operation's response type;
helpers without `remote_cancel` have no such method:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.polling.handle -->
<!-- fmt: off -->

```python
class JobsTrackedHandle(LroHandle[_dcg_type_1, GetJobResponse]):
    """A handle of the jobs.tracked polling helper, which also cancels the operation with DELETE /jobs/{jobId}."""

    __slots__ = ()

    def cancel_remote(self) -> CancelReceipt[CancelJobResponse]:
        """Ask the server to cancel the operation; the handle keeps its last poll until it polls again."""
        return self._cancel_remote(_plans.CANCEL_5)
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.polling.handle -->

`cancel_remote()` sends the cancel operation once, at once, as a call of the handle's session, writing its bindings'
values: what its selectors read from the create response for `source: initial`, or from the latest response, the
create response and then each pending poll, for `source: previous`. A missing value in one of those responses fails
it as a poll binding's does. The receipt is immutable, with read-only `data` and `response`. The request does not
settle the operation: the handle keeps its phase, its last poll, and the time of its next poll, and only the next
`status` or `wait` tells whether the operation was cancelled. It counts no poll; a session without a send slot raises
`SessionLimitError` with a checkpoint, and any other error leaves the handle as it was. It runs while another thread or
task is in `status` or `wait`, so a waiting operation can be cancelled from elsewhere: it reads the values to send when
it starts, and the `wait` goes on until a poll tells it the operation settled. A settled operation raises
`ProtocolStateError` with its phase as the `state`, and a closed handle with `state='closed'`, both without sending.
`close()` never sends the cancel request.

The concrete handle classes are not exported from `pkg.protocols`: use the type `start` and `resume` return, or
annotate a handle as `LroHandle[T, P]` or `AsyncLroHandle[T, P]`, which they subclass, without `cancel_remote`.

`expires_at` is a selector of the server's expiry of the operation, read once from the accepted create response: a
string giving an RFC 3339 date-time with an offset or an HTTP date, where a leap second is the second after the one
before it. A missing, null, non-string, or unparsable value fails `start` with `ProtocolDataError` after the create
response, as a missing binding value does; the remote operation was created all the same, and nothing cancels it. The expiry, in UTC,
becomes the expiry of every checkpoint of the handle, so `resume` refuses them afterwards with
`ResumeStateError(condition="expired")`; it does not stop a live handle from polling. Without `expires_at`, checkpoints
never expire, and no expiry is assumed. An immediate result has none.

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.polling.tracked -->
<!-- fmt: off -->

```yaml
schema_version: 1
helpers:
  jobs.tracked:
    kind: polling
    create: /paths/~1jobs/post
    accepted_statuses: [202]
    poll: /paths/~1jobs~1{jobId}/get
    bindings:
      - target: {in: path, name: jobId}
        value: {source: initial, selector: {from: body, pointer: /id}}
    state: {from: body, pointer: /status}
    pending: [queued, running]
    succeeded: [done]
    failed: [failed]
    cancelled: [cancelled]
    result:
      kind: inline
      selector: {from: body, pointer: /result}
      schema: {pointer: /components/schemas/Report}
    remote_cancel:
      operation: /paths/~1jobs~1{jobId}/delete
      bindings:
        - target: {in: path, name: jobId}
          value: {source: previous, selector: {from: body, pointer: /id}}
        - target: {in: header, name: X-Reason}
          value: {literal: requested}
    expires_at: {from: body, pointer: /expires}
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.polling.tracked -->

### Generation checks

Each accepted and immediate status must select a success response of the `create` operation, and the `poll`
operation, like a result `operation`, must declare exactly one success response with one JSON media type and a schema,
read natively. The state, each binding's selector, and an inline result must read what the responses declare: a body
pointer a property of the response's model, through every accepted create response for `source: initial` and also the
poll's for `source: previous`, or a declared header. Each declared state must have a JSON type the state reads. The
JSON types a binding reads or its literal has must be ones its target accepts, literals that make a path segment `.` or
`..` once encoded are no path parameter values, and every required parameter and required body of the poll and result
operations must be written by a binding, and a body a binding writes needs a default media type, since a poll or a
result fetch names none. Two bindings writing the same target, or a body or querystring member inside or around
another's, fail with `E_CONFIG_CONFLICT`. As for pagination, no binding of the poll or the result fetch writes
a credential position: a
cookie, the `Authorization`, `Proxy-Authorization`, `Cookie`, and `Cookie2` headers, or a header, query parameter, or
querystring property a security scheme of the package names. An inline or immediate result reads a body pointer whose
schema is the declared `schema`; an immediate result needs a result kind other than `none`, success responses of
`create` that are all one JSON model, and the type of the result. A `remote_cancel` operation must declare a success
response, and its bindings are checked as the poll's, against the same responses; `expires_at` must read strings from
every accepted create response. Bindings that read the helper's input, request bodies other than JSON written by a
binding, and pointers that read through a union or a map are not supported yet:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.polling.diagnostics -->
<!-- fmt: off -->

```text
E_CONFIG_VALUE config protocols.helpers['checks.later'].accepted_statuses[1] /paths/~1jobs/post: The accepted status 201 of 'checks.later' selects no success response of POST /jobs
E_CONFIG_VALUE config protocols.helpers['checks.later'].accepted_statuses[2] /paths/~1jobs/post: The accepted status 404 of 'checks.later' selects no success response of POST /jobs
E_CONFIG_VALUE config protocols.helpers['checks.later'].expires_at /paths/~1jobs/post: The expiry of 'checks.later' reads integer values, where only a string gives a date and time
E_CONFIG_VALUE config protocols.helpers['checks.later'].remote_cancel.operation /paths/~1archives/post: POST /archives must declare a success response for the remote cancel of 'checks.later'
E_CONFIG_VALUE config protocols.helpers['checks.poll'].poll /paths/~1exports/post: POST /exports must declare exactly one JSON success response for the polling helper 'checks.poll'
E_CONFIG_VALUE config protocols.helpers['checks.states'].pending[0] /paths/~1exports~1status/get: The state 'zero' of 'checks.states' is string, which its state never reads
E_CONFIG_VALUE config protocols.helpers['checks.states'].failed[0] /paths/~1exports~1status/get: The state True of 'checks.states' is boolean, which its state never reads
E_CONFIG_VALUE config protocols.helpers['checks.unread'].state /paths/~1exports~1status/get: The state of 'checks.unread' reads the header 'X-State', which GET /exports/status does not declare
E_CONFIG_VALUE config protocols.helpers['checks.bindings'].bindings[0].target /paths/~1jobs~1{jobId}/get: The binding 0 of 'checks.bindings' writes the cookie 'session', which carries credentials no helper writes
E_CONFIG_CONFLICT config protocols.helpers['checks.bindings'].bindings[2].target /paths/~1jobs~1{jobId}/get: The binding 2 of 'checks.bindings' writes the same target as its binding 1
E_CLIENT_UNSUPPORTED target protocols.helpers['checks.bindings'].bindings[3].value.source /paths/~1jobs~1{jobId}/get: The binding 3 of 'checks.bindings' reads the helper's input, which is not supported yet
E_CONFIG_VALUE config protocols.helpers['checks.bindings'].bindings[4].value.selector /paths/~1jobs/post: The binding 4 pointer '/nothing' of 'checks.bindings' names no property of the POST /jobs response
E_CONFIG_VALUE config protocols.helpers['checks.bindings'].bindings[5].target /paths/~1jobs~1{jobId}/get: The binding 5 of 'checks.bindings' gives integer values, which the header parameter 'X-Trace' of GET /jobs/{jobId} does not accept
E_CONFIG_VALUE config protocols.helpers['checks.dots'].bindings[0].value.literal /paths/~1jobs~1{jobId}/get: The binding 0 of 'checks.dots' gives '..', which makes the segment of the path parameter 'jobId' of GET /jobs/{jobId} a dot segment
E_CONFIG_VALUE config protocols.helpers['checks.bodyless'].bindings[0].value.selector /paths/~1exports/post: The binding 0 of 'checks.bodyless' reads the body of the 202 response of POST /exports, which is no JSON model
E_CONFIG_VALUE config protocols.helpers['checks.required'].bindings /paths/~1exports~1find/get: GET /exports/find requires the querystring parameter 'filter', which no binding of 'checks.required' writes
E_CONFIG_VALUE config protocols.helpers['checks.required'].result.bindings /paths/~1reports/post: POST /reports requires a request body, which no binding of 'checks.required' writes
E_CONFIG_VALUE config protocols.helpers['checks.unwritten'].bindings /paths/~1jobs~1{jobId}/get: GET /jobs/{jobId} requires the path parameter 'jobId', which no binding of 'checks.unwritten' writes
E_CLIENT_UNSUPPORTED target protocols.helpers['checks.unwritten'].result.bindings /paths/~1notes/post: The polling helper 'checks.unwritten' writes a request body of POST /notes other than JSON, which is not supported yet
E_CONFIG_CONFLICT config protocols.helpers['checks.overlaps'].result.bindings[1].target /paths/~1reports/post: The result binding 1 of 'checks.overlaps' writes a target overlapping that of its result binding 0
E_CONFIG_VALUE config protocols.helpers['checks.fetch'].result.operation /paths/~1exports/post: POST /exports must declare exactly one JSON success response for the result of 'checks.fetch'
E_CONFIG_VALUE config protocols.helpers['checks.header'].result.selector /paths/~1jobs~1{jobId}/get: The result of 'checks.header' reads a header, where only a body pointer reads a result
E_CLIENT_UNSUPPORTED target protocols.helpers['checks.union'].result.selector /paths/~1jobs~1{jobId}/get: The result pointer '/labels/rows' of 'checks.union' reads through a union or map, which is not supported yet
E_CONFIG_VALUE config protocols.helpers['checks.absent'].result.selector /paths/~1jobs~1{jobId}/get: The result pointer '/nothing' of 'checks.absent' names no property of the GET /jobs/{jobId} response
E_CONFIG_VALUE config protocols.helpers['checks.missing_schema'].result.schema /paths/~1jobs~1{jobId}/get: The result schema '/components/schemas/Missing' of 'checks.missing_schema' does not exist in its document
E_CONFIG_VALUE config protocols.helpers['checks.other_schema'].result.schema /paths/~1jobs~1{jobId}/get: The result schema '/components/schemas/Report' of 'checks.other_schema' is not the schema its pointer reads
E_CONFIG_VALUE config protocols.helpers['checks.none_immediate'].immediate_result /paths/~1jobs/post: The immediate result of 'checks.none_immediate' gives a result, which a helper without a result never has
E_CONFIG_VALUE config protocols.helpers['checks.immediate'].immediate_result.statuses[0] /paths/~1jobs/post: The immediate status 201 of 'checks.immediate' selects no success response of POST /jobs
E_CONFIG_VALUE config protocols.helpers['checks.immediate'].immediate_result.selector /paths/~1jobs/post: The immediate result of 'checks.immediate' reads models.ExportState, which is not its result type models.Report
E_CONFIG_VALUE config protocols.helpers['checks.immediate_models'].immediate_result.statuses[0] /paths/~1exports/post: The immediate status 200 of 'checks.immediate_models' selects no success response of POST /exports
E_CLIENT_UNSUPPORTED target protocols.helpers['checks.immediate_models'].immediate_result /paths/~1exports/post: The immediate result of 'checks.immediate_models' reads POST /exports, whose success responses are not all one JSON model, which is not supported yet
E_CONFIG_VALUE config protocols.helpers['checks.immediate_header'].immediate_result.selector /paths/~1jobs/post: The immediate result of 'checks.immediate_header' reads a header, where only a body pointer reads a result
E_CONFIG_VALUE config protocols.helpers['checks.progress'].immediate_result.selector /paths/~1jobs/post: The immediate result of 'checks.progress' reads models.Report, which is not its result type int
E_CONFIG_VALUE config protocols.helpers['checks.credentials'].bindings[1].target /paths/~1jobs~1{jobId}/get: The binding 1 of 'checks.credentials' writes the header 'x-api-key', which carries credentials no helper writes
E_CONFIG_VALUE config protocols.helpers['checks.credentials'].bindings[2].target /paths/~1jobs~1{jobId}/get: The binding 2 of 'checks.credentials' writes the query parameter 'api_key', which carries credentials no helper writes
E_CONFIG_VALUE config protocols.helpers['checks.credentials'].result.bindings[0].target /paths/~1exports~1status/get: The result binding 0 of 'checks.credentials' writes the query field 'api_key', which carries credentials no helper writes
E_CONFIG_VALUE config protocols.helpers['checks.no_success'].accepted_statuses[0] /paths/~1archives/post: The accepted status 202 of 'checks.no_success' selects no success response of POST /archives
E_CONFIG_VALUE config protocols.helpers['checks.no_success'].immediate_result.statuses[0] /paths/~1archives/post: The immediate status 200 of 'checks.no_success' selects no success response of POST /archives
E_CLIENT_UNSUPPORTED target protocols.helpers['checks.no_success'].immediate_result /paths/~1archives/post: The immediate result of 'checks.no_success' reads POST /archives, whose success responses are not all one JSON model, which is not supported yet
E_CONFIG_VALUE config protocols.helpers['checks.media'].result.bindings /paths/~1archives~1search/post: The polling helper 'checks.media' writes a request body of POST /archives/search, which has no default media type; set the operation's request_media_type
E_CONFIG_VALUE config protocols.helpers['checks.cancel'].expires_at /paths/~1jobs/post: The expiry of 'checks.cancel' reads the header 'Expires', which POST /jobs does not declare
E_CONFIG_VALUE config protocols.helpers['checks.cancel'].remote_cancel.bindings[0].target /paths/~1jobs~1{jobId}/delete: The cancel binding 0 of 'checks.cancel' gives integer values, which the header parameter 'X-Reason' of DELETE /jobs/{jobId} does not accept
E_CONFIG_VALUE config protocols.helpers['checks.cancel'].remote_cancel.bindings /paths/~1jobs~1{jobId}/delete: DELETE /jobs/{jobId} requires the path parameter 'jobId', which no binding of 'checks.cancel' writes
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.polling.diagnostics -->

## Upload helpers

An enabled `resumable_upload` helper of the `offset` profile is generated at `client.protocols.<name>` on `Client`
and `AsyncClient` alike. Its `start` takes the upload source, then the `create` operation's parameters and its body as
keywords, never field arguments, without the parameter the helper writes the content's size to, then
`upload_options`, `options`, and `session_options`; `resume` takes the source and a checkpoint. Both are coroutines on
`AsyncClient`:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.uploads.helper -->
<!-- fmt: off -->

```python
    def start(
        self,
        source: UploadSource,
        *,
        tus_resumable: _dcg_type_0,
        x_name: _dcg_type_1 | Unset = UNSET,
        upload_options: UploadOptions | None = None,
        options: RequestOptions | None = None,
        session_options: SessionOptions | None = None,
    ) -> UploadHandle[None]:
        """Measure the source, create the upload of POST /files, and return its handle."""
        return start_upload(
            self._core,
            _plans.PLAN_0,
            source,
            (tus_resumable, x_name),
            upload_options=upload_options,
            options=options,
            session_options=session_options,
        )

    def resume(
        self,
        source: UploadSource,
        state: ResumeState,
        *,
        upload_options: UploadOptions | None = None,
        options: RequestOptions | None = None,
        session_options: SessionOptions | None = None,
    ) -> UploadHandle[None]:
        """Check a checkpoint and the source size, then continue from the server offset."""
        return resume_upload(
            self._core,
            _plans.PLAN_0,
            source,
            state,
            upload_options=upload_options,
            options=options,
            session_options=session_options,
        )
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.uploads.helper -->

```python
with Client() as client, open("video.mp4", "rb") as video:
    upload = client.protocols.files.upload
    handle = upload.start(video, tus_resumable=version)
    progress = handle.advance()
    state = handle.checkpoint()
    handle.run()
```

`UploadHandle[T]` and `AsyncUploadHandle[T]`, imported from `pkg.protocols`, hold one upload: `T` is `None` for an
upload completed by length and the completion operation's response type otherwise. `advance()` appends one chunk, or
completes the upload once the server holds every byte, and returns an `UploadProgress`; `run()` appends every
remaining chunk, completes the upload, and returns its result. Both are coroutines on `AsyncUploadHandle`. Once
complete, `advance` returns the progress and `run` the kept result without sending. `checkpoint()` returns a
`ResumeState` at any time before the upload completes, even while another thread or task steps the handle, and sends
nothing; a complete upload has nothing to resume, so its `checkpoint()` raises `ProtocolStateError` with
`state='complete'`.

### Sources

A source is `bytes`, a `bytearray`, a `memoryview`, or a seekable binary file, such as one `open(path, "rb")` returns
or an `io.BytesIO`; `UploadSource` names that union. `Client` and `AsyncClient` take the same sources. A file's content
runs from its position when `start` or `resume` is called to its end, and the call measures its size by seeking to
the end, without reading it. A file that cannot seek, an iterable, an iterator, or an asyncio stream raises
`NonResumableSourceError` with its `source_kind` before anything is sent; send it as an ordinary upload. A file whose
`read`, `seek`, or `tell` gives anything but bytes and integers, such as a text file or an asyncio file, raises
`ProtocolConfigurationError` with `condition='wrong_capability'`. Sources are borrowed and never closed by the client,
and nothing is ever spooled to disk.

Each append seeks to the confirmed offset and reads the rest of its chunk into one buffer, synchronously on both
clients; the last chunk is read with one byte more. Content shorter or longer than the size measured at `start` raises
`UploadSourceChangedError` with both sizes, and the handle sends nothing more: every later step raises
`ProtocolStateError` with `state='source_changed'`, though `checkpoint()` still works. The content is not hashed, so a
change that keeps its size is not found: keep it unchanged while it is uploaded. The body is sent from that buffer in
64 KiB slices, so an upload holds one chunk at a time besides fixed read buffers.

### Chunks, offsets, and recovery

A chunk is `UploadOptions.chunk_bytes`, 8 MiB by default, or the helper's smaller `max_chunk_bytes`. An upload of more
chunks than `UploadOptions.max_parts`, 10000 by default, raises `ProtocolConfigurationError` before anything is sent.
The offset profile sends one chunk at a time.

`start` sends the create request once, resent only as shared retries allow, with the content's size in the declared
`size` parameter, and reads every binding's `initial` value and the server's expiry from its response. The upload
starts at offset 0. Each append writes the confirmed offset into `offset`, the length of the bytes it sends into
`length` when declared, and their checksum into `checksum.target` when declared, and sends the rest of the chunk as the
body; a success response confirms the chunk. A checksum is the `md5`, `sha1`, `sha256`, or `sha512` digest of those
bytes, computed from the chunk's buffer as each append is built, in `base64` (the default) or `hex`, after the
algorithm's name and a space with `algorithm_prefix: true`, as the tus checksum extension spells it. An append is never sent
again by shared retries once it may have reached the server, even for an operation that is otherwise safe to retry:

| Append outcome | What follows |
|---|---|
| A success response | The chunk is confirmed |
| A transport error after it may have been sent | Up to `UploadOptions.max_uncertain_probes` probes, 3 by default: an unchanged offset sends the range again as a new call, the chunk's end confirms it, and an offset inside it confirms its bytes before it only with `partial_commit: allowed`, the rest being sent next |
| Probes that never answer | `UploadDeliveryUnknownError(phase='append')` with the delivery state, the progress, and `resume_state` |
| Any other failure, a cancellation included | Raised; the next step probes the server's offset before it appends |

A probe's offset is a JSON integer, or a header of ASCII digits; a missing one raises `ProtocolDataError` with the
condition `missing`, a header repeated with `malformed`, and anything else with `type`, `null`, or `value`. An offset
below the confirmed one, past the content, past the chunk sent, or inside a chunk with `partial_commit: forbidden`
raises `UploadOffsetError` with the confirmed, expected, and remote offsets, the size, and `resume_state`. A handle
`start` returned also refuses an offset past every byte it has sent; a handle `resume` returned cannot know what an
earlier session sent, so it accepts any offset up to the size.

An upload completed by `length` completes when the server holds every byte. A completion `operation` is sent once with
its bindings; its response is the result. A completion whose outcome is unknown is never sent again: it raises
`UploadDeliveryUnknownError(phase='complete')`, and so does every later step and every resume of its checkpoint,
without sending. That includes a completion answered with 502 or 504, which a gateway gives even when the server behind
it may have applied the completion. A completion answered with any other error status, such as a 503 with
`Retry-After`, did not apply, so the next step may send it again.

`close()` or `aclose()`, or leaving a `with` or `async with` block, stops only local uploading; the remote upload
stays, and every later step raises `ProtocolStateError` with `state='closed'`. Calling `advance`, `run`, `close`, or
`aclose` while another step runs raises `ProtocolStateError` with `state='uploading'`.

### Checkpoints and resume

`checkpoint()` saves the content's size, the chunk size, the confirmed offset, the values the probe, the append, and
the completion write, a completion of unknown outcome, and the server's expiry; it saves no content, digest, or
result, credential, or security binding: a resumed upload sends with the resuming client's own auth, as pagination
checkpoints do.

`resume(source, state)` starts a new session and never creates the upload again. It checks the call's options, then the
checkpoint: not a `ResumeState` raises `ProtocolConfigurationError`; another helper's raises
`ResumeStateError(condition='fingerprint')`, one whose state does not fit the helper
`ResumeStateError(condition='malformed')`, and one past the server's expiry `UploadExpiredError`. A checkpoint keeps its chunk size, which must not exceed the call's
`UploadOptions.chunk_bytes`, since an append holds one chunk in memory, and its chunk count must not exceed `max_parts`;
either raises `ProtocolConfigurationError` with the option's `field_path`. Then the saved values each call writes: a
value that makes a path segment `.` or `..` raises `ProtocolDataError`, as if a server gave it, and one that cannot be
sent `ResumeStateError(condition='malformed')`. Then the source: a one-shot input raises `NonResumableSourceError`, and
a size other than the checkpoint's raises `UploadSourceChangedError`; the source is not read. A completion of unknown
outcome raises `UploadDeliveryUnknownError` again without sending. Otherwise it probes the server's offset once, which
must not be below the saved one, and returns the handle.

A server's expiry is read once from the create response at `create.expires_at`, an RFC 3339 date-time with an offset or
an HTTP date; a missing, null, or unparsable value raises `ProtocolDataError` from `start`. It bounds only checkpoints
once it has passed: `resume` raises `UploadExpiredError`, a subclass of `ResumeStateError` with the condition
`expired`, with the expiry; uploading goes on until the server refuses.

### Limits and sessions

`start` and its handle are one session, and so are `resume` and its handle. The create call, every probe, every append,
and the completion are calls of their own, with their own retries, total timeout, and idempotency key, and the session
bounds all of them. Each limit comes from the call's options, then the helper's `ProtocolDefaults` in
`ProtocolClientOptions.defaults`, then the default below:

| Limit | Default | None |
|---|---|---|
| `UploadOptions.chunk_bytes` | 8 MiB, at most the helper's `max_chunk_bytes` | Not allowed |
| `UploadOptions.max_parts` | 10000 chunks | Removes the limit |
| `UploadOptions.max_uncertain_probes` | 3 probes; 0 probes none | Not allowed |
| `SessionOptions.total_timeout` | No limit | Removes the limit |
| `SessionOptions.deadline` | None | No deadline |
| `SessionOptions.max_network_sends` | 10000 sends | Removes the limit |

By default, an upload session has no total lifetime limit. If a finite `total_timeout` is configured, it runs from
`start` or `resume` for the whole life of the handle. A long upload stepped slowly may then need a larger timeout,
`SessionOptions(total_timeout=None)`, or a `resume` from a checkpoint, which starts a new session. A call past a limit
raises `SessionLimitError` with the kind `network_sends`, the progress so far, and `resume_state`, before sending; a
create request the session has no slot for raises it without `resume_state`, since nothing was created. The options
must not fix an idempotency key, from the client, a view, or the call, and must not patch a header or a query parameter
the helper writes, the size included; `start` and `resume` raise `ProtocolConfigurationError` before reading or
sending, as they do for options of another type.

### Upload generation checks

The `create` operation must declare a success response, and each selector must read what every success response
declares: a server expiry reads strings and a remote offset integers, from a body pointer of the response's model or
a declared header, never the status. The `size`, `offset`, and `length` targets are path, query, or header parameters
that accept integers, never a credential position, and no two writes of the append share a target. The `append`
operation takes exactly one binary request media and declares a success response; no binding writes its body. The
`probe` declares a success response. Bindings read the create response with `source: initial`, or give a literal, and
fit their targets as polling bindings do; every required parameter of the probe, the append, and the completion must
be written. A completion operation declares exactly one JSON success response whose schema is its `result_schema`, and
a body its bindings write is JSON with a default media type. The `parts` profile, `abort`, `create.session_url`, and
bindings that read the previous response or the helper's input are not supported yet:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.uploads.diagnostics -->
<!-- fmt: off -->

```text
E_CLIENT_UNSUPPORTED target protocols.helpers['checks.later'].abort /paths/~1files/post: The resumable_upload helper 'checks.later' declares remote abort, which is not supported yet
E_CLIENT_UNSUPPORTED target protocols.helpers['checks.later'].create.session_url /paths/~1files/post: The resumable_upload helper 'checks.later' declares a session URL, which is not supported yet
E_CONFIG_VALUE config protocols.helpers['checks.created'].create.operation /paths/~1drafts/post: POST /drafts must declare a success response for the upload helper 'checks.created'
E_CONFIG_VALUE config protocols.helpers['checks.size'].create.size /paths/~1files/post: The size of 'checks.size' writes the cookie 'session', which carries credentials no helper writes
E_CONFIG_VALUE config protocols.helpers['checks.size_body'].create.size /paths/~1files~1{fileId}~1status/post: The size of 'checks.size_body' writes 'body', where only a path, query, or header parameter takes it
E_CONFIG_VALUE config protocols.helpers['checks.size_body'].probe.bindings[0].value.selector /paths/~1files~1{fileId}~1status/post: The probe binding 0 pointer '/id' of 'checks.size_body' names no property of the POST /files/{fileId}/status response
E_CONFIG_VALUE config protocols.helpers['checks.size_body'].append.bindings[0].value.selector /paths/~1files~1{fileId}~1status/post: The append binding 0 pointer '/id' of 'checks.size_body' names no property of the POST /files/{fileId}/status response
E_CONFIG_VALUE config protocols.helpers['checks.size_type'].create.size /paths/~1files/post: The size of 'checks.size_type' gives integer values, which the header parameter 'X-Name' of POST /files does not accept
E_CONFIG_VALUE config protocols.helpers['checks.expiry_status'].create.expires_at /paths/~1files/post: The server expiry of 'checks.expiry_status' reads the status, which gives no server expiry
E_CONFIG_VALUE config protocols.helpers['checks.expiry_type'].create.expires_at /paths/~1files/post: The server expiry of 'checks.expiry_type' reads integer values, where only string values fit
E_CONFIG_VALUE config protocols.helpers['checks.expiry_header'].create.expires_at /paths/~1files/post: The server expiry of 'checks.expiry_header' reads the header 'X-Expires', which POST /files does not declare
E_CLIENT_UNSUPPORTED target protocols.helpers['checks.probe_sources'].probe.bindings[0].value.source /paths/~1files~1{fileId}/head: The probe binding 0 of 'checks.probe_sources' reads the previous response, which is not supported yet
E_CONFIG_VALUE config protocols.helpers['checks.probe_required'].probe.bindings /paths/~1files~1{fileId}/head: HEAD /files/{fileId} requires the path parameter 'fileId', which no binding of 'checks.probe_required' writes
E_CONFIG_VALUE config protocols.helpers['checks.probe_body'].probe.bindings /paths/~1files~1{fileId}~1status/post: POST /files/{fileId}/status requires a request body, which no binding of 'checks.probe_body' writes
E_CLIENT_UNSUPPORTED target protocols.helpers['checks.probe_form'].probe.bindings /paths/~1files~1{fileId}~1form/post: The upload helper 'checks.probe_form' writes a request body of POST /files/{fileId}/form other than JSON, which is not supported yet
E_CONFIG_VALUE config protocols.helpers['checks.probe_media'].probe.bindings /paths/~1files~1{fileId}~1search/post: The upload helper 'checks.probe_media' writes a request body of POST /files/{fileId}/search, which has no default media type; set the operation's request_media_type
E_CONFIG_VALUE config protocols.helpers['checks.probe_none'].probe.operation /paths/~1files~1{fileId}~1gone/delete: DELETE /files/{fileId}/gone must declare a success response for the probe of 'checks.probe_none'
E_CONFIG_VALUE config protocols.helpers['checks.offset_type'].probe.remote_offset /paths/~1files~1{fileId}~1status/get: The remote offset of 'checks.offset_type' reads string values, where only integer values fit
E_CONFIG_VALUE config protocols.helpers['checks.offset_status'].probe.remote_offset /paths/~1files~1{fileId}/head: The remote offset of 'checks.offset_status' reads the status, which gives no remote offset
E_CONFIG_VALUE config protocols.helpers['checks.append_media'].append.operation /paths/~1files~1{fileId}/put: PUT /files/{fileId} must take exactly one binary request media for the chunks of 'checks.append_media'
E_CONFIG_VALUE config protocols.helpers['checks.append_none'].append.operation /paths/~1files~1{fileId}~1gone/delete: DELETE /files/{fileId}/gone must take exactly one binary request media for the chunks of 'checks.append_none'
E_CONFIG_VALUE config protocols.helpers['checks.append_none'].append.operation /paths/~1files~1{fileId}~1gone/delete: DELETE /files/{fileId}/gone must declare a success response for the appends of 'checks.append_none'
E_CONFIG_CONFLICT config protocols.helpers['checks.append_none'].append.offset /paths/~1files~1{fileId}~1gone/delete: The chunk offset of 'checks.append_none' writes the same target as its append binding 0
E_CONFIG_CONFLICT config protocols.helpers['checks.append_overlap'].append.offset /paths/~1files~1{fileId}/patch: The chunk offset of 'checks.append_overlap' writes the same target as its append binding 1
E_CONFIG_CONFLICT config protocols.helpers['checks.append_length'].append.length /paths/~1files~1{fileId}/patch: The chunk length of 'checks.append_length' writes the same target as its chunk offset
E_CONFIG_VALUE config protocols.helpers['checks.append_places'].append.offset /paths/~1files~1{fileId}/patch: The chunk offset of 'checks.append_places' writes the cookie 'session', which carries credentials no helper writes
E_CONFIG_VALUE config protocols.helpers['checks.append_places'].append.length /paths/~1files~1{fileId}/patch: The chunk length of 'checks.append_places' writes 'body', where only a path, query, or header parameter takes it
E_CONFIG_VALUE config protocols.helpers['checks.append_places'].append.bindings /paths/~1files~1{fileId}/patch: PATCH /files/{fileId} requires the header parameter 'Upload-Offset', which no binding of 'checks.append_places' writes
E_CONFIG_VALUE config protocols.helpers['checks.append_types'].append.offset /paths/~1files~1{fileId}/patch: The chunk offset of 'checks.append_types' gives integer values, which the header parameter 'X-Note' of PATCH /files/{fileId} does not accept
E_CONFIG_CONFLICT config protocols.helpers['checks.checksum_overlap'].append.checksum.target /paths/~1files~1{fileId}/patch: The chunk checksum of 'checks.checksum_overlap' writes the same target as its chunk offset
E_CONFIG_VALUE config protocols.helpers['checks.checksum_place'].append.checksum.target /paths/~1files~1{fileId}/patch: The chunk checksum of 'checks.checksum_place' writes the cookie 'session', which carries credentials no helper writes
E_CONFIG_VALUE config protocols.helpers['checks.checksum_type'].append.checksum.target /paths/~1files~1{fileId}/patch: The chunk checksum of 'checks.checksum_type' gives string values, which the header parameter 'X-Count' of PATCH /files/{fileId} does not accept
E_CONFIG_VALUE config protocols.helpers['checks.append_body'].append.bindings[1].target /paths/~1files~1{fileId}/patch: The append binding 1 of 'checks.append_body' writes the request body, which is the chunk
E_CONFIG_VALUE config protocols.helpers['checks.completion_media'].completion.operation /paths/~1files~1{fileId}~1finish/post: POST /files/{fileId}/finish must declare exactly one JSON success response for the completion of 'checks.completion_media'
E_CONFIG_VALUE config protocols.helpers['checks.completion_missing'].completion.result_schema /paths/~1files~1{fileId}~1complete/post: The result schema '/components/schemas/Missing' of 'checks.completion_missing' does not exist in its document
E_CONFIG_VALUE config protocols.helpers['checks.completion_other'].completion.result_schema /paths/~1files~1{fileId}~1complete/post: The result schema '/components/schemas/Note' of 'checks.completion_other' is not the schema of the POST /files/{fileId}/complete response
E_CONFIG_VALUE config protocols.helpers['checks.completion_body'].completion.bindings /paths/~1files~1{fileId}~1complete/post: POST /files/{fileId}/complete requires a request body, which no binding of 'checks.completion_body' writes
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.uploads.diagnostics -->

## SSE stream helpers

An enabled `sse` helper is generated at `client.protocols.<name>` on `Client` and `AsyncClient` alike, with one method,
`open`. It takes the operation's parameters and its body as keywords, never field arguments, then `stream_options`,
`options`, and `session_options`, sends the operation in a session of its own, and returns an `EventStream[T]`, or with
one `await` an `AsyncEventStream[T]`, once the response is a declared success of the helper's media type:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.streams.helper -->
<!-- fmt: off -->

```python
    def open(
        self,
        *,
        topic: _dcg_type_0 | Unset = UNSET,
        last_event_id: _dcg_type_1 | Unset = UNSET,
        stream_options: StreamOptions | None = None,
        options: RequestOptions | None = None,
        session_options: SessionOptions | None = None,
    ) -> EventStream[_dcg_type_2]:
        """Open the event stream of GET /events, returning once its response is a declared success."""
        return open_events(
            self._core,
            _plans.STREAM_0,
            (topic, last_event_id),
            stream_options=stream_options,
            options=options,
            session_options=session_options,
        )
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.streams.helper -->

```python
with Client() as client, client.protocols.events.typed.open() as stream:
    for event in stream:
        print(event.sequence, event.event_type, event.data)
```

`T` is the type of the helper's event schema, or the union of its mapped schemas' types, with `UnknownEvent` when
`unknown: raw` keeps unmapped events. Iterating a stream yields `StreamEvent[T]` records: `data`, the event's SSE
`event_type` (`message` when the event names none), `event_id`, the last ID the stream set or None when it set none or
an empty one, `retry_ms`, the last valid `retry` in milliseconds, `sequence`, numbering the dispatched events from 1,
and `raw_data`, the event's data text. `data()` iterates over the data of the same events and advances the same stream;
the asyncio `data()` is not awaited. Representations name no data, event ID, or raw data. `StreamEvent`,
`UnknownEvent`, `EventStream`, and `AsyncEventStream` are imported from `pkg.protocols`, in every package.

The response's status and `Content-Type` are checked before `open` returns: an error status raises the operation's
typed HTTP error, an undeclared status `UnexpectedStatusError`, and a body of another media type, or none,
`UnexpectedMediaTypeError`. Its acquisition retries like any call. A stream then reads only the bytes its next event
needs and owns the response until it ends, fails, or closes: close it with `with`, `async with`, `close()`, or
`aclose()`, since leaving a loop early releases nothing. One consumer reads a stream at a time: a step, or a close,
while another step runs raises `ProtocolStateError`, and so does every step after a failure or `close()`; after its end
every step stops. Closing the client waits up to its `cleanup_timeout` for open streams, then closes them and raises
`CleanupError` naming them as pending leases; their next step raises `ClientClosedError`.

### Framing and events

The body is read as the WHATWG event-stream interpretation reads it: a leading byte order mark is skipped, CR, LF, and
CRLF end lines, including a CRLF split between reads, lines starting with `:` are comments, `data` lines join with LF,
`event` names the type, an `id` containing NUL is ignored, `retry` of ASCII digits sets the reconnection time (more than
18 digits are ignored), and a blank line dispatches an event that has data. Invalid UTF-8 decodes to replacement
characters. Each event's data is JSON converted to its schema's type through its converter; data that is not JSON, or
that the type refuses, raises `StreamDecodeError`
with at most the event limit or 64 KiB of the data as `raw_prefix`.

An event schema mapping is chosen by the event's SSE type with `discriminator: {from: event_type}`, or by the string a
body pointer reads from the JSON data with `{from: body, pointer}`; a missing, null, or non-string body discriminator,
and an unmapped one without `unknown: raw`, raise `StreamDecodeError`. An `error_events` key raises
`StreamRemoteError` with the error schema's decoded `data` instead of yielding the event.

The stream ends as its `completion` declares: at the end of the body for `eof`, or at the event whose raw data equals a
`sentinel` value or whose SSE type is the `event_type` value; neither terminal event is decoded or yielded, and the
response is released. A body that ends before a sentinel or terminal type raises `StreamInterruptedError` with the
condition `eof`, and one that cuts a line or an event, under any completion, raises `IncompleteFrameError` with the
bytes of the cut frame. A connection that breaks while the body is read raises `StreamInterruptedError` with the
condition `transport` and the transport failure as its `cause`. `sequence` is the last event delivered.

### Stream limits and sessions

`open` is one session holding one logical call. The call's total timeout bounds only acquiring the response; the
stream then ends no later than the earlier of the call's `stream_total_timeout` from the handoff and the session's
deadline, raising `DeadlineExceededError` with the phase `stream` at the next step, even for an event whose bytes it
already read, and releasing the response. Its idle timeout counts only while a step waits for bytes, comments
included, so a pause between steps never counts, and raises `PhaseTimeoutError`. Each limit comes from the call's
options, then the helper's `ProtocolDefaults` in `ProtocolClientOptions.defaults`, then the default below:

| Limit | Default | None |
|---|---|---|
| `StreamOptions.idle_timeout` | The call's merged `stream_idle_timeout`, 60 seconds by default | No idle limit |
| `StreamOptions.max_line_bytes` | 256 KiB per line | Not allowed |
| `StreamOptions.max_event_bytes` | 1 MiB of data per event | Not allowed |
| `SessionOptions.total_timeout` | None | No session deadline |
| `SessionOptions.deadline` | None | No deadline |
| `SessionOptions.max_network_sends` | 16 sends | Removes the limit |
| `StreamOptions.reconnect` | False | Not allowed |
| `StreamOptions.max_reconnects` | 5 reconnections, counted across resumes; 0 allows none | Removes the limit |
| `StreamOptions.max_reconnect_wait` | 60 seconds | No wait limit |

A line or event over its limit raises `ProtocolSizeError` with the kind `line` or `event` before it is kept, and a
session without a send slot raises `SessionLimitError` without sending. Only a helper that declares `resume` reconnects:
for any other, `StreamOptions(reconnect=True)` raises `ProtocolConfigurationError` with the condition
`missing_metadata`. Options of another type raise `ProtocolConfigurationError`.

### Checkpoints, resume, and reconnection

A helper whose metadata enables `resume` names the cursor of its events, `cursor: event_id` for the SSE event ID or a
`{from: body, pointer}` selector for a value in each event's JSON data, the request target its reopen writes it to, the
`reopen_operation`, and `delivery: at_least_once`. A body cursor also says what a missing value does, `missing:
inherit` keeping the cursor before or `error`, and what null does, `null: clear` or `error`. `bindings` write values
into the reopen request, read from the open response for `initial` or the latest open or reopen response for
`previous`, and `expires_at` reads the server's expiry from a header of the open response:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.streams.resume-yaml -->
<!-- fmt: off -->

```yaml
schema_version: 1
helpers:
  events.live:
    kind: sse
    operation: /paths/~1events/get
    media: text/event-stream
    event_schema: {pointer: /components/schemas/Message}
    completion: {kind: eof}
    resume:
      enabled: true
      cursor: event_id
      write: {in: header, name: Last-Event-ID}
      reopen_operation: /paths/~1events/get
      delivery: at_least_once
  events.tracked:
    kind: sse
    operation: /paths/~1events/get
    media: text/event-stream
    event_schema:
      discriminator: {from: event_type}
      mapping:
        created: {pointer: /components/schemas/Created}
    unknown: raw
    error_events:
      error: {pointer: /components/schemas/StreamError}
    completion: {kind: event_type, value: done}
    resume:
      enabled: true
      cursor: event_id
      write: {in: header, name: Last-Event-ID}
      reopen_operation: /paths/~1streams~1{streamId}/get
      delivery: at_least_once
      bindings:
        - {target: {in: path, name: streamId}, value: {source: initial, selector: {from: header, name: X-Stream-Id}}}
        - {target: {in: query, name: token}, value: {source: previous, selector: {from: header, name: X-Resume-Token}}}
        - {target: {in: query, name: mode}, value: {literal: resume}}
      reconnect_on: [transport_interruption, incomplete_eof]
      expires_at: {from: header, name: X-Stream-Expires}
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.streams.resume-yaml -->

The helper then also has `resume`, which takes a `ResumeState` and the options `open` takes; the asyncio one is awaited
once and returns the stream:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.streams.resume -->
<!-- fmt: off -->

```python
    def resume(
        self,
        state: ResumeState,
        *,
        stream_options: StreamOptions | None = None,
        options: RequestOptions | None = None,
        session_options: SessionOptions | None = None,
    ) -> EventStream[_dcg_type_3]:
        """Reopen the event stream after a checkpoint's cursor, returning once its response is a declared success."""
        return resume_events(
            self._core,
            _plans.STREAM_0,
            state,
            stream_options=stream_options,
            options=options,
            session_options=session_options,
        )
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.streams.resume -->

```python
with Client() as client, client.protocols.events.live.open() as stream:
    for event in stream:
        handle(event)
        save(stream.checkpoint().export())
with Client() as client, client.protocols.events.live.resume(import_state(load())) as stream:
    for event in stream:
        handle(event)
```

The cursor is that of the last event the stream returned, never of a terminal, error, refused, or cut event: an SSE
event without an `id` keeps the ID before, an empty `id` clears it, and a body cursor follows its `missing` and `null`
settings, raising `StreamDecodeError` with the condition `missing` or `null` before the event under `error`. An
unknown event kept raw gives its body cursor from its data when the data is JSON. A cursor is never limited apart from
its event.

`checkpoint()` sends nothing and works on an open, ended, failed, or closed stream once a cursor was delivered; before
that, and while another step runs, it raises `ProtocolStateError`, and on a stream of a helper without resume metadata
`ProtocolConfigurationError` with the condition `missing_metadata`. It saves the cursor, the bindings' values, the
server's expiry, and, when the reopen operation is the helper's own, the wire values of the caller's first request,
never events, counts, `retry` times, responses, the session, the call's options, or what its auth adds; the caller's value of an optional parameter the reopen writes, such as its own `Last-Event-ID`, is left
out. A call that gives a cookie, a credential header, or a security scheme's query parameter cannot be checkpointed and
raises `ProtocolConfigurationError` with the condition `wrong_capability`, and so does a stream whose cursor or binding
value the reopen sends as such a query field, including a property of an exploded form or deepObject query parameter. A cursor
the reopen request cannot encode, such as an event ID ending in a space or an object written to a query parameter,
raises `ProtocolDataError` with the condition `value` and the cursor's selector, or for an event ID the target it is
written to, as `location`, from `checkpoint()` and from a reconnection, which keeps no `resume_state` and has the
interruption as its context. Like pagination and polling tokens, it holds no credential and binds to no auth.

`resume` creates a session of its own and sends the reopen at once: the helper's own operation repeats the caller's
first request, another one sends only what is written, each binding's value first and then the cursor, whose
parameter is omitted once the cursor is cleared, so a cleared SSE cursor sends no `Last-Event-ID`. The request then
returns once its response is a declared success, as `open` does; sequences, reconnections, the session's deadline and
sends start afresh, and the reopen counts as no reconnection. Before sending it refuses a value that is not a
`ResumeState` with `ProtocolConfigurationError`, and with `ResumeStateError` another helper's state, an expired one,
and one that does not fit the helper or whose request does not encode. The saved cursor and bindings' values are written into the saved request and validated with it as a
saved request is, its body whole, so a value that does not fit its target is refused too; a saved dot segment for a
path parameter raises `ProtocolDataError` as a server's would.

With `StreamOptions(reconnect=True)`, a stream that has delivered a cursor reopens itself within the same step after a
read-phase transport failure the shared retry classification retries, or a read timeout the call's own
`TimeoutOptions(read=...)` set rather than the stream's idle limit, which wins a tie; after a cut frame or an end before
the declared completion it does so only when `reconnect_on` lists `incomplete_eof`. Each reopen is one more child call
of the stream's session: its own retries, Retry-After included, follow the call's retry options, and its sends count
toward the session's. Before a reopen the stream waits the retry backoff, and at least the last `retry` time the server
sent, in the session's deadline. When the backoff's cap or that `retry` time is longer than `max_reconnect_wait`,
whatever the jitter draws below the cap, or the wait is not shorter than the session's remaining time, the wait is not
begun and the interruption is raised with its `resume_state`. Running out of reconnections raises
`StreamResumeExhaustedError` with the kind `reconnects`, and out of the session's sends with the kind `network_sends`,
each with a checkpoint as `resume_state` and never as a normal end. A decode, size, remote, idle, or deadline failure,
the declared end, a reopen answered with an error, and closing never reconnect. Events the server sends again after a
reopen are delivered again, numbered on: nothing removes duplicates. Every open and reopen reports its own hook events,
so an interrupted response reports `stream_end` with the outcome `error` before its reopen starts. A
`StreamInterruptedError` that does not reconnect keeps a checkpoint as `resume_state`, or None before any cursor. No
options of a resuming helper, the client's, a view's, or the call's, may patch a header or query parameter its reopen
writes or fix an idempotency key.

### Stream generation checks

The helper's `media` must be `text/event-stream`, compared without case and with any parameters allowed, and a success
response of its operation must declare an event stream media type, which the helper then requests as declared. Each
event and error schema must exist. A body discriminator must name a declared property of each mapped and error schema
whose values can be strings, and an `event_type` completion cannot be a key of the event type mapping or the error
events:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.streams.diagnostics -->
<!-- fmt: off -->

```text
E_CONFIG_VALUE config protocols.helpers['checks.media'].media /paths/~1events/get: The media type 'text/plain' of 'checks.media' is not text/event-stream
E_CONFIG_VALUE config protocols.helpers['checks.response'].operation /paths/~1status/get: GET /status declares no text/event-stream success response for the SSE helper 'checks.response'
E_CONFIG_VALUE config protocols.helpers['checks.schema'].event_schema /paths/~1events/get: The schema '/components/schemas/Nobody' of 'checks.schema' does not exist in its document
E_CONFIG_CONFLICT config protocols.helpers['checks.terminal_event'].completion.value /paths/~1events/get: The completion event type 'done' of 'checks.terminal_event' is also a key of its event_schema.mapping
E_CONFIG_CONFLICT config protocols.helpers['checks.terminal_error'].completion.value /paths/~1events/get: The completion event type 'stop' of 'checks.terminal_error' is also a key of its error_events
E_CONFIG_VALUE config protocols.helpers['checks.discriminator_absent'].event_schema.discriminator.pointer /paths/~1events/get: The discriminator pointer '/kind' of 'checks.discriminator_absent' names no property of '/components/schemas/Created'
E_CONFIG_VALUE config protocols.helpers['checks.discriminator_type'].event_schema.discriminator.pointer /paths/~1events/get: The discriminator of 'checks.discriminator_type' reads integer values from '/components/schemas/Counted', where only string values fit
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.streams.diagnostics -->

A resumption is checked against its reopen operation, which must declare a success response of the helper's media type.
A cursor is never written to a cookie, a credential header, a security scheme's position, an exploded form object query
parameter declaring a property at such a position, or a path parameter, and one that can be cleared, an event ID or a
body cursor with `null: clear`, only to an optional header or query parameter; a body cursor must read a declared
property of an event schema, of every one under `missing: error`, whose types fit its target. Bindings are checked as
polling bindings are, reading only the headers and status of the stream responses, and a reopen with another operation
must write each of its required parameters and its body:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.streams.resume-diagnostics -->
<!-- fmt: off -->

```text
E_CONFIG_VALUE config protocols.helpers['checks.cursor_header'].resume.cursor /paths/~1events/get: The cursor of 'checks.cursor_header' reads a header, where only a body pointer reads an event's cursor
E_CONFIG_VALUE config protocols.helpers['checks.cursor_absent'].resume.cursor.pointer /paths/~1events/get: The cursor pointer '/id' of 'checks.cursor_absent' names no property of any event schema
E_CONFIG_VALUE config protocols.helpers['checks.cursor_required'].resume.cursor.pointer /paths/~1events/get: The cursor pointer '/seq' of 'checks.cursor_required' names no property of '/components/schemas/Created'
E_CONFIG_VALUE config protocols.helpers['checks.cursor_types'].resume.write /paths/~1events/get: The cursor of 'checks.cursor_types' reads integer values, which the header parameter 'Last-Event-ID' of GET /events does not accept
E_CONFIG_VALUE config protocols.helpers['checks.cursor_cookie'].resume.write /paths/~1events/get: The cursor of 'checks.cursor_cookie' writes the cookie 'session', which carries credentials no helper writes
E_CLIENT_UNSUPPORTED target protocols.helpers['checks.cursor_path'].resume.write /paths/~1streams~1{streamId}/get: The cursor of 'checks.cursor_path' is written to the path parameter 'streamId' of GET /streams/{streamId}, which is not supported yet
E_CLIENT_UNSUPPORTED target protocols.helpers['checks.cleared_body'].resume.write /paths/~1feed/post: The cursor of 'checks.cleared_body' can be cleared, which only a header or query parameter can omit; writing it to the request body of POST /feed is not supported yet
E_CONFIG_VALUE config protocols.helpers['checks.cleared_required'].resume.write /paths/~1replay/get: The cursor of 'checks.cleared_required' can be cleared, which the query parameter 'from' of GET /replay, a required one, cannot omit
E_CONFIG_VALUE config protocols.helpers['checks.reopen_media'].resume.reopen_operation /paths/~1records/get: GET /records declares no text/event-stream success response for the SSE helper 'checks.reopen_media' to reopen its stream with
E_CONFIG_VALUE config protocols.helpers['checks.bindings'].resume.bindings[0].value.selector /paths/~1events/get: The binding 0 of 'checks.bindings' reads the body of the 200 response of GET /events, which is no JSON model
E_CLIENT_UNSUPPORTED target protocols.helpers['checks.bindings'].resume.bindings[1].value.source /paths/~1streams~1{streamId}/get: The binding 1 of 'checks.bindings' reads the helper's input, which is not supported yet
E_CONFIG_CONFLICT config protocols.helpers['checks.bindings'].resume.bindings[2].target /paths/~1streams~1{streamId}/get: The binding 2 of 'checks.bindings' writes the same target as its cursor
E_CONFIG_VALUE config protocols.helpers['checks.bindings'].resume.bindings[3].value.selector /paths/~1streams~1{streamId}/get: The binding 3 of 'checks.bindings' reads the header 'X-Stream-Id', which GET /streams/{streamId} does not declare
E_CONFIG_VALUE config protocols.helpers['checks.bindings'].resume.expires_at /paths/~1events/get: The expiry of 'checks.bindings' reads a body, where only a header of a stream gives one
E_CONFIG_VALUE config protocols.helpers['checks.expiry'].resume.expires_at /paths/~1events/get: The expiry of 'checks.expiry' reads the header 'Expires', which GET /events does not declare
E_CONFIG_VALUE config protocols.helpers['checks.cursor_exploded'].resume.write /paths/~1keyed-marks/get: The cursor of 'checks.cursor_exploded' writes the query field 'api_key', which carries credentials no helper writes
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.streams.resume-diagnostics -->

## NDJSON stream helpers

An enabled `ndjson` helper reads a body of newline-delimited JSON records, one JSON value a line, as the
[SSE stream helper](#sse-stream-helpers) reads events: it is generated at `client.protocols.<name>` with the same `open`
method and returns the same `EventStream[T]`, or `AsyncEventStream[T]`, so everything above about opening, iterating,
`data()`, closing, error events, limits, and sessions applies to it too, except what concerns SSE framing:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.ndjson.helper -->
<!-- fmt: off -->

```python
    def open(
        self,
        *,
        topic: _dcg_type_0 | Unset = UNSET,
        stream_options: StreamOptions | None = None,
        options: RequestOptions | None = None,
        session_options: SessionOptions | None = None,
    ) -> EventStream[_dcg_type_1]:
        """Open the NDJSON stream of GET /records, returning once its response is a declared success."""
        return open_events(
            self._core,
            _plans.STREAM_0,
            (topic,),
            stream_options=stream_options,
            options=options,
            session_options=session_options,
        )
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.ndjson.helper -->

```python
with Client() as client, client.protocols.records.all.open() as stream:
    for record in stream.data():
        print(record.text)
```

```yaml
helpers:
  records.all:
    kind: ndjson
    operation: /paths/~1records/get
    media: application/x-ndjson
    event_schema: {pointer: /components/schemas/Record}
    completion: {kind: eof}
    final_line: require_newline
```

### Records

LF ends a line, and so does CRLF, whose CR is not part of the record; a CR alone does not end one. Each line is one
record, strict UTF-8 JSON decoded by the helper's schema, or by the schema a `{from: body, pointer}` discriminator maps
it to, the only discriminator NDJSON has. Every line is a record, so a blank or whitespace-only line, one that is not
UTF-8, a leading byte order mark, and anything else that is not JSON raise `StreamDecodeError` with the condition
`malformed`; none is skipped. The error keeps at most the event limit or 64 KiB of the line as `raw_prefix`, which no
message shows, and a line that is not UTF-8 has no `cause`.

A record yields a `StreamEvent` whose `event_type` is the empty string and whose `event_id` and `retry_ms` are None; an
`error_events` record raises `StreamRemoteError` with the `event_type` None. A `sentinel` completion ends at the line
equal to its value, which is compared before JSON decoding and never yielded, so the value need not be JSON; a value
containing LF could never match, so it fails generation with `E_CONFIG_VALUE`.

`final_line` says how the body may end. With `require_newline`, every record ends with a line end, and bytes after the
last one raise `IncompleteFrameError` with their count as `buffered_bytes`. With `allow_eof`, those bytes are decoded as
the last record, a sentinel included; a body that ends with a line end ends the same way under both.

A record counts toward both `StreamOptions.max_line_bytes` and `max_event_bytes`, without its LF or the CR of a CRLF;
one over the smaller limit raises `ProtocolSizeError` with that limit's kind, `line` or `event`, before it is kept.
Bytes are searched for a line end at most twice, so a line split over many reads costs time linear in its length.

### NDJSON generation checks

The helper's `media` must be `application/x-ndjson`, `application/ndjson`, `application/jsonl`, `application/x-jsonl`,
`application/jsonlines`, or `application/x-jsonlines`, compared without case and with any parameters allowed, and a
success response of its operation must declare that media type, which the helper then requests as declared. The schema
and resumption checks are those of SSE helpers, and an NDJSON cursor is always a body pointer:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.ndjson.diagnostics -->
<!-- fmt: off -->

```text
E_CONFIG_VALUE config protocols.helpers['checks.media'].media /paths/~1records/get: The media type 'application/json' of 'checks.media' is not one of application/jsonl, application/jsonlines, application/ndjson, application/x-jsonl, application/x-jsonlines, or application/x-ndjson
E_CONFIG_VALUE config protocols.helpers['checks.response'].operation /paths/~1status/get: GET /status declares no application/x-ndjson success response for the NDJSON helper 'checks.response'
E_CONFIG_VALUE config protocols.helpers['checks.other_media'].operation /paths/~1search/post: POST /search declares no application/x-ndjson success response for the NDJSON helper 'checks.other_media'
E_CONFIG_VALUE config protocols.helpers['checks.discriminator_absent'].event_schema.discriminator.pointer /paths/~1records/get: The discriminator pointer '/kind' of 'checks.discriminator_absent' names no property of '/components/schemas/Created'
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.ndjson.diagnostics -->

## WebSocket helpers

An enabled `websocket` helper is generated at `client.protocols.<name>` on `Client` and `AsyncClient` alike, with one
method, `connect`. Its channel is a GET operation, whose handshake request the helper sends: the operation gives the
URL, the parameters, the servers, and the security. `connect` takes the operation's parameters as keywords, then
`ws_options`, `options`, and `session_options`, and returns a `WebSocketSession[S, R]`, or with one `await` an
`AsyncWebSocketSession[S, R]`, once the server accepted the handshake:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.websocket.helper -->
<!-- fmt: off -->

```python
    def connect(
        self,
        *,
        room: _dcg_type_0,
        since: _dcg_type_1 | Unset = UNSET,
        ws_options: WSOptions | None = None,
        options: RequestOptions | None = None,
        session_options: SessionOptions | None = None,
    ) -> WebSocketSession[_dcg_type_2, _dcg_type_3]:
        """Open the WebSocket of GET /rooms/{room}/socket, returning once its handshake got a valid 101."""
        return connect_socket(
            self._core,
            _plans.SOCKET_0,
            (room, since),
            ws_options=ws_options,
            options=options,
            session_options=session_options,
        )
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.websocket.helper -->

```python
with Client() as client, client.protocols.rooms.chat.connect(room="lobby") as session:
    session.send(ClientMessage(text="hi"))
    for message in session:
        print(message.sequence, message.data)
```

```yaml
helpers:
  rooms.chat:
    kind: websocket
    operation: /paths/~1rooms~1{room}~1socket/get
    subprotocols: [chat.v2, chat.v1]
    compression: true
    send: {codec: json, schema: {pointer: /components/schemas/ClientMessage}}
    receive: {codec: json, schema: {pointer: /components/schemas/ServerMessage}}
```

`send` and `receive` declare how the messages of each direction are coded: `codec: json` with a `schema`, whose value
is sent as compact UTF-8 JSON and received through the schema's codec, `codec: utf8` for `str` text, or
`codec: bytes`. `frame` is `text` or `binary`; it defaults to `text` for JSON and UTF-8 messages and to `binary` for
bytes, and only JSON messages may choose. `S` is the send schema's argument type, its model type `T`, or `str` or
`bytes`, and `R` the received type; a union schema carries several message types. `subprotocols` lists the offered
subprotocols in order (`[]` by default), and `compression` (`false` by default) permits
`WSOptions(compression="deflate")`.

`WebSocketSession`, `AsyncWebSocketSession`, `Message`, `PingReceipt`, `WSOptions`, and `WebSocketTransportOptions` are
imported from `pkg.protocols`, and the WebSocket exceptions from `pkg.errors`. A package without WebSocket helpers
never imports the WebSocket library.

### Handshakes

The handshake is one logical call of the operation, with initial authentication, limiter, hooks, and deadline.
Only a 101 response whose headers validate opens the session: any other response is read up to `max_error_body_bytes`
and raises the operation's typed `HTTPStatusError`, or `UnexpectedStatusError` for an undeclared status, so 101 need not
be declared. Received refusals are terminal, including redirects and 401s; a refused upgrade never invalidates or
refreshes credentials. Only an initial transport failure proven `NOT_SENT` before session handover may use the call's
existing retry policy. A handshake that may have reached the server is never sent again.

The handshake's limiter permit is held for the whole session and released when it closes or fails. URLs keep their
`https` or `http` server and are opened as `wss` or `ws`. When the helper offers subprotocols, the server must select one
of them, or `connect` raises `WebSocketHandshakeError` with the condition `negotiation`; `session.subprotocol` is the
selected one and `session.response` the 101 response. Credentials are sent as the operation's security declares,
only to the server's origin or the security context's allowed origins. Headers the handshake manages, such as
`Upgrade`, `Connection`, and `Sec-WebSocket-*`, given through request options or parameters raise `ConfigurationError`
with the condition `managed` before anything is sent. The handshake's hooks end with the call outcome `handed_off`, and
the session's end emits `stream_end`.

### Sessions

| Method | Behavior |
|---|---|
| `send(value)` | Encodes one message and sends it as one message of the declared frame |
| `receive()` | Returns the next `Message[R]`: `data`, `frame` (`text` or `binary`), `sequence` from 1, and `raw` bytes |
| Iteration | Yields messages until the server closes normally |
| `ping(payload=b"")` | Sends a ping of at most 125 bytes and returns a `PingReceipt` with the pong's `latency` in seconds |
| `close(code=1000, reason="")`, `aclose` | Closes with 1000, 1001, or a code from 3000 to 4999 and a reason of at most 123 UTF-8 bytes; repeats do nothing |
| `with`, `async with` | Closes the session on exit |
| `progress` | The session's network sends, `messages_sent`, and `messages_received` |

The session owns the connection until it closes or fails, and closing the client closes it after the client's cleanup
wait. One `receive` waits at a time: another raises `ConcurrentReceiveError`, while one send may run beside it, and
sends go one at a time in arrival order. Cancelling the task of an asyncio `receive`, as `asyncio.wait_for` does, leaves
the session usable, since whole messages are read; a cancelled `send` or `ping` fails the session, since a message may
be half written. A received message of another frame kind, or one that does not decode, raises
`StreamDecodeError` with at most 64 KiB of it as `raw_prefix` and closes the connection with 1002. A server's closure
raises `WebSocketClosedError` with its `code`, `reason`, and `clean`, which is true for a normal closure that alone
ends iteration; after it, `receive`, `send`, and `ping` raise it again. After any other failure, every step raises
`ProtocolStateError`, as it does after `close()`. Representations
never show messages, URLs, headers, or close reasons.

### WebSocket limits

Each limit comes from the call's `ws_options`, then the helper's `ProtocolDefaults` in `ProtocolClientOptions.defaults`,
then the default below. The session's deadline and its idle, send, and pong timeouts run on the client's `Clock`; a wait
ends once that clock reaches its end, or once as much real time passed as the clock had left when the wait began:

| Limit | Default | None |
|---|---|---|
| `WSOptions.open_timeout` | 5 seconds, also capped by the connect, read, and write timeouts and the deadline | No open limit |
| `WSOptions.idle_timeout` | The call's merged `stream_idle_timeout`, 60 seconds by default | No idle limit |
| `WSOptions.max_message_bytes` | 1 MiB per message, after decompression | Not allowed |
| `WSOptions.max_queue` | 16 frames: the high-water mark of received frames, above which reading pauses | Not allowed |
| `WSOptions.send_timeout` | 30 seconds, waiting for earlier sends included | No send limit |
| `WSOptions.ping_interval`, `pong_timeout` | 20 seconds each | No keepalive pings |
| `WSOptions.close_timeout` | 5 seconds | Not allowed |
| `SessionOptions.total_timeout`, `deadline` | None | No session deadline |
| `SessionOptions.max_network_sends` | 16 handshake sends | Removes the limit |

A message over `max_message_bytes` raises `ProtocolSizeError` with the kind `message` after the connection closed with
1009. A receive that waits longer than the idle timeout raises `PhaseTimeoutError` with the phase `read` and closes with
1001, and a missed pong closes with 1011 and raises `WebSocketClosedError` with `clean` false. The session deadline
bounds every wait and raises `DeadlineExceededError`. A send that sent nothing before its timeout raises
`PhaseTimeoutError` with the phase `write` and keeps the session open; one that may have reached the server raises
`DeliveryUnknownError` with the delivery state `MAYBE_SENT`, closes the session, and is never sent again. A message is
written whole: in a `WebSocketSession`, the send timeout bounds the wait for earlier sends and is checked before the
write starts, and a write that started runs until it completes or the connection fails. A send to a connection the
server closed raises `WebSocketClosedError`. Sessions never reconnect.
`WSOptions(compression="deflate")` for a helper that does not permit it raises `ProtocolConfigurationError` with
`invalid_value`. Options of another type raise `ProtocolConfigurationError`.

### Connectors and transports

Without a connector, the client opens its connections with the `websockets` library, which a package with WebSocket
helpers depends on, so generation prints it among the packages to add. `ProtocolClientOptions(websocket_connector=...)`
borrows a `WebSocketConnector`, or an `AsyncWebSocketConnector` for `AsyncClient`, which is never closed: its `open`
receives a `WebSocketOpenRequest` with the `ws` or `wss` URL, the headers, and the offered subprotocols, the attempt's
context, the resolved `WSOptions`, and the resolved transport settings, and opens one connection or raises
`HandshakeResponse` with a response other than 101, which the client turns into the call's result. A connector of the
other kind raises `ProtocolConfigurationError` with the condition `wrong_capability` when the client is constructed.

`websocket_transport=WebSocketTransportOptions(...)` sets the `ssl_context` of the server connection, an HTTP or HTTPS
`proxy` URL, which the representation hides, the `proxy_ssl_context` of an HTTPS proxy, and `trust_env`, which lets
environment proxies apply. A proxy that refuses or breaks the tunnel raises `WebSocketProxyError`; SOCKS proxies are
not supported.

### WebSocket generation checks

The operation must be a GET without a request body, and each JSON message's schema must exist. Message definitions, frames, and subprotocol tokens are checked as the helper file is read:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.websocket.diagnostics -->
<!-- fmt: off -->

```text
E_CONFIG_VALUE config protocols.helpers['checks.method'].operation /paths/~1chat/post: The WebSocket helper 'checks.method' opens POST /chat, which is not a GET operation
E_CONFIG_VALUE config protocols.helpers['checks.body'].operation /paths/~1upload/get: The WebSocket helper 'checks.body' opens GET /upload, which takes a request body
E_CONFIG_VALUE config protocols.helpers['checks.schema'].send.schema /paths/~1chat/get: The schema '/components/schemas/Nobody' of 'checks.schema' does not exist in its document
E_CONFIG_VALUE config protocols.helpers['codec.unknown'].send.codec: protocols.helpers['codec.unknown'].send.codec must be 'json', 'utf8' or 'bytes'
E_CONFIG_VALUE config protocols.helpers['codec.schema_missing'].send.schema: protocols.helpers['codec.schema_missing'].send needs 'schema' for JSON messages
E_CONFIG_CONFLICT config protocols.helpers['codec.schema_extra'].send.schema: protocols.helpers['codec.schema_extra'].send.schema applies only to JSON messages
E_CONFIG_CONFLICT config protocols.helpers['frame.text'].send.frame: protocols.helpers['frame.text'].send.frame must be 'text' for utf8 messages
E_CONFIG_CONFLICT config protocols.helpers['frame.binary'].receive.frame: protocols.helpers['frame.binary'].receive.frame must be 'binary' for bytes messages
E_CONFIG_VALUE config protocols.helpers['frame.json'].receive.frame: protocols.helpers['frame.json'].receive.frame must be 'text' or 'binary'
E_CONFIG_VALUE config protocols.helpers['subprotocols.token'].subprotocols[0]: protocols.helpers['subprotocols.token'].subprotocols[0] must be a subprotocol token
E_CONFIG_VALUE config protocols.helpers['subprotocols.token'].subprotocols[1]: protocols.helpers['subprotocols.token'].subprotocols[1] must be a subprotocol token
E_CONFIG_VALUE config protocols.helpers['subprotocols.repeated'].subprotocols[1]: protocols.helpers['subprotocols.repeated'].subprotocols[1] repeats a subprotocol
E_CONFIG_VALUE config protocols.helpers['message.type'].compression: protocols.helpers['message.type'].compression must be a boolean
E_CONFIG_VALUE config protocols.helpers['message.type'].send: protocols.helpers['message.type'].send must be a message definition
E_CONFIG_UNKNOWN config protocols.helpers['message.type'].receive.extra: protocols.helpers['message.type'].receive has no key 'extra'
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.websocket.diagnostics -->

## Cache helpers

An enabled `cache` helper fetches one GET operation through a private cache that you lend it: it answers from a fresh
stored response without sending, revalidates a stale one with its `ETag` or `Last-Modified` validator, and otherwise
sends the request as an ordinary call. Nothing is cached for ordinary methods, and a client without a helper or a
store keeps no cache state. The helper is generated at `client.protocols.<name>` on `Client` and `AsyncClient` alike:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.cache.yaml -->
<!-- fmt: off -->

```yaml
schema_version: 1
helpers:
  users.profile:
    kind: cache
    operation: /paths/~1users~1{userId}/get
    validator: both
    authenticated: false
    vary_allowlist: [Accept-Language]
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.cache.yaml -->

| Setting | Meaning |
|---|---|
| `operation` | The GET operation the helper fetches. It takes no request body, and each cacheable status is a declared 2xx success with one JSON body |
| `validator` | `etag` revalidates with `If-None-Match` from `ETag`, `last_modified` with `If-Modified-Since` from `Last-Modified`, and `both` with `If-None-Match` when an `ETag` is stored and `If-Modified-Since` otherwise |
| `authenticated` | Whether the fetch carries credentials. It must match every call: a call that the auth, a credential or cookie header, or a security scheme's field authenticates needs `true` and a credential partition, and any other call `false` |
| `statuses` | The cacheable statuses, distinct, from 100 to 599 |
| `vary_allowlist` | The request headers a response's `Vary` may name; a response that varies on any other header, or on `*`, is not stored. A header credentials travel in (`Authorization`, `Proxy-Authorization`, `Cookie`, `Cookie2`, or a declared security scheme's header) fails generation with `E_CONFIG_VALUE`. Responses behind a CDN often vary on `Accept-Encoding`: allow it to store them |

`fetch` takes the operation's parameters as keywords, then `cache_options` and `options`, and returns a `CacheResult`.
With asyncio, `fetch` is a coroutine:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.cache.helper -->
<!-- fmt: off -->

```python
    def fetch(
        self,
        *,
        fields: _dcg_type_0 | Unset = UNSET,
        accept_language: _dcg_type_1 | Unset = UNSET,
        user_id: _dcg_type_2,
        cache_options: CacheOptions | None = None,
        options: RequestOptions | None = None,
    ) -> CacheResult[GetUserResponse]:
        """Fetch GET /users/{userId} through the helper's cache, revalidating a stale entry."""
        return fetch(
            self._core,
            _plans.PLAN_0,
            (fields, accept_language, user_id),
            cache_options=cache_options,
            options=options,
        )
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.cache.helper -->

A helper needs the store `ProtocolClientOptions.cache_stores` lends it under its name, or `fetch` raises `ProtocolConfigurationError(condition='missing_adapter')` before sending. The client checks the stores
when it is constructed: a name that is no cache helper of the package fails with `unknown_field`, and an object without
the three store methods, or whose methods are coroutines for a `Client` or plain functions for an `AsyncClient`, with
`wrong_capability`. The client borrows a store: it never creates, closes, or keeps one after a call.

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.cache.usage -->
<!-- fmt: off -->

```python
def cached_client() -> Client:
    """Lend a bounded memory store to the users.profile helper, which keeps its entries there."""
    store = MemoryCacheStore(max_entries=1000)
    return Client(options=ClientOptions(protocols=ProtocolClientOptions(cache_stores={"users.profile": store})))
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.cache.usage -->

### Results and freshness

`CacheResult[T]` is immutable: `data: T`, the decoded body; `source`, `network` for a response the network sent,
`fresh_cache` for a fresh stored response answered without sending, or `revalidated` for a stored response a 304
confirmed; `response: ResponseInfo`; and `network_status`, the status the network returned, 304 for a revalidation and
`None` for a fresh answer. A fresh answer's `response` has a new `call_id`, every count 0, and the elapsed time of the
lookup and decoding; a revalidation's has the 304 call's identity, counts, and elapsed time, with the stored status and
the merged headers, request id, and content type. A stored body is decoded again on every use, so callers never share a
model object. A fresh answer sends nothing, so it emits no call events to hooks and takes no limiter permit; a network
fetch emits the events of an ordinary call.

An entry is fresh while its age is below its freshness lifetime and below `CacheOptions.max_ttl`. The lifetime is the
response's `Cache-Control: max-age`, else `Expires` minus `Date`, else 0; the age follows RFC 9111 from `Age`, `Date`,
and the times of the request and the response. There is no heuristic freshness and no stale-on-error. A response's
`no-cache` makes every use revalidate. A fetch's own `Cache-Control` may say `no-cache` (revalidate), `no-store` (no
stored response is read or written), or `max-age=N` (use a stored response only up to that age); any other directive,
and a `Range`, `If-Range`, `If-Match`, or `If-Unmodified-Since` header, raises `ProtocolConfigurationError` before
anything is looked up.

A stale entry is revalidated with its validator as `validator` declares; a fetch that gives `If-None-Match` or
`If-Modified-Since` itself sends it once, and raises `CacheValidatorConflictError` before sending when a usable entry
has another validator or the header is given more than once. A 304 updates the entry's headers, except `Content-Type`, `Content-Encoding`, `Content-Length`,
and hop-by-hop fields, and its freshness. A 304 without a usable entry, after a redirect, or with an `ETag` or
`Last-Modified` other than the entry's raises `CacheProtocolError`; no request is sent again.

### Storing and keys

A response is stored only when its status is cacheable, its body is within `CacheOptions.max_entry_bytes`, it came
without a redirect, it has no `Set-Cookie`, its `Cache-Control` has only RFC 9111 directives (`s-maxage` is ignored)
and no `no-store`, its `Vary` names only allowlisted headers, and it is fresh or can be revalidated. A response that
cannot be stored removes the entry it supersedes, and errors and decoding failures store nothing and keep the entry.
Entries hold the body after content decoding and the headers without `Content-Encoding` or hop-by-hop fields.

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.cache.keys -->
`fetch` returns a `CacheResult` whose `source` is `fresh_cache` for a fresh entry, answered without sending or call
events, `revalidated` for a stale entry a 304 confirmed, and `network` otherwise; a stored body is decoded again every
time. An entry is keyed by the method, the URL, the Accept header, the credential partition, and the credentials the
auth binds, and selected by the request headers its `Vary` names and those a header patch or a declared parameter fills.
A request carrying credentials needs `ProtocolSecurityContext.credential_partition`, a helper declared authenticated,
and the client's own auth, not a view's or a call's; anything else raises `ProtocolConfigurationError`. A response whose
`Vary` names a header the auth manages is never stored, and one partition is one permission set: credentials the client
cannot see, such as a client certificate, need a partition of their own. Freshness comes from `max-age` or `Expires`
only, capped by `max_ttl`; a stale entry is revalidated with its validator, and a 304 without a usable entry raises
`CacheProtocolError`. A response is stored only when its status is cacheable, it came without a redirect, Set-Cookie,
`no-store`, or an unsupported Cache-Control directive, and its `Vary` names only allowlisted headers; otherwise it
removes the entry it supersedes. Store failures raise `CacheStoreError` and never resend a request.
The store keeps one representation per key. A Vary mismatch is a miss; a successful cacheable response replaces it.
Vary stores plain ordered header values in private process memory, excluding credential headers and cookies.
Memory stores are bounded by entry count (128 by default), with reads and writes marking a key recently used.
Concurrent custom-store writes use last-completing replacement.
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.cache.keys -->

!!! warning "Credentials the cache cannot see"
    The auth adds its credentials after the cache looks a request up, so a response whose `Vary` names a header the
    call's auth manages, such as `Authorization`, a cookie, a declared scheme's header, or a signer's managed header, is
    never stored. Within one partition, all calls are taken to share one permission set: a provider whose token or
    identity changes from call to call needs a client per partition. A view's or a call's own `auth` therefore fails
    an authenticated fetch with `ProtocolConfigurationError(field_path=('options', 'auth'),
    condition='security_partition')`; `auth=None` stays allowed for anonymous fetches. Credentials the SDK never sees,
    such as a client certificate, a borrowed HTTP client's own headers, or a transport adapter that authenticates, make
    a call look anonymous: give each such identity its own `credential_partition`, or its own store.

### Stores

`CacheStore` and `AsyncCacheStore` provide `get(key) -> CacheEntry | None`, `set(key, entry) -> None`, and
`delete(key) -> None`, using byte keys. Each key holds one representation. A Vary mismatch is a miss, and a successful
cacheable response replaces the representation. Concurrent custom-store writes use last-completing replacement.
A store exception or a result of another type raises `CacheStoreError` with the method as `action` and the exception
as `cause`; the request is never sent again for it.

`CacheEntry` has `vary`, `vary_values`, `status_code`, `headers`, `body`, `request_time`, `response_time`, `stored_at`,
`freshness_seconds`, `initial_age_seconds`, and `schema_fingerprint`. Plain Vary values preserve each named header's
ordered values, including the distinction between a missing header and an empty value. Credential headers and cookies
are excluded from these values and isolated by the opaque cache key. Values stay in private process memory and the
entry's representation names only its status. A mismatched fingerprint or status cannot serve a fetch or a 304.

`MemoryCacheStore(max_entries=128)` and `AsyncMemoryCacheStore(max_entries=128)` bound entries by count. Reads and
writes mark a key recently used, replacement keeps the count unchanged, and adding a key evicts the least recently
used key when needed. Deletion is idempotent. Freshness and revalidation use the fetching client's clock.

### Cache generation checks

The operation must be a GET without a request body; other methods fail with `E_CONFIG_VALUE`, and HEAD is not
supported yet. Each cacheable status must be a declared 2xx success with one natively decoded JSON body, a helper
declared anonymous cannot fetch an operation that requires credentials:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.cache.diagnostics -->
<!-- fmt: off -->

```text
E_CONFIG_VALUE config protocols.helpers['users.created'].operation /paths/~1users/post: The cache helper 'users.created' fetches POST /users, which changes state; only GET is cached
E_CONFIG_VALUE config protocols.helpers['users.created'].operation /paths/~1users/post: The cache helper 'users.created' fetches POST /users, which takes a request body
E_CONFIG_VALUE config protocols.helpers['users.created'].statuses[0] /paths/~1users/post: The cacheable status 200 of 'users.created' is no declared 2xx success of POST /users
E_CLIENT_UNSUPPORTED target protocols.helpers['users.checked'].operation /paths/~1users~1{userId}/head: The cache helper 'users.checked' fetches HEAD /users/{userId}, and HEAD is not supported yet
E_CLIENT_UNSUPPORTED target protocols.helpers['users.checked'].statuses[0] /paths/~1users~1{userId}/head: The cacheable status 200 of 'users.checked' has a response other than one natively decoded JSON body, which is not supported yet
E_CONFIG_VALUE config protocols.helpers['reports.get'].operation /paths/~1reports/get: The cache helper 'reports.get' fetches GET /reports, which takes a request body
E_CLIENT_UNSUPPORTED target protocols.helpers['reports.get'].statuses[0] /paths/~1reports/get: The cacheable status 200 of 'reports.get' has a response other than one natively decoded JSON body, which is not supported yet
E_CLIENT_UNSUPPORTED target protocols.helpers['reports.get'].statuses[1] /paths/~1reports/get: The cacheable status 204 of 'reports.get' has a response other than one natively decoded JSON body, which is not supported yet
E_CONFIG_VALUE config protocols.helpers['users.statuses'].statuses[0] /paths/~1users~1{userId}/get: The cacheable status 404 of 'users.statuses' is no declared 2xx success of GET /users/{userId}
E_CONFIG_VALUE config protocols.helpers['users.statuses'].statuses[1] /paths/~1users~1{userId}/get: The cacheable status 201 of 'users.statuses' is no declared 2xx success of GET /users/{userId}
E_CONFIG_VALUE config protocols.helpers['secure.anonymous'].authenticated /paths/~1secure~1users~1{userId}/get: The cache helper 'secure.anonymous' is declared anonymous, but GET /secure/users/{userId} requires credentials
E_CONFIG_VALUE config protocols.helpers['secure.varying'].vary_allowlist[1] /paths/~1secure~1users~1{userId}/get: The cache helper 'secure.varying' allows a Vary on 'authorization', which credentials travel in; the auth adds it after the cache looks a request up
E_CONFIG_VALUE config protocols.helpers['secure.varying'].vary_allowlist[2] /paths/~1secure~1users~1{userId}/get: The cache helper 'secure.varying' allows a Vary on 'Cookie', which credentials travel in; the auth adds it after the cache looks a request up
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.cache.diagnostics -->


## Signature style

`signature_style` chooses how every operation method of the generated package declares its keyword arguments.

| Setting | Values | Default | Where |
|---|---|---|---|
| `signature_style` | `"explicit"`, `"unpack"` | `"explicit"` | Target file: `signature_style = "unpack"`; Python: `ClientGenerationConfig(signature_style="unpack")` |

It is one choice for the whole package: there is no per-operation setting and no call-time switch. It changes only
how methods are declared. The calls a method accepts, the requests it sends, its results, and its errors are the same
in both styles, and the model backend is unaffected: `unpack` does not turn your models into TypedDicts.

### `explicit`

Every keyword is a real keyword-only parameter of the method and of each of its overloads:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.signature-style.explicit -->
<!-- fmt: off -->

```python
    @overload
    def get_pet(
        self,
        *,
        pet_id: _dcg_type_6,
        response_media_type: None = None,
        options: RequestOptions | None = None,
    ) -> _dcg_type_7: ...
    @overload
    def get_pet(
        self,
        *,
        pet_id: _dcg_type_6,
        response_media_type: Literal['application/json'],
        options: RequestOptions | None = None,
    ) -> _dcg_type_7: ...
    @overload
    def get_pet(
        self,
        *,
        pet_id: _dcg_type_6,
        response_media_type: Literal['text/plain'],
        options: RequestOptions | None = None,
    ) -> _dcg_type_8: ...
    def get_pet(
        self,
        *,
        pet_id: _dcg_type_6,
        response_media_type: Literal['application/json', 'text/plain'] | None = None,
        options: RequestOptions | None = None,
    ) -> GetPetResponse:
        """Show one pet."""
        return self._core.execute(
            _operations.OPERATION_2,
            (pet_id,),
            options=options,
            response_media_type=response_media_type,
        ).data
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.signature-style.explicit -->

### `unpack`

Every overload and the implementation take `**kwargs` typed by a TypedDict of that signature's keywords:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.signature-style.unpack -->
<!-- fmt: off -->

```python
    @overload
    def get_pet(self, **kwargs: Unpack[Operation2Arguments]) -> _dcg_type_0: ...
    @overload
    def get_pet(self, **kwargs: Unpack[Operation2Arguments1]) -> _dcg_type_0: ...
    @overload
    def get_pet(self, **kwargs: Unpack[Operation2Arguments2]) -> _dcg_type_1: ...
    def get_pet(self, **kwargs: Unpack[Operation2Arguments3]) -> GetPetResponse:
        """Show one pet."""
        KEYWORDS_2.check(kwargs)
        return self._core.execute(
            _operations.OPERATION_2,
            (kwargs['pet_id'],),
            options=kwargs.get('options'),
            response_media_type=kwargs.get('response_media_type'),
        ).data
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.signature-style.unpack -->

The TypedDicts live in the private module `pkg._generated.client_arguments`, one for each distinct signature of an
operation, shared by its normal, `with_response`, `with_raw_response`, and `with_streaming_response` views. Their
names follow the operation's position and may change between generations, so do not import them: declare your own
TypedDict with the keys you forward. The module also holds the list of keywords each method takes:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.signature-style.records -->
<!-- fmt: off -->

```python
class Operation2Arguments(TypedDict):
    """The keyword arguments of one signature of get_pet."""

    pet_id: _dcg_type_6
    response_media_type: NotRequired[None]
    options: NotRequired[RequestOptions | None]


class Operation2Arguments1(TypedDict):
    """The keyword arguments of one signature of get_pet."""

    pet_id: _dcg_type_6
    response_media_type: Literal['application/json']
    options: NotRequired[RequestOptions | None]


class Operation2Arguments2(TypedDict):
    """The keyword arguments of one signature of get_pet."""

    pet_id: _dcg_type_6
    response_media_type: Literal['text/plain']
    options: NotRequired[RequestOptions | None]


class Operation2Arguments3(TypedDict):
    """The keyword arguments of one signature of get_pet."""

    pet_id: _dcg_type_6
    response_media_type: NotRequired[Literal['application/json', 'text/plain'] | None]
    options: NotRequired[RequestOptions | None]


KEYWORDS_2: Final = Keywords(
    'get_pet',
    ('pet_id', 'response_media_type', 'options'),
    ('pet_id',),
)
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.signature-style.records -->

### What differs between the styles

Both styles accept the same direct calls, and forwarding a mapping whose type declares known keys:

```python
from typing_extensions import NotRequired, TypedDict


class ListArguments(TypedDict):
    x_trace: FieldPetsGetHeaderXTraceParameter
    options: NotRequired[RequestOptions | None]


def forward(client: Client, arguments: ListArguments) -> ListPetsResponse:
    return client.pets.list_pets(**arguments)
```

- **Introspection.** `inspect.signature` shows the keyword-only parameters of an `explicit` method, and a single
  `**kwargs` parameter for an `unpack` method. `typing.get_type_hints(method, include_extras=True)` resolves the
  TypedDict of an `unpack` method from any module, whose keys are the same keywords, optional ones marked
  `NotRequired`.
- **Binding errors.** An unknown keyword or a missing required one raises `TypeError` in both styles, before anything
  is sent. Python binds an `explicit` async method when it is called, while an `unpack` async method checks its
  keywords when it is awaited; async streaming methods, which are ordinary functions, check them when called. The
  wording of the error message is not the same.
- **Type checkers.** Both styles give the same keywords the same types. Checkers treat a TypedDict with extra keys
  differently: forwarding one to an `explicit` method is an error for mypy, Pyright, and ty, while for an `unpack`
  method only mypy reports it. The method still refuses the extra key at runtime.

The binding differences, as the tests of this repository record them for the same `get_pet` and `list_pets` calls:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.signature-style.binding -->
<!-- fmt: off -->

```text
# explicit
plain get_pet (pet_id:KEYWORD_ONLY, response_media_type:KEYWORD_ONLY, options:KEYWORD_ONLY) unbound (self:POSITIONAL_OR_KEYWORD, pet_id:KEYWORD_ONLY, response_media_type:KEYWORD_ONLY, options:KEYWORD_ONLY)
  keywords pet_id!, response_media_type, options coroutine False
async plain get_pet (pet_id:KEYWORD_ONLY, response_media_type:KEYWORD_ONLY, options:KEYWORD_ONLY) unbound (self:POSITIONAL_OR_KEYWORD, pet_id:KEYWORD_ONLY, response_media_type:KEYWORD_ONLY, options:KEYWORD_ONLY)
  keywords pet_id!, response_media_type, options coroutine True
async list with an unknown keyword ! TypeError when called, sent False
async list with no keyword ! TypeError when called, sent False
# unpack
plain get_pet (kwargs:VAR_KEYWORD) unbound (self:POSITIONAL_OR_KEYWORD, kwargs:VAR_KEYWORD)
  keywords pet_id!, response_media_type, options coroutine False
async plain get_pet (kwargs:VAR_KEYWORD) unbound (self:POSITIONAL_OR_KEYWORD, kwargs:VAR_KEYWORD)
  keywords pet_id!, response_media_type, options coroutine True
async list with an unknown keyword ! TypeError when awaited, sent False
async list with no keyword ! TypeError when awaited, sent False
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.signature-style.binding -->

### Cost

`explicit` adds nothing at runtime. An `unpack` method checks the keys it was given and reads each value with its
default, which added 0.3–0.9 µs to a 65 µs `list_pets` call over a mock transport, and importing a resource module
takes about 0.5 ms more for its TypedDicts (CPython 3.13, macOS arm64, Pydantic v2 models). Rendering the package
takes the same time in both styles.

## Body field arguments

`body_arguments` chooses whether a call may give the declared fields of an object body as keyword arguments instead
of the body. `"body"`, the default, keeps the body argument alone; `"both"` adds the fields of every body that can
take them next to the body argument, on every view of the method.

| Setting | Values | Default | Where |
|---|---|---|---|
| `body_arguments` | `"body"`, `"both"` | `"body"` | Target file: `body_arguments = "both"`; Python: `ClientGenerationConfig(body_arguments="both")`; one operation: `ClientOperationConfig(body_arguments=...)`, where `None` inherits the package's |
| `body_field_names` | `BodyFieldName` records | `()` | One operation: `ClientOperationConfig(body_field_names=(BodyFieldName(media_type=..., name=..., python_name=...),))` |

With `"both"`, a JSON body whose model is `NewPet` takes either call:

```python
client.pets.create_pet(body=NewPet(name="Mimi", kind=Kind.cat), media_type="application/json")
client.pets.create_pet(name="Mimi", kind=Kind.cat, media_type="application/json")
```

The target file takes the same fields:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.body-fields.toml -->
<!-- fmt: off -->

```toml
schema_version = 1
output = "client"
package = "client"
model_package = "models"
body_arguments = "both"

[[operations]]
ref = "/paths/~1pets~1{petId}/put"
body_arguments = "body"

[[operations]]
ref = "/paths/~1pets/post"
body_field_names = [{ media_type = "application/json", name = "tag", python_name = "pet_tag" }]
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.body-fields.toml -->

### Which bodies take fields

A JSON or form body takes fields when its schema is one object whose model its backend constructs from them, an
object that may be null included: Pydantic models and dataclasses, stdlib dataclasses, TypedDicts, and msgspec
Structs alike. Other bodies keep only `body`: unions of objects, arrays and scalars, bodies without a schema, binary
bodies and form-data sent as parts, bodies whose model cannot hold a request, such as one with a required read-only
member, and objects whose schema requires a key only their extra properties can hold. Extra keys, a null body, and an empty object also need `body`, and a nested object is given as its own model.

Each field argument is named by the snake case of its wire property, and a read-only member has none.
`body_field_names` names one field of one media type instead, without changing the model or the wire property. Fields
of different media types may share a name. Generation fails with `E_NAME_COLLISION` when a field would take the name
of a parameter or of another field of the same media type, a reserved name such as `body` or `options`, or no valid
identifier, and with `E_CONFIG_VALUE` when a body field name names no field argument or its operation keeps only
`body`:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.body-fields.diagnostics -->
<!-- fmt: off -->

```text
E_CONFIG_VALUE config operations /paths/~1pets/post: The body field name of the application/json property 'id' of POST /pets names no field argument
E_CONFIG_VALUE config operations /paths/~1pets/post: The body field name of the application/xml property 'tag' of POST /pets names no field argument
E_CONFIG_VALUE config operations /paths/~1pets/post: The body field name of the application/json property 'absent' of POST /pets names no field argument
E_NAME_COLLISION target /paths/~1pets/post: The application/json body fields of POST /pets cannot take the argument names 'kind', 'tag'; name them with body_field_names
E_NAME_COLLISION target /paths/~1pets/post: The application/x-www-form-urlencoded body fields of POST /pets cannot take the argument names 'tag'; name them with body_field_names
E_NAME_COLLISION target /paths/~1pets~1{petId}~1visits/post: The application/json body fields of POST /pets/{petId}/visits cannot take the argument names 'options'; name them with body_field_names
E_CONFIG_VALUE config operations /paths/~1search/post: The body field name of the text/plain property 'q' of POST /search names no field argument
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.body-fields.diagnostics -->

### Signatures

The body and each media type's fields are separate overloads. The body's takes every field as `Unset`; a field
overload takes the body as `Unset`, its required fields without a default, and its optional fields with `UNSET`, and
a field takes `None` only when its property is nullable. An optional body whose fields are all optional has one
overload for each field, which that overload requires, and one that takes nothing and sends nothing:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.body-fields.overloads -->
<!-- fmt: off -->

```python
    @overload
    def update_pet(
        self,
        *,
        pet_id: _dcg_type_6,
        body: _dcg_type_7,
        name: Unset = UNSET,
        tag: Unset = UNSET,
        media_type: Literal['application/json'] | None = None,
        options: RequestOptions | None = None,
    ) -> UpdatePetResponse: ...
    @overload
    def update_pet(
        self,
        *,
        pet_id: _dcg_type_6,
        body: Unset = UNSET,
        name: str,
        tag: str | None | Unset = UNSET,
        media_type: Literal['application/json'] | None = None,
        options: RequestOptions | None = None,
    ) -> UpdatePetResponse: ...
    @overload
    def update_pet(
        self,
        *,
        pet_id: _dcg_type_6,
        body: Unset = UNSET,
        name: str | Unset = UNSET,
        tag: str | None,
        media_type: Literal['application/json'] | None = None,
        options: RequestOptions | None = None,
    ) -> UpdatePetResponse: ...
    @overload
    def update_pet(
        self,
        *,
        pet_id: _dcg_type_6,
        body: Unset = UNSET,
        name: Unset = UNSET,
        tag: Unset = UNSET,
        media_type: None = None,
        options: RequestOptions | None = None,
    ) -> UpdatePetResponse: ...
    def update_pet(
        self,
        *,
        pet_id: _dcg_type_6,
        body: _dcg_type_7 | Unset = UNSET,
        name: str | Unset = UNSET,
        tag: str | None | Unset = UNSET,
        media_type: Literal['application/json'] | None = None,
        options: RequestOptions | None = None,
    ) -> UpdatePetResponse:
        """Call PATCH /pets/{petId}."""
        return self._core.execute(
            _operations.OPERATION_1,
            (pet_id,),
            body=body,
            fields=(name, tag),
            media_type=media_type,
            options=options,
        ).data
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.body-fields.overloads -->

The implementation takes every field with an `UNSET` default, and with `signature_style = "unpack"` each overload
takes its own TypedDict.

### Calls

A call gives the body or the fields of the media type it sends, never both, and an explicit `UNSET` counts as
omitted. The method refuses any other binding before any hook, request, or stream: with `TypeError` for a body with
fields, a missing required field, a field of another media type, or fields for a media type that takes none, and with
`ConfigurationError` for a missing or undeclared media type, as it does for bodies:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.body-fields.refusals -->
<!-- fmt: off -->

```text
a body and fields ! TypeError: create_pet() takes a body or its field arguments, not both: 'name' []
  hook: call_start attempt=None sent=False status=None outcome=None phase=None path=/pets origin=None counts=0/0 request_id=None timed=False context={} options={'max_response_bytes': None, 'max_error_body_bytes': 65536, 'max_stream_bytes': None, 'cleanup_timeout': 5.0, 'total_timeout': 60.0, 'max_network_sends': 3, 'stream_idle_timeout': 60.0, 'stream_total_timeout': None}
  hook: call_end attempt=None sent=False status=None outcome=error phase=None path=/pets origin=None counts=0/0 request_id=None timed=True context={}
fields missing a required one ! TypeError: create_pet() missing required field arguments for application/json: 'kind' []
  hook: call_start attempt=None sent=False status=None outcome=None phase=None path=/pets origin=None counts=0/0 request_id=None timed=False context={} options={'max_response_bytes': None, 'max_error_body_bytes': 65536, 'max_stream_bytes': None, 'cleanup_timeout': 5.0, 'total_timeout': 60.0, 'max_network_sends': 3, 'stream_idle_timeout': 60.0, 'stream_total_timeout': None}
  hook: call_end attempt=None sent=False status=None outcome=error phase=None path=/pets origin=None counts=0/0 request_id=None timed=True context={}
a field of another media ! TypeError: create_pet() takes no such field arguments for application/x-www-form-urlencoded: 'kind' []
  hook: call_start attempt=None sent=False status=None outcome=None phase=None path=/pets origin=None counts=0/0 request_id=None timed=False context={} options={'max_response_bytes': None, 'max_error_body_bytes': 65536, 'max_stream_bytes': None, 'cleanup_timeout': 5.0, 'total_timeout': 60.0, 'max_network_sends': 3, 'stream_idle_timeout': 60.0, 'stream_total_timeout': None}
  hook: call_end attempt=None sent=False status=None outcome=error phase=None path=/pets origin=None counts=0/0 request_id=None timed=True context={}
fields without a media type ! ConfigurationError: ConfigurationError(operation_id='createPet', call_id='<call>', field_path='media_type', condition='missing') [operation_id='createPet', field_path=('media_type',), condition='missing'] configuration_error
  hook: call_start attempt=None sent=False status=None outcome=None phase=None path=/pets origin=None counts=0/0 request_id=None timed=False context={} options={'max_response_bytes': None, 'max_error_body_bytes': 65536, 'max_stream_bytes': None, 'cleanup_timeout': 5.0, 'total_timeout': 60.0, 'max_network_sends': 3, 'stream_idle_timeout': 60.0, 'stream_total_timeout': None}
  hook: call_end attempt=None sent=False status=None outcome=error phase=None path=/pets origin=None counts=0/0 request_id=None timed=True context={}
fields for text ! TypeError: log_visit() takes no field arguments for text/plain: 'note' []
  hook: call_start attempt=None sent=False status=None outcome=None phase=None path=/pets/{petId}/visits origin=None counts=0/0 request_id=None timed=False context={} options={'max_response_bytes': None, 'max_error_body_bytes': 65536, 'max_stream_bytes': None, 'cleanup_timeout': 5.0, 'total_timeout': 60.0, 'max_network_sends': 3, 'stream_idle_timeout': 60.0, 'stream_total_timeout': None}
  hook: call_end attempt=None sent=False status=None outcome=error phase=None path=/pets/{petId}/visits origin=None counts=0/0 request_id=None timed=True context={}
update naming only a media type ! ConfigurationError: ConfigurationError(operation_id='updatePet', call_id='<call>', field_path='media_type', condition='without_body') [operation_id='updatePet', field_path=('media_type',), condition='without_body'] configuration_error
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.body-fields.refusals -->

- Only the fields a call gives are sent: the model's defaults are not, and `None` sends `null`.
- A required body whose fields are all optional is sent as `{}` when a call gives none of them. An optional body is
  omitted, and naming only its media type still raises `ConfigurationError`.
- The model is built from the fields by its constructor, which validates Pydantic models and dataclasses, while stdlib
  dataclasses, TypedDicts, and msgspec Structs take the values as given.

### Cost

A body call of a package generated with `"both"` spends about 0.2 µs checking that it gives no fields, within the
noise of a 57–64 µs `create_pet` call over a mock transport. Giving fields instead of a body built in the same call
adds 2.4–4.1 µs: 1.6 µs to bind them and select the media type, which the call then does not select again, and about
0.7 µs to build the model and keep only the given fields (CPython 3.13, macOS arm64, every backend).

## Model codecs

Generated clients send and read model values through their model backend, with no validation layer of their own:

- **Requests.** Arguments and bodies are serialized from the model values as given. Aliases, presence, and the
  conversion of dates, decimals, enums, and media follow the models, and a read-only member is left out. A value is
  checked only as far as its model's constructor already checked it, and what the serializer cannot represent fails
  with `RequestEncodingError` before anything is sent. An empty array or object in a style-encoded parameter or a form
  member is left out as an omitted value is: it writes no query pair, header, cookie, or part. A path segment cannot be
  left out, so an empty array or object in a path parameter fails with `RequestEncodingError` before anything is sent.
- **Responses.** Bodies, parts, stream events, and socket messages are converted by the model backend into the declared
  types, which check what those types declare and coerce as the backend does; a value the type refuses raises
  `ResponseValidationError`, and a write-only member is refused.
- **Binding.** A missing required argument, an unknown keyword, or a media type the operation does not declare fails
  before anything is sent.
- **Path segments.** Path arguments that make their segment `.` or `..` once encoded in their parameters' styles and
  put into the path template, `%2E` in either case counting as `.`, such as `..` in the simple style, the empty string
  in the label style, or `%2e` with `allowReserved`, fail with `RequestEncodingError` at the segment's first parameter
  whose encoded text is non-empty, with a `ParameterEncodingError` cause, before anything is sent: URL normalization in
  clients, proxies, and servers would remove the segment and send the call to another resource, and encoding the dots
  does not prevent it. `...` and `.a` are sent as they are, and a dot segment the path template spells is not refused.
- **Headers.** The header decoders check a header's value against its schema before converting it. Read a body
  without any model with `with_raw_response` or `with_streaming_response` instead.

A response whose union members only their schemas tell apart, such as two object models read by a stdlib dataclass or
TypedDict converter, cannot be converted, so generation fails with `E_CONFIG_VALUE` naming the use:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.model-codecs.diagnostics -->
<!-- fmt: off -->

```text
E_CONFIG_VALUE binding /paths/~1orders~1{orderId}/get/responses/200: The client cannot convert the response body 200 application/json of GET /orders/{orderId}, which tells its union members apart only by their schemas
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.model-codecs.diagnostics -->

## Timeouts, cancellation, and send limits

The generated package's `options` module provides `ClientOptions`, `RequestOptions`, `TimeoutOptions`, `Deadline`,
`CancelToken`, and `Clock`. These settings apply to typed operations and `request_raw`, including their response and
streaming views. The following examples use a generated package named `pets` and take the service URL from their
caller.

| Option | Effective default | Meaning |
|---|---|---|
| `timeout` | `TimeoutOptions(connect=5, read=30, write=30, pool=5)` | Native I/O phase limits, in seconds |
| `total_timeout` | `60` | Relative budget from call entry through encoding, callbacks, sending, reading, and decoding |
| `deadline` | `None` | An absolute monotonic deadline created by `Deadline.after(seconds)` |
| `cancel_token` | `None` | An explicit cancellation signal shared with the call |
| `max_network_sends` | `1 + max_retries + (max_redirects if enabled)` | Maximum number of send slots the call may reserve; `3` with the fixed defaults |
| `stream_idle_timeout` | `60` | Read inactivity limit after a streaming response is handed to the caller |
| `stream_total_timeout` | `None` | Total stream lifetime after handoff |
| `cleanup_timeout` | `5` | Separate positive, finite budget for releasing resources |
| `limiter` | `None` | An application-provided `Limiter` or `AsyncLimiter` |
| `clock` | `Clock()`, the system clock | Client only: the time and jitter sources of every call, described in [Clocks and retry jitter](#clocks-and-retry-jitter) |

Omitted fields remain `UNSET` until resolution. Each field inherits in this order: request, `with_options` view,
client, generated default. `TimeoutOptions` merges each phase separately. For example, a client's
`TimeoutOptions(connect=3, read=5)` and a view's `TimeoutOptions(write=7)` resolve to connect/read/write/pool limits
of `3/5/7/5` seconds; a request that sets only `pool=2` changes them to `3/5/7/2`.

`timeout=None` clears all four phase limits; `TimeoutOptions(read=None)` clears just the read limit. Neither clears
the total budget. `total_timeout=None` clears the inherited relative limit, and `deadline=None` clears the inherited
absolute deadline. With both present, the earlier deadline applies. `stream_idle_timeout=None` and
`stream_total_timeout=None` clear only their respective stream limits. `cancel_token=None` and `limiter=None` remove
those inherited objects. `max_network_sends=None` removes the per-call send cap.

Durations must be finite and nonnegative, and send limits must be nonnegative integers; booleans are rejected.
`cleanup_timeout` must be strictly positive and cannot be `None`. Invalid values raise `ConfigurationError` with the
option's `field_path`, such as `("timeout", "read")`. `total_timeout=0` raises `DeadlineExceededError` before body
factories, limiter acquisition, or sending; hooks still receive `call_start` and `call_end` with zero sends.
`max_network_sends=0` raises `BudgetExceededError` before a send.

### A budget shared by every phase

`Deadline.after(10)` fixes the expiry when it is created. Reusing that object across calls shares the same expiry;
each call's `total_timeout` starts again at call entry. `Deadline.at` is the readonly monotonic timestamp on the
deadline's clock, `Deadline.clock`, and `remaining()` returns the seconds left on that clock, never a negative number.
Do not compare `at` with wall-clock timestamps.

```python
from pets import Client
from pets.options import Deadline, RequestOptions, TimeoutOptions


def read_with_budget(client: Client, url: str) -> bytes:
    deadline = Deadline.after(10)
    options = RequestOptions(total_timeout=20, deadline=deadline, timeout=TimeoutOptions(read=3))
    with client.with_options(options) as view:
        response = view.request_raw("GET", url, options=RequestOptions(timeout=TimeoutOptions(connect=2)))
        return response.read()
```

This call has at most the remaining portion of the ten-second absolute budget. Its connect and read phases also
have their own two- and three-second caps. A phase is clamped to the remaining total budget when that is smaller.

`PhaseTimeoutError`, a subclass of `TransportError`, means the phase's own cap expired. It carries `phase`,
`effective_timeout`, `delivery_state`, and the native timeout in `cause`. A cap supplied by the total deadline instead
raises `DeadlineExceededError`; equal caps favor the deadline. That error is separate from `TransportError` and carries
`deadline_at`, `elapsed`, `delivery_state`, and the interrupted activity in `phase`. Logical deadlines never retry.
Phase timeouts can be candidates under the safety, replay, and budget rules below; pool timeouts are excluded unless
`retry_on_pool_timeout=True` is explicit.

Synchronous total deadlines are cooperative: the client checks them around callbacks and encoding/decoding, and at
SDK send and chunk boundaries. A blocking callback, DNS resolution, or native socket operation can return after the
deadline; the client then raises `DeadlineExceededError` and starts no further network work. Native read caps are
latched when acquisition or body reading begins, so a sequence of reads or HTTP/2 stream processing can overrun a
total deadline. There is no background thread that forcibly interrupts a synchronous call.

Async clients require asyncio. Like `asyncio.timeout`, they stop a call at its deadline, explicit cancellation, or
client closing by cancelling the task that awaits it, and turn that cancellation into the matching SDK error. Native
task cancellation propagates as `asyncio.CancelledError`; it is never converted into an SDK error, even when a result
becomes ready at the same time or a callback suppresses or converts it. A hook, limiter, or body factory that
suppresses cancellation delays the stop until it returns; the call then ends at the SDK's next boundary.

### Explicit cancellation

`CancelToken.cancel()` is thread-safe and idempotent. Its readonly `cancelled` property records whether cancellation
has been requested; a cancelled token stays cancelled and can stop more than one call. Use a fresh token for later
work that should proceed.

```python
from pets import Client
from pets.errors import RequestCancelledError
from pets.options import CancelToken, RequestOptions


def cancel_before_send(client: Client, url: str) -> int:
    token = CancelToken()
    token.cancel()
    try:
        client.request_raw("GET", url, options=RequestOptions(cancel_token=token))
    except RequestCancelledError as error:
        return error.network_send_count
    raise RuntimeError("The cancelled call unexpectedly completed")
```

This example returns `0`. In a running application, another thread or an asyncio task may hold the same token and
call `cancel()`. Sync calls observe it when control returns to the SDK; async waits check it at intervals of at most
50 ms. Calls without a token perform no token polling. `RequestCancelledError` carries `source="cancel_token"` and
the request's `delivery_state`.

When outcomes race, native cancellation or `KeyboardInterrupt` takes precedence, followed by an observed token
cancellation, client closing, deadline expiry, and the operation result. `SystemExit` also propagates unchanged.
Cleanup preserves the selected error or native interruption.

### Streaming after handoff

A streaming call uses its ordinary call deadline while acquiring the response. After the context manager yields
the handle, `stream_idle_timeout` and `stream_total_timeout` apply. The completed acquisition's remaining time is
not carried into body reads. The ordinary 30-second read default does not apply to this stage; an explicitly
configured `TimeoutOptions(read=...)` adds a cap alongside the stream limits. The smallest active cap wins.

```python
from typing import BinaryIO

from pets import Client
from pets.options import RequestOptions


def download(client: Client, url: str, destination: BinaryIO) -> None:
    options = RequestOptions(total_timeout=10, stream_idle_timeout=60, stream_total_timeout=300)
    with client.with_streaming_response.request_raw("GET", url, options=options) as response:
        response.stream_to(destination)
```

Here acquisition has ten seconds, a stalled body read has sixty seconds, and the stream has five minutes after
handoff. Idle expiry raises a read `PhaseTimeoutError`; stream-total expiry raises `DeadlineExceededError` with
`phase="stream"`. Sync streams check total expiry at SDK chunk boundaries and retain the native read-latching limits
described above. Async streams apply the bounds to each asynchronous read. Always leave the response's context
manager, including when abandoning a download early.

`stream_to(path)` writes the decoded body to a new temporary file beside the target and moves it there only once the
body is complete. A handle whose body is already being read or is gone raises `ResponseConsumedError` before any file
work, and an existing target raises `FileExistsError` unless `overwrite=True`. A failure while the body streams closes
the response, removes the temporary file within `cleanup_timeout` (later, after the failure propagates, when removal
takes longer), and leaves an existing target unchanged.

An async handle does this file work on a disk thread of its own, started by the download and stopped when it ends, so
the event loop never blocks on the disk: it gathers the body into writes of about 64 KiB, each running while the next
bytes are read. Creating the file and waiting for a write count against `stream_total_timeout`, the cancel token, and
client closing, but not against `stream_idle_timeout` or `TimeoutOptions(read=...)`, which measure only the wait for
body bytes. Once the whole body has been read and written, the write of its last bytes under 64 KiB, the close, and
the move run to their end; only native task cancellation interrupts them, and the move may still complete after the
cancellation. Writing a saved body of a buffered response is not bounded by the call at all. Closing the client waits,
within `cleanup_timeout`, for a download's file work to finish.

`stream_to(file_object)` writes to a borrowed file on the calling thread or event loop and never closes, seeks, or
truncates it; bytes already written stay there. A failed write closes the response before the failure propagates.

### Clocks and retry jitter

`ClientOptions(clock=Clock(...))` replaces the time and jitter sources of every call a client makes. Views and requests
cannot change it. Each of the three sources is a function that takes no arguments:

| Source | Default | Read for |
|---|---|---|
| `monotonic` | `time.monotonic` | Deadlines, elapsed times, retry targets, token expiry, hook event durations, and protocol helper sessions, poll intervals, and stream deadlines |
| `time` | `time.time` | Placing a wall-clock instant on the monotonic scale once: an HTTP-date `Retry-After` or polling delay header at receipt and an access token's `expires_at`; a polling, stream, or upload helper's `resume` check of its state's `expires_at`; and a cache fetch's request, response, and age times |
| `random` | A secure uniform draw | The fraction in `[0, 1)` of a full-jitter backoff, drawn only when a retry needs one |

A source that cannot be called raises `ConfigurationError` with the `field_path` `("clock", name)`. OAuth providers
and flows keep their own time through `OAuthProviderOptions(clock=...)`, since one provider can serve several clients.
A client and the providers it uses must agree on wall time, because an access token's `expires_at` passes between them
as a UTC datetime. A helper's `resume` checks a token's expiry by its client's wall clock.

A deadline remembers its clock. `Deadline.after(seconds, clock=clock)` creates it on that clock, the system clock by
default, and `remaining()` reads that clock, so an adapter, limiter, or provider that receives it measures it
correctly. A call given a deadline made on another `Clock` object moves it onto its own clock by the time remaining at
call entry. A deadline made on the client's own `Clock` object stays the same object, so
`DeadlineExceededError.deadline_at` equals its `at`.

The client still waits in real time. A retry sleep or a wait before a poll ends once its clock reaches the target or
once as much real time has passed as the wait measured on its clock when it began, whichever comes first, so a frozen
clock still waits as long as the policy chose. An I/O timeout or an asyncio deadline timer lasts the time left that was
measured on the clock when it started. Closing a client and its cleanup limits use the system clock.

A test clock that should skip a wait advances itself, for example from a hook when a retry is scheduled:

```python
from pets import Client
from pets.hooks import CallEvent
from pets.options import ClientOptions, Clock, RetryOptions


class SteppedClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def on_event(self, event: CallEvent) -> None:
        if event.name == "retry_scheduled" and event.duration is not None:
            self.now += event.duration


def instant_retries(url: str) -> Client:
    stepped = SteppedClock()
    clock = Clock(monotonic=stepped, random=lambda: 0.5)
    return Client(options=ClientOptions(base_url=url, retry=RetryOptions(), hooks=(stepped,), clock=clock))
```

Each retry of this client is scheduled at half its backoff cap and starts at once, while its hooks and errors report
the delays the retry policy chose.

## Application concurrency limits

There is no default application limiter. Configure one on a client, view, or request through `limiter`. A sync
`Limiter.acquire(context)` returns a `Permit`; `AsyncLimiter.acquire(context)` is awaited and returns an `AsyncPermit`.
Their `release()` methods are respectively synchronous and asynchronous, and must be idempotent. A client refuses a
limiter of the opposite mode with `ConfigurationError` before I/O. The Protocols are exported from `pets.hooks`.

The immutable `LimiterContext` contains exactly `operation_id`, `origin`, `call_id`, `parent_session_id`,
`remaining_timeout`, and `cancel_token`. The origin has no path or query, and `remaining_timeout` is a snapshot taken
on entry. Contexts carry no credentials, headers, or bodies. A synchronous limiter must cooperate with these limits
when it blocks; the SDK cannot forcibly interrupt its callback. Async acquisition is bounded by the call itself.

This asyncio limiter shares four permits across all calls through its configured view:

```python
import asyncio

from pets import AsyncClient
from pets.hooks import AsyncLimiter, AsyncPermit, LimiterContext
from pets.options import RequestOptions


class SemaphorePermit:
    """Release one asyncio semaphore slot exactly once."""

    def __init__(self, semaphore: asyncio.Semaphore) -> None:
        self._semaphore = semaphore
        self._released = False

    async def release(self) -> None:
        if not self._released:
            self._released = True
            self._semaphore.release()


class SemaphoreLimiter:
    """Share one concurrency limit among calls made through the configured client or view."""

    def __init__(self, limit: int) -> None:
        self._semaphore = asyncio.Semaphore(limit)

    async def acquire(self, context: LimiterContext) -> AsyncPermit:
        await self._semaphore.acquire()
        return SemaphorePermit(self._semaphore)


async def read_limited(client: AsyncClient, urls: tuple[str, ...]) -> tuple[bytes, ...]:
    limiter: AsyncLimiter = SemaphoreLimiter(4)
    async with client.with_options(RequestOptions(limiter=limiter)) as view:

        async def read(url: str) -> bytes:
            response = await view.request_raw("GET", url)
            return await response.read()

        return tuple(await asyncio.gather(*(read(url) for url in urls)))
```

The SDK waits for a permit before opening the request body and retains it until the response is released. A streamed
response retains its permit until the handle closes; buffered responses release it before returning. Cancellation,
deadline expiry, hook failure, and an abandoned stream all release acquired permits, including a permit an async
callback returns after cancellation. The SDK releases permits but does not own the limiter itself.

Hooks observe `limiter_wait` followed by `limiter_acquired` when acquisition succeeds. Acquisition failure raises
`LimiterExecutionError(action="acquire")` with the callback exception as `cause`. Release failure is recorded as
`LimiterExecutionError(action="release")`: it is the `cause` of a `CleanupError` when no earlier error exists,
or a secondary error on the error already propagating.

### Counters and cleanup

Each logical call has one `call_id`. `resource_attempt_count` counts attempts at send time, `network_send_count`
counts adapter send invocations, and `network_send_budget_used` counts reserved send slots. Preparation and hook
failures before sending do not increment the attempt count. A reserved send slot is never refunded.

`ResponseInfo`, terminal call events, and `SDKError` expose snapshots of these counters, together with
`redirect_count`, `auth_exchange_count`, `auth_exchange_budget_used`, `auth_refresh_ids`, and `auth_refresh_pending`.
Errors expose counters even when no response arrived: their `info` remains `None` in that case. The readonly
`wire_send_count` field on errors and `ResponseInfo` is `None` when the adapter cannot prove wire sends. The
SDK-owned native adapter supplies this evidence; an injected transport's send invocations alone do not prove its
internal wire count. `BudgetExceededError` identifies the exhausted `budget_kind`, its `limit`, and its `used` slots.

Closing a client changes it to `CLOSING` immediately. New work is refused, and active calls or stream reads raise
`ClientClosedError` when the SDK observes closing. A view's close affects that view; closing the owning client
affects its views too. Already-buffered responses remain usable.

`cleanup_timeout` bounds the SDK's wait to release responses, body resources, permits, and owned work. It does not
extend the original call deadline or authorize another send. Cleanup failures are attached to an SDK error's
`secondary_errors`, or as safe notes on a native exception when that Python version supports notes. They do not
replace the primary failure. Unfinished cleanup stays owned and observed; a repeated `close()` or `aclose()` can wait
again. A close that exhausts its budget raises `CleanupError` with the unfinished counts. Blocking synchronous cleanup
has the same cooperative limits as other sync callbacks.

Cancelling an async file upload can return before its current disk operation finishes. The SDK retains that work,
keeps an open-file input claimed until it settles, and closes an owned handle once. Borrowed handles remain open.
`aclose()` includes this pending work in its cleanup wait.

## Retries and operation contracts

`RetryOptions` applies on clients, views, and calls. Its fields merge independently; omitted fields inherit, while a
status set replaces the inherited set. `retry=None` is invalid. Automatic retries require a candidate failure or
status, operation safety, replayable input, and enough time and send slots. JSON/model decoding, arbitrary callbacks,
body-factory programming errors, cancellation, and logical deadlines never restart a request.

| Field | Effective default | Meaning |
|---|---|---|
| `max_retries` | `2` | Retries after the initial attempt; `0` disables retries |
| `initial_delay`, `max_delay` | `0.5`, `8` seconds | Exponential backoff, with maximum at least the initial delay |
| `jitter` | `"full"` | Uniform delay below the exponential cap; `"none"` uses the cap |
| `statuses` | `{408, 429, 500, 502, 503, 504}` | Replace with an integer set in 400–599; 401/403/407 are forbidden |
| `max_retry_after` | `60` seconds | Positive cap on accepted server delay; `None` removes this cap |
| `respect_retry_after` | `True` | Explicit `False` ignores server delay hints |
| `retry_after_ms_header`, `should_retry_header` | Operation declaration, otherwise `None` | Vendor controls require generated metadata; explicit `None` disables one |
| `retry_on_pool_timeout` | `False` | Avoid amplifying pool contention unless explicitly enabled |

GET, HEAD, OPTIONS, PUT, and DELETE are eligible for retries by default. POST, PATCH, and other methods require an explicit
`retry_safety="idempotent"` declaration or a valid server key contract. Proven unsent connection failures from the
SDK-owned native transport can permit otherwise unsafe methods; a custom adapter's reported `NOT_SENT` alone
cannot establish that proof. `retry_safety="never"` prohibits every resend, including a proven unsent request.
TLS/certificate and configuration errors, permanent DNS failures, and unclassified failures are not candidates.

A server delay is a minimum: the client never shortens it to fit `max_retry_after` or the remaining deadline. A
valid server veto prevents a retry. Without a valid hint, bounded exponential backoff applies. Retry waits consume the
same logical budget as sending and decoding. `RetryOptions(respect_retry_after=False)` is an explicit application
policy override, disclosed in every generated README; it is never silently embedded in generated defaults.

```python
from pets import Client
from pets.options import RedirectOptions, RequestOptions, RetryOptions


def fetch_with_retries(client: Client, url: str) -> bytes:
    options = RequestOptions(
        retry=RetryOptions(max_retries=2, max_retry_after=20),
        redirects=RedirectOptions(enabled=True, max_redirects=2),
        total_timeout=30,
    )
    return client.request_raw("GET", url, options=options).read()
```

Buffered and streaming raw APIs return the final HTTP response when status retries end, including non-2xx statuses.
Transport, policy, deadline, cancellation, and budget failures still raise. A hook failure, or a response that cannot
be released within `cleanup_timeout`, stops a planned retry after its response was discarded: the call then raises
that response's status error with `retry_stop_reason="callback_failure"` and the failure among its secondary errors. Typed operations retain final response
metadata in their operation error. A streaming response can retry during acquisition; after handoff, body failures
terminate that stream and never issue another request.

### Declare API guarantees during generation

The generator entrypoint remains internal during development. This example uses the current internal generator;
there is no released client CLI or public client-generator facade yet. It assumes `api.yaml` declares
`POST /orders` and generates its model package alongside the client.

```python
from pathlib import Path

from datamodel_code_generator import GenerateConfig, OpenAPIScope
from datamodel_code_generator._api_generation import generate_target
from datamodel_code_generator._client.config import (
    ClientGenerationConfig,
    ClientOperationConfig,
    IdempotencyMetadata,
    RuntimeOperationMetadata,
)
from datamodel_code_generator._client.target import ClientTarget
from datamodel_code_generator.format import Formatter


generate_target(
    Path("api.yaml"),
    model_config=GenerateConfig(
        output=Path("build/order_models.py"),
        input_file_type="openapi",
        openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
        formatters=[Formatter.BUILTIN],
    ),
    config=ClientGenerationConfig(
        output=Path("build/orders"),
        package="orders",
        model_package="order_models",
        operations=(
            ClientOperationConfig(
                ref="/paths/~1orders/post",
                runtime=RuntimeOperationMetadata(
                    idempotency=IdempotencyMetadata(
                        header_name="Idempotency-Key",
                    ),
                    retry_after_ms_header="X-Retry-In-Ms",
                    should_retry_header="X-Retry-Permitted",
                ),
            ),
        ),
    ),
    generator=ClientTarget(),
)
```

The corresponding flat target-file entry is:

```toml
[[operations]]
ref = "/paths/~1orders/post"

[operations.runtime]
retry_safety = "method_default"
retry_after_ms_header = "X-Retry-In-Ms"
should_retry_header = "X-Retry-Permitted"

[operations.runtime.idempotency]
header_name = "Idempotency-Key"
```

Only `header_name` is required for idempotency. The key header must be an HTTP token and cannot share
an outgoing position with an effective parameter or authentication header. The two response control headers must
have distinct names. Header comparisons ignore ASCII case; outgoing and incoming positions are independent.
Unknown TOML keys receive `E_CONFIG_UNKNOWN`; malformed values receive `E_CONFIG_VALUE`, and ownership conflicts
receive `E_CONFIG_CONFLICT`. The generated README lists only the finalized selected operations and their contracts.

### Supply and retain an idempotency key

`IdempotencyMetadata(header_name=...)` declares the API's idempotency key header. Supply a caller key with
`IdempotencyKey("saved-value")`, or create a UUID4 value with `IdempotencyKey.new()`. The value is omitted from its
representation.

With `idempotency_key=UNSET`, the client creates one key per logical call for an operation with a declared header.
`idempotency_key=None` disables automatic creation and clears an inherited key. An effective caller key on an
undeclared operation or `request_raw` fails before sending, including keys inherited from a client or view.
The same key is sent unchanged on every eligible retry. A declaration without an active key does not authorize
unsafe retries. The contract does not guarantee exactly-once business execution.

Keys must be nonempty, UTF-8-encodable strings. ASCII control characters are rejected except for interior tabs;
leading or trailing ASCII spaces and tabs are also rejected. Interior spaces, tabs, and valid Unicode are preserved
without trimming or normalization. Invalid keys raise `ConfigurationError` when constructed, before any send.

```python
from pets import Client
from pets.options import IdempotencyKey, RequestOptions


def keyed_view(client: Client, value: str) -> Client:
    key = IdempotencyKey(value)
    return client.with_options(RequestOptions(idempotency_key=key))
```

Use the returned view in a `with` block and call an operation with the matching declaration. The client reuses
the supplied value across eligible retries.

## Request compression

### Declare accepted codings

`RuntimeOperationMetadata.accepted_content_encodings: tuple[str, ...] = ()` lists the request content codings an
operation accepts. Values are HTTP tokens compared in lowercase, without duplicates; `gzip` is the only coding with a
builtin encoder, so another value, including `identity`, receives `E_CONFIG_VALUE`, and a repeated one
`E_CONFIG_CONFLICT`. With the internal generator entry point shown in
[Declare API guarantees during generation](#declare-api-guarantees-during-generation):

```python
ClientOperationConfig(
    ref="/paths/~1items/post",
    runtime=RuntimeOperationMetadata(accepted_content_encodings=("gzip",)),
)
```

The flat target-file entry is:

```toml
[[operations]]
ref = "/paths/~1items/post"

[operations.runtime]
accepted_content_encodings = ["gzip"]
```

The generated README lists the operations that accept a coding.

### Select a coding

`ClientOptions(compression="gzip")` is the default. The SDK gzips only bodies of operations that declare gzip;
`ClientOptions(compression=None)` disables it. Views and calls inherit the client setting. Undeclared operations,
bodyless requests, raw requests, and token requests stay uncompressed. A Content-Encoding header conflicts only
when the SDK compresses the body. The gzip encoder uses level 6 and a zero modification time.

Bytes and encoded bodies are compressed once and every retry resends the same bytes. File, stream, factory, and
multipart bodies are compressed as each attempt streams, without a Content-Length, and replay exactly as they would
uncompressed; a one-shot body stays one-shot. A signer that needs a body digest digests the compressed bytes, so it
accepts only bodies encoded once. A redirect that drops the body also drops Content-Encoding.

Each protocol helper request follows its own operation's declaration and the client setting. Bodyless polls and
followed URLs stay uncompressed. Token requests are never compressed.


## Body replay and resource ownership

Immutable bytes and JSON encoding results are retained and reused without rerunning serialization for each attempt.
The JSON encoding allocation scales with the call's input size independently of response-byte limits. Multipart
fixes its boundary once per logical call and can replay only if every part can replay.

`FileBody(file)` records the current offset at call entry and seeks back there for each attempt when possible.
Borrowed files stay open and their final position is not restored. Concurrent reads of one borrowed file by different
calls are rejected. `FileBody.from_path(path)` and `AsyncFileBody.from_path(path)` reopen for each attempt and compare
device, inode, size, and modification time; identical stat data does not guarantee identical bytes. The caller must
keep input immutable. Explicit async file adapters own one worker with at most one disk chunk in flight; their
caller closes them when borrowed. File and multipart reads use chunks of at most 64 KiB.

```python
from pathlib import Path

from pets import Client
from pets.bodies import FileBody
from pets.options import RequestOptions, RetryOptions


def upload_file(client: Client, url: str, path: Path) -> bytes:
    response = client.request_raw(
        "PUT", url, body=FileBody.from_path(path), options=RequestOptions(retry=RetryOptions(max_retries=2))
    )
    return response.read()
```

`StreamBody` and `AsyncStreamBody` are one-shot. After consumption they cannot replay, and the SDK does not buffer or
spool them to create replayability. Owned iterators are closed; borrowed iterators remain caller-owned.

`BodyFactory` and `AsyncBodyFactory` must return a fresh `BodyAttempt` or `AsyncBodyAttempt` with the same payload for
every invocation. Each returned attempt is SDK-owned and closes on success, failure, or interruption. A factory is
responsible for freshness across all calls: the detection ledger covers a logical call and an immediate cross-call
guard, rather than indefinite object history. Length/fingerprint/stat checks detect available evidence of changes
without buffering the whole payload; a detected change raises `BodyChangedError`. Factory callback failures do not
retry. An already-used attempt raises `BodyNotReplayableError`.

## Redirects and transport construction

Redirects are disabled by default. `RedirectOptions` merges per field, with `enabled=False`, `max_redirects=5`,
`allow_303_to_get=False`, `allowed_origins=()`, and `allow_https_downgrade=False`. An empty origin allowlist permits
only the original origin. An allowed destination and permission to downgrade HTTPS are separate conditions.

301/302 follow only GET/HEAD. 303 changes to GET for GET/HEAD or explicit `allow_303_to_get=True`; it removes the body
and content/framing headers, including Content-Encoding. 307/308 retain the method/body and require replayability and
operation safety. Every hop consumes a send slot and the same call deadline. Credentials and cookies from the original
request are stripped across origins, and so are the headers and query fields the package's security schemes name,
however the request came to carry them. Repeated method/URL loops, invalid or multiple Location values, forbidden
destinations, exhausted redirect limits, or unsafe replay raise `RedirectPolicyError`. It directly inherits `SDKError`
and exposes readonly `delivery_state`, `body_available=False`, and available response metadata. Native failure while
constructing a redirect also raises this error if no response handle can be returned.

A HEAD operation also follows a 303 as GET, regardless of `allow_303_to_get`. Its typed return contract remains
bodyless: ordinary and `with_response` calls return `None` data for an empty final body and raise
`BodyProtocolError(condition="forbidden_body")` if the GET returns content. Use `with_raw_response` or
`with_streaming_response` to access that final GET body without typed decoding.

`TransportOptions` belongs only to `ClientOptions`; it cannot be set on a view or request. Its effective defaults are
`verify=True`, `ssl_context=None`, `proxy=None`, `trust_env=True`, `http2=False`, `max_connections=100`,
`max_keepalive_connections=20`, `keepalive_expiry=5`, and `retry_owner="sdk"`. Supplying an SSLContext uses its CA,
verification, and client certificate settings and rejects any explicit `verify` override. Proxy/environment/TLS
settings follow HTTPX2 environment handling by default, preserving native system trust. With `trust_env=False`,
HTTPX2 environment configuration is disabled while native TLS trust behavior is preserved. Caller `ssl_context` or
`verify=False` controls origin TLS; injected native clients retain their settings and ownership.
Typed error handling and exception body prefixes use `max_error_body_bytes`, which defaults to 64 KiB.
Buffered raw responses of every status use `max_response_bytes`, which defaults to `None` (no cap). Set it on client,
view, or request options to bound them; `None` removes an inherited cap. For buffered raw responses, only
`raise_for_status()` applies the error-prefix cap. HTTP/2 is opt-in and requires its optional dependency.

```python
from ssl import create_default_context

from pets import Client
from pets.options import ClientOptions, TransportOptions


def configured_client(ca_file: str) -> Client:
    context = create_default_context(cafile=ca_file)
    transport = TransportOptions(ssl_context=context, max_connections=50, max_keepalive_connections=10)
    return Client(options=ClientOptions(transport=transport))
```

Use the returned client in a `with` block. The supplied CA bundle controls TLS verification; this example leaves
verification enabled. Omitting `trust_env` keeps its default of `True`, so native environment proxy settings remain
effective. Set `trust_env=False` on `TransportOptions` to opt out explicitly.

An injected native client's pool/proxy/TLS settings remain its own, and incompatible SDK construction settings are
rejected. Borrowed clients and adapters are not closed; `OwnedTransportAdapter` transfers adapter ownership.
The default native transport has no internal retries. `retry_owner="transport"` requires an explicitly injected
adapter with the internal retry-count, deadline, and body-safety contract and disables SDK retry decisions.
`network_send_count` still counts adapter invocations; only trusted evidence permits a non-None `wire_send_count`.

## Explicit authentication and request signing

Generated clients compile root security inheritance and operation overrides from OpenAPI. An AND requirement needs
all its schemes; an OR list selects its first fully available alternative, or the index given by `AuthConfig.selection`.
The index applies only to operations that declare more than one alternative, so one client-wide configuration also
serves anonymous and single-requirement operations. Selection stays fixed for the logical call, including retries and
401 recovery. Required credentials that are missing,
unknown, unavailable, or of the wrong material kind fail before a provider callback or send. Unused unsupported or
unresolved scheme declarations do not prevent generation, but configuring one does not make it usable.

The generated `auth` module exports `AuthConfig`, provider Protocols, static/environment providers, and signer
Protocols, with the credential values and providers of the schemes the API declares: `ApiKeyCredential` for an API key,
`BasicCredential` for HTTP basic, `BearerCredential`, `AccessToken`, and the token providers for a bearer, OAuth 2, or
OpenID Connect scheme, the client credentials providers for an OAuth 2 `clientCredentials` flow, and the refresh-token
providers for its other flows and for OpenID Connect. `ClientOptions.auth` and `RequestOptions.auth` default to `UNSET`; an `AuthConfig` replaces the
inherited configuration as a whole and preserves provider identity. `auth=None` disables inherited credentials and
signers; it cannot make a required operation anonymous. No environment variables or credentials are discovered by default.

These examples assume a generated API with a `bearer` scheme, a `header_key` scheme, and an `auth` resource containing
`bearer`, `api_key_header`, and `signed_body` operations. The calling application supplies tokens, keys, and clients.

```python
from pets import Client
from pets.auth import AccessToken, AuthConfig, StaticTokenProvider
from pets.options import RequestOptions


def authenticated_get(client: Client, token: str) -> bytes:
    provider = StaticTokenProvider(AccessToken(token, scopes=None))
    with client.with_options(RequestOptions(auth=AuthConfig({"bearer": provider}))) as view:
        return view.auth.bearer()
```

`AccessToken.scopes` records token metadata: `None` means unknown grants and `()` means known empty grants.
Known grants and requested scopes are deduplicated and ASCII-sorted. The client sends empty, narrow, hierarchical,
and unknown grants to the permitted resource origin and leaves authorization to the server. A 403 is terminal: the
SDK never broadens scopes or refreshes a token to recover from it. Expired material raises `TokenExpiredError` and cannot justify an endless refresh.
`BearerCredential` pairs a token with an opaque `TokenVersion()` whose identity belongs to that provider's publication.

Async clients require async providers. Matching static and environment variants are available; a custom provider's
async `get` is awaited once. There is no implicit thread offload or sync/async conversion.

```python
from pets import AsyncClient
from pets.auth import AccessToken, AsyncStaticTokenProvider, AuthConfig
from pets.options import RequestOptions


def async_credentials(token: str) -> AuthConfig:
    return AuthConfig({"bearer": AsyncStaticTokenProvider(AccessToken(token))})


async def authenticated_get_async(client: AsyncClient, token: str) -> bytes:
    async with client.with_options(RequestOptions(auth=async_credentials(token))) as view:
        return await view.auth.bearer()
```

`ApiKeyCredential(value)` uses its declared header, query, or cookie name. `BasicCredential(username, password)` uses
UTF-8 Basic encoding. HTTP bearer, OAuth2, and OpenID Connect declarations accept explicitly supplied bearer material;
this layer neither discovers endpoints nor starts an OAuth flow. `CredentialContext` carries the selected scheme,
canonical requirements, current resource origin, deadline, and cancellation token. Its audience is currently `None`.

An environment provider reads only when selected and called. It rereads its named variable for each `get`; construction
and package imports do not read the environment. Choose `kind="bearer"` for an environment token.

```python
from pets import Client
from pets.auth import AuthConfig, EnvironmentCredentialProvider
from pets.options import RequestOptions


def environment_get(client: Client, variable_name: str) -> bytes:
    provider = EnvironmentCredentialProvider(variable_name, kind="api_key")
    with client.with_options(RequestOptions(auth=AuthConfig({"header_key": provider}))) as view:
        return view.auth.api_key_header()
```

Custom providers implement the public structural Protocol. Callback failures retain their cause in
`AuthProviderExecutionError` and are not transport-retried. The callback must cooperate with its supplied deadline
and cancellation signal; a synchronous callback cannot be forcibly terminated at the deadline.

```python
from collections.abc import Callable

from pets.auth import ApiKeyCredential, CredentialContext


class ApplicationKeyProvider:
    def __init__(self, resolve: Callable[[], str]) -> None:
        self._resolve = resolve

    def get(self, context: CredentialContext) -> ApiKeyCredential:
        return ApiKeyCredential(self._resolve())
```

Providers are borrowed by default. `OwnedCredentialProvider(provider)` explicitly transfers a closeable provider to
the root client's scope; the generic wrapper preserves the concrete provider type. Root close drains active calls and
closes each owned identity once, independently of transport ownership. Closing a `with_options` view does not close
shared providers. Retained asynchronous cleanup can outlive a cleanup timeout and is drained by a later close.
Never wrap the same stateful provider in independent roots unless its own lifecycle supports that arrangement.

### Anonymous calls, origins, and recovery

Absent security, explicit `[]`, and an empty AND alternative are anonymous choices. Configuring credentials alone does
not send them on those operations or on `request_raw`. Sending selected credentials requires both
`send_on_anonymous=True` and explicit `anonymous_schemes=("bearer",)` (or the applicable declared names). Signer-only
anonymous calls also require `send_on_anonymous=True`. A true opt-in with neither a selected credential nor a signer
is invalid. Ordered mixed anonymous/authenticated alternatives retain their declared selection indices.

An empty authentication `allowed_origins` permits only the selected server's origin. An origin includes scheme, host,
and effective port. Arbitrary `request_raw` destinations require explicit allowed origins as well as anonymous opt-in.
Authentication and redirect allowlists are independent: permitting a redirect does not permit sending credentials
there. Each hop rebuilds credentials and signatures for its current destination; managed fields from an earlier hop
are never carried across origins. Generic header/query patches cannot override or delete credential/signature owners.

A static provider does not refresh. A custom refresh provider explicitly implements `get`, `invalidate(version)`, and
`refresh(context)`; the async Protocol makes all three methods async. At most one eligible 401 recovery invalidates the
exact used token version and refreshes without switching the selected OR alternative. It requires an appropriate
Bearer invalid-token challenge, or the explicit operation declaration below, and still obeys retry safety, body
replayability, retry count, send slots, and the original deadline. `max_retries=0` prevents that resend, while initial
credential acquisition is still allowed. These callbacks add no implicit token HTTP traffic.

Use the existing internal generation API to declare an API-specific challenge-less 401 contract. It is false unless
explicitly set and is recorded under `operations[].runtime.auth_challenge_less_401` in generated documentation.
It allows token-expired recovery after a 401 without a challenge, subject to the retry rules above.
It is not a client option or an inferred response behavior.

```python
from datamodel_code_generator._client.config import ClientOperationConfig, RuntimeOperationMetadata

operation = ClientOperationConfig(
    ref="/paths/~1challenge-less/get",
    runtime=RuntimeOperationMetadata(auth_challenge_less_401=True),
)
```

The equivalent field in an existing target TOML file is:

```toml
[[operations]]
ref = "/paths/~1challenge-less/get"

[operations.runtime]
auth_challenge_less_401 = true
```

### Sign the finalized request

Signers declare their allowed origins, managed header/query names, and whether they require a SHA-256 body digest.
They run in tuple order after credential placement and final body framing/content type, before the readonly attempt
hook and send. `SigningInput.query` is the exact raw query bytes; its headers and body digest describe that hop's
unsigned request. A signer returns only `SignatureFields` for names it declared. Overlapping owners fail before
callbacks or sends; arbitrary signer exceptions raise `SigningExecutionError` and do not retry.

```python
import hmac

from pets import Client
from pets.auth import AuthConfig, SignatureFields, SignerCapabilities, SigningInput
from pets.options import RequestOptions


class PayloadSigner:
    def __init__(self, key: bytes, origin: str) -> None:
        self._key = key
        self._capabilities = SignerCapabilities(
            allowed_origins=(origin,),
            managed_headers=("X-Payload-Signature",),
            managed_query=(),
            requires_body_digest=True,
        )

    @property
    def capabilities(self) -> SignerCapabilities:
        return self._capabilities

    def sign(self, request: SigningInput) -> SignatureFields:
        assert request.body_digest is not None
        message = request.method.encode("ascii") + b"\n" + request.url.encode("utf-8") + b"\n" + request.body_digest
        signature = hmac.digest(self._key, message, "sha256").hex()
        return SignatureFields(headers=(("X-Payload-Signature", signature),), query=())


def signed_upload(client: Client, origin: str, key: bytes, payload: bytes) -> bytes:
    auth = AuthConfig({}, allowed_origins=(origin,), send_on_anonymous=True, signers=(PayloadSigner(key, origin),))
    with client.with_options(RequestOptions(auth=auth)) as view:
        return view.auth.signed_body(body=payload)
```

This example defines its own canonical input; a service's signature protocol must define the same bytes. Signatures
are rebuilt for every attempt and redirect hop. Unsigned calls do not hash bodies or invoke signer/provider callbacks.
Hashing bytes, files, or complete seekable multipart input costs time proportional to payload size and consumes the
call deadline. Seekable sources are restored to their original position after hashing, and reads remain chunked.

`BodyFactory(..., sha256=digest)` and `AsyncBodyFactory(..., sha256=digest)` accept an optional 32-byte declaration for
the exact whole payload. A digest-declaring factory is not read to compute the digest; its declaration and replay
identity remain the application's obligations. A one-shot stream or digest-less factory cannot satisfy a signer
that requires a digest and fails before sending, without implicit spooling. A file-part factory's digest is not the
multipart payload's digest: factory-containing multipart is rejected for digest-required signing, while it remains
supported without such a signer. No multipart digest field is added.

Credential values, signing inputs, and returned signature values are omitted from their representations and from
hook events. A credential or signature placed in the query is part of the request URL, which HTTPX2 logs at INFO level
on its `httpx2` logger, so keep that logger above INFO wherever URLs must stay private. Errors retain safe metadata,
common send/attempt counters, and causes without automatically formatting secret-bearing callback messages.
Applications must apply their own policy before explicitly inspecting those causes.

### Acquire client credentials

`ClientCredentialsProvider` and `AsyncClientCredentialsProvider` implement the OAuth client credentials grant for a
confidential client acting on its own behalf. Each is a refreshable token provider for an OAuth scheme of `AuthConfig`:
the first call that needs a token acquires it, later calls share it, and the call that finds a tenth of its lifetime,
at most thirty seconds, left acquires a new one. A token without `expires_in` is kept until a resource rejects it, when
401 recovery invalidates that exact version and acquires another.

```python
from pets import Client
from pets.auth import AuthConfig, ClientCredentialsProvider, CredentialProvider, OwnedCredentialProvider
from pets.options import ClientOptions


def service_client(secret: CredentialProvider) -> Client:
    provider = ClientCredentialsProvider(
        "https://auth.example.com/token",
        client_id="pets-service",
        client_secret=secret,
        scopes=("pets.read",),
    )
    return Client(options=ClientOptions(auth=AuthConfig({"oauth": OwnedCredentialProvider(provider)})))
```

The token request sends the configured `scopes` in canonical order, and `audience` when one is configured. The client
authenticates with `client_secret_basic` (the default) or `client_secret_post`; `none` is refused, since a public
client has no credentials of its own. The configured scopes become the token's scopes when the response omits its
`scope` member, and a caller whose context requires another audience than the configured one fails with
`AuthConfigurationError` before any request.

The call that needs a token requests it inline, while holding the provider's lock: a `threading.Lock`, or an
`asyncio.Lock` for the async provider. Concurrent callers wait for that lock, a synchronous one no longer than its
deadline, and then use the token it obtained, so one token request serves them all; the provider starts no thread or
task of its own. A token request ends within `refresh_timeout`, and at the calling request's deadline at the latest;
cancelling an asyncio caller cancels its token request. A failed request leaves nothing behind, and the next call
acquires again. When an early renewal fails, the call keeps using the current token until it expires; a failure raises
once the token expired, or when a resource's rejection forced the request. A rejection, an unexpected status, or an
unusable response raises `OAuthExchangeError`; a transport failure raises `OAuthExchangeError`, or `AuthTimeoutError`
when a time limit ran out, with the `delivery_state` the request reached.

`get` returns the current token, acquiring one when none is usable; `refresh` acquires a new one, unless another caller
replaced the token while this one waited for the lock; `invalidate(version)` forgets the held token only if it is that
version. Closing the provider closes an owned token transport, and later calls raise `AuthProviderClosedError`; a client
closes a provider it owns through `OwnedCredentialProvider`. The async provider belongs to the event loop it was
created on or first used from.

`client_secret_basic` (the default) and `client_secret_post` take a `client_secret` provider returning an
`ApiKeyCredential` of visible ASCII; it is called once per token request with a context naming the token endpoint's
origin and the scheme `oauth_client_secret`. The token endpoint must be HTTPS; plain HTTP to a loopback host requires
`OAuthProviderOptions(allow_insecure_loopback=True)`.

Token requests use a transport the provider owns, separate from every client: an HTTPX2 client created at the first
token request from `OAuthProviderOptions.transport`, or an adapter passed as `token_transport`, which is borrowed unless
wrapped in `OwnedTransportAdapter`. Its settings must verify certificates and host names and leave retries to the SDK,
and an injected adapter must declare `internal_retry_limit=0`. Token requests never follow redirects or retry, and
closing the provider, or leaving its `with` block, closes an owned transport. `refresh_timeout` (30 seconds by default)
bounds each token request, the client secret lookup included; `phase_timeout` caps connecting, reading, writing, and
pool waits at 5, 15, 15, and 5 seconds unless overridden. Responses are read up to 64 KiB and must be UTF-8 JSON
objects. Only a transport that declares delivery evidence can prove that a failed request was never sent. Errors keep
no response body or error description, but a transport failure's cause is the native exception, which can hold the
token request and its client authentication, so apply your own policy before logging causes.

### Refresh a token set

`RefreshTokenProvider` and `AsyncRefreshTokenProvider` keep an access token current with the OAuth refresh token grant,
starting from the `TokenSet` an authorization produced. The provider alone refreshes it: no other provider, process,
or event loop may send the same refresh token. It serves the access token while it lasts, and the call that finds a
tenth of its lifetime, at most thirty seconds, left, or that follows a resource's rejection, refreshes it inline under
the provider's lock, as the client credentials provider acquires.

```python
from pets import Client
from pets.auth import AuthConfig, OwnedCredentialProvider, RefreshTokenProvider, TokenSet
from pets.options import ClientOptions


def save(tokens: TokenSet) -> None: ...


def user_client(tokens: TokenSet) -> Client:
    provider = RefreshTokenProvider(
        "https://auth.example.com/token",
        client_id="pets-app",
        token_set=tokens,
        on_token_refreshed=save,
        client_auth_method="none",
    )
    return Client(options=ClientOptions(auth=AuthConfig({"oauth": OwnedCredentialProvider(provider)})))
```

The refresh request sends the refresh token with the client authentication, never the configured `scopes` or `audience`:
`none` sends the `client_id` of a public client, and `client_secret_basic` (the default) and `client_secret_post` need a
`client_secret`. A caller whose context requires another audience than the configured one fails with
`AuthConfigurationError`, and so does a token set whose access token names another audience. A token set needs the
Bearer type, and a timezone-aware `expires_at` when its access token expires.

Each successful refresh makes a new `TokenSet` current. The response's refresh token replaces the one sent, and a
response without one keeps it; a response without `scope` keeps the grants of the access token it replaces, known or
unknown. Narrower refreshed grants remain token metadata: subsequent resource calls reach the server, and its 403
remains terminal. The provider then passes the new token set to `on_token_refreshed`, a coroutine function for the async
provider, while still holding its lock, so an application can persist each token set in order; the SDK itself
persists nothing. An exception the callback raises reaches the caller, and the refreshed token set stays current, as it
does when an asyncio caller is cancelled while the callback runs, which may then not finish. The callback must not call
its own provider, which holds its lock until the callback returns.

`invalid_grant` raises `AuthReauthorizationRequiredError`, and the application starts a new authorization. Any other
failure raises as for client credentials and leaves the token set as it was, so the next call sends the same refresh
token again. A token set without a refresh token serves its access token until it expires or a resource rejects it, and
then raises `AuthReauthorizationRequiredError` without a request, as a forced `refresh` does at once. Token requests,
client authentication, and closing otherwise follow the client credentials provider.

Basic charset overrides, resource audience metadata, and generated OAuth provider factories are not available yet;
applications construct providers explicitly.

## Webhook verification helpers

An enabled `webhook` helper generates the module `pkg.webhooks.<name>`, where each dotted part of the helper's name is a
package or the module. It verifies one received delivery, with a builtin `standard_webhooks`, `stripe_style`, `body_hmac`, `ed25519`, or
`rsa-pss-sha256` signature or through your verifier for an [`adapter` signature](#adapter-signatures), and decodes its
JSON event; a helper whose signature is [`none`](#unsigned-webhooks) only decodes. It creates no client, server, or
route. The key types of builtin signatures are in `pkg.webhooks.keys`, which exists only when a helper has a builtin
signature and whose name a helper cannot take, and a package exists only when at least one webhook helper is enabled.
With the package `pets` and the `standard.message` helper below:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.usage -->
<!-- fmt: off -->

```python
def receive(raw_body: bytes, headers: list[tuple[str, str]], secret: bytes) -> Message:
    """Verify one delivery with the active key, return its event."""
    keys = KeySet(keys=(HmacKey(id="2026-09", secret=secret),))
    verified = message.verify(raw_body, headers, keys, now=datetime.now(timezone.utc))
    return verified.data
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.usage -->

The generated functions take the delivery and the keys positionally and everything else by keyword:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.helper -->
<!-- fmt: off -->

```python
def verify(
    raw_body: bytes,
    headers: Sequence[tuple[str, str]],
    keys: KeySet[HmacKey],
    *,
    now: datetime,
    options: WebhookOptions | None = None,
) -> VerifiedWebhook[_dcg_type_0]:
    """Verify a delivery and decode its event.

    Deliveries outside the timestamp window are rejected. Verification retains no
    delivery state; deduplicate in your application using delivery_id when present.
    """
    return verify_webhook(_PLAN, raw_body, headers, keys, now=now, options=options)
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.helper -->

`verify(raw_body, headers, keys, *, now, options=None)` returns `VerifiedWebhook[Event]`, where `Event` is the
event schema's model type. `async def verify_async(...)` takes the same arguments and returns the same facts.
`raw_body` is the exact received `bytes`, `headers` a list or tuple of `(name, value)` string pairs in received order,
and `now` an aware datetime. `HmacKey(*, id: str, secret: bytes)` passes the secret to HMAC unchanged; the id is a
nonempty string without NUL, CR, or LF, the secret must be `bytes`, and its representation names the id only. Keys
cannot be changed, copied into new objects, or pickled.

### Public-key signatures

An `ed25519` helper takes `KeySet[Ed25519Key]` and an `rsa-pss-sha256` helper `KeySet[RSAPSSKey]`, where
`Ed25519Key(*, id: str, public_key: Ed25519PublicKey)` and `RSAPSSKey(*, id: str, public_key: RSAPublicKey)` wrap the
public key classes of [cryptography](https://cryptography.io/). `pkg.webhooks.keys` exports only the key types of the
selected signature kinds, and only these key types and their helpers import cryptography, so `pkg`, `pkg.protocols`,
and HMAC helpers import without it. A package with such a helper lists `cryptography>=50.0.0` among its dependencies,
and generation prints it in the `uv add` hint. Load the key with cryptography yourself, for example:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.public-keys -->
<!-- fmt: off -->

```python
def keys(raw_key: bytes, modulus: int) -> tuple[KeySet[Ed25519Key], KeySet[RSAPSSKey]]:
    """Wrap an Ed25519 public key from its raw bytes and an RSA public key from its numbers."""
    ed25519_key = Ed25519Key(id="active", public_key=Ed25519PublicKey.from_public_bytes(raw_key))
    rsa_key = RSAPSSKey(id="rsa-2048", public_key=RSAPublicNumbers(65537, modulus).public_key())
    return KeySet(keys=(ed25519_key,)), KeySet(keys=(rsa_key,))
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.public-keys -->

A key keeps the public key object it was given and uses it only to verify: it is never copied, serialized, compared, or
shown, and a private key, `bytes`, PEM or DER text, or a key of the other kind raises `TypeError` naming only the
expected class. The key's own class must derive from the cryptography class, so an object that only claims that class is
refused, while a class registered with it is accepted: the application supplies its keys. The SDK never derives a public
key from a private one. An Ed25519 signature is the raw 64-byte signature, and a decoded signature of another length is
`malformed_signature`. RSA-PSS uses SHA-256, MGF1 with SHA-256, and a 32-byte salt; each key decides its signature size,
so keys of different sizes can share a key set during rotation, and a signature of the wrong size for a key is
`invalid_signature` for that key. Only cryptography's `InvalidSignature` moves on to the next signature or key;
`UnsupportedAlgorithm` raises `ProtocolConfigurationError` with `condition="wrong_capability"` and the key's
`("keys", "<index>")`, and any other error, including cancellation, propagates unchanged. The signature settings are the
same as for HMAC:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.public-keys-yaml -->
<!-- fmt: off -->

```yaml
schema_version: 1
helpers:
  rfc8032.ed25519:
    kind: webhook
    event_schema:
      pointer: /components/schemas/Push
    signature:
      kind: ed25519
      header: X-Signature
      encoding: hex
      prefix: ''
  wycheproof.rsa:
    kind: webhook
    event_schema:
      pointer: /components/schemas/Count
    signature:
      kind: rsa-pss-sha256
      header: X-Signature
      encoding: hex
      prefix: ''
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.public-keys-yaml -->

### Signature settings

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.yaml -->
<!-- fmt: off -->

```yaml
schema_version: 1
helpers:
  standard.message:
    kind: webhook
    event_schema:
      pointer: /components/schemas/Message
    signature:
      kind: standard_webhooks
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.yaml -->

| Signature kind | Authenticated bytes and configuration |
|---|---|
| `standard_webhooks` | `webhook-id.webhook-timestamp.body`; fixed HMAC-SHA256, `webhook-signature` with space-separated `v1,<base64>` signatures |
| `stripe_style` | `timestamp.body`; one decimal `t` and hex `v1` candidates in a comma-separated header, default `Stripe-Signature` |
| `body_hmac` | Raw body; required `header`, `algorithm` (`hmac-sha256` or `hmac-sha512`, default SHA256), `encoding` (`hex` or `base64`, default hex), and optional `prefix` (default empty) |
| `ed25519`, `rsa-pss-sha256` | Raw body; required `header`, optional `encoding` (`hex`, `base64`, or `base64url`, default hex) and `prefix` (default empty) |

Standard Webhooks returns an authenticated delivery id and timestamp. Stripe-style signatures return the timestamp
and no delivery id. Body HMAC and public-key signatures return neither fact. Other signature formats use an adapter.
Each helper's `event_schema` selects a webhook or callback JSON request-body model, or an [event mapping](#event-mappings).
Header names are case insensitive; prefixes match exactly. Hex accepts either case, base64 requires padding, and
public-key base64url allows optional padding.

Invalid configuration is rejected during generation:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.diagnostics -->
<!-- fmt: off -->

```text
E_CONFIG_VALUE config protocols.helpers['shape.missing'].signature: protocols.helpers['shape.missing'] needs 'signature'
E_CONFIG_VALUE config protocols.helpers['shape.values'].signature: protocols.helpers['shape.values'].signature must be a signature
E_CONFIG_VALUE config protocols.helpers['shape.kind'].signature.kind: protocols.helpers['shape.kind'].signature.kind must be 'standard_webhooks', 'stripe_style', 'body_hmac', 'ed25519', 'rsa-pss-sha256', 'adapter' or 'none'
E_CONFIG_VALUE config protocols.helpers['shape.ed25519'].signature.header: protocols.helpers['shape.ed25519'].signature needs 'header'
E_CONFIG_VALUE config protocols.helpers['shape.adapter'].signature.timestamp: protocols.helpers['shape.adapter'].signature needs 'timestamp'
E_CONFIG_VALUE config protocols.helpers['shape.adapter'].signature.delivery_id: protocols.helpers['shape.adapter'].signature needs 'delivery_id'
E_CONFIG_VALUE config protocols.helpers['shape.adapter_facts'].signature.timestamp: protocols.helpers['shape.adapter_facts'].signature.timestamp must be 'required' or 'none'
E_CONFIG_VALUE config protocols.helpers['shape.adapter_facts'].signature.delivery_id: protocols.helpers['shape.adapter_facts'].signature.delivery_id must be 'required' or 'none'
E_CONFIG_VALUE config protocols.helpers['shape.settings'].signature.header: protocols.helpers['shape.settings'].signature.header must be a header name
E_CONFIG_VALUE config protocols.helpers['shape.settings'].signature.encoding: protocols.helpers['shape.settings'].signature.encoding must be 'hex' or 'base64'
E_CONFIG_VALUE config protocols.helpers['shape.settings'].signature.prefix: protocols.helpers['shape.settings'].signature.prefix must be visible ASCII text
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.diagnostics -->

A header value is read as ASCII; a scheme that signs in any way the presets cannot describe needs an
[adapter signature](#adapter-signatures).

### Event mappings

Instead of one schema reference, `event_schema` can map the type name a body member holds to each event's schema, as SSE
and NDJSON helpers do, with a body discriminator only. The helper's event type is the union of the mapped models, and
each mapped schema must be a JSON request body of a webhook or callback operation. After verification, the body is
parsed once, the member the RFC 6901 `pointer` names selects the event's model, and the event decodes with it. The
member must be a string equal to a mapped name, compared exactly; otherwise decoding raises `ProtocolDataError` with
`location=BodySelector(pointer=...)` and `condition` `missing` for an absent member, `null`, `type` for another JSON
type, or `value` for an unknown name. There is no `unknown` setting for webhooks.

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.adapter-yaml -->
<!-- fmt: off -->

```yaml
schema_version: 1
helpers:
  stripe.event:
    kind: webhook
    event_schema:
      discriminator:
        from: body
        pointer: /type
      mapping:
        invoice.paid:
          pointer: /components/schemas/Invoice
        invoice.updated:
          pointer: /components/schemas/Invoice
        customer.created:
          pointer: /components/schemas/Customer
    signature:
      kind: adapter
      timestamp: required
      delivery_id: none
  unsigned.event:
    kind: webhook
    event_schema:
      discriminator:
        from: body
        pointer: /meta/type
      mapping:
        invoice.paid:
          pointer: /components/schemas/Invoice
        customer.created:
          pointer: /components/schemas/Customer
    signature:
      kind: none
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.adapter-yaml -->

### Adapter signatures

A signature `{kind: adapter, timestamp, delivery_id}` declares that your code verifies the signature: `timestamp` and
`delivery_id` are each `required` or `none`, and no other setting is read. The helper's functions are generic in the key
type and take a required `verifier: Verifier[K]` with `keys: KeySet[K]` of the same key type; `pkg.webhooks.keys` has
no key type for them. Write the verifier from the API's documentation, for example for a Stripe-style header:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.adapter -->
<!-- fmt: off -->

```python
@dataclass(frozen=True)
class StripeKey:
    """An application key: the SDK never reads its fields."""

    name: str
    secret: bytes = field(repr=False)


class StripeVerifier:
    """Verify `Stripe-Signature: t=<seconds>,v1=<hex>` over `<t>.<raw body>` with the first matching key."""

    def verify(
        self,
        raw_body: bytes,
        ordered_headers: tuple[tuple[str, str], ...],
        keys: KeySet[StripeKey],
        now: datetime,
        limits: ResolvedWebhookOptions,
    ) -> VerifiedSignature:
        """Return the signed timestamp and the matched key, or raise WebhookVerificationError."""
        del now
        values = [value for name, value in ordered_headers if name.lower() == "stripe-signature"]
        items = [item.partition("=") for item in (values[0].split(",") if len(values) == 1 else ())]
        stamps = [value for scheme, _, value in items if scheme == "t"]
        encoded = [value for scheme, _, value in items if scheme == "v1"]
        if len(stamps) != 1 or not (stamps[0].isascii() and stamps[0].isdigit()) or not encoded:
            raise WebhookVerificationError(condition="malformed_signature")
        if (count := len(encoded)) > limits.max_signatures:
            raise ProtocolSizeError(kind="signatures", unit="items", limit=limits.max_signatures, observed=count)
        try:
            signatures = [bytes.fromhex(value) for value in encoded if value.isascii()]
        except ValueError:
            signatures = []
        if len(signatures) != count:
            raise WebhookVerificationError(condition="malformed_signature")
        signed = stamps[0].encode() + b"." + raw_body
        for key in keys.keys:
            expected = hmac.new(key.secret, signed, "sha256").digest()
            if any(hmac.compare_digest(expected, signature) for signature in signatures):
                moment = datetime.fromtimestamp(int(stamps[0]), timezone.utc)
                return VerifiedSignature(delivery_id=None, timestamp=moment, matched_key_id=key.name)
        raise WebhookVerificationError(condition="invalid_signature")


def receive_stripe(raw_body: bytes, headers: list[tuple[str, str]], secret: bytes) -> Invoice | Customer:
    """Verify one Stripe-style delivery with the application's verifier and return its mapped event."""
    keys = KeySet(keys=(StripeKey(name="2026-09", secret=secret),))
    verified = event.verify(raw_body, headers, keys, verifier=StripeVerifier(), now=datetime.now(timezone.utc))
    return verified.data
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.adapter -->

`verify_async` calls the same synchronous verifier, once. After the helper checks its
arguments, including that `verifier` has a callable `verify` (or `ProtocolConfigurationError` with
`field_path=("verifier",)`), and the body, header, and key-count limits, it passes the exact body, the headers as a
tuple of pairs in received order, the key set, `now`, and the resolved limits; it never reads the keys. The verifier
must authenticate the whole body and every fact it returns, honor `max_signatures`, and raise `WebhookVerificationError`
when verification fails; every error it raises, including cancellation, propagates unchanged. Before decoding, the helper
raises `AdapterContractError` with `delivery_state=NOT_SENT`, without decoding, when the result is not a
`VerifiedSignature` (an awaitable result is closed unawaited), its `matched_key_id` is not a nonempty string, a
`required` fact is missing, not a nonempty string delivery id, or not an aware datetime, or a `none` fact is not
`None`. A returned timestamp is then checked against the timestamp window, as for builtin signatures.

### Unsigned webhooks

A signature `{kind: none}` declares that the API does not sign its deliveries; a helper without `signature` is
refused, so omitting it never means unsigned. The module has only
`decode_unverified(raw_body, *, options=None) -> Event`, which refuses a body that is not `bytes`
(`ProtocolConfigurationError`) or is over `max_body_bytes` (`ProtocolSizeError`), the only option it reads, and decodes
the event. Nothing authenticates who sent the delivery or whether it was changed or replayed, and the result is the
event alone, with no facts: treat it as untrusted input.

### Verification order and limits

A call checks `raw_body`, `headers`, `keys`, `now`, and `options`, then each builtin key's type and unique id.
Invalid arguments raise `ProtocolConfigurationError` with their field path. Body, header, key, and signature counts
are bounded by `ProtocolSizeError`. The helper checks signature syntax, timestamp tolerance when present, and
cryptographic authenticity before decoding the model. Decoding errors raise `ProtocolDataError`. An adapter checks
that `verifier.verify` is callable, then sizes, its returned facts, and the timestamp tolerance before decoding.
Errors keep no key, signature, header value, or body.

| Limit (`WebhookOptions`) | Default |
|---|---|
| `max_body_bytes` | 8 MiB |
| `max_header_bytes`, counting each name and value | 16 KiB |
| `max_keys`, `max_signatures` | 8 |
| `past_tolerance`, `future_tolerance` | 300 and 30 seconds; 0 is allowed |

- `malformed_signature`: a missing, empty, misencoded, or wrong-size signature, an incorrect prefix, or a missing,
  repeated, or invalid required timestamp or delivery id. A malformed candidate fails even if another would verify.
- `timestamp_window`: the timestamp lies outside `now - past_tolerance` to `now + future_tolerance`, both inclusive,
  compared in exact microseconds.
- `missing_key`: the active key set is empty.
- `invalid_signature`: no active key verifies. Keys and signatures are tried in order; HMAC digests are compared in
  constant time, and public keys verify with cryptography. The first matching key supplies `matched_key_id`.

### Application deduplication

Verification retains no delivery state and returns every authenticated event independently. Use an authenticated
`delivery_id` to deduplicate in application storage when the scheme supplies one. Keep processing and deduplication
consistent with your application's transaction and acknowledgment rules.
