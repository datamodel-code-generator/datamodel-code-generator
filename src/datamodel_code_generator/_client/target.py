"""The client target: plan, bind, and render one client package behind the single-target coordinator."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Final, cast

from typing_extensions import TypeIs

from datamodel_code_generator._api_generation import TargetRender
from datamodel_code_generator._api_manifest import canonical_bytes, sha256
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
from datamodel_code_generator._codec_type_source import Namespace, TypeSource
from datamodel_code_generator._openapi_wire_plan import operation_uses, plan_wire
from datamodel_code_generator._runtime.model_codecs.wire import checked_scalar
from datamodel_code_generator._target_render import model_dependencies
from datamodel_code_generator.enums import DataModelType

if TYPE_CHECKING:
    from pathlib import Path

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_types import TargetKind
    from datamodel_code_generator._client.caching import CacheSpec
    from datamodel_code_generator._client.codec_plan import ClientCodecs, CodecBackend
    from datamodel_code_generator._client.pagination import PaginationSpec
    from datamodel_code_generator._client.plan import OperationSpec
    from datamodel_code_generator._client.polling import PollingSpec
    from datamodel_code_generator._client.sockets import SocketSpec
    from datamodel_code_generator._client.streams import StreamSpec
    from datamodel_code_generator._client.uploads import UploadSpec
    from datamodel_code_generator._client.webhooks import WebhookSpec
    from datamodel_code_generator._openapi_wire_plan import CodecDiagnostic, WirePlan
    from datamodel_code_generator._runtime.model_codecs.wire import JSONValue
    from datamodel_code_generator._target_contract import (
        GeneratedTypeContractBatch,
        SourceLocation,
        TypeUseBinding,
        TypeUseId,
    )

DEPENDENCIES: Final = ("httpx2>=2.13.0", "typing-extensions>=4.16")
PYDANTIC: Final = "pydantic>=2.13.5"
BACKEND_DEPENDENCIES: Final[dict[str, tuple[str, ...]]] = {
    "pydantic_v2.BaseModel": (PYDANTIC,),
    "pydantic_v2.dataclass": (PYDANTIC,),
    "msgspec.Struct": ("msgspec>=0.18",),
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
            raise APIGenerationError(
                tuple(replace(item, target_id=request.target_id) for item in error.diagnostics),
                option_prefix=OPTION_PREFIX,
            ) from None
        events, hooked = webhook_uses(protocols, request)
        received = frozenset(event.use.id for spec in events for event in spec.events)
        streamed, stream_events, stream_problems = stream_uses(protocols, plan, request, wire)
        opened, messages, socket_problems = socket_uses(protocols, plan, request, wire)
        uses = frozenset(plan_uses(plan)) | received | frozenset(use.id for use in (*stream_events, *messages))
        batch = request.batch
        if parts := (*part_uses(plan), *stream_events, *messages):
            batch = replace(batch, type_uses=(*batch.type_uses, *parts))
        if parts or events:
            wire = _wire(request, batch, parts, received)
        codecs = plan_client_codecs(batch, wire, backend, uses, facts)
        selected = {spec.contract.id for spec in plan.operations}
        if problems := [item for item in codecs.diagnostics if item.operation in {None, *selected}]:
            raise APIGenerationError(tuple(_diagnostic(item, request) for item in problems))
        coded = frozenset(item.use for item in codecs.uses)
        plan, named = plan_fields(plan, facts, coded, wire)
        pages, checked = plan_pagination(protocols, plan, facts, coded, wire, request)
        polls, polled = plan_polling(protocols, plan, facts, coded, wire, request)
        caches, cached = plan_caches(protocols, plan, facts, coded, wire, request)
        uploads, uploaded = plan_uploads(protocols, plan, facts, coded, wire, request)
        order = {} if protocols is None else {helper.name: index for index, helper in enumerate(protocols.helpers)}
        helpers = tuple(sorted((*pages, *polls, *caches, *uploads), key=lambda spec: order[spec.helper.name]))
        streams = plan_streams(streamed, protocols, plan, facts, coded, wire, request, stream_problems)
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
            raise APIGenerationError(
                tuple(
                    replace(item, source_uri=request.documents.root_uri, target_id=request.target_id)
                    for item in refused
                ),
                option_prefix=OPTION_PREFIX,
            )
        data = _HelperDigests(request, codecs, wire)
        metadata = helper_metadata(protocols, request)
        fingerprints = {spec.helper.name: data.fingerprint(spec, metadata[spec.helper.name]) for spec in pages}
        fingerprints.update((spec.helper.name, data.polling(spec, metadata[spec.helper.name])) for spec in polls)
        fingerprints.update((spec.helper.name, data.cache(spec, metadata[spec.helper.name])) for spec in caches)
        fingerprints.update((spec.helper.name, data.upload(spec, metadata[spec.helper.name])) for spec in uploads)
        fingerprints.update((spec.helper.name, data.webhook(spec, metadata[spec.helper.name])) for spec in webhooks)
        fingerprints.update((spec.helper.name, data.stream(spec, metadata[spec.helper.name])) for spec in streams)
        fingerprints.update((spec.helper.name, data.socket(spec, metadata[spec.helper.name])) for spec in sockets)
        dependencies = (
            *DEPENDENCIES,
            *((WEBSOCKETS,) if sockets else ()),
            *BACKEND_DEPENDENCIES.get(backend, ()),
            *webhook_dependencies(webhooks),
            *model_dependencies(request.models),
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
            webhooks=partial(webhook_files, webhooks, dict(codecs.imports)),
            signatures=frozenset(spec.helper.tree["signature"]["kind"] for spec in webhooks),
            backend=backend,
            dependencies=dependencies,
            templates=ClientTemplates.custom(request.model_config, request.target_id, request.cwd),
        )
        return TargetRender(files=renderer.files(), dependencies=dependencies)


def _wire(
    request: TargetRequest,
    batch: GeneratedTypeContractBatch,
    parts: tuple[TypeUseBinding, ...] = (),
    received: frozenset[TypeUseId] = frozenset(),
) -> WirePlan:
    """Plan the wire of the selected operations' uses, the parts they send, and the webhook events they receive.

    Forms get their member plans.
    """
    return plan_wire(
        batch,
        request.lease,
        [
            *(use for operation in request.operations for use in operation_uses(operation)),
            *encoding_header_uses(request),
            *(part.id for part in parts),
            *received,
        ],
        operations=frozenset(operation.id for operation in request.operations),
        documents=request.documents.pointers,
        forms=dict(form_uses(request)),
        styles=dict(style_uses(request)),
    )


def _diagnostic(item: CodecDiagnostic, request: TargetRequest) -> Diagnostic:
    return Diagnostic(
        code=item.code,
        severity="error",
        stage="binding",
        message=item.message,
        source_uri=request.documents.root_uri,
        source_pointer=item.source.pointer,
        target_id=request.target_id,
    )


class _HelperDigests:
    """Digest each rendered helper's contract closure."""

    def __init__(self, request: TargetRequest, codecs: ClientCodecs, wire: WirePlan) -> None:
        """Index the import locations of the generated symbols."""
        self.request = request
        self.wire = wire
        self.spelling = TypeSource(Namespace(()), dict(codecs.imports), lambda module, name: f"{module}.{name}")

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
            "item": self.spelling.static(spec.item),
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

    def polling(self, spec: PollingSpec, settings: JSONValue) -> str:
        """Return the digest of a polling helper's contract closure: its signature and settings, operations, and uses.

        The signature spells the create call's arguments and the result type, and the uses are the create responses',
        the poll's, the result fetch's, and the remote cancel's.
        """
        operation, helper, fetch = spec.operation, spec.helper, spec.fetch
        body = operation.body
        uses = (
            *spec.create_uses,
            spec.poll_use,
            *(() if spec.fetch_use is None else (spec.fetch_use,)),
            *spec.cancel_uses,
        )
        signature = {
            "name": helper.name,
            "parameters": [(item.python_name, item.required, self.type(item.use)) for item in operation.parameters],
            "body": None
            if body is None
            else (body.required, [(media.media_type, self.type(media.use)) for media in body.media]),
            "result": None if spec.value is None else self.spelling.static(spec.value),
            "uses": [self.type(use) for use in uses],
            "settings": settings,
        }
        documents = self.request.documents
        return _digest({
            "kind": helper.kind,
            "signatures": [signature],
            "operations": [
                documents.operation(item.contract.id)
                for item in (operation, spec.poll, *(item for item in (fetch, spec.cancel) if item is not None))
            ],
            "schemas": list(spec.schemas),
            "type_uses": [self.contract(use) for use in uses],
        })

    def upload(self, spec: UploadSpec, settings: JSONValue) -> str:
        """Return the digest of an upload helper's contract closure: its signature and settings, operations, and uses.

        The signature spells the create call's arguments, the size it writes, and the result type, and the uses are the
        create responses' and the completion's.
        """
        operation, helper = spec.operation, spec.helper
        body = operation.body
        uses = (*spec.create_uses, *(() if spec.completion_use is None else (spec.completion_use,)))
        signature = {
            "name": helper.name,
            "parameters": [(item.python_name, item.required, self.type(item.use)) for item in operation.parameters],
            "size": None if spec.size is None else spec.size.python_name,
            "body": None
            if body is None
            else (body.required, [(media.media_type, self.type(media.use)) for media in body.media]),
            "result": None if spec.completion_use is None else self.type(spec.completion_use),
            "uses": [self.type(use) for use in uses],
            "settings": settings,
        }
        documents = self.request.documents
        operations = (operation, spec.probe, spec.append, *(() if spec.completion is None else (spec.completion,)))
        return _digest({
            "kind": helper.kind,
            "signatures": [signature],
            "operations": [documents.operation(item.contract.id) for item in operations],
            "schemas": list(spec.schemas),
            "type_uses": [self.contract(use) for use in uses],
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

    def stream(self, spec: StreamSpec, settings: JSONValue) -> str:
        """Return the digest of a stream helper's contract closure: its signature, settings, operations, and schemas.

        Each event and error use contributes its type and contract, so a changed schema changes the digest, and a helper
        reopening its stream with another operation adds that operation.
        """
        operation, helper = spec.operation, spec.helper
        body = operation.body
        signature = {
            "name": helper.name,
            "parameters": [(item.python_name, item.required, self.type(item.use)) for item in operation.parameters],
            "body": None
            if body is None
            else (body.required, [(media.media_type, self.type(media.use)) for media in body.media]),
            "events": [(key, self.type(use)) for key, use in spec.events],
            "errors": [(key, self.type(use)) for key, use in spec.errors],
            "settings": settings,
        }
        return _digest({
            "kind": helper.kind,
            "signatures": [signature],
            "operations": [
                self.request.documents.operation(item.contract.id)
                for item in (operation, *(() if spec.reopen is None or spec.own else (spec.reopen,)))
            ],
            "schemas": list(spec.schemas),
            "type_uses": [self.contract(use) for use in spec.uses],
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

    def type(self, use: TypeUseBinding | None) -> str | None:
        """Return a use's final type spelled with the import locations of its names, or None without a schema."""
        return None if use is None or use.type is None else self.spelling.static(use.type)

    def contract(self, use: TypeUseBinding) -> object:
        """Return a retained helper use's normalized schema at its site."""
        return self.wire.schema(cast("SourceLocation", use.schema))[1]


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
