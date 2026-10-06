"""The client target: plan, bind, and render one client package behind the single-target coordinator."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Final, cast

from typing_extensions import TypeIs

from datamodel_code_generator._api_generation import TargetBinding, TargetRender
from datamodel_code_generator._api_manifest import canonical_bytes, sha256
from datamodel_code_generator._api_types import APIGenerationError, Diagnostic
from datamodel_code_generator._client.caching import plan_caches
from datamodel_code_generator._client.config import ClientGenerationConfig
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
    protocol_helpers,
    protocol_metadata,
)
from datamodel_code_generator._client.render import ClientRenderer
from datamodel_code_generator._client.sockets import DEPENDENCY as WEBSOCKETS
from datamodel_code_generator._client.sockets import plan_sockets, socket_uses
from datamodel_code_generator._client.streams import plan_streams, stream_uses
from datamodel_code_generator._client.uploads import plan_uploads
from datamodel_code_generator._client.webhooks import (
    key_class,
    plan_webhooks,
    webhook_dependencies,
    webhook_files,
    webhook_uses,
)
from datamodel_code_generator._codec_type_source import Namespace, TypeSource
from datamodel_code_generator._openapi_codec_plan import plan_model_codecs
from datamodel_code_generator._openapi_wire_plan import operation_uses, plan_wire
from datamodel_code_generator._runtime.model_codecs.codec import needs_schema
from datamodel_code_generator._runtime.model_codecs.wire import checked_scalar
from datamodel_code_generator._target_contract import OperationId
from datamodel_code_generator._target_render import PATTERNS, model_dependencies, patterned
from datamodel_code_generator.enums import DataModelType

if TYPE_CHECKING:
    from collections.abc import Iterator

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_manifest import JSONObject
    from datamodel_code_generator._api_types import TargetKind
    from datamodel_code_generator._client.caching import CacheSpec
    from datamodel_code_generator._client.pagination import PaginationSpec
    from datamodel_code_generator._client.plan import ClientPlan, OperationSpec
    from datamodel_code_generator._client.polling import PollingSpec
    from datamodel_code_generator._client.protocol_plan import Protocols
    from datamodel_code_generator._client.sockets import SocketSpec
    from datamodel_code_generator._client.streams import StreamSpec
    from datamodel_code_generator._client.uploads import UploadSpec
    from datamodel_code_generator._client.webhooks import WebhookSpec
    from datamodel_code_generator._openapi_codec_plan import CodecBackend, CodecPlan
    from datamodel_code_generator._openapi_wire_plan import CodecDiagnostic, WirePlan
    from datamodel_code_generator._runtime.model_codecs.wire import JSONValue
    from datamodel_code_generator._target_contract import (
        GeneratedTypeContractBatch,
        SourceLocation,
        TypeUseBinding,
        TypeUseId,
    )

DEPENDENCIES: Final = ("httpx2>=2.13.0", "typing-extensions>=4.16")
VALIDATION: Final = ("jsonschema[format-nongpl]>=4.26", "referencing>=0.37")
PYDANTIC: Final = "pydantic>=2.13.5"
BACKEND_DEPENDENCIES: Final[dict[str, tuple[str, ...]]] = {
    "pydantic_v2.BaseModel": (PYDANTIC,),
    "pydantic_v2.dataclass": (PYDANTIC,),
    "msgspec.Struct": ("msgspec>=0.18",),
}
_RESPONSE_ROLES: Final = {"response_body": "response body", "response_encoding_header": "part header"}
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

    def render(self, request: TargetRequest) -> TargetRender:  # ruff: ignore[no-self-use, too-many-locals]
        """Plan the selected operations, bind their codecs, and render the package."""
        config = request.config
        assert isinstance(config, ClientGenerationConfig)
        backend = _BACKENDS[request.model_config.output_model_type]
        protocols = plan_protocols(request, config.protocols)
        wire = _wire(request, request.batch)
        try:
            plan = Planner(request, config, wire).plan()
        except PlanError as error:
            raise APIGenerationError(
                tuple(replace(item, target_id=request.target_id) for item in error.diagnostics)
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
        codecs = plan_model_codecs(
            batch,
            replace(wire, schema_ids=tuple(item for item in wire.schema_ids if item[0] in uses)),
            backend,
            envelopes=False,
        )
        selected = {spec.contract.id for spec in plan.operations}
        if problems := [item for item in codecs.diagnostics if item.operation in {None, *selected}]:
            raise APIGenerationError(tuple(_diagnostic(item, request) for item in problems))
        facts, coded = ModelFacts(batch), frozenset(use for use, _ in codecs.bindings)
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
        ordinary = replace(codecs, bindings=tuple(item for item in codecs.bindings if item[0] not in received))
        if refused := (
            *named,
            *_inseparable(ordinary),
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
                )
            )
        data = _TargetData(plan, config, request, codecs, wire)
        metadata = helper_metadata(protocols, request)
        fingerprints = {spec.helper.name: data.fingerprint(spec, metadata[spec.helper.name]) for spec in pages}
        fingerprints.update((spec.helper.name, data.polling(spec, metadata[spec.helper.name])) for spec in polls)
        fingerprints.update((spec.helper.name, data.cache(spec, metadata[spec.helper.name])) for spec in caches)
        fingerprints.update((spec.helper.name, data.upload(spec, metadata[spec.helper.name])) for spec in uploads)
        fingerprints.update((spec.helper.name, data.webhook(spec, metadata[spec.helper.name])) for spec in webhooks)
        fingerprints.update((spec.helper.name, data.stream(spec, metadata[spec.helper.name])) for spec in streams)
        fingerprints.update((spec.helper.name, data.socket(spec, metadata[spec.helper.name])) for spec in sockets)
        renderer = ClientRenderer(
            config=config,
            package=request.layout.package,
            plan=plan,
            batch=batch,
            wire=wire,
            codecs=codecs,
            helpers=helpers,
            streams=streams,
            sockets=sockets,
            fingerprints=fingerprints,
            webhooks=partial(webhook_files, webhooks, dict(codecs.imports)),
        )
        return TargetRender(
            files=renderer.files(),
            target_data=data.data(protocols, fingerprints),
            dependencies=(
                *DEPENDENCIES,
                *((WEBSOCKETS,) if sockets else ()),
                *(VALIDATION if codecs.bindings else ()),
                *BACKEND_DEPENDENCIES.get(backend, ()),
                *((PATTERNS,) if patterned(wire) else ()),
                *webhook_dependencies(webhooks),
                *model_dependencies(request.models),
            ),
            bindings=_bindings(codecs, backend),
            protocol_metadata=protocol_metadata(metadata, protocols),
        )


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


def _label(use: TypeUseId) -> str:
    """Return how a message names a response use: its role, name, status, media, and operation's method and path."""
    owner = use.owner
    assert isinstance(owner, OperationId)
    *_, path, method = (token.replace("~1", "/").replace("~0", "~") for token in owner.use_site.pointer.split("/"))
    part = "part" if use.name is not None and use.role.endswith("_body") else None
    described = (_RESPONSE_ROLES[use.role], part, use.name, use.status, use.media)
    return f"{' '.join(word for word in described if word)} of {method.upper()} {path}"


def _inseparable(codecs: CodecPlan) -> Iterator[Diagnostic]:
    """Yield a diagnostic for each received use whose union members only their schemas tell apart.

    Responses are converted natively, which cannot choose among such members.
    """
    for use, binding in codecs.bindings:
        if use.role in _RESPONSE_ROLES and needs_schema(binding):
            yield Diagnostic(
                code="E_CONFIG_VALUE",
                severity="error",
                stage="binding",
                message=(
                    f"The client cannot convert the {_label(use)}, which tells its union members apart only by their "
                    "schemas"
                ),
                source_pointer=use.use_site.pointer,
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


def _bindings(codecs: CodecPlan, backend: str) -> tuple[TargetBinding, ...]:
    return tuple(
        TargetBinding(
            use=use,
            backend=backend,
            strategy=binding.projection_mode,
            converter_strategy=binding.converter_strategy,
        )
        for use, binding in codecs.bindings
    )


class _TargetData:
    """Record the client's namespace and each public operation."""

    def __init__(
        self,
        plan: ClientPlan,
        config: ClientGenerationConfig,
        request: TargetRequest,
        codecs: CodecPlan,
        wire: WirePlan,
    ) -> None:
        """Index the selected operations and the import locations of the generated symbols."""
        self.plan = plan
        self.config = config
        self.request = request
        self.codecs = codecs
        self.wire = wire
        self.selected = {operation.id: index for index, operation in enumerate(request.operations)}
        self.spelling = TypeSource(Namespace(()), dict(codecs.imports), lambda module, name: f"{module}.{name}")

    def data(self, protocols: Protocols | None, fingerprints: Mapping[str, str]) -> JSONObject:
        """Return the client manifest data with the target's helpers."""
        extensions = int(self.config.formatter_settings is not None) + len(self.config.custom_formatters)
        return {
            "namespace": self.config.package,
            "public_api": [self.operation(spec) for spec in self.plan.operations],
            "protocol_helpers": protocol_helpers(protocols, fingerprints),
            "runtime_defaults_ref": "/inputs/target_config/runtime_defaults",
            "selection_ref": "/selection",
            "binding_refs": [f"/bindings/{index}" for index in range(len(self.codecs.bindings))],
            "extension_refs": [f"/extensions/formatters/{index}" for index in range(extensions)],
        }

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
            "validate": {event.name: event.validate for event in events} if mapped else events[0].validate,
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

    def operation(self, spec: OperationSpec) -> JSONValue:
        """Return the manifest record of one public operation."""
        types = f"{self.config.package}.types.{spec.resource}"
        headers = any(response.headers for response in spec.responses)
        return {
            "operation_ref": f"/selection/selected_operations/{self.selected[spec.contract.id]}",
            "resource": spec.resource,
            "method": spec.name,
            "parameters": [
                {"location": parameter.location, "wire_name": parameter.wire_name, "python_name": parameter.python_name}
                for parameter in spec.parameters
            ],
            "exports": {
                "response": f"{types}.{spec.pascal}Response",
                "header_decoder": f"{types}.decode_{spec.name}_header" if headers else None,
            },
        }

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
    """Project a contract value into canonical JSON."""
    if _is_sequence(value):
        return [_projection(item) for item in value]
    if _is_mapping(value):
        return {str(key): _projection(item) for key, item in value.items()}
    return checked_scalar(value)
