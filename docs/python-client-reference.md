# Python Client Reference

!!! warning "Experimental"

    HTTPX2 client generation is experimental. Its options, generated package, and template values may change. See
    [Experimental Features](experimental.md).

This page is the reference behind [Python Client Generation](python-client.md): how the generated methods are
declared, how values are converted, and the full rules of timeouts, retries, request bodies, redirects, templates,
and errors. The guide covers the common cases with tested examples; come here for the precise rules of one setting.
The [protocol helpers](python-client-helpers.md) have a page of their own. The examples use the layout of the guide's
quick start: the generated package `app.client` and its models `app.models`, inside the application package `app`. The
text calls the generated package `client`, as in `client.errors`.

## Signature style

[`--client-signature-style`](cli-reference/target-generation-options.md#client-signature-style) chooses how every
operation method of the generated package declares its keyword arguments.

| Option | Values | Default | Where |
|---|---|---|---|
| `--client-signature-style` | `"explicit"`, `"unpack"` | `"explicit"` | `--client-signature-style unpack`; `pyproject.toml`: `client-signature-style = "unpack"`; `generate()`: `client_signature_style="unpack"` |

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
        petId: int,
        response_media_type: None = None,
        options: RequestOptions | None = None,
    ) -> models.Pet: ...
    @overload
    def get_pet(
        self,
        *,
        petId: int,
        response_media_type: Literal['application/json'],
        options: RequestOptions | None = None,
    ) -> models.Pet: ...
    @overload
    def get_pet(
        self,
        *,
        petId: int,
        response_media_type: Literal['text/plain'],
        options: RequestOptions | None = None,
    ) -> models.PetsPetIdGetResponse: ...
    def get_pet(
        self,
        *,
        petId: int,
        response_media_type: Literal['application/json', 'text/plain'] | None = None,
        options: RequestOptions | None = None,
    ) -> GetPetResponse:
        """
        Call GET /pets/{petId}.
        """
        return self._core.execute(
            _operations.OPERATION_2,
            (petId,),
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
    def get_pet(
        self,
        **kwargs: Unpack[Operation2Arguments],
    ) -> models.Pet: ...
    @overload
    def get_pet(
        self,
        **kwargs: Unpack[Operation2Arguments1],
    ) -> models.Pet: ...
    @overload
    def get_pet(
        self,
        **kwargs: Unpack[Operation2Arguments2],
    ) -> models.PetsPetIdGetResponse: ...
    def get_pet(
        self,
        **kwargs: Unpack[Operation2Arguments3],
    ) -> GetPetResponse:
        """
        Call GET /pets/{petId}.
        """
        KEYWORDS_2.check(kwargs)
        return self._core.execute(
            _operations.OPERATION_2,
            (kwargs['petId'],),
            options=kwargs.get('options'),
            response_media_type=kwargs.get('response_media_type'),
        ).data
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.signature-style.unpack -->

The TypedDicts live in the private module `client._generated.client_arguments`, one for each distinct signature of an
operation, shared by its normal, `with_response`, `with_raw_response`, and `with_streaming_response` views. Their
names follow the operation's position and may change between generations, so do not import them: declare your own
TypedDict with the keys you forward. The module also holds the list of keywords each method takes:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.signature-style.records -->
<!-- fmt: off -->

```python
class Operation2Arguments(TypedDict):
    """The keyword arguments of one signature of get_pet."""

    petId: int
    response_media_type: NotRequired[None]
    options: NotRequired[RequestOptions | None]


class Operation2Arguments1(TypedDict):
    """The keyword arguments of one signature of get_pet."""

    petId: int
    response_media_type: Literal['application/json']
    options: NotRequired[RequestOptions | None]


class Operation2Arguments2(TypedDict):
    """The keyword arguments of one signature of get_pet."""

    petId: int
    response_media_type: Literal['text/plain']
    options: NotRequired[RequestOptions | None]


class Operation2Arguments3(TypedDict):
    """The keyword arguments of one signature of get_pet."""

    petId: int
    response_media_type: NotRequired[Literal['application/json', 'text/plain'] | None]
    options: NotRequired[RequestOptions | None]


KEYWORDS_2: Final = Keywords(
    'get_pet',
    ('petId', 'response_media_type', 'options'),
    ('petId',),
)
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: python-client.signature-style.records -->

### What differs between the styles

Both styles accept the same direct calls, and forwarding a mapping whose type declares known keys:

```python
from typing_extensions import NotRequired, TypedDict

from app.client import Client
from app.client.options import RequestOptions


class GetArguments(TypedDict):
    petId: int
    options: NotRequired[RequestOptions | None]


def forward(client: Client, arguments: GetArguments) -> None:
    print(client.pets.get_pet(**arguments))
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

## Body field arguments

[`--client-body-arguments`](cli-reference/target-generation-options.md#client-body-arguments) chooses whether a call
may give the declared fields of an object body as keyword arguments instead of the body. `"body"`, the default, keeps the body argument alone; `"both"` adds the fields of every body that can
take them next to the body argument, on every view of the method.

| Option | Values | Default | Where |
|---|---|---|---|
| `--client-body-arguments` | `"body"`, `"both"` | `"body"` | `--client-body-arguments both`; `pyproject.toml`: `client-body-arguments = "both"`; `generate()`: `client_body_arguments="both"`; one operation: its `body_arguments` member of `--client-operations`, where `null` inherits the package's |
| `body_field_names` of `--client-operations` | Property names by media type | none | One operation: its `body_field_names` member of `--client-operations`, such as `{"application/json": {"tag": "pet_tag"}}` |

With `"both"`, a JSON body whose model is `NewPet` takes either call:

```python
from app.client import Client
from app.models import NewPet

with Client() as client:
    client.pets.create_pet(body=NewPet(name="Mimi", tag="cat"), media_type="application/json")
    client.pets.create_pet(name="Mimi", tag="cat", media_type="application/json")
```

In `pyproject.toml`, the same settings are:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.body-fields.pyproject -->
<!-- fmt: off -->

```toml
[tool.datamodel-codegen]
input = "fields.yaml"
input-file-type = "openapi"
output = "app/models.py"
output-model-type = "pydantic_v2.BaseModel"
openapi-scopes = ["schemas", "api"]
target-python-version = "3.11"
formatters = ["builtin"]
disable-timestamp = true
openapi-include-paths = ["/pets*", "/owners"]
generate-client = "httpx2"
client-output = "app/client"
client-package = "app.client"
client-model-package = "app.models"
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

Each field argument takes its model field's name, as the model naming options give it, or for a key that is no
identifier the name such a field would take, and a read-only member has none. A field whose name a parameter, a
call option such as `body` or `options`, or another field takes is suffixed, such as `name_1`.
`body_field_names` names one field of one media type instead, without changing the model or the wire property. Fields
of different media types may share a name. Generation fails when a body field name or an `--aliases` entry takes the
name of a parameter, a call option, or another field of the same media type, and when a body field name names no
field argument or its operation keeps only `body`:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: python-client.body-fields.diagnostics -->
<!-- fmt: off -->

```text
--client-operations: The body field name of the application/json property 'id' of POST /pets names no field argument
--client-operations: The body field name of the application/xml property 'tag' of POST /pets names no field argument
--client-operations: The body field name of the application/json property 'absent' of POST /pets names no field argument
/paths/~1pets/post: The application/json body fields of POST /pets cannot take the argument names 'media_type', 'tag', which other arguments take
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
        petId: int,
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
        petId: int,
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
        petId: int,
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
        petId: int,
        body: UNSET = UNSET,
        name: UNSET = UNSET,
        tag: UNSET = UNSET,
        media_type: None = None,
        options: RequestOptions | None = None,
    ) -> UpdatePetResponse: ...
    def update_pet(
        self,
        *,
        petId: int,
        body: models.PetPatch | UNSET = UNSET,
        name: str | UNSET = UNSET,
        tag: str | None | UNSET = UNSET,
        media_type: Literal['application/json'] | None = None,
        options: RequestOptions | None = None,
    ) -> UpdatePetResponse:
        """
        Call PATCH /pets/{petId}.
        """
        return self._core.execute(
            _operations.OPERATION_1,
            (petId,),
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
`ConfigurationError` for a missing or undeclared media type, as it does for bodies.

- Only the fields a call gives are sent: the model's defaults are not, and `None` sends `null`.
- A required body whose fields are all optional is sent as `{}` when a call gives none of them. An optional body is
  omitted, and naming only its media type still raises `ConfigurationError`.
- The model is built from the fields by its constructor, which validates Pydantic models and dataclasses, while stdlib
  dataclasses, TypedDicts, and msgspec Structs take the values as given.

## Model codecs

The generated models determine validation and conversion. Text in parameters, response headers, forms and text parts
is decoded before the selected backend handles it: a declared integer, number or boolean becomes that builtin when the
text is its canonical JSON literal. Pydantic and msgspec receive any other text unchanged and decide by their native
conversion rules, as they do for unknown object members. Dataclass and TypedDict codecs validate nothing, so for them
such text is a decoding error, like text that cannot become a `Decimal`, date or `UUID` leaf; their JSON bodies are
still constructed without added schema validation. Repeated undeclared members keep their last value.

A package holds the codec of its own model backend only. A Pydantic package imports no msgspec, a msgspec package no
Pydantic, and a dataclass or TypedDict package neither, so the package imports, and mypy, Pyright, and ty check it,
with only the dependencies that generation prints installed.

Generated clients send and read model values through their model backend, with no validation layer of their own:

| Backend | Sends | Receives |
|---|---|---|
| Pydantic v2 `BaseModel` | `model_dump_json(by_alias=True, exclude_unset=True)`; other types through a cached `TypeAdapter` | `TypeAdapter(T).validate_json` |
| Pydantic v2 dataclass | `TypeAdapter(T).dump_json(by_alias=True)`, which leaves out the fields that hold `MISSING` | `TypeAdapter(T).validate_json` |
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
  header, cookie, or part. A style or a form cannot write null, so a `None` member of an object parameter or of a
  URL-encoded or multipart form, and a `FieldPart` whose value is `None`, are left out the same way. A multipart form
  that is left with no part is still sent, as its closing boundary alone. A path segment cannot be left out, so an
  empty array or object in a path parameter fails with a request `DecodeError` before anything is sent.
- **Parameter arguments.** A parameter whose model type is a type alias or a root model takes the type it stands for,
  such as `limit: int` or `tags: list[str]`, never the alias or the root model. An alias's argument is sent as given,
  without validation. With Pydantic models, an argument whose parameter is a root model is built into it before it is
  sent, which checks it as the root model's constructor does: a value it refuses fails with a request `DecodeError`
  with the reason `unencodable`.
- **Parameter defaults.** An optional parameter whose schema declares a boolean, number, or string default of its
  builtin type, alone or with null, defaults to it, such as `limit: int = 20`; any other optional parameter defaults to
  `UNSET`. A parameter with such a default is always sent, with the default when the call omits it: the client pins
  the default it was generated with, so a later change of the server's default does not apply until you regenerate,
  and a default that breaks its own schema's constraints is sent as it is. A path parameter that names a placeholder
  of its path is a required argument without a default, as OpenAPI requires, even when the document leaves out
  `required` or declares it `false`; a document with a path parameter that names no placeholder of its path is refused.
- **Responses.** Bodies, parts, stream events, and socket messages are converted by the model backend into the declared
  types, which check what those types declare and coerce as the backend does; a value the type refuses raises
  `DecodeError` with the reason `invalid_value`. Read-only and write-only behavior follows the generated model options.
- **Binding.** A missing required argument, an unknown keyword, or a media type the operation does not declare fails
  before anything is sent. A parameter value with text that UTF-8 cannot encode, such as a lone surrogate, fails with
  a request `DecodeError` of the reason `unencodable`, with a `ParameterEncodingError` cause, before anything is sent.
- **Path segments.** Path arguments that make their segment `.` or `..` once encoded in their parameters' styles and
  put into the path template, `%2E` in either case counting as `.`, such as `..` in the simple style, the empty string
  in the label style, or `%2e` with `allowReserved`, fail with a request `DecodeError` at the segment's first parameter
  whose encoded text is non-empty, with a `ParameterEncodingError` cause, before anything is sent: URL normalization in
  clients, proxies, and servers would remove the segment and send the call to another resource, and encoding the dots
  does not prevent it. `...` and `.a` are sent as they are, and a dot segment the path template spells is not refused.
- **Path text.** The literal text of a path template is sent as RFC 3986 allows it in a path, as path arguments are:
  any other character, such as `\`, `|`, `^`, `[`, `]`, `?`, `#`, a space, or non-ASCII text, is percent-encoded as
  UTF-8, `\` as `%5C`, so no client, proxy, or server reads `\` as `/` or ends the path early. A `%` and two hex
  digits is an octet the document already encodes and is sent as it is, so `/files/a%20b` sends `a%20b`, which the
  generated server routes by its decoded value; any other `%` is sent as `%25`. The client holds the encoded path from generation, so calls pay nothing for it.
- **Header names.** A header parameter, response header, or part header whose name is not an RFC 9110 token, such as
  `X Sp` or `X-Näme`, stops generation with an error, as it stops the generated server's, since no HTTP client or
  server can send it. A declared media type whose text is not ASCII header text, such as `text/plain; label="café"`,
  stops generation too, since its Content-Type could not be sent; a call's `media_type` of such text raises
  `ConfigurationError` with the reason `invalid_value` and the `field_path` `("headers", "Content-Type")`.
- **Header values.** A header carries header text only: HTAB, SP, and visible ASCII, without whitespace at either end,
  the text every HTTP client and server reads alike. RFC 9110 leaves any other byte opaque, and servers read it as
  Latin-1 or as UTF-8; the generated FastAPI server answers `400` for it in a header parameter of the operation. A
  header parameter of any style, or of text content, whose value, array item, or object member is any other text, such
  as `café` or one with a control character, fails with a request `DecodeError` of the reason `unencodable`, with a
  `ParameterEncodingError` cause, before anything is sent, instead of arriving as other text. A header parameter of JSON
  content is written with its non-ASCII text and DEL escaped, so it carries any text; declare the header that way, or
  apply an encoding the API defines yourself, such as percent-encoding, Base64, or RFC 8187, and decode it in the
  service. The headers of `default_headers` and `extra_headers` and an `idempotency_key` follow the same rule; see
  [timeouts and cancellation](#timeouts-and-cancellation). A value a server gave that a protocol helper writes to a
  header parameter, such as an event ID, a cursor, or a binding's value, follows it too. A declared response header is
  read by the same rule: a value with a byte outside ASCII fails its `decode_..._header` accessor with a response
  `DecodeError` of the reason `invalid_header`, with a `MalformedError` cause, so its text does not depend on how HTTPX2
  decodes the response's headers, as a whole set: ASCII, else UTF-8 when every header of the response is UTF-8, else
  Latin-1. `ResponseInfo.headers` keeps the text HTTPX2 decodes. The generated server sends ASCII header text only.
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

The guide's [timeouts](python-client.md#timeouts) cover the settings; the details below cover the budget across
attempts, cancellation, streams, and the clock the client reads.

### An optional budget across attempts

The runtime supplies no whole-call budget by default. Set `Client(total_timeout=60)` to bound every call to a minute.
Native phase timeouts bound each I/O wait; they do not limit the total duration of a call that keeps making progress.

```python
import httpx2

from app.client import Client
from app.client.options import RequestOptions


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

from app.client import Client
from app.client.options import RequestOptions


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
from app.client import Client
from app.client.options import Clock


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

from app.client import Client


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
generated README, `app/client/README.md`; it is never silently embedded in generated defaults.

```python
from app.client import Client
from app.client.options import RequestOptions, RetryOptions


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

With the [Python API](python-client.md#python-api), the same operation setting is a mapping of `client_operations`:

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
exactly-once business execution. A key is [header text](#model-codecs), HTAB, SP, and visible ASCII without
whitespace at either end: for any other value, such as `clé`, a line break, a space at its end, or a number,
`RequestOptions(idempotency_key=...)` raises `ConfigurationError` with the reason `invalid_value` and the
`field_path` `("idempotency_key",)`.

```python
from app.client import Client
from app.client.options import RequestOptions


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

A package accepts the request bodies its operations declare. A schema-less form body exposes `FormData` in `bodies.py`
and uses the core operation runtime, without the binary or multipart body runtime. Without a binary or multipart
request body, `request_raw` takes `bytes`; with a binary body `request_raw` also takes the binary inputs below, and
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

A `MultipartBody` with parts is sent through HTTPX2's `files=` encoding, its parts in their order: each `FieldPart` and
`FilePart` keeps its name, filename, media type and extra headers, and parts may repeat a name. A field's value goes
through its member's model codec, a repeated member sends a part for each item, a styled member the parts its style
gives, and a media type the member's encoding declares must cover the one a part names. A member's `style`, `explode`,
and `allowReserved` apply in OpenAPI 3.1 and 3.2 documents, whose parts carry the style's text without percent-encoding,
so a delimited value that holds its style's delimiter (`,`, a space, or `|`) raises a request `DecodeError` with the
reason `unencodable` before sending; OpenAPI 3.0 ignores them for `multipart/form-data`, and the member is written as
one without them, in its `contentType`. Generation stops for a form whose exploded object that allows other properties
shares the form with another such member, a `deepObject` member, or other properties of its own that its schema allows
with `additionalProperties` as `true` or a schema, or that its Pydantic model keeps by its configuration, such as
`--extra-fields allow`, which would write the same names, as it does for query parameters.
The SDK does not check parts against the schema. A part header HTTPX2 refuses, and a part that is not
a `FieldPart` or a `FilePart` with a string name, raises a request `DecodeError` with the reason `unencodable` before
sending. A body whose parts are all bytes is encoded once, with `Content-Length`; a body without parts, which HTTPX2
would not send, is written by the SDK as its closing boundary alone, with the `multipart/form-data` Content-Type that
names the boundary, which a server reads as a form without members. Each body gets a boundary of its own.

The type of a member decides its parts, in a `MultipartBody` and in a form-data body sent as its model alike. A
member repeats, one part for each item, when its type is an array. A value whose type has one scalar kind is text: a
string as it is, a number or a boolean as JSON writes it. A value whose type has none is one `application/json` part:
an object, a mapping, a union of a string and an object, an enum of mixed kinds, or a union of an array and another
type such as `string | string[]`, whose array is then one part rather than one for each item. A member without a
type, whose schema is `{}`, takes any value, and that value is one `application/json` part as well, a string or an
array included; an array whose items have no type has one such part for each item. A media type that the member's
`encoding` declares takes precedence. A multipart response is read by the same rule, and a part that names a JSON
media type is read as JSON; a part of a member without a type that names none is read as text.

The client sends a JSON part as a field, without a filename, and Starlette, which the generated server and FastAPI
use, answers `400` for a field part of more than 1 MiB. Send such a value as a `FilePart` with a filename, which
Starlette does not limit by size, or read the form in your own server code with a larger `max_part_size`.

The properties a schema types without declaring them, with `additionalProperties: {...}`, follow the same rule under
whatever names a body gives them, by the type of their values: an object is one `application/json` part, an array of
objects one for each item, and a union of an array and another type one part. The type is the one the model keeps them
as, in a field of its own or one it inherits from a schema combined with `allOf`, and for a model without such a field,
the type of their schema, the form's own or that of a schema it combines with `allOf`, also when `--reuse-model` makes
the form's model inherit an equal one whose schema types them otherwise. Other properties the schema allows without a
type, with `additionalProperties: {}`, `true`, or no `additionalProperties`, are each one `application/json` part, as a
member without a type is, a string or an array included. A multipart response reads typed ones by their type too. A
URL-encoded form writes them as text of that type's kind, one pair for each item of an array, and generation stops with
`A parameter value needs one unambiguous scalar kind` at the `additionalProperties` that types them when the type has
none. A URL-encoded form writes a declared member by the same rule, and stops with that message for a member without one
scalar kind, unless the member's `encoding` gives it a style or a `contentType` (see [URL-encoded members with an
encoding](#url-encoded-members-with-an-encoding)). The generated FastAPI server reads the members of form bodies, these
other properties included, by this rule too, and stops for a member or other properties without one scalar kind with the
same message at the same place. Both also refuse a URL-encoded body whose schema has `patternProperties`
(`Pattern properties have no builtin parameter encoding`) or is not an object, such as an array, a string, or
`properties` without `type: object` (`A URL-encoded value must be an object`); the server reads such a body only in the
request body mode. A form body whose schema is a union of one schema and `null` alone is written as the form of that
schema, by its members and other properties; a multipart body whose schema is a union of several schemas has no members
to write parts by, and generation stops with `needs an object schema to be sent as parts`, as the server's generation
stops for it.

A multipart response is read only when the operation declares one, with the standard library's MIME parser: a part's
bytes are kept as they arrived unless it names a transfer encoding, and a broken body, or a part that is itself a
multipart body or a message, raises `DecodeError` with the reason `invalid_framing`.

```python
from pathlib import Path

from app.client import Client
from app.client.options import RequestOptions


def upload_file(client: Client, url: str, path: Path) -> bytes:
    response = client.request_raw("PUT", url, body=path, options=RequestOptions(max_retries=2))
    return response.read()
```

### URL-encoded members with an encoding

An `application/x-www-form-urlencoded` request body's `encoding` can give a member a `style`, `explode`, or
`allowReserved`, in every OpenAPI version, and the member is then written as the query parameter that encoding
describes, as percent-encoded pairs: an object with `style: form`, the style of an encoding that names none, and
`explode: true`, its default, as the pairs of its own members, a `deepObject` as `name[key]` pairs, and `form` without
explode, `spaceDelimited`, and `pipeDelimited` as one delimited pair. These fields take precedence over a `contentType`.
A `contentType` alone writes the member as one pair of that text, or of JSON for a JSON media type; any other media
type, such as `application/xml`, stops generation with `The member content has no builtin encoding`. A style the
member's type has no form for, such as `deepObject` for an array, stops generation too, as it does for a query
parameter. Generation also stops for a form whose exploded object that allows other properties shares the form with
another such member, a `deepObject` member, or other properties of its own that its schema allows with
`additionalProperties` as `true` or a schema, or that its Pydantic model keeps by its configuration, such as
`--extra-fields allow`, which would write the same names. A form that leaves its other properties unsaid, with a model
that drops them, is written, and its exploded object takes the names no other member writes. The other members are
written as in a form without such members. The generated FastAPI server reads these members as the client writes them.

## Redirects and transport construction

Calls are sent through the native client with `send(request, stream=True)`: its `auth`, event hooks, redirect
setting, framing, and content decoding are effective. `follow_redirects` of the client, a view, or a call's
`RequestOptions` is the native boolean for that call; unset, an SDK-created client follows no redirect and an injected
one keeps its own. HTTPX2 follows redirects itself, with its method rewriting, its `max_redirects` limit
(`TooManyRedirects` raises `APIConnectionError`), and its header rules: on every redirect it replaces the `Cookie`
header of the request, a cookie parameter included, with the cookies of the client's cookie jar that match the hop, such
as one a `Set-Cookie` response header set earlier, and it drops `Authorization` across origins. The request of the
first hop carries no jar cookie, because the SDK builds it itself. Every hop consumes the same call deadline, and a
failure after a redirect was answered is never sent again.

In a package that declares security schemes, a request carrying a credential at a scheme's position other than
`Authorization` (an API key in a header, query field, or cookie, however the request came to carry it) is never
redirected: its 3xx is the final response, which the typed call raises as `APIStatusError` and `with_raw_response`
returns with its `Location`. To follow it anyway, place the credential with an `httpx2.Auth` of your own on the
injected client, which HTTPX2 runs on every request it sends:

```python
import httpx2

from app.client import Client


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

from app.client import Client


def configured_client(ca_file: str) -> tuple[Client, httpx2.Client]:
    context = create_default_context(cafile=ca_file)
    native = httpx2.Client(verify=context, limits=httpx2.Limits(max_connections=50, max_keepalive_connections=10))
    return Client(http_client=native), native
```

An injected native client keeps its own pool, proxy, and TLS settings. The root closes only the native client it
created, once; views share their root. A borrowed client's cookies, headers, and query defaults are not merged
into SDK requests; its `Accept-Encoding` is.

## Templates

The client templates come from [`--custom-template-dir`](cli-reference/template-customization.md#custom-template-dir),
as model templates do: the files of its `client` directory replace the builtin roles of the same name:
`facade.jinja2` (the package initializers and the public modules `options`, `errors`, `responses`, `auth`, `bodies`,
and `protocols`, and the webhook packages' initializers and `webhooks/keys.py`), `client.jinja2` (`_client.py` and `_async_client.py`), `resource.jinja2` (each
resource's `_sync.py` and `_async.py`), `types.jinja2` (each resource's `types/<resource>/_operations.py`),
`arguments.jinja2` (`_generated/client_arguments.py`), `operations.jinja2` (`_operations.py`), `security.jinja2`
(`_generated/security.py`), `model_bindings.jinja2` (`_generated/model_bindings.py`), `helper_plans.jinja2`
(`protocols/_plans.py`), `helpers.jinja2` (`protocols/_helpers.py` and `protocols/_async_helpers.py`),
`webhook.jinja2` (each webhook helper's module), `readme.jinja2`, and `runtime.jinja2` (the runtime reference). A
role the directory does not hold, or a custom template directory without a `client` directory, keeps its builtin
template. The builtin templates are in the `_client/templates` directory of the installed package; copy
one to start from it. Each role receives the values its builtin template uses and, as custom model templates do, the
`#all#` entry of [`--extra-template-data`](cli-reference/template-customization.md#extra-template-data); the role's
own values take precedence over the extra data.

!!! warning "Overrides are not checked against the builtin roles"

    A file of the `client` directory that is not one of these role names is ignored without a warning, so a
    misspelled or renamed role keeps its builtin template. A value that an override names and the builtin no longer
    provides, such as one that an older copy of a builtin template uses, prints as an empty string and iterates as
    nothing, as Jinja's default `Undefined` does; the values a role receives can change while client generation is
    experimental. Generation can then succeed and write a package that fails when it is imported. Reading an attribute
    of such a value, or calling it, fails the render instead, which stops the run with an `Error` that names the
    template. After each upgrade, compare your overrides with the
    current builtin templates, and in CI import the generated package, or run `datamodel-codegen --check` and your
    tests.

The templates write every module, class, function, signature, and call; they receive records of names, type
annotations, and the values of the runtime plans. Each role receives these top-level values, where `imports` is the
module's import block and the facade's `module`, which the builtin template does not use, is the module's dotted path
in the package, empty for the package itself:

| Role | Values |
| --- | --- |
| `arguments.jinja2` | `docstring`, `imports`, `records`, `keywords` |
| `client.jinja2` | `asynchronous`, `docstring`, `imports`, `type_checking`, `lazy_imports`, `defaults`, `name`, `class_docstring`, `core`, `view_keywords`, `client_keywords`, `credentials`, `create`, `scheme_credentials`, `unset`, `request_options`, `cached_property`, `raw`, `binary`, `manager`, `self_type`, `traceback`, `add_secondary`, `resources`, `protocols`, `protocols_module`, `helper_settings` |
| `facade.jinja2` | `module`, `docstring`, `future`, `imports`, `checking`, `classes`, `exports`, `lazy`, `loader`, `listing` |
| `helper_plans.jinja2` | `docstring`, `imports`, `accessors`, `plans` |
| `helpers.jinja2` | `asynchronous`, `docstring`, `imports`, `core`, `cached_property`, `namespaces`, `helpers` |
| `model_bindings.jinja2` | `imports`, `codec_imports`, `models_annotation`, `models`, `codecs` |
| `operations.jinja2` | `docstring`, `imports`, `servers`, `operations` |
| `readme.jinja2` | `package`, `public_modules`, `reference`, `signature_style`, `body_arguments`, `backend`, `model_package`, `operations`, `contracts`, `helpers`, `stream_label`, `resumes`, `sockets`, `kinds`, `compression`, `dependencies`, `security`, `capabilities`, `runtime` |
| `resource.jinja2` | `docstring`, `imports`, `core`, `cached_property`, `overload_decorator`, `views` |
| `runtime.jinja2` | `package`, `oauth`, `schemes`, `security`, `raw_bytes`, `multipart_requests`, `credentials`, `example`, `flows`, `pagination`, `polling`, `cache`, `uploads`, `streams`, `sockets`, `compression` |
| `security.jinja2` | `docstring`, `imports`, `schemes`, `root_annotation`, `root`, `operations` |
| `types.jinja2` | `docstring`, `imports`, `type_alias`, `overload_decorator`, `aliases` |
| `webhook.jinja2` | `kind`, `name`, `algorithm`, `timestamp`, `imports`, `exports`, `typevar`, `plan`, `functions` |

`resource.jinja2` receives one view for each class of a resource module, with its cached `attributes` and its
`methods`. A method has its `name`, `view` (`plain`, `metadata`, `raw`, or `streaming`), whether it is a
`coroutine`, its `docstring`, its `overloads` and `implementation` signatures, each with its `parameters` (`name`,
`annotation`, `default`) or the TypedDict it `unpacked`, and its `returns`, the keywords record it `check`s an
unpacked call with, and its `call` of the client core: the `operation`, the `parameters` tuple, and the keyword
`arguments`. The plan constants of `operations.jinja2`, `security.jinja2`, and `arguments.jinja2` have a `name`, an
`annotation`, a `constructor`, and `arguments`, each with a `keyword`, empty for a positional one, and a `value`; so
do the `plans` of `helper_plans.jinja2` and the `plan` of `webhook.jinja2`. `helper_plans.jinja2` also receives the
typed `accessors` the plans read items and results with: each has a `name`, the `data` type it reads, the type it
`returns`, its `docstring`, the `checks` that end its read early (the `value` local, the `read` it keeps, whether it
ends at `none`, the `unset` type it ends at, when there is one, and the `missing` sentinel it ends at, when there is
one), and its `result`. `helpers.jinja2` receives the helper `namespaces`, each with its `name`, `docstring`, the
`core` it takes, the core it `wrap`s that in at the root, and its `children`, and the `helpers`, each with its `name`, `docstring`, `methods`, and the `handle` class a polling
helper with remote cancellation returns. A helper method, like a function of `webhook.jinja2`, has its `name`, whether
it is a `coroutine`, its `positional` parameters and the `keywords` after `*`, what it `returns`, its `docstring`
(empty for a webhook function, whose builtin template writes its own), and the runtime `function` it returns the call
of with its `arguments`. The `keywords` may be empty, as an unsigned webhook function's are, and a template writes
`*` only when they are not. `webhook.jinja2` receives the signature `kind`
(`none`, `adapter`, or `builtin`), the helper's `name` and its signature's `algorithm`, and whether deliveries carry a
`timestamp`; `model_bindings.jinja2` receives the stdlib field maps as `models`, each with its `type` and `value`, and
the `codecs`, each with its `name`, `annotation`, `value`, and `description`. A builtin template
writes each parameter, argument, export, and plan keyword on its own line, and leaves the type annotations and plan
values on one line for the formatter.

An operation method's docstring is the `description` of its `--client-operations` entry, or, with `--use-schema-description`, the
operation's summary and description, or else its route, such as `Call GET /pets/{petId}.`, formatted as model
docstrings are, on one line with `--use-single-line-docstring`. A template that does not parse or render stops the
run with an `Error` that names the file in the custom template directory and, when Jinja knows it, the line.

## Errors

Every exception the client defines derives from `SDKError` and is imported from `client.errors`. Python's own
exceptions pass through unchanged: `TypeError` for keywords a method cannot bind, `FileExistsError` for an existing
download target without `overwrite=True`, and the native cancellation exception of a cancelled task. Each `SDKError`
constructor is keyword-only and does not validate field values. Each carries `reason`, a stable snake_case symbol or
`None`, `operation_id`, `info`, the response metadata when one arrived, `cause`, and the measurements `attempt_count`,
`elapsed`, and `request_id`. Messages name safe call metadata (the class, `operation_id`, `reason`, and a few
class-specific details), never headers or credentials. The one payload a message carries is the start of the response
body in an `APIStatusError` message, described below; treat that message as response data before you log it. A
secondary failure beside the primary error, such as a cleanup or invalidation failure, is named in the primary error's
`__notes__`, never in an attribute or in its place.

| Exception | Raised for | Fields beside the shared ones |
|---|---|---|
| `ConfigurationError` | A setting, argument, or call the client refused, including the reasons `client_closed`, `response_consumed`, and `invalid_state` | `field_path`, `reason` (`invalid_value` by default), `helper_id` |
| `APIConnectionError` | A classified I/O failure of the transport, with the native failure as `cause`; the reason is `None` for a transport failure, `delivery_unknown` when a WebSocket send or an upload append or completion may have reached the server and is never sent again, and `negotiation_failed` for a WebSocket 101 that selected no offered subprotocol | None |
| `APITimeoutError` | A subclass of `APIConnectionError`: a phase cap that expired, with the reason `phase_timeout`, or the call's or stream's total timeout, with the reason `deadline_exceeded` | None |
| `APIStatusError` | A final status the operation does not declare as a success | `status_code`, `headers`, `request_id`, `body`, `body_bytes`, `truncated` |
| `DecodeError` | A request argument its wire form cannot carry, or a response, stream event, record, or message that cannot become its declared value | `direction` (`request` or `response`), `location`, `body_bytes`, `truncated`, `media_type` |
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
or `malformed_coding` for a body whose content coding does not decode. A stream event, NDJSON record, or WebSocket
message that does not decode has the reason `missing`, `null`, `type`, `value`, or `malformed`, the `location` `("event", sequence)` or `("message", sequence)` with a pointer
where a member is at fault, and at most 64 KiB of it as `body_bytes`. `AuthError` has the reason `provider_failed`, `invalid_expiry`, `oauth_error`, `timeout`, or `reauthorization_required`.
It keeps no credential material or provider description itself; the `cause` of `provider_failed` is the application's
own exception from its credential callable or token callback.

Helpers raise `ProtocolDataError`, `SessionLimitError`, `StreamInterruptedError`, and
`WebSocketClosedError`, described with each helper, and `SDKError` with the reason `store_failed` for a cache store that
fails.
