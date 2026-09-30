# Python Client (In Development)

!!! warning "Not available from the CLI yet"
    The HTTPX2 client generator is still being built. Its settings below are fields of the client's flat target
    configuration file and of `ClientGenerationConfig`; the `--generate-client` command line entry point and the
    matching `--client-*` options ship with its release, together with a link to this page from the navigation.

Like the server, the client needs Python 3.11 or later, both to run `datamodel-codegen` and as the target Python
version; a target below 3.11, including the default 3.10, is refused with `E_CONFIG_VALUE`.

Settings of the generated client that change how its methods are declared or checked do not change the models: the
same OpenAPI document and model settings produce the same model files whichever client settings you choose.

## Webhook contracts and replay stores

Generated packages expose webhook contracts from `pkg.protocols` and their exceptions from `pkg.errors`, where
`pkg` is the generated package name. These imports need no HTTP or cryptography library. API-specific signature
verification and event-decoding helpers are still being implemented; constructing a signature or event record
does not verify received data.

All records below are immutable and keyword-only. `KeySet` retains the original tuple and key objects without
inspecting or copying the keys. Its representation excludes key material. `VerifiedWebhook` excludes event data
from its representation.

| Type | Fields |
|---|---|
| `OperationRef` | `pointer: str`, `document: str \| None = None` |
| `KeySet[K]` | `keys: tuple[K, ...]`; an empty tuple is valid; a list or another iterable raises `TypeError` |
| `VerifiedSignature` | `delivery_id: str \| None`, `timestamp: datetime \| None`, `matched_key_id: str`; all fields are required |
| `VerifiedWebhook[T]` | `data: T`, `delivery_id: str \| None`, `timestamp: datetime \| None`, `matched_key_id: str`, `duplicate: bool`; all fields are required |

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
synchronous verifier contract applies to asynchronous consumers.

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
| `replay_ttl` | `float \| Unset` | `300` seconds | Finite, positive number |

Options reject `None`, booleans, negative values, nonfinite durations, zero count limits, and zero `replay_ttl`
with `ProtocolConfigurationError`. For example, `WebhookOptions(past_tolerance=0)` is valid, while
`WebhookOptions(max_keys=0)` and `WebhookOptions(replay_ttl=None)` are invalid.

`ReplayStore.claim(namespace: str, delivery_id: str, expires_at: datetime) -> bool` atomically returns `True`
for a new claim and `False` for an unexpired duplicate. `AsyncReplayStore` declares the same method as `async def`.
The store retains the claim until its aware datetime expires. Namespaces separate applications that share a store.

```python
from datetime import datetime, timedelta, timezone

from pkg.protocols import AsyncMemoryReplayStore, MemoryReplayStore

store = MemoryReplayStore(max_entries=10000)
expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
first = store.claim("application", "delivery-42", expiry)
duplicate = store.claim("application", "delivery-42", expiry)


async def claim_delivery() -> bool:
    async_store = AsyncMemoryReplayStore(max_entries=10000)
    return await async_store.claim("application", "delivery-42", expiry)
```

The memory stores are loaded when their public classes are requested and allocated only by explicit construction.
Each instance is independent. Both constructors accept `max_entries: int = 10000`, which must be a positive
integer, excluding booleans. They retain live entries at capacity and raise `ReplayStoreFullError` for another
new live claim. Expired entries release capacity. Claims whose expiry has already passed are not retained.
These stores coordinate callers in one process; a shared external adapter supplies multiprocess atomicity.
They create no HTTP clients, workers, or background tasks and require no close method.

Webhook exceptions have keyword-only constructors and the following additional fields:

| Exception | Direct base | Fields |
|---|---|---|
| `ProtocolConfigurationError` | `ConfigurationError` | `field_path: tuple[str, ...]`, `condition: Literal['unknown_field', 'invalid_value', 'missing_metadata', 'missing_adapter', 'wrong_capability', 'security_partition', 'binding_mismatch']` |
| `WebhookVerificationError` | `ProtocolError` | `condition: Literal['malformed_signature', 'invalid_signature', 'missing_key', 'timestamp_window', 'missing_delivery_id']` |
| `WebhookReplayError` | `ProtocolError` | `delivery_id: str`, `namespace: str` |
| `ProtocolStoreError` | `ProtocolError` | `action: Literal['lookup', 'fingerprint_vary', 'compare_exchange', 'delete', 'invalidate', 'claim', 'put', 'get', 'open', 'read', 'close', 'purge_terminal', 'admit', 'record', 'reset', 'snapshot']`, `entry_id: str \| None = None` |
| `WebhookStoreError` | `ProtocolStoreError` | No additional fields |
| `ReplayStoreFullError` | `WebhookStoreError` | `max_entries: int` |

All fields without a displayed default are required. They also accept the shared context fields
`helper_id: str | None = None`, `operation: OperationRef | None = None`, `info: ResponseInfo | None = None`,
`cause: BaseException | None = None`, and `secondary_errors: tuple[BaseException, ...] = ()`, together with inherited
SDK error fields. `reason_code` is the concrete class name in snake case. Error strings and representations exclude
delivery IDs, namespaces, operation references, entry IDs, and causes; callers may read those attributes explicitly.

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
        pet_id: _dcg_type_6 | ModelValue[_dcg_type_6],
        response_media_type: None = None,
        options: RequestOptions | None = None,
    ) -> _dcg_type_7: ...
    @overload
    def get_pet(
        self,
        *,
        pet_id: _dcg_type_6 | ModelValue[_dcg_type_6],
        response_media_type: Literal['application/json'] | ResponseMedia[_dcg_type_7],
        options: RequestOptions | None = None,
    ) -> _dcg_type_7: ...
    @overload
    def get_pet(
        self,
        *,
        pet_id: _dcg_type_6 | ModelValue[_dcg_type_6],
        response_media_type: Literal['text/plain'] | ResponseMedia[_dcg_type_8],
        options: RequestOptions | None = None,
    ) -> _dcg_type_8: ...
    def get_pet(
        self,
        *,
        pet_id: _dcg_type_6 | ModelValue[_dcg_type_6],
        response_media_type: Literal['application/json', 'text/plain'] | ResponseMedia[_dcg_type_7] | ResponseMedia[_dcg_type_8] | None = None,
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

    pet_id: _dcg_type_6 | ModelValue[_dcg_type_6]
    response_media_type: NotRequired[None]
    options: NotRequired[RequestOptions | None]


class Operation2Arguments1(TypedDict):
    """The keyword arguments of one signature of get_pet."""

    pet_id: _dcg_type_6 | ModelValue[_dcg_type_6]
    response_media_type: Literal['application/json'] | ResponseMedia[_dcg_type_7]
    options: NotRequired[RequestOptions | None]


class Operation2Arguments2(TypedDict):
    """The keyword arguments of one signature of get_pet."""

    pet_id: _dcg_type_6 | ModelValue[_dcg_type_6]
    response_media_type: Literal['text/plain'] | ResponseMedia[_dcg_type_8]
    options: NotRequired[RequestOptions | None]


class Operation2Arguments3(TypedDict):
    """The keyword arguments of one signature of get_pet."""

    pet_id: _dcg_type_6 | ModelValue[_dcg_type_6]
    response_media_type: NotRequired[Literal['application/json', 'text/plain'] | ResponseMedia[_dcg_type_7] | ResponseMedia[_dcg_type_8] | None]
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
- **Contract records.** The style is part of the package's public signature: switching it changes each operation's
  signature digest in the target manifest, and nothing else.

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
bodies and form-data sent as parts, bodies a registered codec adapter reads, bodies whose model cannot hold a request,
such as one with a required read-only member, and objects whose schema requires a key only their extra properties can
hold. Extra keys, a null body, and an empty object also need `body`, and a nested object is given as its own model.

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
E_CONFIG_VALUE config operations /paths/~1pets~1{petId}~1records/put: The body field name of the application/json property 'name' of PUT /pets/{petId}/records names no field argument
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
        pet_id: _dcg_type_6 | ModelValue[_dcg_type_6],
        body: _dcg_type_7 | ModelValue[_dcg_type_7],
        name: Unset = UNSET,
        tag: Unset = UNSET,
        media_type: Literal['application/json'] | RequestMedia[_dcg_type_7 | ModelValue[_dcg_type_7], _dcg_type_7 | ModelValue[_dcg_type_7]] | None = None,
        options: RequestOptions | None = None,
    ) -> UpdatePetResponse: ...
    @overload
    def update_pet(
        self,
        *,
        pet_id: _dcg_type_6 | ModelValue[_dcg_type_6],
        body: Unset = UNSET,
        name: str,
        tag: str | None | Unset = UNSET,
        media_type: Literal['application/json'] | RequestMedia[_dcg_type_7 | ModelValue[_dcg_type_7], _dcg_type_7 | ModelValue[_dcg_type_7]] | None = None,
        options: RequestOptions | None = None,
    ) -> UpdatePetResponse: ...
    @overload
    def update_pet(
        self,
        *,
        pet_id: _dcg_type_6 | ModelValue[_dcg_type_6],
        body: Unset = UNSET,
        name: str | Unset = UNSET,
        tag: str | None,
        media_type: Literal['application/json'] | RequestMedia[_dcg_type_7 | ModelValue[_dcg_type_7], _dcg_type_7 | ModelValue[_dcg_type_7]] | None = None,
        options: RequestOptions | None = None,
    ) -> UpdatePetResponse: ...
    @overload
    def update_pet(
        self,
        *,
        pet_id: _dcg_type_6 | ModelValue[_dcg_type_6],
        body: Unset = UNSET,
        name: Unset = UNSET,
        tag: Unset = UNSET,
        media_type: None = None,
        options: RequestOptions | None = None,
    ) -> UpdatePetResponse: ...
    def update_pet(
        self,
        *,
        pet_id: _dcg_type_6 | ModelValue[_dcg_type_6],
        body: _dcg_type_7 | ModelValue[_dcg_type_7] | Unset = UNSET,
        name: str | Unset = UNSET,
        tag: str | None | Unset = UNSET,
        media_type: Literal['application/json'] | RequestMedia[_dcg_type_7 | ModelValue[_dcg_type_7], _dcg_type_7 | ModelValue[_dcg_type_7]] | None = None,
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
  hook: call_start attempt=None sent=False status=None outcome=None phase=None path=/pets origin=None counts=0/0 request_id=None timed=False context={} options={'max_response_bytes': 16777216, 'max_error_body_bytes': 65536, 'max_stream_bytes': None, 'cleanup_timeout': 5.0, 'total_timeout': 60.0, 'max_network_sends': 3, 'stream_idle_timeout': 60.0, 'stream_total_timeout': None}
  hook: call_end attempt=None sent=False status=None outcome=error phase=None path=/pets origin=None counts=0/0 request_id=None timed=True context={}
fields missing a required one ! TypeError: create_pet() missing required field arguments for application/json: 'kind' []
  hook: call_start attempt=None sent=False status=None outcome=None phase=None path=/pets origin=None counts=0/0 request_id=None timed=False context={} options={'max_response_bytes': 16777216, 'max_error_body_bytes': 65536, 'max_stream_bytes': None, 'cleanup_timeout': 5.0, 'total_timeout': 60.0, 'max_network_sends': 3, 'stream_idle_timeout': 60.0, 'stream_total_timeout': None}
  hook: call_end attempt=None sent=False status=None outcome=error phase=None path=/pets origin=None counts=0/0 request_id=None timed=True context={}
a field of another media ! TypeError: create_pet() takes no such field arguments for application/x-www-form-urlencoded: 'kind' []
  hook: call_start attempt=None sent=False status=None outcome=None phase=None path=/pets origin=None counts=0/0 request_id=None timed=False context={} options={'max_response_bytes': 16777216, 'max_error_body_bytes': 65536, 'max_stream_bytes': None, 'cleanup_timeout': 5.0, 'total_timeout': 60.0, 'max_network_sends': 3, 'stream_idle_timeout': 60.0, 'stream_total_timeout': None}
  hook: call_end attempt=None sent=False status=None outcome=error phase=None path=/pets origin=None counts=0/0 request_id=None timed=True context={}
fields without a media type ! ConfigurationError: ConfigurationError(operation_id='createPet', call_id='<call>', field_path='media_type', condition='missing') [operation_id='createPet', field_path=('media_type',), condition='missing'] configuration_error
  hook: call_start attempt=None sent=False status=None outcome=None phase=None path=/pets origin=None counts=0/0 request_id=None timed=False context={} options={'max_response_bytes': 16777216, 'max_error_body_bytes': 65536, 'max_stream_bytes': None, 'cleanup_timeout': 5.0, 'total_timeout': 60.0, 'max_network_sends': 3, 'stream_idle_timeout': 60.0, 'stream_total_timeout': None}
  hook: call_end attempt=None sent=False status=None outcome=error phase=None path=/pets origin=None counts=0/0 request_id=None timed=True context={}
fields for text ! TypeError: log_visit() takes no field arguments for text/plain: 'note' []
  hook: call_start attempt=None sent=False status=None outcome=None phase=None path=/pets/{petId}/visits origin=None counts=0/0 request_id=None timed=False context={} options={'max_response_bytes': 16777216, 'max_error_body_bytes': 65536, 'max_stream_bytes': None, 'cleanup_timeout': 5.0, 'total_timeout': 60.0, 'max_network_sends': 3, 'stream_idle_timeout': 60.0, 'stream_total_timeout': None}
  hook: call_end attempt=None sent=False status=None outcome=error phase=None path=/pets/{petId}/visits origin=None counts=0/0 request_id=None timed=True context={}
update naming only a media type ! ConfigurationError: ConfigurationError(operation_id='updatePet', call_id='<call>', field_path='media_type', condition='without_body') [operation_id='updatePet', field_path=('media_type',), condition='without_body'] configuration_error
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.body-fields.refusals -->

- Only the fields a call gives are sent: the model's defaults are not, and `None` sends `null`.
- A required body whose fields are all optional is sent as `{}` when a call gives none of them. An optional body is
  omitted, and naming only its media type still raises `ConfigurationError`.
- The model is built from the fields by its constructor, which validates Pydantic models and dataclasses, while stdlib
  dataclasses, TypedDicts, and msgspec Structs take the values as given. Under `native`, msgspec builds it through its
  converter instead, which validates it once; under `schema`, the serialized object is validated against its schema.
- With Pydantic argument validation, the fields are validated with the operation's parameters, each at its
  `("body", <wire name>)` location.

### Cost

A body call of a package generated with `"both"` spends about 0.2 µs checking that it gives no fields, within the
noise of a 57–64 µs `create_pet` call over a mock transport. Giving fields instead of a body built in the same call
adds 2.4–4.1 µs: 1.6 µs to bind them and select the media type, which the call then does not select again, and about
0.7 µs to build the model and keep only the given fields (CPython 3.13, macOS arm64, every backend).

## Validation

`validation` chooses what ordinary calls check beyond sending and reading their values. It is a
`ClientValidationConfig` with one mode for each axis, the one every client, view, and call uses unless it selects
another, and the other modes the package lets them select at runtime.

| Axis | Modes | Default | What the mode adds |
|---|---|---|---|
| `request` | `none`, `native`, `schema` | `none` | `none` sends the native value as it serializes; `native` first passes it through its model backend's own validation; `schema` validates the serialized value against its OpenAPI schema |
| `response` | `native`, `schema` | `native` | `native` constructs the declared type through the backend's converter; `schema` validates the received value against its OpenAPI schema before constructing it |
| `arguments` | `none`, `pydantic` | `none` | `pydantic` validates the arguments a call takes as native values with Pydantic before they are sent |

What every mode still does:

- **Binding.** A missing required argument, an unknown keyword, or a media type the operation does not declare fails
  before anything is sent.
- **Serialization and direction.** Aliases, presence, and the conversion of dates, decimals, enums, and media stay the
  same in every mode, a read-only member is left out of a request, and a write-only member is refused in a response.
  A form-data member that its direction excludes takes no part in either.
- **Native behavior.** `none` does not undo validation a model's constructor already did, and it cannot send what the
  serializer cannot represent.
- **Strict records.** A `ModelValue` or `ModelInput` passed as an argument, the `RequestCodecs` factories, the header
  decoders, and a response the package returns as `DecodedValue` keep their strict schema validation in every mode.
  Read a body without any model with `with_raw_response` or `with_streaming_response` instead.

### Configuration

Python and the flat target file take the same fields:

```python
ClientGenerationConfig(
    output=Path("client"),
    package="client",
    model_package="models",
    validation=ClientValidationConfig(request_overrides=("schema",), response_overrides=("schema",)),
)
```

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.validation.toml -->
<!-- fmt: off -->

```toml
schema_version = 1
output = "client"
package = "client"
model_package = "models"

[validation]
request = "none"
response = "native"
arguments = "none"
request_overrides = ["schema"]
response_overrides = ["schema"]
argument_overrides = []
pydantic_strict = true
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.validation.toml -->

The default of each axis and its overrides are every mode a package allows on it: empty overrides fix the mode, and
a package allows no mode it was not generated with. The generated clients record the allowed modes when they differ
from the defaults above:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.validation.defaults -->
<!-- fmt: off -->

```python
_DEFAULTS = ClientDefaults(user_agent=None, validation=ValidationModes(request=('schema',), response=('schema',)))
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.validation.defaults -->

### Selecting a mode at runtime

`pkg.options.ValidationOptions` has the three axes, each left `UNSET` to inherit. A call's `RequestOptions`, a
`with_options` view, the client's `ClientOptions`, and the generated default are layered in that order, axis by axis:

```python
from pkg.options import ClientOptions, RequestOptions, ValidationOptions

client = Client(options=ClientOptions(validation=ValidationOptions(response="schema")))
checked = client.with_options(RequestOptions(validation=ValidationOptions(request="schema")))
client.pets.list_pets(options=RequestOptions(validation=ValidationOptions(response="native")))
```

A mode the package does not allow raises `ConfigurationError` with condition `not_allowed`: from the client or view
that selects it, or from a call before any hook, request, or stream, and a value that is no mode raises it with
`invalid_value`. The tests record, for a stdlib dataclass package that allows `none` and `schema` requests:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.validation.options -->
<!-- fmt: off -->

```text
client selecting Pydantic arguments ! ConfigurationError: ConfigurationError(field_path='validation.arguments', condition='not_allowed') [field_path=('validation', 'arguments'), condition='not_allowed'] configuration_error
view selecting native requests ! ConfigurationError: ConfigurationError(field_path='validation.request', condition='not_allowed') [field_path=('validation', 'request'), condition='not_allowed'] configuration_error
request mode None ! ConfigurationError: ConfigurationError(field_path='validation.request', condition='invalid_value') [field_path=('validation', 'request'), condition='invalid_value'] configuration_error
response mode none ! ConfigurationError: ConfigurationError(field_path='validation.response', condition='invalid_value') [field_path=('validation', 'response'), condition='invalid_value'] configuration_error
call selecting native requests ! ConfigurationError: ConfigurationError(operation_id='listPets', field_path='validation.request', condition='not_allowed') [operation_id='listPets', field_path=('validation', 'request'), condition='not_allowed'] configuration_error
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.validation.options -->

### What each mode checks

The native converters check the declared Python types, which do not carry every constraint of the source schema, and
coerce as their backend does. The tests send and read these values through each backend, with the models of
`--output-model-type` and no model options:

| Value | `native` with Pydantic models or dataclasses | `native` with msgspec | `native` with stdlib dataclasses or TypedDict | `schema` |
|---|---|---|---|---|
| An `int32` member beyond its range | accepted | accepted | accepted | refused |
| A string longer than its `maxLength` | refused | accepted | accepted | refused |
| `"1"` for an integer | converted to `1` | refused | refused | refused |
| A member `additionalProperties: false` forbids | refused | ignored | ignored (dataclass), refused (TypedDict) | refused |
| A write-only member in a response | refused | refused | refused | refused |
| A required member missing | refused | refused | refused | refused |

On requests, `none` sends a model instance changed after construction, and `native` sends it too, since the backends
trust an instance they built; `native` validates a mapping that stands for the model. `schema` refuses both.

A form-data part sends the headers its encoding declares as the call gives them. `none` checks none of them, `native`
checks that each required one is present and reads as its declared type, and `schema` also validates each against its
schema, such as its `pattern` or `minimum`.

### Pydantic argument validation

With `pydantic` among the `arguments` modes, a call validates the parameters and the body it takes as native values
with Pydantic's `validate_call` before anything is encoded or sent: the body of the media type the call sends, unless
it is sent as parts or as binary. `ModelValue` and `ModelInput` arguments keep their own strict validation, and
omitted arguments stay omitted. Each branch of an operation has a private function whose parameters take the final
types of its arguments, which the package builds the first time a call selects it:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.validation.arguments -->
<!-- fmt: off -->

```python
def _operation_1_0(
    *,
    body: _dcg_type_1 = OMITTED,
) -> tuple[object, ...]:
    return (body,)


@cache
def operation_1_0() -> ArgumentCheck:
    """Return the validation of the arguments of create_pet sending application/json."""
    return ArgumentCheck((('body', ('body',)),), _operation_1_0, strict=True)
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.validation.arguments -->

The validation uses `ConfigDict(strict=pydantic_strict, revalidate_instances="always", extra="allow")`, where
`pydantic_strict` is `True` unless the target configuration sets it to `False` to let Pydantic coerce values; the
validated or coerced values are the ones sent. That configuration applies to stdlib dataclasses and TypedDicts:
an existing dataclass instance is validated again, and a closed TypedDict still refuses extra keys. Pydantic models
and dataclasses keep their own configuration, so an instance changed after construction is sent as their
configuration trusts it. A value Pydantic refuses raises `RequestEncodingError` at its argument, with the
`ValidationError` as its cause, and nothing is sent.

| Argument | Pydantic models or dataclasses | stdlib dataclasses | TypedDicts |
|---|---|---|---|
| A model changed after construction to a string `age` | sent as it is | refused, or converted without `pydantic_strict` | refused, or converted without `pydantic_strict` |
| `"5"` for an integer parameter | converted for a model's `RootModel`; for a dataclass's alias, refused, or converted without `pydantic_strict` | refused, or converted without `pydantic_strict` | refused, or converted without `pydantic_strict` |
| A mapping with an undeclared key as the body | refused | refused, since strict validation takes only instances, or kept without `pydantic_strict` | refused, as the TypedDict is closed |
| `5` for a string parameter | refused | refused | refused |

A package that allows Pydantic argument validation depends on `pydantic>=2.13.5` even when its default is `none`,
and imports Pydantic only when a call first selects it. It is not available for msgspec Structs, which Pydantic
cannot validate, or for an argument a registered codec adapter reads.

### When a mode is not available

A package must be able to take every mode it allows for every use, or generation fails with `E_CONFIG_VALUE` naming the
use and the mode to select instead:

- `request = "native"` needs a backend validation entry: Pydantic models and dataclasses and msgspec Structs have one,
  stdlib dataclasses and TypedDicts do not. A form-data part's headers need none.
- `response = "native"` is refused for a union whose members only their schemas tell apart, such as two object models
  read by a stdlib dataclass or TypedDict converter, unless the union's use returns `DecodedValue`.
- A use that a registered codec adapter reads or writes allows only `schema` in its direction.
- `arguments = "pydantic"` is refused for an argument holding msgspec Structs or read by a registered codec adapter.

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.validation.diagnostics -->
<!-- fmt: off -->

```text
E_CONFIG_VALUE binding validation.response /paths/~1orders~1{orderId}/get/responses/200: The 'native' response validation cannot take the response body 200 application/json of GET /orders/{orderId}, which tells its union members apart only by their schemas; select 'schema'
E_CONFIG_VALUE binding validation.request /paths/~1pets/get/parameters/0: The 'none' request validation cannot take the query parameter limit of GET /pets, which goes through a registered adapter that validates it against its schema; select 'schema'
E_CONFIG_VALUE binding validation.argument_overrides[0] /paths/~1pets/get/parameters/0: The 'pydantic' arguments validation cannot take the query parameter limit of GET /pets, which goes through a registered adapter that gives Pydantic no schema; select 'none'
E_CONFIG_VALUE binding validation.response /paths/~1pets/post/responses/201: The 'native' response validation cannot take the response body 201 application/json of POST /pets, which goes through a registered adapter that validates it against its schema; select 'schema'
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.validation.diagnostics -->

### Cost

`none` and `native` never build the package's schema bundle or import `jsonschema`, which the strict factories still
need, so the package keeps it as a dependency. The first `list_pets` call of a fresh process takes 3.9 ms with the
default modes and 349 ms with `schema`, most of it importing `jsonschema` with its format checkers. A `create_pet`
call over a mock transport takes 64 µs with `none` and `native` responses, 66 µs with `native` requests, and 107 µs with
`schema` for both; listing 20 pets takes 189 µs natively and 388 µs with `schema` (CPython 3.13, macOS arm64, Pydantic
v2 models). msgspec lists them in 132 µs and stdlib dataclasses in 135 µs natively, and both in about 380 µs with
`schema`. Pydantic argument validation adds 6–7 µs to a `create_pet` call with Pydantic models, stdlib dataclasses,
or TypedDicts; with the latter two, the first call that selects it takes about 23 ms to import Pydantic and build its
validation.

## Timeouts, cancellation, and send limits

The generated package's `options` module provides `ClientOptions`, `RequestOptions`, `TimeoutOptions`, `Deadline`,
and `CancelToken`. These settings apply to typed operations and `request_raw`, including their response and streaming
views. The following examples use a generated package named `pets` and take the service URL from their caller.

| Option | Effective default | Meaning |
|---|---|---|
| `timeout` | `TimeoutOptions(connect=5, read=30, write=30, pool=5)` | Native I/O phase limits, in seconds |
| `total_timeout` | `60` | Relative budget from call entry through encoding, callbacks, sending, reading, and decoding |
| `deadline` | `None` | An absolute monotonic deadline created by `Deadline.after(seconds)` |
| `cancel_token` | `None` | An explicit cancellation signal shared with the call |
| `max_network_sends` | `1 + max_retries + (max_redirects if enabled)`, plus `AuthConfig.max_token_exchanges` with an OAuth provider of the SDK | Maximum number of send slots the call may reserve; `3` with the fixed defaults |
| `stream_idle_timeout` | `60` | Read inactivity limit after a streaming response is handed to the caller |
| `stream_total_timeout` | `None` | Total stream lifetime after handoff |
| `cleanup_timeout` | `5` | Separate positive, finite budget for releasing resources |
| `limiter` | `None` | An application-provided `Limiter` or `AsyncLimiter` |

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
`max_network_sends=0` raises `BudgetExceededError` before a send, or `AuthBudgetExceededError` when an OAuth provider of
the SDK would first acquire a token.

### A budget shared by every phase

`Deadline.after(10)` fixes the expiry when it is created. Reusing that object across calls shares the same expiry;
each call's `total_timeout` starts again at call entry. `Deadline.at` is the readonly monotonic timestamp, and
`remaining()` returns the seconds left, never a negative number. Do not compare `at` with wall-clock timestamps.

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
                        replay_safe_with_key=True,
                        retention_seconds=86400,
                        scope="orders-v1",
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
replay_safe_with_key = true
retention_seconds = 86400
scope = "orders-v1"
```

All four idempotency fields are required. Retention must be positive and finite; booleans are not numbers. Scope is a
nonsecret opaque identifier containing non-whitespace text. The key header must be an HTTP token and cannot share
an outgoing position with an effective parameter or authentication header. The two response control headers must
have distinct names. Header comparisons ignore ASCII case; outgoing and incoming positions are independent.
Unknown TOML keys receive `E_CONFIG_UNKNOWN`; malformed values receive `E_CONFIG_VALUE`, and ownership conflicts
receive `E_CONFIG_CONFLICT`. The generated README lists only the finalized selected operations and their contracts.

### Supply and retain an idempotency key

`IdempotencyMetadata` describes the API's guarantee. The generated package's `IdempotencyKey` supplies a call's key:
`IdempotencyKey("saved-value", first_used_at=aware_datetime)` or `IdempotencyKey.new()`. The latter creates a UUID4 and
records the current UTC time. The value is omitted from its representation. A known timestamp must be timezone-aware.
A value without a known prior-use time may be sent but cannot justify an unsafe retry; an expired key also cannot.

With `idempotency_key=UNSET`, the client creates one key only for an operation with an idempotency declaration.
`idempotency_key=None` disables automatic creation. A caller key on a client/view is conditional on the operation's
declaration; an explicit non-None call value on an undeclared operation or `request_raw` fails before sending. The
same call retains one key, origin, and scope throughout retries; expiry never causes automatic key replacement.
A `replay_safe_with_key=False` declaration allows the header without promising that it makes an unsafe resend safe.
The contract does not guarantee exactly-once business execution.

Keys must be nonempty, UTF-8-encodable strings. ASCII control characters are rejected except for interior tabs;
leading or trailing ASCII spaces and tabs are also rejected. Interior spaces, tabs, and valid Unicode are preserved
without trimming or normalization. Invalid keys raise `ConfigurationError` when constructed, before any send.

```python
from datetime import datetime

from pets import Client
from pets.options import IdempotencyKey, RequestOptions


def keyed_view(client: Client, value: str, first_used_at: datetime) -> Client:
    key = IdempotencyKey(value, first_used_at=first_used_at)
    return client.with_options(RequestOptions(idempotency_key=key))
```

Use the returned view in a `with` block and call an operation with the matching declaration. Supply the persisted
first-use time, not the time of the newest retry. An attached key whose retention has expired stops replay even for
a normally safe method.

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
and content/framing headers, including Content-Encoding. 307/308 retain the method/body and require replayability
and operation safety. Every hop consumes a send slot and the same call deadline. Credentials and cookies from the
original request are stripped across origins. Repeated method/URL loops, invalid or multiple Location values,
forbidden destinations, exhausted redirect limits, or unsafe replay raise `RedirectPolicyError`. It directly
inherits `SDKError` and exposes readonly `delivery_state`, `body_available=False`, and available response metadata.
Native failure while constructing a redirect also raises this error if no response handle can be returned.

A HEAD operation also follows a 303 as GET, regardless of `allow_303_to_get`. Its typed return contract remains
bodyless: ordinary and `with_response` calls return `None` data for an empty final body and raise
`BodyProtocolError(condition="forbidden_body")` if the GET returns content. Use `with_raw_response` or
`with_streaming_response` to access that final GET body without typed decoding.

`TransportOptions` belongs only to `ClientOptions`; it cannot be set on a view or request. Its effective defaults are
`verify=True`, `ssl_context=None`, `proxy=None`, `trust_env=False`, `http2=False`, `max_connections=100`,
`max_keepalive_connections=20`, `keepalive_expiry=5`, and `retry_owner="sdk"`. Supplying an SSLContext uses its CA,
verification, and client certificate settings and rejects any explicit `verify` override. Proxy/environment/TLS
choices are explicit. HTTP/2 is opt-in and requires its optional dependency.

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
verification enabled and does not inherit proxy configuration from the environment.

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

The generated `auth` module exports `AuthConfig`, credential values, provider Protocols, static/environment providers,
and signer Protocols. `ClientOptions.auth` and `RequestOptions.auth` default to `UNSET`; an `AuthConfig` replaces the
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

`AccessToken.scopes=None` means that grants are unknown. It skips local containment checking and leaves the decision
to the server; it does not claim unrestricted permission. `scopes=()` is known empty and rejects a nonempty requirement.
Known grants and required scopes are deduplicated and ASCII-sorted. An insufficient known grant raises
`InsufficientScopeError` with readonly required, granted, and missing tuples before sending. The SDK never broadens
scopes automatically after a 403. Expired material raises `TokenExpiredError` and cannot justify an endless refresh.
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
explicitly set, is recorded under `operations[].runtime.auth_challenge_less_401` in generated documentation, and affects
only the security contract digest. It is not a client option or an inferred response behavior.

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

### Exchange an authorization code

`AuthorizationCodeFlow` and `AsyncAuthorizationCodeFlow` implement the OAuth authorization code grant with PKCE S256
for applications that obtain the code themselves. `authorization_request(redirect_uri, scopes)` performs no I/O: it
creates a fresh state and code verifier and returns the authorization URL for the application to open. The SDK never
opens a browser or listens for the redirect. `exchange_code(code, returned_state, request)` compares the returned state
in constant time, sends one token request, and returns a `TokenSet` whose repr omits both tokens.

```python
from pets.auth import AccessToken, AuthorizationCodeFlow, AuthorizationRequest


def login_flow() -> AuthorizationCodeFlow:
    return AuthorizationCodeFlow(
        "https://auth.example.com/authorize",
        "https://auth.example.com/token",
        client_id="pets-cli",
        client_auth_method="none",
    )


def begin_login(flow: AuthorizationCodeFlow) -> AuthorizationRequest:
    return flow.authorization_request("http://127.0.0.1:8400/callback", ("pets.read",))


def finish_login(flow: AuthorizationCodeFlow, request: AuthorizationRequest, code: str, state: str) -> AccessToken:
    return flow.exchange_code(code, state, request).access_token
```

A request belongs to the flow that created it and allows one exchange. A mismatched state, or a code that is empty or
not visible ASCII, is refused with `AuthConfigurationError` before the request is used; the first exchange then
consumes it whatever the outcome, and a later one raises `AuthStateConflictError`. The requested scopes, in canonical
order, become the token's scopes when the token response omits its `scope` member. The authorization URL may carry
its own query, but not the parameters the flow adds.

`client_secret_basic` (the default) and `client_secret_post` take a `client_secret` provider returning an
`ApiKeyCredential` of visible ASCII; it is called once per exchange with a context naming the token endpoint's origin
and the scheme `oauth_client_secret`. `none` sends only the client id. Both endpoints must be HTTPS; plain HTTP to a
loopback host requires `OAuthProviderOptions(allow_insecure_loopback=True)`.

Token requests use a transport the flow owns, separate from every client: an HTTPX2 client created at the first
exchange from `OAuthProviderOptions.transport`, or an adapter passed as `token_transport`, which is borrowed unless
wrapped in `OwnedTransportAdapter`. Its settings must verify certificates and host names and leave retries to the
SDK, and an injected adapter must declare `internal_retry_limit=0`. Token requests never follow redirects or retry,
and closing the flow, or leaving its `with` block, closes an owned transport. `refresh_timeout` (30 seconds by default)
bounds each exchange, the client secret lookup included, independently of any resource call; the synchronous flow
checks it when the secret provider returns and at each response chunk. `phase_timeout` caps connecting, reading,
writing, and pool waits at 5, 15, 15, and 5 seconds unless overridden. The async flow binds to the event loop it was created on, or else to the
first one that exchanges a code.

`invalid_grant` raises `AuthReauthorizationRequiredError`. Another RFC 6749 error code in a 400 response, or
`invalid_client` in a 401, raises `OAuthExchangeError` with its `oauth_error`. A client secret provider's failure
raises its own auth error or `AuthProviderExecutionError`, and a failure proven to happen before sending raises
`OAuthExchangeError`, or `AuthTimeoutError` when a time limit ran out; none of them sent the code, but the request is
still used. When the endpoint may have issued tokens that did not arrive intact (a transport failure after sending, the
session deadline, an unexpected status, or a malformed or unusable response), `AuthStateUncertainError` reports the
`failure_kind`, and the application starts a new authorization. Only a transport that declares delivery evidence can
prove that a failed request was never sent. Responses are read up to 64 KiB and must be UTF-8 JSON objects. Errors
keep no response body or error description, but a transport failure's cause is the native exception, which can hold
the token request and its client authentication, so apply your own policy before logging causes.

### Authorize a device

`DeviceAuthorizationFlow` and `AsyncDeviceAuthorizationFlow` implement the OAuth device authorization grant for
devices that cannot open a browser. One flow owns exactly one transaction: `begin(scopes)` requests a device
authorization and returns a `DeviceAuthorization` whose user code and verification URIs the application shows; the SDK
never opens them. `poll()` then waits the server's interval, five seconds unless the response names another, and polls
the token endpoint until the user approves, returning a `TokenSet`.

```python
from collections.abc import Callable

from pets.auth import AccessToken, DeviceAuthorizationFlow


def device_login(show: Callable[[str, str], None]) -> AccessToken:
    with DeviceAuthorizationFlow(
        "https://auth.example.com/device",
        "https://auth.example.com/token",
        client_id="pets-tv",
        client_auth_method="none",
    ) as flow:
        authorization = flow.begin(("pets.read",))
        show(authorization.user_code, authorization.verification_uri)
        return flow.poll().access_token
```

`authorization_pending` waits within the same `poll` call, and `slow_down` adds five seconds to every later wait.
`access_denied` and `expired_token` end the transaction with `OAuthExchangeError` in the `REAUTH_REQUIRED` state, whose
cause's message names which of the two the server sent. A transaction never resumes, so a new authorization needs a
new flow, and calling `begin` twice, `poll` before `begin`, `poll` concurrently, or either after the end raises
`AuthStateConflictError`. `begin` validates every member of the device authorization response first and refuses a
response without a positive `expires_in`; the requested scopes become the token's scopes when the token response
omits its `scope` member.

The transaction ends at the response's `expires_in`, or earlier at the limit of the `SessionOptions` (from
`pets.options`) given to `begin`, whose `total_timeout` counts from the `begin` call and whose `deadline` is absolute.
The `begin` and every poll count against `max_network_sends`, 128 by default; `None` removes that limit, while `0` or
a limit that already passed refuses the `begin` itself. A poll that could not be sent before the deadline or within the
limit raises `DeadlineExceededError` or `BudgetExceededError` at once instead of waiting, and a send the session limit
cuts short raises `DeadlineExceededError`. `poll(cancel_token=...)` stops a wait with `RequestCancelledError`, and
closing the flow stops it with `AuthProviderClosedError`.

Client authentication, endpoints, and the token transport follow the authorization code flow; the client secret
lookup for `begin` names the device authorization endpoint's origin. Unlike the code flow, every answer the flow cannot
continue from, including an invalid token response, an unexpected status, or a malformed body, raises
`OAuthExchangeError` in the `EXCHANGE_REJECTED` state. A transport failure raises `OAuthExchangeError`, or
`AuthTimeoutError` when a time limit ran out, in the `UNCERTAIN` state when the request may have been sent and in the
`FAILED_NOT_SENT` state otherwise.

### Acquire client credentials

`ClientCredentialsProvider` and `AsyncClientCredentialsProvider` implement the OAuth client credentials grant for a
confidential client acting on its own behalf. Each is a refreshable token provider for an OAuth scheme of `AuthConfig`:
the first call that needs a token acquires it, later calls share it, and a call renews it once a tenth of its lifetime,
at most thirty seconds, remains. A token without `expires_in` is kept until a resource rejects it, when 401 recovery
invalidates that exact version and acquires another.

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

Concurrent callers share one acquisition. It runs on a worker thread of the provider, or in a task on its event loop,
under the provider's own `refresh_timeout`; callers only wait for it, each within its own deadline and cancel token, so
a caller that leaves never cancels it and its token still serves later calls. `OAuthProviderOptions` bound the sharing:
`max_concurrent_refreshes` acquisitions run at once (one by default), `max_pending_refreshes` (32) counts the running
and queued ones, and `max_waiters` (1024) callers may wait for one; a caller beyond a limit gets
`AuthConcurrencyLimitError`. An acquisition still running a second after its `refresh_timeout` fails its waiters with
`AuthTimeoutError` and keeps its slot until it returns, and its late token is discarded.

A failed acquisition leaves nothing behind: each waiter receives its own instance of the same error, and the next call
acquires again. A rejection, an unexpected status, or an unusable response raises `OAuthExchangeError` in the
`EXCHANGE_REJECTED` state; a transport failure raises `OAuthExchangeError`, or `AuthTimeoutError` when a time limit ran
out, in the `FAILED_NOT_SENT` state when the request provably never left and in the `EXCHANGE_REJECTED` state
otherwise. The auth refresh errors of an acquisition carry its `refresh_id`, unless a client secret provider's own error
already names another one, and `refresh_snapshot(refresh_id)` returns its `RefreshInfo`: `PENDING` while it is queued
or runs, then the state it ended in, whether it sent a token request, and its failure's reason code, without tokens.
Snapshots of the latest 128 ended acquisitions are kept for five minutes.

A generated call accounts for these acquisitions. The call that needs a new one pays one of its
`AuthConfig.max_token_exchanges` exchanges (two by default) and one network send, and it needs room for the request the
token serves too; when `max_network_sends` is omitted, the call's send limit makes room for its exchanges. A call that
joins an acquisition another caller started pays nothing and reports `auth_wait` to its hooks; when a queued acquisition
starts later, its oldest waiter pays for it if that waiter is a call. A call that cannot pay raises
`AuthBudgetExceededError` before the token request, and recovering from a rejected token stops with
`auth_exchange_budget_exhausted` when the recovery needs an acquisition the call can no longer pay for. `ResponseInfo`
and errors report `auth_exchange_count`, `auth_exchange_budget_used`, the `auth_refresh_ids` of the acquisitions the
call started or waited for, and `auth_refresh_pending`, how many of them were still queued or running. Custom providers,
wrappers of these providers included, take no part in this accounting.

`get` returns the shared token, waiting for it when none is usable; `refresh` acquires a new one, or joins the running
acquisition, whatever the provider holds; `invalidate(version)` forgets the held token only if it is that version.
Closing the provider refuses new acquisitions with `AuthProviderClosedError`, ends a queued one, lets a running one
finish within its session, and then closes an owned token transport; a concurrent `close` returns once that is done, and
every `close` raises that release's failure. The synchronous provider's `request_close()` does the same without waiting
and returns a `concurrent.futures.Future` of the release: an idle provider releases on its worker, or in the calling
thread when it never started one, and a busy one in a thread of its own once its running acquisition returns or its
session ends. A client that owns the provider through `OwnedCredentialProvider` starts that release, or the async
provider's `aclose`, and waits for it only until its `cleanup_timeout`, measured from the start of its close, runs out;
otherwise it raises `CleanupError` with `pending_providers`, a later close waits again, and each release failure is
reported once. A synchronous call waiting for a shared acquisition stops with its own error, `ClientClosedError` once
its client closes, checking its client and cancel token every 50 milliseconds and waking at its deadline; an asyncio
call stops as soon as its client closes or its deadline passes, and within 50 milliseconds of its cancel token. The
async provider belongs to the event loop it was created on or first used from, and its `aclose` runs in a task of its
own that a later `aclose` awaits when the first was cancelled. Client authentication, endpoints, and the token transport
otherwise follow the authorization code flow.

### Refresh a token family

`RefreshTokenProvider` and `AsyncRefreshTokenProvider` keep one token family current with the OAuth refresh token grant,
starting from the `TokenSet` an authorization produced. The provider alone refreshes its family: no other provider,
process, or event loop may send the same refresh token. It serves the access token while it lasts, and a call refreshes
it once a tenth of its lifetime, at most thirty seconds, remains, or after a resource rejected it.

```python
from pets import Client
from pets.auth import AuthConfig, OwnedCredentialProvider, RefreshTokenProvider, TokenSet
from pets.options import ClientOptions


def user_client(tokens: TokenSet) -> Client:
    provider = RefreshTokenProvider(
        "https://auth.example.com/token", client_id="pets-app", token_set=tokens, client_auth_method="none"
    )
    return Client(options=ClientOptions(auth=AuthConfig({"oauth": OwnedCredentialProvider(provider)})))
```

The refresh request sends the refresh token with the client authentication, never the configured `scopes` or `audience`:
`none` sends the `client_id` of a public client, and `client_secret_basic` (the default) and `client_secret_post` need a
`client_secret`. A caller whose context requires another audience than the configured one fails with
`AuthConfigurationError`, as does a token whose own `audience` differs from it, leaving the family as it was. A token
set needs the Bearer type, and a timezone-aware `expires_at` when its access token expires.

Each refresh replaces the token set with one of the next revision. The response's refresh token rotates the family, and
a response without one keeps the refresh token sent; a response without `scope` keeps the grants of the access token it
replaces, known or unknown. A caller requiring a scope that known grants lack fails with `InsufficientScopeError` before
any request, since a refresh never widens them, and a caller whose refresh narrowed them fails the same way. A refresh
token the family rotated away from, or sent in a request that may have been delivered without a usable answer, is spent:
the family never sends it again, and a response returning a spent one is unusable.

A refresh that may have spent its refresh token stops the family: `invalid_grant` raises
`AuthReauthorizationRequiredError` in the `REAUTH_REQUIRED` state, another known error code `OAuthExchangeError` in the
`EXCHANGE_REJECTED` state, and any other answer, an unusable one, or a lost request `AuthStateUncertainError` in the
`UNCERTAIN` state, as does a refresh still running a second after its `refresh_timeout`. Every later `get` and `refresh`
raises a new instance of that error without a request, until `replace_token_set` adopts a token set of a higher
revision, or `reload_token_set` loads one; `replace_token_set` refuses one whose refresh token the family spent, or that
has neither a refresh token nor an unexpired access token, with `AuthConfigurationError`. A new authorization's token
set has revision 0; without a load, which returns the current token set, the family's revision is not exposed, so a
stopped family is restarted with a new provider from that token set. A refresh that provably sent nothing, as when the
client secret provider fails, raises its error in the `FAILED_NOT_SENT` state and leaves the family as it was, so the
next call sends the same refresh token. A token set without a refresh token serves its access token until it expires or
a resource rejects it, then stops the family with `AuthReauthorizationRequiredError`, as a forced `refresh` does at
once.

Concurrent callers, `OAuthProviderOptions`, snapshots, call accounting, and closing follow the client credentials
provider. `replace_token_set` raises `AuthStateConflictError` while a refresh, load, reload, or store runs, or once the
provider is closing; its `persist` argument has no effect without a token store.

`TokenLoad` and `TokenStore`, with `AsyncTokenLoad` and `AsyncTokenStore`, are the callbacks that persist a token
family, each bound to the storage namespace of one family: `load(context)` returns the stored `TokenSet` or None, and
`store(token_set, expected_revision=..., context=...)` saves it durably only if the stored revision is
`expected_revision`, or if nothing is stored for None, treating an identical stored token set as success and raising
`AuthTokenStoreConflictError` on any other conflict. Their `TokenPersistenceContext` names the provider, the session,
its deadline, the `purpose`, and the `cache_key` fingerprinting the family's configuration without tokens or secrets;
the key alone is no global store key, since families of different users share it.

A provider needs a `token_set`, a `load`, or both. With a load, the first call loads the persisted token set once, in a
session of the provider's own that no call pays for, and keeps the newer of it and the constructor's token set by
revision; callers arriving meanwhile wait for that load. A failing load, or a stored token set that differs from the
constructor's at the same revision, stops the family in the `LOAD_FAILED` state with `AuthTokenLoadError` or
`AuthTokenStoreConflictError`, without loading again by itself; a newer token set that can never serve is made current
and stops the family with `AuthReauthorizationRequiredError`, as does a family with no token set at all.

Each refresh then reads the persisted token set right before its request. A newer one that can serve is used without a
request, although the refresh keeps what it charged its call; a newer expired one is refreshed instead of the current
one, and one that can never serve stops the family; an older one, or one bringing back a spent refresh token, changes
nothing. A failed read, or another token set at the same revision, ends the refresh without a request in the
`FAILED_NOT_SENT` state. After `invalid_grant`, the family requires reauthorization while the refresh reads once more,
and recovers only with a newer token set whose access token has not expired and whose refresh token the family never
spent; otherwise `AuthReauthorizationRequiredError` keeps a failed read or a conflict as its cause.

`reload_token_set()` loads once more, or raises `AuthStateConflictError` while a refresh, load, reload, or store runs,
including one whose session ended before its work returned, while a store is pending, or once the provider is closing.
It makes a newer token set current unless that brings back a spent refresh token, recovering a family that failed its
load or stopped once the token set can serve; a family a refresh stopped keeps its state otherwise, and any other family
stops for reauthorization when the newer token set can never serve. It returns the current token set, or None once the
family stopped, and its own failures are raised to its caller alone, leaving the family as it was.

A provider with a `store` needs a `load` too. A refresh that receives a token set stores it once before it becomes
current, expecting the stored revision the last load or store confirmed, or None while none did, so callers are served,
and their scopes checked, only once it is stored. A store that fails, conflicts, outlives the refresh's session, or is
interrupted keeps the token set pending in the `PERSIST_PENDING` state: the refresh raises `AuthTokenStoreError`, or
`AuthTokenStoreConflictError` with the revision the store observed, and later calls raise a new instance of the latest
store failure without a request, while callers arriving during a store wait for it. `retry_store()` stores the same
token set once more, expecting the same revision, and returns it once current; concurrent retries expecting the same
revision share one store. Since a conflict repeats until the expected revision changes,
`retry_store(expected_revision=...)` stores the same pending token set expecting another revision, such as the
`observed_revision` of the conflict once the application has checked what is stored; a revision that is negative or not
an integer, or one at or above the pending token set's, which storing would roll back, raises `AuthConfigurationError`,
and a newer stored token set is adopted with `replace_token_set(..., persist=False)` instead. It raises
`AuthStateConflictError` without a pending store, while a refresh, load, reload, or store runs, including one whose
session ended before its work returned, or once the provider is closing. `replace_token_set` needs a revision above the
pending one too, and by default stores the token set first under the same conditions, expecting the confirmed revision;
one whose store fails is pending as a refreshed one is. With `persist=False` it becomes current at once and is not
stored, leaving the stored revision as it is. A refresh that already failed, as one whose work outlived its session by
more than a second, stores nothing, and a pending token set lives only in its provider, which drops it once closed. Load
and store callbacks must not call their own provider, which waits for them.

Basic charset overrides, resource audience metadata, and generated OAuth provider factories are not available yet;
applications construct providers explicitly.
