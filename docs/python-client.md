# Python Client Generation

!!! warning "Experimental"

    HTTPX2 client generation is experimental. Its options, generated package, and template values may change. See
    [Experimental Features](experimental.md).

`--generate-client httpx2` generates the models of one OpenAPI document with the usual model options and, in the
same run, an HTTPX2 client package for its operations: a `Client` and an `AsyncClient` with a resource for each tag
and a method for each operation. Every generated file is regenerated on each run.

## Quick start

Set the client options in `[tool.datamodel-codegen]` of `pyproject.toml`, next to the model options:

```toml
[tool.datamodel-codegen]
input = "openapi.yaml"
input-file-type = "openapi"
output = "models.py"
output-model-type = "pydantic_v2.BaseModel"
openapi-scopes = ["schemas", "api"]
target-python-version = "3.11"
generate-client = "httpx2"
client-output = "client"
client-package = "client"
client-model-package = "models"
```

Then run `datamodel-codegen` without arguments to generate the models and the client, or pass the same settings as
options:

```bash
datamodel-codegen \
  --input openapi.yaml \
  --input-file-type openapi \
  --output models.py \
  --output-model-type pydantic_v2.BaseModel \
  --openapi-scopes schemas api \
  --target-python-version 3.11 \
  --generate-client httpx2 \
  --client-output client \
  --client-package client \
  --client-model-package models
```

Generation ends by printing to stderr the `uv add` command that adds the runtime dependencies of the package to your
project. `--check` compares without writing and prints the differences as for models, and `--output-format json`
emits the model generation and check payloads, as with `--generate-server`. `--diff-against` and `--watch` also work
as they do with `--generate-server`.

As for models, every generation overwrites every generated file of the package, also one you edited, and never
deletes one. The modules of a resource that is no longer generated stay until you delete them, and `--check` lists
them as extra files; keep your own code outside the package, because `--check` treats every `.py` file under
`--client-output` as generated. The models can live inside the package, such as `--output client/models.py` with
`--client-model-package client.models`, as for the [server package](fastapi-server.md#the-generated-package).

## Client settings

Every client setting is an option, a key of `[tool.datamodel-codegen]` in `pyproject.toml`, and an option of
`generate()` for the [Python API](#python-api):

| Option | `pyproject.toml` key | `generate()` option | Default |
|---|---|---|---|
| `--generate-client httpx2` | `generate-client` | `generate_client` | off |
| `--client-output` | `client-output` | `client_output` | required |
| `--client-package` | `client-package` | `client_package` | required |
| `--client-model-package` | `client-model-package` | `client_model_package` | required |
| `--client-signature-style` | `client-signature-style` | `client_signature_style` | `"explicit"` |
| `--client-body-arguments` | `client-body-arguments` | `client_body_arguments` | `"body"` |
| `--client-resource-names` | `client-resource-names` | `client_resource_names` | none |
| `--client-operations` | `client-operations` | `client_operations` | none |
| `--client-default-base-url` | `client-default-base-url` | `client_default_base_url` | none |
| `--client-server-base-url` | `client-server-base-url` | `client_server_base_url` | none |
| `--client-protocols` | `client-protocols` | `client_protocols` | none |

`--client-resource-names`, `--client-operations`, and `--client-protocols` take a JSON object, inline or as the path
of a JSON file, as `--aliases` does; in `pyproject.toml` they are tables, or the path of a JSON file, and in
`generate()` mappings of the same shape. A value on the
command line takes precedence over the selected profile, which takes precedence over the base table, and a
command-line table replaces the whole table of `pyproject.toml`. An operation's own `body_arguments` takes precedence
over `--client-body-arguments`, wherever each comes from. Paths in `pyproject.toml`, including the JSON files, are
relative to its directory, and command-line paths are relative to the working directory. The documents that
operation references and helpers name are relative to the JSON file that holds them, however the file is given, as
a `$ref` is relative to its document; a file given through a symbolic link uses the link's directory. Without a
file, they are relative to the `pyproject.toml` directory for its tables, and to the working directory for inline
JSON on the command line and for mappings given to `generate()`.

`--client-resource-names` maps a tag to the dotted namespace of the resource its operations join, such as
`{"pets": "store.pets"}`. `--client-operations` maps an operation reference, the JSON pointer of the path item method
such as `/paths/~1pets/get`, or a document and a pointer joined by `#`, to the settings of that operation:

| Member | Value |
|---|---|
| `resource` | The dotted namespace of the operation's resource |
| `name` | The method name |
| `parameter_names` | Argument names keyed by location and name, such as `{"query:limit": "page_size"}` |
| `request_media_type`, `response_media_type` | The default media types of the body and of the result |
| `description` | The method docstring |
| `body_arguments` | `"body"` or `"both"`, over `--client-body-arguments` |
| `body_field_names` | Keyword names of body fields, keyed by media type, then property, such as `{"application/json": {"petName": "pet_name"}}` |
| `runtime` | `request_id_header`, `success_statuses`, `retry_safety`, `idempotency` (`{"header_name": "Idempotency-Key"}`), `retry_after_ms_header`, `should_retry_header`, `auth_challenge_less_401`, and `accepted_content_encodings` |

```toml
[tool.datamodel-codegen.client-operations]
"/paths/~1pets/get" = { name = "list_all", parameter_names = { "query:limit" = "page_size" } }
"/paths/~1pets/post" = { runtime = { idempotency = { header_name = "Idempotency-Key" } } }
```

```bash
datamodel-codegen --client-operations '{"/paths/~1pets/get": {"name": "list_all"}}'
```

A member the client does not know, or a value it cannot use, stops generation with `Error:` and exit code 2, naming
the setting by the key it was given under, such as `--client-operations['/paths/~1pets/get'].runtime.retry_safety`;
`generate()` raises `datamodel_code_generator.Error` with the same message.

`--generate-client` requires `--client-output`, `--client-package`, and `--client-model-package`, and a `--client-*`
option given on the command line requires `--generate-client`; both stop with `Error:` and exit code 2.
`--generate-client` cannot be used with `--generate-server`, and one selected on the command line replaces the other
selected in `pyproject.toml`. Client keys of `pyproject.toml` are validated like model keys, but have no effect while
no client is selected.

A [named job](pyproject_toml.md#named-jobs-experimental) whose settings select the client generates it as a single run
does, under the rules of [server jobs](fastapi-server.md#batch-jobs): `client-output` must not overlap the outputs of
other jobs or contain the remote lock file, and `--all-jobs --watch` regenerates client jobs too. Jobs that each
select a server or a client may name the same models `output`, which the batch writes once; they must generate the
same models for it.

## Python API

`generate()` takes the client options with the model options. The choices are the enums `ClientType`,
`ClientSignatureStyle`, and `ClientBodyArguments` of `datamodel_code_generator`, whose string values also work, and
`client_resource_names`, `client_operations`, and `client_protocols` take mappings shaped like their JSON:

```python
from pathlib import Path

from datamodel_code_generator import ClientType, OpenAPIScope, generate

generate(
    Path("openapi.yaml"),
    input_file_type="openapi",
    openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
    target_python_version="3.11",
    output=Path("models.py"),
    generate_client=ClientType.HTTPX2,
    client_output=Path("client"),
    client_package="client",
    client_model_package="models",
    client_operations={"/paths/~1pets/get": {"name": "list_all", "parameter_names": {"query:limit": "page_size"}}},
)
```

It publishes every change together and returns `None`, like model generation. Without an `output`, it writes nothing
but the model metadata and a remote lock update and returns every model and client file as `GeneratedModules`, under
the paths their import paths imply; see
[Generating a Server or Client Package](using_as_module.md#generating-a-server-or-client-package).
`load_pyproject_config()` reads the client keys of `pyproject.toml`, whose paths and documents resolve as they do on
the command line.

## Requirements

Like the server, the client needs Python 3.11 or later, both to run `datamodel-codegen` and as the target Python
version; a target below 3.11, including the default 3.10, is refused.

Settings of the generated client that change how its methods are declared or checked do not change the models: the
same OpenAPI document and model settings produce the same model files whichever client settings you choose.

The model's `openapi_scopes` must include `api`, so that the models cover the parameters, bodies, and responses of
the operations; without it, generation stops with `Error: --generate-client requires --openapi-scopes to include api`
and exit code 2, and the Python API raises `datamodel_code_generator.Error`. The client keeps every model setting
as given, including the generation timestamp, which `disable_timestamp` leaves out. The client files are headed,
formatted, and encoded like the model files, by the same model settings: `formatters`, `custom_formatters` and
`custom_formatters_kwargs`, `custom_file_header`, `custom_file_header_path` and `custom_file_header_mode`,
`disable_timestamp`, `enable_version_header`, `enable_command_header`, `use_double_quotes`,
`builtin_format_line_length`, `encoding` (for the Python files; the other files are UTF-8), and `settings_path`;
`use_type_checking_imports` does not apply to the client files, whose annotations are read at run time. Custom
templates and custom formatters must keep the class and field names of the models: the client binds each operation
to the names in the generated model graph and does not read the rendered model source.

## Model conversion

The generated models determine validation and conversion. Text in parameters, response headers, forms and text parts
is decoded before the selected backend handles it: a declared integer, number or boolean becomes that builtin when the
text is its canonical JSON literal. Pydantic and msgspec receive any other text unchanged and decide by their native
conversion rules, as they do for unknown object members. Dataclass and TypedDict codecs validate nothing, so for them
such text is a decoding error, like text that cannot become a `Decimal`, date or `UUID` leaf; their JSON bodies are
still constructed without added schema validation. Repeated undeclared members keep their last value.

The generated `model_codecs` module exports `UNSET` and `JSONValue`. Parameter parsing records and wire
validation errors are implementation details.

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
`UNSET` removed and every argument required. Both types are exported from `pkg.protocols`.
`ResolvedWebhookOptions` records limits already validated and resolved by the consuming helper. Helpers must
validate `WebhookOptions` before constructing this record and passing it to an application verifier.

| Field | Type | Effective default | Valid values |
|---|---|---|---|
| `max_body_bytes` | `int \| UNSET` | `8388608` | Positive integer |
| `max_header_bytes` | `int \| UNSET` | `16384` | Positive integer |
| `max_keys` | `int \| UNSET` | `8` | Positive integer |
| `max_signatures` | `int \| UNSET` | `8` | Positive integer |
| `past_tolerance` | `float \| UNSET` | `300` seconds | Finite, nonnegative number |
| `future_tolerance` | `float \| UNSET` | `30` seconds | Finite, nonnegative number |

Options reject `None`, booleans, negative values, nonfinite durations, and zero count limits with
`ConfigurationError`. For example, `WebhookOptions(past_tolerance=0)` is valid, while
`WebhookOptions(max_keys=0)` is invalid. Verification retains no delivery state. Applications can deduplicate
using the authenticated `delivery_id` when a signature scheme supplies one.

Webhook exceptions have keyword-only constructors and the following additional fields:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.error-fields -->
| Exception | Direct base | Fields |
|---|---|---|
| `ConfigurationError` | `SDKError` | `field_path: tuple[str \| int, ...] = ()`, `reason: str = 'invalid_value'` |
| `ProtocolDataError` | `SDKError` | `reason: str = 'value'` |
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.error-fields -->

All fields without a displayed default are required. They also accept the shared context fields
`helper_id: str | None = None`, `operation: OperationRef | None = None`, `info: ResponseInfo | None = None`, and
`cause: BaseException | None = None`, together with inherited SDK error fields. A failed webhook verification raises
`ProtocolDataError` with the verification failure as its `reason`: `malformed_signature`, `invalid_signature`,
`missing_key`, or `timestamp_window`; a body, header, key, or signature count over its limit raises it with the reason
`too_large`. Error strings and representations exclude delivery IDs, namespaces, operation references, entry IDs, and
causes; callers may read those attributes explicitly.

## Protocol contracts

Generated packages also expose the shared contracts of the pagination, polling, stream, WebSocket, cache, upload, and
webhook helpers they declare, and copy only the runtime modules those helpers, the declared security schemes, request
bodies, and response headers, and the model backend need; `pkg.protocols` exists only with a helper, and `pkg.bodies`
only with a binary, multipart, or schema-less form body. Records, options, cache stores, and resume state come from
`pkg.protocols`. The client's `helper_defaults=` keyword is available only in packages that declare a pagination,
polling, stream, WebSocket, cache, or upload helper; see [client helper settings](#client-helper-settings). Helper
execution loads when `client.protocols`
is first used; ordinary operations share the same native HTTP client. Exceptions come from `pkg.errors`, each with the
helper that raises it. These imports need no HTTP library and start no threads. A client that uses no helper settings
loads none of these definitions: `pkg.errors` loads them the first time one of its names is used. The
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

### Poll snapshots and progress

`PollSnapshot[P]` is an immutable poll result with `state: JSONValue`, `terminal: bool`, `data: P`, and
`response: ResponseInfo`. The state is a copy of the given value, and the state and data are excluded from the
representation. `P` is covariant, so a `PollSnapshot[Pet]` is also a `PollSnapshot[object]`. `CancelReceipt[C]` is the
immutable response of a remote cancel request, with `data: C` and `response: ResponseInfo`; the data is excluded from
the representation, and `C` is covariant too.

`ProgressKey` is `Literal['pages', 'items', 'polls', 'reconnects', 'parts', 'confirmed_bytes', 'messages_sent',
'messages_received']`, and `ProtocolProgress` is `Mapping[ProgressKey, int]`.

Resume state encodes its value as canonical JSON: UTF-8 without whitespace, object members sorted by the code points
of their names, and strings escaped as Python's `json.dumps` escapes them with `ensure_ascii=False`, so only `"`, `\`,
and U+0000–U+001F are escaped. Numbers are written as `json.dumps` writes them: an integer in plain decimal and a
float as its shortest round-trip representation, so `1e16` stays `1e+16`. Decoding the result with `json.loads` and
encoding it again gives the same bytes. A value `json.dumps` cannot encode raises `TypeError`; a non-finite number, a
lone surrogate, a reference cycle, a value nested beyond the interpreter recursion limit, and an integer beyond the
interpreter's decimal conversion limit raise `ValueError`. A member name that is not a string is written as
`json.dumps` writes it.

### Upload records and sources

`UploadProgress(*, confirmed_bytes: int, total_bytes: int, complete: bool = False)` is how far an upload is; the
confirmed bytes never exceed the total. `UploadSource` is `bytes | bytearray | memoryview | BinaryIO`, the content an
upload reads; see [upload helpers](#upload-helpers). The handles are loaded only when first requested.

### Helper options

Every option field defaults to `UNSET`, imported from `pkg.options`. The effective defaults below apply after
merging. `None` removes a limit only where the table allows it. Counts and byte limits are integers, excluding
booleans. Durations are finite numbers of seconds, excluding booleans; integers are accepted. Invalid values raise
`ConfigurationError` with `reason='invalid_value'` and the field name as `field_path`.

| Type | Field | Effective default | Valid values |
|---|---|---|---|
| `PaginationOptions` | `max_pages` | `None` (no limit) | Positive integer or `None` |
| | `max_items` | `None` (no limit) | Nonnegative integer or `None`; `0` ends without sending |
| `PollOptions` | `max_polls` | `1000` | Positive integer or `None` |
| | `interval` | The declared interval, `1` second by default | Positive duration |
| | `max_wait` | `60` seconds | Positive duration or `None` |
| `StreamOptions` | `idle_timeout` | The native read timeout | Positive duration or `None` |
| | `reconnect` | `False` | `bool` |
| | `max_reconnects` | `5` | Nonnegative integer or `None` |
| | `max_reconnect_wait` | `60` seconds | Positive duration or `None` |
| `UploadOptions` | `chunk_bytes` | `8388608`, at most the helper's `max_chunk_bytes` | Positive integer |
| `CacheOptions` | `max_entry_bytes` | `2097152` | Positive integer: the largest body a fetch stores |
| | `max_ttl` | `300` seconds | Positive duration: the cap on any entry's freshness |
| `WSOptions` | `open_timeout`, `idle_timeout`, `ping_interval`, `pong_timeout` | See [WebSocket limits](#websocket-limits) | Positive duration or `None` |
| | `max_message_bytes` | `1048576` | Positive integer |
| Every type but `CacheOptions` | `total_timeout` | `600` seconds for polling, no limit otherwise | Nonnegative duration or `None`: the helper session's budget from its start |


### Client helper settings

Helper settings belong to the root client only: neither `with_options` nor `RequestOptions` takes them, and views share
the root's. A root takes `helper_defaults` when the package declares a helper, `cache_stores` when it declares a cache
helper, and `allowed_origins` when it declares a pagination helper that follows a server's next URL or `Link` header.

```python
from pkg import Client
from pkg.protocols import PaginationOptions

client = Client(helper_defaults={"users.all": PaginationOptions(max_pages=10, total_timeout=300)})
```

| Setting | Type | Meaning |
|---|---|---|
| `helper_defaults` | `Mapping[str, HelperOptions] \| None`, default `None` | The options of each helper, by helper name: `PaginationOptions`, `PollOptions`, `StreamOptions`, `CacheOptions`, `WSOptions`, or `UploadOptions`, of the helper's kind. The mapping is copied |
| `cache_stores` | `Mapping[str, CacheStore] \| None` (`AsyncCacheStore` on `AsyncClient`), default `None` | The store each [cache helper](#cache-helpers) keeps its entries in, by helper name. The mapping is copied and keeps each store's identity; the client borrows the stores and never closes them |
| `allowed_origins` | `Sequence[Origin] \| None`, default `None` | The origins beyond the server's that a next-URL or Link pagination helper may follow a URL to; an origin listed twice counts once |

The client checks these settings when it is constructed: a name that is no helper of the package raises
`ConfigurationError` with `unknown_field`, and options of another kind, or no options record, with `invalid_value`.
Explicit call options take precedence over these defaults, which take precedence over the effective defaults above.

### Protocol exceptions

Four classes cover what a helper reports beyond the core errors. Each derives from `SDKError`, comes from `pkg.errors`
only when the package declares a helper that raises it, and has a keyword-only constructor that does not validate field
values. Besides the fields below, each accepts `helper_id`, `operation`, `operation_id`, `info`, and `cause`.

| Exception | Fields |
|---|---|
| `ProtocolDataError` | `reason: str = 'value'`, `location: Selector \| RequestTarget \| None = None`, `data: object = None` |
| `SessionLimitError` | `reason: Literal['pages', 'items', 'polls', 'reconnects', 'parts', 'wait', 'deadline']`, `limit: float`, `progress: ProtocolProgress`, `required_wait: float \| None = None` |
| `StreamInterruptedError` | `reason: Literal['eof', 'transport']`, `sequence: int`, the last record delivered |
| `WebSocketClosedError` | `code: int \| None`, `reason: str` of at most 123 UTF-8 bytes, `clean: bool` |

`ProtocolDataError` has the `reason` `missing`, `null`, `type`, `value`, `malformed`, `inconsistent`, or `too_large` for
data that breaks the helper's rules, `pagination_cycle` for a continuation already seen, `operation_failed` or
`operation_cancelled` for a polled operation that reached that declared state, `error_event` for a declared error event
of a stream, and `malformed_signature`, `invalid_signature`, `missing_key`, or `timestamp_window` for a webhook that
failed verification. `data` is the value the server sent with it, decoded as declared: the final poll's data or the
error event's. `SessionLimitError` names the cap in `reason`; `wait` and `deadline` mean that a server's delay, kept in
`required_wait`, is longer than the allowed wait or the remaining deadline. `progress` is copied into a read-only
mapping, and the handle's `checkpoint()` continues the operation.

Other refusals use the core errors. A call or checkpoint a helper refuses, such as a step while another runs, a step
after a failure or `close()`, an expired or non-resumable checkpoint, a source whose size changed, or a validator header
that conflicts with the cache entry, is a `ConfigurationError` with a `field_path` and a `reason` such as
`invalid_state`, `expired`, `wrong_capability`, `source_changed`, or `binding_mismatch`. A failed WebSocket handshake
negotiation, and a send or upload request that may have reached the server, is an `APIConnectionError` with the reason
`negotiation_failed` or `delivery_unknown`. A stream event, NDJSON record, or WebSocket message that does not decode is
a `DecodeError` with its `location`. A cache store that fails is an `SDKError` with the reason `store_failed` and the
store's exception as `cause`.

Messages and representations exclude locations, progress, resume state, raw bytes, event data, and tags; read those
attributes explicitly.

## Protocol helper configuration

Pagination, polling, SSE or NDJSON stream, WebSocket, webhook, cache, and resumable upload helpers of an API are declared
in a helper configuration, which the client target reads through its `protocols` setting. The helpers are still being
implemented: generation validates every helper and resolves its references against the selected API. An enabled
pagination helper generates the [pagination helper](#pagination-helpers) below, an enabled polling helper the
[polling helper](#polling-helpers), an enabled `offset` upload helper the
[upload helper](#upload-helpers), an enabled SSE helper the [SSE stream helper](#sse-stream-helpers), an enabled NDJSON
helper the [NDJSON stream helper](#ndjson-stream-helpers), an enabled WebSocket helper the
[WebSocket helper](#websocket-helpers), an enabled cache helper the [cache helper](#cache-helpers), and an enabled webhook helper the
[webhook verification helper](#webhook-verification-helpers). A disabled helper generates nothing, so the package is the
same as without it. The `parts` profile of `resumable_upload`
fails whether the helper is enabled or not, and its settings are not read yet; an enabled
upload helper declaring `abort` or `create.session_url` fails too.

| Setting | Values | Default | Where |
|---|---|---|---|
| `protocols` | A helper file path, a JSON object, or none | None | `--client-protocols helpers.json` or inline JSON; `client-protocols = "helpers.json"` or a table in `pyproject.toml`; `generate()`: `client_protocols`, a mapping. The documents a helper file names are relative to the file; those of inline JSON or a mapping are relative to the working directory, and those of a `pyproject.toml` table to its directory |

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.protocols.pyproject -->
<!-- fmt: off -->

```toml
[tool.datamodel-codegen]
input = "options.yaml"
input-file-type = "openapi"
output = "models.py"
output-model-type = "pydantic_v2.BaseModel"
openapi-scopes = ["schemas", "api"]
target-python-version = "3.11"
formatters = ["builtin"]
disable-timestamp = true
generate-client = "httpx2"
client-output = "client"
client-package = "client"
client-model-package = "models"
client-protocols = "protocols.json"
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.protocols.pyproject -->

The helper file is read only when the target is generated, so its problems are reported then, with the target's
identity. Without
`protocols`, nothing is read.

### The helper file

The file holds one JSON object mapping each helper's name to its definition, in declaration order:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.protocols.json -->
<!-- fmt: off -->

```json
{
  "users.all": {
    "kind": "pagination",
    "enabled": false,
    "operation": "/paths/~1users/get",
    "items": {"from": "body", "pointer": "/data"},
    "item_schema": {"pointer": "/components/schemas/User", "document": "../helpers.yaml"},
    "continuation": {
      "kind": "cursor",
      "read": {"from": "body", "pointer": "/next_cursor"},
      "write": {"in": "query", "name": "cursor"},
      "end": [{"kind": "missing"}, {"kind": "null"}],
      "empty_string": "value"
    },
    "bindings": [
      {
        "target": {"in": "header", "name": "snapshot"},
        "value": {"source": "initial", "selector": {"from": "header", "name": "Snapshot", "occurrence": "all"}}
      }
    ]
  },
  "jobs.run": {
    "kind": "polling",
    "enabled": false,
    "create": "/paths/~1jobs/post",
    "accepted_statuses": [202],
    "poll": "/paths/~1jobs~1{jobId}/get",
    "bindings": [
      {
        "target": {"in": "path", "name": "jobId"},
        "value": {"source": "initial", "selector": {"from": "body", "pointer": "/id"}}
      }
    ],
    "state": {"from": "body", "pointer": "/status"},
    "pending": ["queued", "running"],
    "succeeded": ["done"],
    "failed": ["failed"],
    "cancelled": ["cancelled"],
    "result": {
      "kind": "inline",
      "selector": {"from": "body", "pointer": ""},
      "schema": {"pointer": "/components/schemas/Job"}
    },
    "interval": {"seconds": 2, "retry_after_header": "Retry-After"},
    "remote_cancel": {
      "operation": "/paths/~1jobs~1{jobId}/delete",
      "bindings": [
        {
          "target": {"in": "path", "name": "jobId"},
          "value": {"source": "initial", "selector": {"from": "body", "pointer": "/id"}}
        }
      ]
    },
    "immediate_result": {
      "statuses": [200, 201],
      "selector": {"from": "body", "pointer": ""},
      "schema": {"pointer": "/components/schemas/Job"}
    },
    "expires_at": {"from": "body", "pointer": "/expires"}
  },
  "records.watch": {
    "kind": "ndjson",
    "enabled": false,
    "operation": "/paths/~1records/get",
    "media": "application/x-ndjson",
    "event_schema": {"pointer": "/components/schemas/Record", "document": "../helpers-external.yaml"},
    "completion": {"kind": "sentinel", "value": "[DONE]"},
    "final_line": "allow_eof",
    "resume": {
      "enabled": true,
      "cursor": {"from": "body", "pointer": "/id"},
      "write": {"in": "query", "name": "after"},
      "reopen_operation": "/paths/~1records/get",
      "delivery": "at_least_once",
      "missing": "inherit",
      "null": "clear",
      "expires_at": {"from": "header", "name": "X-Expires"}
    }
  }
}
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.protocols.json -->

- The file is read as JSON options such as `--aliases` read a JSON file: UTF-8 text parsed by Python's `json`
  module, so a key repeated in one object keeps its last value. A file that cannot be read, text that is not JSON,
  including a path that names no file, and a value that is not an object fail with the message
  `--client-protocols` gives, such as `Invalid JSON for protocols: ...` or
  `Invalid protocols: Input should be a valid dictionary`. Before parsing,
  text that opens more than 64 arrays and objects at once fails the same way, with
  `Invalid JSON for protocols: nests collections deeper than 64 levels`. `NaN` and `Infinity`, which the parser
  accepts, are not JSON values for a literal.
- Every key is checked at every level. A key the definition does not have, including a setting of another kind or
  one that the chosen variant does not take, fails; a missing required key or a value of
  the wrong type fails; and settings that contradict each other fail.
- A helper name is Python identifiers separated by dots, each in NFKC form, not a keyword, not starting with `_`, and
  not a Windows device name such as `con` or `com1`. Two names equal after NFC normalization and case folding, or
  one name that is a dotted prefix of another, such as `users` and `users.all`, fail.
- Every definition has `kind`, one of `pagination`, `polling`, `sse`, `ndjson`, `websocket`, `webhook`, `cache`,
  and `resumable_upload`, and `enabled`, a boolean that defaults to `true`.

### Shared values

| Value | Forms |
|---|---|
| Operation reference | An RFC 6901 pointer string to a root path operation, or `{pointer, document?}`. A document names the root input, relative to the helper file, or without a file to the working directory, or to the `pyproject.toml` directory for its table |
| Schema reference | `{pointer, document?}` with an RFC 6901 pointer; an omitted document is the root input. A document, resolved as for operation references, must be one of the accepted input's documents |
| Selector | `{from: body, pointer}`, an RFC 6901 pointer over wire names; `{from: header, name, occurrence?}` with `occurrence` `single` (default) or `all`; or `{from: status}`. `all` is accepted only in a binding value |
| Request target | `{in: path\|query\|header\|cookie, name}`, `{in: querystring, name, pointer}` with the declared querystring's name, or `{in: body, pointer}` |
| Binding | `{target, value}`, where `value` is `{source, selector}` with `source` `input`, `initial`, or `previous`, or `{literal}` with any JSON value whose text is UTF-8 and whose containers nest at most 64 levels |
| End condition | `{kind: missing}`, `{kind: "null"}`, or `{kind: value, value}` with a JSON value other than null. A list of them is nonempty and names each condition once |

A reference must select a root path operation of the input, or generation fails; webhooks,
path items of other documents, and paths that `--openapi-include-paths` leaves out are refused. Each request target
must exist in the operation it writes to: the declared parameter of that location and name, header names compared
without case, the operation's own querystring, or its request body. An operation that owns a querystring takes no
`in: query` target.

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
four state lists hold JSON values: a list that repeats a value fails, and a value that two lists
share fails. `result` is `{kind: inline, selector, schema}`,
`{kind: operation, operation, bindings?}`, or `{kind: none}`, and `interval.seconds` is positive.

An upload's `completion` is `{kind: length}` or `{kind: operation, operation, result_schema, bindings?}`. The `parts`
profile is refused without reading its other settings.

A stream's `event_schema` is one schema reference, or `{discriminator, mapping}` where `discriminator` is
`{from: event_type}` (SSE only) or `{from: body, pointer}` and `mapping` names the schema of each event type.
`unknown: raw` and `error_events` need a discriminator, and an error event cannot also be a mapped event. `completion`
is `{kind: eof}`, `{kind: sentinel, value}`, or `{kind: event_type, value}` (SSE only). An enabled `resume` takes
`cursor` (a selector, or `event_id` for SSE), `write`, `reopen_operation`, `delivery: at_least_once`, `bindings`
(`[]`), `reconnect_on` (a nonempty list of `transport_interruption` and `incomplete_eof`, by default
`[transport_interruption]`), and optionally `expires_at`; a selector cursor also takes `missing` (`inherit` or
`error`) and `"null"` (`clear` or `error`). A disabled `resume` takes no other key.

Helpers reserve the arguments `pagination_options`, `poll_options`, `stream_options`,
`ws_options`, `cache_options`, `upload_options`, `source`, `items`, and `state`. When
an enabled helper's operation, or its `create` operation for polling and uploads, has a parameter or field argument
with one of these names, generation fails instead of renaming it; name the argument with
`parameter_names` or `body_field_names`:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.protocols.diagnostics -->
<!-- fmt: off -->

```text
--client-protocols['tags.all']: The pagination helper 'tags.all' reserves the argument 'items' of GET /tags; rename them with the operation's parameter_names or body_field_names in --client-operations
--client-protocols['jobs.run']: The polling helper 'jobs.run' reserves the argument 'state' of POST /jobs; rename them with the operation's parameter_names or body_field_names in --client-operations
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.protocols.diagnostics -->

### Helpers in Python

`generate()` takes the object of the helper file as a mapping in `client_protocols`, validated as the file is, with
the same messages:

```python
generate(
    Path("openapi.yaml"),
    ...,
    generate_client="httpx2",
    client_output=Path("client"),
    client_package="client",
    client_model_package="models",
    client_protocols={
        "users.all": {
            "kind": "pagination",
            "enabled": False,
            "operation": "/paths/~1users/get",
            "items": {"from": "body", "pointer": "/data"},
            "item_schema": {"pointer": "/components/schemas/User"},
            "continuation": {"kind": "link", "header": "Link"},
        },
    },
)
```

A file and the equal mapping generate the same package, as do references that name the root input explicitly and
ones that leave it implied.

## Pagination helpers

An enabled pagination helper, whatever its continuation, is generated at `client.protocols.<name>` on `Client` and
`AsyncClient` alike; the property exists only when the package has a helper. `page` fetches the first
page, `next_page` the page after one the helper returned, and `iterate` returns a pager, which sends nothing until it is
iterated; `resume` returns a pager continuing a pager's checkpoint. A helper takes the operation's parameters and its
body as keywords, never field arguments, then `pagination_options` and `options`:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.pagination.helper -->
<!-- fmt: off -->

```python
    def page(
        self,
        *,
        cursor: str | UNSET = UNSET,
        limit: int | UNSET = UNSET,
        x_snapshot: str | UNSET = UNSET,
        pagination_options: PaginationOptions | None = None,
        options: RequestOptions | None = None,
    ) -> Page[models.User, ListUsersResponse]:
        """Fetch the first page of GET /users."""
        return first_page(
            self._core,
            _plans.PLAN_0,
            (cursor, limit, x_snapshot),
            pagination_options=pagination_options,
            options=options,
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
`ResponseInfo`, and the `continuation` after it: the server's cursor, the next offset or page number, or the resolved
next URL, as a `JSONValue`, or None on the last page. `Pager[T, P]` iterates over the items and `iter_pages()` over the
pages; `AsyncPager[T, P]` does the same with `async for`, and its `iter_pages()` is not awaited. `Page`, `Pager`, and
`AsyncPager` are imported from `pkg.protocols`, in every package. A pager fetches a page only once the previous one is
consumed and reads, decodes, and closes each page inside its own call, so leaving a loop early holds no response. Its
`progress` reports the pages fetched and the items delivered.

### Cursors and end conditions

A page's items must be a JSON array at the `items` pointer; an empty array does not end the traversal. The cursor is
read from the page's body, a response header, or its status, and written to the target of `write`: a path, query, or
header parameter, a property of the querystring, or a member of the JSON request body, never a position that carries
credentials. A cursor the server returned is sent as it came, without its target's codec, while a start cursor the
caller passes is encoded like any other argument. The traversal ends at a page whose cursor is missing or null where
the helper declares that end, is one of its end values, or is empty with `empty_string: end`. A missing or null cursor
that no end covers, missing or null items, a header repeated for a single cursor, and a value read for a path
parameter that makes its segment, encoded in the parameters' styles with the caller's own path arguments and the
helper's literals beside it, a dot segment (`.` or `..`, `%2E` in either case counting as `.`) raise
`ProtocolDataError` as a failure of the page's call. The error names the first such read value whose encoded text is
non-empty, or else the first one. A page is read whole like any response. A
continuation returned by an earlier page of the same pager, or an earlier page `next_page` continued from, ends it with
`ProtocolDataError` with the reason `pagination_cycle` after the repeating page.

### Offsets and page numbers

An `offset` or `page` continuation counts positions instead of reading them. The first request is sent as the caller
gives it. The traversal starts at the position the caller passes for the `write` target, read from the first request
after its usual encoding: the parameter's value, or the member at the pointer of the querystring or JSON body. An
integral number such as `40.0` on a `number` target starts at its integer. Without one it starts at `first`, the offset
or page number the server gives a request without one. A starting value that is not an integer fails like any invalid
argument, with a request `DecodeError` when its codec refuses it, and otherwise with `ProtocolDataError` (reason
`type`, location the target) before anything is sent. Each request after a page writes the page's position advanced by
the `literal` step, or for an offset by the page's item count with `page_items_count`; a page number always advances by
a literal step, and generation refuses `page_items_count` for it. The position is written to `write` like a cursor, so
its target must accept integers, and `Page.continuation` has the kind `offset` or `page`.

```json
{
  "users.by_offset": {
    "kind": "pagination",
    "operation": "/paths/~1users/get",
    "items": {"from": "body", "pointer": "/data"},
    "item_schema": {"pointer": "/components/schemas/User"},
    "continuation": {
      "kind": "offset",
      "write": {"in": "query", "name": "offset"},
      "first": 0,
      "step": {"page_items_count": true},
      "total": {"from": "header", "name": "X-Total-Count"}
    }
  }
}
```

The traversal ends at a page whose `has_more` reads `false`, or once the items counted before the next position reach
the `total` the page reads. For an offset they are the offsets past `first`, since a total counts the whole collection
wherever the traversal started; for a page number they are the items delivered since the start, the only count a page
number gives, and a page without items ends it too, since no later page can bring the count nearer. A `total` of 0 ends
at the first page, and a position past the total ends too. `has_more` must read a JSON boolean and `total` a JSON
integer of at least 0; a header spells `true` or `false`, or decimal digits with an optional `-`, and generation checks
the types its declared schema gives. A missing, null, or mistyped value raises `ProtocolDataError`, and a negative
total, from the body or a header, raises it with the reason `value`. Otherwise an empty page does not end the
traversal, but one that continues while `page_items_count` counts items would ask for the same position again, so it
raises `ProtocolDataError` with the reason `inconsistent`. Positions only grow, so these helpers never raise
`ProtocolDataError` with the reason `pagination_cycle`.

### Next URLs and Link headers

A `next_url` or `link` continuation follows the URL a page gives. The first request is sent as the caller gives it, and
each later page goes to the URL the last page gave, as it came: the caller's query and the `default_query` or `extra_query` of
the client, a view, or the call are never laid over it, while the call's headers and the operation's header and cookie parameters
are sent again. A later page is a GET without a body, unless a next URL sets `repeat_request_body: true`, which sends
the operation's method with the caller's body again; generation refuses it unless the body may
be sent again as a 307 or 308 redirect sends it: the operation's method is GET, HEAD, OPTIONS, PUT, or DELETE, or its
`retry_safety` is `idempotent`. A `next_url` reads the URL with its `read` selector and ends like a cursor: at a missing
or null value only where an `end` condition says so, or at one of its end values, and a missing or null value no end
covers, or a value that is not a string, raises `ProtocolDataError`. A `link` continuation reads the RFC 8288 links of
the `header` field, every value of it, and follows the one whose `rel` lists the relation, `next` unless `rel` names
another; a page without that link is the last. An empty page with a next URL does not end the traversal.

```json
{
  "users.linked": {
    "kind": "pagination",
    "operation": "/paths/~1users/get",
    "items": {"from": "body", "pointer": "/data"},
    "item_schema": {"pointer": "/components/schemas/User"},
    "continuation": {"kind": "link", "header": "Link"}
  }
}
```

The Link header is read strictly. Links are separated by commas, each a `<URI-Reference>` followed by `;` and its
parameters, a token or a quoted string with backslash escapes, so a comma or semicolon inside quotes separates nothing;
empty list elements and the whitespace around separators are skipped. Parameter names and relation types match
without regard to ASCII case, a `rel` lists several relation types separated by spaces, and only a link's first `rel`
counts. A link with an `anchor` parameter describes another resource and is skipped. Any other syntax raises
`ProtocolDataError` with the reason `malformed`, and two links of the relation raise it with `inconsistent`.

A URL, from either continuation, resolves against the URL of the request that returned the page, after any redirect, as
RFC 3986 resolves a reference. A reference with characters outside RFC 3986, or with brackets anywhere but around an
IPv6 host, raises `ProtocolDataError` with `malformed`, and one with a fragment, user information, even empty, a scheme
other than `http` or `https`, or an invalid port raises it with `value`. The resolved URL excludes the query fields the
client's authentication places itself. The URL must name the origin of the server the operation is sent to, its scheme,
host, and port, or an origin the client's `allowed_origins` lists; any other origin raises
`ProtocolDataError` with `value` before anything is sent to it, and so does a `next_page` whose options select a server
whose origin the URL no longer shares or is allowed beside. A request to an allowed origin other than the server's, like a redirect to another origin, carries no
`Authorization`, `Proxy-Authorization`, `Cookie`, or `Cookie2` header, and none of the headers or query fields the
package's security schemes name, whether the authentication, a client, view, or call header, a parameter, a binding, or the server's URL
put them there; its other headers, the call's and the operation's, are sent as usual. The client's credentials are
placed only at the server's origin, so such a page is sent without them. A query credential the URL repeats is
removed before the client places its own. `Page.continuation` is the resolved server URL, and a URL an earlier
page of the session gave, or the URL of the first page itself, ends the traversal with `ProtocolDataError` with the reason
`pagination_cycle` after the repeating page; URLs compare once resolved and without the authentication's query fields, with the host's case, the
default port, and dot segments normalized, but not their percent-encoding.

### Bindings and request targets

Each request after the first is the first request with the helper's `bindings` written in order, then the cursor or
position. A binding writes a `literal`, or what its selector reads from the `initial` page's response, kept for the
whole traversal, or from the `previous` page's; a header selector with `occurrence: all` reads every value of the header
as an array. The bindings are read from every page that has a next page, even one a caller never continues, and a value
its selector finds missing raises `ProtocolDataError`; a header none of whose values is present counts as missing under
`occurrence: all` too. A next-URL or Link helper writes no cursor; its bindings write headers, and the JSON body only
when a next URL repeats it. A `null` is written as it is to a JSON body or a querystring of JSON content, the only
targets that can carry it. A parameter target replaces the argument. A querystring or body target writes into the
caller's querystring or JSON body, encoded and checked as in any call, or into an empty object when the call gives none;
a missing, null, or other non-object value on its pointer is replaced by an empty object. The querystring is then
encoded once, so no query pair is added. The values a server gave are never checked against their targets' schemas. A
call's `extra_headers` and `extra_query` must not name a header or a query parameter the helper writes: `iterate`,
`page`, and `next_page` raise `ConfigurationError` with a `field_path` of `("options", "extra_headers" or
"extra_query", <name>)` before sending. A binding
with `source: input` is not supported yet.

### Limits and sessions

A pager, and each `page` or `next_page` call, is one session. Every page is its own call, with its own retries, total
timeout, and idempotency key, and the session bounds all of them: a page's deadline is the earlier of its own and the
session's. Each limit comes from the call's options, then the helper's options in the client's
`helper_defaults`, then the default below:

| Limit | Default | None |
|---|---|---|
| `PaginationOptions.max_pages` | No limit | Removes the limit |
| `PaginationOptions.max_items` | No limit; 0 ends a pager at once without sending | Removes the limit |
| `PaginationOptions.total_timeout` | No limit | Removes the limit |

A limit reached while pages remain raises `SessionLimitError` with the progress so far; the last page ends normally even
exactly at a limit. An item limit is exact for items, while `iter_pages` checks it before each fetch, so a page that
crosses it is delivered whole, and `page` or `next_page` with `max_items=0` raises `SessionLimitError(reason="items",
limit=0)` without sending. Defaults naming a helper the package lacks, or giving it another kind's options, fail the
client's construction with `ConfigurationError`, and so do options of another type, and an idempotency key the call's
`RequestOptions` fixes, when a helper is called.

A pager is used by one consumer at a time and in one mode: stepping it while it fetches, closing it then, and
mixing items with pages raise `ConfigurationError` with the reason `invalid_state`. After a failure, cancellation
included, and after `close()`, every step raises it, and nothing is fetched again. Each page is an ordinary request
of the HTTP client, which its `event_hooks` see.

### Checkpoints and resume

`pager.checkpoint()` returns the continuation the pager fetches its next page with: the server's cursor, the next
offset or page number, or the resolved next URL, the same value as `Page.continuation`. It sends nothing, on `Pager`
and `AsyncPager` alike, and is None before the first page and after the last. The helper's `resume(state, ...)` takes
that value with the operation's arguments and any request body again, as `iterate` takes them, and returns a pager in a
session of its own that sends nothing until it is iterated; it is not awaited, even on `AsyncClient`.

```python
from pkg.errors import SessionLimitError
from pkg.protocols import PaginationOptions

with Client() as client:
    helper = client.protocols.users.all
    pager = helper.iterate(limit=20, pagination_options=PaginationOptions(max_pages=5))
    try:
        for user in pager:
            print(user.id)
    except SessionLimitError:
        for user in helper.resume(pager.checkpoint(), limit=20):
            print(user.id)
```

A continuation is the server's value alone. It holds no call arguments, credentials, body, or progress, and nothing in
it names the helper, so the caller keeps it beside the arguments of the call it belongs to. It is JSON, which
`json.dumps` and `json.loads` round-trip.

A pager iterated by items that stops in the middle of a page gives the continuation before that page, so a resumed
pager fetches the page again and repeats the items already delivered from it; iterate with `iter_pages()` to checkpoint
between pages. `resume(None, ...)` starts at the caller's own first request, as `iterate` does. A checkpoint remains
available after a pager fails or is closed, and a pager fetching a page raises `ConfigurationError` with the reason
`invalid_state`.

A pager stopped by `SessionLimitError` or by `ProtocolDataError` with the reason `pagination_cycle` is resumed from its
`checkpoint()` like any other; the errors themselves carry no continuation. A page fetched with
`page` or `next_page` continues from its own `continuation`. An item limit that stops inside a page leaves the
continuation before that page, None inside the first one, so resuming with a `max_items` below the page size delivers
the same items again; raise the limit, or limit pages instead.

A resumed pager counts its pages and items from zero against its own limits, its session's timeout and deadline start
afresh, and it detects cycles from the given continuation on. The first resumed request writes the continuation and the
helper's literal bindings; a binding that reads a response has no value yet, so its target takes the caller's argument,
encoded as in any call, and an `initial` binding is read from the first resumed page. A page-number helper that ends at
a total counts the items from the resumed page on: where a first run ends at the page that completes the total without
another request, a resumed one asks for the page after it and ends there when that page has no items, or fails with
whatever error the server answers for a page past the end.

An offset or page number must be a nonnegative integer and a next URL a string, or `resume` raises
`ConfigurationError(field_path=("state",), reason="invalid_value")`, as it does for a value that is not JSON. A next URL
is checked as a server's: it must be absolute, without a fragment or user information, and name the origin of the
server or one the client's `allowed_origins` lists, or `resume` raises `ProtocolDataError` before anything
is sent. The query fields the client's authentication places itself are removed from it at once, so the pager's
`checkpoint()` and its requests are without them, and a request to an allowed origin other than the server's carries
credentials only as a server's next URL would. A cursor written to a path
parameter is refused as any call's path value is when it makes its segment a dot segment.

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
target or another binding's, or a body or querystring member inside or around one, fails. An
offset's or page number's target must accept integers, a page number advances by a literal step, `has_more` must read
booleans, and `total` must read integers from the body or a header, never the status. A next URL must read strings, or
null with a `null` end, its end values must be strings, and `repeat_request_body` needs an operation with a body that
may be sent again; a Link continuation's header must be one the response declares, and `rel` one relation type, a
registered name such as `next` or an absolute URI. A next-URL or Link helper's binding that writes a path, query, or
querystring parameter, which the URL replaces, or the body without `repeat_request_body`, fails.
So does a cursor, position, or binding, a literal one included, that writes a cookie, the `Authorization`,
`Proxy-Authorization`, `Cookie`, or `Cookie2` header, or a header, query parameter, or querystring property at the
position of a declared security scheme: credentials are never written from what a server or a checkpoint gives. Every
cookie counts, declared as a scheme or not, because cookies commonly carry session state; this removes the cookie
cursors and cookie bindings earlier versions of the helpers wrote.
Helper names whose classes collide, such as `users.all_items` and `users_all.items`, fail.
Bindings that read the helper's input, request bodies other than JSON, and items or selector pointers that read
through a union or a map are not supported yet:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.pagination.diagnostics -->
<!-- fmt: off -->

```text
--client-protocols['users_all.items']: The helper name 'users_all.items' gives the class name 'UsersAllItemsPagination', which 'users.all_items' already gives
--client-protocols['bindings.input'].bindings[0].value.source: The binding 0 of 'bindings.input' reads the helper's input, which is not supported yet
--client-protocols['bindings.reads'].bindings[2].target: The binding 2 of 'bindings.reads' writes the cookie 'session', which carries credentials no helper writes
--client-protocols['bindings.reads'].bindings[0].value.selector: The binding 0 pointer '/nothing' of 'bindings.reads' names no property of the GET /users response
--client-protocols['bindings.reads'].bindings[1].value.selector: The binding 1 of 'bindings.reads' reads the header 'X-Missing', which GET /users does not declare
--client-protocols['bindings.reads'].bindings[2].value.selector: The binding 2 pointer '/grouped/admins' of 'bindings.reads' reads through a union or map, which is not supported yet
--client-protocols['bindings.types'].bindings[2].target: The binding 2 of 'bindings.types' writes the cookie 'session', which carries credentials no helper writes
--client-protocols['bindings.types'].bindings[0].target: The binding 0 of 'bindings.types' gives integer values, which the header parameter 'X-Cursor' of GET /users does not accept
--client-protocols['bindings.types'].bindings[1].target: The binding 1 of 'bindings.types' gives string values, which the query parameter 'size' of GET /users does not accept
--client-protocols['bindings.types'].bindings[2].target: The binding 2 of 'bindings.types' gives array values, which the cookie parameter 'session' of GET /users does not accept
--client-protocols['bindings.null'].bindings[0].target: The binding 0 of 'bindings.null' can give null, which cannot be written to the header parameter 'X-Cursor' of GET /users
--client-protocols['bindings.null'].bindings[1].target: The binding 1 of 'bindings.null' can give null, which cannot be written to the query parameter 'size' of GET /users
--client-protocols['bindings.conflicts'].bindings[0].target: The binding 0 of 'bindings.conflicts' writes the same target as its cursor
--client-protocols['bindings.conflicts'].bindings[2].target: The binding 2 of 'bindings.conflicts' writes the same target as its binding 1
--client-protocols['bindings.overlaps'].bindings[0].target: The binding 0 of 'bindings.overlaps' writes a target overlapping that of its cursor
--client-protocols['bindings.overlaps'].bindings[2].target: The binding 2 of 'bindings.overlaps' writes a target overlapping that of its cursor
--client-protocols['bindings.dots'].bindings[0].value.literal: The binding 0 of 'bindings.dots' gives '..', which makes the segment of the path parameter 'folder' of GET /folders/{folder} a dot segment
--client-protocols['bindings.label_empty'].bindings[0].value.literal: The binding 0 of 'bindings.label_empty' gives '', which makes the segment of the path parameter 'label' of GET /styled/{label}/{reserved}/{file}.json a dot segment
--client-protocols['bindings.label_dot'].bindings[0].value.literal: The binding 0 of 'bindings.label_dot' gives '.', which makes the segment of the path parameter 'label' of GET /styled/{label}/{reserved}/{file}.json a dot segment
--client-protocols['bindings.path_null'].bindings[0].target: The binding 0 of 'bindings.path_null' can give null, which cannot be written to the path parameter 'reserved' of GET /styled/{label}/{reserved}/{file}.json
--client-protocols['bindings.joined_dots'].bindings[0].value.literal: The binding 0 of 'bindings.joined_dots' gives '.', which makes the segment of the path parameter 'first' of GET /joined/{first}{second} a dot segment
--client-protocols['write.optional_body'].continuation.write: The cursor of 'write.optional_body' writes the request body of POST /multi, which is optional and has no media type a call without one sends
--client-protocols['write.body_type'].continuation.write: The cursor of 'write.body_type' reads string values, which the property '/count' of the request body of POST /bodies does not accept
--client-protocols['body.form']: The pagination helper 'body.form' sends a request body other than JSON, which is not supported yet
--client-protocols['responses.twice'].operation: GET /twice must declare exactly one JSON success response for the pagination helper 'responses.twice'
--client-protocols['responses.hidden'].continuation.write: The cursor of 'responses.hidden' reads array values, which the query parameter 'cursor' of GET /hidden does not accept
--client-protocols['items.union'].items: The items pointer '/either/data' of 'items.union' reads through a union or map, which is not supported yet
--client-protocols['items.map'].items: The items pointer '/grouped/admins' of 'items.map' reads through a union or map, which is not supported yet
--client-protocols['items.absent'].items: The items pointer '/data/id' of 'items.absent' names no property of the GET /users response
--client-protocols['items.scalar'].items: The items pointer '/count' of 'items.scalar' selects no JSON array of the GET /users response
--client-protocols['items.unknown_schema'].item_schema: The item_schema '/components/schemas/Nobody' of 'items.unknown_schema' does not exist in its document
--client-protocols['items.other_schema'].item_schema: The item_schema '/components/schemas/Label' of 'items.other_schema' is not the item schema of the array its items pointer selects
--client-protocols['cursor.absent'].continuation.read: The cursor pointer '/nothing' of 'cursor.absent' names no property of the GET /users response
--client-protocols['cursor.header'].continuation.read: The cursor of 'cursor.header' reads the header 'X-Missing', which GET /users does not declare
--client-protocols['cursor.types'].continuation.write: The cursor of 'cursor.types' reads array, boolean, integer, number, or object values, which the query parameter 'cursor' of GET /users does not accept
--client-protocols['cursor.null'].continuation.end: The cursor of 'cursor.null' can read null, which no end condition covers
--client-protocols['cursor.null'].continuation.end[1].value: The end value 5 of 'cursor.null' is integer, which its cursor never reads
--client-protocols['cursor.number'].continuation.end[1].value: The end value 2.0 of 'cursor.number' is number, which its cursor never reads
--client-protocols['cursor.union'].continuation.write: The cursor of 'cursor.union' reads integer values, which the query parameter 'cursor' of GET /users does not accept
--client-protocols['cursor.union'].continuation.end: The cursor of 'cursor.union' can read null, which no end condition covers
--client-protocols['cursor.map'].continuation.read: The cursor pointer '/grouped/admins' of 'cursor.map' reads through a union or map, which is not supported yet
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.pagination.diagnostics -->

## Polling helpers

An enabled polling helper is generated at `client.protocols.<name>` on `Client` and `AsyncClient` alike. Its `start`
takes the `create` operation's parameters and its body as keywords, never field arguments, then `poll_options`,
and `options`, sends the create request, and returns a handle; the asyncio `start` is awaited:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.polling.helper -->
<!-- fmt: off -->

```python
    def start(
        self,
        *,
        body: models.JobRequest,
        media_type: Literal['application/json'] | None = None,
        poll_options: PollOptions | None = None,
        options: RequestOptions | None = None,
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
none refused before sending.

### States and results

`start` sends the create request once, resent only as shared retries allow. A status in `accepted_statuses` returns a
pending handle. A status in `immediate_result.statuses` returns a handle that already holds the result, read from the
create response at the immediate result's selector; its `wait` returns the result without sending, and its `status`
raises `ConfigurationError` with the reason `invalid_state`, since it has no poll. Any other success status raises
`ProtocolDataError` with a `StatusSelector` location, and an error status raises the operation's HTTP error.

A poll's `state` selector must read a value equal to one of the `pending`, `succeeded`, `failed`, or `cancelled` values,
JSON type included; a missing state raises `ProtocolDataError`, and any other value raises it with the reason `type`
when no declared state has its JSON type and `value` otherwise. Success is never inferred. A failed or cancelled
operation makes `wait` raise `ProtocolDataError` with the reason `operation_failed` or `operation_cancelled`, the last
poll's decoded data as `data` and its response as `info`, and every later `wait` raises it again without sending;
`status` returns that poll. After a success, `wait` returns the result: the value an `inline` result's selector reads
from the final poll, the response of the result `operation`, fetched once with its bindings, whose `previous` source is
the final poll, or None for `kind: none`. Later `status` and `wait` calls send nothing. A missing or null result in the
final poll raises `ProtocolDataError` and leaves the handle pending, so a later `status` or `wait` polls again and
raises the same error while the result stays missing; a missing or null immediate result fails `start`.

An error that settles nothing, such as a transport error, an HTTP error, a deadline, a cancellation, a limit, or a
`ProtocolDataError` of a poll, leaves the handle as it was, so a later `status` or `wait` polls again without a new
create request, and a failed result fetch is retried alone. A spent limit stays spent for the handle's session, though:
once `max_polls` is reached, every later poll raises `SessionLimitError` again before sending. `close()` or `aclose()`,
or leaving a `with` or `async with` block, stops only local polling; the remote operation goes on, and every later step
raises `ConfigurationError` with the reason `invalid_state`. Calling `status`, `wait`, `close`, or `aclose` while
another step runs raises it too; a block that ends with an error while another step runs leaves
the handle open and lets its own error propagate. `checkpoint()` and `cancel_remote()` are not steps: they run while
another thread or task is in `status` or `wait`, such as one sleeping until its next poll.

### Waits and server delays

The first poll waits until `interval` seconds after the create response's receipt, and each later one until the
interval after the last poll's; with `interval.retry_after_header`, a valid delay in that header, in seconds or as an
HTTP date, makes the wait longer, never shorter. The delay counts from the final response of a call, after any retries
inside it, so a retry's own delay is never added to the poll interval. A poll or a result fetch that fails with a
response, such as a `503` after its retries, sets the next wait from that response the same way before its error is
raised, and the next poll or fetch waits for it; the first result fetch is sent at once. `status` waits the same way.
An interval longer than `PollOptions.max_wait`, or not shorter than the session's `total_timeout`, could never be
waited out, so `start` raises `ConfigurationError` with the
`field_path` `("poll_options", "interval")` before sending the create request. A wait a server delay makes longer
than `max_wait`, or not shorter than the remaining budget derived from the session and call `total_timeout`, raises
`SessionLimitError` with the reason `wait` or `deadline`, the `required_wait`, and the `limit` before anything is sent;
the handle stays as it was. Async waits propagate native task cancellation unchanged. Once the client is
closed, every later `status` or `wait` that needs a poll or a result fetch raises `ConfigurationError` with the reason
`client_closed`. The handle's [checkpoint](#checkpoints-and-resume-of-operations) continues it after that
`SessionLimitError`.

### Limits and sessions

`start` and its handle are one session. The create call, every poll, and the result fetch are calls of their own, with
their own retries, total timeout, and idempotency key, and the session bounds all of them: a call's deadline is the
earlier of its own and the session's. Each limit comes from the call's options, then the helper's options in the client's
`helper_defaults`, then the default below:

| Limit | Default | None |
|---|---|---|
| `PollOptions.max_polls` | 1000 polls | Removes the limit |
| `PollOptions.interval` | The declared `interval.seconds`, 1 second by default | Not allowed |
| `PollOptions.max_wait` | 60 seconds | Removes the limit |
| `PollOptions.total_timeout` | 600 seconds from `start` | Removes the limit |

A poll past a limit raises `SessionLimitError` with the reason `polls` and the progress so far, before sending; the
handle's `checkpoint()` continues the operation. The options of `start` apply to
every call of the handle. They must not fix an idempotency key, and their extra headers and query must not name
a header or a query parameter a binding writes; `start` raises `ConfigurationError` before
sending, as it does for options of another type.

### Checkpoints and resume of operations

`handle.checkpoint()` returns plain JSON without sending, on `LroHandle` and `AsyncLroHandle` alike, also after
`close`: the server's values the next request writes, never an SDK envelope. While another thread or task runs
`status` or `wait`, it returns the handle as the last poll that settled left it, never waiting for a step. A settled
operation, one that failed, was cancelled, or holds its result, including one that completed at once, has nothing left
to continue: its `checkpoint()` raises `ConfigurationError`. The helper's
`resume(state, *, poll_options=None, options=None)` is not awaited, even on `AsyncClient`, and
returns the handle type `start` returns, in a session of its own, without sending: it never creates the operation
again. A pending handle's `status` or `wait` sends its first poll at once; a handle whose result fetch is due fetches it
once in `wait`, and its `status` raises `ConfigurationError`, since it holds no poll. A checkpoint does not keep a
server's delay, so a caller resuming after a `SessionLimitError` with the reason `wait` or `deadline` waits out its
`required_wait` itself before polling.

```python
import json
import time

from pkg.errors import SessionLimitError

with Client() as client:
    helper = client.protocols.jobs.run
    handle = helper.start(body=job)
    saved = json.dumps(handle.checkpoint())
    report = helper.resume(json.loads(saved)).wait()
    handle = helper.start(body=job)
    try:
        handle.wait()
    except SessionLimitError as error:
        if error.reason == "wait":
            time.sleep(error.required_wait)
        later = helper.resume(handle.checkpoint())
```

| Member | Value |
|---|---|
| `phase` | `"pending"`, or `"fetch"` when the result fetch is due |
| `bound` | While pending, the values the next poll writes, read from the create response or the last pending poll; while the fetch is due, the values it writes |
| `cancel` | While pending, the values a remote cancel writes |
| `seed` | While pending, the values the create response gave the result fetch's `initial` bindings |
| `expires_at` | The server's expiry an `expires_at` helper read, as `datetime.isoformat()` text, or null |

The create request, its body and idempotency key, polls, results, model objects, the session, its deadline, the
call's `options`, and anything its auth adds are never part of it. A resumed pending handle polls with the saved
values, and one whose fetch is due fetches the result with them. Polls count afresh against the resumed call's
`max_polls`, and the session's timeout and deadline start afresh. A literal binding sends the plan's value, never a
saved one.

`resume` checks the resumed call's options as `start` does, then the state, before returning:

| Rejected state | Exception |
|---|---|
| A value that is not JSON, or one that does not fit the helper: an unknown or missing member, an unknown phase, a due fetch of a helper without one, values of another count than the bindings, or an expiry `datetime.isoformat()` would spell differently | `ConfigurationError(field_path=("state",), reason="invalid_value")` |
| An expiry that has passed | `ConfigurationError(field_path=("state",), reason="expired")` |
| A dot segment (`.` or `..`) a saved value of the next poll or remote cancel would write to a path parameter | `ProtocolDataError`, as for a server's value |

Any other saved value, a result fetch's included, is checked and encoded when the request that writes it is built, as a server's value is, so a value that
cannot be sent, such as one with CR, LF, or NUL in a header, raises the call's encoding error from `status` or `wait`
before sending. A checkpoint holds no credential and binds to no auth; store it as the call's own data.

### Remote cancellation and expiry

A helper that declares `remote_cancel` generates a handle class of its own, a subclass of `LroHandle` or
`AsyncLroHandle`, which `start` and `resume` return. Only it has `cancel_remote()`, a coroutine on the asyncio handle,
which returns a `CancelReceipt[C]`, imported from `pkg.protocols`, where `C` is the cancel operation's response type;
helpers without `remote_cancel` have no such method:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.polling.handle -->
<!-- fmt: off -->

```python
class JobsTrackedHandle(LroHandle[models.Report, GetJobResponse]):
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
`status` or `wait` tells whether the operation was cancelled. It counts no poll, and an error leaves the handle as it
was. It runs while another thread or
task is in `status` or `wait`, so a waiting operation can be cancelled from elsewhere: it reads the values to send when
it starts, and the `wait` goes on until a poll tells it the operation settled. A settled operation raises
`ConfigurationError` with the reason `invalid_state`, and so does a closed handle, both without sending.
`close()` never sends the cancel request.

The concrete handle classes are not exported from `pkg.protocols`: use the type `start` and `resume` return, or
annotate a handle as `LroHandle[T, P]` or `AsyncLroHandle[T, P]`, which they subclass, without `cancel_remote`.

`expires_at` is a selector of the server's expiry of the operation, read once from the accepted create response: a
string giving an RFC 3339 date-time with an offset, where a leap second is the second after the one before it, or
an HTTP date, read as [`Retry-After` dates](#retries-and-operation-contracts) are. A missing, null, non-string, or unparsable value fails `start` with `ProtocolDataError` after the create
response, as a missing binding value does; the remote operation was created all the same, and nothing cancels it. The
expiry, in UTC, becomes the expiry of every checkpoint of the handle, so `resume` refuses them afterwards with
`ConfigurationError` with the reason `expired`; it does not stop a live handle from polling. Without `expires_at`,
checkpoints never expire, and no expiry is assumed. An immediate result has none.

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.polling.tracked -->
<!-- fmt: off -->

```json
{
  "jobs.tracked": {
    "kind": "polling",
    "create": "/paths/~1jobs/post",
    "accepted_statuses": [202],
    "poll": "/paths/~1jobs~1{jobId}/get",
    "bindings": [
      {
        "target": {"in": "path", "name": "jobId"},
        "value": {"source": "initial", "selector": {"from": "body", "pointer": "/id"}}
      }
    ],
    "state": {"from": "body", "pointer": "/status"},
    "pending": ["queued", "running"],
    "succeeded": ["done"],
    "failed": ["failed"],
    "cancelled": ["cancelled"],
    "result": {
      "kind": "inline",
      "selector": {"from": "body", "pointer": "/result"},
      "schema": {"pointer": "/components/schemas/Report"}
    },
    "remote_cancel": {
      "operation": "/paths/~1jobs~1{jobId}/delete",
      "bindings": [
        {
          "target": {"in": "path", "name": "jobId"},
          "value": {"source": "previous", "selector": {"from": "body", "pointer": "/id"}}
        },
        {"target": {"in": "header", "name": "X-Reason"}, "value": {"literal": "requested"}}
      ]
    },
    "expires_at": {"from": "body", "pointer": "/expires"}
  }
}
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
another's, fail. As for pagination, no binding of the poll or the result fetch writes
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
--client-protocols['checks.later'].accepted_statuses[1]: The accepted status 201 of 'checks.later' selects no success response of POST /jobs
--client-protocols['checks.later'].accepted_statuses[2]: The accepted status 404 of 'checks.later' selects no success response of POST /jobs
--client-protocols['checks.later'].expires_at: The expiry of 'checks.later' reads integer values, where only a string gives a date and time
--client-protocols['checks.later'].remote_cancel.operation: POST /archives must declare a success response for the remote cancel of 'checks.later'
--client-protocols['checks.poll'].poll: POST /exports must declare exactly one JSON success response for the polling helper 'checks.poll'
--client-protocols['checks.states'].pending[0]: The state 'zero' of 'checks.states' is string, which its state never reads
--client-protocols['checks.states'].failed[0]: The state True of 'checks.states' is boolean, which its state never reads
--client-protocols['checks.unread'].state: The state of 'checks.unread' reads the header 'X-State', which GET /exports/status does not declare
--client-protocols['checks.bindings'].bindings[0].target: The binding 0 of 'checks.bindings' writes the cookie 'session', which carries credentials no helper writes
--client-protocols['checks.bindings'].bindings[2].target: The binding 2 of 'checks.bindings' writes the same target as its binding 1
--client-protocols['checks.bindings'].bindings[3].value.source: The binding 3 of 'checks.bindings' reads the helper's input, which is not supported yet
--client-protocols['checks.bindings'].bindings[4].value.selector: The binding 4 pointer '/nothing' of 'checks.bindings' names no property of the POST /jobs response
--client-protocols['checks.bindings'].bindings[5].target: The binding 5 of 'checks.bindings' gives integer values, which the header parameter 'X-Trace' of GET /jobs/{jobId} does not accept
--client-protocols['checks.dots'].bindings[0].value.literal: The binding 0 of 'checks.dots' gives '..', which makes the segment of the path parameter 'jobId' of GET /jobs/{jobId} a dot segment
--client-protocols['checks.bodyless'].bindings[0].value.selector: The binding 0 of 'checks.bodyless' reads the body of the 202 response of POST /exports, which is no JSON model
--client-protocols['checks.required'].bindings: GET /exports/find requires the querystring parameter 'filter', which no binding of 'checks.required' writes
--client-protocols['checks.required'].result.bindings: POST /reports requires a request body, which no binding of 'checks.required' writes
--client-protocols['checks.unwritten'].bindings: GET /jobs/{jobId} requires the path parameter 'jobId', which no binding of 'checks.unwritten' writes
--client-protocols['checks.unwritten'].result.bindings: The polling helper 'checks.unwritten' writes a request body of POST /notes other than JSON, which is not supported yet
--client-protocols['checks.overlaps'].result.bindings[1].target: The result binding 1 of 'checks.overlaps' writes a target overlapping that of its result binding 0
--client-protocols['checks.fetch'].result.operation: POST /exports must declare exactly one JSON success response for the result of 'checks.fetch'
--client-protocols['checks.header'].result.selector: The result of 'checks.header' reads a header, where only a body pointer reads a result
--client-protocols['checks.union'].result.selector: The result pointer '/labels/rows' of 'checks.union' reads through a union or map, which is not supported yet
--client-protocols['checks.absent'].result.selector: The result pointer '/nothing' of 'checks.absent' names no property of the GET /jobs/{jobId} response
--client-protocols['checks.missing_schema'].result.schema: The result schema '/components/schemas/Missing' of 'checks.missing_schema' does not exist in its document
--client-protocols['checks.other_schema'].result.schema: The result schema '/components/schemas/Report' of 'checks.other_schema' is not the schema its pointer reads
--client-protocols['checks.none_immediate'].immediate_result: The immediate result of 'checks.none_immediate' gives a result, which a helper without a result never has
--client-protocols['checks.immediate'].immediate_result.statuses[0]: The immediate status 201 of 'checks.immediate' selects no success response of POST /jobs
--client-protocols['checks.immediate'].immediate_result.selector: The immediate result of 'checks.immediate' reads models.ExportState, which is not its result type models.Report
--client-protocols['checks.immediate_models'].immediate_result.statuses[0]: The immediate status 200 of 'checks.immediate_models' selects no success response of POST /exports
--client-protocols['checks.immediate_models'].immediate_result: The immediate result of 'checks.immediate_models' reads POST /exports, whose success responses are not all one JSON model, which is not supported yet
--client-protocols['checks.immediate_header'].immediate_result.selector: The immediate result of 'checks.immediate_header' reads a header, where only a body pointer reads a result
--client-protocols['checks.progress'].immediate_result.selector: The immediate result of 'checks.progress' reads models.Report, which is not its result type int
--client-protocols['checks.credentials'].bindings[1].target: The binding 1 of 'checks.credentials' writes the header 'x-api-key', which carries credentials no helper writes
--client-protocols['checks.credentials'].bindings[2].target: The binding 2 of 'checks.credentials' writes the query parameter 'api_key', which carries credentials no helper writes
--client-protocols['checks.credentials'].result.bindings[0].target: The result binding 0 of 'checks.credentials' writes the query field 'api_key', which carries credentials no helper writes
--client-protocols['checks.no_success'].accepted_statuses[0]: The accepted status 202 of 'checks.no_success' selects no success response of POST /archives
--client-protocols['checks.no_success'].immediate_result.statuses[0]: The immediate status 200 of 'checks.no_success' selects no success response of POST /archives
--client-protocols['checks.no_success'].immediate_result: The immediate result of 'checks.no_success' reads POST /archives, whose success responses are not all one JSON model, which is not supported yet
--client-protocols['checks.media'].result.bindings: The polling helper 'checks.media' writes a request body of POST /archives/search, which has no default media type; set the operation's request_media_type
--client-protocols['checks.cancel'].expires_at: The expiry of 'checks.cancel' reads the header 'Expires', which POST /jobs does not declare
--client-protocols['checks.cancel'].remote_cancel.bindings[0].target: The cancel binding 0 of 'checks.cancel' gives integer values, which the header parameter 'X-Reason' of DELETE /jobs/{jobId} does not accept
--client-protocols['checks.cancel'].remote_cancel.bindings: DELETE /jobs/{jobId} requires the path parameter 'jobId', which no binding of 'checks.cancel' writes
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.polling.diagnostics -->

## Upload helpers

An enabled `resumable_upload` helper of the `offset` profile is generated at `client.protocols.<name>` on `Client`
and `AsyncClient` alike. Its `start` takes the upload source, then the `create` operation's parameters and its body as
keywords, never field arguments, without the parameter the helper writes the content's size to, then
`upload_options` and `options`; `resume` takes the source and a checkpoint. Both are coroutines on
`AsyncClient`:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.uploads.helper -->
<!-- fmt: off -->

```python
    def start(
        self,
        source: UploadSource,
        *,
        tus_resumable: str,
        x_name: str | UNSET = UNSET,
        upload_options: UploadOptions | None = None,
        options: RequestOptions | None = None,
    ) -> UploadHandle[None]:
        """Measure the source, create the upload of POST /files, and return its handle."""
        return start_upload(
            self._core,
            _plans.PLAN_0,
            source,
            (tus_resumable, x_name),
            upload_options=upload_options,
            options=options,
        )

    def resume(
        self,
        source: UploadSource,
        state: JSONValue,
        *,
        upload_options: UploadOptions | None = None,
        options: RequestOptions | None = None,
    ) -> UploadHandle[None]:
        """Check a checkpoint and the source size, then continue from the server offset."""
        return resume_upload(
            self._core,
            _plans.PLAN_0,
            source,
            state,
            upload_options=upload_options,
            options=options,
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
complete, `advance` returns the progress and `run` the kept result without sending. `checkpoint()` returns plain JSON
at any time before the upload completes, even while another thread or task steps the handle, and sends
nothing; a complete upload has nothing to resume, so its `checkpoint()` raises `ConfigurationError` with the reason
`invalid_state`.

### Sources

A source is `bytes`, a `bytearray`, a `memoryview`, or a seekable binary file, such as one `open(path, "rb")` returns
or an `io.BytesIO`; `UploadSource` names that union. `Client` and `AsyncClient` take the same sources. A file's content
runs from its position when `start` or `resume` is called to its end, and the call measures its size by seeking to
the end, without reading it. A file that cannot seek, an iterable, an iterator, or an asyncio stream raises
`ConfigurationError` with `field_path=("source",)` and the reason `wrong_capability` before anything is sent; send it
as an ordinary upload. A file whose `read`, `seek`, or `tell` gives anything but bytes and integers, such as a text
file or an asyncio file, raises it too. Sources are borrowed and never closed by the client,
and nothing is ever spooled to disk.

Each append seeks to the confirmed offset and reads the rest of its chunk into one buffer, synchronously on both
clients; the last chunk is read with one byte more. Content shorter or longer than the size measured at `start` raises
`ConfigurationError` with `field_path=("source",)` and the reason `source_changed`, and the handle sends nothing more:
every later step raises `ConfigurationError` with the reason `invalid_state`, though `checkpoint()` still works. The
content is not hashed, so a change that keeps its size is not found: keep it unchanged while it is uploaded. The body is
sent from that buffer in 64 KiB slices, so an upload holds one chunk at a time besides fixed read buffers.

### Chunks, offsets, and recovery

A chunk is `UploadOptions.chunk_bytes`, 8 MiB by default, or the helper's smaller `max_chunk_bytes`. The offset
profile sends one chunk at a time.

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
| A transport error after it may have been sent | One probe, retried only as shared retries allow: an unchanged offset sends the range again as a new call, the chunk's end confirms it, and an offset inside it confirms its bytes before it only with `partial_commit: allowed`, the rest being sent next |
| A probe that fails with a transport error or an error status | `APIConnectionError` with the reason `delivery_unknown` and the probe's failure named in its notes; the handle's `checkpoint()` resumes it |
| Any other failure, a cancellation included | Raised; the next step probes the server's offset before it appends |

A probe's offset is a JSON integer, or a header of ASCII digits; a missing one raises `ProtocolDataError` with the
reason `missing`, a header repeated with `malformed`, and anything else with `type`, `null`, or `value`. An offset
below the confirmed one, past the content, past the chunk sent, or inside a chunk with `partial_commit: forbidden`
raises `ProtocolDataError` with the reason `inconsistent` and the remote offset's selector as `location`. A handle
`start` returned also refuses an offset past every byte it has sent; a handle `resume` returned cannot know what an
earlier session sent, so it accepts any offset up to the size.

An upload completed by `length` completes when the server holds every byte. A completion `operation` is sent once with
its bindings; its response is the result. A completion whose outcome is unknown is never sent again: it raises
`APIConnectionError` with the reason `delivery_unknown`, and so does every later step and every resume of its
checkpoint, without sending. That includes a completion answered with 502 or 504, which a gateway gives even when the
server behind it may have applied the completion. A completion answered with any other error status, such as a 503 with
`Retry-After`, did not apply, so the next step may send it again.

`close()` or `aclose()`, or leaving a `with` or `async with` block, stops only local uploading; the remote upload
stays, and every later step raises `ConfigurationError` with the reason `invalid_state`. Calling `advance`, `run`,
`close`, or `aclose` while another step runs raises it too.

### Checkpoints and resume

`checkpoint()` returns plain JSON: an object with the content's `size`, the `chunk` size, the `confirmed` offset, the
values the probe, the append, and the completion write (`bound`, three arrays), a completion of unknown outcome
(`phase` `unknown`, otherwise `uploading`), and the server's `expires_at`. It holds
no content, digest, result, credential, or security binding: a resumed upload sends with the resuming client's own
auth, as pagination checkpoints do. Errors carry no checkpoint; call the handle's `checkpoint()`.

`resume(source, state)` starts a new session and never creates the upload again. It checks the call's options, then the
checkpoint: a value that is not JSON or does not fit the helper raises `ConfigurationError(field_path=("state",))`, and
one past the server's expiry the reason `expired`. A checkpoint keeps its chunk size, which must not exceed the call's
`UploadOptions.chunk_bytes`, since an append holds one chunk in memory; it raises `ConfigurationError` with the option's
`field_path`. Then the saved values each call writes: a value that makes a path segment `.` or `..` raises
`ProtocolDataError`, as if a server gave it; any other saved value is encoded when its request is built. Then the
source: a one-shot input raises `ConfigurationError` with the reason `wrong_capability`, and a size other than the
checkpoint's the reason `source_changed`; the source is not read. A completion of unknown outcome raises
`APIConnectionError` with the reason `delivery_unknown` again without sending. Otherwise it probes the server's offset
once, which must not be below the saved one, and returns the handle.

A server's expiry is read once from the create response at `create.expires_at`, an RFC 3339 date-time with an offset or
an HTTP date; a missing, null, or unparsable value raises `ProtocolDataError` from `start`. It bounds only checkpoints
once it has passed: `resume` raises `ConfigurationError` with the reason `expired`; uploading goes on until the server
refuses.

### Limits and sessions

`start` and its handle are one session, and so are `resume` and its handle. The create call, every probe, every append,
and the completion are calls of their own, with their own retries, total timeout, and idempotency key, and the session
bounds all of them. Each limit comes from the call's options, then the helper's options in the client's
`helper_defaults`, then the default below:

| Limit | Default | None |
|---|---|---|
| `UploadOptions.chunk_bytes` | 8 MiB, at most the helper's `max_chunk_bytes` | Not allowed |
| `UploadOptions.total_timeout` | No limit | Removes the limit |

By default, an upload session has no total lifetime limit. If a finite `total_timeout` is configured, it runs from
`start` or `resume` for the whole life of the handle. A long upload stepped slowly may then need a larger timeout,
`UploadOptions(total_timeout=None)`, or a `resume` from a checkpoint, which starts a new session. The options must
not fix an idempotency key, and their extra headers and query must not name a header or a query parameter the helper
writes, the size included; `start` and `resume` raise `ConfigurationError` before reading or
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
--client-protocols['checks.later'].abort: The resumable_upload helper 'checks.later' declares remote abort, which is not supported yet
--client-protocols['checks.later'].create.session_url: The resumable_upload helper 'checks.later' declares a session URL, which is not supported yet
--client-protocols['checks.created'].create.operation: POST /drafts must declare a success response for the upload helper 'checks.created'
--client-protocols['checks.size'].create.size: The size of 'checks.size' writes the cookie 'session', which carries credentials no helper writes
--client-protocols['checks.size_body'].create.size: The size of 'checks.size_body' writes 'body', where only a path, query, or header parameter takes it
--client-protocols['checks.size_body'].probe.bindings[0].value.selector: The probe binding 0 pointer '/id' of 'checks.size_body' names no property of the POST /files/{fileId}/status response
--client-protocols['checks.size_body'].append.bindings[0].value.selector: The append binding 0 pointer '/id' of 'checks.size_body' names no property of the POST /files/{fileId}/status response
--client-protocols['checks.size_type'].create.size: The size of 'checks.size_type' gives integer values, which the header parameter 'X-Name' of POST /files does not accept
--client-protocols['checks.expiry_status'].create.expires_at: The server expiry of 'checks.expiry_status' reads the status, which gives no server expiry
--client-protocols['checks.expiry_type'].create.expires_at: The server expiry of 'checks.expiry_type' reads integer values, where only string values fit
--client-protocols['checks.expiry_header'].create.expires_at: The server expiry of 'checks.expiry_header' reads the header 'X-Expires', which POST /files does not declare
--client-protocols['checks.probe_sources'].probe.bindings[0].value.source: The probe binding 0 of 'checks.probe_sources' reads the previous response, which is not supported yet
--client-protocols['checks.probe_required'].probe.bindings: HEAD /files/{fileId} requires the path parameter 'fileId', which no binding of 'checks.probe_required' writes
--client-protocols['checks.probe_body'].probe.bindings: POST /files/{fileId}/status requires a request body, which no binding of 'checks.probe_body' writes
--client-protocols['checks.probe_form'].probe.bindings: The upload helper 'checks.probe_form' writes a request body of POST /files/{fileId}/form other than JSON, which is not supported yet
--client-protocols['checks.probe_media'].probe.bindings: The upload helper 'checks.probe_media' writes a request body of POST /files/{fileId}/search, which has no default media type; set the operation's request_media_type
--client-protocols['checks.probe_none'].probe.operation: DELETE /files/{fileId}/gone must declare a success response for the probe of 'checks.probe_none'
--client-protocols['checks.offset_type'].probe.remote_offset: The remote offset of 'checks.offset_type' reads string values, where only integer values fit
--client-protocols['checks.offset_status'].probe.remote_offset: The remote offset of 'checks.offset_status' reads the status, which gives no remote offset
--client-protocols['checks.append_media'].append.operation: PUT /files/{fileId} must take exactly one binary request media for the chunks of 'checks.append_media'
--client-protocols['checks.append_none'].append.operation: DELETE /files/{fileId}/gone must take exactly one binary request media for the chunks of 'checks.append_none'
--client-protocols['checks.append_none'].append.operation: DELETE /files/{fileId}/gone must declare a success response for the appends of 'checks.append_none'
--client-protocols['checks.append_none'].append.offset: The chunk offset of 'checks.append_none' writes the same target as its append binding 0
--client-protocols['checks.append_overlap'].append.offset: The chunk offset of 'checks.append_overlap' writes the same target as its append binding 1
--client-protocols['checks.append_length'].append.length: The chunk length of 'checks.append_length' writes the same target as its chunk offset
--client-protocols['checks.append_places'].append.offset: The chunk offset of 'checks.append_places' writes the cookie 'session', which carries credentials no helper writes
--client-protocols['checks.append_places'].append.length: The chunk length of 'checks.append_places' writes 'body', where only a path, query, or header parameter takes it
--client-protocols['checks.append_places'].append.bindings: PATCH /files/{fileId} requires the header parameter 'Upload-Offset', which no binding of 'checks.append_places' writes
--client-protocols['checks.append_types'].append.offset: The chunk offset of 'checks.append_types' gives integer values, which the header parameter 'X-Note' of PATCH /files/{fileId} does not accept
--client-protocols['checks.checksum_overlap'].append.checksum.target: The chunk checksum of 'checks.checksum_overlap' writes the same target as its chunk offset
--client-protocols['checks.checksum_place'].append.checksum.target: The chunk checksum of 'checks.checksum_place' writes the cookie 'session', which carries credentials no helper writes
--client-protocols['checks.checksum_type'].append.checksum.target: The chunk checksum of 'checks.checksum_type' gives string values, which the header parameter 'X-Count' of PATCH /files/{fileId} does not accept
--client-protocols['checks.append_body'].append.bindings[1].target: The append binding 1 of 'checks.append_body' writes the request body, which is the chunk
--client-protocols['checks.completion_media'].completion.operation: POST /files/{fileId}/finish must declare exactly one JSON success response for the completion of 'checks.completion_media'
--client-protocols['checks.completion_missing'].completion.result_schema: The result schema '/components/schemas/Missing' of 'checks.completion_missing' does not exist in its document
--client-protocols['checks.completion_other'].completion.result_schema: The result schema '/components/schemas/Note' of 'checks.completion_other' is not the schema of the POST /files/{fileId}/complete response
--client-protocols['checks.completion_body'].completion.bindings: POST /files/{fileId}/complete requires a request body, which no binding of 'checks.completion_body' writes
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.uploads.diagnostics -->

## SSE stream helpers

An enabled `sse` helper is generated at `client.protocols.<name>` on `Client` and `AsyncClient` alike, with one method,
`open`. It takes the operation's parameters and its body as keywords, never field arguments, then `stream_options`,
and `options`, sends the operation in a session of its own, and returns an `EventStream[T]`, or with
one `await` an `AsyncEventStream[T]`, once the response is a declared success of the helper's media type:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.streams.helper -->
<!-- fmt: off -->

```python
    def open(
        self,
        *,
        topic: str | UNSET = UNSET,
        last_event_id: str | UNSET = UNSET,
        stream_options: StreamOptions | None = None,
        options: RequestOptions | None = None,
    ) -> EventStream[models.Message]:
        """Open the event stream of GET /events, returning once its response is a declared success."""
        return open_events(
            self._core,
            _plans.STREAM_0,
            (topic, last_event_id),
            stream_options=stream_options,
            options=options,
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

The response's status and `Content-Type` are checked before `open` returns: an error status raises the `APIStatusError`
subclass of its status, an undeclared status `APIStatusError` with the reason `unexpected_status`, and a body of another
media type, or none, `DecodeError` with the reason `unexpected_media_type`. Its acquisition retries like any call. A
stream then reads only the bytes its next event needs and owns the response until it ends, fails, or closes: close it
with `with`, `async with`, `close()`, or `aclose()`, since leaving a loop early releases nothing. One consumer reads a
stream at a time: a step, or a close, while another step runs raises `ConfigurationError` with the reason
`invalid_state`, and so does every step after a failure or `close()`; after its end every step stops. Streams own their
native response until it ends or the caller closes it. Close each stream explicitly before closing an owning root; a
borrowed native client retains its caller's lifetime.

### Framing and events

HTTPX2's `EventSource` parses the body as the WHATWG event-stream interpretation reads it: CR, LF, and CRLF end lines,
including a CRLF split between reads, lines starting with `:` are comments, `data` lines join with LF, `event` names
the type, an `id` containing NUL is ignored, and a blank line dispatches an event. The body is decoded as UTF-8 without
a leading byte order mark, whatever charset it declares, and invalid UTF-8 decodes to replacement characters. An event
without data is not delivered, though its `id` and `retry` still count; a `retry` that is negative or of more than 18
digits is ignored. An event over HTTPX2's event size limit, 1 MiB, raises `ProtocolDataError` with the reason
`too_large` and HTTPX2's `SSEError` as its `cause`. Each event's data is JSON converted to its schema's type through its
converter; data that is not JSON, or that the type refuses, raises `DecodeError` with the `location`
`("event", sequence)`, or `("event", sequence, pointer)` where a member is at fault, and at most 64 KiB of the data as
`body_bytes`.

An event schema mapping is chosen by the event's SSE type with `discriminator: {from: event_type}`, or by the string a
body pointer reads from the JSON data with `{from: body, pointer}`; a missing, null, or non-string body discriminator,
and an unmapped one without `unknown: raw`, raise `DecodeError`. An `error_events` key raises `ProtocolDataError` with
the reason `error_event` and the error schema's decoded `data` instead of yielding the event.

The stream ends as its `completion` declares: at the end of the body for `eof`, or at the event whose raw data equals a
`sentinel` value or whose SSE type is the `event_type` value; neither terminal event is decoded or yielded, and the
response is released. A body that ends before a sentinel or terminal type raises `StreamInterruptedError` with the
reason `eof`; an event the body ends in the middle of is discarded, as the event-stream interpretation discards it,
so under `eof` the stream simply ends and `incomplete_eof` cannot trigger a reconnect there. A connection that breaks
while the body is read raises `StreamInterruptedError` with the reason `transport` and the transport failure as its
`cause`. `sequence` is the last event delivered.

### Stream limits and sessions

`open` is one session holding one logical call. An optional total timeout bounds attempts while acquiring the
response. Native read timeouts bound idle I/O while reading the stream, and a helper session's optional total timeout
is checked before the next step. Decoding and cleanup preserve fully received results after a request budget expires.
Each limit comes from the call's options, then the helper's options in the client's `helper_defaults`, then the
default below:

| Limit | Default | None |
|---|---|---|
| `StreamOptions.idle_timeout` | The native read timeout | No idle limit |
| `StreamOptions.total_timeout` | None | No session deadline |
| `StreamOptions.reconnect` | False | Not allowed |
| `StreamOptions.max_reconnects` | 5 reconnections, counted across resumes; 0 allows none | Removes the limit |
| `StreamOptions.max_reconnect_wait` | 60 seconds | No wait limit |

Only a helper that declares `resume` reconnects: for any other, `StreamOptions(reconnect=True)` raises
`ConfigurationError` with the reason `missing_metadata`. Options of another type raise `ConfigurationError`.

### Checkpoints, resume, and reconnection

A helper whose metadata enables `resume` names the cursor of its events, `cursor: event_id` for the SSE event ID or a
`{from: body, pointer}` selector for a value in each event's JSON data, the request target its reopen writes it to, the
`reopen_operation`, and `delivery: at_least_once`. A body cursor also says what a missing value does, `missing:
inherit` keeping the cursor before or `error`, and what null does, `null: clear` or `error`. `bindings` write values
into the reopen request, read from the open response for `initial` or the latest open or reopen response for
`previous`, and `expires_at` reads the server's expiry from a header of the open response:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.streams.resume-json -->
<!-- fmt: off -->

```json
{
  "events.live": {
    "kind": "sse",
    "operation": "/paths/~1events/get",
    "media": "text/event-stream",
    "event_schema": {"pointer": "/components/schemas/Message"},
    "completion": {"kind": "eof"},
    "resume": {
      "enabled": true,
      "cursor": "event_id",
      "write": {"in": "header", "name": "Last-Event-ID"},
      "reopen_operation": "/paths/~1events/get",
      "delivery": "at_least_once"
    }
  },
  "events.tracked": {
    "kind": "sse",
    "operation": "/paths/~1events/get",
    "media": "text/event-stream",
    "event_schema": {
      "discriminator": {"from": "event_type"},
      "mapping": {"created": {"pointer": "/components/schemas/Created"}}
    },
    "unknown": "raw",
    "error_events": {"error": {"pointer": "/components/schemas/StreamError"}},
    "completion": {"kind": "event_type", "value": "done"},
    "resume": {
      "enabled": true,
      "cursor": "event_id",
      "write": {"in": "header", "name": "Last-Event-ID"},
      "reopen_operation": "/paths/~1streams~1{streamId}/get",
      "delivery": "at_least_once",
      "bindings": [
        {
          "target": {"in": "path", "name": "streamId"},
          "value": {"source": "initial", "selector": {"from": "header", "name": "X-Stream-Id"}}
        },
        {
          "target": {"in": "query", "name": "token"},
          "value": {"source": "previous", "selector": {"from": "header", "name": "X-Resume-Token"}}
        },
        {"target": {"in": "query", "name": "mode"}, "value": {"literal": "resume"}}
      ],
      "reconnect_on": ["transport_interruption", "incomplete_eof"],
      "expires_at": {"from": "header", "name": "X-Stream-Expires"}
    }
  }
}
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.streams.resume-json -->

The helper then also has `resume`, which takes a checkpoint and the options `open` takes; when the reopen operation is
the helper's own, it also takes the operation's parameters and body again, as pagination's `resume` does. The asyncio
one is awaited once and returns the stream:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.streams.resume -->
<!-- fmt: off -->

```python
    def resume(
        self,
        state: JSONValue,
        *,
        criteria: dict[str, str] | UNSET = UNSET,
        body: models.FeedQuery,
        media_type: Literal['application/json'] | None = None,
        stream_options: StreamOptions | None = None,
        options: RequestOptions | None = None,
    ) -> EventStream[models.Tick]:
        """Reopen the event stream after a checkpoint's cursor, returning once its response is a declared success."""
        return resume_events(
            self._core,
            _plans.STREAM_0,
            state,
            (criteria,),
            body=body,
            media_type=media_type,
            stream_options=stream_options,
            options=options,
        )
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.streams.resume -->

```python
with Client() as client, client.protocols.events.live.open() as stream:
    for event in stream:
        handle(event)
        save(json.dumps(stream.checkpoint()))
with Client() as client, client.protocols.events.live.resume(json.loads(load())) as stream:
    for event in stream:
        handle(event)
```

The cursor is that of the last event the stream returned, never of a terminal, error, refused, or cut event: an SSE
event without an `id` keeps the ID before, an empty `id` clears it, and a body cursor follows its `missing` and `null`
settings, raising `DecodeError` with the reason `missing` or `null` before the event under `error`. HTTPX2
reports an empty `id` and no `id` alike until a response sets one, so a reopened response's events keep the ID it
reopened after until the response sends a nonempty one. An unknown event kept raw gives its body cursor from its data
when the data is JSON. A cursor is never limited apart from its event.

`checkpoint()` sends nothing and works on an open, ended, failed, or closed stream once a cursor was delivered; before
that, and while another step runs, it raises `ConfigurationError` with the reason `invalid_state`, and on a stream of a
helper without resume metadata `ConfigurationError` with the reason `missing_metadata`. It returns plain JSON, an object
with the `cursor`, the `bound` values of the bindings, and the server's `expires_at` as ISO 8601 text or null, never the
caller's arguments, events, counts, `retry` times, responses, the session, the call's options, or what its auth adds.
Like pagination and polling checkpoints, it holds no credential and binds to no auth; store it as the call's own data.
Errors carry no checkpoint: call the stream's `checkpoint()`.

`resume` creates a session of its own and sends the reopen at once: the helper's own operation sends the arguments and
body the caller gives `resume`, another one sends only what is written, each binding's value first and then the cursor,
whose parameter is omitted once the cursor is cleared, so a cleared SSE cursor sends no `Last-Event-ID`. The request
then returns once its response is a declared success, as `open` does; sequences, reconnections, and the session's
deadline start afresh, and the reopen counts as no reconnection. Before sending it refuses a checkpoint that is not JSON
or does not fit the helper, such as a missing or extra member, an empty event ID, or an expiry of another form, with
`ConfigurationError(field_path=("state",))`, and an expired one with `ConfigurationError(field_path=("state",),
reason="expired")`. The cursor and the bindings' values are written as a server's are: a saved dot segment for a path
parameter raises `ProtocolDataError`, a value the reopen would send as a credential header or a security scheme's query
field, including a property of an exploded form or deepObject query parameter, raises `ConfigurationError` with the
reason `wrong_capability`, and one the request cannot encode, such as an event ID ending in a space, raises the
request's `DecodeError`.

With `StreamOptions(reconnect=True)`, a stream that has delivered a cursor reopens itself within the same step after a
read-phase transport failure the shared retry classification retries, or a read timeout the call's own `timeout`
set rather than the stream's idle limit, which wins a tie; after an NDJSON line cut by the
end of the body or an end before the declared completion it does so only when `reconnect_on` lists `incomplete_eof`.
Each reopen is one more child call of the stream's session: its own retries, Retry-After included, follow the call's
retry options. Before a reopen the stream waits the retry backoff, and at least the last `retry` time the server sent,
in the session's deadline. When the backoff's cap or that `retry` time is longer than `max_reconnect_wait`, whatever the
jitter draws below the cap, or the wait is not shorter than the session's remaining time, the wait is not begun and the
interruption is raised. Running out of reconnections raises `SessionLimitError` with the reason `reconnects`,
never a normal end. A cursor a reconnection cannot encode raises `ProtocolDataError` with the reason `value` and the
cursor's selector, or for an event ID the target it is written to, as `location`, with the interruption as its context,
and a value it would send as a credential raises `ConfigurationError` with the reason `wrong_capability`. A decode,
size, remote, idle, or deadline failure, the declared end, a reopen answered with an error, and closing never reconnect.
Events the server sends again after a reopen are delivered again, numbered on: nothing removes duplicates. Every open
and reopen is its own request of the HTTP client, which its `event_hooks` see. Neither the headers and query of the
client, a view, or the call may name a header or query parameter a resuming helper's reopen writes, nor the call fix
an idempotency key.

### Stream generation checks

The helper's `media` must be `text/event-stream`, compared without case and with any parameters allowed, and a success
response of its operation must declare an event stream media type, which the helper then requests as declared. Each
event and error schema must exist. A body discriminator must name a declared property of each mapped and error schema
whose values can be strings, and an `event_type` completion cannot be a key of the event type mapping or the error
events:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.streams.diagnostics -->
<!-- fmt: off -->

```text
--client-protocols['checks.media'].media: The media type 'text/plain' of 'checks.media' is not text/event-stream
--client-protocols['checks.response'].operation: GET /status declares no text/event-stream success response for the SSE helper 'checks.response'
--client-protocols['checks.schema'].event_schema: The schema '/components/schemas/Nobody' of 'checks.schema' does not exist in its document
--client-protocols['checks.terminal_event'].completion.value: The completion event type 'done' of 'checks.terminal_event' is also a key of its event_schema.mapping
--client-protocols['checks.terminal_error'].completion.value: The completion event type 'stop' of 'checks.terminal_error' is also a key of its error_events
--client-protocols['checks.discriminator_absent'].event_schema.discriminator.pointer: The discriminator pointer '/kind' of 'checks.discriminator_absent' names no property of '/components/schemas/Created'
--client-protocols['checks.discriminator_type'].event_schema.discriminator.pointer: The discriminator of 'checks.discriminator_type' reads integer values from '/components/schemas/Counted', where only string values fit
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
--client-protocols['checks.cursor_header'].resume.cursor: The cursor of 'checks.cursor_header' reads a header, where only a body pointer reads an event's cursor
--client-protocols['checks.cursor_absent'].resume.cursor.pointer: The cursor pointer '/id' of 'checks.cursor_absent' names no property of any event schema
--client-protocols['checks.cursor_required'].resume.cursor.pointer: The cursor pointer '/seq' of 'checks.cursor_required' names no property of '/components/schemas/Created'
--client-protocols['checks.cursor_types'].resume.write: The cursor of 'checks.cursor_types' reads integer values, which the header parameter 'Last-Event-ID' of GET /events does not accept
--client-protocols['checks.cursor_cookie'].resume.write: The cursor of 'checks.cursor_cookie' writes the cookie 'session', which carries credentials no helper writes
--client-protocols['checks.cursor_path'].resume.write: The cursor of 'checks.cursor_path' is written to the path parameter 'streamId' of GET /streams/{streamId}, which is not supported yet
--client-protocols['checks.cleared_body'].resume.write: The cursor of 'checks.cleared_body' can be cleared, which only a header or query parameter can omit; writing it to the request body of POST /feed is not supported yet
--client-protocols['checks.cleared_required'].resume.write: The cursor of 'checks.cleared_required' can be cleared, which the query parameter 'from' of GET /replay, a required one, cannot omit
--client-protocols['checks.reopen_media'].resume.reopen_operation: GET /records declares no text/event-stream success response for the SSE helper 'checks.reopen_media' to reopen its stream with
--client-protocols['checks.bindings'].resume.bindings[0].value.selector: The binding 0 of 'checks.bindings' reads the body of the 200 response of GET /events, which is no JSON model
--client-protocols['checks.bindings'].resume.bindings[1].value.source: The binding 1 of 'checks.bindings' reads the helper's input, which is not supported yet
--client-protocols['checks.bindings'].resume.bindings[2].target: The binding 2 of 'checks.bindings' writes the same target as its cursor
--client-protocols['checks.bindings'].resume.bindings[3].value.selector: The binding 3 of 'checks.bindings' reads the header 'X-Stream-Id', which GET /streams/{streamId} does not declare
--client-protocols['checks.bindings'].resume.expires_at: The expiry of 'checks.bindings' reads a body, where only a header of a stream gives one
--client-protocols['checks.expiry'].resume.expires_at: The expiry of 'checks.expiry' reads the header 'Expires', which GET /events does not declare
--client-protocols['checks.cursor_exploded'].resume.write: The cursor of 'checks.cursor_exploded' writes the query field 'api_key', which carries credentials no helper writes
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
        topic: str | UNSET = UNSET,
        stream_options: StreamOptions | None = None,
        options: RequestOptions | None = None,
    ) -> EventStream[models.Record]:
        """Open the NDJSON stream of GET /records, returning once its response is a declared success."""
        return open_events(
            self._core,
            _plans.STREAM_0,
            (topic,),
            stream_options=stream_options,
            options=options,
        )
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.ndjson.helper -->

```python
with Client() as client, client.protocols.records.all.open() as stream:
    for record in stream.data():
        print(record.text)
```

```json
{
  "records.all": {
    "kind": "ndjson",
    "operation": "/paths/~1records/get",
    "media": "application/x-ndjson",
    "event_schema": {"pointer": "/components/schemas/Record"},
    "completion": {"kind": "eof"},
    "final_line": "require_newline"
  }
}
```

### Records

LF ends a line, and so does CRLF, whose CR is not part of the record; a CR alone does not end one. Each line is one
record, strict UTF-8 JSON decoded by the helper's schema, or by the schema a `{from: body, pointer}` discriminator maps
it to, the only discriminator NDJSON has. Every line is a record, so a blank or whitespace-only line, one that is not
UTF-8, a leading byte order mark, and anything else that is not JSON raise `DecodeError` with the reason
`malformed` and the `location` `("event", sequence)`; none is skipped. The error keeps at most the event limit or 64 KiB
of the line as `body_bytes`, which no message shows, and a line that is not UTF-8 has no `cause`.

A record yields a `StreamEvent` whose `event_type` is the empty string and whose `event_id` and `retry_ms` are None; an
`error_events` record raises `ProtocolDataError` with the reason `error_event`. A `sentinel` completion ends at the line
equal to its value, which is compared before JSON decoding and never yielded, so the value need not be JSON; a value
containing LF could never match, so it fails generation.

`final_line` says how the body may end. With `require_newline`, every record ends with a line end, and bytes after the
last one raise `StreamInterruptedError` with the reason `eof`. With `allow_eof`, those bytes are decoded as
the last record, a sentinel included; a body that ends with a line end ends the same way under both.

A line split over many reads is joined once its line end arrives, so it costs time linear in its length.

### NDJSON generation checks

The helper's `media` must be `application/x-ndjson`, `application/ndjson`, `application/jsonl`, `application/x-jsonl`,
`application/jsonlines`, or `application/x-jsonlines`, compared without case and with any parameters allowed, and a
success response of its operation must declare that media type, which the helper then requests as declared. The schema
and resumption checks are those of SSE helpers, and an NDJSON cursor is always a body pointer:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.ndjson.diagnostics -->
<!-- fmt: off -->

```text
--client-protocols['checks.media'].media: The media type 'application/json' of 'checks.media' is not one of application/jsonl, application/jsonlines, application/ndjson, application/x-jsonl, application/x-jsonlines, or application/x-ndjson
--client-protocols['checks.response'].operation: GET /status declares no application/x-ndjson success response for the NDJSON helper 'checks.response'
--client-protocols['checks.other_media'].operation: POST /search declares no application/x-ndjson success response for the NDJSON helper 'checks.other_media'
--client-protocols['checks.discriminator_absent'].event_schema.discriminator.pointer: The discriminator pointer '/kind' of 'checks.discriminator_absent' names no property of '/components/schemas/Created'
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.ndjson.diagnostics -->

## WebSocket helpers

An enabled `websocket` helper is generated at `client.protocols.<name>` on `Client` and `AsyncClient` alike, with one
method, `connect`. Its channel is a GET operation, whose handshake request the helper sends: the operation gives the
URL, the parameters, the servers, and the security. `connect` takes the operation's parameters as keywords, then
`ws_options` and `options`. On `Client` it returns a `WebSocketSession[S, R]` once the server
accepted the handshake; `with` or `close()` closes it. On `AsyncClient` it returns an async context manager: `async
with` sends the handshake and enters an `AsyncWebSocketSession[S, R]`, which leaving the block closes:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.websocket.helper -->
<!-- fmt: off -->

```python
    def connect(
        self,
        *,
        room: str,
        since: int | UNSET = UNSET,
        ws_options: WSOptions | None = None,
        options: RequestOptions | None = None,
    ) -> WebSocketSession[models.ClientMessage, models.ServerMessage]:
        """Open the WebSocket of GET /rooms/{room}/socket, returning once its handshake got a valid 101."""
        return connect_socket(
            self._core,
            _plans.SOCKET_0,
            (room, since),
            ws_options=ws_options,
            options=options,
        )
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.websocket.helper -->

```python
with Client() as client, client.protocols.rooms.chat.connect(room="lobby") as session:
    session.send(ClientMessage(text="hi"))
    for message in session:
        print(message.sequence, message.data)

async with AsyncClient() as client, client.protocols.rooms.chat.connect(room="lobby") as session:
    await session.send(ClientMessage(text="hi"))
    async for message in session:
        print(message.sequence, message.data)
```

```json
{
  "rooms.chat": {
    "kind": "websocket",
    "operation": "/paths/~1rooms~1{room}~1socket/get",
    "subprotocols": ["chat.v2", "chat.v1"],
    "compression": true,
    "send": {"codec": "json", "schema": {"pointer": "/components/schemas/ClientMessage"}},
    "receive": {"codec": "json", "schema": {"pointer": "/components/schemas/ServerMessage"}}
  }
}
```

`send` and `receive` declare how the messages of each direction are coded: `codec: json` with a `schema`, whose value
is sent as compact UTF-8 JSON and received through the schema's codec, `codec: utf8` for `str` text, or
`codec: bytes`. `frame` is `text` or `binary`; it defaults to `text` for JSON and UTF-8 messages and to `binary` for
bytes, and only JSON messages may choose. `S` is the send schema's argument type, its model type `T`, or `str` or
`bytes`, and `R` the received type; a union schema carries several message types. `subprotocols` lists the offered
subprotocols in order (`[]` by default). `compression` is still accepted and has no effect: HTTPX2 WebSockets do not
negotiate `permessage-deflate`.

`WebSocketSession`, `AsyncWebSocketSession`, `Message`, `PingReceipt`, and `WSOptions` are imported from
`pkg.protocols`, and the WebSocket exceptions from `pkg.errors`. Sessions run on `httpx2.websockets`, so generation
prints `httpx2[ws]` among the packages to add for a package with WebSocket helpers; other packages never import it.

### Handshakes

The handshake is one logical call of the operation, with initial authentication and deadline, sent as
a GET with the upgrade headers through the client's HTTP client: its transport, proxy, TLS, and `event_hooks` apply
as to any call. Only a 101 response whose subprotocol the helper offered opens the session; HTTPX2 checks nothing else
of it, as its own WebSocket client does. Any other response is read up to its 64 KiB error prefix and raises the
`APIStatusError` subclass of its status, or `APIStatusError` with the reason `unexpected_status` for an undeclared
status, so 101 need not be declared. Received refusals are terminal, including redirects, which a handshake never
follows, and 401s; a refused upgrade never renews a token. Only an initial transport failure
proven unsent before session handover may use the call's existing retry policy. A handshake that may have reached
the server is never sent again.

When the helper offers subprotocols, the server must select one of them, or `connect` raises `APIConnectionError` with the
reason `negotiation_failed`; `session.subprotocol` is the selected one and `session.response` the 101 response.
Credentials are sent as the operation's security declares, only to the server's origin. Headers the handshake manages, such as `Upgrade`, `Connection`, and `Sec-WebSocket-*`, given through
request options or parameters raise `ConfigurationError` with the reason `managed` before anything is sent.

### Sessions

| Method | Behavior |
|---|---|
| `send(value)` | Encodes one message and sends it as one message of the declared frame |
| `receive()` | Returns the next `Message[R]`: `data`, `frame` (`text` or `binary`), `sequence` from 1, and `raw` bytes |
| Iteration | Yields messages until the server closes normally |
| `ping(payload=b"")` | Sends a ping of at most 125 bytes, a random one when empty, and returns a `PingReceipt` with the pong's `latency` in seconds |
| `close(code=1000, reason="")`, `aclose` | Closes with 1000, 1001, or a code from 3000 to 4999 and a reason of at most 123 UTF-8 bytes; repeats do nothing |
| `with` | Closes a synchronous session on exit; an asyncio session is the `async with` block of its `connect` |
| `progress` | The session's `messages_sent` and `messages_received` |

An HTTPX2 `WebSocketSession` or `AsyncWebSocketSession` carries the connection: it reads in the background, answers
pings, and sends keepalive pings. The session owns it until it closes or fails, but the connection belongs to the HTTP
client's pool. Closing a `Client` that created its HTTP client closes its open sessions first, as their `close()` does,
after which every step raises `ConfigurationError` with the reason `invalid_state`; close sessions before closing a
borrowed HTTP client, since closing a socket does not wake the session's reader on every platform. Closing an
`AsyncClient` closes the connections of its open sessions, whose next step then fails. One `receive` waits at a time:
another raises `ConfigurationError` with the reason `invalid_state`, while sends, which HTTPX2 writes one at a time, may
run beside it. Cancelling the task of an asyncio `receive`, as `asyncio.wait_for` does, leaves the session usable, since
HTTPX2 queues whole messages; a cancelled `send` or `ping` fails the session, since a message may be half written. An
`AsyncWebSocketSession` runs the HTTPX2 session in the task that entered its `connect` block, as HTTPX2's task group
must end in the task that started it, and closes it when the block ends; tasks the block starts may use and close it
meanwhile. A failure the block raises, even an exit such as `SystemExit`, leaves it unwrapped; a failure of HTTPX2's
background tasks cancels the block and leaves it as `APIConnectionError`. A received message of another frame kind, or
one that does not decode, raises `DecodeError` with the `location` `("message", sequence)` and at most 64 KiB of it as
`body_bytes` and closes the connection with 1002. A server's closure raises `WebSocketClosedError` with its `code`,
`reason`, and `clean`, which is true for 1000 and 1001, the closures that alone end iteration; the session answers it
with the same code and reason, and after it `receive`, `send`, and `ping` raise it again. A `send` or `ping` on a
connection that is already closing, before `receive` reported why, raises `WebSocketClosedError` without a code. After
any other failure, every step raises `ConfigurationError` with the reason `invalid_state`, as it does after `close()`.
Representations never show messages, URLs, headers, or close reasons.

### WebSocket limits

Each limit comes from the call's `ws_options`, then the helper's options in the client's `helper_defaults`, then the
default below. The session's optional total budget uses the client's `Clock`; native waits receive the
remaining duration as their timeout:

| Limit | Default | None |
|---|---|---|
| `WSOptions.open_timeout` | 5 seconds, also capped by the connect, read, write, and pool timeouts and the deadline | No open limit |
| `WSOptions.idle_timeout` | The native read timeout | No idle limit |
| `WSOptions.max_message_bytes` | 1 MiB per message, HTTPX2's `max_message_size_bytes` | Not allowed |
| `WSOptions.ping_interval`, `pong_timeout` | 20 seconds each, HTTPX2's keepalive settings; `pong_timeout` also bounds `ping()` | No keepalive pings |
| `WSOptions.total_timeout` | None | No session budget |

A message over `max_message_bytes` raises `ProtocolDataError` with the reason `too_large` after HTTPX2 closed the
connection with 1009. A receive that waits longer than the idle timeout raises `APITimeoutError` with the reason
`phase_timeout` and closes with 1001, as does a ping whose pong does not arrive within the pong timeout; another
failure closes with 1011. A missed keepalive pong makes HTTPX2 close with 1011, and the next `receive` raises
`APIConnectionError`. The session deadline bounds every wait and raises `APITimeoutError` with
the reason `deadline_exceeded`. Messages are written whole by HTTPX2, without a timeout of their own; a send that may
have reached the server raises `APIConnectionError` with the reason `delivery_unknown`, closes the session, and is never
sent again. Received messages wait in HTTPX2's queue until they are read. Sessions never reconnect. Options of
another type raise `ConfigurationError`.

### WebSocket generation checks

The operation must be a GET without a request body, and each JSON message's schema must exist. Message definitions, frames, and subprotocol tokens are checked as the helper file is read:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.websocket.diagnostics -->
<!-- fmt: off -->

```text
--client-protocols['checks.method'].operation: The WebSocket helper 'checks.method' opens POST /chat, which is not a GET operation
--client-protocols['checks.body'].operation: The WebSocket helper 'checks.body' opens GET /upload, which takes a request body
--client-protocols['checks.schema'].send.schema: The schema '/components/schemas/Nobody' of 'checks.schema' does not exist in its document
--client-protocols['codec.unknown'].send.codec must be 'json', 'utf8' or 'bytes'
--client-protocols['codec.schema_missing'].send needs 'schema' for JSON messages
--client-protocols['codec.schema_extra'].send.schema applies only to JSON messages
--client-protocols['frame.text'].send.frame must be 'text' for utf8 messages
--client-protocols['frame.binary'].receive.frame must be 'binary' for bytes messages
--client-protocols['frame.json'].receive.frame must be 'text' or 'binary'
--client-protocols['subprotocols.token'].subprotocols[0] must be a subprotocol token
--client-protocols['subprotocols.token'].subprotocols[1] must be a subprotocol token
--client-protocols['subprotocols.repeated'].subprotocols[1] repeats a subprotocol
--client-protocols['message.type'] has no key 'compression'
--client-protocols['message.type'].send must be a message definition
--client-protocols['message.type'].receive has no key 'extra'
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.websocket.diagnostics -->

## Cache helpers

An enabled `cache` helper fetches one GET operation through a private cache: it answers from a fresh
stored response without sending, revalidates a stale one with its `ETag` or `Last-Modified` validator, and otherwise
sends the request as an ordinary call. Nothing is cached for ordinary methods, and a client without a cache helper
keeps no cache state. The helper is generated at `client.protocols.<name>` on `Client` and `AsyncClient` alike:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.cache.json -->
<!-- fmt: off -->

```json
{
  "users.profile": {
    "kind": "cache",
    "operation": "/paths/~1users~1{userId}/get",
    "validator": "both",
    "authenticated": false,
    "vary_allowlist": ["Accept-Language"]
  }
}
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.cache.json -->

| Setting | Meaning |
|---|---|
| `operation` | The GET operation the helper fetches. It takes no request body, and each cacheable status is a declared 2xx success with one JSON body |
| `validator` | `etag` revalidates with `If-None-Match` from `ETag`, `last_modified` with `If-Modified-Since` from `Last-Modified`, and `both` with `If-None-Match` when an `ETag` is stored and `If-Modified-Since` otherwise |
| `authenticated` | Whether the fetch carries credentials. It must match every call: a call that the auth, a credential or cookie header, or a security scheme's field authenticates needs `true` and a credential partition, and any other call `false` |
| `statuses` | The cacheable statuses, distinct, from 100 to 599 |
| `vary_allowlist` | The request headers a response's `Vary` may name; a response that varies on any other header, or on `*`, is not stored. A header credentials travel in (`Authorization`, `Proxy-Authorization`, `Cookie`, `Cookie2`, or a declared security scheme's header) fails generation. Responses behind a CDN often vary on `Accept-Encoding`: allow it to store them |

`fetch` takes the operation's parameters as keywords, then `cache_options` and `options`, and returns a `CacheResult`.
With asyncio, `fetch` is a coroutine:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.cache.helper -->
<!-- fmt: off -->

```python
    def fetch(
        self,
        *,
        fields: str | UNSET = UNSET,
        accept_language: str | UNSET = UNSET,
        user_id: int,
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

A helper keeps its entries in the store the client's `cache_stores` lends it under its name. A helper without one
uses a `MemoryCacheStore` (`AsyncMemoryCacheStore` on `AsyncClient`) of 128 entries, which the root client creates at
the first such fetch and shares with its views and its other cache helpers without a store; another client never
shares it. The client checks the lent stores when it is constructed: a name that is no cache helper of the package
fails with `unknown_field`, and an object without the three store methods, or whose methods are coroutines for a
`Client` or plain functions for an `AsyncClient`, with `wrong_capability`. The client borrows a lent store: it never
closes one.

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.cache.usage -->
<!-- fmt: off -->

```python
def cached_client() -> Client:
    """Lend a bounded memory store to the users.profile helper, which keeps its entries there."""
    store = MemoryCacheStore(max_entries=1000)
    return Client(cache_stores={"users.profile": store}, helper_defaults={"users.profile": CacheOptions(max_ttl=60)})
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.cache.usage -->

### Results and freshness

`CacheResult[T]` is immutable: `data: T`, the decoded body; `source`, `network` for a response the network sent,
`fresh_cache` for a fresh stored response answered without sending, or `revalidated` for a stored response a 304
confirmed; `response: ResponseInfo`; and `network_status`, the status the network returned, 304 for a revalidation and
`None` for a fresh answer. A fresh answer's `response` has every count 0 and the elapsed time of the
lookup and decoding; a revalidation's has the 304 call's counts and elapsed time, with the stored status and
the merged headers, request id, and content type. A stored body is decoded again on every use, so callers never share a
model object. A fresh answer sends nothing, so the HTTP client's `event_hooks` see no request; a network fetch is an
ordinary request.

An entry is fresh while its age is below its freshness lifetime and below `CacheOptions.max_ttl`. The lifetime is the
response's `Cache-Control: max-age`, else `Expires` minus `Date`, else 0; the age follows RFC 9111 from `Age`, `Date`,
and the times of the request and the response. There is no heuristic freshness and no stale-on-error. A response's
`no-cache` makes every use revalidate. A fetch's own `Cache-Control` may say `no-cache` (revalidate), `no-store` (no
stored response is read or written), or `max-age=N` (use a stored response only up to that age); any other directive,
and a `Range`, `If-Range`, `If-Match`, or `If-Unmodified-Since` header, raises `ConfigurationError` before
anything is looked up.

A stale entry is revalidated with its validator as `validator` declares; a fetch that gives `If-None-Match` or
`If-Modified-Since` itself sends it once, and raises `ConfigurationError` with the reason `binding_mismatch` and the
header's name as `field_path` before sending when a usable entry has another validator or the header is given more than
once. A 304 updates the entry's headers, except `Content-Type`, `Content-Encoding`, `Content-Length`, and hop-by-hop
fields, and its freshness. A 304 without a usable entry, after a redirect, or with an `ETag` or `Last-Modified` other
than the entry's raises `ProtocolDataError` with the reason `inconsistent`; no request is sent again.

### Storing and keys

A response is stored only when its status is cacheable, its body is within `CacheOptions.max_entry_bytes`, it came
without a redirect, it has no `Set-Cookie`, its `Cache-Control` has only RFC 9111 directives (`s-maxage` is ignored)
and no `no-store`, its `Vary` names only allowlisted headers, and it is fresh or can be revalidated. A response that
cannot be stored removes the entry it supersedes, and errors and decoding failures store nothing and keep the entry.
Entries hold the body after content decoding and the headers without `Content-Encoding` or hop-by-hop fields.

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.cache.keys -->
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
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.cache.keys -->

!!! warning "Credentials the cache cannot see"
    The key covers the credentials a request carries when the call's Auth places them. Credentials the SDK never
    sees, such as a client certificate, or a borrowed HTTP client's authenticating transport or event hooks, make
    requests with different identities share keys: give each such identity a client of its own, or a store of its
    own.

### Stores

`CacheStore` and `AsyncCacheStore` provide `get(key) -> CacheEntry | None`, `set(key, entry) -> None`, and
`delete(key) -> None`, using byte keys. Each key holds one representation. A Vary mismatch is a miss, and a successful
cacheable response replaces the representation. Concurrent custom-store writes use last-completing replacement.
A store exception or a result of another type raises `SDKError` with the reason `store_failed` and the exception as
`cause`; the request is never sent again for it.

`CacheEntry` has `vary`, `vary_values`, `status_code`, `headers`, `body`, `request_time`, `response_time`, `stored_at`,
`freshness_seconds`, `initial_age_seconds`, and `schema_fingerprint`. Plain Vary values preserve each named header's
ordered values, including the distinction between a missing header and an empty value. Credential headers and cookies
are excluded from these values and isolated by the opaque cache key. Values stay in private process memory and the
entry's representation names only its status. A mismatched fingerprint or status cannot serve a fetch or a 304.

`MemoryCacheStore(max_entries=128)` and `AsyncMemoryCacheStore(max_entries=128)` bound entries by count. Reads and
writes mark a key recently used, replacement keeps the count unchanged, and adding a key evicts the least recently
used key when needed. Deletion is idempotent. Freshness and revalidation use the fetching client's clock.

### Cache generation checks

The operation must be a GET without a request body; other methods fail, and HEAD is not
supported yet. Each cacheable status must be a declared 2xx success with one natively decoded JSON body, a helper
declared anonymous cannot fetch an operation that requires credentials:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.cache.diagnostics -->
<!-- fmt: off -->

```text
--client-protocols['users.created'].operation: The cache helper 'users.created' fetches POST /users, which changes state; only GET is cached
--client-protocols['users.created'].operation: The cache helper 'users.created' fetches POST /users, which takes a request body
--client-protocols['users.created'].statuses[0]: The cacheable status 200 of 'users.created' is no declared 2xx success of POST /users
--client-protocols['users.checked'].operation: The cache helper 'users.checked' fetches HEAD /users/{userId}, and HEAD is not supported yet
--client-protocols['users.checked'].statuses[0]: The cacheable status 200 of 'users.checked' has a response other than one natively decoded JSON body, which is not supported yet
--client-protocols['reports.get'].operation: The cache helper 'reports.get' fetches GET /reports, which takes a request body
--client-protocols['reports.get'].statuses[0]: The cacheable status 200 of 'reports.get' has a response other than one natively decoded JSON body, which is not supported yet
--client-protocols['reports.get'].statuses[1]: The cacheable status 204 of 'reports.get' has a response other than one natively decoded JSON body, which is not supported yet
--client-protocols['users.statuses'].statuses[0]: The cacheable status 404 of 'users.statuses' is no declared 2xx success of GET /users/{userId}
--client-protocols['users.statuses'].statuses[1]: The cacheable status 201 of 'users.statuses' is no declared 2xx success of GET /users/{userId}
--client-protocols['secure.anonymous'].authenticated: The cache helper 'secure.anonymous' is declared anonymous, but GET /secure/users/{userId} requires credentials
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.cache.diagnostics -->


## Signature style

`signature_style` chooses how every operation method of the generated package declares its keyword arguments.

| Setting | Values | Default | Where |
|---|---|---|---|
| `signature_style` | `"explicit"`, `"unpack"` | `"explicit"` | `--client-signature-style unpack`; `pyproject.toml`: `client-signature-style = "unpack"`; `generate()`: `client_signature_style="unpack"` |

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
        pet_id: int,
        response_media_type: None = None,
        options: RequestOptions | None = None,
    ) -> models.Pet: ...
    @overload
    def get_pet(
        self,
        *,
        pet_id: int,
        response_media_type: Literal['application/json'],
        options: RequestOptions | None = None,
    ) -> models.Pet: ...
    @overload
    def get_pet(
        self,
        *,
        pet_id: int,
        response_media_type: Literal['text/plain'],
        options: RequestOptions | None = None,
    ) -> models.FieldPetsPetIdGetResponse: ...
    def get_pet(
        self,
        *,
        pet_id: int,
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
    def get_pet(self, **kwargs: Unpack[Operation2Arguments]) -> models.Pet: ...
    @overload
    def get_pet(self, **kwargs: Unpack[Operation2Arguments1]) -> models.Pet: ...
    @overload
    def get_pet(
        self,
        **kwargs: Unpack[Operation2Arguments2],
    ) -> models.FieldPetsPetIdGetResponse: ...
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

    pet_id: int
    response_media_type: NotRequired[None]
    options: NotRequired[RequestOptions | None]


class Operation2Arguments1(TypedDict):
    """The keyword arguments of one signature of get_pet."""

    pet_id: int
    response_media_type: Literal['application/json']
    options: NotRequired[RequestOptions | None]


class Operation2Arguments2(TypedDict):
    """The keyword arguments of one signature of get_pet."""

    pet_id: int
    response_media_type: Literal['text/plain']
    options: NotRequired[RequestOptions | None]


class Operation2Arguments3(TypedDict):
    """The keyword arguments of one signature of get_pet."""

    pet_id: int
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
| `body_arguments` | `"body"`, `"both"` | `"body"` | `--client-body-arguments both`; `pyproject.toml`: `client-body-arguments = "both"`; `generate()`: `client_body_arguments="both"`; one operation: its `body_arguments` member of `--client-operations`, where `null` inherits the package's |
| `body_field_names` | Property names by media type | none | One operation: its `body_field_names` member of `--client-operations`, such as `{"application/json": {"tag": "pet_tag"}}` |

With `"both"`, a JSON body whose model is `NewPet` takes either call:

```python
client.pets.create_pet(body=NewPet(name="Mimi", kind=Kind.cat), media_type="application/json")
client.pets.create_pet(name="Mimi", kind=Kind.cat, media_type="application/json")
```

In `pyproject.toml`, the same settings are:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.body-fields.pyproject -->
<!-- fmt: off -->

```toml
[tool.datamodel-codegen]
input = "fields.yaml"
input-file-type = "openapi"
output = "models.py"
output-model-type = "pydantic_v2.BaseModel"
openapi-scopes = ["schemas", "api"]
target-python-version = "3.11"
formatters = ["builtin"]
disable-timestamp = true
openapi-include-paths = ["/pets*", "/owners"]
generate-client = "httpx2"
client-output = "client"
client-package = "client"
client-model-package = "models"
client-body-arguments = "both"

[tool.datamodel-codegen.client-operations."/paths/~1pets/post".body_field_names]
"application/json" = { tag = "pet_tag" }
"application/x-www-form-urlencoded" = { tag = "pet_tag" }

[tool.datamodel-codegen.client-operations."/paths/~1pets~1{petId}~1visits/post"]
body_field_names = { "application/json" = { options = "visit_options" } }

[tool.datamodel-codegen.client-operations."/paths/~1pets~1{petId}~1owner/put"]
body_arguments = "body"
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.body-fields.pyproject -->

### Which bodies take fields

A JSON or form body takes fields when its schema is one object whose model its backend constructs from them, an
object that may be null included: Pydantic models and dataclasses, stdlib dataclasses, TypedDicts, and msgspec
Structs alike. Other bodies keep only `body`: unions of objects, arrays and scalars, bodies without a schema, binary
bodies and form-data sent as parts, bodies whose model cannot hold a request, such as one with a required read-only
member. Extra keys, a null body, and an empty object also need `body`, and a nested object is given as its own model.

Each field argument is named by the snake case of its wire property, and a read-only member has none.
`body_field_names` names one field of one media type instead, without changing the model or the wire property. Fields
of different media types may share a name. Generation fails when a field would take the name
of a parameter or of another field of the same media type, a reserved name such as `body` or `options`, or no valid
identifier, and when a body field name names no field argument or its operation keeps only
`body`:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.body-fields.diagnostics -->
<!-- fmt: off -->

```text
--client-operations: The body field name of the application/json property 'id' of POST /pets names no field argument
--client-operations: The body field name of the application/xml property 'tag' of POST /pets names no field argument
--client-operations: The body field name of the application/json property 'absent' of POST /pets names no field argument
/paths/~1pets/post: The application/json body fields of POST /pets cannot take the argument names 'kind', 'tag'; name them with the operation's body_field_names in --client-operations
/paths/~1pets/post: The application/x-www-form-urlencoded body fields of POST /pets cannot take the argument names 'tag'; name them with the operation's body_field_names in --client-operations
/paths/~1pets~1{petId}~1visits/post: The application/json body fields of POST /pets/{petId}/visits cannot take the argument names 'options'; name them with the operation's body_field_names in --client-operations
--client-operations: The body field name of the text/plain property 'q' of POST /search names no field argument
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.body-fields.diagnostics -->

### Signatures

The body and each media type's fields are separate overloads. The body's takes every field as `UNSET`; a field
overload takes the body as `UNSET`, the fields its model's constructor requires without a default, and its other
fields with `UNSET`, and a field takes `None` only when its property is nullable. A field is required exactly when the
generated model requires it, so `--force-optional` and defaults make it optional. An optional body whose fields are all optional has one
overload for each field, which that overload requires, and one that takes nothing and sends nothing:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.body-fields.overloads -->
<!-- fmt: off -->

```python
    @overload
    def update_pet(
        self,
        *,
        pet_id: int,
        body: models.PetPatch,
        name: UNSET = UNSET,
        tag: UNSET = UNSET,
        media_type: Literal['application/json'] | None = None,
        options: RequestOptions | None = None,
    ) -> UpdatePetResponse: ...
    @overload
    def update_pet(
        self,
        *,
        pet_id: int,
        body: UNSET = UNSET,
        name: str,
        tag: str | None | UNSET = UNSET,
        media_type: Literal['application/json'] | None = None,
        options: RequestOptions | None = None,
    ) -> UpdatePetResponse: ...
    @overload
    def update_pet(
        self,
        *,
        pet_id: int,
        body: UNSET = UNSET,
        name: str | UNSET = UNSET,
        tag: str | None,
        media_type: Literal['application/json'] | None = None,
        options: RequestOptions | None = None,
    ) -> UpdatePetResponse: ...
    @overload
    def update_pet(
        self,
        *,
        pet_id: int,
        body: UNSET = UNSET,
        name: UNSET = UNSET,
        tag: UNSET = UNSET,
        media_type: None = None,
        options: RequestOptions | None = None,
    ) -> UpdatePetResponse: ...
    def update_pet(
        self,
        *,
        pet_id: int,
        body: models.PetPatch | UNSET = UNSET,
        name: str | UNSET = UNSET,
        tag: str | None | UNSET = UNSET,
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
omitted. The method refuses any other binding before any request or stream: with `TypeError` for a body with
fields, a missing required field, a field of another media type, or fields for a media type that takes none, and with
`ConfigurationError` for a missing or undeclared media type, as it does for bodies:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.body-fields.refusals -->
<!-- fmt: off -->

```text
a body and fields ! TypeError: create_pet() takes a body or its field arguments, not both: 'name' []
fields missing a required one ! TypeError: create_pet() missing required field arguments for application/json: 'kind' []
a field of another media ! TypeError: create_pet() takes no such field arguments for application/x-www-form-urlencoded: 'kind' []
fields without a media type ! ConfigurationError: ConfigurationError(operation_id='createPet', reason='missing', field_path='media_type') [operation_id='createPet', field_path=('media_type',)] missing
fields for text ! TypeError: log_visit() takes no field arguments for text/plain: 'note' []
update naming only a media type ! ConfigurationError: ConfigurationError(operation_id='updatePet', reason='without_body', field_path='media_type') [operation_id='updatePet', field_path=('media_type',)] without_body
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

| Backend | Sends | Receives |
|---|---|---|
| Pydantic v2 `BaseModel` | `model_dump_json(by_alias=True, exclude_unset=True)`; other types through a cached `TypeAdapter` | `TypeAdapter(T).validate_json` |
| Pydantic v2 dataclass | `TypeAdapter(T).dump_json(by_alias=True)` | `TypeAdapter(T).validate_json` |
| `msgspec.Struct` | `msgspec.json.encode` | `msgspec.json.Decoder(T).decode` |
| dataclasses, `TypedDict` | `json.dumps` of the renamed fields | `json.loads`, then the renamed fields |

Adapters and decoders are built on a type's first use and cached for the process. dataclasses and `TypedDict` have no
validation of their own: the generated converter renames wire names to fields and builds nested models, lists, sets,
tuples, dicts, and optionals, and converts only `datetime`, `date`, and `time` (with `fromisoformat`), `UUID`,
`Decimal` (sent as a string), an `Enum` by value (an unknown value fails), and `bytes` as base64. A `TypedDict` keeps
unknown keys and leaves out absent ones; a dataclass drops unknown keys and leaves out a field whose value is its
`None` default.

- **Requests.** Arguments and bodies are serialized from the model values as given. Aliases, presence, and the
  conversion of dates, decimals, enums, and media follow the models. A value is checked only as far as its model's
  constructor already checked it, and what the serializer cannot represent fails with a request `DecodeError` with the
  reason `unencodable` before anything is sent; its `location` names the argument, never the value. An empty array or
  object in a style-encoded parameter or a form member is left out as an omitted value is: it writes no query pair,
  header, cookie, or part. A path segment cannot be left out, so an empty array or object in a path parameter fails
  with a request `DecodeError` before anything is sent.
- **Parameter arguments.** A parameter whose model type is a type alias or a root model takes the type it stands for,
  such as `limit: int` or `tags: list[str]`, never the alias or the root model. An alias's argument is sent as given,
  without validation. With Pydantic models, an argument whose parameter is a root model is built into it before it is
  sent, which checks it as the root model's constructor does: a value it refuses fails with a request `DecodeError`
  with the reason `unencodable`.
- **Parameter defaults.** An optional parameter whose schema declares a boolean, number, or string default of its
  builtin type, alone or with null, defaults to it, such as `limit: int = 20`; any other optional parameter defaults to
  `UNSET`. A parameter with such a default is always sent, with the default when the call omits it: the client pins
  the default it was generated with, so a later change of the server's default does not apply until you regenerate,
  and a default that breaks its own schema's constraints is sent as it is.
- **Responses.** Bodies, parts, stream events, and socket messages are converted by the model backend into the declared
  types, which check what those types declare and coerce as the backend does; a value the type refuses raises
  `DecodeError` with the reason `invalid_value`. Read-only and write-only behavior follows the generated model options.
- **Binding.** A missing required argument, an unknown keyword, or a media type the operation does not declare fails
  before anything is sent.
- **Path segments.** Path arguments that make their segment `.` or `..` once encoded in their parameters' styles and
  put into the path template, `%2E` in either case counting as `.`, such as `..` in the simple style, the empty string
  in the label style, or `%2e` with `allowReserved`, fail with a request `DecodeError` at the segment's first parameter
  whose encoded text is non-empty, with a `ParameterEncodingError` cause, before anything is sent: URL normalization in
  clients, proxies, and servers would remove the segment and send the call to another resource, and encoding the dots
  does not prevent it. `...` and `.a` are sent as they are, and a dot segment the path template spells is not refused.
- **Cookies.** A cookie parameter with a single value is sent as it is, without percent-encoding, which is how HTTP
  clients send cookies and how the generated server reads them. A cookie cannot carry `;`, `,`, `"`, `\`,
  whitespace, control characters, or non-ASCII text (RFC 6265), so such a value fails with a request `DecodeError`
  of the reason `unencodable`, with a `ParameterEncodingError` cause, before anything is sent, instead of arriving
  cut short. Encode a value that needs those characters yourself, for example as Base64, and decode it in the
  service. Such a parameter whose name is not a token stops generation with an error. Array and object cookie
  parameters send one cookie for each item or property, percent-encoded in the `form` style.
- **Headers.** Header values are converted through their native types. Read a body without any model with
  `with_raw_response` or `with_streaming_response` instead.

A union of object models read by a stdlib dataclass or TypedDict converter needs a declared discriminator. Without
one, generation raises `Error` naming the use:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.model-codecs.diagnostics -->
<!-- fmt: off -->

```text
/paths/~1animals~1any/put/requestBody: The union of Cat and Dog needs a declared discriminator for the dataclasses.dataclass converter
/paths/~1kin/put/requestBody: The union of Animal and Cat needs a declared discriminator for the dataclasses.dataclass converter
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.model-codecs.diagnostics -->

## Timeouts and cancellation

A client takes its settings as keyword arguments, a view takes them through `with_options(...)`, and one call takes
them as `options=RequestOptions(...)` from the generated package's `options` module, which also provides `RetryOptions`,
`ServerSelection`, and `Clock`. These settings apply to typed operations and `request_raw`, including their response
and streaming views.

```python
import httpx2

from pets import Client
from pets.options import RequestOptions

client = Client(base_url="https://api.example.com", timeout=30, max_retries=3, default_headers={"X-App": "demo"})
patient = client.with_options(timeout=httpx2.Timeout(120, connect=5), total_timeout=None)
response = patient.request_raw(
    "GET", "https://api.example.com/health", options=RequestOptions(extra_headers={"X-App": None})
)
```

`default_headers` and `default_query` of the client and its views, and a call's `extra_headers` and `extra_query`, give
each name they list their value over the generated headers and query, the client's first, then the views', the call's
parameters', and the call's extra ones last; None removes that name. Header names compare case-insensitively, query
names exactly. A body's media type ranks above every layer but the call's `extra_headers`: only a call's Content-Type
relabels its body, or with None sends it unlabelled, and a multipart one without `boundary=` keeps the body's boundary.

| Setting | Effective default | Meaning |
|---|---|---|
| `timeout` | Owned client: connect 5, read/write/pool 600 seconds; injected client: its native timeouts | Native I/O phase limits |
| `total_timeout` | `None` | Optional budget across attempts and retry waits, starting at call entry |
| `clock` | `Clock()`, the system clock | Client only: time, jitter, and wait sources |

Each setting a call leaves None inherits from the nearest `with_options` view, then the client. Where None has a
meaning of its own, UNSET is the default and inherits instead: `timeout=None` clears all four phase limits, and
`total_timeout=None` on a view or a call removes an inherited total budget. A number as `timeout` limits every phase,
and an `httpx2.Timeout` gives each phase its own limit. An injected HTTP client's native timeouts supply the initial
phase settings unless the client is given `timeout`. Durations must be finite and nonnegative; booleans are rejected.
Invalid values raise `ConfigurationError` with the setting's `field_path`. `total_timeout=0` is valid configuration;
starting a call with it raises `APITimeoutError` before preparing or sending a request.

### An optional budget across attempts

The runtime supplies no whole-call budget by default. Set `Client(total_timeout=60)` to bound every call to a minute.
Native phase timeouts bound each I/O wait; they do not limit the total duration of a call that keeps making progress.

```python
import httpx2

from pets import Client
from pets.options import RequestOptions


def read_with_budget(client: Client, url: str) -> bytes:
    options = RequestOptions(total_timeout=10, timeout=httpx2.Timeout(3, connect=2))
    return client.request_raw("GET", url, options=options).read()
```

The budget is checked before each attempt and retry wait and caps each native I/O timeout. A native timeout raises
`APITimeoutError` with the reason `phase_timeout`, or `deadline_exceeded` once the budget has expired, with the native
failure as `cause`. A fully received response stays available when decoding or cleanup finishes after expiry.

### Native cancellation

Async calls run in their caller's task. Cancel that task through the native backend; its cancellation exception
propagates unchanged. The SDK never uncancels the task, selects a competing outcome, or wraps a `BaseException`.
Responses and bodies are released in `finally`, and callbacks such as the HTTP client's `event_hooks` run in the
caller's cancellation scope. Synchronous preparation and callbacks remain cooperative.

### Streaming after handoff

Streaming uses the native read timeout for idle I/O. An optional acquisition budget caps the request's native
phase timeouts before sending; it does not add a separate stream lifetime timer.

```python
from typing import BinaryIO

import httpx2

from pets import Client
from pets.options import RequestOptions


def download(client: Client, url: str, destination: BinaryIO) -> None:
    options = RequestOptions(timeout=httpx2.Timeout(600, read=60))
    with client.with_streaming_response.request_raw("GET", url, options=options) as response:
        response.stream_to(destination)
```

Always leave the response's context manager, including when abandoning a download early. A configured helper
session may additionally stop before its next step when its total budget has expired.

`stream_to(path)` writes the decoded body to a temporary file beside the target and moves it there once the body is
complete; an existing target raises `FileExistsError` unless `overwrite=True`, and a consumed handle raises
`ConfigurationError` with the reason `response_consumed`. A failure removes the temporary file and leaves the target
unchanged. In async calls the file calls run one at a time in a worker thread, and a cancellation during the final move
lets the move finish. `stream_to(file_object)` writes to a borrowed file and never closes, seeks, or truncates it.

### Clocks and retry jitter

`Client(clock=Clock(...))` replaces the time, jitter, and wait sources of every call a client makes. Views and requests
cannot change it. Each source is a function:

| Source | Default | Read for |
|---|---|---|
| `monotonic` | `time.monotonic` | Deadlines, elapsed times, retry targets, token expiry, and protocol helper sessions, poll intervals, and stream deadlines |
| `time` | `time.time` | Placing a wall-clock instant on the monotonic scale once: an HTTP-date `Retry-After` or polling delay header at receipt and an access token's `expires_at`; a polling, stream, or upload helper's `resume` check of its state's `expires_at`; and a cache fetch's request, response, and age times |
| `random` | A secure uniform draw | The fraction in `[0, 1)` of a full-jitter backoff, drawn only when a retry needs one |
| `sleep` | `time.sleep` | Each wait of a synchronous client, given its seconds: retry waits, polling intervals, and stream reconnection waits |
| `asleep` | `anyio.sleep` | Each wait of an asyncio client, given its seconds, returning the awaitable the call waits on, which task cancellation interrupts |

A source that cannot be called raises `ConfigurationError` with the `field_path` `("clock", name)`. OAuth providers
keep their own time through their `clock=` argument and must agree with their clients on wall time. Retry,
polling, and reconnection waits pass the remaining duration to the clock's `sleep` or `asleep`, and I/O timeouts
use the duration the monotonic source measured. A test that records the waits and advances its own monotonic
source by each one runs every wait at once and can check its length:

```python
from pets import Client
from pets.options import Clock


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.waits: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.now += seconds


def instant_retries(url: str, fake: FakeTime) -> Client:
    return Client(base_url=url, clock=Clock(monotonic=fake.monotonic, sleep=fake.sleep))
```

Each retry of this client starts at once, server delays included, and `fake.waits` holds the waits it chose.

## Instrumentation

The SDK adds no hook or limiter layer of its own. Instrument calls on the HTTP client: pass an `httpx2.Client` or
`httpx2.AsyncClient` with `event_hooks`, or a wrapping transport, as `http_client`. Request hooks see every attempt and
followed redirect, credentials included, and response hooks every response. An ordinary exception a hook raises ends
the call as `APIConnectionError` with that exception as `cause`, and the call is not sent again. Limit concurrency
with the HTTP client's pool limits or an application semaphore.

```python
import httpx2

from pets import Client


def log_request(request: httpx2.Request) -> None:
    print(request.method, request.url.path)


def log_response(response: httpx2.Response) -> None:
    print(response.status_code, response.request.url.path)


def instrumented(url: str, http: httpx2.Client) -> Client:
    http.event_hooks = {"request": [log_request], "response": [log_response]}
    return Client(http_client=http, base_url=url)
```

An injected client stays the caller's: close it after the SDK client.

### Counters and cleanup

`ResponseInfo` and every `SDKError` expose `attempt_count`, counted at send time, `elapsed`, and `request_id`, also
when no response arrived. Closing a root refuses new calls from the root and its views and closes its created native
client once; borrowed clients and providers keep the caller's lifetime. Responses and files opened from paths are
released in `finally`; a release failure is named in the notes of the primary error, or propagates without one.

## Errors

Every exception a client raises derives from `SDKError`, and all of them are imported from `pkg.errors`. Each
constructor is keyword-only and does not validate field values. Each carries `reason`, a stable snake_case symbol or
`None`, `operation_id`, `info`, the response metadata when one arrived, `cause`, and the measurements `attempt_count`,
`elapsed`, and `request_id`. Messages name only safe call metadata (the class, `operation_id`, `reason`, and a few
class-specific details), never payloads, headers, or credentials. A secondary failure beside the primary error, such as
a cleanup or invalidation failure, is named in the primary error's `__notes__`, never in an attribute or in its
place.

| Exception | Raised for | Fields beside the shared ones |
|---|---|---|
| `ConfigurationError` | A setting, argument, or call the client refused, including the reasons `client_closed`, `response_consumed`, and `invalid_state` | `field_path`, `reason` (`invalid_value` by default), `source_uri`, `source_pointer`, `helper_id` |
| `APIConnectionError` | A classified I/O failure of the transport, with the native failure as `cause`; the reason is `None` for a transport failure, `delivery_unknown` when a WebSocket send or an upload append or completion may have reached the server and is never sent again, and `negotiation_failed` for a WebSocket 101 that selected no offered subprotocol | None |
| `APITimeoutError` | A subclass of `APIConnectionError`: a phase cap that expired, with the reason `phase_timeout`, or the call's or stream's total timeout, with the reason `deadline_exceeded` | None |
| `APIStatusError` | A final status the operation does not declare as a success | `status_code`, `headers`, `request_id`, `body`, `body_bytes`, `truncated` |
| `DecodeError` | A request argument its wire form cannot carry, or a response, stream event, record, or message that cannot become its declared value | `direction` (`request` or `response`), `location`, `body_bytes`, `truncated`, `media_type`, `limit`, `observed` |
| `AuthError` | Credential acquisition or a token exchange failed; exported only by a package whose operations take credentials | `reason`, `status_code`, `oauth_error` |

`APIStatusError` has a subclass for each common status: `BadRequestError` (400), `AuthenticationError` (401),
`PermissionDeniedError` (403), `NotFoundError` (404), `ConflictError` (409), `UnprocessableEntityError` (422),
`RateLimitError` (429), and `InternalServerError` (500 and above); any other error status raises `APIStatusError`
itself. Its message is `Error code: <status> - <start of the body>`, with at most 500 characters of the body and `...`
when it is cut. Its `body` is the payload decoded with the operation's declared error schema for that status, or the raw
bytes, at most a 64 KiB prefix, when none is declared; a body that does not decode stays raw, and the decode
failure is the `cause`. A final status declared neither as a success nor as an error, such as an undeclared 3xx or 2xx,
raises `APIStatusError` with the reason `unexpected_status`.

A request `DecodeError` has the reason `unencodable`, with the argument's path as `location` and never its value, or
`body_not_replayable` at the `location` `("body",)`. A response `DecodeError` has the reason `invalid_syntax`,
`invalid_value` when the model or schema refuses the value, `unexpected_media_type` with the response's `media_type`,
`forbidden_body`, `missing_body`, `invalid_framing`, `invalid_header` with the `location` `("header", name)`,
`malformed_coding` for a body whose content coding does not decode, or `response_too_large` with its `limit` and
`observed` size. A stream event, NDJSON record, or WebSocket message that does not decode has the reason `missing`,
`null`, `type`, `value`, or `malformed`, the `location` `("event", sequence)` or `("message", sequence)` with a pointer
where a member is at fault, and at most 64 KiB of it as `body_bytes`. `AuthError` has the reason `provider_failed`, `invalid_expiry`, `oauth_error`, `timeout`, or `reauthorization_required`.
It keeps no credential material or provider description itself; the `cause` of `provider_failed` is the application's
own exception from its credential callable or token callback.

Helpers raise `ProtocolDataError`, `SessionLimitError`, `StreamInterruptedError`, and
`WebSocketClosedError`, described with each helper, and `SDKError` with the reason `store_failed` for a cache store that
fails.

## Retries and operation contracts

`max_retries` and `retry=RetryOptions(...)` apply on clients, views, and calls. The fields of `RetryOptions` merge
independently; omitted fields inherit, while a status set replaces the inherited set, and `retry=None` inherits the
whole record. Automatic retries require a candidate failure or status, operation safety, replayable input, and enough
time. JSON/model decoding, arbitrary callbacks,
cancellation, and logical deadlines never restart a request.

| Setting | Effective default | Meaning |
|---|---|---|
| `max_retries` | `2` | Retries after the initial attempt; `0` disables retries; a setting of its own, not a `RetryOptions` field |
| `initial_delay`, `max_delay` | `0.5`, `8` seconds | Exponential backoff, with maximum at least the initial delay |
| `jitter` | `"full"` | Uniform delay below the exponential cap; `"none"` uses the cap |
| `statuses` | `{408, 429, 500, 502, 503, 504}` | Replace with an integer set in 400–599; 401/403/407 are forbidden |
| `max_retry_after` | `60` seconds | Positive cap on accepted server delay; `None` removes this cap |
| `respect_retry_after` | `True` | Explicit `False` ignores server delay hints |
| `retry_after_ms_header`, `should_retry_header` | Operation declaration, otherwise `None` | Vendor controls require generated metadata; explicit `None` disables one |
| `retry_on_pool_timeout` | `False` | Avoid amplifying pool contention unless explicitly enabled |

GET, HEAD, OPTIONS, PUT, and DELETE are eligible for retries by default. POST, PATCH, and other methods require an explicit
`retry_safety="idempotent"` declaration or a valid server key contract. A native `ConnectError` (TLS and DNS failures
included), `ConnectTimeout`, or `PoolTimeout` proves the request unsent and can permit otherwise unsafe methods,
unless an earlier attempt of the call, or a response HTTPX2 followed a redirect from, reached the server; every other
failure after the send started may have been sent and is never sent again. `retry_safety="never"` prohibits every
resend, including an unsent request.

A server delay is a minimum: the client never shortens it to fit `max_retry_after` or the remaining deadline, and a
valid server veto prevents a retry. `Retry-After` gives seconds or an HTTP date, read by the standard library's
`email.utils.parsedate_to_datetime`: a date without a zone, with `-0000`, or with an unknown zone is UTC, and an
impossible date, such as one with a leap second, is ignored. The two-digit year of an RFC 850 date is the latest that
is at most 50 years after receipt, as RFC 9110 reads it; a year below 100 in another form is read as 1969 to 2068.
Without a valid hint, bounded exponential backoff applies. Retry waits consume the same logical budget as sending and
decoding. `RetryOptions(respect_retry_after=False)` is an explicit application policy override, disclosed in every
generated README; it is never silently embedded in generated defaults.

```python
from pets import Client
from pets.options import RequestOptions, RetryOptions


def fetch_with_retries(client: Client, url: str) -> bytes:
    options = RequestOptions(
        max_retries=2,
        retry=RetryOptions(max_retry_after=20),
        follow_redirects=True,
        total_timeout=30,
    )
    return client.request_raw("GET", url, options=options).read()
```

Buffered and streaming raw APIs return the final HTTP response when status retries end, including non-2xx statuses.
Transport, policy, deadline, and cancellation failures still raise. A response release failure
stops a planned retry after its response was discarded: the call then raises that response's `APIStatusError` with the
failure named in its notes.
Typed operations retain final response metadata in their `APIStatusError`. A streaming response can retry during
acquisition; after handoff, body failures terminate that stream and never issue another request.

### Declare API guarantees during generation

Operation guarantees are set per operation in `--client-operations`, or `client-operations` of `pyproject.toml`.
This example assumes `api.yaml` declares `POST /orders`:

```toml
[tool.datamodel-codegen.client-operations."/paths/~1orders/post".runtime]
retry_safety = "method_default"
retry_after_ms_header = "X-Retry-In-Ms"
should_retry_header = "X-Retry-Permitted"
idempotency = { header_name = "Idempotency-Key" }
```

With the [Python API](#python-api), the same operation setting is a mapping of `client_operations`:

```python
client_operations = {
    "/paths/~1orders/post": {
        "runtime": {
            "retry_safety": "method_default",
            "retry_after_ms_header": "X-Retry-In-Ms",
            "should_retry_header": "X-Retry-Permitted",
            "idempotency": {"header_name": "Idempotency-Key"},
        },
    },
}
```

Only `header_name` is required for idempotency. The key header must be an HTTP token and cannot share
an outgoing position with an effective parameter or authentication header. The two response control headers must
have distinct names. Header comparisons ignore ASCII case; outgoing and incoming positions are independent.
Unknown keys, malformed values, and ownership conflicts fail generation with an error naming the
setting. The generated README lists only the finalized selected operations and their contracts.

### Supply and retain an idempotency key

The `idempotency` setting's `header_name` declares the API's idempotency key header. A key belongs to one call:
supply the caller's own as `RequestOptions(idempotency_key="saved-value")`.

Unset, the client creates one UUID4 key per logical call for an operation with a declared header.
`idempotency_key=None` disables that automatic key. A caller key on an undeclared operation or `request_raw` fails
before sending with `ConfigurationError` and the reason `not_declared`. A header of the declared name among the call's
`extra_headers` or the client's or a view's `default_headers` is sent as it is and is the call's key instead, the same
on every call it reaches; a protocol helper, which needs a key per request, refuses it. The same key is sent unchanged on every eligible
retry. A declaration without an active key does not authorize unsafe retries. The contract does not guarantee
exactly-once business execution. HTTPX2 refuses a key that is not a valid header value when it sends the request.

```python
from pets import Client
from pets.options import RequestOptions


def keyed_options(value: str) -> RequestOptions:
    return RequestOptions(idempotency_key=value)
```

Pass the returned options to an operation with the matching declaration. The client reuses the supplied value across
eligible retries.

## Request compression

### Declare accepted codings

The `accepted_content_encodings` runtime setting, empty by default, lists the request content codings an operation
accepts. Values are HTTP tokens compared in lowercase, without duplicates; `gzip` is the only coding with a
builtin encoder, so another value, including `identity`, fails generation, and so does a repeated
one. As in [Declare API guarantees during generation](#declare-api-guarantees-during-generation):

```toml
[tool.datamodel-codegen.client-operations]
"/paths/~1items/post" = { runtime = { accepted_content_encodings = ["gzip"] } }
```

The generated README lists the operations that accept a coding.

### Select a coding

`Client(compression="gzip")` is the default. The SDK gzips only bodies of operations that declare gzip;
`Client(compression=None)` disables it. Views and calls inherit the client setting; a package without such an
operation has no `compression` setting. Undeclared operations,
bodyless requests, raw requests, and token requests stay uncompressed. A Content-Encoding header conflicts only
when the SDK compresses the body. The gzip encoder uses level 6 and a zero modification time.

Bytes and encoded bodies are compressed once and every retry resends the same bytes. Files, paths, iterables, and
multipart bodies are compressed as each attempt streams, without a Content-Length, and replay exactly as they would
uncompressed; a one-shot body stays one-shot.

Each protocol helper request follows its own operation's declaration and the client setting. Bodyless polls and
followed URLs stay uncompressed. Token requests are never compressed.


## Body replay and resource ownership

A package accepts the request bodies its operations declare. Without a binary or multipart request body it copies no
body runtime, and `request_raw` takes `bytes`; with a binary body `request_raw` also takes the binary inputs below, and
with a `multipart/form-data` body a `MultipartBody` (`AsyncMultipartBody` for an asyncio client) as well. Encoded
bytes, JSON included, are kept once per logical call and resent unchanged; a multipart body keeps its boundary.

A binary body, and the content of a multipart `FilePart`, is one of these inputs. Nothing is buffered or spooled to
make a one-shot input replayable.

| Input | Calls | How it is read | Framing | Sent again |
| --- | --- | --- | --- | --- |
| `bytes` | sync, async | Sent as given. | `Content-Length` | Yes |
| Binary file object with a synchronous `read` | sync, async | `read` in chunks of at most 64 KiB from its position at call entry, up to the length measured there when it can seek, else until it returns no bytes. | `Content-Length` when it can `tell` and `seek`, else chunked | Yes after seeking back; no when it cannot seek |
| `os.PathLike` path such as `Path` | sync, async | Opened in binary mode when the body is first sent, read like a file, closed when the call ends. | `Content-Length`; chunked when its file cannot seek, such as a FIFO | Yes; no when its file cannot seek |
| Iterable of `bytes` | sync, async | Iterated once; each item is sent as it is yielded. | Chunked | No |
| Async file object whose `read` is a coroutine function, such as an `anyio` or `aiofiles` file; not as a `FilePart` | async | `await read(65536)` from its current position until it returns no bytes, never line by line. | Chunked | No |
| Async iterable of `bytes`; not as a `FilePart` | async | Iterated once; each item is sent as it is yielded. | Chunked | No |

A `str` is not read as a path. `str`, `bytearray`, `memoryview`, synchronous text-mode or closed files, a path that
cannot be opened, and an async file or async iterable as a `FilePart` (HTTPX2 reads multipart files synchronously)
raise a request `DecodeError` with the reason `unencodable` before anything is sent, with the underlying failure as
`cause`. An async file is not inspected first: a text-mode or closed one fails while sending as `APIConnectionError`.

Caller files stay open, wherever the last read left them. A path the call opened is closed when the call ends; a close
failure is attached to an error already propagating, and otherwise raises `SDKError` with the reason
`cleanup_failed`. Sync calls do all file I/O on the calling thread. In async calls a path the call opens is opened,
read, and closed in a worker thread, one chunk at a time; a caller's synchronous file is read on the event loop, and
an async file where it decides. A cancelled or timed-out call waits for a running file call before it closes the file.

A retry, or a 307 or 308 redirect HTTPX2 follows, resends bytes and seeks a seekable file back to its entry position; a
failing seek raises a request `DecodeError` with the reason `body_not_replayable`. A consumed one-shot input is not
retried: the failure that would have been retried is raised. A multipart body replays when every file part can. A
failure while a file or iterable is read during sending raises `APIConnectionError` with that failure as `cause`.

### Multipart bodies

A `MultipartBody` is sent through HTTPX2's `files=` encoding, its parts in their order: each `FieldPart` and
`FilePart` keeps its name, filename, media type and extra headers, and parts may repeat a name. A field's value goes
through its member's model codec, a repeated member sends a part for each item, a styled member the parts its style
gives, and a media type the member's encoding declares must cover the one a part names. The SDK does not check parts
against the schema. A part header HTTPX2 refuses, and a part that is not a `FieldPart` or a `FilePart` with a string
name, raises a request `DecodeError` with the reason `unencodable` before sending. A body whose parts are all bytes is
encoded once, with `Content-Length`; a body without parts is sent empty, without a Content-Type. HTTPX2 chooses the
boundary.

A multipart response is read only when the operation declares one, with the standard library's MIME parser: a part's
bytes are kept as they arrived unless it names a transfer encoding, and a broken body, or a part that is itself a
multipart body or a message, raises `DecodeError` with the reason `invalid_framing`.

```python
from pathlib import Path

from pets import Client
from pets.options import RequestOptions


def upload_file(client: Client, url: str, path: Path) -> bytes:
    response = client.request_raw("PUT", url, body=path, options=RequestOptions(max_retries=2))
    return response.read()
```

## Redirects and transport construction

Calls are sent through the native client with `send(request, stream=True)`: its `auth`, event hooks, redirect
setting, framing, and content decoding are effective. `follow_redirects` of the client, a view, or a call's
`RequestOptions` is the native boolean for that call; unset, an SDK-created client follows no redirect and an injected
one keeps its own. HTTPX2 follows redirects itself, with its method rewriting, its `max_redirects` limit
(`TooManyRedirects` raises `APIConnectionError`), and its header rules: it drops `Cookie` on every redirect and
`Authorization` across origins. Every hop consumes the same call deadline, and a failure after a redirect was answered
is never sent again.

In a package that declares security schemes, a request carrying a credential at a scheme's position other than
`Authorization` (an API key in a header, query field, or cookie, however the request came to carry it) is never
redirected: its 3xx is the final response, which the typed call raises as `APIStatusError` and `with_raw_response`
returns with its `Location`. To follow it anyway, place the credential with an `httpx2.Auth` of your own on the
injected client, which HTTPX2 runs on every request it sends:

```python
import httpx2

from pets import Client


class ApiKey(httpx2.Auth):
    def __init__(self, key: str) -> None:
        self.key = key

    def auth_flow(self, request: httpx2.Request):
        request.headers["X-API-Key"] = self.key
        yield request


def following_client(key: str) -> Client:
    native = httpx2.Client(auth=ApiKey(key), follow_redirects=True)
    return Client(http_client=native)
```

Request event hooks and an `httpx2.Auth` of your own run after the follow decision, so they can still add a header such
as `X-API-Key` to a request sent to another origin.

The native client's `Accept-Encoding` is sent and response content codings are removed by HTTPX2; a body that does
not decode raises `DecodeError` with the reason `malformed_coding`. A streaming raw response's `iter_raw_bytes()`
yields the body as it arrived; a buffered one keeps only its decoded body. A HEAD operation's typed result stays
bodyless after a redirect.

A client the SDK creates is `httpx2.Client(timeout=httpx2.Timeout(600, connect=5))`, or its asyncio twin, with
every other HTTPX2 default. Configure TLS, proxies, pools, HTTP/2, and environment handling on an HTTPX2 client of
your own passed as `http_client`; the SDK keeps its settings, its timeout unless `timeout` is given, and its
ownership. Typed errors and exception bodies keep at most a 64 KiB prefix of an error body; buffered raw responses
and streams are not capped.

```python
from ssl import create_default_context

import httpx2

from pets import Client


def configured_client(ca_file: str) -> tuple[Client, httpx2.Client]:
    context = create_default_context(cafile=ca_file)
    native = httpx2.Client(verify=context, limits=httpx2.Limits(max_connections=50, max_keepalive_connections=10))
    return Client(http_client=native), native
```

An injected native client keeps its own pool, proxy, and TLS settings. The root closes only the native client it
created, once; views share their root. A borrowed client's cookies, headers, and query defaults are not merged
into SDK requests; its `Accept-Encoding` is.

## Authentication

Generated clients compile root security inheritance and operation overrides from OpenAPI. `Client` and `AsyncClient`
take one keyword argument per security scheme an operation requires, named after the scheme in snake case; a package
whose operations require none takes no credentials, copies no credential runtime, and exports no `AuthError`. A
scheme whose argument would be empty, `options`, `http_client`, `self`, or another scheme's, or whose OAuth
provider classes would take another scheme's PascalCase prefix, fails generation with `E_RESERVED_NAME`.

These examples assume a generated API with a `bearer` scheme, a `header_key` API key scheme, and an `oauth` OAuth 2
scheme with client credentials and authorization code flows, and an `auth` resource containing `bearer` and
`signed_body` operations. The calling application supplies tokens, keys, and clients.

```python
from pets import Client


def authenticated_get(token: str, key: str) -> bytes:
    with Client(bearer=token, header_key=key) as client:
        return client.auth.bearer()
```

An API key or bearer argument takes a string, and an HTTP Basic one a `(username, password)` tuple, which is encoded
as UTF-8. Each also takes a callable returning its value, which the client calls for each request, so a rotating
token or a key read from a secret store stays current; a bearer argument also takes an OAuth provider. A callable's
failure raises `AuthError` with the reason `provider_failed` before the request is sent, and a value of the wrong type
`ConfigurationError` with the reason `invalid_type`. No environment variables or credentials are discovered by
default.

```python
from collections.abc import Callable

from pets import AsyncClient


async def rotated_get(read_token: Callable[[], str]) -> bytes:
    async with AsyncClient(bearer=read_token) as client:
        return await client.auth.bearer()
```

A call sends the credentials of its operation's first security alternative they all satisfy: an AND alternative
needs all its schemes, and an OR list is tried in declared order. Each credential goes to the header, query field, or
cookie its scheme declares, replacing a value a client, view, or call header or a parameter put there, and only to the server's
origin: a page a server links at another origin, and `request_raw`, carry none of them. Absent security, explicit
`[]`, and an empty alternative are anonymous choices that apply only when no other alternative is satisfied, so an
optional operation sends a credential given for a listed scheme and, with none, sends nothing. A required operation that no credential satisfies raises `ConfigurationError` with the reason
`missing_credentials` before sending, unless the HTTP client has an Auth of its own, which it then uses. The client
authorizes no scopes: the resource server decides, and a 403 is terminal.

`auth` of the client, a view, or a call's `RequestOptions` takes any `httpx2.Auth`, which replaces the credentials
for those calls, and `auth=None` sends without an Auth; it cannot make a required operation anonymous. Unset, the
credentials apply, or else the HTTP client's own `auth`. Credentials given beside `Client(auth=...)` or an injected
HTTP client with an `auth` raise `ConfigurationError` with the reason `conflicting_auth`, since one
request runs one Auth flow.

Declare an API-specific challenge-less 401 contract in the operation's `runtime` setting. It is false unless
explicitly set and is recorded under `operations[].runtime.auth_challenge_less_401` in generated documentation.
It lets an OAuth provider renew its token after a 401 without a `WWW-Authenticate` challenge.
It is not a client option or an inferred response behavior.

```toml
[tool.datamodel-codegen.client-operations]
"/paths/~1challenge-less/get" = { runtime = { auth_challenge_less_401 = true } }
```

### Sign requests with an HTTPX2 Auth

Request signing is an `httpx2.Auth` of your own, given as `auth`. Its flow sees the final request, body included,
after the client framed it; set `requires_request_body = True` to read a streamed body first. HTTPX2 runs it once per
send, and every retry sends the original request through it again.

```python
import hashlib
import hmac
from collections.abc import Generator

import httpx2

from pets import Client


class BodySigner(httpx2.Auth):
    requires_request_body = True

    def __init__(self, key: bytes) -> None:
        self.key = key

    def auth_flow(self, request: httpx2.Request) -> Generator[httpx2.Request, httpx2.Response, None]:
        digest = hmac.digest(self.key, request.method.encode() + b"\n" + request.content, hashlib.sha256)
        request.headers["X-Request-Signature"] = digest.hex()
        yield request


def signed_upload(client: Client, key: bytes, payload: bytes) -> bytes:
    view = client.with_options(auth=BodySigner(key))
    return view.auth.signed_body(body=payload)
```

A signature header other than `Authorization` is copied to a redirect HTTPX2 follows; set `follow_redirects=False`
for signed calls whose redirects may leave the server's origin.

Credential values do not appear in repr. A credential placed in the query is part of the request URL,
which HTTPX2 logs at INFO level on its `httpx2` logger, so keep that logger above INFO wherever URLs must stay
private. Errors retain safe metadata and their causes, which can hold an exception a credential callable raised;
apply your own policy before logging causes.

### OAuth providers

A package whose operations require an OAuth 2 or OpenID Connect scheme exports `ClientCredentials`, `RefreshToken`,
and `TokenSet` from its `auth` module, and one subclass for each declared flow whose token URL the API declares, such
as `OauthClientCredentials` for the `oauth` scheme's client credentials flow. A subclass requests tokens from its
declared URL unless `token_url` names another; the base classes need `token_url`. A provider is a bearer credential:
the call that needs a token requests it inline while holding the provider's lock, a `threading.Lock` or an
`asyncio.Lock` on the event loop the provider is used on, concurrent calls wait for that token, and nothing runs in
the background or is persisted.

```python
from pets import Client
from pets.auth import OauthClientCredentials


def list_pets(secret: str) -> None:
    provider = OauthClientCredentials(client_id="pets-service", client_secret=secret, scopes=("pets.read",))
    with Client(oauth=provider) as client:
        client.pets.list_pets()
```

`ClientCredentials` requests a confidential client's own token. Its request sends the `scopes` in order and the
`audience` when one is given, and authenticates the client with `client_secret_basic` (the default) or
`client_secret_post`; the `client_secret` is a string or a callable returning one, called for each token request.

`RefreshToken` keeps an access token current with the refresh token grant, starting from the `TokenSet` an authorization
produced. The provider alone refreshes it: no other provider or process may send the same refresh token, and since
synchronous calls and each event loop renew under a lock of their own, use it from one of them at a time. Its client
authentication defaults to `none`, which sends the `client_id` of a public client.

```python
from pets import Client
from pets.auth import OauthRefreshToken, TokenSet


def save(tokens: TokenSet) -> None: ...


def list_pets(tokens: TokenSet) -> None:
    provider = OauthRefreshToken(tokens, client_id="pets-app", on_token_refreshed=save)
    with Client(oauth=provider) as client:
        client.pets.list_pets()
```

Each successful refresh makes a new `TokenSet` current, keeping the refresh token sent when the response gives none,
and then passes it to `on_token_refreshed` while the provider still holds its lock, so an application can persist each
token set in order; for an async client the callback may return an awaitable, which is awaited. An exception the
callback raises reaches the caller, and the refreshed token set stays current. The callback must not call its own
provider. A `TokenSet` needs a timezone-aware `expires_at` when its access token expires, and its repr omits both
tokens. `invalid_grant` raises `AuthError` with the reason `reauthorization_required`, and the application starts a
new authorization; a token set without a refresh token serves its access token until it expires or a resource rejects
it, and then raises the same error without a request.

A provider serves its token until a tenth of its lifetime, at most thirty seconds, remains, and then the call that
finds it due requests another; a token without `expires_in` is kept until a resource rejects it. When an early renewal
fails, the call keeps using the current token until it expires, and each later call in that margin tries the renewal
again. A 401 whose `WWW-Authenticate` has a Bearer
`invalid_token` error, or a 401 without a challenge on an operation declaring `auth_challenge_less_401`, renews the
token once, unless another call already replaced it, and sends the request again when its body can be sent again.
A 401 after a redirect, and a WebSocket handshake's, renews nothing.

Token requests go through the client's HTTP client without its Auth and without following redirects, with the calling
request's timeouts, or through the provider's own `http_client` when one is given, which keeps its own settings. The
client's HTTP client runs its event hooks on these token requests and responses, which carry the client authentication
and tokens. Used as an `httpx2.Auth` elsewhere, a provider needs its own `http_client`. The token URL must be HTTPS, or
HTTP to a loopback host. A rejection, an unexpected status, or a response that is not a JSON object with a Bearer
`access_token` raises `AuthError` with the reason `oauth_error`, a rejection keeping its `status_code` and standard
`oauth_error` code, and an `expires_in` that is not a positive number, or a refreshed one past the last date Python can
hold, the reason `invalid_expiry`. A transport failure
raises `AuthError` with the reason `oauth_error`, or `timeout`, and the native exception as its cause, which can hold
the token request and its client authentication. A provider's `clock=Clock(...)` times its token expiry.

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
) -> VerifiedWebhook[models.Message]:
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
`UnsupportedAlgorithm` raises `ConfigurationError` with `reason="wrong_capability"` and the key's
`("keys", "<index>")`, and any other error, including cancellation, propagates unchanged. The signature settings are the
same as for HMAC:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.public-keys-json -->
<!-- fmt: off -->

```json
{
  "rfc8032.ed25519": {
    "kind": "webhook",
    "event_schema": {"pointer": "/components/schemas/Push"},
    "signature": {"kind": "ed25519", "header": "X-Signature", "encoding": "hex", "prefix": ""}
  },
  "wycheproof.rsa": {
    "kind": "webhook",
    "event_schema": {"pointer": "/components/schemas/Count"},
    "signature": {"kind": "rsa-pss-sha256", "header": "X-Signature", "encoding": "hex", "prefix": ""}
  }
}
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.public-keys-json -->

### Signature settings

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.json -->
<!-- fmt: off -->

```json
{
  "standard.message": {
    "kind": "webhook",
    "event_schema": {"pointer": "/components/schemas/Message"},
    "signature": {"kind": "standard_webhooks"}
  }
}
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.json -->

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
--client-protocols['shape.values'].signature must be a signature
--client-protocols['shape.kind'].signature.kind must be 'standard_webhooks', 'stripe_style', 'body_hmac', 'ed25519', 'rsa-pss-sha256', 'adapter' or 'none'
--client-protocols['shape.ed25519'].signature needs 'header'
--client-protocols['shape.adapter'].signature needs 'timestamp'
--client-protocols['shape.adapter'].signature needs 'delivery_id'
--client-protocols['shape.adapter_facts'].signature has no key 'header'
--client-protocols['shape.adapter_facts'].signature.timestamp must be 'required' or 'none'
--client-protocols['shape.adapter_facts'].signature.delivery_id must be 'required' or 'none'
--client-protocols['shape.none'].signature has no key 'prefix'
--client-protocols['shape.settings'].signature has no key 'extra'
--client-protocols['shape.settings'].signature.header must be a header name
--client-protocols['shape.settings'].signature.encoding must be 'hex' or 'base64'
--client-protocols['shape.settings'].signature.prefix must be visible ASCII text
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
`location=BodySelector(pointer=...)` and the reason `missing` for an absent member, `null`, `type` for another JSON
type, or `value` for an unknown name. There is no `unknown` setting for webhooks.

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.adapter-json -->
<!-- fmt: off -->

```json
{
  "stripe.event": {
    "kind": "webhook",
    "event_schema": {
      "discriminator": {"from": "body", "pointer": "/type"},
      "mapping": {
        "invoice.paid": {"pointer": "/components/schemas/Invoice"},
        "invoice.updated": {"pointer": "/components/schemas/Invoice"},
        "customer.created": {"pointer": "/components/schemas/Customer"}
      }
    },
    "signature": {"kind": "adapter", "timestamp": "required", "delivery_id": "none"}
  },
  "unsigned.event": {
    "kind": "webhook",
    "event_schema": {
      "discriminator": {"from": "body", "pointer": "/meta/type"},
      "mapping": {
        "invoice.paid": {"pointer": "/components/schemas/Invoice"},
        "customer.created": {"pointer": "/components/schemas/Customer"}
      }
    },
    "signature": {"kind": "none"}
  }
}
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.adapter-json -->

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
        """Return the signed timestamp and the matched key, or raise ProtocolDataError."""
        del now
        values = [value for name, value in ordered_headers if name.lower() == "stripe-signature"]
        items = [item.partition("=") for item in (values[0].split(",") if len(values) == 1 else ())]
        stamps = [value for scheme, _, value in items if scheme == "t"]
        encoded = [value for scheme, _, value in items if scheme == "v1"]
        if len(stamps) != 1 or not (stamps[0].isascii() and stamps[0].isdigit()) or not encoded:
            raise ProtocolDataError(reason="malformed_signature")
        if (count := len(encoded)) > limits.max_signatures:
            raise ProtocolDataError(reason="too_large")
        try:
            signatures = [bytes.fromhex(value) for value in encoded if value.isascii()]
        except ValueError:
            signatures = []
        if len(signatures) != count:
            raise ProtocolDataError(reason="malformed_signature")
        signed = stamps[0].encode() + b"." + raw_body
        for key in keys.keys:
            expected = hmac.new(key.secret, signed, "sha256").digest()
            if any(hmac.compare_digest(expected, signature) for signature in signatures):
                moment = datetime.fromtimestamp(int(stamps[0]), timezone.utc)
                return VerifiedSignature(delivery_id=None, timestamp=moment, matched_key_id=key.name)
        raise ProtocolDataError(reason="invalid_signature")


def receive_stripe(raw_body: bytes, headers: list[tuple[str, str]], secret: bytes) -> Invoice | Customer:
    """Verify one Stripe-style delivery with the application's verifier and return its mapped event."""
    keys = KeySet(keys=(StripeKey(name="2026-09", secret=secret),))
    verified = event.verify(raw_body, headers, keys, verifier=StripeVerifier(), now=datetime.now(timezone.utc))
    return verified.data
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.webhooks.adapter -->

`verify_async` calls the same synchronous verifier, once. After the helper checks its arguments, including that
`verifier` has a callable `verify` (or `ConfigurationError` with `field_path=("verifier",)`), and the body, header, and
key-count limits, it passes the exact body, the headers as a tuple of pairs in received order, the key set, `now`, and
the resolved limits; it never reads the keys. The verifier must authenticate the whole body and every fact it returns,
honor `max_signatures`, and raise `ProtocolDataError` with the reason `malformed_signature`, `invalid_signature`,
`missing_key`, or `timestamp_window` when verification fails; every error it raises, including cancellation, propagates
unchanged. Before decoding, the helper raises `ConfigurationError` with `field_path=("verifier",)` and the reason
`invalid_result`, without decoding, when the result is not a `VerifiedSignature` (an awaitable result is closed
unawaited), its `matched_key_id` is not a nonempty string, a `required` fact is missing, not a nonempty string delivery
id, or not an aware datetime, or a `none` fact is not `None`. A returned timestamp is then checked against the timestamp
window, as for builtin signatures.

### Unsigned webhooks

A signature `{kind: none}` declares that the API does not sign its deliveries; a helper without `signature` is refused,
so omitting it never means unsigned. The module has only `decode_unverified(raw_body, *, options=None) -> Event`, which
refuses a body that is not `bytes` (`ConfigurationError`) or is over `max_body_bytes` (`ProtocolDataError` with the
reason `too_large`), the only option it reads, and decodes the event. Nothing authenticates who sent the delivery or
whether it was changed or replayed, and the result is the event alone, with no facts: treat it as untrusted input.

### Verification order and limits

A call checks `raw_body`, `headers`, `keys`, `now`, and `options`, then each builtin key's type and unique id. Invalid
arguments raise `ConfigurationError` with their field path. Body, header, key, and signature counts are bounded by
`ProtocolDataError` with the reason `too_large`. The helper checks signature syntax, timestamp tolerance when present,
and cryptographic authenticity before decoding the model. Decoding errors raise `ProtocolDataError`. An adapter checks
that `verifier.verify` is callable, then sizes, its returned facts, and the timestamp tolerance before decoding. Errors
keep no key, signature, header value, or body.

| Limit (`WebhookOptions`) | Default |
|---|---|
| `max_body_bytes` | 8 MiB |
| `max_header_bytes`, counting each name and value | 16 KiB |
| `max_keys`, `max_signatures` | 8 |
| `past_tolerance`, `future_tolerance` | 300 and 30 seconds; 0 is allowed |

A failed verification raises `ProtocolDataError` with one of these reasons:

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
