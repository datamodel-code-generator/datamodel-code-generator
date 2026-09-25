"""Render one target's `_generated/model_bindings.py` and its public `model_codecs` module."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import starmap
from typing import TYPE_CHECKING, Final

from typing_extensions import TypeIs

from datamodel_code_generator._codec_type_source import Namespace, TypeSource
from datamodel_code_generator._generation_contract import OperationId
from datamodel_code_generator._python_layout import Doc, Group, layout
from datamodel_code_generator._runtime.model_codecs.capabilities import AdapterManifest
from datamodel_code_generator._runtime.model_codecs.context import CodecContext
from datamodel_code_generator._runtime.model_codecs.views import SchemaResourceLimits

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

    from datamodel_code_generator._generation_contract import (
        FinalPythonType,
        GeneratedTypeContractBatch,
        TypeUseId,
    )
    from datamodel_code_generator._openapi_codec_adapters import AdapterPlan, BindingViewPlan, SchemaAdapterPlan
    from datamodel_code_generator._openapi_codec_plan import CodecPlan
    from datamodel_code_generator._openapi_wire_plan import ParameterViewPlan, WirePlan
    from datamodel_code_generator._runtime.model_codecs.bindings import UseBinding
    from datamodel_code_generator._runtime.model_codecs.context import Direction, Surface
    from datamodel_code_generator._runtime.model_codecs.registry import SchemaSource

_RUNTIME: Final = "datamodel_code_generator._runtime.model_codecs."
_WIDTH: Final = 120
_RUNTIME_NAMES: Final = (
    "AdapterManifest",
    "AdapterModelCodec",
    "AdapterParameterCodec",
    "ArrayNode",
    "BundleSchemaRegistry",
    "ClientMediaCodecCapabilities",
    "CodecBindingView",
    "CodecCapabilities",
    "CodecContext",
    "CodecFieldView",
    "CodecSourceRef",
    "CodecUseView",
    "DirectionalView",
    "EnvelopeOutboundCodec",
    "FieldBinding",
    "LeafNode",
    "MapNode",
    "ModelBinding",
    "ModelCodecAdapterV1",
    "ModelExportView",
    "ModelNode",
    "NativeOutboundCodec",
    "ParameterCodecAdapterV1",
    "ParameterCodecCapabilities",
    "ParameterPlanView",
    "PydanticModelCodec",
    "RuntimeModelBindingView",
    "SchemaAdapterValidator",
    "SchemaBundle",
    "SchemaCodecAdapterV1",
    "SchemaCodecCapabilities",
    "SchemaPatch",
    "SchemaPlanView",
    "SchemaResource",
    "SchemaResourceLimits",
    "SchemaSource",
    "ServerMediaCodecCapabilities",
    "TupleNode",
    "UnionNode",
    "UseBinding",
    "freeze_wire",
)
_OWN_NAMES: Final = ("Final", "annotations", "cache", "_resources", "_request_view", "_response_view")
_USE_PREFIXES: Final = ("codec", "CONTEXT", "outbound", "parameter", "validator")
_FACTORY_PROTOCOLS: Final = {"parameter": "ParameterCodecAdapterV1", "schema": "SchemaCodecAdapterV1"}
_PUBLIC: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    (
        "adapters",
        (
            "AsyncByteReader",
            "ByteReader",
            "CompiledSchemaValidatorV1",
            "ModelCodecAdapterV1",
            "ParameterCodecAdapterV1",
            "SchemaCodecAdapterV1",
        ),
    ),
    ("bindings", ("BackendId", "ConverterStrategy", "NativeKind")),
    ("capabilities", ("CodecCapabilities", "ParameterCodecCapabilities", "SchemaCodecCapabilities", "WireKind")),
    ("context", ("CodecContext",)),
    (
        "errors",
        (
            "CodecAdapterError",
            "CodecBindingError",
            "CodecConfigurationError",
            "CodecError",
            "CodecResourceLimitError",
            "CodecSelectionError",
            "ModelProjectionError",
            "NativeIssue",
            "NativeValidationError",
            "ParameterEncodingError",
            "WireIssue",
            "WireValidationError",
        ),
    ),
    ("outbound", ("EnvelopeOutboundCodec", "ModelCodec", "NativeOutboundCodec", "OutboundCodec")),
    (
        "parameters",
        (
            "EncodedParameterContribution",
            "FragmentContribution",
            "ParameterFragment",
            "ParameterLocation",
            "QueryStringContribution",
            "RawParameter",
        ),
    ),
    ("unset", ("UNSET", "Unset")),
    ("values", ("DecodedValue", "ModelInput", "ModelValue", "ProjectionIssue")),
    (
        "views",
        (
            "CodecBindingView",
            "CodecFieldView",
            "CodecSourceRef",
            "CodecUseView",
            "ModelExportView",
            "OfflineSchemaRegistry",
            "ParameterPlanView",
            "RuntimeModelBindingView",
            "SchemaPlanView",
            "SchemaResourceLimits",
            "SchemaView",
        ),
    ),
    ("wire", ("JSONValue", "PresenceTree", "WireValue", "freeze_wire", "presence_of", "thaw_wire")),
)
_SURFACE_PUBLIC: Final[dict[Surface, tuple[tuple[str, str], ...]]] = {
    "client": (("capabilities", "ClientMediaCodecCapabilities"),),
    "server": (("adapters", "ServerMediaCodecAdapterV1"), ("capabilities", "ServerMediaCodecCapabilities")),
}


def _is_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _is_tuple(value: object) -> TypeIs[tuple[object, ...]]:
    return isinstance(value, tuple)


def _is_frozenset(value: object) -> TypeIs[frozenset[object]]:
    return isinstance(value, frozenset)


def _json(value: object) -> Doc:
    match value:
        case _ if _is_mapping(value):
            return Group("{", tuple((f"{key!r}: ", _json(item)) for key, item in value.items()), "}")
        case _ if _is_tuple(value):
            return Group("[", tuple(("", _json(item)) for item in value), "]")
    return repr(value)


def _is_default(field: dataclasses.Field[object], value: object) -> bool:
    return field.default is not dataclasses.MISSING and type(field.default) is type(value) and field.default == value


def _import(module: str, names: list[str]) -> str:
    line = f"from {module} import {', '.join(names)}"
    if len(line) < _WIDTH:
        return line
    return f"from {module} import (\n" + "".join(f"    {name},\n" for name in names) + ")"


def _description(use: TypeUseId) -> str:
    owner = use.owner.use_site.pointer if isinstance(use.owner, OperationId) else use.owner.pointer
    selectors = "".join(f" {part}" for part in (use.location, use.name, use.status, use.media) if part)
    text = f"Codec of {owner} {use.role} ({use.direction}{selectors})."
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _function(name: str, returns: str, body: Doc, docstring: str = "") -> str:
    lines = f'    """{docstring}"""\n' if docstring else ""
    return f"@cache\ndef {name}() -> {returns}:\n{lines}    return {layout(body, 4, 7, _WIDTH)}\n"


class _Records:
    """Spell runtime records as constructor calls and collect the runtime names the module imports."""

    def __init__(self) -> None:
        self.imports: dict[str, set[str]] = {}

    def name(self, module: str, name: str) -> str:
        self.imports.setdefault(module, set()).add(name)
        return name

    def call(self, module: str, name: str, items: tuple[tuple[str, Doc], ...]) -> Group:
        return Group(f"{self.name(module, name)}(", items, ")")

    def record(self, value: DataclassInstance, **overrides: Doc) -> Group:
        kind = type(value)
        return self.call(
            kind.__module__.removeprefix(_RUNTIME),
            kind.__name__,
            tuple(
                (f"{field.name}=", overrides.get(field.name) or self.doc(getattr(value, field.name)))
                for field in dataclasses.fields(value)
                if field.name in overrides or not _is_default(field, getattr(value, field.name))
            ),
        )

    def doc(self, value: object) -> Doc:
        match value:
            case _ if _is_mapping(value):
                items = tuple((f"{key!r}: ", _json(item)) for key, item in value.items())
                return Group(f"{self.name('wire', 'freeze_wire')}({{", items, "})")
            case _ if _is_tuple(value):
                return Group("(", tuple(("", self.doc(item)) for item in value), ")", ",")
            case _ if _is_frozenset(value):
                return Group("frozenset({", tuple(("", repr(item)) for item in sorted(value, key=repr)), "})")
            case _ if dataclasses.is_dataclass(value) and not isinstance(value, type):
                return self.record(value)
        return repr(value)


@dataclass(frozen=True, slots=True)
class UseAccessors:
    """The generated names that serve one planned use."""

    use: TypeUseId
    codec: str
    context: str
    outbound: str | None = None
    parameter: str | None = None


@dataclass(frozen=True, slots=True)
class RenderedBindings:
    """The source of `_generated/model_bindings.py` and the accessors of every planned use."""

    source: str
    uses: tuple[UseAccessors, ...]


class _Renderer:
    def __init__(self, plan: CodecPlan, wire: WirePlan, batch: GeneratedTypeContractBatch, surface: Surface) -> None:
        self.plan = plan
        self.wire = wire
        self.surface: Surface = surface
        self.types: dict[TypeUseId, FinalPythonType] = {
            use.id: use.type for use in batch.type_uses if use.type is not None
        }
        imports = dict(plan.imports)
        self.field_types = {
            f"{imports[member.consumer]}.{member.slot.name}": facts.type
            for member in batch.fields
            if (facts := member.model_facts) is not None and member.slot is not None and member.consumer in imports
        }
        self.models = {adapter.use: adapter for adapter in plan.adapters if adapter.registration.kind == "model"}
        self.parameters: dict[TypeUseId, tuple[AdapterPlan, ParameterViewPlan]] = {
            adapter.use: (adapter, adapter.parameter) for adapter in plan.adapters if adapter.parameter is not None
        }
        self.schemas: dict[TypeUseId, tuple[AdapterPlan, SchemaAdapterPlan]] = {
            adapter.use: (adapter, adapter.schema) for adapter in plan.adapters if adapter.schema is not None
        }
        self.factories: dict[str, tuple[int, AdapterPlan]] = {}
        for adapter in (adapter for adapter in plan.adapters if adapter.registration.kind != "media"):
            self.factories.setdefault(adapter.registration.name, (len(self.factories), adapter))
        self.model_bindings = {model.symbol: model for _, binding in plan.bindings for model in binding.models}
        self.model_index = {symbol: index for index, symbol in enumerate(self.model_bindings)}
        count = len(plan.bindings)
        self.namespace = Namespace((
            *_RUNTIME_NAMES,
            *_OWN_NAMES,
            *(f"{prefix}_{index}" for prefix in _USE_PREFIXES for index in range(count)),
            *(f"_model_{index}" for index in range(len(self.model_bindings))),
            *(f"_factory_{index}" for index in range(len(self.factories))),
            *(f"{direction}_{kind}" for direction in ("request", "response") for kind in ("bundle", "registry")),
        ))
        self.source = TypeSource(self.namespace, imports)
        self.records = _Records()
        self.directions: set[Direction] = set()
        self.registries: dict[str, dict[str, SchemaSource]] = {}

    def render(self) -> RenderedBindings:
        sections: list[str] = []
        accessors = tuple(
            self.use(index, use, binding, sections) for index, (use, binding) in enumerate(self.plan.bindings)
        )
        head = [
            self.resources(),
            *self.views(),
            *self.bundles(),
            *self.registry_functions(),
            *self.model_functions(),
            *starmap(self.factory, self.factories.values()),
        ]
        return RenderedBindings(self.module([*head, *sections]), accessors)

    def use(self, index: int, use: TypeUseId, binding: UseBinding, sections: list[str]) -> UseAccessors:
        self.directions.add(binding.direction)
        value = self.types[use]
        static, runtime = self.source.static(value), self.source.runtime(value)
        context = CodecContext(
            surface=self.surface,
            direction=binding.direction,
            schema_id=binding.schema_id,
            operation_id=binding.operation_id,
            media_type=binding.media_type,
        )
        sections.append(f"CONTEXT_{index}: Final = {layout(self.records.doc(context), 0, 18, _WIDTH)}\n")
        validator = self.validator(index, use, sections)
        codec, body = self.codec(use, binding, runtime, validator)
        sections.append(_function(f"codec_{index}", f"{codec}[{static}]", body, _description(use)))
        outbound = None
        if not context.inbound:
            outbound = f"outbound_{index}"
            facade = self.records.name(
                "outbound", "EnvelopeOutboundCodec" if binding.projection_mode == "envelope" else "NativeOutboundCodec"
            )
            body = Group(f"{facade}(", (("", f"codec_{index}()"), ("", f"CONTEXT_{index}")), ")")
            sections.append(_function(outbound, f"{facade}[{static}]", body))
        parameter = None
        if (planned := self.parameters.get(use)) is not None:
            parameter = f"parameter_{index}"
            sections.append(
                _function(parameter, self.records.name("adapters", "AdapterParameterCodec"), self.parameter(*planned))
            )
        return UseAccessors(use, f"codec_{index}", f"CONTEXT_{index}", outbound, parameter)

    def codec(self, use: TypeUseId, binding: UseBinding, runtime: str, validator: str | None) -> tuple[str, Group]:
        if (adapter := self.models.get(use)) is None:
            codec = self.records.name("pydantic_v2", "PydanticModelCodec")
            models = tuple((f"{model.symbol!r}: ", self.symbol(model.symbol)) for model in binding.models)
            return codec, Group(
                f"{codec}(",
                (
                    ("", self.use_binding(binding)),
                    ("", runtime),
                    ("", Group("{", models, "}")),
                    ("", f"{binding.direction}_bundle()"),
                    *((("validator=", validator),) if validator else ()),
                ),
                ")",
            )
        codec = self.records.name("adapters", "AdapterModelCodec")
        return codec, Group(
            f"{codec}(",
            (
                ("manifest=", self.manifest(adapter)),
                ("view=", self.runtime_view(adapter, runtime)),
                ("adapter=", f"_factory_{self.factories[adapter.registration.name][0]}()"),
                ("validator=", validator or f"{binding.direction}_bundle().validator({binding.schema_id!r})"),
            ),
            ")",
        )

    def parameter(self, adapter: AdapterPlan, facts: ParameterViewPlan) -> Doc:
        plan = self.records.call(
            "views",
            "ParameterPlanView",
            (
                ("binding=", self.binding_view(adapter.binding)),
                *(
                    (f"{field.name}=", self.records.doc(getattr(facts, field.name)))
                    for field in dataclasses.fields(facts)
                ),
            ),
        )
        return self.records.call(
            "adapters",
            "AdapterParameterCodec",
            (
                ("manifest=", self.manifest(adapter)),
                ("plan=", plan),
                ("adapter=", f"_factory_{self.factories[adapter.registration.name][0]}()"),
            ),
        )

    def validator(self, index: int, use: TypeUseId, sections: list[str]) -> str | None:
        if (planned := self.schemas.get(use)) is None:
            return None
        adapter, schema = planned
        direction = schema.direction
        self.registries.setdefault(direction, {})[schema.source.schema_id] = schema.source
        excluded = frozenset(schema.excluded)
        plan = self.records.call(
            "views",
            "SchemaPlanView",
            (
                ("root=", f"{direction}_registry().get({schema.source.schema_id!r})"),
                ("direction=", repr(direction)),
                ("required_vocabularies=", self.records.doc(schema.required_vocabularies)),
                ("required_keywords=", self.records.doc(schema.required_keywords)),
                ("pattern_dialects=", self.records.doc(schema.pattern_dialects)),
                ("limits=", self.records.record(SchemaResourceLimits())),
            ),
        )
        validator = self.records.name("adapters", "SchemaAdapterValidator")
        body = Group(
            f"{validator}(",
            (
                ("manifest=", self.manifest(adapter)),
                ("plan=", plan),
                ("adapter=", f"_factory_{self.factories[adapter.registration.name][0]}()"),
                ("registry=", f"{direction}_registry()"),
                *((("excluded=", self.records.doc(excluded)),) if excluded else ()),
            ),
            ")",
        )
        sections.append(_function(f"validator_{index}", validator, body))
        return f"validator_{index}()"

    def symbol(self, key: str) -> str:
        module, _, name = key.partition(":")
        return f"{self.namespace.module(module)}.{name}"

    def use_binding(self, binding: UseBinding) -> Doc:
        models = tuple(("", f"_model_{self.model_index[model.symbol]}()") for model in binding.models)
        return self.records.record(binding, models=Group("(", models, ")", ","))

    def manifest(self, adapter: AdapterPlan) -> Doc:
        registration = adapter.registration
        return self.records.record(
            AdapterManifest(
                name=registration.name, import_ref=registration.import_ref, capabilities=registration.capabilities
            )
        )

    def binding_view(self, view: BindingViewPlan) -> Doc:
        direction = view.use.direction
        self.registries.setdefault(direction, {})[view.source.schema_id] = view.source
        return self.records.call(
            "views",
            "CodecBindingView",
            (
                ("binding_id=", repr(view.binding_id)),
                ("use=", self.records.doc(view.use)),
                ("schema=", f"{direction}_registry().get({view.source.schema_id!r})"),
                ("backend=", repr(view.backend)),
                ("native_kind=", repr(view.native_kind)),
                ("native_export=", self.records.doc(view.native_export)),
                ("fields=", self.records.doc(view.fields)),
                ("projection_mode=", repr(view.projection_mode)),
                ("converter_strategy=", repr(view.converter_strategy)),
            ),
        )

    def runtime_view(self, adapter: AdapterPlan, runtime: str) -> Doc:
        fields = tuple(
            (f"{field.field_id!r}: ", self.source.runtime(self.field_types[field.field_id]))
            for field in adapter.binding.fields
        )
        proxy = f"{self.namespace.module('types')}.MappingProxyType({{"
        return self.records.call(
            "views",
            "RuntimeModelBindingView",
            (
                ("binding=", self.binding_view(adapter.binding)),
                ("native_type=", runtime),
                ("native_field_types=", Group(proxy, fields, "})")),
            ),
        )

    def factory(self, index: int, adapter: AdapterPlan) -> str:
        module, _, symbol = adapter.registration.import_ref.partition(":")
        return _function(f"_factory_{index}", self.protocol(adapter), f"{self.namespace.module(module)}.{symbol}()")

    def protocol(self, adapter: AdapterPlan) -> str:
        match adapter.registration.kind:
            case "model":
                native = self.source.static(self.types[adapter.use])
                return f"{self.records.name('adapters', 'ModelCodecAdapterV1')}[{native}]"
            case kind:
                return self.records.name("adapters", _FACTORY_PROTOCOLS[kind])

    def resources(self) -> str:
        returns = f"tuple[{self.records.name('schema', 'SchemaResource')}, ...]"
        return _function("_resources", returns, self.records.doc(self.wire.resources))

    def views(self) -> list[str]:
        return [
            _function(f"_{view.direction}_view", self.records.name("schema", "DirectionalView"), self.records.doc(view))
            for view in self.wire.views
            if view.direction in self.directions
        ]

    def bundles(self) -> list[str]:
        functions: list[str] = []
        for direction in sorted(self.directions):
            patterns = frozenset(
                pattern
                for _, schema in self.schemas.values()
                if schema.direction == direction
                for pattern in schema.patterns
            )
            items: tuple[tuple[str, Doc], ...] = (
                ("", "_resources()"),
                ("", f"_{direction}_view()"),
                *((("adapted_patterns=", self.records.doc(patterns)),) if patterns else ()),
            )
            bundle = self.records.name("schema", "SchemaBundle")
            functions.append(_function(f"{direction}_bundle", bundle, Group(f"{bundle}(", items, ")")))
        return functions

    def registry_functions(self) -> list[str]:
        return [
            _function(
                f"{direction}_registry",
                self.records.name("registry", "BundleSchemaRegistry"),
                self.records.call(
                    "registry",
                    "BundleSchemaRegistry",
                    (("", f"{direction}_bundle()"), ("", self.records.doc(tuple(sources.values())))),
                ),
            )
            for direction, sources in sorted(self.registries.items())
        ]

    def model_functions(self) -> list[str]:
        return [
            _function(f"_model_{index}", self.records.name("bindings", "ModelBinding"), self.records.record(model))
            for index, model in enumerate(self.model_bindings.values())
        ]

    def module(self, sections: list[str]) -> str:
        header = [
            '"""Model codec bindings of this generated package; regenerate them instead of editing."""',
            "",
            "from __future__ import annotations",
            "",
            "from functools import cache",
            "from typing import Final",
            "",
            *self.namespace.imports(),
            "",
            *(
                _import(f".._runtime.model_codecs.{module}", sorted(names))
                for module, names in sorted(self.records.imports.items())
            ),
        ]
        return "\n".join(header) + "\n\n\n" + "\n\n".join(sections)


def render_model_bindings(
    plan: CodecPlan, wire: WirePlan, batch: GeneratedTypeContractBatch, *, surface: Surface
) -> RenderedBindings:
    """Render lazily built bundles, bindings, and typed codec accessors for every planned use of one target."""
    return _Renderer(plan, wire, batch, surface).render()


def render_model_codecs(surface: Surface) -> str:
    """Render the public `model_codecs` module that re-exports one surface's codec types and protocols."""
    names: dict[str, list[str]] = {module: list(items) for module, items in _PUBLIC}
    for module, name in _SURFACE_PUBLIC[surface]:
        names[module].append(name)
    lines = [
        '"""Public model codec values, errors, views, and adapter protocols of this generated package."""',
        "",
        *(_import(f"._runtime.model_codecs.{module}", sorted(items)) for module, items in sorted(names.items())),
        "",
        "__all__ = [",
        *(f"    {name!r}," for name in sorted(name for items in names.values() for name in items)),
        "]",
    ]
    return "\n".join(lines) + "\n"
