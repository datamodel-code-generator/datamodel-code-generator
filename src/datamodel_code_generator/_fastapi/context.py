"""Frozen views of a planned FastAPI server that hooks read and templates receive, and the patches hooks return."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path  # noqa: TC003 - Public annotations support get_type_hints().
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

from datamodel_code_generator._runtime.model_codecs.bindings import NativeKind

if TYPE_CHECKING:
    from datamodel_code_generator._runtime.model_codecs.wire import WireValue

CONTEXT_VERSION: Final = 1

FrozenJSONMap: TypeAlias = "Mapping[str, WireValue]"
RenderKind: TypeAlias = NativeKind | Literal["surface", "request", "principal", "upload", "media_type", "result"]
ArgumentLocation: TypeAlias = Literal[
    "path", "query", "querystring", "header", "cookie", "form", "file", "body", "request", "principal", "media_type"
]


def _empty() -> FrozenJSONMap:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True, kw_only=True)
class ImportSpec:
    """One import: a module, or a name from it, optionally aliased, and optionally only for type checkers."""

    module: str
    name: str | None = None
    alias: str | None = None
    type_checking: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class RenderType:
    """A finalized Python type: its binding and schema when it has them, its kind, expression, and imports.

    The expression spells modules through the aliases of `imports`, which import them absolutely.
    """

    binding_id: str | None
    schema_id: str | None
    kind: RenderKind
    value: str
    imports: tuple[ImportSpec, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceView:
    """Where a value comes from: a document's persistent URI and a pointer into it."""

    uri: str
    pointer: str


@dataclass(frozen=True, slots=True, kw_only=True)
class FastAPIProjectionView:
    """How one parameter, body, or primary response is handled, why, and where the reason comes from."""

    use_ids: tuple[WireValue, ...]
    site: Literal["parameter", "body", "primary_response"]
    transport: Literal["fastapi_native", "codec_adapter", "raw_request"]
    reason: str
    source: SourceView | None


@dataclass(frozen=True, slots=True, kw_only=True)
class NativeDeclarationView:
    """A native FastAPI declaration: its API, alias, requiredness, default kind, and projected FieldInfo keywords."""

    api: Literal["Path", "Query", "Header", "Body", "Form", "File"]
    alias: str | None
    required: bool
    default_kind: Literal["required", "literal", "factory"]
    kwargs: FrozenJSONMap
    media_type: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ArgumentView:
    """One keyword the handler receives, in the fixed argument order."""

    python_name: str
    wire_name: str | None
    location: ArgumentLocation
    required: bool
    default_present: bool
    default_value: WireValue
    nullable: bool
    type: RenderType
    source_pointer: str | None
    is_request: bool
    is_principal: bool
    is_file: bool
    parameter_codec_id: str | None
    bound_type: RenderType | None
    projection: FastAPIProjectionView | None
    native_declaration: NativeDeclarationView | None


@dataclass(frozen=True, slots=True, kw_only=True)
class MediaView:
    """One declared media type of a body or response, and the type of its content when it has a schema."""

    media_type: str
    schema_id: str | None
    type: RenderType | None
    source_pointer: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RequestView:
    """A request body: requiredness, media in declaration order, and how the handler receives it."""

    required: bool
    media: tuple[MediaView, ...]
    body_mode: Literal["typed", "request"]
    body_projection: FastAPIProjectionView
    source_pointer: str


@dataclass(frozen=True, slots=True, kw_only=True)
class HeaderView:
    """One effective declared response header."""

    name: str
    required: bool
    type: RenderType | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ResponseView:
    """One declared response: its status key, description, media, headers, and the payload types it sends."""

    status_key: str
    description: str | None
    media: tuple[MediaView, ...]
    headers: tuple[HeaderView, ...]
    source_pointer: str


@dataclass(frozen=True, slots=True, kw_only=True)
class PrimaryResponseView:
    """The response a bare return value takes."""

    status_code: int
    media_type: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class PathSlotView:
    """One placeholder the route path renames for Starlette."""

    wire_name: str
    slot: str
    occurrence: int


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationView:
    """One selected operation after naming and route planning."""

    key: str
    source: SourceView
    declaration: SourceView
    method: str
    path: str
    route_path: str
    operation_id: str | None
    python_name: str
    handler_mode: Literal["sync", "async"]
    summary: str | None
    description: str | None
    deprecated: bool
    tags: tuple[str, ...]
    group_key: str
    arguments: tuple[ArgumentView, ...]
    request: RequestView | None
    responses: tuple[ResponseView, ...]
    primary_response: PrimaryResponseView | None
    registration_status: int
    primary_return_type: RenderType | None
    response_payload_type: RenderType
    response_payload_alias: str
    response_codecs_name: str
    handler_return_type: RenderType
    security: tuple[tuple[tuple[str, tuple[str, ...]], ...], ...] | None
    servers: tuple[FrozenJSONMap, ...]
    callbacks: tuple[str, ...]
    projections: tuple[FastAPIProjectionView, ...]
    path_slots: tuple[PathSlotView, ...]
    extras: FrozenJSONMap = field(default_factory=_empty)


@dataclass(frozen=True, slots=True, kw_only=True)
class CallbackUseView:
    """One schema use of a callback operation; the server binds no codec to callbacks, so it has no type."""

    schema_id: str | None
    source_pointer: str
    type: RenderType | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CallbackOperationView:
    """One operation of a callback expression: its key, method, declaration, facts, and schema uses."""

    key: str
    method: str
    declaration: SourceView
    operation_id: str | None
    summary: str | None
    description: str | None
    deprecated: bool
    uses: tuple[CallbackUseView, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class CallbackView:
    """One callback expression of an operation or callback, with its operations in declaration order."""

    key: str
    parent_operation_key: str
    name: str
    expression: str
    operations: tuple[CallbackOperationView, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class RouterView:
    """One router group: its key, file stem, membership tag, and operation keys in order."""

    key: str
    file_stem: str
    primary_tag: str | None
    operations: tuple[str, ...]
    extras: FrozenJSONMap = field(default_factory=_empty)


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelSymbolView:
    """One emitted model declaration: its binding identity, kind, and import address."""

    binding_id: str
    kind: str
    module: str
    name: str
    path: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class FastAPIContext:
    """The planned server a hook reads, or a template renders: info, operations, routers, models, and extras."""

    info: FrozenJSONMap
    operations: tuple[OperationView, ...]
    callbacks: tuple[CallbackView, ...]
    routers: tuple[RouterView, ...]
    models: tuple[ModelSymbolView, ...]
    imports: tuple[ImportSpec, ...]
    extras: FrozenJSONMap = field(default_factory=_empty)
    context_version: Literal[1] = CONTEXT_VERSION


@dataclass(frozen=True, slots=True, kw_only=True)
class FastAPIContextPatch:
    """The changes one hook asks for: operation order, names, modes, extras, and imports; None leaves order as is."""

    operation_order: tuple[str, ...] | None = None
    operation_names: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    router_names: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    handler_modes: Mapping[str, Literal["sync", "async"]] = field(default_factory=lambda: MappingProxyType({}))
    operation_extras: Mapping[str, FrozenJSONMap] = field(default_factory=lambda: MappingProxyType({}))
    router_extras: Mapping[str, FrozenJSONMap] = field(default_factory=lambda: MappingProxyType({}))
    extras: FrozenJSONMap = field(default_factory=_empty)
    imports: tuple[ImportSpec, ...] = ()


FastAPIHook: TypeAlias = Callable[[FastAPIContext], FastAPIContextPatch]


@dataclass(frozen=True, slots=True, kw_only=True)
class HookReference:
    """A hook named by an importable module or a Python file, and the callable to call in it."""

    module: str | None = None
    file: Path | None = None
    callable: str = "transform"
