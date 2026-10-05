"""Render one target's `_generated/model_bindings.py` and its public `model_codecs` module."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from typing_extensions import TypeIs

from datamodel_code_generator._codec_type_source import Namespace, TypeSource
from datamodel_code_generator._python_layout import Doc, Group, layout
from datamodel_code_generator._runtime.model_codecs.context import CodecContext
from datamodel_code_generator._target_contract import OperationId

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

    from datamodel_code_generator._openapi_codec_plan import CodecPlan
    from datamodel_code_generator._openapi_wire_plan import WirePlan
    from datamodel_code_generator._runtime.model_codecs.bindings import UseBinding
    from datamodel_code_generator._runtime.model_codecs.context import Direction, Surface
    from datamodel_code_generator._target_contract import FinalPythonType, GeneratedTypeContractBatch, TypeUseId

_RUNTIME: Final = "datamodel_code_generator._runtime.model_codecs."
_WIDTH: Final = 120
_RUNTIME_NAMES: Final = (
    "ArrayNode",
    "CodecContext",
    "DirectionalView",
    "EnvelopeOutboundCodec",
    "FieldBinding",
    "LeafNode",
    "MapNode",
    "ModelBinding",
    "ModelNode",
    "NativeOutboundCodec",
    "PydanticModelCodec",
    "SchemaBundle",
    "SchemaPatch",
    "SchemaResource",
    "StructuralModelCodec",
    "TupleNode",
    "UnionNode",
    "UseBinding",
    "freeze_wire",
)
_CODECS: Final = {
    "pydantic_v2.BaseModel": ("pydantic_v2", "PydanticModelCodec"),
    "pydantic_v2.dataclass": ("pydantic_v2", "PydanticModelCodec"),
    "dataclasses.dataclass": ("structural", "StructuralModelCodec"),
    "typing.TypedDict": ("structural", "StructuralModelCodec"),
    "msgspec.Struct": ("structural", "StructuralModelCodec"),
}
_OWN_NAMES: Final = ("Final", "Mapping", "annotations", "cache", "_resources", "_request_view", "_response_view")
_USE_PREFIXES: Final = ("codec", "CONTEXT", "outbound")
_PUBLIC: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("bindings", ("BackendId", "ConverterStrategy", "NativeKind")),
    ("context", ("CodecContext",)),
    (
        "errors",
        (
            "CodecBindingError",
            "CodecConfigurationError",
            "CodecError",
            "CodecResourceLimitError",
            "ModelProjectionError",
            "NativeIssue",
            "NativeValidationError",
            "ParameterEncodingError",
            "WireIssue",
            "WireValidationError",
        ),
    ),
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
    ("wire", ("JSONValue", "WireValue")),
)


def _is_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _is_tuple(value: object) -> TypeIs[tuple[object, ...]]:
    return isinstance(value, tuple)


def _json(value: object) -> Doc:
    match value:
        case _ if _is_mapping(value):
            return Group("{", tuple((f"{key!r}: ", _json(item)) for key, item in value.items()), "}")
        case _ if _is_tuple(value):
            return Group("[", tuple(("", _json(item)) for item in value), "]")
        case _:
            pass
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
            case _ if dataclasses.is_dataclass(value) and not isinstance(value, type):
                return self.record(value)
            case _:
                pass
        return repr(value)


@dataclass(frozen=True, slots=True)
class UseAccessors:
    """The generated names that serve one planned use."""

    use: TypeUseId
    codec: str
    context: str


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
        self.model_bindings = {model.symbol: model for _, binding in plan.bindings for model in binding.models}
        self.model_index = {symbol: index for index, symbol in enumerate(self.model_bindings)}
        self.typed_models: dict[tuple[str, ...], int] = {}
        for _, binding in plan.bindings:
            if sum(model.native_kind == "typed_dict" for model in binding.models) > 1:
                self.typed_models.setdefault(tuple(model.symbol for model in binding.models), len(self.typed_models))
        count = len(plan.bindings)
        self.namespace = Namespace((
            *_RUNTIME_NAMES,
            *_OWN_NAMES,
            *(f"{prefix}_{index}" for prefix in _USE_PREFIXES for index in range(count)),
            *(f"_model_{index}" for index in range(len(self.model_bindings))),
            *(f"_types_{index}" for index in range(len(self.typed_models))),
            *(f"{direction}_bundle" for direction in ("request", "response")),
        ))
        self.source = TypeSource(self.namespace, dict(plan.imports))
        self.records = _Records()
        self.directions: set[Direction] = set()

    def render(self) -> RenderedBindings:
        sections: list[str] = []
        accessors = tuple(
            self.use(index, use, binding, sections) for index, (use, binding) in enumerate(self.plan.bindings)
        )
        head = [
            *((self.resources(),) if self.directions else ()),
            *self.views(),
            *self.bundles(),
            *self.model_functions(),
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
        codec, body = self.codec(binding, runtime)
        sections.append(_function(f"codec_{index}", f"{codec}[{static}]", body, _description(use)))
        return UseAccessors(use, f"codec_{index}", f"CONTEXT_{index}")

    def codec(self, binding: UseBinding, runtime: str) -> tuple[str, Group]:
        codec = self.records.name(*_CODECS[binding.backend])
        symbols = tuple(model.symbol for model in binding.models)
        index = self.typed_models.get(symbols)
        return codec, Group(
            f"{codec}(",
            (
                ("", self.use_binding(binding)),
                ("", runtime),
                ("", self.model_types(symbols) if index is None else f"_types_{index}()"),
                ("", f"{binding.direction}_bundle"),
            ),
            ")",
        )

    def model_types(self, symbols: tuple[str, ...]) -> Group:
        return Group("{", tuple((f"{symbol!r}: ", self.symbol(symbol)) for symbol in symbols), "}")

    def symbol(self, key: str) -> str:
        module, _, name = key.partition(":")
        return f"{self.namespace.module(module)}.{name}"

    def use_binding(self, binding: UseBinding) -> Doc:
        models = tuple(("", f"_model_{self.model_index[model.symbol]}()") for model in binding.models)
        return self.records.record(binding, models=Group("(", models, ")", ","))

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
            items: tuple[tuple[str, Doc], ...] = (("", "_resources()"), ("", f"_{direction}_view()"))
            bundle = self.records.name("schema", "SchemaBundle")
            functions.append(_function(f"{direction}_bundle", bundle, Group(f"{bundle}(", items, ")")))
        return functions

    def model_functions(self) -> list[str]:
        """Return each model's binding, then each mapping of several TypedDict classes typed as a mapping of types.

        mypy may join the classes of such a dict display into a constructor type that rejects its own entries.
        """
        return [
            *(
                _function(f"_model_{index}", self.records.name("bindings", "ModelBinding"), self.records.record(model))
                for index, model in enumerate(self.model_bindings.values())
            ),
            *(
                _function(f"_types_{index}", "Mapping[str, type]", self.model_types(symbols))
                for symbols, index in self.typed_models.items()
            ),
        ]

    def module(self, sections: list[str]) -> str:
        header = [
            '"""Model codec bindings of this generated package; regenerate them instead of editing."""',
            "",
            "from __future__ import annotations",
            "",
            *(("from collections.abc import Mapping",) if self.typed_models else ()),
            *(("from functools import cache",) if sections else ()),
            *(("from typing import Final",) if self.plan.bindings else ()),
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


def render_model_codecs() -> str:
    """Render the public `model_codecs` module that re-exports the codec types and protocols."""
    names: dict[str, list[str]] = {module: list(items) for module, items in _PUBLIC}
    lines = [
        '"""Public model codec values, errors, and records of this generated package."""',
        "",
        *(_import(f"._runtime.model_codecs.{module}", sorted(items)) for module, items in sorted(names.items())),
        "",
        "__all__ = [",
        *(f"    {name!r}," for name in sorted(name for items in names.values() for name in items)),
        "]",
    ]
    return "\n".join(lines) + "\n"
