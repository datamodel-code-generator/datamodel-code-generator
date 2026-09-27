# Python Client (In Development)

!!! warning "Not available from the CLI yet"
    The HTTPX2 client generator is still being built. Its settings below are fields of the client's flat target
    configuration file and of `ClientGenerationConfig`; the `--generate-client` command line entry point and the
    matching `--client-*` options ship with its release, together with a link to this page from the navigation.

Settings of the generated client that change how its methods are declared or checked do not change the models: the
same OpenAPI document and model settings produce the same model files whichever client settings you choose.

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

## Validation

`validation` chooses what ordinary calls check beyond sending and reading their values. It is a
`ClientValidationConfig` with one mode for each axis, the one every client, view, and call uses unless it selects
another, and the other modes the package lets them select at runtime.

| Axis | Modes | Default | What the mode adds |
|---|---|---|---|
| `request` | `none`, `native`, `schema` | `none` | `none` sends the native value as it serializes; `native` first passes it through its model backend's own validation; `schema` validates the serialized value against its OpenAPI schema |
| `response` | `native`, `schema` | `native` | `native` constructs the declared type through the backend's converter; `schema` validates the received value against its OpenAPI schema before constructing it |
| `arguments` | `none` | `none` | Pydantic validation of a call's arguments is not available yet |

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
a package allows no mode it was not generated with. `pydantic_strict` is kept for Pydantic argument validation. The
generated clients record the allowed modes when they differ from the defaults above:

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

### When a mode is not available

A package must be able to take every mode it allows for every use, or generation fails with `E_CONFIG_VALUE` naming the
use and the mode to select instead:

- `request = "native"` needs a backend validation entry: Pydantic models and dataclasses and msgspec Structs have one,
  stdlib dataclasses and TypedDicts do not.
- `response = "native"` is refused for a union whose members only their schemas tell apart, such as two object models
  read by a stdlib dataclass or TypedDict converter, unless the union's use returns `DecodedValue`.
- A use that a registered codec adapter reads or writes allows only `schema` in its direction.

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.validation.diagnostics -->
<!-- fmt: off -->

```text
E_CONFIG_VALUE binding validation.response /paths/~1orders~1{orderId}/get/responses/200: The 'native' response validation cannot take the response body 200 application/json of GET /orders/{orderId}, which tells its union members apart only by their schemas; select 'schema'
E_CONFIG_VALUE binding validation.request /paths/~1pets/get/parameters/0: The 'none' request validation cannot take the query parameter limit of GET /pets, which goes through a registered adapter that validates it against its schema; select 'schema'
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
`schema`.
