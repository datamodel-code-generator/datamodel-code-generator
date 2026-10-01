"""The client target: plan, bind, and render one client package behind the single-target coordinator."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from functools import partial
from typing import TYPE_CHECKING, Final

from typing_extensions import TypeIs

from datamodel_code_generator._api_generation import TargetBinding, TargetRender
from datamodel_code_generator._api_manifest import canonical_bytes, sha256
from datamodel_code_generator._api_types import APIGenerationError, Diagnostic
from datamodel_code_generator._client.config import ClientGenerationConfig
from datamodel_code_generator._client.fields import plan_fields
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
from datamodel_code_generator._client.protocol_plan import (
    helper_metadata,
    helper_problems,
    plan_protocols,
    protocol_helpers,
    protocol_metadata,
)
from datamodel_code_generator._client.render import ClientRenderer
from datamodel_code_generator._client.security import security_contract
from datamodel_code_generator._client.sockets import DEPENDENCY as WEBSOCKETS
from datamodel_code_generator._client.sockets import plan_sockets, socket_uses
from datamodel_code_generator._client.streams import plan_streams, stream_uses
from datamodel_code_generator._client.validation import admission_problems, allowed, argument_uses
from datamodel_code_generator._client.webhooks import (
    key_class,
    plan_webhooks,
    webhook_dependencies,
    webhook_files,
    webhook_uses,
)
from datamodel_code_generator._codec_declarations import CodecDeclarations
from datamodel_code_generator._codec_type_source import Namespace, TypeSource
from datamodel_code_generator._openapi_codec_adapters import select_adapters
from datamodel_code_generator._openapi_codec_plan import artifact_module, plan_model_codecs
from datamodel_code_generator._openapi_wire_plan import operation_uses, plan_wire
from datamodel_code_generator._runtime.model_codecs.wire import checked_scalar
from datamodel_code_generator._target_render import PATTERNS, model_dependencies, patterned
from datamodel_code_generator.enums import DataModelType

if TYPE_CHECKING:
    from collections.abc import Callable

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_manifest import JSONObject
    from datamodel_code_generator._api_types import TargetKind
    from datamodel_code_generator._client.pagination import PaginationSpec
    from datamodel_code_generator._client.plan import ClientPlan, MediaSpec, OperationSpec, ParameterSpec, PartSpec
    from datamodel_code_generator._client.protocol_plan import Protocols
    from datamodel_code_generator._client.sockets import SocketSpec
    from datamodel_code_generator._client.streams import StreamSpec
    from datamodel_code_generator._client.webhooks import WebhookSpec
    from datamodel_code_generator._generation_contract import GeneratedTypeContractBatch, TypeUseBinding, TypeUseId
    from datamodel_code_generator._openapi_codec_plan import CodecBackend, CodecPlan
    from datamodel_code_generator._openapi_wire_plan import CodecDiagnostic, WirePlan
    from datamodel_code_generator._runtime.model_codecs.parameters import ParameterPlan
    from datamodel_code_generator._runtime.model_codecs.wire import JSONValue

DEPENDENCIES: Final = ("httpx2>=2.13.0", "typing-extensions>=4.16")
VALIDATION: Final = ("jsonschema[format-nongpl]>=4.26", "referencing>=0.37")
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

    def render(self, request: TargetRequest) -> TargetRender:  # noqa: PLR6301, PLR0914
        """Plan the selected operations, bind their codecs, and render the package."""
        config = request.config
        assert isinstance(config, ClientGenerationConfig)
        backend = _BACKENDS[request.model_config.output_model_type]
        protocols = plan_protocols(request, config.protocols)
        wire = _wire(request, request.batch)
        declarations = CodecDeclarations(
            compatibility=config.builtin_codec_compatibility,
            exports=config.export_bindings,
            adapters=config.codec_adapters,
        )
        selection = select_adapters(request.batch, wire, declarations, "client") if config.codec_adapters else None
        adapted = frozenset() if selection is None else selection.uses("parameter")
        try:
            plan = Planner(request, config, wire, adapted).plan()
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
            selection = None
        codecs = plan_model_codecs(
            batch,
            replace(wire, schema_ids=tuple(item for item in wire.schema_ids if item[0] in uses)),
            backend,
            declarations=declarations,
            surface="client",
            lease=request.lease,
            sources=_sources(request),
            selection=selection,
        )
        selected = {spec.contract.id for spec in plan.operations}
        if problems := [item for item in codecs.diagnostics if item.operation in {None, *selected}]:
            raise APIGenerationError(tuple(_diagnostic(item, request) for item in problems))
        plan, named = plan_fields(plan, codecs, batch, wire)
        helpers, checked = plan_pagination(protocols, plan, codecs, wire, request)
        streams = plan_streams(streamed, protocols, codecs, wire, request, stream_problems)
        sockets = plan_sockets(opened, codecs, socket_problems)
        webhooks = plan_webhooks(events, codecs, config, hooked)
        ordinary = replace(codecs, bindings=tuple(item for item in codecs.bindings if item[0] not in received))
        if refused := (
            *named,
            *admission_problems(config.validation, ordinary, argument_uses(plan)),
            *helper_problems(protocols, plan, {**checked, **hooked, **stream_problems, **socket_problems}),
        ):
            raise APIGenerationError(
                tuple(
                    replace(item, source_uri=request.documents.root_uri, target_id=request.target_id)
                    for item in refused
                )
            )
        data = _TargetData(plan, config, request, codecs, wire)
        metadata = helper_metadata(protocols, request)
        fingerprints = {spec.helper.name: data.fingerprint(spec, metadata[spec.helper.name]) for spec in helpers}
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
            webhooks=partial(webhook_files, webhooks, dict(codecs.imports), fingerprints),
        )
        validation = config.validation
        return TargetRender(
            files=renderer.files(),
            target_data=data.data(protocols, fingerprints),
            dependencies=(
                *DEPENDENCIES,
                *((WEBSOCKETS,) if sockets else ()),
                *(VALIDATION if codecs.bindings else ()),
                *dict.fromkeys((
                    *BACKEND_DEPENDENCIES.get(backend, ()),
                    *(
                        (PYDANTIC,)
                        if "pydantic" in allowed(validation.arguments, validation.argument_overrides)
                        else ()
                    ),
                )),
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


def _sources(request: TargetRequest) -> dict[str, str]:
    contents = {artifact.path: artifact.content.decode(artifact.encoding) for artifact in request.models}
    return {
        artifact_module(address): contents[address.relative_path]
        for address in request.batch.artifacts
        if address.relative_path in contents
    }


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
            strategy="adapter" if binding.converter_strategy == "registered_adapter" else binding.projection_mode,
            converter_strategy=binding.converter_strategy,
        )
        for use, binding in codecs.bindings
    )


class _TargetData:
    """Record the client's namespace and each public operation with the digests of its contract."""

    def __init__(
        self,
        plan: ClientPlan,
        config: ClientGenerationConfig,
        request: TargetRequest,
        codecs: CodecPlan,
        wire: WirePlan,
    ) -> None:
        """Index the selected operations, the use bindings, and the import locations of the generated symbols."""
        self.plan = plan
        self.config = config
        self.request = request
        self.codecs = codecs
        self.wire = wire
        self.bindings = dict(codecs.bindings)
        self.adapters = {
            item.use: (
                item.registration.name,
                item.registration.import_ref,
                item.registration.capabilities,
                item.parameter,
            )
            for item in codecs.adapters
            if item.parameter is not None
        }
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
            "adapters": [],
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
            "adapters": [],
        })

    def stream(self, spec: StreamSpec, settings: JSONValue) -> str:
        """Return the digest of a stream helper's contract closure: its signature, settings, operation, and schemas.

        Each event and error use contributes its type and contract, so a changed schema changes the digest.
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
            "operations": [self.request.documents.operation(operation.contract.id)],
            "schemas": list(spec.schemas),
            "type_uses": [self.contract(use) for use in spec.uses],
            "adapters": [],
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
            "adapters": [],
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
                "error_data": f"{types}.{spec.pascal}ErrorData",
                "http_error": f"{types}.{spec.pascal}HTTPError",
                "request_codecs": f"{types}.{spec.pascal}RequestCodecs",
                "header_decoder": f"{types}.decode_{spec.name}_header" if headers else None,
            },
            "contract_digests": {name: _digest(value) for name, value in self.contracts(spec)},
        }

    def contracts(self, spec: OperationSpec) -> tuple[tuple[str, object], ...]:
        """Return the operation's public signature, request, response, and security contracts."""
        body = spec.body
        signature: dict[str, object] = {
            "style": self.config.signature_style,
            "resource": spec.resource,
            "method": spec.name,
            "parameters": [(item.python_name, item.required, self.type(item.use)) for item in spec.parameters],
            "body": None
            if body is None
            else (
                body.required,
                body.default,
                [
                    (media.media_type, self.type(media.use))
                    if media.members is None
                    else (media.media_type, self.type(media.use), self.members(media, self.type))
                    for media in body.media
                ],
            ),
            "responses": [
                (
                    item.status,
                    [
                        self.type(media.use)
                        if media.members is None
                        else (self.type(media.use), self.members(media, self.type))
                        for media in item.media
                    ],
                )
                for item in spec.responses
            ],
            "response_media_type": spec.response_media_type,
        }
        if spec.fields:
            signature["fields"] = [
                (
                    branch.media_type,
                    [
                        (field.python_name, field.wire_name, field.required, self.spelling.static(field.type))
                        for field in branch.fields
                    ],
                )
                for branch in spec.fields
            ]
        request = {
            "method": spec.contract.method,
            "path": spec.contract.path,
            "servers": spec.servers,
            "parameters": [self.parameter(item) for item in spec.parameters],
            "retry_safety": spec.retry_safety,
            "idempotency": None
            if (idempotency := spec.idempotency) is None
            else {
                "header_name": idempotency.header_name,
                "replay_safe_with_key": idempotency.replay_safe_with_key,
                "retention_seconds": idempotency.retention_seconds,
                "scope": idempotency.scope,
            },
            "body": None
            if body is None
            else [
                (
                    item.media_type,
                    item.fields,
                    item.additional,
                    self.contract(item.use),
                    *item.encoded,
                    *item.content_types,
                )
                if item.members is None
                else (item.media_type, self.contract(item.use), self.members(item, self.contract))
                for item in body.media
            ],
        }
        response = {
            "success_statuses": spec.success_statuses,
            "request_id_header": spec.request_id_header,
            "retry_after_ms_header": spec.retry_after_ms_header,
            "should_retry_header": spec.should_retry_header,
            "responses": [
                (
                    item.status,
                    [
                        (media.media_type, self.contract(media.use))
                        if media.members is None
                        else (media.media_type, self.contract(media.use), self.members(media, self.contract))
                        for media in item.media
                    ],
                    [(header.name, header.required, header.plan, self.contract(header.use)) for header in item.headers],
                )
                for item in spec.responses
            ],
        }
        security = security_contract(
            self.request.batch, spec.contract, spec.security, challenge_less=spec.auth_challenge_less_401
        )
        return (("signature", signature), ("request", request), ("response", response), ("security", security))

    @staticmethod
    def members(media: MediaSpec, project: Callable[[TypeUseBinding | None], object]) -> object:
        """Return each member plan of media sent or read as parts with a projection of its use, then any other's."""
        extra = media.extra
        return (
            [
                (
                    *_plan(part),
                    project(part.use),
                    [(header.name, header.required, header.plan, project(header.use)) for header in part.headers],
                )
                for part in media.members or ()
            ],
            None if extra is None else (*_plan(extra), project(extra.use)),
        )

    def type(self, use: TypeUseBinding | None) -> str | None:
        """Return a use's final type spelled with the import locations of its names, or None without a schema."""
        return None if use is None or use.type is None else self.spelling.static(use.type)

    def parameter(self, parameter: ParameterSpec) -> tuple[object, ...]:
        """Return a parameter's plan and use contract, with the registered adapter that carries it, if any."""
        contract = (parameter.plan, self.contract(parameter.use))
        if (use := parameter.use) is None or (adapter := self.adapters.get(use.id)) is None:
            return contract
        return (*contract, adapter)

    def contract(self, use: TypeUseBinding | None) -> object:
        """Return a use's codec binding and the normalized schema at its site, or None without a schema."""
        if use is None or use.schema is None:
            return None
        return (self.bindings.get(use.id), self.wire.schema(use.schema)[1])


def _plan(part: PartSpec) -> tuple[str, bool, bool, bool, tuple[str, ...], ParameterPlan | None]:
    """Return a part's name, whether it repeats, holds files, or is required, and its encoding's media and style."""
    plan = part.plan
    return plan.name, plan.repeated, plan.file, plan.required, plan.content_types, plan.style


def _digest(value: object) -> str:
    return sha256(canonical_bytes(_projection(value)))


def _is_sequence(value: object) -> TypeIs[tuple[object, ...] | list[object]]:
    return isinstance(value, (tuple, list))


def _is_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _projection(value: object) -> JSONValue:
    """Project a contract value into canonical JSON: records become objects of their fields."""
    if _is_sequence(value):
        return [_projection(item) for item in value]
    if _is_mapping(value):
        return {str(key): _projection(item) for key, item in value.items()}
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "kind": type(value).__name__,
            **{item.name: _projection(getattr(value, item.name)) for item in fields(value)},
        }
    return checked_scalar(value)
