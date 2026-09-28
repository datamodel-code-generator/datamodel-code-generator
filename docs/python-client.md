# Python Client (In Development)

!!! warning "Not available from the CLI yet"
    The HTTPX2 client generator is still being built. Its settings below are fields of the client's flat target
    configuration file and of `ClientGenerationConfig`; the `--generate-client` command line entry point and the
    matching `--client-*` options ship with its release, together with a link to this page from the navigation.

Like the server, the client needs Python 3.11 or later, both to run `datamodel-codegen` and as the target Python
version; a target below 3.11, including the default 3.10, is refused with `E_CONFIG_VALUE`.

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
fields missing a required one ! TypeError: create_pet() missing required field arguments for application/json: 'kind' []
a field of another media ! TypeError: create_pet() takes no such field arguments for application/x-www-form-urlencoded: 'kind' []
fields without a media type ! ConfigurationError: ConfigurationError(operation_id='createPet', field_path='media_type', condition='missing') [operation_id='createPet', field_path=('media_type',), condition='missing'] configuration_error
fields for text ! TypeError: log_visit() takes no field arguments for text/plain: 'note' []
update naming only a media type ! ConfigurationError: ConfigurationError(operation_id='updatePet', field_path='media_type', condition='without_body') [operation_id='updatePet', field_path=('media_type',), condition='without_body'] configuration_error
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
| `max_network_sends` | `1` | Maximum number of send slots the call may reserve |
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
`max_network_sends=0` raises `BudgetExceededError` before a send.

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
`deadline_at`, `elapsed`, `delivery_state`, and the interrupted activity in `phase`. Neither timeout causes a retry.

Synchronous total deadlines are cooperative: the client checks them around callbacks and encoding/decoding, and at
SDK send and chunk boundaries. A blocking callback, DNS resolution, or native socket operation can return after the
deadline; the client then raises `DeadlineExceededError` and starts no further network work. Native read caps are
latched when acquisition or body reading begins, so a sequence of reads or HTTP/2 stream processing can overrun a
total deadline. There is no background thread that forcibly interrupts a synchronous call.

Async clients require asyncio. They bound their owned asynchronous operations by deadline, explicit cancellation,
and client closing. Native task cancellation propagates as `asyncio.CancelledError`; it is never converted into an
SDK timeout, even when a result becomes ready at the same time.

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

Hooks observe `limiter_wait` followed by `limiter_acquired` when acquisition succeeds. A limiter callback failure
raises `LimiterExecutionError` with `action="acquire"` or `"release"` and the original callback exception as `cause`.

### Counters and cleanup

Each logical call has one `call_id`. `resource_attempt_count` counts attempts at send time, `network_send_count`
counts adapter send invocations, and `network_send_budget_used` counts reserved send slots. Preparation and hook
failures before sending do not increment the attempt count. A reserved send slot is never refunded.

`ResponseInfo`, terminal call events, and `SDKError` expose snapshots of these counters, together with
`redirect_count`, `auth_exchange_count`, `auth_exchange_budget_used`, `auth_refresh_ids`, and `auth_refresh_pending`.
Errors expose counters even when no response arrived: their `info` remains `None` in that case. The readonly
`wire_send_count` field on errors is currently `None`; adapter invocations do not prove the number of wire sends
inside an injected transport. `BudgetExceededError` identifies the exhausted `budget_kind`, its `limit`, and its
`used` slots.

Closing a client changes it to `CLOSING` immediately. New work is refused, and active calls or stream reads raise
`ClientClosedError` when the SDK observes closing. A view's close affects that view; closing the owning client
affects its views too. Already-buffered responses remain usable.

`cleanup_timeout` bounds the SDK's wait to release responses, body resources, permits, and owned work. It does not
extend the original call deadline or authorize another send. Cleanup failures are attached to an SDK error's
`secondary_errors`, or as safe notes on a native exception when that Python version supports notes. They do not
replace the primary failure. Unfinished cleanup stays owned and observed; a repeated `close()` or `aclose()` can wait
again. A close that exhausts its budget raises `CleanupError` with the unfinished counts. Blocking synchronous cleanup
has the same cooperative limits as other sync callbacks.
