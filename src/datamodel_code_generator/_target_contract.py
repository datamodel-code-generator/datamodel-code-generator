"""Immutable values that bind target operations to emitted models, without a parser or model graph."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, NewType, TypeAlias

if TYPE_CHECKING:
    from decimal import Decimal

    from datamodel_code_generator._generation_contract import AttemptId
    from datamodel_code_generator._python_type_binding import BoundPythonType
    from datamodel_code_generator.imports import Import
    from datamodel_code_generator.types import DataType

SourceDocumentId = NewType("SourceDocumentId", int)
GraphObjectId = NewType("GraphObjectId", int)
SymbolId = NewType("SymbolId", int)


@dataclass(frozen=True, slots=True)
class FieldSlot:
    """Identify an actual declaring field independently from inheriting consumers."""

    attempt: AttemptId
    symbol: SymbolId
    field: GraphObjectId
    index: int
    name: str


Direction: TypeAlias = Literal["request", "response", "neutral"]
TypeUseRole: TypeAlias = Literal[
    "schema",
    "parameter",
    "request_body",
    "response_body",
    "response_header",
    "request_encoding_header",
    "response_encoding_header",
]


@dataclass(frozen=True, slots=True)
class SourceDocument:
    """Identify a document read by the actual loader within one attempt."""

    id: SourceDocumentId
    uri: str


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """Keep an original declaration or use pointer separate from model names."""

    document: SourceDocumentId
    pointer: str
    role: Literal["declaration", "use", "schema"]


@dataclass(frozen=True, slots=True)
class DeclarationId:
    """Identify the actual declaration, including its declaring document."""

    location: SourceLocation


@dataclass(frozen=True, slots=True)
class OperationId:
    """Identify an operation by its root use site, retaining callback ancestry."""

    use_site: SourceLocation
    kind: Literal["path", "webhook", "callback"]
    parent: OperationId | None = None
    callback_expression: SourceLocation | None = None


@dataclass(frozen=True, slots=True)
class TypeUseId:
    """Distinguish source occurrences even when their final Python type is shared."""

    owner: OperationId | SourceLocation
    role: TypeUseRole
    use_site: SourceLocation
    schema_site: SourceLocation
    declaration: DeclarationId
    direction: Direction
    projection: Literal["value", "item_stream_array"] = "value"
    location: str | None = None
    name: str | None = None
    status: str | None = None
    media: str | None = None


@dataclass(frozen=True, slots=True)
class LiteralScalar:
    """Retain a builtin literal's exact type, including the bool/int distinction."""

    kind: Literal["none", "bool", "int", "float", "str", "bytes", "decimal"]
    value: bool | int | float | str | bytes | Decimal | None


@dataclass(frozen=True, slots=True)
class LiteralSequence:
    """Freeze a literal container without conflating its Python constructor."""

    kind: Literal["list", "tuple", "set", "frozenset"]
    items: tuple[FrozenLiteral, ...]


@dataclass(frozen=True, slots=True)
class LiteralMapping:
    """Keep ordered literal key/value pairs without retaining a mutable mapping."""

    entries: tuple[tuple[FrozenLiteral, FrozenLiteral], ...]


FrozenLiteral: TypeAlias = LiteralScalar | LiteralSequence | LiteralMapping


@dataclass(frozen=True, slots=True)
class SourceExpression:
    """Retain unexecuted source explicitly supplied by an existing code producer."""

    text: str


@dataclass(frozen=True, slots=True)
class ImportedExpression:
    """Preserve an existing runtime expression's import identity and source parts."""

    import_: Import
    prefix: str
    suffix: str


TypeArgument: TypeAlias = FrozenLiteral | SourceExpression | ImportedExpression
HintPart: TypeAlias = "str | SymbolId | Import"


@dataclass(frozen=True, slots=True)
class HintText:
    """One spelling of a model type: its text, with each generated model and imported name left for a module to name.

    A part is text, a generated symbol, or an imported name by its identity. `imports` are the names the text spells
    as they are, such as the typing constructs the model generator writes.
    """

    parts: tuple[HintPart, ...]
    imports: tuple[Import, ...] = ()


@dataclass(frozen=True, slots=True)
class ModelHint:
    """A model type as the model generator spells it.

    `annotation` validates like the model's own annotation, and `static` is what type checkers read: a constrained
    scalar as its base type, and a union without its discriminator.
    """

    annotation: HintText
    static: HintText


@dataclass(frozen=True, slots=True)
class GeneratedSymbolType:
    """Stop traversal of generated model references at the final symbol identity."""

    symbol: SymbolId


@dataclass(frozen=True, slots=True)
class BuiltinType:
    """Name one recognized Python builtin independently of its rendered spelling."""

    name: Literal[
        "bool", "bytes", "complex", "float", "int", "str", "object", "list", "set", "frozenset", "dict", "tuple"
    ]


@dataclass(frozen=True, slots=True)
class NoneType:
    """Represent the null type without confusing it with unavailable projection."""


@dataclass(frozen=True, slots=True)
class ImportedType:
    """Keep the actual immutable Import, including independent alias ownership."""

    import_: Import
    qualified_suffix: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BoundType:
    """Reuse an existing immutable semantic Python annotation without reparsing it."""

    binding: BoundPythonType
    hint: ModelHint | None = dataclasses.field(default=None, compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class GenericType:
    """Preserve ordered container arguments and fixed versus variadic tuple shape."""

    base: TypeView
    arguments: tuple[TypeView, ...]
    tuple_form: Literal["not_tuple", "fixed"] = "not_tuple"
    hint: ModelHint | None = dataclasses.field(default=None, compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class UnionDiscriminator:
    """The discriminator a union's schema declares: its wire property, and the source of the model each value names.

    A source is the path of the schema a model is generated from, as `FinalModelSymbol.source` holds it.
    """

    property_name: str
    mapping: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class UnionType:
    """Retain the final engine's ordered member types and ordering policy, and the discriminator its schema declares.

    The discriminator is metadata of this occurrence, so it takes no part in comparing types.
    """

    members: tuple[TypeView, ...]
    preserve_order: bool
    discriminator: UnionDiscriminator | None = dataclasses.field(default=None, compare=False)
    hint: ModelHint | None = dataclasses.field(default=None, compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class GeneratedEnumMember:
    """Bind an actual enum member field to its final declaring symbol."""

    symbol: SymbolId
    field: GraphObjectId
    name: str


@dataclass(frozen=True, slots=True)
class LiteralType:
    """Keep type-sensitive literals and actual enum member identities."""

    values: tuple[LiteralScalar | GeneratedEnumMember, ...]
    hint: ModelHint | None = dataclasses.field(default=None, compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class ConstructorType:
    """Preserve a constrained type constructor and its actual ordered arguments."""

    callable: ImportedType
    keywords: tuple[tuple[str, TypeArgument], ...]
    hint: ModelHint | None = dataclasses.field(default=None, compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class MetadataCall:
    """Keep a type metadata constructor without invoking it or normalizing its values."""

    import_: Import
    keywords: tuple[tuple[str, TypeArgument], ...]


@dataclass(frozen=True, slots=True)
class AnnotatedType:
    """Retain metadata layer placement around an already projected Python type."""

    base: TypeView
    metadata: tuple[MetadataCall, ...]
    hint: ModelHint | None = dataclasses.field(default=None, compare=False, repr=False)


UnannotatedPythonType: TypeAlias = (
    GeneratedSymbolType
    | BuiltinType
    | NoneType
    | ImportedType
    | BoundType
    | GenericType
    | UnionType
    | LiteralType
    | ConstructorType
)

TypeView: TypeAlias = UnannotatedPythonType | AnnotatedType


TypeProjectionReason: TypeAlias = Literal[
    "BND_TYPE_EXPRESSION_UNSUPPORTED",
    "BND_SYMBOL_NOT_EMITTED",
    "BND_UNRESOLVED_REFERENCE",
    "BND_AMBIGUOUS_REPLACEMENT",
]


@dataclass(frozen=True, slots=True)
class TypeProjection:
    """Keep unsupported final-type forms as finite consumer diagnostics."""

    value: TypeView | None
    reason: TypeProjectionReason | None = None


@dataclass(frozen=True, slots=True)
class ModelArtifactAddress:
    """Locate actual definitions beneath the caller's explicit model-package anchor."""

    result_key: tuple[str, ...] | Literal["single"]
    relative_path: tuple[str, ...]
    model_package: str
    secondary_definitions: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class ModelFieldFacts:
    """Keep adopted model semantics separate from each original wire occurrence.

    `schema_null` says the None the field's annotation accepts comes from its schema's nullability or its own type,
    rather than from an optional fallback or the model configuration.
    """

    required: bool
    nullable: bool | None
    has_default: bool
    type_has_null: bool | None
    read_only: bool
    write_only: bool
    type: TypeView
    backend: BackendFieldFacts
    schema_null: bool


@dataclass(frozen=True, slots=True)
class FinalModelSymbol:
    """Identify a real emitted declaration without retaining its generation graph.

    `source` is the path of the schema the declaration is generated from, and `discriminator` the one the schema of a
    union that reuse replaced with an equal union declares. An enum's `value_types` are the JSON types of its values.
    """

    id: SymbolId
    model: GraphObjectId
    reference: GraphObjectId
    backend: BackendName | None
    kind: Literal["model", "root", "alias", "enum", "custom"]
    name: str
    artifact: ModelArtifactAddress | None
    order: int
    bases: tuple[SymbolId, ...]
    fields: tuple[FieldSlot, ...]
    is_alias: bool
    nullable: bool
    facts: BackendModelFacts | None
    values: tuple[LiteralScalar | None, ...] = ()
    source: str = ""
    discriminator: UnionDiscriminator | None = None
    value_types: tuple[str, ...] = ()


BindingReason: TypeAlias = Literal[
    "BND_MODEL_SCOPE_REQUIRED",
    "BND_UNRESOLVED_REFERENCE",
    "BND_AMBIGUOUS_REPLACEMENT",
    "BND_TYPE_EXPRESSION_UNSUPPORTED",
    "BND_SYMBOL_NOT_EMITTED",
]


@dataclass(frozen=True, slots=True)
class FieldUseBinding:
    """Join a consumer's wire member to its real declaring slot or explicit exclusion."""

    member_kind: Literal[
        "property",
        "required_only",
        "additional_properties",
        "pattern_properties",
        "root_value",
        "discriminator_synthetic",
    ]
    wire_name: str | None
    consumer: SymbolId
    slot: FieldSlot | None
    model_facts: ModelFieldFacts | None
    schema: SourceLocation | None
    direction: Direction
    exclusion: Literal["read_only", "write_only", "tag"] | None = None


@dataclass(frozen=True, slots=True)
class SchemaSite:
    """A schema's location, and the location its whole-schema references lead to."""

    location: SourceLocation
    target: SourceLocation


@dataclass(frozen=True, slots=True)
class PartSchema:
    """How the schema of a multipart member encodes its parts, by the types and formats it declares.

    A member holds files when it is a binary string, or an array of them; it repeats as an array, one part for each
    item; and its values, or its items', are text when it declares only scalar types and structured when it declares
    an object or an array. `own` is where the member's schema is, and `items` where its items' is when it repeats.
    """

    file: bool
    repeated: bool
    text: bool
    structured: bool
    own: SchemaSite | None = None
    items: SchemaSite | None = None


@dataclass(frozen=True, slots=True)
class PartFacts:
    """How the schema of a multipart body encodes its parts, which its model's types do not say.

    `object` says the schema declares an object, or no type; `members` gives each property's encoding by wire name,
    and `extra` that of any other property: none when the schema allows no others, None when any value.
    """

    object: bool
    members: tuple[tuple[str, PartSchema], ...]
    extra: PartSchema | Literal["closed"] | None


LeafStep: TypeAlias = Literal["items", "values"]


@dataclass(frozen=True, slots=True)
class KindSite:
    """Where the lexical kind of a text leaf is read: the places a type may be bound for it, and where none is reported.

    A place is a schema's location and the steps from the type bound there to the leaf.
    """

    source: SourceLocation
    leaves: tuple[tuple[SourceLocation, tuple[LeafStep, ...]], ...]


@dataclass(frozen=True, slots=True)
class MemberShape:
    """One member of an object written as text: its name, where its kind is read, and whether it repeats.

    A member without a kind site is any string.
    """

    name: str
    kind: KindSite | None = None
    repeated: bool = False


@dataclass(frozen=True, slots=True)
class TextShape:
    """How a schema's values are written as parameter text or as URL-encoded members, by the JSON types it declares.

    A scalar or an array has the kind of its value or items; an object has its properties' members, then any other
    property's, none when it allows no others. `problem` is the location and message of why no builtin encoding
    writes the values.
    """

    shape: Literal["scalar", "array", "object"]
    kind: KindSite | None = None
    members: tuple[MemberShape, ...] = ()
    additional: MemberShape | None = None
    problem: tuple[SourceLocation, str] | None = None


@dataclass(frozen=True, slots=True)
class EncodingFacts:
    """How the schema of a parameter, a header or a form is written as text, as the parser recorded on acquiring it.

    `types` are the JSON types it declares, None when it declares none; `value` is its shape as one parameter value,
    `form` as URL-encoded members, and `members` each property's shape as one parameter value, by the location of the
    property's schema.
    """

    types: frozenset[str] | None
    value: TextShape
    form: TextShape
    members: tuple[tuple[SourceLocation, TextShape], ...] = ()


@dataclass(frozen=True, slots=True)
class TypeUseBinding:
    """Bind one actual source occurrence without inventing an unavailable type.

    `default` is the JSON boolean, number, or string default a parameter's schema declares, `parts` what the
    schema of a multipart body says of its parts, `encoding` how a parameter, header or form schema is written as
    text, and `keywords` the title, description, deprecation, examples and default a parameter's schema declares.
    `argument` spells the type a parameter's argument takes: its type with each alias and root model by the type it
    stands for.
    """

    id: TypeUseId
    state: Literal["bound", "not_generated", "invalid"]
    type: TypeView | None
    reason: BindingReason | None
    members: tuple[FieldUseBinding, ...] = ()
    schema: SourceLocation | None = None
    default: LiteralScalar | None = None
    parts: PartFacts | None = None
    encoding: EncodingFacts | None = None
    keywords: tuple[tuple[str, FrozenLiteral], ...] = ()
    argument: ModelHint | None = None


@dataclass(frozen=True, slots=True)
class SourceReference:
    """Retain a metadata reference and whether it reaches an object of a document that loads."""

    source: SourceLocation
    reference: str
    target: SourceLocation | None
    state: Literal["resolved", "document_not_observed", "pointer_missing", "invalid_pointer", "invalid_target", "cycle"]


@dataclass(frozen=True, slots=True)
class WireDeclaration:
    """Freeze non-schema wire facts while retaining original source/schema locations."""

    kind: Literal[
        "parameter", "request_body", "response", "header", "media", "encoding", "link", "callback", "security_scheme"
    ]
    name: str | None
    declaration: DeclarationId
    use_site: SourceLocation
    facts: tuple[tuple[str, FrozenLiteral], ...]
    schemas: tuple[TypeUseId, ...] = ()
    children: tuple[WireDeclaration, ...] = ()
    references: tuple[SourceReference, ...] = ()


@dataclass(frozen=True, slots=True)
class IgnoredDeclaration:
    """Describe one semantic exclusion at its actual source and original use site."""

    source: SourceLocation
    use_site: SourceLocation
    owner: str
    media: str | None
    wire_name: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class OperationContract:
    """Retain effective ordered wire values independently from parser naming paths."""

    id: OperationId
    declaration: DeclarationId
    method: str
    path: str
    explicit_operation_id: bool
    servers_declared: bool
    order: int
    facts: tuple[tuple[str, FrozenLiteral], ...]
    parameters: tuple[WireDeclaration, ...]
    request_body: WireDeclaration | None
    responses: tuple[WireDeclaration, ...]
    callbacks: tuple[WireDeclaration, ...]
    ignored: tuple[IgnoredDeclaration, ...]


@dataclass(frozen=True, slots=True)
class GeneratedTypeContractBatch:
    """Own one accepted attempt's immutable contracts, with no parser or source mappings.

    `document_facts` are the OpenAPI version, info, tags and servers the root document declares. `hint_type` is the
    model generator's configured type class, which composes model types as the models spell them, and `hint_imports`
    the names its spellings write as they are.
    """

    attempt: AttemptId
    root_selector_document: str
    documents: tuple[SourceDocument, ...]
    operations: tuple[OperationContract, ...]
    type_uses: tuple[TypeUseBinding, ...]
    symbols: tuple[FinalModelSymbol, ...]
    artifacts: tuple[ModelArtifactAddress, ...]
    fields: tuple[FieldUseBinding, ...]
    security_schemes: tuple[WireDeclaration, ...] = ()
    api_scope: bool = False
    document_facts: tuple[tuple[str, FrozenLiteral], ...] = ()
    hint_type: type[DataType] | None = None
    hint_imports: tuple[Import, ...] = ()

    @property
    def openapi(self) -> str:
        """Return the OpenAPI version the root document declares, empty when it declares none."""
        version = next((value for key, value in self.document_facts if key == "openapi"), None)
        return str(version.value) if isinstance(version, LiteralScalar) else ""


BackendName: TypeAlias = Literal["dataclass", "pydantic_dataclass", "pydantic", "typeddict", "msgspec"]
DefaultKind: TypeAlias = Literal[
    "absent", "none", "literal", "expression", "factory", "msgspec_unset", "pydantic_missing", "opaque"
]


@dataclass(frozen=True, slots=True)
class EmittedFieldFacts:
    """Observe constructor syntax without claiming arbitrary callable execution results."""

    emitted: bool
    emitted_default_kind: DefaultKind
    emitted_default_value: FrozenLiteral | SourceExpression | None
    qualifiers: tuple[str, ...]
    constructor_keywords: tuple[tuple[str, FrozenLiteral | SourceExpression], ...]


@dataclass(frozen=True, slots=True)
class KnownBackendValue:
    """Retain a finite declaration, including explicit None and False."""

    value: TypeArgument
    state: Literal["known"] = "known"


@dataclass(frozen=True, slots=True)
class RuntimeBackendValue:
    """Identify an effect that generation must never execute to discover."""

    reason: Literal["expression"]
    state: Literal["runtime"] = "runtime"


@dataclass(frozen=True, slots=True)
class OpaqueBackendValue:
    """Keep unavailable semantics distinct from builtin defaults."""

    reason: Literal["custom_origin", "unsupported_value", "model_policy_required"]
    state: Literal["opaque"] = "opaque"


BackendValue: TypeAlias = KnownBackendValue | RuntimeBackendValue | OpaqueBackendValue


@dataclass(frozen=True, slots=True)
class BackendFieldFacts:
    """Freeze builtin field declarations separately from their runtime effects."""

    backend: BackendName
    emitted: EmittedFieldFacts
    constructor_init: BackendValue


@dataclass(frozen=True, slots=True)
class BackendSetting:
    """Distinguish an omitted declaration from explicit None and unknown presence."""

    name: str
    present: bool | None
    value: BackendValue


@dataclass(frozen=True, slots=True)
class BackendModelFacts:
    """Keep finite adopted model declarations independently of runtime defaults."""

    backend: BackendName
    parameters: tuple[BackendSetting, ...]
    configuration: tuple[BackendSetting, ...]
    functional_typeddict: bool


@dataclass(frozen=True, slots=True)
class ModelArtifact:
    """Retain logical relative paths and bytes from the ordinary emission owner."""

    path: tuple[str, ...]
    content: bytes
    encoding: str = "utf-8"


class UnsupportedBindingValueError(Exception):
    """Report a finite unsupported literal without executing or coercing it."""

    def __init__(self, reason: TypeProjectionReason) -> None:
        """Keep the same diagnostic identity through projection and emitted facts."""
        self.reason: TypeProjectionReason = reason
        super().__init__(reason)
