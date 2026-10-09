"""The client target: plan, bind, and render one client package behind the single-target coordinator."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Final

from typing_extensions import TypeIs

from datamodel_code_generator._api_generation import TargetRender
from datamodel_code_generator._api_types import APIGenerationError, Diagnostic
from datamodel_code_generator._client.caching import plan_caches
from datamodel_code_generator._client.codec_plan import plan_client_codecs
from datamodel_code_generator._client.config import OPTION_PREFIX, ClientGenerationConfig
from datamodel_code_generator._client.fields import plan_fields
from datamodel_code_generator._client.model_facts import ModelFacts
from datamodel_code_generator._client.pagination import plan_pagination
from datamodel_code_generator._client.plan import (
    PlanError,
    Planner,
    encoding_header_uses,
    form_uses,
    part_uses,
    plan_uses,
    style_uses,
)
from datamodel_code_generator._client.polling import plan_polling
from datamodel_code_generator._client.protocol_plan import (
    helper_metadata,
    helper_problems,
    plan_protocols,
)
from datamodel_code_generator._client.render import ClientRenderer
from datamodel_code_generator._client.sockets import DEPENDENCY as WEBSOCKETS
from datamodel_code_generator._client.sockets import plan_sockets, socket_uses
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
from datamodel_code_generator._openapi_wire_plan import operation_uses, plan_wire
from datamodel_code_generator._runtime.model_codecs.wire import checked_scalar
from datamodel_code_generator._target_contract import (
    AnnotatedType,
    GeneratedEnumMember,
    GeneratedSymbolType,
    GenericType,
    UnionType,
)
from datamodel_code_generator._target_documents import canonical_bytes, sha256
from datamodel_code_generator._target_module import TargetModule, TypeNames
from datamodel_code_generator._target_render import model_dependencies
from datamodel_code_generator.enums import DataModelType

if TYPE_CHECKING:
    from pathlib import Path

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_types import TargetKind
    from datamodel_code_generator._client.caching import CacheSpec
    from datamodel_code_generator._client.codec_plan import CodecBackend
    from datamodel_code_generator._client.pagination import PaginationSpec
    from datamodel_code_generator._client.plan import OperationSpec
    from datamodel_code_generator._client.sockets import SocketSpec
    from datamodel_code_generator._client.webhooks import WebhookSpec
    from datamodel_code_generator._openapi_wire_plan import CodecDiagnostic, WirePlan
    from datamodel_code_generator._runtime.model_codecs.wire import JSONValue
    from datamodel_code_generator._target_contract import (
        GeneratedTypeContractBatch,
        ModelFieldFacts,
        SymbolId,
        TypeUseBinding,
        TypeView,
    )

HTTPX2: Final = "httpx2>=2.13.0"
DEPENDENCIES: Final = ("typing-extensions>=4.16",)
PYDANTIC: Final = "pydantic>=2.13.5"
BACKEND_DEPENDENCIES: Final[dict[str, tuple[str, ...]]] = {
    "pydantic_v2.BaseModel": (PYDANTIC,),
    "pydantic_v2.dataclass": (PYDANTIC,),
    "msgspec.Struct": ("msgspec>=0.21.1",),
}
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

    def __init__(self, protocol_base: Path | None = None) -> None:
        """Resolve the documents that helper records or a helper JSON object name against protocol_base.

        Without it, they resolve against the working directory.
        """
        self.protocol_base = protocol_base

    def render(self, request: TargetRequest) -> TargetRender:  # ruff: ignore[too-many-locals]
        """Plan the selected operations, bind their codecs, and render the package."""
        config = request.config
        assert isinstance(config, ClientGenerationConfig)
        backend = _BACKENDS[request.model_config.output_model_type]
        protocols = plan_protocols(request, config.protocols, self.protocol_base or request.cwd)
        wire = _wire(request, request.batch)
        facts = ModelFacts(request.batch)
        try:
            plan = Planner(request, config, wire, facts).plan()
        except PlanError as error:
            raise APIGenerationError(error.diagnostics, option_prefix=OPTION_PREFIX) from None
        events, hooked = webhook_uses(protocols, request)
        received = frozenset(event.use.id for spec in events for event in spec.events)
        streamed, stream_events, stream_problems = stream_uses(protocols, plan, request)
        opened, messages, socket_problems = socket_uses(protocols, plan, request)
        uses = frozenset(plan_uses(plan)) | received | frozenset(use.id for use in (*stream_events, *messages))
        batch = request.batch
        if parts := (*part_uses(plan), *stream_events, *messages):
            batch = replace(batch, type_uses=(*batch.type_uses, *parts))
        codecs = plan_client_codecs(batch, wire, backend, uses, facts)
        selected = {spec.contract.id for spec in plan.operations}
        if problems := [item for item in codecs.diagnostics if item.operation in {None, *selected}]:
            raise APIGenerationError(tuple(map(_diagnostic, problems)))
        coded = frozenset(item.use for item in codecs.uses)
        plan, named = plan_fields(plan, facts, coded)
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
        types = TypeNames(
            batch,
            exact=bool(request.model_config.use_exact_imports),
            overrides=request.model_config.import_overrides,
        )
        data = _HelperDigests(request, types, facts)
        metadata = helper_metadata(protocols, request)
        fingerprints = {spec.helper.name: data.fingerprint(spec, metadata[spec.helper.name]) for spec in pages}
        fingerprints.update((spec.helper.name, data.cache(spec, metadata[spec.helper.name])) for spec in caches)
        fingerprints.update((spec.helper.name, data.webhook(spec, metadata[spec.helper.name])) for spec in webhooks)
        fingerprints.update((spec.helper.name, data.socket(spec, metadata[spec.helper.name])) for spec in sockets)
        dependencies = (
            WEBSOCKETS if sockets else HTTPX2,
            *DEPENDENCIES,
            *BACKEND_DEPENDENCIES.get(backend, ()),
            *webhook_dependencies(webhooks),
            *model_dependencies(request.model_imports),
        )
        renderer = ClientRenderer(
            config=config,
            plan=plan,
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
        )
        return TargetRender(files=renderer.files(), dependencies=dependencies)


def _wire(request: TargetRequest, batch: GeneratedTypeContractBatch) -> WirePlan:
    """Plan the parameters and headers of the selected operations, with the member plans of their forms."""
    return plan_wire(
        batch,
        [
            *(use for operation in request.operations for use in operation_uses(operation)),
            *encoding_header_uses(request),
        ],
        operations=frozenset(operation.id for operation in request.operations),
        forms=dict(form_uses(request)),
        styles=dict(style_uses(request)),
    )


def _diagnostic(item: CodecDiagnostic) -> Diagnostic:
    return Diagnostic(
        code=item.code,
        severity="error",
        stage="binding",
        message=item.message,
        source_pointer=item.source.pointer,
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
        operation, helper = spec.operation, spec.helper
        body = operation.body
        signature = {
            "name": helper.name,
            "parameters": [(item.python_name, item.required, self.type(item.use)) for item in operation.parameters],
            "body": None
            if body is None
            else (body.required, [(media.media_type, self.type(media.use)) for media in body.media]),
            "item": self.spelled(spec.item),
            "page": self.type(spec.page),
            "settings": settings,
        }
        return _digest({
            "kind": helper.kind,
            "signatures": [signature],
            "operations": [self.request.documents.operation(operation.contract.id)],
            "schemas": [spec.item_schema],
            "type_uses": [self.contract(spec.page)],
        })

    def cache(self, spec: CacheSpec, settings: JSONValue) -> str:
        """Return the digest of a cache helper's contract closure: its fetch signature and operation.

        The fetch's signature carries the settings, and the type use of its cacheable response closes the contract.
        """

        def signature(name: str, operation: OperationSpec) -> dict[str, object]:
            body = operation.body
            return {
                "name": name,
                "parameters": [(item.python_name, item.required, self.type(item.use)) for item in operation.parameters],
                "body": None
                if body is None
                else (body.required, [(media.media_type, self.type(media.use)) for media in body.media]),
                "responses": [
                    (item.status, [self.type(media.use) for media in item.media]) for item in operation.responses
                ],
            }

        operations = (spec.operation,)
        return _digest({
            "kind": "cache",
            "signatures": [
                {**signature(spec.helper.name, spec.operation), "settings": settings},
            ],
            "operations": [self.request.documents.operation(item.contract.id) for item in operations],
            "schemas": [],
            "type_uses": [self.contract(spec.response)],
        })

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
        return _digest({
            "kind": "webhook",
            "signatures": [signature],
            "operations": [],
            "schemas": [event.schema for event in events],
            "type_uses": [self.contract(event.use) for event in events],
        })

    def socket(self, spec: SocketSpec, settings: JSONValue) -> str:
        """Return the digest of a WebSocket helper's contract closure: its signature, settings, operation, and schemas.

        Each message use contributes its type and contract, so a changed schema changes the digest.
        """
        operation, helper = spec.operation, spec.helper
        signature = {
            "name": helper.name,
            "parameters": [(item.python_name, item.required, self.type(item.use)) for item in operation.parameters],
            "send": None if spec.send is None else self.type(spec.send),
            "receive": None if spec.receive is None else self.type(spec.receive),
            "settings": settings,
        }
        return _digest({
            "kind": helper.kind,
            "signatures": [signature],
            "operations": [self.request.documents.operation(operation.contract.id)],
            "schemas": list(spec.schemas),
            "type_uses": [self.contract(use) for use in spec.uses],
        })

    def spelled(self, value: TypeView) -> str:
        """Return a type's canonical spelling: its containers and unions by structure, its leaves as models spell them.

        The model options that only style annotations, such as the union operator, leave the spelling unchanged, so a
        regenerated package with the same contract keeps its fingerprints.
        """
        value = value.base if isinstance(value, AnnotatedType) else value
        match value:
            case GenericType():
                arguments = ", ".join(self.spelled(item) for item in value.arguments)
                base = self.spelled(value.base)
                return f"{base}[{arguments or '()'}]" if arguments or value.tuple_form == "fixed" else base
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
        pending = list(() if use.type is None else _named(use.type))
        models: dict[str, object] = {}
        while pending:
            symbol = pending.pop()
            if (name := self.spelling.symbol(symbol)) in models:
                continue
            found, members = self.facts.symbols[symbol], self.facts.members.get(symbol, ())
            models[name] = {
                "kind": found.kind,
                "values": [None if value is None else repr(value.value) for value in found.values],
                "fields": [
                    [member.wire_name, member.member_kind, member.exclusion, *self.field(member.model_facts)]
                    for member in members
                ],
            }
            pending.extend(
                symbol for member in members if member.model_facts for symbol in _named(member.model_facts.type)
            )
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
                repr(facts.backend.emitted.emitted_default_value),
            )
        )


def _named(value: TypeView) -> tuple[SymbolId, ...]:
    """Return the generated symbols a type names, through its members, arguments, metadata base and enum literals."""
    nested: tuple[TypeView, ...] = (
        *getattr(value, "members", ()),
        *getattr(value, "arguments", ()),
        *((value.base,) if isinstance(value, AnnotatedType | GenericType) else ()),
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


def _digest(value: object) -> str:
    return sha256(canonical_bytes(_projection(value)))


def _is_sequence(value: object) -> TypeIs[tuple[object, ...] | list[object]]:
    return isinstance(value, (tuple, list))


def _is_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _projection(value: object) -> JSONValue:
    """Project normalized contract mappings, sequences, and scalar values into canonical JSON."""
    if _is_sequence(value):
        return [_projection(item) for item in value]
    if _is_mapping(value):
        return {str(key): _projection(item) for key, item in value.items()}
    return checked_scalar(value)
