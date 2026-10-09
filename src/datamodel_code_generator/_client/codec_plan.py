"""Plan native client codecs from acquired model types, names and discriminator declarations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

from datamodel_code_generator._codec_type_source import type_reason
from datamodel_code_generator._openapi_wire_plan import CodecDiagnostic
from datamodel_code_generator._python_type_annotation import render_python_type_expr
from datamodel_code_generator._target_contract import (
    BoundType,
    BuiltinType,
    GeneratedEnumMember,
    GeneratedSymbolType,
    GenericType,
    ImportedType,
    KnownBackendValue,
    LiteralScalar,
    LiteralType,
    NoneType,
    UnionType,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator

    from datamodel_code_generator._client.model_facts import ModelFacts
    from datamodel_code_generator._openapi_wire_plan import CodecReason, WirePlan
    from datamodel_code_generator._target_contract import (
        GeneratedTypeContractBatch,
        SourceLocation,
        SymbolId,
        TypeUseId,
        TypeView,
        UnionDiscriminator,
    )

CodecBackend: TypeAlias = Literal[
    "pydantic_v2.BaseModel", "pydantic_v2.dataclass", "msgspec.Struct", "dataclasses.dataclass", "typing.TypedDict"
]
CodecKind: TypeAlias = Literal["pydantic", "pydantic_dataclass", "msgspec", "stdlib"]
JSONKind: TypeAlias = Literal["object", "array", "string", "number", "boolean"]
Shape: TypeAlias = "Class | Items | Fixed | Values | Choice | None"
_KINDS: Final[dict[CodecBackend, CodecKind]] = {
    "pydantic_v2.BaseModel": "pydantic",
    "pydantic_v2.dataclass": "pydantic_dataclass",
    "msgspec.Struct": "msgspec",
    "dataclasses.dataclass": "stdlib",
    "typing.TypedDict": "stdlib",
}
_SEQUENCES: Final = {
    "list": "list",
    "typing.List": "list",
    "typing.Sequence": "list",
    "collections.abc.Sequence": "list",
    "set": "set",
    "frozenset": "frozenset",
    "tuple": "tuple",
}
_PAIR: Final = 2
_MAPPINGS: Final = frozenset({"dict", "typing.Mapping", "collections.abc.Mapping"})
_LEAVES: Final = frozenset({
    "bytes",
    "datetime.datetime",
    "datetime.date",
    "datetime.time",
    "decimal.Decimal",
    "uuid.UUID",
})
_PLAIN: Final = frozenset({
    "str",
    "int",
    "float",
    "bool",
    "object",
    "typing.Any",
    "typing_extensions.Any",
    "types.NoneType",
    "list",
    "dict",
})
_ALL_KINDS: Final[tuple[JSONKind, ...]] = ("object", "array", "string", "number", "boolean")
_JSON_KINDS: Final[dict[str | None, tuple[JSONKind, ...]]] = {
    **dict.fromkeys(_MAPPINGS | {"dict"}, ("object",)),
    **dict.fromkeys(_SEQUENCES, ("array",)),
    "bool": ("boolean",),
    "int": ("number",),
    "float": ("number",),
    "decimal.Decimal": ("string", "number"),
}


@dataclass(frozen=True, slots=True)
class Class:
    """An acquired model, enum or supported leaf type."""

    type: TypeView


@dataclass(frozen=True, slots=True)
class Items:
    """The conversion of a sequence's elements and its native container kind."""

    item: Shape
    kind: str = "list"


@dataclass(frozen=True, slots=True)
class Fixed:
    """One conversion for each position of a tuple."""

    items: tuple[Shape, ...]


@dataclass(frozen=True, slots=True)
class Values:
    """The conversion of mapping values."""

    value: Shape


@dataclass(frozen=True, slots=True)
class Choice:
    """Conversions selected solely by JSON kind or a declared discriminator."""

    kinds: tuple[tuple[JSONKind, Shape], ...]
    tag: str | None = None
    tags: tuple[tuple[str, SymbolId], ...] = ()


@dataclass(frozen=True, slots=True)
class Field:
    """A stdlib model field's wire name, Python name and required conversion."""

    wire: str
    name: str
    shape: Shape
    omit_none: bool = False


@dataclass(frozen=True, slots=True)
class Model:
    """A static map of the actual emitted dataclass or TypedDict fields."""

    symbol: SymbolId
    fields: tuple[Field, ...]
    keyed: bool


@dataclass(frozen=True, slots=True)
class CodecUse:
    """One directional use's acquired native type and optional stdlib conversion."""

    use: TypeUseId
    kind: CodecKind
    type: TypeView
    shape: Shape = None


@dataclass(frozen=True, slots=True)
class ClientCodecs:
    """Native codecs in stable use order, static model maps, and generation diagnostics."""

    uses: tuple[CodecUse, ...]
    models: tuple[Model, ...]
    diagnostics: tuple[CodecDiagnostic, ...]
    imports: tuple[tuple[int, str], ...]
    backend: CodecBackend

    def __contains__(self, use: TypeUseId) -> bool:
        """Return whether a selected use has an acquired native codec."""
        return any(item.use == use for item in self.uses)


def _name(value: TypeView) -> str | None:
    name = None
    if isinstance(value, BuiltinType):
        name = value.name
    elif isinstance(value, ImportedType):
        name = f"{value.import_.from_}.{value.import_.import_}"
    return name


def _literal_kind(value: LiteralType) -> str | None:
    kinds = {item.kind if isinstance(item, LiteralScalar) else "enum" for item in value.values}
    return next(iter(kinds)) if len(kinds) == 1 and kinds <= {"str", "int", "none"} else None


class _Planner:
    """Plan the conversions of the emitted models' types, reporting where a use's field has no conversion.

    A union of models is told apart by the discriminator its type records from the union's declaration.
    """

    def __init__(self, backend: CodecBackend, facts: ModelFacts) -> None:
        self.backend, self.facts = backend, facts
        self.symbols, self.members = facts.symbols, facts.members
        self.models: dict[SymbolId, Model] = {}
        self.building: set[SymbolId] = set()
        self.aliases: set[SymbolId] = set()
        self.diagnostics: dict[CodecDiagnostic, None] = {}
        self.source: SourceLocation

    def report(
        self, message: str, source: SourceLocation | None = None, code: CodecReason = "MC_CODEC_UNSUPPORTED"
    ) -> None:
        self.diagnostics[CodecDiagnostic(code, source or self.source, message)] = None

    def label(self, value: TypeView) -> str:
        if isinstance(value, GeneratedSymbolType):
            return self.symbols[value.symbol].name
        if isinstance(value, BoundType):
            return render_python_type_expr(value.binding.expression)
        return _name(value) or type(value).__name__

    def unsupported(self, value: TypeView, schema: SourceLocation | None) -> None:
        self.report(f"The {self.backend} converter has no conversion for the {self.label(value)} type", schema)

    def shape(  # ruff: ignore[too-many-return-statements, too-many-branches]
        self, value: TypeView, schema: SourceLocation | None, declared: UnionDiscriminator | None = None
    ) -> Shape:
        """Return a type's conversion; an alias passes the discriminator it declares to the union it stands for."""
        if isinstance(value, NoneType):
            return None
        if isinstance(value, UnionType):
            return self.union(value, schema, declared or value.discriminator)
        if isinstance(value, GeneratedSymbolType):
            symbol = self.symbols[value.symbol]
            if symbol.kind in {"alias", "root"}:
                if value.symbol in self.aliases:
                    return None
                root = self.root(symbol.id)
                self.aliases.add(value.symbol)
                try:
                    return self.shape(root, schema, declared or symbol.discriminator)
                finally:
                    self.aliases.remove(value.symbol)
            if symbol.kind == "enum":
                return Class(value)
            if symbol.id not in self.models and symbol.id not in self.building:
                self.building.add(symbol.id)
                fields = tuple(
                    Field(
                        field.wire_name,
                        field.name,
                        self.shape(field.type, field.member.schema),
                        field.member.model_facts.backend.emitted.emitted_default_value
                        == LiteralScalar(kind="none", value=None),
                    )
                    for field in self.facts.fields(symbol.id)
                    if field.member.model_facts is not None
                    and field.member.model_facts.backend.constructor_init
                    != KnownBackendValue(LiteralScalar(kind="bool", value=False))
                )
                self.models[symbol.id] = Model(symbol.id, fields, symbol.backend == "typeddict")
                self.building.remove(symbol.id)
            return Class(value)
        if isinstance(value, GenericType):
            if value.tuple_form == "fixed":
                return Fixed(tuple(self.shape(item, schema) for item in value.arguments))
            name = _name(value.base)
            if name in _MAPPINGS and len(value.arguments) == _PAIR:
                key, item = value.arguments
                if _name(key) not in {"str", "object", "typing.Any", "typing_extensions.Any"}:
                    self.report(f"The {self.label(key)} key type has no {self.backend} conversion", schema)
                nested = self.shape(item, schema)
                return None if nested is None else Values(nested)
            if name in _SEQUENCES and len(value.arguments) == 1:
                item = self.shape(value.arguments[0], schema)
                return None if item is None and _SEQUENCES[name] == "list" else Items(item, _SEQUENCES[name])
            return None
        if isinstance(value, LiteralType):
            enums = [item for item in value.values if isinstance(item, GeneratedEnumMember)]
            return Class(GeneratedSymbolType(enums[0].symbol)) if enums else None
        if _name(value) in _PLAIN:
            return None
        if _name(value) in _LEAVES:
            return Class(value)
        self.unsupported(value, schema)
        return None

    def root(self, symbol: SymbolId) -> TypeView:
        return next(member.model_facts.type for member in self.members[symbol] if member.model_facts is not None)

    def flattened(self, members: tuple[TypeView, ...], seen: frozenset[SymbolId] = frozenset()) -> Iterator[TypeView]:
        for member in members:
            if (
                isinstance(member, GeneratedSymbolType)
                and member.symbol not in seen
                and self.symbols[member.symbol].kind == "alias"
            ):
                root = self.root(member.symbol)
                yield from self.flattened(
                    root.members if isinstance(root, UnionType) else (root,), seen | {member.symbol}
                )
            elif not isinstance(member, NoneType):
                yield member

    def kinds(self, value: TypeView) -> tuple[JSONKind, ...]:
        if isinstance(value, GeneratedSymbolType):
            if (symbol := self.symbols[value.symbol]).kind == "enum":
                kinds = {item.kind for item in symbol.values if item is not None}
                return tuple(kind for kind, native in (("string", "str"), ("number", "int")) if native in kinds)
            return ("object",)
        if isinstance(value, GenericType):
            return ("object",) if _name(value.base) in _MAPPINGS else ("array",)
        if isinstance(value, LiteralType):
            native = _literal_kind(value)
            return ("string",) if native == "str" else ("number",) if native == "int" else ()
        name = _name(value)
        if name in {"object", "typing.Any"}:
            return _ALL_KINDS
        return _JSON_KINDS.get(name, ("string",))

    def union(self, value: UnionType, schema: SourceLocation | None, declared: UnionDiscriminator | None) -> Shape:
        members = tuple(dict.fromkeys(self.flattened(value.members)))
        shapes = [(member, self.shape(member, schema)) for member in members]
        if not any(shape is not None for _, shape in shapes):
            return None
        models = [
            member.symbol
            for member, shape in shapes
            if isinstance(member, GeneratedSymbolType)
            and isinstance(shape, Class)
            and (member.symbol in self.models or member.symbol in self.building)
        ]
        model_shapes = [Class(GeneratedSymbolType(symbol)) for symbol in models]
        kinds: dict[JSONKind, Shape] = {}
        for member, shape in shapes:
            for kind in self.kinds(member):
                if (
                    kind in kinds
                    and (shape is not None or kinds[kind] is not None)
                    and not (
                        kind == "object" and len(models) > 1 and shape in model_shapes and kinds[kind] in model_shapes
                    )
                ):
                    self.report(
                        f"The union members are both JSON {kind}s, which the {self.backend} converter cannot tell apart"
                    )
                kinds[kind] = shape
        tag, tags = None, ()
        if len(models) > 1:
            tag, tags = self.discriminator(models, declared)
            kinds.pop("object", None)
        return Choice(tuple(kinds.items()), tag, tags)

    def discriminator(
        self, models: list[SymbolId], declared: UnionDiscriminator | None
    ) -> tuple[str | None, tuple[tuple[str, SymbolId], ...]]:
        """Return a union's discriminator property and the model of each of its values.

        A model takes the values of its literal field of that property, else the values mapped to it, else the last
        segment of the path of the schema it is generated from.
        """
        if declared is None:
            self.report(
                f"The union of {' and '.join(self.symbols[item].name for item in models)} "
                f"needs a declared discriminator for the {self.backend} converter"
            )
            return None, ()
        tag = declared.property_name
        mapped: dict[str, list[str]] = {}
        for name, target in declared.mapping:
            mapped.setdefault(target, []).append(name)
        tags: dict[str, SymbolId] = {}
        for symbol in models:
            field = next((field for field in self.facts.fields(symbol) if field.wire_name == tag), None)
            type_ = None if field is None else field.type
            values = (
                [item.value for item in type_.values if isinstance(item, LiteralScalar) and isinstance(item.value, str)]
                if isinstance(type_, LiteralType)
                else []
            )
            source = self.symbols[symbol].source
            values = values or mapped.get(source, []) or [source.rsplit("/", 1)[-1]]
            for name in values:
                if name in tags and tags[name] != symbol:
                    self.report(f"The discriminator {tag} gives {name} to two union models")
                tags[name] = symbol
        return tag, tuple(tags.items())


def plan_client_codecs(
    batch: GeneratedTypeContractBatch,
    wire: WirePlan,
    backend: CodecBackend,
    uses: Collection[TypeUseId],
    facts: ModelFacts,
) -> ClientCodecs:
    """Plan only selected directional uses, keeping the accepted batch's final model names and modules."""
    planner = _Planner(backend, facts)
    planned = []
    for use in batch.type_uses:
        if use.id not in uses or use.id.direction == "neutral" or use.schema is None:
            continue
        planner.source = use.id.use_site
        if use.state != "bound" or use.type is None:
            planner.report(
                "The use has no generated native type",
                code="BND_MODEL_SCOPE_REQUIRED" if use.state == "not_generated" else use.reason or "MC_BINDING_MISSING",
            )
            continue
        if (reason := type_reason(use.type, facts.imports)) is not None:
            planner.report("The use's final type has no expression a generated module can import", code=reason)
            continue
        kind = _KINDS[backend]
        shape = None
        if kind == "stdlib":
            shape = planner.shape(use.type, use.schema)
        planned.append(CodecUse(use.id, kind, use.type, shape))
    return ClientCodecs(
        tuple(planned),
        tuple(planner.models[key] for key in sorted(planner.models)),
        (*wire.diagnostics, *planner.diagnostics),
        tuple(facts.imports.items()),
        backend,
    )
