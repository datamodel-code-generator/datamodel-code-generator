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
class NoneDefaultProvenance:
    """Keep accepted constructor defaults separate from wire-null provenance."""

    emitted_default: Literal["absent", "none", "value", "factory", "missing", "opaque"]
    origin: Literal[
        "synthesized_optional_fallback",
        "schema_default",
        "explicit_model_default",
        "explicit_nullable",
        "runtime_or_opaque",
        "not_applicable",
    ]
    annotation_null_origin: Literal[
        "optional_fallback",
        "schema",
        "model_configuration",
        "preexisting_type",
        "none",
        "opaque",
    ]


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


@dataclass(frozen=True, slots=True)
class GenericType:
    """Preserve ordered container arguments and fixed versus variadic tuple shape."""

    base: FinalPythonType
    arguments: tuple[FinalPythonType, ...]
    tuple_form: Literal["not_tuple", "fixed"] = "not_tuple"


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

    members: tuple[FinalPythonType, ...]
    preserve_order: bool
    discriminator: UnionDiscriminator | None = dataclasses.field(default=None, compare=False)


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


@dataclass(frozen=True, slots=True)
class ConstructorType:
    """Preserve a constrained type constructor and its actual ordered arguments."""

    callable: ImportedType
    keywords: tuple[tuple[str, TypeArgument], ...]


@dataclass(frozen=True, slots=True)
class MetadataCall:
    """Keep a type metadata constructor without invoking it or normalizing its values."""

    import_: Import
    keywords: tuple[tuple[str, TypeArgument], ...]


@dataclass(frozen=True, slots=True)
class AnnotatedType:
    """Retain metadata layer placement around an already projected Python type."""

    base: FinalPythonType
    metadata: tuple[MetadataCall, ...]


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

FinalPythonType: TypeAlias = UnannotatedPythonType | AnnotatedType


TypeProjectionReason: TypeAlias = Literal[
    "BND_TYPE_EXPRESSION_UNSUPPORTED",
    "BND_SYMBOL_NOT_EMITTED",
    "BND_UNRESOLVED_REFERENCE",
    "BND_AMBIGUOUS_REPLACEMENT",
]


@dataclass(frozen=True, slots=True)
class TypeProjection:
    """Keep unsupported final-type forms as finite consumer diagnostics."""

    value: FinalPythonType | None
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
    """Keep adopted model semantics separate from each original wire occurrence."""

    required: bool
    nullable: bool | None
    has_default: bool
    explicit_default_factory: bool
    type_has_null: bool | None
    read_only: bool
    write_only: bool
    alias: str | None
    validation_aliases: tuple[str, ...] | None
    serialization_alias: str | None
    use_serialization_alias: bool
    type: FinalPythonType
    backend: BackendFieldFacts
    none_default_provenance: NoneDefaultProvenance


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
class BindingDiagnostic:
    """Describe one finite binding failure using immutable identities and values."""

    code: BindingReason
    operation: OperationId | None = None
    type_use: TypeUseId | None = None
    source_locations: tuple[SourceLocation, ...] = ()
    details: tuple[tuple[str, str | int | bool | None], ...] = ()


@dataclass(frozen=True, slots=True)
class FieldSourceOrigin:
    """Retain every producer's original occurrence without retaining schema nodes."""

    location: SourceLocation
    relation: str


@dataclass(frozen=True, slots=True)
class FieldUseBinding:
    """Join a consumer's wire member to its real declaring slot or explicit exclusion."""

    origin_state: Literal["known", "unavailable", "ambiguous"]
    origin_reason: str | None
    member_kind: Literal[
        "property",
        "required_only",
        "additional_properties",
        "pattern_properties",
        "root_value",
        "discriminator_synthetic",
    ]
    occurrences: tuple[FieldSourceOrigin, ...]
    wire_name: str | None
    consumer: SymbolId
    slot: FieldSlot | None
    model_facts: ModelFieldFacts | None
    schema: SourceLocation | None
    direction: Direction
    exclusion: Literal["read_only", "write_only", "tag"] | None = None


@dataclass(frozen=True, slots=True)
class TypeUseBinding:
    """Bind one actual source occurrence without inventing an unavailable type."""

    id: TypeUseId
    state: Literal["bound", "not_generated", "invalid"]
    type: FinalPythonType | None
    reason: BindingReason | None
    members: tuple[FieldUseBinding, ...] = ()
    producers: tuple[FieldSlot, ...] = ()
    schema: SourceLocation | None = None


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
    """Own one accepted attempt's immutable contracts, with no parser or source mappings."""

    attempt: AttemptId
    root_selector_document: str
    documents: tuple[SourceDocument, ...]
    operations: tuple[OperationContract, ...]
    type_uses: tuple[TypeUseBinding, ...]
    symbols: tuple[FinalModelSymbol, ...]
    artifacts: tuple[ModelArtifactAddress, ...]
    fields: tuple[FieldUseBinding, ...]
    diagnostics: tuple[BindingDiagnostic, ...]
    security_schemes: tuple[WireDeclaration, ...] = ()
    api_scope: bool = False


BackendName: TypeAlias = Literal["dataclass", "pydantic_dataclass", "pydantic", "typeddict", "msgspec"]
DefaultKind: TypeAlias = Literal[
    "absent", "none", "literal", "expression", "factory", "msgspec_unset", "pydantic_missing", "opaque"
]


@dataclass(frozen=True, slots=True)
class MetaLayer:
    """Locate emitted metadata on an already projected structural type node."""

    node_path: tuple[int, ...]
    ordinal: int
    keywords: tuple[tuple[str, FrozenLiteral | SourceExpression], ...]
    line: int
    column: int


@dataclass(frozen=True, slots=True)
class EmittedFieldFacts:
    """Observe constructor syntax without claiming arbitrary callable execution results."""

    emitted: bool
    emitted_default_kind: DefaultKind
    emitted_default_value: FrozenLiteral | SourceExpression | None
    factory_present: bool
    factory_expression: SourceExpression | None
    unset_default: bool
    unset_type_in_annotation: bool
    null_type_in_annotation: bool
    qualifiers: tuple[str, ...]
    constructor_keywords: tuple[tuple[str, FrozenLiteral | SourceExpression], ...]
    meta_layers: tuple[MetaLayer, ...] = ()


@dataclass(frozen=True, slots=True)
class KnownBackendValue:
    """Retain a finite declaration, including explicit None and False."""

    value: TypeArgument
    state: Literal["known"] = "known"


@dataclass(frozen=True, slots=True)
class RuntimeBackendValue:
    """Identify an effect that generation must never execute to discover."""

    reason: Literal["factory_result", "fields_set", "expression"]
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
    declarations: tuple[tuple[str, BackendValue], ...]
    emitted: EmittedFieldFacts
    constructor_init: BackendValue
    init_var: BackendValue
    kw_only: BackendValue
    factory_result: BackendValue
    fields_set: BackendValue


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
    extra_items_present: bool | None
    extra_items: FinalPythonType | None
    custom_base: bool = False


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
