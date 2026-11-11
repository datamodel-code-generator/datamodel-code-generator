"""The client target: plan, bind, and render one client package behind the single-target coordinator."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast
from warnings import warn

from typing_extensions import TypeIs

from datamodel_code_generator._api_generation import TargetRender
from datamodel_code_generator._api_types import APIGenerationError, Diagnostic, NextSteps, error_text
from datamodel_code_generator._client.caching import plan_caches
from datamodel_code_generator._client.channels import channel_protocols, directed
from datamodel_code_generator._client.codec_plan import plan_client_codecs
from datamodel_code_generator._client.config import OPTION_PREFIX, ClientGenerationConfig, _is_sequence
from datamodel_code_generator._client.fields import plan_fields
from datamodel_code_generator._client.model_facts import ModelFacts
from datamodel_code_generator._client.pagination import plan_pagination
from datamodel_code_generator._client.plan import (
    Planner,
    encoding_header_uses,
    form_uses,
    part_uses,
    plan_uses,
    style_uses,
)
from datamodel_code_generator._client.polling import plan_polling
from datamodel_code_generator._client.protocol_plan import (
    helper_calls,
    helper_metadata,
    helper_operations,
    helper_problems,
    plan_protocols,
)
from datamodel_code_generator._client.render import ClientRenderer
from datamodel_code_generator._client.sockets import DEPENDENCY as WEBSOCKETS
from datamodel_code_generator._client.sockets import plan_sockets, routed_streams, socket_uses
from datamodel_code_generator._client.streams import plan_streams, stream_uses
from datamodel_code_generator._client.templates import ClientTemplates
from datamodel_code_generator._client.uploads import plan_uploads
from datamodel_code_generator._client.webhooks import (
    key_class,
    plan_webhooks,
    webhook_dependencies,
    webhook_files,
    webhook_uses,
)
from datamodel_code_generator._graph import reach
from datamodel_code_generator._openapi_features import feature_label
from datamodel_code_generator._openapi_wire_plan import operation_uses, plan_wire
from datamodel_code_generator._python_syntax import subscript_text
from datamodel_code_generator._target_contract import (
    GeneratedEnumMember,
    GeneratedSymbolType,
    GenericType,
    UnionType,
    label,
    request_media,
)
from datamodel_code_generator._target_documents import canonical_bytes, sha256, shown
from datamodel_code_generator._target_module import TargetModule, TypeNames
from datamodel_code_generator._target_operations import PlanError, raise_selected
from datamodel_code_generator._target_render import (
    PYDANTIC_BACKENDS,
    TYPING_EXTENSIONS,
    model_dependencies,
    pydantic_dependency,
)
from datamodel_code_generator.enums import DataModelType
from datamodel_code_generator.python_literal import stable_repr

if TYPE_CHECKING:
    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_types import TargetKind
    from datamodel_code_generator._client.caching import CacheSpec
    from datamodel_code_generator._client.codec_plan import CodecBackend
    from datamodel_code_generator._client.pagination import PaginationSpec
    from datamodel_code_generator._client.plan import ClientPlan, OperationSpec
    from datamodel_code_generator._client.protocol_plan import Protocols
    from datamodel_code_generator._client.protocols import Helper
    from datamodel_code_generator._client.sockets import MessageRoute, SocketSpec
    from datamodel_code_generator._client.streams import StreamSpec
    from datamodel_code_generator._client.webhooks import WebhookSpec
    from datamodel_code_generator._openapi_wire_plan import WirePlan
    from datamodel_code_generator._runtime.model_codecs.media import JSONValue
    from datamodel_code_generator._target_contract import (
        ChannelContract,
        GeneratedTypeContractBatch,
        ModelFieldFacts,
        OperationContract,
        OperationId,
        SymbolId,
        TypeUseBinding,
        TypeView,
    )
    from datamodel_code_generator._target_documents import JSONObject

HTTPX2: Final = "httpx2>=2.13.0"
MSGSPEC: Final = "msgspec>=0.22.0"
_BACKENDS: Final[dict[DataModelType, CodecBackend]] = {
    DataModelType.PydanticV2BaseModel: "pydantic_v2.BaseModel",
    DataModelType.PydanticV2Dataclass: "pydantic_v2.dataclass",
    DataModelType.DataclassesDataclass: "dataclasses.dataclass",
    DataModelType.TypingTypedDict: "typing.TypedDict",
    DataModelType.MsgspecStruct: "msgspec.Struct",
}


class ClientTarget:
    """Render an HTTPX2 client package for every model backend."""

    kind: TargetKind = "client"
    backends: frozenset[DataModelType] = frozenset(_BACKENDS)
    unsupported_backend: str = "E_CONFIG_VALUE"
    selector: str = "--generate-client"

    def __init__(self, protocol_base: Path) -> None:
        """Resolve the documents that the helper JSON object names against protocol_base."""
        self.protocol_base = protocol_base

    def render(self, request: TargetRequest) -> TargetRender:
        """Plan the selected operations, bind their codecs, and render the package.

        An operation that only its own declarations refuse, and that no enabled helper calls, is left out with a warning
        that names the OpenAPI features it uses that the client does not support, and the others are planned again.
        When each of its findings lies in a media of its request body and another media remains, only those media are
        left out instead, with a warning each, and the operation is planned again with the others (`_kept`); the
        operation skipped later reports their findings with its own. A refusal stays an error, with the findings of the
        operations skipped before it, when anything else is refused too, or nothing is left. The channels of an
        AsyncAPI input are opened as WebSocket and SSE helpers instead (`channel_protocols`), and a channel is left out
        the same way, also for what its document declares, and for what its client would send on a server-sent event
        stream (`directed`); when every channel is left out so, their findings are the error.
        """
        config = request.config
        assert isinstance(config, ClientGenerationConfig)
        skipped: list[tuple[str, list[Diagnostic]]] = []
        left_out: dict[OperationId, list[tuple[str, str, list[Diagnostic]]]] = {}
        protocols: Protocols | None = None
        opened: dict[OperationId, ChannelContract] = {}
        if (channels := request.batch.channels) is None:
            protocols = plan_protocols(request, config.protocols, self.protocol_base)
            called = helper_calls(protocols)
            subjects: dict[OperationId, str] = {}
        else:
            channels = directed(channels)
            skipped.extend((_channel(channel), _skips(channel)) for channel in channels if channel.skips)
            opened = {
                channel.handshake.id: channel
                for channel in channels
                if not channel.skips and channel.handshake is not None
            }
            if not opened and skipped:
                raise APIGenerationError(
                    tuple(item for _, found in skipped for item in found), option_prefix=OPTION_PREFIX
                )
            subjects = {identity: _channel(channel) for identity, channel in opened.items()}
            request = replace(request, operations=tuple(channel.handshake for channel in opened.values()))
            called = frozenset()
        selected = request.operations
        while isinstance(
            rendered := _attempt(
                request,
                config,
                protocols
                if channels is None
                else channel_protocols(request, config, tuple(opened[item.id] for item in request.operations)),
                selected,
            ),
            APIGenerationError,
        ):
            refused = _refused(rendered, request.operations, called)
            kept = {} if refused is None else {key: _kept(*value) for key, value in refused.items()}
            if refused is None or (len(refused) == len(request.operations) and not any(kept.values())):
                diagnostics = (
                    *(item for _, found in skipped for item in found),
                    *(item for media in left_out.values() for _, _, found in media for item in found),
                    *rendered.diagnostics,
                )
                raise APIGenerationError(diagnostics, option_prefix=rendered.option_prefix) from None
            for identity, (operation, found) in refused.items():
                subject = subjects.get(identity) or _operation(operation)
                if (narrowed := kept[identity]) is None:
                    earlier = [item for _, _, media_found in left_out.pop(identity, ()) for item in media_found]
                    skipped.append((subject, [*earlier, *found]))
                else:
                    left_out.setdefault(identity, []).extend((subject, *media) for media in narrowed[1])
            request = replace(request, operations=_narrowed(request.operations, kept))
        _warn(shown(config.output, request.shown_root).as_posix(), skipped, left_out, asyncapi=channels is not None)
        return rendered


def _narrowed(
    operations: tuple[OperationContract, ...],
    kept: Mapping[OperationId, tuple[OperationContract, list[tuple[str, list[Diagnostic]]]] | None],
) -> tuple[OperationContract, ...]:
    """Return the operations without those skipped, each refused one that keeps media in the form `_kept` gives."""
    narrowed: list[OperationContract] = []
    for operation in operations:
        if operation.id not in kept:
            narrowed.append(operation)
        elif (found := kept[operation.id]) is not None:
            narrowed.append(found[0])
    return tuple(narrowed)


def _warn(
    output: str,
    skipped: list[tuple[str, list[Diagnostic]]],
    left_out: Mapping[OperationId, list[tuple[str, str, list[Diagnostic]]]],
    *,
    asyncapi: bool,
) -> None:
    """Warn of each operation or channel skipped, then of each request body media left out, with its findings."""
    for subject, found in skipped:
        warn(f"{output}: Skipping {subject}, {_why(found, asyncapi=asyncapi)}: {error_text(found)}", stacklevel=3)
    for subject, name, found in (media for operation in left_out.values() for media in operation):
        why = _why(found, asyncapi=asyncapi)
        warn(f"{output}: Leaving out the {name} media of {subject}, {why}: {error_text(found)}", stacklevel=3)


def _why(found: list[Diagnostic], *, asyncapi: bool) -> str:
    """Return why a warning leaves something out: the features its findings use that the client does not support."""
    features = ", ".join(
        dict.fromkeys(feature_label(item.feature, asyncapi=asyncapi) for item in found if item.feature)
    )
    return f"which uses {features} that the client does not support" if features else "which the client cannot generate"


def _kept(
    operation: OperationContract, found: list[Diagnostic]
) -> tuple[OperationContract, list[tuple[str, list[Diagnostic]]]] | None:
    """Return an operation without the request body media its findings all lie in, with each one's findings.

    A finding lies in a media when it refuses that media's type use, or is located within its declaration. None when
    a finding lies in no media of the request body, or no media would remain.
    """
    media = request_media(operation)
    names = {child.name for child in media}
    by_name: dict[str, list[Diagnostic]] = {}
    for item in found:
        pointer = item.source_pointer or ""
        name = item.media or next(
            (
                child.name
                for child in media
                if pointer == (site := child.use_site.pointer) or pointer.startswith(f"{site}/")
            ),
            None,
        )
        if name is None or name not in names:
            return None
        by_name.setdefault(name, []).append(item)
    remaining = tuple(child for child in media if child.name not in by_name)
    if not remaining:
        return None
    assert operation.request_body is not None
    return replace(operation, request_body=replace(operation.request_body, children=remaining)), list(by_name.items())


def _operation(operation: OperationContract) -> str:
    """Return how a skip warning names an operation: its method and path, and its operationId."""
    named = "" if operation.operation_id is None else f" (operationId {stable_repr(operation.operation_id)})"
    return f"{label(operation)}{named}"


def _channel(channel: ChannelContract) -> str:
    """Return how a skip warning names an AsyncAPI channel: by its id or address."""
    return f"channel {stable_repr(channel.name)}"


def _skips(channel: ChannelContract) -> list[Diagnostic]:
    """Return the findings of what an AsyncAPI channel declares that the client cannot open."""
    return [
        Diagnostic(
            code="E_CLIENT_UNSUPPORTED",
            severity="error",
            stage="target",
            message=skip.message,
            source_pointer=channel.use_site.pointer,
            feature=skip.feature,
        )
        for skip in channel.skips
    ]


def _attempt(
    request: TargetRequest,
    config: ClientGenerationConfig,
    protocols: Protocols | None,
    selected: tuple[OperationContract, ...],
) -> TargetRender | APIGenerationError:
    """Render the request's operations, or return the error that refuses them.

    `selected` are the operations selected before any was skipped.
    """
    try:
        return _render(request, config, protocols, selected)
    except APIGenerationError as error:
        return error


def _render(  # ruff: ignore[too-many-locals]
    request: TargetRequest,
    config: ClientGenerationConfig,
    protocols: Protocols | None,
    selected: tuple[OperationContract, ...],
) -> TargetRender:
    """Plan the request's operations, bind their codecs, and render the package."""
    backend = _BACKENDS[request.model_config.output_model_type]
    wire = _wire(request, request.batch, brackets=config.deep_object_brackets)
    facts = ModelFacts(wire.kinds.tables)
    helping = helper_operations(protocols)
    try:
        plan = Planner(request, config, wire, facts, helping, selected=selected).plan()
    except PlanError as error:
        raise APIGenerationError(error.diagnostics, option_prefix=OPTION_PREFIX) from None
    events, hooked = webhook_uses(protocols, plan, request)
    received = frozenset(event.use.id for spec in events for event in spec.events)
    streamed, stream_events, stream_problems = stream_uses(protocols, plan, request)
    if request.batch.channels is not None:
        streamed, stream_events = routed_streams(streamed, protocols, plan, request, facts, backend, stream_problems)
    opened, messages, socket_problems = socket_uses(protocols, plan, request, facts, backend)
    uses = frozenset(plan_uses(plan)) | received | frozenset(use.id for use in (*stream_events, *messages))
    batch = request.batch
    if parts := (*part_uses(plan), *stream_events, *messages):
        batch = replace(batch, type_uses=(*batch.type_uses, *parts))
    types = TypeNames.of(batch, request.model_config)
    codecs = plan_client_codecs(batch, wire, backend, uses, facts, names=types)
    raise_selected(codecs.diagnostics, (spec.contract.id for spec in plan.operations))
    coded = frozenset(item.use for item in codecs.uses)
    assert batch.names is not None
    plan, named = plan_fields(plan, facts, coded, batch.names, helping)
    pages, checked = plan_pagination(protocols, plan, facts, coded, request)
    polls, polled = plan_polling(protocols, plan, facts, coded, request)
    caches, cached = plan_caches(protocols, plan, facts, coded, request)
    uploads, uploaded = plan_uploads(protocols, plan, facts, coded, request)
    order = {} if protocols is None else {helper.name: index for index, helper in enumerate(protocols.helpers)}
    helpers = tuple(sorted((*pages, *polls, *caches, *uploads), key=lambda spec: order[spec.helper.name]))
    streams = plan_streams(streamed, protocols, plan, facts, coded, request, stream_problems)
    sockets = plan_sockets(opened, socket_problems)
    webhooks = plan_webhooks(events, codecs, hooked)
    if refused := (
        *named,
        *helper_problems(
            protocols,
            plan,
            {
                **checked,
                **polled,
                **cached,
                **uploaded,
                **hooked,
                **stream_problems,
                **socket_problems,
            },
        ),
    ):
        raise APIGenerationError(refused, option_prefix=OPTION_PREFIX)
    data = _HelperDigests(request, types, facts)
    metadata = helper_metadata(protocols, request)
    fingerprints = {spec.helper.name: data.fingerprint(spec, metadata[spec.helper.name]) for spec in pages}
    fingerprints.update((spec.helper.name, data.cache(spec, metadata[spec.helper.name])) for spec in caches)
    fingerprints.update((spec.helper.name, data.webhook(spec, metadata[spec.helper.name])) for spec in webhooks)
    fingerprints.update((spec.helper.name, data.socket(spec, metadata[spec.helper.name])) for spec in sockets)
    dependencies = (
        WEBSOCKETS if sockets else HTTPX2,
        TYPING_EXTENSIONS,
        *_backend_dependencies(backend, request.model_imports),
        *webhook_dependencies(webhooks),
        *model_dependencies(request.model_imports),
    )
    renderer = ClientRenderer(
        config=config,
        plan=plan if request.batch.channels is None else replace(plan, resources=()),
        batch=batch,
        wire=wire,
        codecs=codecs,
        helpers=helpers,
        streams=streams,
        sockets=sockets,
        fingerprints=fingerprints,
        webhooks=partial(webhook_files, webhooks, types),
        signatures=frozenset(spec.helper.tree["signature"]["kind"] for spec in webhooks),
        backend=backend,
        types=types,
        dependencies=dependencies,
        templates=ClientTemplates.custom(request.model_config, request.cwd),
        use_schema_description=request.model_config.use_schema_description,
        use_single_line_docstring=request.model_config.use_single_line_docstring,
    )
    return TargetRender(
        files=renderer.files(), dependencies=dependencies, next_steps=_next_steps(config, plan, sockets, streams)
    )


def _refused(
    error: APIGenerationError, operations: tuple[OperationContract, ...], called: frozenset[OperationId]
) -> dict[OperationId, tuple[OperationContract, list[Diagnostic]]] | None:
    """Return each operation an error refuses with its findings, or None when a finding refuses more than one operation.

    A finding refuses one operation alone when that operation's own declarations cause it: no setting does, and no
    enabled helper calls the operation.
    """
    by_pointer = {operation.id.use_site.pointer: operation for operation in operations}
    refused: dict[OperationId, tuple[OperationContract, list[Diagnostic]]] = {}
    for item in error.diagnostics:
        if item.operation is None or item.option_path is not None:
            return None
        operation = by_pointer[item.operation.pointer]
        if operation.id in called:
            return None
        refused.setdefault(operation.id, (operation, []))[1].append(item)
    return refused


def _next_steps(
    config: ClientGenerationConfig, plan: ClientPlan, sockets: tuple[SocketSpec, ...], streams: tuple[StreamSpec, ...]
) -> NextSteps:
    """Return the README and code that calls the first operation in a client's context.

    The client of an AsyncAPI input opens its first channel instead: a WebSocket's session, or an event stream.
    """
    if (spec := next(iter(plan.operations), None)) is None:
        call = "..."
    elif spec.contract.id.kind == "channel":
        name, opened = next(
            (item.helper.name, "connect(...) as session")
            if item.helper.kind == "websocket"
            else (item.helper.name, "open(...) as stream")
            for item in (*sockets, *streams)
            if item.operation.contract.id == spec.contract.id
        )
        call = f"with client.{name}.{opened}:\n        ..."
    else:
        call = f"client.{spec.resource}.{spec.name}(...)"
    return NextSteps(
        readme=Path("README.md"),
        usage=f"from {config.package} import Client\n\nwith Client(base_url=...) as client:\n    {call}",
    )


def _backend_dependencies(backend: CodecBackend, model_imports: frozenset[str]) -> tuple[str, ...]:
    if backend in PYDANTIC_BACKENDS:
        return (pydantic_dependency(model_imports),)
    return (MSGSPEC,) if backend == "msgspec.Struct" else ()


def _wire(request: TargetRequest, batch: GeneratedTypeContractBatch, *, brackets: bool) -> WirePlan:
    """Plan the parameters and headers of the selected operations, with the member plans of their forms.

    The operations are planned as the request holds them, without the request body media left out. With `brackets`, a
    deepObject query parameter or URL-encoded form member is written in the bracket form.
    """
    selected = {operation.id: operation for operation in request.operations}
    return plan_wire(
        replace(batch, operations=tuple(selected.get(operation.id, operation) for operation in batch.operations)),
        [
            *(use for operation in request.operations for use in operation_uses(operation)),
            *encoding_header_uses(request),
        ],
        operations=frozenset(operation.id for operation in request.operations),
        forms=dict(form_uses(request)),
        styles=dict(style_uses(request)),
        writes=True,
        brackets=brackets,
    )


class _HelperDigests:
    """Digest each rendered helper's contract closure."""

    def __init__(self, request: TargetRequest, types: TypeNames, facts: ModelFacts) -> None:
        """Spell types by the full paths of their names."""
        self.request = request
        self.facts = facts
        self.spelling = TargetModule(types, qualified=True)

    def fingerprint(self, spec: PaginationSpec, settings: JSONValue) -> str:
        """Return the digest of a helper's contract closure: its signature and settings, operation, schema, and page.

        The settings are the helper's normalized metadata, so equivalent spellings of its references digest alike.
        """
        signature = {
            **self.call(spec.helper, spec.operation, settings),
            "body": self.body(spec.operation),
            "item": self.spelled(spec.item),
            "page": self.type(spec.page),
        }
        return self.digest(spec.helper.kind, signature, spec.operation, [spec.item_schema], [self.contract(spec.page)])

    def cache(self, spec: CacheSpec, settings: JSONValue) -> str:
        """Return the digest of a cache helper's contract closure: its fetch signature and operation.

        The fetch's signature carries the settings, and the type use of its cacheable response closes the contract.
        """
        operation = spec.operation
        signature = {
            **self.call(spec.helper, operation, settings),
            "body": self.body(operation),
            "responses": [
                (item.status, [self.type(media.use) for media in item.media]) for item in operation.responses
            ],
        }
        return self.digest("cache", signature, operation, [], [self.contract(spec.response)])

    def webhook(self, spec: WebhookSpec, settings: JSONValue) -> str:
        """Return the digest of a webhook helper's contract closure: its signature, settings, schemas, and event uses.

        A mapped helper digests the type and decoding mode of each event name.
        """
        events = spec.events
        mapped = events[0].name is not None
        signature = {
            "name": spec.helper.name,
            "event": {event.name: self.type(event.use) for event in events} if mapped else self.type(events[0].use),
            "key": key_class(spec.helper.tree["signature"]["kind"]),
            "settings": settings,
        }
        schemas, uses = [event.schema for event in events], [self.contract(event.use) for event in events]
        return self.digest("webhook", signature, None, schemas, uses)

    def socket(self, spec: SocketSpec, settings: JSONValue) -> str:
        """Return the digest of a WebSocket helper's contract closure: its signature, settings, operation, and schemas.

        Each message use contributes its type and contract, so a changed schema changes the digest; a direction of
        several messages contributes each one's codec, type and tag, and the class it sends.
        """
        signature = {
            **self.call(spec.helper, spec.operation, settings),
            "send": self.messages(spec.sends),
            "receive": self.messages(spec.receives),
            **({} if spec.discriminator is None else {"discriminator": spec.discriminator}),
        }
        uses = [self.contract(use) for use in spec.uses]
        return self.digest(spec.helper.kind, signature, spec.operation, list(spec.schemas), uses)

    def messages(self, routes: tuple[MessageRoute, ...]) -> object:
        """Return the signature of a WebSocket direction: its one message's type, else each message's."""
        if len(routes) < 2:  # noqa: PLR2004
            return None if not routes else self.type(routes[0].use)
        return [
            [
                route.codec,
                self.type(route.use),
                route.tag,
                route.key,
                None if route.type is None else self.spelled(route.type),
            ]
            for route in routes
        ]

    def call(self, helper: Helper, operation: OperationSpec, settings: JSONValue) -> dict[str, object]:
        """Return the name, parameters, and settings of a helper's signature for the operation it calls."""
        return {
            "name": helper.name,
            "parameters": [(item.python_name, item.required, self.type(item.use)) for item in operation.parameters],
            "settings": settings,
        }

    def body(self, operation: OperationSpec) -> object:
        """Return the requiredness and the typed media of an operation's request body, or None without one."""
        if (body := operation.body) is None:
            return None
        return body.required, [(item.media_type, self.type(item.use)) for item in body.media]

    def digest(
        self,
        kind: str,
        signature: dict[str, object],
        operation: OperationSpec | None,
        schemas: list[JSONObject],
        uses: list[object],
    ) -> str:
        """Return the digest of a helper's contract closure from its one signature, operation, schemas, and uses."""
        operations = [] if operation is None else [self.request.documents.operation(operation.contract.id)]
        closure = {
            "kind": kind,
            "signatures": [signature],
            "operations": operations,
            "schemas": schemas,
            "type_uses": uses,
        }
        return sha256(canonical_bytes(_projection(closure)))

    def spelled(self, value: TypeView) -> str:
        """Return a type's canonical spelling: its containers and unions by structure, its leaves as models spell them.

        The model options that only style annotations, such as the union operator, leave the spelling unchanged, so a
        regenerated package with the same contract keeps its fingerprints.
        """
        match value:
            case GenericType():
                arguments = [self.spelled(item) for item in value.arguments]
                if arguments and value.tuple_form == "variadic":
                    arguments.append("...")
                base = self.spelled(value.base)
                return subscript_text(base, arguments) if arguments or value.tuple_form == "fixed" else base
            case UnionType():
                return " | ".join(self.spelled(member) for member in value.members)
            case _:
                pass
        return self.spelling.hint(value)

    def type(self, use: TypeUseBinding | None) -> str | None:
        """Return a use's final type spelled with the import locations of its names, or None without a schema."""
        return None if use is None or use.type is None else self.spelled(use.type)

    def contract(self, use: TypeUseBinding) -> object:
        """Return a retained helper use's contract: its type, and the values or fields of every model type it reaches.

        The models are those its type names, then those their fields' types name, through recursion. A field gives its
        wire name, member kind, exclusion, requiredness, nullability, direction, type and emitted default, so a schema
        change that changes any reached model changes the digest.
        """
        models: dict[str, object] = {}

        def visit(symbol: SymbolId) -> list[SymbolId]:
            found, members = self.facts.symbols[symbol], self.facts.members.get(symbol, ())
            models[self.spelling.symbol(symbol)] = {
                "kind": found.kind,
                "values": [None if value is None else stable_repr(value.value) for value in found.values],
                "fields": [
                    [member.wire_name, member.member_kind, member.exclusion, *self.field(member.model_facts)]
                    for member in members
                ],
            }
            return [named for member in members if member.model_facts for named in _named(member.model_facts.type)]

        reach(() if use.type is None else _named(use.type), visit, key=self.spelling.symbol)
        return {"type": self.type(use), "models": models}

    def field(self, facts: ModelFieldFacts | None) -> tuple[object, ...]:
        """Return a model field's contract facts: requiredness, nullability, direction, type and default."""
        return (
            ()
            if facts is None
            else (
                facts.required,
                facts.nullable,
                facts.read_only,
                facts.write_only,
                self.spelled(facts.type),
                facts.backend.emitted.emitted_default_kind,
                stable_repr(facts.backend.emitted.emitted_default_value),
            )
        )


def _named(value: TypeView) -> tuple[SymbolId, ...]:
    """Return the generated symbols a type names, through its members, arguments, metadata base and enum literals."""
    nested: tuple[TypeView, ...] = (
        *getattr(value, "members", ()),
        *getattr(value, "arguments", ()),
        *((value.base,) if isinstance(value, GenericType) else ()),
        *(
            GeneratedSymbolType(item.symbol)
            for item in getattr(value, "values", ())
            if isinstance(item, GeneratedEnumMember)
        ),
    )
    return (
        *((value.symbol,) if isinstance(value, GeneratedSymbolType) else ()),
        *(symbol for item in nested for symbol in _named(item)),
    )


def _is_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _projection(value: object) -> JSONValue:
    """Project normalized contract mappings, sequences, and scalar values into canonical JSON."""
    if _is_sequence(value):
        return [_projection(item) for item in value]
    if _is_mapping(value):
        return {str(key): _projection(item) for key, item in value.items()}
    return cast("JSONValue", value)
