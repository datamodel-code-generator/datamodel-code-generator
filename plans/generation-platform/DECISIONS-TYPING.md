# Type contract for new generation targets

2026-09-18. Adopted rules for new servers/clients, private runtimes in generated packages, public extensions, and the new target code that produces them. This is not a product implementation. Servers support Pydantic v2 BaseModel / Pydantic v2 dataclass; clients support the five established backends. [ENTRY](DECISIONS-ENTRY.md) owns mutually exclusive CLI selection; this document adds no simultaneous-generation entry point.

Do not change existing D models, annotations, imports, public APIs, or ordinary-generation dependencies/performance. Model bytes produced by ordinary generation and target generation with the same D and explicit settings must be identical. Models under an explicit `api` scope are likewise compared against the same scope/config; the target must not edit models to strengthen types or create separate TypedDict models. TypedDicts in this document are records for call arguments, settings, and metadata. Preserve Any, unspecified fields, and existing aliases emitted by D according to schema/config/backend, treating them as boundaries with recorded provenance under §8.

Reflect type connection points with identical shapes in [CLIENT](DECISIONS-CLIENT.md), [RUNTIME](DECISIONS-RUNTIME.md), [PROTOCOLS](DECISIONS-PROTOCOLS.md), [MODEL-CODECS](DECISIONS-MODEL-CODECS.md), and [FASTAPI](DECISIONS-FASTAPI.md). Do not accept contradictions with old shapes merely by adding a precedence clause here. Typing must not change wire validity, ownership, cancellation, retries, or native/codec selection.

## 1. Supported Python, dependencies, and adopted features

The common syntax baseline for new targets is Python 3.10. Use the same ordinary syntax on 3.10–3.14, with `TypeVar` / `Generic` / `Protocol` forms. Do not emit `class X[T]`, `def f[T]`, `type Alias = ...`, or new syntax for type-parameter defaults. Independently honor existing backend/capability minimum versions; for example, do not claim Python 3.10 support for a package selecting PROTOCOLS WebSocket. A newer D target-python setting must not cause the private runtime alone to branch into another syntax.

New generated distributions explicitly declare the established `typing-extensions>=4.16`. This lower bound already exists in [D at the implementation baseline](https://github.com/datamodel-code-generator/datamodel-code-generator/blob/4f96e22ea403a66faae96f3949d41dd61fc1186f/pyproject.toml#L42) and [CODECS](DECISIONS-MODEL-CODECS.md); it is not a D dependency change. Do not add new-module imports/type reflection to ordinary D paths where no new target is selected. Type checkers are not generated-package runtime dependencies.

|Feature|Adopted uses and limits|
|---|---|
|`Generic` / `TypeVar` / `Protocol` / `Literal`|Value correlations in §3–7, operation/media/helper names, and public callbacks. Do not emit unparameterized Generics, bare `Callable`, or bare `dict/list/Mapping` in new source|
|`TypedDict` / `Required` / `NotRequired`|Settings and fixed keyword records. Represent presence and nullability separately. Consistently import these from `typing_extensions` in new targets so generic TypedDicts work on 3.10 too|
|`Unpack[TypedDict]`|Private wrappers that need to forward existing fixed keyword sets. Do not replace readable public-operation keyword-only signatures with generic kwargs|
|`ReadOnly`|Fields in TypedDicts used to observe generated metadata/settings. A shallow static write restriction, not a substitute for deep immutability or runtime validation. Do not insert it into existing D TypedDicts|
|`ParamSpec` / `Concatenate`|Only when preserving all arguments in existing transparent callable wrappers. Use dedicated Protocols for fixed-signature hooks/adapters. Do not add decorators/callback layers just to use a type feature|
|`TypeGuard` / `TypeIs`|Only existing narrowing points that actually validate input. TypeGuard must justify its positive assertion; TypeIs must justify both positive and negative assertions. Do not use TypeIs to claim a value is not of a Python type merely because it fails a value constraint such as finite-float validation|
|`Never` / `assert_never`|Returns with no successful value, empty operation Literal sets, and unreachable branches in closed tagged unions. Do not hide unsupported schema values as Never|
|`Self`|Only methods such as `__enter__` that actually return themselves. When `with_options()` creates a concrete Client view, return Client / AsyncClient without promising subclass preservation|
|`TypeAliasType`|Recursive JSON aliases in the new runtime in §2. Use the backport through ordinary assignment syntax to avoid forward-reference problems after import from another module on 3.10|
|`TypeForm`|Not adopted initially. Union/TypeAliasType positive cases do not pass the pinned Pyright's ordinary configuration, so explicitly expose the §8 type-expression reflection boundary as object. Do not claim support through experimental flags or casts|
|Type-parameter defaults|Use `TypeVar(default=object)` only for unknown payloads of generic exceptions in §6. This is not a reason to omit ordinary codec/response/handler type arguments|
|Not adopted|TypeVarTuple, variadic shapes, PEP 695 syntax, new TypedDict closed/extra_items syntax, type-checker plugins, and empty Protocols satisfied by every object, none of which are needed initially. Do not add recent features that do not improve actual correlations|

Import `Required/NotRequired/ReadOnly/Unpack/Self/Never/TypeIs/TypeAliasType/assert_never/assert_type`, which are absent from 3.10, and TypeVar with defaults from `typing_extensions`. `Generic/Protocol/Literal/overload/ParamSpec/Concatenate/TypeGuard` may use Python 3.10's `typing`. Import types appearing in public annotations where they can be resolved at runtime; do not hide types FastAPI needs exclusively under `TYPE_CHECKING`. [Python 3.10 typing](https://docs.python.org/3.10/library/typing.html), [Python 3.14 typing](https://docs.python.org/3.14/library/typing.html), [typing_extensions](https://typing-extensions.readthedocs.io/en/latest/) (evidence scope in §10).

## 2. The JSON/object boundary

The new runtime has the following two public JSON types. Types alone cannot guarantee finite numbers, depth/size, duplicate-key rules, precision, or copy ownership, so preserve CODECS' existing validation. Including `Decimal` does not permit arbitrary Python objects as JSON.

```python
from collections.abc import Mapping
from decimal import Decimal
from typing import Union
from typing_extensions import TypeAliasType

JSONScalar = TypeAliasType("JSONScalar", Union[None, bool, str, int, float, Decimal])
JSONValue = TypeAliasType(
    "JSONValue",
    Union[JSONScalar, list["JSONValue"], tuple["JSONValue", ...], dict[str, "JSONValue"]],
)
WireValue = TypeAliasType("WireValue", Union[JSONScalar, tuple["WireValue", ...], Mapping[str, "WireValue"]])
```

`JSONValue` represents user input/thawed values; `WireValue` transfers immutable wire representations. A Mapping annotation alone does not prove the actual object is immutable. `freeze_wire` and existing codec boundaries recursively validate and copy ownership into ordered mappings/tuples; do not trust and retain mutable Mappings supplied from outside. Public `RawResponse.json()` / `AsyncRawResponse.json()` return `JSONValue`, not models. Schema-less JSON also uses WireValue/JSONValue; text uses str, binary uses bytes, and other media retain their existing media types.

First contain JSON-parser results, dynamic-module imports, and untyped external-framework returns as `object`. Validation functions actually inspect/construct structure as `object -> concrete record/JSONValue/DecodedValue[T]`. Do not turn parser Any into T by return annotation alone. A model T is obtained only from successful conversion by a converter/ModelCodec[T] already bound to that T. Do not fall back to another type, raw, or Any after wire-validation failure.

It is prohibited to return `TypeGuard[Model]` merely after checking for a dict or keys, prove method signatures or generic T solely through `isinstance(x, runtime_checkable Protocol)`, or apply `cast(Model, ...)` to arbitrary JSON. Checking that an unknown dynamic callable is callable or inspecting its signature does not prove its return type either. Separate statically typed extension entry points from validation responsibilities at dynamically loaded boundaries. [Narrowing specification](https://typing.python.org/en/latest/spec/narrowing.html).

## 3. Shared rules for generics, variance, and selectors

`ModelCodec[T]` / `OutboundCodec[T]` / `NativeOutboundCodec[T]` / `EnvelopeOutboundCodec[T]` remain invariant because they both receive and return T. Do not change the established variance of `ModelValue[T]` / `ModelInput[T]` / `DecodedValue[T]`. Do not collapse a codec union into `OutboundCodec[T1 | T2]`. Output-only types whose public fields are readonly, such as `Response[T_co]` and existing frozen `HTTPResult[BodyT_co]`, may be covariant. Do not declare mutable Protocol attributes covariant; use properties when they actually are readonly. Handles and ownership wrappers remain invariant unless the required proof exists.

Generate Literal overloads from actual declared names/media/statuses. When a dynamic selector can select multiple types, return the union of every possibility. Do not create a generic method that takes a specific name and returns an arbitrary T. A status=int fallback includes all reachable exact/range/default destinations, rather than declaring only range results after excluding exact overloads. For example, a dynamic str header name returns the union of every declared header type plus Unset where required; undeclared names retain existing selection errors.

This union fallback is safe for **factories that take only a selector**. Do not expose a sending-method fallback of `media_type: str, body: A | B`, which would also accept incorrect Literal/body combinations. Preserve dynamic media through the nominal selectors in §4. Overload implementations handle every input/return branch; detailed overload declarations must not conceal Any implementations. [Generics specification](https://typing.python.org/en/latest/spec/generics.html), [overload specification](https://typing.python.org/en/latest/spec/overload.html).

## 4. Client arguments, media, and returns

|Public/internal surface|Fixed type|
|---|---|
|Ordinary method|Annotate with CLIENT's final keyword names, required/nullable/Unset rules, and actual OutboundType unchanged. Return the selected success union: None when successful content is absent, Never when no success declaration exists|
|Response views|Ordinary T; with_response returns Response[T]; raw returns RawResponse / AsyncRawResponse. An ordinary async method is `async def ... -> T`, without additionally returning Awaitable[T]|
|Streaming view|Sync returns ContextManager[RawResponse]; async is an **ordinary def** returning AsyncContextManager[AsyncRawResponse]. Do not require an unnecessary await before `async with`|
|Request/header/part factories|The correct NativeOutboundCodec[T] / EnvelopeOutboundCodec[T] for each Literal combination. A dynamic selector returns a union of codecs themselves. Do not merge header/status/media into one type based only on names|
|Argument forwarding|A fixed `<Method>Arguments` TypedDict for each operation. Required keys use Required, optional keys use NotRequired, and None/Unset values follow the original signature. Only necessary private wrappers use `**kwargs: Unpack[<Method>Arguments]`. This is not a separate model duplicating model fields|
|Options forwarding|Do not overlap keys in Unpack with explicit arguments such as `options`. Since the original TypedDict may be a structural subtype with extra keys, do not blindly re-expand the whole dict into another callback; assemble only that destination's fixed keys|
|Error|`<Method>HTTPError` is a closed non-generic class inheriting `HTTPStatusError[<Method>ErrorData]`. Do not expose `<Method>HTTPError[E]`|

CLIENT's Literal overloads correlate finite concrete media with bodies/returns. Preserve existing rules for required media when multiple media exist, absent bodies, explicit None, and body-less success. To retain support for dynamic str and schema wildcard media, use the **same existing RequestCodecs facade** below. Do not create a new client class, config flag, parser, or media decoder.

|Type/factory|Fixed shape|
|---|---|
|`RequestMedia[SyncOutT, AsyncOutT]`|A frozen/slots nominal type in pkg.model_codecs, constructible only by factories. Both Ts are invariant and exclude Unset representing omission of the entire operation argument. Readonly `declared_media: str, concrete_media: str`; privately retains operation/use/direction/fingerprint. Users cannot supply arbitrary T through a public constructor|
|`ResponseMedia[R]`|Likewise factory-only and invariant. R is the union of most-specific branches actually reachable across all success statuses for concrete media within the declared media range. Include more-specific declarations/fallbacks and None for body-less success|
|`<Method>RequestCodecs.select_request_media(*, declared_media: Literal[declared_media], concrete_media: str)`|An overload for each declaration returns `RequestMedia[its sync OutboundType, its async OutboundType]`. Ordinary JSON has the same type on both axes. Do not mix sync and async BodyInput for binary/multipart|
|`select_response_media(*, declared_media: Literal[declared_media], concrete_media: str)`|An overload for each declaration returns `ResponseMedia[its success union]`. It belongs on the same RequestCodecs because it selects outgoing Accept/response_media_type|
|Method overload|Correlate the sync body with the selector's fixed sync type and the async body with its fixed async type. Body is required on selector-specified branches. Put the Unset branch for an optional body in a separate overload that forbids media selection. `response_media_type=ResponseMedia[R]` fixes the result to that R. Do not add a catchall overload inferring a free T from body|

Factories match declared exact/wildcard media to concrete media under existing media rules and bind owner/use. [CODECS §3](DECISIONS-MODEL-CODECS.md) is authoritative for this predicate; share CLIENT's existing dispatch plan instead of creating another selector evaluator. For requests, the winner key in existing most-specific dispatch for the concrete media must also equal declared_media. With `application/*: B` and `application/json: A`, choosing the wildcard while supplying application/json as concrete media produces CodecSelectionError; a B-typed value cannot reach A's decoder. Response R is the union of winners across every success status; do not force received status/media dispatch to one declaration key. This typing preserves [OAS precedence rules](https://spec.openapis.org/oas/v3.1.1.html#request-body-object).

There is no str fallback for unknown declarations. Generate no select_request_media when there are zero request-body media, and no select_response_media when there are zero success-body media. Preserve existing body() selection-error rules. A single overload branch becomes an ordinary typed method, not a lone @overload. Selectors from another operation, a different direction/use, or an old fingerprint produce CodecSelectionError before I/O. Existing codec/media processing validates bodies; merely creating a selector must not cast a body to T. Media selectors may exist for binary or schema-less media without fabricating model codecs. Private identity uses the top-level media/Accept plan; do not create a fake TypeUseId without a schema. `body(media_type=RequestMedia[...])` returns the corresponding codec only on branches where one exists; other branches retain existing selection errors. Selectors apply only to top-level body/Accept, leaving existing Literal/str → codec-union behavior of `part(name, media_type)` unchanged.

Type names alone cannot fully represent operation identity or valid wire values. Runtime ownership checks also reject misuse of different-operation selectors that share the same body type. Do not generate a nominal body model for every operation just to strengthen types.

## 5. Server services, authentication, and FastAPI settings

Native parameters use the SurfaceType fixed by FASTAPI; codec adapters use T/DecodedValue[T]; bodies/responses use final model types. Do not unwrap cookies to SurfaceType or change types to increase native coverage. Handler returns retain `P | HTTPResult[A] | Response`: P is the bare primary type, A is the actual payload union across all declared status/media combinations, and P=Never remains valid. Do not claim that one HTTPResult[A] statically proves every status/body correlation; preserve existing response-codec validation.

services.py declares one `<Group>Service` `typing.Protocol` per router group with one `@abstractmethod` per operation, named by the operation's fixed Python name, taking `self` and the operation's fixed keywords and returning the type above; async operations use `async def`. Only groups with an operation receiving a principal are generic, as `Protocol[PrincipalT_contra]` with one contravariant TypeVar; operations with an anonymous alternative accept `PrincipalT_contra | None`, while unauthenticated operations have no principal argument at all. Method names follow FASTAPI's unique non-keyword identifier naming rules: for example, the method is `get_pet`, while the OperationKey for operation_dependencies/install_openapi/selection remains `/paths/~1pets/get`. Builders take one keyword per group, typed with the group's Protocol; do not weaken this entry point to `Mapping[str, Callable]`, `object`, or a TypedDict of callables. Implementations may subclass a Protocol, which lets type checkers check each method at the class and makes Python refuse to instantiate a subclass while an abstract method is missing, or satisfy it structurally at the builder call. Retain startup checks for missing methods, callability, modes, and keywords, without treating those checks alone as proof of argument or return types.

Authentication records adopt the following types while retaining existing semantics. Do not copy/serialize/close CustomSecret values or change scheme selection, OR/AND, Basic's scope, or explicit-override rules.

|Type|Public type/correlation|
|---|---|
|`CustomSecret[SecretT_co]`|Frozen, readonly value: SecretT_co. Preserves the user's concrete opaque type|
|`Credential[SecretT_co]`|payload is ApiKeySecret / BasicSecret / BearerSecret / CustomSecret[SecretT_co]. scheme_name is the Literal union of selected SchemeKeys. Construct builtin-only values as Credential[Never]|
|`RequirementCandidate[SecretT_co]` / `AuthContext[SecretT_co]`|Preserve Credential's type through existing tuples/readonly Mappings. context.operation_key is OperationKey. Multiple custom-secret types use an explicit union; do not cast to one unproven type from a scheme name|
|`CredentialExtractor[SecretT_co]`|`__call__(request: Request, /) -> Credential[SecretT_co] \| None`. AsyncCredentialExtractor is async def with the same result type. CredentialExtractors[SecretT] is the generic alias `Mapping[SchemeKey, CredentialExtractor[SecretT] \| AsyncCredentialExtractor[SecretT]]`. Keys may be any selected subset, retaining original scheme names such as `my-api-key` as Literals. All values share one type, so a generic TypedDict with invalid identifiers is unnecessary|
|`Authorizer[SecretT_contra, PrincipalT_co]`|`__call__(context: AuthContext[SecretT_contra], /) -> PrincipalT_co`. AsyncAuthorizer is async def with the same result type. Rejection uses existing HTTPException. Preserve separate axes for AuthContext's opaque type and the principal type received by service methods|
|`OperationDependencies`|A **non-generic functional** TypedDict with all selected OperationKeys as NotRequired; values are Sequence[Dependency]. Pointer keys can be used unchanged. Dependency is the existing fastapi.params.Depends type alias|

For `build_router` / `create_app`, each group keyword is `<Group>Service[PrincipalT]` for a generic group and the plain Protocol otherwise; authorizer is `Authorizer[SecretT, PrincipalT] | AsyncAuthorizer[SecretT, PrincipalT]`, present and required only when a selected operation has security; credential_extractors is `CredentialExtractors[SecretT] | None` alongside it; and operation_dependencies is `OperationDependencies | None`. Preserve other existing arguments and returns. Statically reject incompatible principals between services and authorizers, and include a positive case for an authorizer correctly handling a SecretT union.

The generator cannot know the user's Principal type and never imports user modules, so it generates no conformance assignments. The standard README path subclasses `<Group>Service[Principal]` and passes the instances and a matching `Authorizer[Secret, Principal]` into the same builder, and statically checks this actual code. Structural implementations are checked where they are passed to a builder. Do not erase principal types to object to make conformance pass.

Use FASTAPI's fixed signature for `install_openapi` OperationKey Literals/empty-set Never and explicit-subset types. Typing must not replace public FastAPI orchestration, standard DI, or default_response_class precedence. Do not introduce new tasks/guards or force every response to JSON.

### 5.1 The closed FastAPIOptions set

Adopt `create_app(..., fastapi_options: FastAPIOptions | None = None)`. FastAPIOptions is a total=False TypedDict with only the keys below. None in a listed type is an explicit value, distinct from omission. Omitted keys use generated info defaults and the pinned FastAPI constructor's defaults; when default_response_class is omitted, omit the argument itself. Do not replace the source Default placeholder with an explicit JSONResponse value.

|Key|Permitted type|Basis when omitted|
|---|---|---|
|debug|bool|False|
|title / version|str|Source info; FastAPI / 0.1.0 if unset|
|summary|str \| None|Source info; None if unset|
|description|str|Source info; empty string if unset|
|openapi_url / docs_url / redoc_url|str \| None|/openapi.json / /docs / /redoc|
|swagger_ui_oauth2_redirect_url|str \| None|/docs/oauth2-redirect|
|openapi_tags / servers|list[dict[str, JSONValue]] \| None|Existing source metadata, or None|
|swagger_ui_init_oauth / swagger_ui_parameters|dict[str, JSONValue] \| None|None|
|terms_of_service|str \| None|Existing source info, or None|
|contact / license_info|dict[str, JSONValue] \| None|Existing source info, or None|
|openapi_external_docs|dict[str, JSONValue] \| None|None|
|responses|dict[int \| str, dict[str, JSONValue]] \| None|None. Only response descriptions/content/headers and similar data expressed as JSON|
|dependencies|Sequence[Dependency] \| None|None|
|middleware|Sequence[Middleware] \| None|None. Starlette public type|
|routes / callbacks|list[BaseRoute] \| None|None. Starlette public type|
|webhooks|APIRouter \| None|None|
|default_response_class|type[Response]|Omit the argument and preserve the ordinary FastAPI default|
|redirect_slashes / root_path_in_servers|bool|True / True|
|root_path|str|Empty string|
|deprecated|bool \| None|None|
|include_in_schema / separate_input_output_schemas / strict_content_type|bool|True / True / True|
|generate_unique_id_function|Callable[[APIRoute], str]|Delegate to FastAPI's default function when the key is omitted|

The table's JSON dicts are not re-exports of upstream Any dicts. Validate the JSON domain at entry, then copy fixed keys into the ordinary dicts/lists upstream requires. This convenience entry point's closed set excludes `responses[...]['model']` containing classes as types in schema/metadata, arbitrary extras, exception_handlers, lifespan, on_startup/on_shutdown, deprecated openapi_prefix, and additional keys specific to dependency versions. Users needing those settings construct ordinary `FastAPI(...)` and connect through public `build_router` / `install_openapi`. This does not mean those FastAPI features are unsupported; include an actual example of this route in the generated README. Check assignability against source types/defaults; adding a key requires the same change to this table, TypedDict, runtime validation, and README. [Pinned FastAPI constructor](https://github.com/fastapi/fastapi/blob/0.141.1/fastapi/applications.py).

## 6. Runtime, exceptions, and TokenVersion

Transport/body/provider/hook/signer/limiter/store callbacks use dedicated Protocols containing every argument, keyword, and return from RUNTIME/PROTOCOLS. Express required readonly attributes as properties; do not add generic callback bags such as `Callable[..., Any]` or `Mapping[str, object]`. Separate sync/async types without adaptations that automatically await an awaitable twice. Check ordinary defs returning ContextManagers separately from coroutines.

Multipart FieldPart[PartT_co] and FilePart[ContentT_co] are covariant frozen/readonly value records; MultipartBody[PartT] / AsyncMultipartBody[PartT] are invariant. FieldPart's readonly value is `PartT_co | Unset`, preserving existing explicit-UNSET omission of optional parts. Existing plans reject UNSET for required names in advance; UNSET never becomes a wire value. Bound FilePart's ContentT to the existing sync/async bytes/file/stream/factory union; do not add a separate AsyncFilePart class. Each multipart's parts is a tuple of FileParts for the corresponding sync/async binary union and FieldPart[PartT]. This permits constructing individual parts as narrowly typed variables and assembling them into a body with the declared part union. An operation's public PartT is the OutboundType union of named nonfile parts and nonfile parts permitted by explicit additionalProperties schemas. For untyped additionalProperties=true, include WireValue for extra values; use Never only for purely file-only cases with false and no other nonfile candidates. Additional binary/file parts use the existing FilePart branch; do not mix bytes into WireValue. This union alone does not prove every correlation between names and part types; retain existing part dispatch/validation.

`BodyInput[PartT] = bytes | FileBody | StreamBody | BodyFactory | MultipartBody[PartT]`; AsyncBodyInput has the same shape with existing async types. Do not change existing sources/ownership. Transport-side representations after existing model → wire encoding, and media representations returned by ClientMediaCodecAdapterV1.encode/encode_async, are BodyInput[WireValue] / AsyncBodyInput[WireValue]. Do not apply model encoding twice to those wire field values; perform only media conversion. This distinction does not add schema models or another encoder.

Transports without knowledge of model types handle bytes/PreparedRequest/TransportResponse; bound decoders return T. Typed entry points for private LogicalCall preserve correlations such as `execute(request: EncodedRequest, decoder: ResponseDecoder[T]) -> Response[T]`. Do not create functions returning arbitrary T solely from a str operation-registry lookup or merely attach `type[T]` to unvalidated wire values. Encoders with input T accept its fixed argument type; do not create TypedDict mirror models symmetric to decoding.

A generic exception's T does not automatically propagate from a calling method into `except`. `HTTPStatusError[E]`, `HookExecutionError[T]`, and payload-bearing exceptions in PROTOCOLS use `typing_extensions.TypeVar(..., covariant=True, default=object)` for output-only type parameters, exposing payloads as readonly properties. PollSnapshot[P_co] is also covariant as a frozen/readonly output record like Response[T_co], allowing it to be returned by failure/cancellation exceptions parameterized by poll type P. A bare catch's data is object; HookExecutionError.require_result is Response[object]. These are accurate types at a boundary where type information is lost, not Any. An existing typed reference retains T, and catching non-generic `<Method>HTTPError` retains concrete ErrorData. Do not restore payload types through `except Error[T]` or casts. Body-less fields/info in common catches always retain their fixed types. Users handling unresolved object values must make actual decisions, such as isinstance against a concrete class or an explicit codec.

Change BearerCredential.version from arbitrary object to the following public nominal type. The code is the adopted documented shape, not evidence of an added runtime implementation.

```python
from dataclasses import dataclass
from typing import final


@final
@dataclass(frozen=True, slots=True, eq=False)
class TokenVersion:
    pass
```

It has no fields or ordering and inherits object-identity eq/hash. An empty dataclass with eq=True is prohibited because all instances would compare equal. Do not add UUID/random/time/string values. A builtin provider creates an instance only when it definitively publishes initial/new material and shares that same instance across gets of unchanged material. Use `BearerCredential.version: TokenVersion` and `invalidate(version: TokenVersion)`, keeping provider ownership separately. Unknown/old/other-owner versions do not invalidate current material and follow existing no-op rules. Do not repurpose this type for token serialization, persistent revisions, CacheStore/QueueStore CAS versions, or comparisons across restarts/providers.

## 7. Protocol-helper type mapping

The following types make PROTOCOLS' existing feature/wire/resource contracts concrete without changing them. Ordinary sync uses Iterator/ContextManager; async uses AsyncIterator/AsyncContextManager. Public async class names are AsyncPager / AsyncLroHandle / AsyncEventStream / AsyncWebSocketSession / AsyncUploadHandle / AsyncBatchIterator; value records such as Page/PollSnapshot/Message are shared. Undeclared helpers/methods must have neither generated types nor runtime presence.

|Surface|Type correlation|
|---|---|
|Paging|Page[T, P] / Pager[T, P]. page.data is P; items is tuple[T, ...]. next_page(Page[T,P]) -> Page[T,P] \| None; iter_pages() -> Iterator[Page[T,P]]. Async counterparts must not return a bare Page either|
|LRO|LroHandle[T, P]. wait()->T; status()->PollSnapshot[P]; resume()->the same handle. Only generated helper-specific handles with a remote_cancel declaration have cancel_remote()->CancelReceipt[C], closing C over that cancellation response. Do not add a cancel method to an undeclared base|
|SSE/NDJSON|EventStream[T] yields StreamEvent[T]; data() returns Iterator[T]. Async data() is also an ordinary def returning AsyncIterator[T]. Include existing UnknownEvent in T's union only for unknown:raw. Error events are exceptions with corresponding payload types; do not mix them into ordinary events or Any|
|WebSocket|WebSocketSession[Send, Recv]. send(Send); receive()->Message[Recv]; iteration also yields Message[Recv]. Raw connectors use WSFrame/bytes. Do not merge send/recv into one T|
|Webhook/cache/upload|Make VerifiedWebhook[T].data, CacheResult[T].data, and UploadHandle[T].run() concrete. Only upload helpers with remote abort have abort_remote returning the declared operation's concrete response type; None when body-less|
|Batch input|SourceItem[InputT].value; BatchSource[InputT].open(cursor)->Iterator[SourceItem[InputT]]. Async uses an ordinary def returning AsyncIterator. Preserve the distinction between Iterable input and a resumable Source; a type name alone must not imply checkpoint capability|
|Batch output|BatchIterator[that helper's Result union]. Fix generated frozen variants `<Helper>Success` / `<Helper>Error` / `<Helper>DeliveryUnknown` and alias `<Helper>Result`. success/error/delivery_unknown are Literal outcomes, allowing successful values/error payloads/unknown delivery to be tracked after branching. Types/schemas belong only to that helper. Preserve existing fields and semantics such as response/id/retry token|
|Queue|enqueue preserves per-alias operation arguments. inspect(id) returns QueueEntry \| None; DrainReport/QueueOutcome remain existing finite records. Do not recover arbitrary model T from entry ID alone; stored payloads remain the established bytes|
|Store/connection/coding|Retain existing public records and concrete Protocols. Property variance alone must not cause copying/closing borrowed objects. Do not change persistent-CAS str versions to TokenVersion|

Even when generating result/tag-record variants, schema models reference D types. Do not add a custom model compiler or new protocol interpretation. A Literal state alone does not establish value/error correlations; do not claim branches automatically narrow `value: T | None, error: E | None` within the same class.

## 8. Exports, reflection, exceptional boundaries, and custom templates

Every new package includes py.typed and aligns public inline annotations, necessary generated .pyi files, `__all__`/lazy exports, and implementation signatures. Fully type private helpers too. Do not hide handwritten handler.py errors behind adjacent .pyi files; check actual handwritten samples through assignment to independent signature Protocols. Custom templates use the same context types/APIs/exports; do not treat a weakly typed counterpart as equivalent to a builtin unless the user explicitly declares an exception. [Distribution specification](https://typing.python.org/en/latest/spec/distributing.html).

Beyond import smoke tests, resolve public annotations referenced through private import aliases from another module using `get_type_hints(..., include_extras=True)`. New recursive JSON aliases use TypeAliasType. For TypedDict under `from __future__ import annotations`, caches such as `__required_keys__` are not guaranteed correct. Runtime presence decisions use the fixed plan or evaluated include_extras, never raw annotation strings/required_keys alone. Validate both FastAPI backends through actual route registration, OpenAPI, and HTTP input/output as well. Do not eval schema strings or substitute generation-time user-model imports for this validation. [Annotation specification](https://typing.python.org/en/latest/spec/annotations.html), [TypedDict specification](https://typing.python.org/en/latest/spec/typeddict.html).

Record Any/object/cast/ignore exceptions in the new target's validation ledger with symbol, file/line, origin, permitted scope, narrowing/validation location, and positive/negative tests. Permitted cases are limited to: (1) legitimate unspecified model annotations in existing D; (2) entry boundaries that contain parser/dynamic-import/untyped-upstream returns as object; (3) generic-exception catches or user-specified unknown opaque values; and (4) local defects in upstream type stubs. Preserve user-specified opaque values through Generics first, without collapsing them to object merely for convenience. Distinguish a model's final type legitimately being Any under its settings from schema-less JSON WireValue and binding failures. It is prohibited to change an entire model to object and abandon field access, blanket-ignore all target modules, or exclude tests to report zero errors.

Allow casts only at limited locations where evidence/validation establishes the same value's actual type, such as insufficient expressiveness in upstream stubs. Do not use them to prove wire → model, signature verification → schema type, dynamic name → arbitrary T, or arbitrary callable → correct return type. `# type: ignore[specific-code]` follows the same conditions, with unused-ignore detection. When using an existing D Any field, explicitly record object containment/actual validation at entry or the existing type boundary; do not evade it with another TypedDict mirror or model regeneration.

Static checking covers new targets/private runtimes/public extensions and typed samples. Do not migrate all existing D models to the new strict policy. Even when isolating existing model-module diagnostics, preserve valid field types/constructors/aliases rather than using follow-imports skip or blanket stubs that turn every imported type into Any. For user settings/custom types where an existing D type itself does not resolve at runtime, state its origin and the required type/adapter responsibilities; do not hide target annotations behind Any and claim support.

CODECS' RuntimeModelBindingView.native_type and native_field_types are **reflection entry points for type expressions**, registered in this exception ledger. Because they carry generics/unions/aliases as well as classes, do not narrow them to `type[T]` or disguise heterogeneous fields as one T. Preserve existing `object` / `Mapping[str, object]` and make validated resolution sources and field_id traceable. TypeForm[object] expresses this use at the language level but is outside the selected initial contract. Any future adoption requires positive union/TypeAliasType cases and negative invalid-expression cases on both supported checkers before changing this boundary. Do not merely change annotations or call arbitrary values as type expressions. Existing ModelCodec[T] separately preserves model-value input/output T. [Type forms specification](https://typing.python.org/en/latest/spec/type-forms.html).

## 9. Acceptance and performance

Check the new scope with both mypy strict and Pyright strict. Save exact versions/settings/target files/hashes for each run. Use a dedicated checking environment pinned to mypy 2.3.1 for the adopted type features, and Pyright 1.1.411 as the initial baseline; bundle neither into product runtimes. Type-checker update differences must not justify legacy D golden changes. Positive cases require zero diagnostics; for negative cases, verify expected codes/counts on each affected line, rather than treating a nonzero exit alone as proof that every negative case passed.

|ID|Fixed check/expected result|
|---|---|
|TY01|For minimal and nullable/union/alias/custom-field fixtures across all five client and two server backends, model bytes equal ordinary D with the same config/api scope. Zero changes to old CLI/API/model-only dependency/import baselines|
|TY02|Applicable-package parse/import/hints on Python 3.10/3.11/3.12/3.13/3.14. Explicitly state existing upper/lower limits per capability, such as WebSocket. New type syntax or unavailable stdlib imports must not break 3.10|
|TY03|Successful hints for recursive JSONValue/WireValue through another module/private alias. object() and non-str-key dicts are static negative cases; finite numbers/size/depth/duplicate keys retain existing runtime oracles. Fixed presence decisions remain correct even for the future-TypedDict required_keys counterexample|
|TY04|Positive/negative cases for method required/nullable/Unset rules, parameter names, Enum/Literal, and media/body/return correlations. Catchalls must not accept incorrect Literal/body pairs. response_media_type must not remove None branches|
|TY05|Positive/negative cases for wildcard/explicit custom-media selectors, most-specific overlap, and sync/async binary distinctions. Wrong-owner/use/stale selectors produce selection errors with zero I/O. Media selectors without codecs succeed; zero fabricated body codecs. A positive case assembles narrow FieldPart/FilePart variables into multipart with the declared union; mixing async files into sync bodies is a negative case|
|TY06|Codec invariance, Native/Envelope from_wire/snapshot types, dynamic-factory codec unions, every exact/range/default fallback type, and header-name returns. The validation ledger rejects implementations casting JSON to models|
|TY07|Unpack unknown keys/missing required keys/None misuse/options overlap are negative cases; known-kwargs forwarding is positive. Where transparent wrappers exist, ParamSpec preserves keyword-only parameters/returns; zero unnecessary wrappers added|
|TY08|Service Protocols' method names and operation signatures, sync/async, group keywords, and authorizer SecretT/PrincipalT correlations. Incorrect principals/extractor results/missing or mismatched methods/unknown group keywords are negative cases; a subclass missing an abstract method also fails to instantiate. Do not confuse method names with OperationKey pointers. Check real implementation modules, subclassed and structural, rather than hiding behind .pyi|
|TY09|All FastAPIOptions key types and assignability to pinned source signatures/defaults. Unknown keys/JSON containing classes/incorrect response classes are negative cases. Ordinary custom-app exception-handler/lifespan paths are positive cases. get_type_hints/routes/OpenAPI/HTTP work for both server backends|
|TY10|Ordinary await happens once; async streaming/source.open and similar APIs directly use async contexts/iterators. A sync adapter in an async slot is a static negative case or existing startup error; zero hidden double awaits|
|TY11|Positive/negative cases for Page T/P, LRO T/P/C, WS send/recv, unknown events, batch success/error, and finite queue records. Undeclared cancel/abort methods are not generated. Typed-helper checkpoints must not change into pickle/model impersonation|
|TY12|Generic-base catches yield object / Response[object], never Any/Unknown; concrete MethodHTTPError retains fixed ErrorData. Reparameterization, except Error[T], and model use of unnarrowed payloads are negative cases|
|TY13|Two TokenVersion instances compare unequal; the same instance is retained; eq=False/frozen/slots; zero invalidation from other-owner/old versions. Zero effects on runtime persistence/CAS versions. Passing strings or arbitrary objects to invalidate is a static negative case|
|TY14|Matching py.typed/exports/stubs/source signatures, unused-ignore detection, custom-template positive cases and deliberate incorrect-annotation negative cases. Audit bare Generic/Callable/Any leaks in the new scope with the ledger|
|TY15|Preserve legitimate existing D Any fixtures with unchanged bytes and exact surrounding known argument/return types. Reject wholesale type-information skipping, mirror models, and golden changes|
|TY16|With identical Python/environment/inputs, separately record old D generation time/import time/peak memory and new-target generation/generated-package import/HTTP and codec hot paths. Resolve type hints during setup, without per-request reflection/new validation traversals/new tasks. Do not claim performance gains or zero cost without measurement|

Module definitions of new typed aliases/Protocols/overloads/TypedDicts incur import/generated-size costs. Per-operation annotations/overloads scale with declared combinations; do not import optional libraries for unused helper types. Use runtime get_type_hints results during setup of trusted generated modules, never evaluate them per request. Typing must not duplicate existing codec validation or add new server branches to existing D hot paths. Observe ordinary client/server runtime performance through TY16 after implementation; neither this document nor small type probes prove throughput.

## 10. Sources and current evidence scope

Official typing specifications, Python documentation, and typing_extensions were consulted on 2026-09-18 JST. Distinguish versioned Python 3.10/3.14 documentation from live typing/backport documentation. Do not infer a typing_extensions release from its page title. mypy 2.3.1 is the checking baseline identified through [official release notes](https://mypy.readthedocs.io/en/stable/changelog.html), not a claim of installation or full-product validation here.

Run the cross-module alias, generic exception, Unpack/covariance, and other language-boundary cases with the declared Python, typing_extensions, Pyright, and mypy versions. Generated-server/client typing, all supported backends, and TY01–TY16 remain unexecuted acceptance requirements.
