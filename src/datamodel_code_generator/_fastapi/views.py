"""Project a planned server into its frozen context: operations, routers, models, and their finalized types."""

from __future__ import annotations

from itertools import starmap
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._fastapi.context import (
    ArgumentView,
    FastAPIContext,
    FastAPIProjectionView,
    HeaderView,
    ImportSpec,
    MediaView,
    ModelSymbolView,
    NativeDeclarationView,
    OperationView,
    PathSlotView,
    PrimaryResponseView,
    RenderType,
    RequestView,
    ResponseView,
    RouterView,
    SourceView,
)
from datamodel_code_generator._fastapi.plan import Default
from datamodel_code_generator._fastapi.render import Module
from datamodel_code_generator._fastapi.routes import tags
from datamodel_code_generator._generation_contract import GeneratedSymbolType, LiteralScalar
from datamodel_code_generator._openapi_codec_plan import artifact_module
from datamodel_code_generator._runtime.model_codecs.unset import UNSET, Unset
from datamodel_code_generator._runtime.model_codecs.wire import checked_wire, freeze_wire

if TYPE_CHECKING:
    from collections.abc import Callable

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._fastapi.context import RenderKind
    from datamodel_code_generator._fastapi.plan import (
        Argument,
        BodySpec,
        Decision,
        HeaderSpec,
        MediaSpec,
        NativeField,
        OperationSpec,
        ResponseSpec,
    )
    from datamodel_code_generator._fastapi.render import ServerRenderer
    from datamodel_code_generator._generation_contract import FrozenLiteral, SourceLocation, TypeUseBinding, TypeUseId
    from datamodel_code_generator._runtime.model_codecs.wire import WireValue

_PRINCIPAL: Final = ("_runtime.server.security", "PrincipalT")


class ContextBuilder:
    """Build the context of one planned server, spelling every type once with absolute imports."""

    def __init__(self, renderer: ServerRenderer, request: TargetRequest) -> None:
        """Keep the renderer that spells types and the request whose documents name source locations."""
        self.renderer = renderer
        self.request = request
        self.package = request.config.package
        self.symbols = {symbol.id: symbol for symbol in request.batch.symbols}

    def context(self) -> FastAPIContext:
        """Return the context of the plan, with no extras or imports yet."""
        plan = self.renderer.plan
        return FastAPIContext(
            info=MappingProxyType(dict(plan.info)),
            operations=tuple(self.operation(spec) for spec in plan.operations),
            routers=tuple(
                RouterView(
                    key=group.key,
                    file_stem=group.stem,
                    primary_tag=group.primary_tag,
                    operations=tuple(spec.key for spec in group.operations),
                )
                for group in plan.groups
            ),
            models=tuple(
                ModelSymbolView(
                    binding_id=f"{module}:{symbol.name}",
                    kind=symbol.kind,
                    module=module,
                    name=symbol.name,
                    path="/".join(symbol.artifact.relative_path),
                )
                for symbol in self.request.batch.symbols
                if symbol.artifact is not None and (module := artifact_module(symbol.artifact))
            ),
            imports=(),
        )

    def operation(self, spec: OperationSpec) -> OperationView:
        """Return the view of one planned operation."""
        contract = spec.contract
        facts = dict(contract.facts)
        primary = spec.primary
        payload = f"{spec.pascal}ResponsePayload"
        return OperationView(
            key=spec.key,
            source=self.source(contract.id.use_site),
            declaration=self.source(contract.declaration.location),
            method=contract.method,
            path=contract.path,
            route_path=spec.route.route_path,
            operation_id=_text(facts.get("operationId")) if contract.explicit_operation_id else None,
            python_name=spec.python_name,
            handler_mode=spec.mode,
            summary=_text(facts.get("summary")),
            description=_text(facts.get("description")),
            deprecated=_flag(facts.get("deprecated")),
            tags=tags(contract),
            group_key=spec.group,
            arguments=tuple(self.argument(spec, argument) for argument in spec.arguments),
            request=None if spec.body is None else self.request_view(spec, spec.body),
            responses=tuple(self.response(response) for response in spec.responses),
            primary_response=None
            if primary is None
            else PrimaryResponseView(
                status_code=primary.status, media_type=None if primary.media is None else primary.media.media_type
            ),
            registration_status=spec.registration_status,
            primary_return_type=None
            if primary is None
            else self.render_type(
                lambda module: " | ".join(self.renderer.bare(module, spec)),
                "result",
                None if primary.media is None or primary.media.use is None else primary.media.use.id,
            ),
            response_payload_type=self.render_type(lambda module: module.local("responses", payload), "result"),
            response_payload_alias=payload,
            response_codecs_name=f"{spec.pascal}ResponseCodecs",
            handler_return_type=self.render_type(
                lambda module: " | ".join(self.renderer.results(module, spec)), "result"
            ),
            security=None if spec.security is None else spec.security.requirements,
            projections=tuple(self.projection(decision) for decision in spec.decisions()),
            path_slots=tuple(
                PathSlotView(wire_name=slot.wire_name, slot=slot.slot, occurrence=slot.occurrence)
                for slot in spec.route.slots
            ),
        )

    def argument(self, spec: OperationSpec, argument: Argument) -> ArgumentView:
        """Return the view of one keyword the handler receives."""
        parameter = argument.parameter
        body = spec.body
        use = _argument_use(argument, body)
        native = argument.native
        default = _default(argument)
        return ArgumentView(
            python_name=argument.name,
            wire_name=argument.wire_name,
            location=argument.location,
            required=argument.required,
            default_present=not isinstance(default, Unset),
            default_value=None if isinstance(default, Unset) else default,
            nullable=self.nullable(use),
            type=self.render_type(
                lambda module: self.renderer.surface(
                    module, spec, argument, module.local(*_PRINCIPAL) if argument.kind == "principal" else ""
                ),
                _kind(argument),
                None if argument.kind == "native" or use is None else use.id,
            ),
            source_pointer=_source_pointer(spec, argument),
            is_request=argument.kind == "request",
            is_principal=argument.kind == "principal",
            is_file=native is not None and native.api == "File",
            parameter_codec_id=self.binding_id(parameter.use)
            if parameter is not None and native is None and parameter.use is not None
            else None,
            bound_type=None if use is None else self.bound(use),
            projection=self.argument_projection(argument, body),
            native_declaration=None if native is None else _native(native),
        )

    def argument_projection(self, argument: Argument, body: BodySpec | None) -> FastAPIProjectionView | None:
        """Return how a parameter or body argument is handled; injected arguments have no projection."""
        match argument.kind:
            case "native" | "adapter" if argument.parameter is not None:
                return self.projection(argument.parameter.decision)
            case "native" | "body" if body is not None:
                return self.projection(body.decision)
            case _:
                pass
        return None

    def request_view(self, spec: OperationSpec, body: BodySpec) -> RequestView:
        """Return the view of an operation's request body."""
        raw = body.decision.transport == "raw_request"
        return RequestView(
            required=body.required,
            media=tuple(self.media(media) for media in body.media),
            body_mode="request" if raw else "typed",
            body_projection=self.projection(body.decision),
            source_pointer=_body_pointer(spec),
        )

    def response(self, response: ResponseSpec) -> ResponseView:
        """Return the view of one declared response."""
        return ResponseView(
            status_key=response.status,
            description=_text(dict(response.declaration.facts).get("description")),
            media=tuple(self.media(media) for media in response.media),
            headers=tuple(self.header(header) for header in response.headers),
            source_pointer=response.declaration.use_site.pointer,
        )

    def media(self, media: MediaSpec) -> MediaView:
        """Return the view of one declared media type."""
        use = media.use
        return MediaView(
            media_type=media.media_type,
            schema_id=None if use is None else self.schema_id(use),
            type=None if use is None or use.type is None else self.bound(use),
            source_pointer=media.declaration.use_site.pointer,
        )

    def header(self, header: HeaderSpec) -> HeaderView:
        """Return the view of one declared response header."""
        use = header.use
        return HeaderView(
            name=header.name,
            required=header.required,
            type=None if use is None or use.type is None else self.bound(use),
        )

    def projection(self, decision: Decision) -> FastAPIProjectionView:
        """Return the view of one parameter, body, or primary response decision."""
        documents = self.request.documents
        return FastAPIProjectionView(
            use_ids=tuple(freeze_wire(documents.use(use)) for use in decision.uses),
            site=decision.site,
            transport=decision.transport,
            reason=decision.reason,
            source=None if decision.source is None else self.source(decision.source),
        )

    def bound(self, use: TypeUseBinding) -> RenderType:
        """Return the final type the model generator bound to a type use."""
        value = use.type
        assert value is not None
        return self.render_type(lambda module: module.static(value), "surface", use.id)

    def render_type(self, spell: Callable[[Module], str], kind: RenderKind, use: TypeUseId | None = None) -> RenderType:
        """Spell a type in a fresh module and return it with its binding identity and absolute imports."""
        module = Module((), self.renderer.symbols, level=2)
        value = spell(module)
        binding = None if use is None else self.renderer.use_bindings.get(use)
        return RenderType(
            binding_id=None if binding is None else binding.binding_id,
            schema_id=None if binding is None else binding.schema_id,
            kind=kind if binding is None else binding.native_kind,
            value=value,
            imports=tuple(starmap(self.import_spec, module.namespace.entries())),
        )

    def import_spec(self, module: str, name: str | None, alias: str) -> ImportSpec:
        """Return one import with the package's relative modules made absolute."""
        absolute = f"{self.package}.{module.lstrip('.')}" if module.startswith(".") else module
        return ImportSpec(module=absolute, name=name, alias=None if alias == (name or module) else alias)

    def binding_id(self, use: TypeUseBinding) -> str | None:
        """Return the codec binding of a type use, when the server binds one."""
        binding = self.renderer.use_bindings.get(use.id)
        return None if binding is None else binding.binding_id

    def schema_id(self, use: TypeUseBinding) -> str | None:
        """Return the schema identity of a type use's codec binding, when the server binds one."""
        binding = self.renderer.use_bindings.get(use.id)
        return None if binding is None else binding.schema_id

    def source(self, location: SourceLocation) -> SourceView:
        """Return a source location as its document's persistent URI and the pointer into it."""
        return SourceView(uri=self.request.documents.uris[location.document], pointer=location.pointer)

    def nullable(self, use: TypeUseBinding | None) -> bool:
        """Return whether the model a type use is bound to accepts null."""
        match None if use is None else use.type:
            case GeneratedSymbolType(symbol=symbol):
                return self.symbols[symbol].nullable
            case _:
                pass
        return False


def _body_pointer(spec: OperationSpec) -> str:
    body = spec.contract.request_body
    assert body is not None
    return body.use_site.pointer


def _source_pointer(spec: OperationSpec, argument: Argument) -> str | None:
    match argument.kind:
        case "native" | "adapter" if argument.parameter is not None:
            return argument.parameter.source.pointer
        case "native" | "body" | "media_type":
            return _body_pointer(spec)
        case _:
            pass
    return None


def _argument_use(argument: Argument, body: BodySpec | None) -> TypeUseBinding | None:
    match argument.kind:
        case "native" | "adapter" if argument.parameter is not None:
            return argument.parameter.use
        case "body" if body is not None and len(body.media) == 1:
            return body.media[0].use
        case _:
            pass
    return None


def _kind(argument: Argument) -> RenderKind:
    match argument.kind:
        case "request":
            return "request"
        case "principal":
            return "principal"
        case "media_type":
            return "media_type"
        case "native" if argument.native is not None and argument.native.api == "File":
            return "upload"
        case _:
            pass
    return "surface"


def _default(argument: Argument) -> WireValue | Unset:
    if (native := argument.native) is not None:
        return UNSET if isinstance(native.default, Default) else checked_wire(native.default)
    if (parameter := argument.parameter) is not None:
        return parameter.default
    return UNSET


def _native(native: NativeField) -> NativeDeclarationView:
    return NativeDeclarationView(
        api=native.api,
        alias=native.alias,
        required=native.default is Default.REQUIRED,
        default_kind="required" if native.default is Default.REQUIRED else "literal",
        kwargs=MappingProxyType({key: checked_wire(value) for key, value in native.keywords}),
    )


def _text(value: FrozenLiteral | None) -> str | None:
    return value.value if isinstance(value, LiteralScalar) and isinstance(value.value, str) else None


def _flag(value: FrozenLiteral | None) -> bool:
    return isinstance(value, LiteralScalar) and value.value is True
