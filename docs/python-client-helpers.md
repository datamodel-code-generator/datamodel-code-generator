# Python Client Protocol Helpers

!!! warning "Experimental"

    HTTPX2 client generation is experimental. Its options, generated package, and template values may change. See
    [Experimental Features](experimental.md).

Many APIs repeat one pattern across their operations: a list split into pages, a job you start and then poll, a file
sent in chunks, or a stream of events. A *protocol helper* is generated code for one such pattern. You describe the
pattern once, in a JSON *helper file* that names the operations it uses, and generation adds an object at
`client.protocols.<name>` that runs the whole exchange, such as a pager that fetches every page.

This page covers the helper file first, then each helper kind: [pagination](#pagination-helpers),
[polling](#polling-helpers), [resumable uploads](#upload-helpers), [SSE](#sse-stream-helpers) and
[NDJSON](#ndjson-stream-helpers) streams, [WebSockets](#websocket-helpers), a [cache](#cache-helpers), and
[webhook verification](#webhook-verification-helpers). The [records, options, and exceptions](#records-options-and-exceptions)
the helpers share come last. Read [Python Client Generation](python-client.md) first: helpers build on the generated
client and its settings.

The examples use the layout of the guide's quick start: the generated package `app.client` and its models
`app.models`, inside the application package `app`. The text calls the generated package `client`, as in
`client.protocols`.

Terms used throughout:

- A *selector* reads a value from a response: a JSON pointer into its body, a header, or its status.
- A *request target* is where a helper writes a value into the next request: a parameter, or a member of the body.
- A *binding* copies a value into a request target: a literal, or what a selector reads from an earlier response.
- A *session* is one run of a helper, such as one pager or one polled operation. Each request in it is still its own
  call, with its own retries and timeouts, and the session's limits, such as `max_pages` or `total_timeout`, bound
  them all.
- A *checkpoint* is plain JSON that a helper returns so that a later session can continue where this one stopped.

## Configure helpers

Pagination, polling, SSE or NDJSON stream, WebSocket, webhook, cache, and resumable upload helpers of an API are declared
in a helper file, which [`--client-protocols`](cli-reference/target-generation-options.md#client-protocols) names. Generation validates every
helper and resolves its references against the selected API. An enabled
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
| `--client-protocols` | A helper file path, a JSON object, or none | None | `--client-protocols helpers.json` or inline JSON; `client-protocols = "helpers.json"` or a table in `pyproject.toml`; `generate()`: `client_protocols`, a mapping. The documents a helper file names are relative to the file; those of inline JSON or a mapping are relative to the working directory, and those of a `pyproject.toml` table to its directory |

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.protocols.pyproject -->
<!-- fmt: off -->

```toml
[tool.datamodel-codegen]
input = "options.yaml"
input-file-type = "openapi"
output = "app/models.py"
output-model-type = "pydantic_v2.BaseModel"
openapi-scopes = ["schemas", "api"]
target-python-version = "3.11"
formatters = ["builtin"]
disable-timestamp = true
generate-client = "httpx2"
client-output = "app/client"
client-package = "app.client"
client-model-package = "app.models"
client-protocols = "protocols.json"
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.protocols.pyproject -->

The helper file is read only when the target is generated, so its problems are reported then, with the target's
identity. Without `--client-protocols`, nothing is read.

### The helper file

The file holds one JSON object mapping each helper's name to its definition, in declaration order. Every definition
has an `enabled` flag, `true` by default. The example below sets `"enabled": false` on each helper: generation still
validates a disabled helper's definition, but generates nothing for it. Leave the key out, or set it to `true`, to
generate the helper:

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
| `websocket` | `operation` (a GET without a body), `send`, `receive` (each `{codec: json\|utf8\|bytes, frame?, schema?}`) | `subprotocols` (`[]`), `enabled` (`true`) |
| `webhook` | `event_schema`, `signature` (see [webhook verification helpers](#webhook-verification-helpers)) | None |
| `cache` | `operation`, `validator` (`etag`, `last_modified`, or `both`), `authenticated` (a boolean) | `statuses` (`[200]`), `vary_allowlist` (`[]`); see [cache helpers](#cache-helpers) |
| `resumable_upload` | `profile` (`offset`), `create` (`{operation, size?, expires_at?}`), `probe` (`{operation, remote_offset, bindings?}`), `append` (`{operation, offset, length?, checksum?, bindings?}`, a `checksum` being `{target, algorithm, encoding?, algorithm_prefix?}`), `partial_commit` (`allowed` or `forbidden`), `completion` | `abort` (`{operation, bindings?}`) and `create.session_url`, both refused as not supported yet |

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
derived from one of these names, the argument takes the model's suffix, such as `items_1`; an explicit name of
`parameter_names`, `body_field_names`, or `--aliases` that takes one fails generation instead of being renamed:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.protocols.diagnostics -->
<!-- fmt: off -->

```text
--client-protocols['tags.all']: The pagination helper 'tags.all' reserves the argument 'items' of GET /tags; rename them with the operation's parameter_names or body_field_names in --client-operations
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.protocols.diagnostics -->

### Helpers in Python

`generate()` takes the object of the helper file as a mapping in `client_protocols`, validated as the file is, with
the same messages:

```python
from pathlib import Path

from datamodel_code_generator import generate

generate(
    Path("openapi.yaml"),
    input_file_type="openapi",
    openapi_scopes=["schemas", "api"],
    output_model_type="pydantic_v2.BaseModel",
    target_python_version="3.11",
    output=Path("app/models.py"),
    generate_client="httpx2",
    client_output=Path("app/client"),
    client_package="app.client",
    client_model_package="app.models",
    client_protocols={
        "users.all": {
            "kind": "pagination",
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

### Helper settings on the client

Helper settings belong to the root client only: neither `with_options` nor `RequestOptions` takes them, and views share
the root's. A root takes `helper_defaults` when the package declares a helper, `cache_stores` when it declares a cache
helper, and `allowed_origins` when it declares a pagination helper that follows a server's next URL or `Link` header.

```python
from app.client import Client
from app.client.protocols import PaginationOptions

client = Client(helper_defaults={"users.all": PaginationOptions(max_pages=10, total_timeout=300)})
```

| Setting | Type | Meaning |
|---|---|---|
| `helper_defaults` | `Mapping[str, HelperOptions] \| None`, default `None`, where `HelperOptions` stands for the union of the six option classes and is not importable | The options of each helper, by helper name: `PaginationOptions`, `PollOptions`, `StreamOptions`, `CacheOptions`, `WSOptions`, or `UploadOptions`, of the helper's kind, each imported from `client.protocols`. The mapping is copied |
| `cache_stores` | `Mapping[str, CacheStore] \| None` (`AsyncCacheStore` on `AsyncClient`), default `None` | The store each [cache helper](#cache-helpers) keeps its entries in, by helper name. The mapping is copied and keeps each store's identity; the client borrows the stores and never closes them |
| `allowed_origins` | `Sequence[Origin] \| None`, default `None` | The origins beyond the server's that a next-URL or Link pagination helper may follow a URL to; an origin listed twice counts once |

The client checks these settings when it is constructed: a name that is no helper of the package raises
`ConfigurationError` with `unknown_field`, and options of another kind, or no options record, with `invalid_value`.
Explicit call options take precedence over these defaults, which take precedence over the effective defaults above.

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
        X_Snapshot: str | UNSET = UNSET,
        pagination_options: PaginationOptions | None = None,
        options: RequestOptions | None = None,
    ) -> Page[models.User, ListUsersResponse]:
        """Fetch the first page of GET /users."""
        return first_page(
            self._core,
            _plans.PLAN_0,
            (cursor, limit, X_Snapshot),
            pagination_options=pagination_options,
            options=options,
        )
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.pagination.helper -->

```python
from app.client import Client

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
`AsyncPager` are imported from `client.protocols`, in every package. A pager fetches a page only once the previous one is
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
integer of at least 0, which a number with a fraction or an exponent is only when its text is integral: `1.0` and
`1e0` count 1 and `9007199254740993.0` counts exactly that, while `9007199254740993.00000000000000000001` is mistyped,
since the total is read from the body's text rather than from a rounded float. A header spells `true` or `false`, or
decimal digits with an optional `-`, and generation checks
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
encoded once, so no query pair is added. The values a server gave are never checked against their targets' schemas;
a header parameter still takes [header text](python-client-reference.md#model-codecs) only, as in any call. A
call's `extra_headers` and `extra_query` must not name a header or a query parameter the helper writes: `iterate`,
`page`, and `next_page` raise `ConfigurationError` with a `field_path` of `("options", "extra_headers" or
"extra_query", <name>)` before sending. A binding
with `source: input` is not supported yet.

### Pagination limits and sessions

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

### Pagination checkpoints and resume

`pager.checkpoint()` returns the continuation the pager fetches its next page with: the server's cursor, the next
offset or page number, or the resolved next URL, the same value as `Page.continuation`. It sends nothing, on `Pager`
and `AsyncPager` alike, and is None before the first page and after the last. The helper's `resume(state, ...)` takes
that value with the operation's arguments and any request body again, as `iterate` takes them, and returns a pager in a
session of its own that sends nothing until it is iterated; it is not awaited, even on `AsyncClient`.

```python
from app.client import Client
from app.client.errors import SessionLimitError
from app.client.protocols import PaginationOptions

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

### Pagination generation checks

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
through a union or a map are not supported yet.

## Polling helpers

An enabled polling helper is generated at `client.protocols.<name>` on `Client` and `AsyncClient` alike. Its `start`
takes the `create` operation's parameters and its body as keywords, never field arguments, then `poll_options`,
and `options`, sends the create request, and returns a handle of the long-running operation (LRO) it created, an
`LroHandle`; the asyncio `start` is awaited:

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
from app.client import Client
from app.models import JobRequest


def run(job: JobRequest) -> None:
    with Client() as client, client.protocols.jobs.run.start(body=job) as handle:
        print(handle.status())
        print(handle.wait())
```

`LroHandle[T, P]` and `AsyncLroHandle[T, P]`, imported from `client.protocols`, hold one operation: `T` is the result
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
`client_closed`. The handle's [checkpoint](#polling-checkpoints-and-resume) continues it after that
`SessionLimitError`.

### Polling limits and sessions

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

### Polling checkpoints and resume

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

from app.client import Client
from app.client.errors import SessionLimitError
from app.models import JobRequest


def run(job: JobRequest) -> None:
    with Client() as client:
        helper = client.protocols.jobs.run
        handle = helper.start(body=job)
        saved = json.dumps(handle.checkpoint())
        print(helper.resume(json.loads(saved)).wait())
        handle = helper.start(body=job)
        try:
            handle.wait()
        except SessionLimitError as error:
            if error.reason == "wait" and error.required_wait is not None:
                time.sleep(error.required_wait)
            print(helper.resume(handle.checkpoint()).wait())
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
which returns a `CancelReceipt[C]`, imported from `client.protocols`, where `C` is the cancel operation's response type;
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

The concrete handle classes are not exported from `client.protocols`: use the type `start` and `resume` return, or
annotate a handle as `LroHandle[T, P]` or `AsyncLroHandle[T, P]`, which they subclass, without `cancel_remote`.

`expires_at` is a selector of the server's expiry of the operation, read once from the accepted create response: a
string giving an RFC 3339 date-time with an offset, where a leap second is the second after the one before it, or
an HTTP date, read as [`Retry-After` dates](python-client-reference.md#retries-and-operation-contracts) are. A missing, null, non-string, or unparsable value fails `start` with `ProtocolDataError` after the create
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

### Polling generation checks

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
binding, and pointers that read through a union or a map are not supported yet.

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
        Tus_Resumable: str,
        X_Name: str | UNSET = UNSET,
        upload_options: UploadOptions | None = None,
        options: RequestOptions | None = None,
    ) -> UploadHandle[None]:
        """Measure the source, create the upload of POST /files, and return its handle."""
        return start_upload(
            self._core,
            _plans.PLAN_0,
            source,
            (Tus_Resumable, X_Name),
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
from app.client import Client

with Client() as client, open("video.mp4", "rb") as video:
    upload = client.protocols.files.upload
    handle = upload.start(video, Tus_Resumable="1.0.0")
    print(handle.advance())
    state = handle.checkpoint()
    handle.run()
```

`UploadHandle[T]` and `AsyncUploadHandle[T]`, imported from `client.protocols`, hold one upload: `T` is `None` for an
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

A chunk is `UploadOptions.chunk_bytes`, 8 MiB by default; set it in `helper_defaults` or per call for a server that
takes another size. The offset profile sends one chunk at a time.

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

### Upload checkpoints and resume

`checkpoint()` returns plain JSON: an object with the content's `size`, the `chunk` size, the `confirmed` offset, the
values the probe, the append, and the completion write (`bound`, three arrays), a completion of unknown outcome
(`phase` `unknown`, otherwise `uploading`), and the server's `expires_at`. It holds
no content, digest, result, credential, or security binding: a resumed upload sends with the resuming client's own
auth, as pagination checkpoints do. Errors carry no checkpoint; call the handle's `checkpoint()`.

`resume(source, state)` starts a new session and never creates the upload again. It checks the call's options, then the
checkpoint: a value that is not JSON or does not fit the helper raises `ConfigurationError(field_path=("state",))`, and
one past the server's expiry the reason `expired`. A checkpoint keeps its chunk size, which must not exceed the call's
`UploadOptions.chunk_bytes`, since an append holds one chunk in memory; it raises `ConfigurationError` whose
`field_path` names where that size came from: `("helper_defaults", "files.upload", "chunk_bytes")` for a helper
named `files.upload` when the client's `helper_defaults` set it and the call's `upload_options` did not, else
`("upload_options", "chunk_bytes")`, which also covers the 8 MiB default. Then the saved values each call writes: a value that makes a path segment `.` or `..` raises
`ProtocolDataError`, as if a server gave it; any other saved value is encoded when its request is built. Then the
source: a one-shot input raises `ConfigurationError` with the reason `wrong_capability`, and a size other than the
checkpoint's the reason `source_changed`; the source is not read. A completion of unknown outcome raises
`APIConnectionError` with the reason `delivery_unknown` again without sending. Otherwise it probes the server's offset
once, which must not be below the saved one, and returns the handle.

A server's expiry is read once from the create response at `create.expires_at`, an RFC 3339 date-time with an offset or
an HTTP date; a missing, null, or unparsable value raises `ProtocolDataError` from `start`. It bounds only checkpoints
once it has passed: `resume` raises `ConfigurationError` with the reason `expired`; uploading goes on until the server
refuses.

### Upload limits and sessions

`start` and its handle are one session, and so are `resume` and its handle. The create call, every probe, every append,
and the completion are calls of their own, with their own retries, total timeout, and idempotency key, and the session
bounds all of them. Each limit comes from the call's options, then the helper's options in the client's
`helper_defaults`, then the default below:

| Limit | Default | None |
|---|---|---|
| `UploadOptions.chunk_bytes` | 8 MiB | Not allowed |
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
bindings that read the previous response or the helper's input are not supported yet.

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
        Last_Event_ID: str | UNSET = UNSET,
        stream_options: StreamOptions | None = None,
        options: RequestOptions | None = None,
    ) -> EventStream[models.Message]:
        """Open the event stream of GET /events, returning once its response is a declared success."""
        return open_events(
            self._core,
            _plans.STREAM_0,
            (topic, Last_Event_ID),
            stream_options=stream_options,
            options=options,
        )
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.streams.helper -->

```python
from app.client import Client

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
`UnknownEvent`, `EventStream`, and `AsyncEventStream` are imported from `client.protocols`, in every package.

The response's status and `Content-Type` are checked before `open` returns: an error status raises the `APIStatusError`
subclass of its status, an undeclared status `APIStatusError` with the reason `unexpected_status`, and a body of another
media type, or none, `DecodeError` with the reason `unexpected_media_type`. A `204` the operation declares without a
body, by its own status, its range, or `default`, returns a stream that ends with no events whatever its completion, as
an `EventSource` stops on a `204`; a reopen or `resume` answered so ends the stream too, reading no binding value or
expiry from it, and an undeclared `204` still fails. Its acquisition retries like any call. A
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
import json
from pathlib import Path

from app.client import Client

saved = Path("events.checkpoint")
with Client() as client, client.protocols.events.live.open() as stream:
    for event in stream:
        print(event.data)
        saved.write_text(json.dumps(stream.checkpoint()))
with Client() as client, client.protocols.events.live.resume(json.loads(saved.read_text())) as stream:
    for event in stream:
        print(event.data)
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
reason `wrong_capability`, and one the request cannot encode, such as an event ID that ends in a space or is not
ASCII, which a header cannot carry, raises the request's `DecodeError`.

With `StreamOptions(reconnect=True)`, a stream that has delivered a cursor reopens itself within the same step after a
read-phase transport failure the shared retry classification retries, or a read timeout the call's own `timeout`
set rather than the stream's idle limit, which wins a tie; after an NDJSON line cut by the
end of the body or an end before the declared completion it does so only when `reconnect_on` lists `incomplete_eof`.
Each reopen is one more child call of the stream's session: its own retries, Retry-After included, follow the call's
retry options. Before a reopen the stream waits the retry backoff, and at least the last `retry` time the server sent,
in the session's deadline. When the backoff's cap or that `retry` time is longer than `max_reconnect_wait`, whatever the
jitter draws below the cap, or the wait is not shorter than the session's remaining time, the wait is not begun and the
interruption is raised. Running out of reconnections raises `SessionLimitError` with the reason `reconnects`,
never a normal end. A cursor a reconnection cannot encode, such as an event ID that is not ASCII, raises
`ProtocolDataError` with the reason `value` and the cursor's selector, or for an event ID the target it is written
to, as `location`, with the interruption as its context,
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
events.

A resumption is checked against its reopen operation, which must declare a success response of the helper's media type.
A cursor is never written to a cookie, a credential header, a security scheme's position, an exploded form object query
parameter declaring a property at such a position, or a path parameter, and one that can be cleared, an event ID or a
body cursor with `null: clear`, only to an optional header or query parameter; a body cursor must read a declared
property of an event schema, of every one under `missing: error`, whose types fit its target. Bindings are checked as
polling bindings are, reading only the headers and status of the stream responses, and a reopen with another operation
must write each of its required parameters and its body.

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
from app.client import Client

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
and resumption checks are those of SSE helpers, and an NDJSON cursor is always a body pointer.

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
import asyncio

from app.client import AsyncClient, Client
from app.models import ClientMessage

with Client() as client, client.protocols.rooms.chat.connect(room="lobby") as session:
    session.send(ClientMessage(text="hi"))
    for message in session:
        print(message.sequence, message.data)


async def chat() -> None:
    async with AsyncClient() as client, client.protocols.rooms.chat.connect(room="lobby") as session:
        await session.send(ClientMessage(text="hi"))
        async for message in session:
            print(message.sequence, message.data)


asyncio.run(chat())
```

```json
{
  "rooms.chat": {
    "kind": "websocket",
    "operation": "/paths/~1rooms~1{room}~1socket/get",
    "subprotocols": ["chat.v2", "chat.v1"],
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
subprotocols in order (`[]` by default). `enabled` defaults to `true`; a disabled helper generates nothing, as for every
helper kind. HTTPX2 WebSockets do not negotiate `permessage-deflate`, so the helper has no compression setting.

`WebSocketSession`, `AsyncWebSocketSession`, `Message`, `PingReceipt`, and `WSOptions` are imported from
`client.protocols`, and the WebSocket exceptions from `client.errors`. Sessions run on `httpx2.websockets`, so generation
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
| `WSOptions.max_message_bytes` | HTTPX2's default (64 KiB in 2.13); a set value is passed as its `max_message_size_bytes` | Not allowed |
| `WSOptions.ping_interval`, `pong_timeout` | 20 seconds each, HTTPX2's keepalive settings; `pong_timeout` also bounds `ping()` | No keepalive pings |
| `WSOptions.total_timeout` | None | No session budget |

A message over `max_message_bytes`, or over HTTPX2's default while that is unset, raises `ProtocolDataError` with the
reason `too_large` after HTTPX2 closed the connection with 1009. A receive that waits longer than the idle timeout raises `APITimeoutError` with the reason
`phase_timeout` and closes with 1001, as does a ping whose pong does not arrive within the pong timeout; another
failure closes with 1011. A missed keepalive pong makes HTTPX2 close with 1011, and the next `receive` raises
`APIConnectionError`. The session deadline bounds every wait and raises `APITimeoutError` with
the reason `deadline_exceeded`. Messages are written whole by HTTPX2, without a timeout of their own; a send that may
have reached the server raises `APIConnectionError` with the reason `delivery_unknown`, closes the session, and is never
sent again. Received messages wait in HTTPX2's queue until they are read. Sessions never reconnect. Options of
another type raise `ConfigurationError`.

### WebSocket generation checks

The operation must be a GET without a request body, and each JSON message's schema must exist. Message definitions, frames, and subprotocol tokens are checked as the helper file is read.

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
| `authenticated` | Whether the fetch carries credentials. It must match every call: a call that the auth, a credential or cookie header, or a security scheme's field authenticates needs `true`, and any other call `false` |
| `statuses` | The cacheable statuses, distinct, from 100 to 599 |
| `vary_allowlist` | The request headers a response's `Vary` may name; a response that varies on any other header, or on `*`, is not stored. The headers credentials travel in (`Authorization`, `Proxy-Authorization`, `Cookie`, `Cookie2`, and the declared security schemes' headers) are allowed as well, since the key covers them. Responses behind a CDN often vary on `Accept-Encoding`: allow it to store them |

`fetch` takes the operation's parameters as keywords, then `cache_options` and `options`, and returns a `CacheResult`.
With asyncio, `fetch` is a coroutine:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.cache.helper -->
<!-- fmt: off -->

```python
    def fetch(
        self,
        *,
        fields: str | UNSET = UNSET,
        Accept_Language: str | UNSET = UNSET,
        userId: int,
        cache_options: CacheOptions | None = None,
        options: RequestOptions | None = None,
    ) -> CacheResult[GetUserResponse]:
        """Fetch GET /users/{userId} through the helper's cache, revalidating a stale entry."""
        return fetch(
            self._core,
            _plans.PLAN_0,
            (fields, Accept_Language, userId),
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
than the entry's raises `ProtocolDataError` with the reason `inconsistent`; no request is sent again. A validator
that is not [header text](python-client-reference.md#model-codecs), such as an entity tag with non-ASCII text, is never sent back, since no
client can write it as every server reads it: it counts as no validator, so its entry is fetched again without a
condition, and a response that is not fresh is not stored for it.

### Storing and keys

A response is stored only when its status is cacheable, it came without a redirect, it has no `Set-Cookie`, its
`Cache-Control` has only RFC 9111 directives (`s-maxage` is ignored) and no `no-store`, its `Vary` names only
allowlisted headers, and it is fresh or can be revalidated. The body's size is not limited: whatever HTTPX2 returned is
stored. A response that cannot be stored removes the entry it supersedes, and errors and decoding failures store
nothing and keep the entry. Entries hold the body after content decoding and the headers without `Content-Encoding` or
hop-by-hop fields.

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
and a 304 without a usable entry raises `ProtocolDataError`. A validator a header cannot carry as ASCII text is never
sent, and its entry is fetched again without it. A response is stored only when its status is cacheable, it
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
declared anonymous cannot fetch an operation that requires credentials.

## Webhook verification helpers

An enabled `webhook` helper generates the module `client.webhooks.<name>`, where each dotted part of the helper's name is a
package or the module. It verifies one received delivery, with a builtin `standard_webhooks`, `stripe_style`, `body_hmac`, `ed25519`, or
`rsa-pss-sha256` signature or through your verifier for an [`adapter` signature](#adapter-signatures), and decodes its
JSON event; a helper whose signature is [`none`](#unsigned-webhooks) only decodes. It creates no client, server, or
route. The key types of builtin signatures are in `client.webhooks.keys`, which exists only when a helper has a builtin
signature and whose name a helper cannot take, and a package exists only when at least one webhook helper is enabled.
With the package `app.client` and the `standard.message` helper below:

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

    Deliveries outside the timestamp window are rejected.
    Verification retains no delivery state; deduplicate in your application using delivery_id when present.
    """
    return verify_webhook(
        _PLAN,
        raw_body,
        headers,
        keys,
        now=now,
        options=options,
    )
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
public key classes of [cryptography](https://cryptography.io/). `client.webhooks.keys` exports only the key types of the
selected signature kinds, and only these key types and their helpers import cryptography, so `client`, `client.protocols`,
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

Invalid configuration is rejected during generation.

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
type and take a required `verifier: Verifier[K]` with `keys: KeySet[K]` of the same key type; `client.webhooks.keys` has
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
`verifier` has a callable `verify` (or `ConfigurationError` with `field_path=("verifier",)`), and the key-count
limit, it passes the exact body, the headers as a tuple of pairs in received order, the key set, `now`, and
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
so omitting it never means unsigned. The module has only `decode_unverified(raw_body) -> Event`, which refuses a body
that is not `bytes` (`ConfigurationError`) and decodes the event. Nothing authenticates who sent the delivery or
whether it was changed or replayed, and the result is the event alone, with no facts: treat it as untrusted input.

### Verification order and limits

A call checks `raw_body`, `headers`, `keys`, `now`, and `options`, then each builtin key's type and unique id. Invalid
arguments raise `ConfigurationError` with their field path. Key and signature counts are bounded by
`ProtocolDataError` with the reason `too_large`; the body and headers have no size limit here, so bound them in the web
server that receives the delivery. The helper checks signature syntax, timestamp tolerance when present, and
cryptographic authenticity before decoding the model. Decoding errors raise `ProtocolDataError`. An adapter checks that
`verifier.verify` is callable, then the key count, its returned facts, and the timestamp tolerance before decoding.
Errors keep no key, signature, header value, or body.

| Limit (`WebhookOptions`) | Default |
|---|---|
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

### Webhook records and options

Generated packages expose webhook contracts from `client.protocols` and their exceptions from `client.errors`, where `client` is
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
from datetime import datetime
from typing import Protocol, TypeVar

from app.client.protocols import KeySet, ResolvedWebhookOptions, VerifiedSignature

K = TypeVar("K")


class Verifier(Protocol[K]):
    def verify(
        self,
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

`WebhookOptions` has the fields below. Every constructor default is `UNSET`, imported from `client.options`;
the effective values are the defaults in the table below, which the
[webhook verification helpers](#verification-order-and-limits) apply. `ResolvedWebhookOptions` has the same fields with
`UNSET` removed and every argument required. Both types are exported from `client.protocols`.
`ResolvedWebhookOptions` records limits already validated and resolved by the consuming helper. Helpers must
validate `WebhookOptions` before constructing this record and passing it to an application verifier.

| Field | Type | Effective default | Valid values |
|---|---|---|---|
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
`missing_key`, or `timestamp_window`; a key or signature count over its limit raises it with the reason
`too_large`. Error strings and representations exclude delivery IDs, namespaces, operation references, entry IDs, and
causes; callers may read those attributes explicitly.

## Records, options, and exceptions

Generated packages also expose the shared contracts of the pagination, polling, stream, WebSocket, cache, upload, and
webhook helpers they declare, and copy only the runtime modules those helpers, the declared security schemes, request
bodies, and response headers, and the model backend need; `client.protocols` exists only with a helper, and `client.bodies`
only with a binary, multipart, or schema-less form body. Records, options, cache stores, and resume state come from
`client.protocols`. The client's `helper_defaults=` keyword is available only in packages that declare a pagination,
polling, stream, WebSocket, cache, or upload helper; see [client helper settings](#helper-settings-on-the-client). Helper
execution loads when `client.protocols`
is first used; ordinary operations share the same native HTTP client. Exceptions come from `client.errors`, each with the
helper that raises it. These imports need no HTTP library and start no threads. A client that uses no helper settings
loads none of these definitions: `client.errors` loads them the first time one of its names is used. Constructing a
record or option sends nothing.

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

Every option field defaults to `UNSET`, imported from `client.options`. The effective defaults below apply after
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
| `UploadOptions` | `chunk_bytes` | `8388608` | Positive integer |
| `CacheOptions` | `max_ttl` | `300` seconds | Positive duration: the cap on any entry's freshness |
| `WSOptions` | `open_timeout`, `idle_timeout`, `ping_interval`, `pong_timeout` | See [WebSocket limits](#websocket-limits) | Positive duration or `None` |
| | `max_message_bytes` | HTTPX2's default (64 KiB in 2.13) | Positive integer |
| Every type but `CacheOptions` | `total_timeout` | `600` seconds for polling, no limit otherwise | Nonnegative duration or `None`: the helper session's budget from its start |

### Protocol exceptions

Four classes cover what a helper reports beyond the core errors. Each derives from `SDKError`, comes from `client.errors`
only when the package declares a helper that raises it, and has a keyword-only constructor that does not validate field
values. Besides the fields below, each accepts `helper_id`, `operation`, `operation_id`, `info`, and `cause`.

| Exception | Fields |
|---|---|
| `ProtocolDataError` | `reason: str = 'value'`, `location: Selector \| RequestTarget \| None = None`, `data: object = None` |
| `SessionLimitError` | `reason: str`, one of `pages`, `items`, `polls`, `reconnects`, `parts`, `wait`, and `deadline`; `limit: float`, `progress: ProtocolProgress \| None = None`, kept as a read-only copy; `required_wait: float \| None = None` |
| `StreamInterruptedError` | `reason: str`, `eof` or `transport`; `sequence: int`, the last record delivered |
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
