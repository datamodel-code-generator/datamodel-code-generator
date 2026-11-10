"""Bind target operations to the emitted models through recorded model replacements.

Only target generation imports this module. A target parser records the two reference redirects of the generation
store, the API schema acquisitions with their engine keys, the objects of each operation where its walk visits them,
and each emitted module's final models. After parsing, `bind_operations` reads the final model graph and these records
once, before disposal, and returns an immutable contract batch; it locates members and nested types through the
parser's origins (``reference.field_origin_of``, ``reference.type_origin_of``) and reads no schema of the loaded
documents.
"""

from __future__ import annotations

from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from decimal import Decimal
from functools import cached_property, partial
from itertools import starmap
from math import isfinite
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal, TypeAlias, cast, get_args
from urllib.parse import ParseResult

from pydantic import ValidationError
from typing_extensions import override

from datamodel_code_generator import Error, SchemaParseError
from datamodel_code_generator._container_kinds import (
    CONTAINER_FLAGS,
    CONTAINERS,
    ITEM_CONTAINERS,
    MAPPINGS,
    SLOT_CLASSES,
    WRITTEN_CONTAINERS,
    given_container_of,
    written_slot,
)
from datamodel_code_generator._document_reader import MISSING, as_mapping, descend, document_statuses, string_key
from datamodel_code_generator._generation_contract import AttemptId
from datamodel_code_generator._json_pointer import reference_tokens
from datamodel_code_generator._module_paths import written_path
from datamodel_code_generator._mro import collect_members, linearize
from datamodel_code_generator._openapi_paths import inherited_security
from datamodel_code_generator._paths import file_uri
from datamodel_code_generator._python_syntax import subscript_text
from datamodel_code_generator._runtime.model_codecs.media import media_kind, normalized
from datamodel_code_generator._site_facts import ANNOTATION_KEYWORDS
from datamodel_code_generator._target_contract import (
    WRAPPER_KINDS,
    BackendFieldFacts,
    BackendModelFacts,
    BackendSetting,
    BoundType,
    BuiltinName,
    BuiltinType,
    ChannelContract,
    ChannelSkip,
    ConstructorType,
    DeclarationId,
    Direction,
    EmittedFieldFacts,
    EncodingFacts,
    FieldSlot,
    FieldUseBinding,
    FinalModelSymbol,
    GeneratedEnumMember,
    GeneratedSymbolType,
    GeneratedTypeContractBatch,
    GenericType,
    GraphObjectId,
    HintText,
    IgnoredDeclaration,
    ImportedExpression,
    ImportedType,
    KindAt,
    KnownBackendValue,
    LiteralMapping,
    LiteralScalar,
    LiteralSequence,
    LiteralType,
    MemberShape,
    MessageContract,
    ModelArtifactAddress,
    ModelFieldFacts,
    ModelHint,
    NoneType,
    OpaqueBackendValue,
    OperationContract,
    OperationId,
    PartFacts,
    PartSchema,
    RuntimeBackendValue,
    SourceDocument,
    SourceDocumentId,
    SourceExpression,
    SourceLocation,
    SourceReference,
    SymbolId,
    TextShape,
    TypeProjection,
    TypeUseBinding,
    TypeUseId,
    UnionDiscriminator,
    UnionType,
    WireDeclaration,
    property_members,
    response_key,
)
from datamodel_code_generator._target_module import TypeComposer
from datamodel_code_generator._target_naming import TargetNames, path_placeholders
from datamodel_code_generator._target_types import (
    admits_none,
    contained,
    is_none,
    kept_values,
    lexical_kind,
    member_kind,
    present,
    sole_present,
    untyped,
    unwrapped,
    wrapper_chain,
)
from datamodel_code_generator.enums import DataclassArguments, OpenAPIVersion
from datamodel_code_generator.imports import IMPORT_ANY, IMPORT_DECIMAL, Import, _import_path, _stored_import
from datamodel_code_generator.model import dataclass as dataclass_model
from datamodel_code_generator.model import msgspec, pydantic_v2, typed_dict
from datamodel_code_generator.model._decisions.enum_kind import json_type
from datamodel_code_generator.model._decisions.nullability import reference_null
from datamodel_code_generator.model._decisions.tag_value import field_source_name
from datamodel_code_generator.model._decisions.union_assembly import WrittenMember, merge_members
from datamodel_code_generator.model.base import (
    UNDEFINED,
    DataModel,
    DataModelFieldBase,
    _has_root_field_constraints,
)
from datamodel_code_generator.model.dataclass import DataModelField as DataclassField
from datamodel_code_generator.model.enum import Enum, IntEnum, StrEnum, get_raw_enum_member_value
from datamodel_code_generator.model.msgspec import DataModelField as MsgspecField
from datamodel_code_generator.model.pydantic_v2 import DataModelField as PydanticField
from datamodel_code_generator.model.pydantic_v2 import dataclass as pydantic_dataclass
from datamodel_code_generator.model.pydantic_v2.base_model import _ANNOTATED_CONSTRAINT_BASES
from datamodel_code_generator.model.pydantic_v2.imports import IMPORT_FIELD, IMPORT_SERIALIZE_AS_ANY
from datamodel_code_generator.model.pydantic_v2.type_alias import TypeAlias as PydanticCompatibleTypeAlias
from datamodel_code_generator.model.pydantic_v2.types import PydanticV2DataType, _writes_serialize_as_any
from datamodel_code_generator.model.type_alias import TypeAlias as TypeAliasModel
from datamodel_code_generator.model.type_alias import TypeAliasTypeBackport, TypeStatement
from datamodel_code_generator.parser.asyncapi import AsyncAPIParser, AsyncAPISchema
from datamodel_code_generator.parser.generation import GenerationStore
from datamodel_code_generator.parser.jsonschema import Discriminator, JsonSchemaObject
from datamodel_code_generator.parser.openapi import HeaderObject, OpenAPIParser, ParameterObject, RequestBodyObject
from datamodel_code_generator.parser.openapi_scope import ApiOpenAPIParser
from datamodel_code_generator.python_literal import PythonCode, PythonRuntimeExpression, stable_repr
from datamodel_code_generator.reference import (
    _RW_DIRECTIONS,
    Reference,
    SchemaLocation,
    _is_special_path,
    _rw_direction,
    _rw_variant_flag,
    _rw_variant_of,
    _rw_variant_path,
    _source_path,
    binary_content_of,
    field_origin_of,
    location_of,
    type_origin_of,
)
from datamodel_code_generator.types import DataType, Types, reference_names_nullable

FieldKind: TypeAlias = Literal[
    "property", "required_only", "additional_properties", "root_value", "discriminator_synthetic"
]

_BACKENDS: Final[dict[type[DataModel], BackendName]] = {
    pydantic_v2.BaseModel: "pydantic",
    pydantic_dataclass.DataClass: "pydantic_dataclass",
    dataclass_model.DataClass: "dataclass",
    typed_dict.TypedDict: "typeddict",
    msgspec.Struct: "msgspec",
}
_ENUMS: Final = frozenset({Enum, IntEnum, StrEnum})
_ALIASES: Final = frozenset({TypeAliasModel, TypeAliasTypeBackport, TypeStatement, PydanticCompatibleTypeAlias})
_ROOTS: Final = frozenset({pydantic_v2.RootModel, pydantic_v2.RootModelTypeAlias})
_BUILTINS: Final[dict[str, BuiltinType]] = {name: BuiltinType(name) for name in get_args(BuiltinName)}
_FILE_TYPES: Final = frozenset({"bytes", "str"})
_FORMS: Final = frozenset({"form", "multipart"})
_TEXT_MEDIA: Final = frozenset({"json", "form", "text"})
_RAW_MEDIA: Final = frozenset({"json", "text", "binary"})
_HEADER_ROLES: Final[frozenset[TypeUseRole]] = frozenset({
    "response_header",
    "request_encoding_header",
    "response_encoding_header",
})
_DOCUMENT_FACTS: Final = ("info", "tags", "servers")
_DEFAULT_KINDS: Final = cast(
    "dict[type, Literal['bool', 'int', 'float', 'str']]", {kind: kind.__name__ for kind in (bool, int, float, str)}
)
_PARAMETER_FACTS: Final = (
    "name",
    "in",
    "description",
    "required",
    "deprecated",
    "allowEmptyValue",
    "style",
    "explode",
    "allowReserved",
    "example",
    "examples",
)
_HEADER_FACTS: Final = tuple(name for name in _PARAMETER_FACTS if name not in {"name", "in"})
"""The facts of a Header Object: a parameter's but its name and location, which its headers map implies."""
_OPERATION_FACTS: Final = (
    "operationId",
    "tags",
    "summary",
    "description",
    "externalDocs",
    "deprecated",
    "security",
    "servers",
)
_METADATA_FACTS: Final = {
    "link": ("operationRef", "operationId", "parameters", "requestBody", "description", "server"),
    "security_scheme": ("type", "description", "name", "in", "scheme", "bearerFormat", "flows", "openIdConnectUrl"),
}
_DATACLASS_PARAMETERS: Final = tuple(DataclassArguments.__annotations__)
_MSGSPEC_PARAMETERS: Final = (
    "tag",
    "tag_field",
    "array_like",
    "forbid_unknown_fields",
    "omit_defaults",
    "kw_only",
    "frozen",
    "rename",
)
_BACKEND_PARAMETERS: Final[dict[BackendName, tuple[str, ...]]] = {
    "dataclass": _DATACLASS_PARAMETERS,
    "pydantic_dataclass": _DATACLASS_PARAMETERS,
    "msgspec": _MSGSPEC_PARAMETERS,
    "typeddict": ("total", "closed"),
}
_PYDANTIC_CONFIGURATION: Final = (
    "extra",
    "strict",
    "validate_by_name",
    "populate_by_name",
    "validate_by_alias",
    "frozen",
    "alias_generator",
    "regex_engine",
)
_SEQUENCES: Final = cast(
    "dict[type, Literal['list', 'tuple', 'set', 'frozenset']]",
    {kind: kind.__name__ for kind in (list, tuple, set, frozenset)},
)
_SLOT: Final = "\x00"
_STYLE: Final = ("use_union_operator", "use_standard_collections", "use_generic_container", "python_version")
_UNCHECKED: Final[frozenset[BackendName | None]] = frozenset({"dataclass", "typeddict"})
_ALONE: Final[dict[str, object]] = {
    **dict.fromkeys((CONTAINER_FLAGS[container] for container in WRITTEN_CONTAINERS), False),
    "is_optional": False,
    "dict_key": None,
    "reference": None,
    "parent": None,
}


class _UnprojectedError(Exception):
    """A type no generated module can spell, with the reason its projection reports."""

    reason: ClassVar[TypeProjectionReason]


class _UnsupportedError(_UnprojectedError):
    """A type expression that the contract values cannot represent."""

    reason: ClassVar[TypeProjectionReason] = "BND_TYPE_EXPRESSION_UNSUPPORTED"


class _LiteralCycleError(_UnsupportedError):
    """A recursive literal, located as its containers unwind."""

    def __init__(self) -> None:
        self.tokens: tuple[str, ...] = ()
        super().__init__()


class _UnservedReferenceError(Error):
    """A reference to a local file that the served source document would publish."""


class MetadataCycleError(Exception):
    """A detected cycle in an API metadata fact at its original source location."""

    def __init__(self, document: str, pointer: str) -> None:
        self.document = document
        self.pointer = pointer
        super().__init__("The input document contains a cyclic mapping or sequence")


class RecordingGenerationStore(GenerationStore):
    """Record the two reference redirects that leave no trace in the final graph."""

    def __init__(self) -> None:
        """Start an attempt with no recorded redirects."""
        super().__init__()
        self.redirects: list[tuple[Reference, Reference, bool]] = []

    def redirect_reference_users(
        self, old_reference: Reference, new_reference: Reference, *, merged: bool = False, keeps_named: bool = False
    ) -> list[DataType]:
        """Record a global redirect, and whether it merges a model, then redirect every user and return them."""
        self.redirects.append((old_reference, new_reference, merged))
        return super().redirect_reference_users(  # ty: ignore[deprecated]
            old_reference, new_reference, merged=merged, keeps_named=keeps_named
        )

    def redirect_model_reference_users(
        self, model: DataModel, models: list[DataModel], new_reference: Reference, *, keeps_named: bool = False
    ) -> None:
        """Record a redirect scoped to some models, which merges a model, then redirect their users."""
        self.redirects.append((model.reference, new_reference, True))
        super().redirect_model_reference_users(  # ty: ignore[deprecated]
            model, models, new_reference, keeps_named=keeps_named
        )


@dataclass(slots=True)
class _WalkedPathItem:
    """A path item under the walk: where the document spells it and the parameters its operations share."""

    declaration: SchemaLocation
    raw: dict[str, YamlValue]
    use_site: SchemaLocation
    origin: SchemaLocation
    shared: dict[int, SchemaLocation] | None = None


@dataclass(frozen=True, slots=True)
class _Selector:
    """A union's declared discriminator: its wire property and each mapped value's resolved reference."""

    property_name: str
    mapping: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _Union:
    """A oneOf or anyOf as the parser parsed it: its members and the discriminator it declares, if any."""

    members: tuple[DataType, ...]
    selector: _Selector | None


@dataclass(slots=True)
class _WalkedHolder:
    """A resolved object of the walk, where the document spells it, and the objects the walk visited in it, in order."""

    declaration: SchemaLocation
    value: dict[str, YamlValue]
    objects: list[_WalkedObject]

    def below(self, kind: ApiObjectKind) -> Iterator[_WalkedObject]:
        """Yield the objects of ``kind`` the walk visited in this one, in order."""
        return (found for found in self.objects if found.kind == kind)


@dataclass(slots=True)
class _WalkedObject(_WalkedHolder):
    """An object of an operation as the walk visited it.

    ``key`` names it in its holder as the document spells it, so a YAML status such as ``200:`` is a number; the other
    names are checked to be text before the bindings read them: a parameter's name, a media type, an encoding's
    property, a header's or a callback's name. ``schemas`` are the schemas the walk acquired in it, with their
    projections; a callback is kept as written, since its metadata follows its references itself.
    """

    kind: ApiObjectKind
    key: Any
    schemas: dict[SchemaLocation, tuple[Projection, ...]]


@dataclass(slots=True)
class _WalkedOperation(_WalkedHolder):
    """An operation as the target parser walked it: the objects the walk visited in it and the parameters in effect."""

    use_site: SchemaLocation
    origin: SchemaLocation
    item: _WalkedPathItem
    parent: int | None
    security: YamlValue
    shared: dict[int, SchemaLocation]
    parameters: list[tuple[SchemaLocation, _WalkedObject]]


class _TargetRecording(OpenAPIParser):
    """Record what binding reads of one target generation attempt, whatever the input kind.

    It records the generation store's two reference redirects, the schema acquisitions with their engine keys, the
    unions, field copies and variant splits the parser makes, and each emitted module's final models. The parser of an
    input kind adds the records of its own walk.
    """

    _generation_store_factory = staticmethod(RecordingGenerationStore.create_with_results)

    def __init__(
        self,
        source: str | Path | list[Path] | ParseResult | dict[str, YamlValue],
        *,
        config: OpenAPIParserConfig | None = None,
    ) -> None:
        """Start with no acquisitions or emitted modules."""
        self.attempt = AttemptId(0)
        self.acquisitions: dict[tuple[SchemaLocation, Projection], str] = {}
        self.module_outputs: list[tuple[ModulePath, tuple[DataModel, ...], Result]] = []
        self.model_imports: set[str] = set()
        self.unions: dict[int, _Union] = {}
        self.copies: dict[int, tuple[DataModelFieldBase, DataModelFieldBase]] = {}
        self.left_out: dict[str, tuple[tuple[DataModelFieldBase, ...], tuple[DataModelFieldBase, ...]]] = {}
        self.schema_records: dict[SchemaLocation, _SchemaRecord] = {}
        self.free_schemas: dict[SchemaLocation, JsonSchemaObject] = {}
        self.object_facts: dict[SchemaLocation | None, _ObjectFacts] = {}
        self.nullable_fields: dict[int, DataModelFieldBase] = {}
        self.document_facts: dict[str, tuple[tuple[str, FrozenLiteral], ...]] = {}
        self.document_versions: dict[str, OpenAPIVersion] = {}
        super().__init__(source, config=config)

    def _record_schema_object(self, reference: Reference, obj: JsonSchemaObject) -> None:
        """Record what a model's schema says of its other and pattern properties, as the parser reads them.

        The other properties are the model's (``_model_additional_properties``), and the schema of the typed ones
        binds model-free. A schema the parser read at no location is recorded under None, which no model's location
        looks up.
        """
        super()._record_schema_object(reference, obj)
        location = obj.schema_location
        if isinstance(additional := obj.additionalProperties, JsonSchemaObject) and location is not None:
            self.free_schemas.setdefault(location.child("additionalProperties"), additional)
        self.object_facts.setdefault(
            location,
            _ObjectFacts(_others_read(self._model_additional_properties(obj)), obj.patternProperties is not None),
        )

    def parse_combined_schema(
        self, name: str, obj: JsonSchemaObject, path: list[str], target_attribute_name: str
    ) -> list[DataType]:
        """Record each combined schema with the discriminator it declares, by the members it parses into.

        Each mapped value, a reference or a schema name, resolves where the union is declared, as the parser
        resolves its references.
        """
        members = super().parse_combined_schema(name, obj, path, target_attribute_name)
        mapping = obj.schema_location and obj.schema_location.child("discriminator", "mapping")
        selector = (
            _Selector(
                declared.propertyName,
                tuple(
                    (value, self._ref_path(self._discriminator_mapping_ref(value, ref, mapping)))
                    for value, ref in (declared.mapping or {}).items()
                ),
            )
            if isinstance(declared := obj.discriminator, Discriminator)
            else None
        )
        union = _Union(tuple(members), selector)
        self.unions.update((id(member), union) for member in members)
        return members

    def _copy_model_field(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, field: DataModelFieldBase, **options: Any
    ) -> DataModelFieldBase:
        """Record the field a copy of a field is of, as inherited fields and read or write variants copy."""
        copied = super()._copy_model_field(field, **options)
        self.copies[id(copied)] = (copied, field)
        return copied

    def _copy_inherited_field(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, field: DataModelFieldBase, inherited_field: DataModelFieldBase, **options: Any
    ) -> DataModelFieldBase | None:
        """Record the inherited field a resolved copy of an inherited field is of."""
        if (copied := super()._copy_inherited_field(field, inherited_field, **options)) is not None:
            self.copies[id(copied)] = (copied, inherited_field)
        return copied

    def _create_variant_model(  # noqa: PLR0913
        self,
        base_reference: Reference,
        suffix: Literal["Request", "Response"],
        model_fields: list[DataModelFieldBase],
        obj: JsonSchemaObject,
        data_model_type_class: type[DataModel],
        *,
        source_fields: Sequence[DataModelFieldBase] = (),
    ) -> None:
        """Record, by the variant's reference key, the split's fields and those of them the variant leaves out."""
        super()._create_variant_model(
            base_reference, suffix, model_fields, obj, data_model_type_class, source_fields=source_fields
        )
        kept = {id(field) for field in model_fields}
        key = self.model_resolver.join_path(tuple(_rw_variant_path(base_reference.path, _rw_direction(suffix))))
        self.left_out[key] = tuple(source_fields), tuple(field for field in source_fields if id(field) not in kept)

    def get_object_field(self, **options: Any) -> DataModelFieldBase:  # pyright: ignore[reportIncompatibleMethodOverride]
        """Record a field whose own schema says it is nullable, which strict nullability alone puts on the field."""
        field = super().get_object_field(**options)
        if isinstance(schema := options.get("field"), JsonSchemaObject) and schema.nullable is True:
            self.nullable_fields[id(field)] = field
        return field

    def nullable(self, field: DataModelFieldBase) -> bool:
        """Return whether the schema a field, or the field it is a copy of, was generated from says it is nullable."""
        while id(field) not in self.nullable_fields:
            if (copy := self.copies.get(id(field))) is None:
                return False
            field = copy[1]
        return True

    def _free(self, declaration: SchemaLocation, projection: Projection, raw: YamlValue, path: list[str]) -> None:
        """Record the schema of a declaration that binds as the parser's model-free type, when no model holds it."""
        if (
            declaration not in self.free_schemas
            and self.model_resolver.references.get(self.acquisitions[declaration, projection]) is None
        ):
            self.free_schemas[declaration] = self._validate_schema_object(raw, path)

    def _generate_module_output(  # noqa: PLR0913, PLR0917
        self,
        ctx: ModuleContext,
        config: ParseConfig,
        contexts: list[ModuleContext],
        forwarder_map: ForwarderMap,
        require_update: Collection[ModelKey],
        future_imports_str: str,
    ) -> Result | None:
        """Record the module's final models and the imports the parser finalized for it once its output exists."""
        result = super()._generate_module_output(
            ctx, config, contexts, forwarder_map, require_update, future_imports_str
        )
        if result is not None:
            self.module_outputs.append((ctx.module, tuple(ctx.models), result))
            self.model_imports.update(_imported_names(self.imports), _imported_names(ctx.imports))
        return result

    def release_records(self) -> None:
        """Drop the recorded graph anchors and borrowed source nodes."""
        self.acquisitions.clear()
        self.module_outputs.clear()
        self.model_imports.clear()
        self.unions.clear()
        self.copies.clear()
        self.schema_records.clear()
        self.free_schemas.clear()
        self.object_facts.clear()
        self.nullable_fields.clear()
        self.document_facts.clear()
        self.document_versions.clear()
        cast("RecordingGenerationStore", self.generation_store).redirects.clear()


class TargetApiOpenAPIParser(_TargetRecording, ApiOpenAPIParser):
    """Record API schema acquisitions and walked operations for one target generation attempt."""

    def __init__(
        self,
        source: str | Path | list[Path] | ParseResult | dict[str, YamlValue],
        *,
        config: OpenAPIParserConfig | None = None,
        serves_source: bool,
    ) -> None:
        """Start with no walked operations."""
        self.serves_source = serves_source
        self.operations: list[_WalkedOperation] = []
        self._walked_items: list[_WalkedPathItem] = []
        self._walked_operations: list[int] = []
        self._visits: list[_WalkedHolder] = []
        self._callback_origin: SchemaLocation | None = None
        super().__init__(source, config=config)
        self._reader = _Reader(self._site_facts, siblings=self._reads_ref_siblings_at)

    def _reads_ref_siblings_at(self, location: SchemaLocation) -> bool:
        """Return whether the models read the keywords beside a ``$ref`` at a location with the schema it names.

        They do in every version, unless --ignore-ref-siblings-by-schema-version reads the ``$ref`` alone where the
        version of the location ignores them (``reads_ref_alone``).
        """
        return not self._ignores_ref_siblings_at(location)

    def _resolve_local_ref_path(self, path: Path, ref: str) -> Path:
        """Refuse a local file that a served source document would publish, before reading it.

        A document read from a URL references no local file; any other document only files inside the input
        directory, after resolving `..` and symbolic links.
        """
        if self.serves_source and path not in self._local_ref_path_cache and (reason := self._unserved(path)):
            msg = f"Blocked unsafe local $ref: {ref}\nReason: the generated server serves the source document, {reason}"
            raise _UnservedReferenceError(msg)
        return super()._resolve_local_ref_path(path, ref)

    def _unserved(self, path: Path) -> str | None:
        """Return why the served document cannot hold a local file, or None when it can."""
        if isinstance(self.source, ParseResult):
            return "which was read from a URL, so it cannot reference a local file."
        if (resolved := path.resolve()).is_relative_to(base := self.base_path.resolve()):
            return None
        return (
            "so a referenced file must be inside the input directory.\n"
            f"Input directory: {base}\nResolved path: {resolved}\n"
            "Move the referenced schemas under the input directory."
        )

    def _walk_path_item(
        self,
        target: Any,
        use_site: SchemaLocation,
        prefix: str,
        global_parameters: list[ApiParameterDeclaration],
    ) -> None:
        """Keep where the document spells the path item while its operations are walked."""
        origin = use_site if (callback := self._callback_origin) is None else callback.child(use_site.tokens[-1])
        self._walked_items.append(_WalkedPathItem(target.declaration, target.value, use_site, origin))
        super()._walk_path_item(target, use_site, prefix, global_parameters)
        self._walked_items.pop()

    def _walk_api_operation(
        self,
        operation: dict[str, YamlValue],
        path: list[str],
        use_site: SchemaLocation,
        prefix: str,
        common: tuple[list[ApiParameterDeclaration], list[ApiParameterDeclaration]],
    ) -> None:
        """Record the operation before its parameters, callbacks and their operations are walked."""
        item = self._walked_items[-1]
        if (shared := item.shared) is None:
            shared = item.shared = {id(entry.target): entry.occurrence for entry in common[0]}
            shared.update(
                (id(entry.target), item.origin.child("parameters", entry.occurrence.tokens[-1])) for entry in common[1]
            )
        walked = _WalkedOperation(
            self._declared(operation),
            operation,
            [],
            use_site,
            item.origin.child(*use_site.tokens[len(item.use_site.tokens) :]),
            item,
            self._walked_operations[-1] if self._walked_operations else None,
            self._api_security,
            shared,
            [],
        )
        self.operations.append(walked)
        self._walked_operations.append(len(self.operations) - 1)
        self._visits.append(walked)
        super()._walk_api_operation(operation, path, use_site, prefix, common)
        self._visits.pop()
        self._walked_operations.pop()

    @override
    @contextmanager
    def _visiting(self, kind: ApiObjectKind, key: object, target: Any) -> Generator[None, None, None]:
        """Record an object of the operation under the walk below the object it is in, a parameter at its place."""
        if not self._visits:
            yield
            return
        declaration, value = (
            (self._declared(target), target) if isinstance(target, dict) else (target.declaration, target.value)
        )
        node = _WalkedObject(declaration, value, [], kind, key, {})
        if kind == "parameter":
            operation = self.operations[self._walked_operations[-1]]
            operation.parameters.append((
                operation.shared.get(id(target))
                or operation.origin.child("parameters", str(len(operation.parameters))),
                node,
            ))
        else:
            self._visits[-1].objects.append(node)
        self._visits.append(node)
        try:
            yield
        finally:
            self._visits.pop()

    def _walk_callback(self, raw: dict[str, YamlValue], path: list[str], prefix: str, use_site: SchemaLocation) -> None:
        """Keep where the document spells the callback while its path items are walked."""
        previous = self._callback_origin
        self._callback_origin = (
            self.operations[self._walked_operations[-1]].origin.child("callbacks", path[-1])
            if self._walked_operations
            else use_site
        )
        super()._walk_callback(raw, path, prefix, use_site)
        self._callback_origin = previous

    def _parse_specification(self, specification: dict[str, YamlValue], path_parts: list[str]) -> None:
        """Record the OpenAPI version the parser reads each walked document by, and its info, tags and servers."""
        key = "/".join(path_parts)
        self.document_facts[key] = _keywords(self._document_as_written(specification), _DOCUMENT_FACTS)
        self.document_versions[key] = cast("OpenAPIVersion", self._read_dialect(specification).version)
        super()._parse_specification(specification, path_parts)

    @override
    def _reads_media_type(self, medium: dict[str, YamlValue], path: list[str], owner: MediaOwner) -> bool:
        """Return True: the bindings locate every medium by its media type, so walking one reads it."""
        return True

    def _visited(self) -> _WalkedObject | None:
        """Return the object of an operation the walk is visiting, None where it visits none."""
        return visited if self._visits and isinstance(visited := self._visits[-1], _WalkedObject) else None

    def _acquire_schema(
        self, name: str, raw: YamlValue, path: list[str], *, declaration: SchemaLocation, role: TypeUseRole
    ) -> None:
        """Record the declaration's engine key, even when it was already generated, and what its schema says.

        Once a parameter's schema is generated, its default and keywords are recorded as the documents spell them,
        since its model types do not say them.
        """
        self.acquisitions.setdefault((declaration, "value"), self.model_resolver.join_path(tuple(path)))
        if (visited := self._visited()) is not None:
            visited.schemas.setdefault(declaration, ("value",))
        super()._acquire_schema(name, raw, path, declaration=declaration, role=role)
        if declaration not in self.schema_records:
            self.schema_records[declaration] = self._reader.record(declaration, role)
        if declaration not in self._declaration_types:
            self._free(declaration, "value", raw, path)

    def _acquire_item_schema(  # ruff: ignore[too-many-arguments]
        self,
        name: str,
        item: YamlValue,
        path: list[str],
        projected: YamlValue,
        *,
        declaration: SchemaLocation,
        role: TypeUseRole,
    ) -> None:
        """Record an item schema and its stream-array projection, even when they were already generated."""
        item_path = [*path, "itemSchema"]
        self.acquisitions.setdefault((declaration, "value"), self.model_resolver.join_path(tuple(item_path)))
        self.acquisitions.setdefault(
            (declaration, "item_stream_array"),
            self.model_resolver.join_path(tuple(self._media_schema_path(path, from_item_schema=True))),
        )
        super()._acquire_item_schema(name, item, path, projected, declaration=declaration, role=role)
        if (visited := self._visited()) is not None:
            visited.schemas[declaration] = ("value", "item_stream_array")
        self._free(declaration, "item_stream_array", item, item_path)

    def referenced_document(self, document: str, ref: str) -> str | None:
        """Return the document a reference in a loaded document names, resolved as the walk resolves them there.

        The walk follows no link or security scheme, so their references resolve here; None is a reference that
        names no document, such as a malformed URL.
        """
        try:
            with self._home_context(document):
                return self.model_resolver.resolve_ref(f"{ref.partition('#')[0]}#").partition("#")[0]
        except ValueError:
            return None

    def load_document(self, document: str) -> dict[str, YamlValue] | None:
        """Load a document that only links or security schemes reference, as the walk loads every other document.

        None is a document that the loader cannot read, decode or fetch, or a name that no file can have; a local file
        the served document would publish is refused.
        """
        try:
            raw = self._get_ref_body(document)
        except _UnservedReferenceError:
            raise
        except (Error, OSError, UnicodeDecodeError):
            return None
        self._api_documents[document] = raw
        return raw

    @override
    def release_records(self) -> None:
        """Drop the recorded graph anchors and borrowed source nodes, and the state of a walk that failed."""
        super().release_records()
        self.operations.clear()
        self._schema_sites.clear()
        self._walked_items.clear()
        self._walked_operations.clear()
        self._visits.clear()
        self._callback_origin = None


class TargetAsyncAPIParser(_TargetRecording, AsyncAPIParser):
    """Record the schema acquisitions and loaded documents of an AsyncAPI document for one client generation attempt.

    The channels themselves are read from the loaded documents when the attempt is bound (``_asyncapi_connections``).
    """

    def __init__(
        self,
        source: str | Path | list[Path] | ParseResult | dict[str, YamlValue],
        *,
        config: AsyncAPIParserConfig | None = None,
    ) -> None:
        """Start with no loaded documents."""
        self._api_documents: dict[str, dict[str, YamlValue]] = {}
        self._api_roots: set[str] = set()
        self._declaration_types: dict[SchemaLocation, DataType] = {}
        self.ignored_declarations: list[ApiIgnoredDeclaration] = []
        super().__init__(source, config=config)  # ty: ignore[invalid-argument-type]

    @override
    def _note_scanned_document(self, document: str, raw: dict[str, YamlValue]) -> None:
        """Hold each document the parser loads, which it scans once for discriminators, by its path."""
        super()._note_scanned_document(document, raw)
        self._api_documents[document.partition("#")[0]] = raw

    @override
    def _collect_discriminator_schemas(self) -> None:
        """Hold the input document scanned now, before its schemas are parsed, as one whose channels are opened."""
        super()._collect_discriminator_schemas()
        self._api_roots.add(self.model_resolver.current_document)

    @override
    def _parse_asyncapi_schema(self, schema: AsyncAPISchema) -> None:
        """Record a located schema's engine key, and its schema where no model holds it, once it is parsed."""
        location = schema.location
        if location is not None:
            self.acquisitions.setdefault((location, "value"), self.model_resolver.join_path(tuple(schema.path)))
        super()._parse_asyncapi_schema(schema)
        if location is not None:
            with self._asyncapi_context(schema.context):
                self._free(location, "value", schema.raw_schema, schema.path)

    def _schema_reference_location(self, holder: str, ref: str) -> SchemaLocation | None:
        """Return the schema a `$ref` of a loaded document names, as the parser resolves it, or None."""
        return self._reference_location(self._api_documents, holder, ref)

    @override
    def release_records(self) -> None:
        """Drop the recorded graph anchors and the loaded documents."""
        super().release_records()
        self._api_documents.clear()
        self._api_roots.clear()


TargetParser: TypeAlias = TargetApiOpenAPIParser | TargetAsyncAPIParser
"""A target parser of any input kind, whose attempt `bind_operations` binds."""


def _builtin_name(value: object) -> str | None:
    """Return the name of the builtin a type is: the builtin itself, or an unaliased import of it from ``builtins``."""
    if isinstance(value, BuiltinType):
        return value.name
    if isinstance(value, ImportedType) and value.import_.from_ == "builtins" and value.import_.alias is None:
        return value.import_.import_
    return None


def _imported_names(imports: Imports) -> Iterator[str]:
    """Yield every module and module member that the import statements of a collection name."""
    for module, names in imports.items():
        members = (_stored_import(name)[0] for name in names)
        if module is None:
            yield from members
        else:
            yield module
            yield from (f"{module}.{member}" for member in members)


_OthersRead: TypeAlias = Literal["absent", "closed", "allowed", "schema"]


@dataclass(frozen=True, slots=True)
class _ObjectFacts:
    """What the parser reads a model's schema to say of its other properties, and whether it has pattern properties.

    `others` is what its additionalProperties say as the model's (``_model_additional_properties``): nothing, none
    (`false`, or an inline allOf branch that closes the model), any value (`true` or `{}`), or a schema of their values.
    """

    others: _OthersRead
    patterns: bool


def _others_read(said: JsonSchemaObject | bool | None) -> _OthersRead:  # noqa: FBT001
    """Return what a model's additionalProperties say (``_ObjectFacts.others``)."""
    if said is None:
        return "absent"
    if isinstance(said, bool):
        return "allowed" if said else "closed"
    return "schema" if said.model_fields_set else "allowed"


@dataclass(frozen=True, slots=True)
class _Others:
    """What the model a type holds says of its other properties, and where their schema is and its values' type.

    `kept` is the member whose field the model keeps typed ones in, and `free` their schema when the model keeps
    none, which binds model-free.
    """

    said: _OthersRead
    patterns: bool = False
    source: SourceLocation | None = None
    values: TypeView | None = None
    kept: FieldUseBinding | None = None
    free: SchemaLocation | None = None


@dataclass(frozen=True, slots=True)
class _SchemaRecord:
    """What the walk recorded of an acquired parameter schema that its model types do not say."""

    default: LiteralScalar | None
    keywords: tuple[tuple[str, FrozenLiteral], ...]


def _media_kind(media: str) -> MediaKind | None:
    return None if (found := normalized(media)) is None else media_kind(found)


def _joined(types: tuple[TypeView, ...]) -> TypeView | None:
    """Return the union of types without repeats, in their order, which a target spells from its members, or the one.

    No types join to None.
    """
    unique = tuple(dict.fromkeys(types))
    return UnionType(unique, preserve_order=True) if len(unique) > 1 else next(iter(unique), None)


def _multipart(media: str) -> bool:
    """Return whether a declared media type is a multipart one, malformed or JSON-suffixed included."""
    return media.strip().lower().startswith("multipart/")


def _resolver_key(resolver: ModelResolver, location: SchemaLocation) -> str:
    """Return the key the parser files the reference of a schema location under."""
    return resolver.join_path((location.document, "#", *location.tokens))


def _default(value: YamlValue) -> LiteralScalar | None:
    """Return a schema's default when it is a JSON boolean, number, or string."""
    kind = _DEFAULT_KINDS.get(type(value))
    return None if kind is None else LiteralScalar(kind, cast("bool | int | float | str", value))


def _keywords(
    raw: Mapping[str, YamlValue], keys: tuple[str, ...] = ANNOTATION_KEYWORDS
) -> tuple[tuple[str, FrozenLiteral], ...]:
    """Return each of ``keys`` an object declares, with its literal; by default a schema's documentation keywords."""
    found: list[tuple[str, FrozenLiteral]] = []
    for key in keys:
        if key in raw:
            with suppress(_UnsupportedError):
                found.append((key, _freeze_literal(raw[key], set())))
    return tuple(found)


_UNRECORDED: Final = _SchemaRecord(None, ())
_NOT_OBJECT: Final = "A URL-encoded value must be an object"
_SCALAR_SYMBOLS: Final = frozenset({"enum", *WRAPPER_KINDS})
_PATTERNS: Final = "Pattern properties have no builtin parameter encoding"


def _pointer_tokens(ref: str, *, strict: bool = False) -> tuple[str, ...] | None:
    """Read a reference's JSON pointer fragment (`reference_tokens`), or return None for a named anchor.

    Under `strict`, a spelling that is no canonical pointer (a stray `%` or `~`, octets that are no UTF-8) is None too.
    """
    try:
        return reference_tokens(ref.partition("#")[2], strict=strict)
    except ValueError:
        return None


def _literal_scalar(value: object) -> LiteralScalar | None:  # noqa: PLR0911
    if value is None:
        return LiteralScalar("none", None)
    match value:
        case bool():
            return LiteralScalar("bool", value)
        case int() if type(value) is int:
            return LiteralScalar("int", value)
        case float() if type(value) is float and isfinite(value):
            return LiteralScalar("float", value)
        case str() if type(value) is str:
            return LiteralScalar("str", value)
        case Decimal() if type(value) is Decimal and value.is_finite():
            return LiteralScalar("decimal", value)
        case _:
            return None


def _freeze_literal(value: object, active: set[int], *, document: bool = False) -> FrozenLiteral:
    """Freeze a literal; with ``document``, a value read from a loaded document, whose strings freeze as plain ``str``.

    A loaded document holds JSON values, and its `$ref` and `$dynamicRef` values carry their edge as a ``str`` subclass.
    """
    if document and isinstance(value, str):
        value = str(value)
    if (scalar := _literal_scalar(value)) is not None:
        return scalar
    if id(value) in active:
        raise _LiteralCycleError
    active.add(id(value))
    try:
        match value:
            case dict() if type(value) is dict:
                items: list[tuple[FrozenLiteral, FrozenLiteral]] = []
                try:
                    for key, item in value.items():
                        items.append((
                            _freeze_literal(key, active, document=document),
                            _freeze_literal(item, active, document=document),
                        ))
                except _LiteralCycleError as error:
                    error.tokens = (str(key), *error.tokens)
                    raise
                return LiteralMapping(tuple(items))
            case list() | tuple() | set() | frozenset() if (kind := _SEQUENCES.get(type(value))) is not None:
                items_sequence: list[FrozenLiteral] = []
                try:
                    items_sequence.extend(_freeze_literal(item, active, document=document) for item in value)
                except _LiteralCycleError as error:
                    error.tokens = (str(len(items_sequence)), *error.tokens)
                    raise
                return LiteralSequence(kind, tuple(items_sequence))
        raise _UnsupportedError
    finally:
        active.remove(id(value))


def _freeze_argument(value: object) -> TypeArgument:
    match value:
        case PythonCode() if type(value) is PythonCode:
            return SourceExpression(value.code)
        case PythonRuntimeExpression() if type(value) is PythonRuntimeExpression:
            return ImportedExpression(value.import_, value.prefix, value.suffix)
        case _:
            return _freeze_literal(value, set())


def _facts(
    raw: dict[str, YamlValue], keys: tuple[str, ...], declaration: SchemaLocation
) -> tuple[tuple[str, FrozenLiteral], ...]:
    facts: list[tuple[str, FrozenLiteral]] = []
    try:
        for key, value in raw.items():
            if key in keys:
                facts.append((key, _freeze_literal(value, set(), document=True)))
    except _LiteralCycleError as error:
        raise MetadataCycleError(declaration.document, declaration.child(key, *error.tokens).pointer) from error
    return tuple(facts)


def _read_required(
    raw: dict[str, YamlValue],
    declaration: SchemaLocation,
    object_type: type[ParameterObject | HeaderObject | RequestBodyObject],
) -> dict[str, YamlValue]:
    """Return an object with its `required` read as the parser's ``object_type`` reads it, a boolean.

    A value the parser reads as a boolean, such as `'yes'` or `1`, is that boolean; the parser refuses any other, and so
    do the targets.
    """
    if isinstance(required := raw.get("required", False), bool):
        return raw
    try:
        read = object_type.model_validate({"required": required}).required
    except ValidationError as error:
        message = f"API_INVALID_OBJECT: required {stable_repr(required)} is not a boolean"
        raise SchemaParseError(message, [str(declaration)]) from error
    return {**raw, "required": read}


def _backend_value(value: object) -> KnownBackendValue:
    return KnownBackendValue(_freeze_argument(value))


def _syntax_value(value: str) -> KnownBackendValue:
    from ast import literal_eval  # noqa: PLC0415

    try:
        literal = _literal_scalar(literal_eval(value))
    except (SyntaxError, ValueError):
        literal = None
    return KnownBackendValue(literal if literal is not None else SourceExpression(value))


def _ordered_union(
    members: tuple[TypeView, ...],
    *,
    preserve_order: bool,
    hint: Callable[[tuple[TypeView, ...]], ModelHint],
    discriminator: UnionDiscriminator | None = None,
) -> TypeView:
    """Return the union of types without repeats, spelled by a hint of its members, or the one type itself.

    A union the model's annotation discriminates stays one member, as its annotation wraps it.
    """
    flattened = tuple(member for value in members for member in _members(value))
    unique = merge_members(WrittenMember(member, member) for member in flattened).kept
    discriminator = discriminator or next(
        (value.discriminator for value in members if isinstance(value, UnionType) and value.discriminator is not None),
        None,
    )
    if len(unique) == 1:
        return unique[0]
    return UnionType(unique, preserve_order, discriminator, hint(unique))


def _constraint_base(data_type: DataType, *, encoded: bool = False) -> str | None:
    """Return the name of the scalar a constrained scalar constrains, as the model generator reads it, or None.

    With ``encoded``, a value decoded from base64 text with constraints constrains the type its decoding writes.
    """
    if getattr(data_type, "annotated_string", False):
        return "str"
    if encoded and (base := getattr(data_type, "encoded_base", None)):
        return base
    return None if (import_ := data_type.import_) is None else _ANNOTATED_CONSTRAINT_BASES.get(_identity(import_))


def _constrained_base(data_type: DataType) -> BuiltinType | ImportedType | None:
    """Return the scalar a constrained scalar constrains, as the model generator reads it, or None for any other."""
    base = _constraint_base(data_type)
    return ImportedType(IMPORT_DECIMAL) if base == "Decimal" else None if base is None else _BUILTINS[base]


def _members(value: TypeView) -> tuple[TypeView, ...]:
    """Return the members a union merges into an enclosing one: its own, or the type itself."""
    return value.members if isinstance(value, UnionType) and value.tag is None else (value,)


@dataclass(frozen=True, slots=True)
class _Reference:
    symbol: SymbolId
    is_alias: bool
    serialize_as_any: bool


@dataclass(frozen=True, slots=True)
class _Policy:
    backend: BackendName | None
    kind: Literal["model", "root", "alias", "enum", "custom"]
    functional_typeddict: bool


def _policy(model: DataModel, configured: type[DataModel]) -> _Policy:
    backend = _BACKENDS.get(configured)
    kind: Literal["model", "root", "alias", "enum", "custom"] = "model" if type(model) in _BACKENDS else "custom"
    if (model_type := type(model)) in _ENUMS:
        kind = "enum"
    elif model_type in _ALIASES:
        kind = "alias"
    elif model_type in _ROOTS:
        kind = "root"
    functional = (
        backend == "typeddict"
        and kind == "model"
        and isinstance(model, typed_dict.TypedDict)
        and model.is_functional_syntax
    )
    return _Policy(backend, kind, functional)


def _source(reference: Reference) -> DataModel:
    return cast("DataModel", reference.source)


def _field_name(model: DataModel, name: str | None) -> str:
    return "root" if type(model) in _ROOTS else name or ""


class _Binder:
    """Read one attempt's final graph and loaded documents, then forget them."""

    def __init__(self, parser: TargetParser, results: str | dict[tuple[str, ...], Result]) -> None:
        self.parser = parser
        self.results = results
        self.redirects: dict[int, list[tuple[Reference, bool]]] = {}
        for old, new, merged in cast("RecordingGenerationStore", parser.generation_store).redirects:
            self.redirects.setdefault(id(old), []).append((new, merged))
        self.outputs = [output for output in parser.module_outputs if output[1]]
        self.models = [model for _, models, _ in self.outputs for model in models]
        self.symbols = {id(model): SymbolId(index) for index, model in enumerate(self.models)}
        self.keys = {id(reference): key for key, reference in parser.model_resolver.references.items()}
        self.identities: dict[int, GraphObjectId] = {}
        self.anchors: list[object] = []
        self.policies = {id(model): _policy(model, parser.data_model_type) for model in self.models}
        manager = parser.data_type_manager
        self.serialize_as_any = bool(manager.use_serialize_as_any) and issubclass(manager.data_type, PydanticV2DataType)
        self.overrides = parser.config.import_overrides or {}
        self.hints = _Hints(manager.data_type)
        self.projectors: dict[tuple[Direction, bool], _Projector] = {}
        self.finals: dict[tuple[int, Direction], tuple[Reference | None, bool | None]] = {}
        self.declaration_types = dict(parser._declaration_types)  # pyright: ignore[reportPrivateUsage] # noqa: SLF001

    def identity(self, node: object) -> GraphObjectId:
        if (identity := self.identities.get(id(node))) is None:
            identity = self.identities[id(node)] = GraphObjectId(len(self.anchors))
            self.anchors.append(node)
        return identity

    @staticmethod
    def reuse_origin(reference: Reference) -> Reference | None:
        """Return the reference of the model reuse replaced with this one's model, or None if reuse did not make it.

        It is the model's `reused_reference`, wherever a later step moved either model.
        """
        return model.reused_reference if isinstance(model := reference.source, DataModel) else None

    def source(self, reference: Reference) -> str:
        """Return the path of the schema a model is generated from, through read and write variants and reuse."""
        return _source_path((self.reuse_origin(reference) or reference).path)

    @staticmethod
    def reused(model: DataModel) -> DataModel | None:
        """Return the model that reuse replaced with a model inheriting from an equal one, if reuse made this one."""
        original = model.reused_reference
        return original.source if original is not None and isinstance(original.source, DataModel) else None

    def kept(self, model: DataModel) -> DataModel:
        """Return the model that reuse kept in place of ``model``: the one a model that reuse made inherits."""
        if model.reused_reference is None:
            return model
        kept = self.final(cast("Reference", model.base_classes[0].reference))
        return model if kept is None else _source(kept)

    def emitted(self, model: object) -> bool:
        return isinstance(model, DataModel) and id(model) in self.symbols

    def final(self, reference: Reference, direction: Direction = "neutral") -> Reference | None:
        return self._final(reference, direction)[0]

    def schema_null(self, reference: Reference, direction: Direction) -> bool:
        """Return whether a use of a schema's own model admits the null of that schema, as a reference to it does.

        Each merge that pointed the schema's model at an equal one recorded the null the use admitted right before it
        (``GenerationStore.redirect_reference_users``), so a schema that rejects null keeps rejecting it in a merged
        nullable model (``nullability.reference_null``, #5410), and under ``--keep-reference-null-on-merged-models`` a
        schema that admits null keeps admitting it in a merged model that rejects null.
        """
        found, named = self._final(reference, direction)
        return found is not None and self._reference_null(found, named=named)

    def _reference_null(self, reference: Reference, *, named: bool | None) -> bool:
        """Return whether a use that recorded ``named`` admits null where it names ``reference``."""
        return reference_null(
            named=named,
            names_nullable=reference_names_nullable(reference),
            keeps_named=self.parser.keep_reference_null_on_merged_models,
        )

    def _final(self, reference: Reference, direction: Direction) -> tuple[Reference | None, bool | None]:
        key = id(reference), direction
        if (known := self.finals.get(key)) is None:
            variant = self._variant(reference, direction) if direction != "neutral" else (None, None)
            known = self.finals[key] = variant if variant[0] is not None else self._terminal(reference)
        return known

    def _terminal(self, reference: Reference) -> tuple[Reference | None, bool | None]:
        """Return the emitted model a reference was redirected to, and the null the merges on the way recorded."""
        if self.emitted(reference.source):
            return reference, None
        terminals: dict[int, tuple[Reference, bool | None]] = {}
        pending: list[tuple[Reference, bool | None]] = [(reference, None)]
        seen: set[int] = set()
        while pending:
            current, named = pending.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            if current is not reference and self.emitted(current.source):
                terminals[id(current)] = current, named
                continue
            null = self._reference_null(current, named=named)
            pending.extend((new, null if merged else named) for new, merged in self.redirects.get(id(current), ()))
        return next(iter(terminals.values())) if len(terminals) == 1 else (None, None)

    def _variant(self, reference: Reference, direction: _RwDirection) -> tuple[Reference | None, bool | None]:
        resolver = self.parser.model_resolver
        for base in dict.fromkeys((self.keys.get(id(reference), reference.path), reference.path)):
            key = resolver.join_path(tuple(_rw_variant_path(base, direction)))
            if (variant := resolver.references.get(key)) is not None:
                return self._terminal(variant)
        return None, None

    def reference(self, reference: Reference, direction: Direction) -> _Reference | None:
        if (final := self.final(reference, direction)) is None:
            return None
        model = _source(final)
        return _Reference(
            self.symbols[id(model)],
            model.IS_ALIAS,
            self.serialize_as_any and _writes_serialize_as_any(model.reference),
        )

    @staticmethod
    def unwrapped(reference: Reference) -> DataType | None:
        model = reference.source
        if isinstance(model, DataModel) and (model.IS_ALIAS or model.IS_ROOT_MODEL) and len(model.fields) == 1:
            return model.fields[0].data_type
        return None

    def root_field(self, model: DataModel) -> DataModelFieldBase | None:
        """Return the field of an alias or root model, or that of the model its reuse inherits."""
        while (
            not model.fields
            and len(model.base_classes) == 1
            and (base := model.base_classes[0].reference) is not None
            and (final := self.final(base)) is not None
        ):
            model = _source(final)
        return model.fields[0] if len(model.fields) == 1 else None

    def constrains(self, model: DataModel) -> bool:
        """Return whether an alias or root model's field constrains its value, as the models that validate one do."""
        return (
            self.policies[id(model)].backend not in _UNCHECKED
            and (field := self.root_field(model)) is not None
            and _has_root_field_constraints(field, row="declared")
        )

    def projector(self, direction: Direction, *, inline: bool = False) -> _Projector:
        if (projector := self.projectors.get((direction, inline))) is None:
            projector = self.projectors[direction, inline] = _Projector(self, direction, inline=inline)
        return projector

    def resolve_import(self, import_: Import) -> Import:
        if import_.from_ == "__future__" or (module := self.overrides.get(import_.import_)) is None:
            return import_
        return replace(import_, from_=module)


def _identity(import_: Import) -> tuple[str | None, str]:
    return import_.from_, import_.import_


def _differs(value: TypeView) -> bool:
    """Return whether a type's static spelling differs from its annotation."""
    hint = getattr(value, "hint", None)
    return hint is not None and hint.static is not hint.annotation


class _Hints:
    """Spell projected types with the model generator's own type rendering, leaving model and import names open.

    A leaf renders a copy of its DataType, and a composite renders the configured type class over the spellings of its
    parts, as the model's type hint composes them. A generated symbol or an imported name is a slot that a target
    module names; the names the rendering writes as they are, such as typing constructs, are the hint's imports.
    """

    def __init__(self, hint_type: type[DataType]) -> None:
        self.hint_type = hint_type
        self.composer = TypeComposer(hint_type)
        self.texts: dict[tuple[str, tuple[tuple[str | None, str], ...]], HintText] = {}
        self.slots: dict[object, str] = {}
        self.values: list[SymbolId | Import] = []
        self.fixed: dict[Import, None] = {}

    def slot(self, value: SymbolId | Import) -> str:
        key = value if isinstance(value, int) else _identity(value)
        if (slot := self.slots.get(key)) is None:
            slot = self.slots[key] = f"{_SLOT}{len(self.values)}{_SLOT}"
            self.values.append(value if isinstance(value, int) else Import(import_=value.import_, from_=value.from_))
        return slot

    def text(self, encoded: str, imports: Iterable[Import]) -> HintText:
        """Return the hint text of a rendering, one record for each distinct text and its fixed names."""
        fixed = tuple(dict.fromkeys(_identity(item) for item in imports))
        if (found := self.texts.get(key := (encoded, fixed))) is None:
            names = tuple(Import(import_=name, from_=module) for module, name in fixed)
            self.fixed.update(dict.fromkeys(names))
            pieces = encoded.split(_SLOT)
            found = self.texts[key] = HintText(
                tuple(self.values[int(piece)] if index % 2 else piece for index, piece in enumerate(pieces) if piece),
                names,
            )
        return found

    def hint(
        self, annotation: tuple[str, Iterable[Import]], static: tuple[str, Iterable[Import]] | None = None
    ) -> ModelHint:
        spelled = self.text(*annotation)
        return ModelHint(spelled, spelled if static is None else self.text(*static))

    def of(self, value: TypeView) -> ModelHint:
        """Return the hint of a projected type, deriving that of a generated symbol, an import or a builtin."""
        if isinstance(value, GeneratedSymbolType | ImportedType | BuiltinType | NoneType):
            return self.hint(self.spelled(value, static=True))
        assert value.hint is not None
        return value.hint

    def spelled(self, value: TypeView, *, static: bool) -> tuple[str, tuple[Import, ...]]:
        """Return a projected type's spelling as rendering text, with the names it writes as they are."""
        match value:
            case GeneratedSymbolType():
                return self.slot(value.symbol), ()
            case ImportedType():
                return self.slot(value.import_) + "".join(f".{part}" for part in value.qualified_suffix), ()
            case BuiltinType():
                return value.name, ()
            case NoneType():
                return "None", ()
            case _:
                pass
        hint = cast("ModelHint", value.hint)
        text = hint.static if static else hint.annotation
        return "".join(part if isinstance(part, str) else self.slot(part) for part in text.parts), text.imports

    def compose(
        self,
        style: DataType,
        *,
        base: str = "",
        members: tuple[str, ...] = (),
        key: str | None = None,
        **flags: object,
    ) -> tuple[str, tuple[Import, ...]]:
        """Render the configured type class over spelled parts, in the style of the DataType they stand for."""
        values = tuple((name, getattr(style, name)) for name in _STYLE)
        return self.composer.compose(values, base=base, members=members, key=key, **flags)

    def composed(
        self,
        style: DataType,
        *,
        base: TypeView | None = None,
        members: tuple[TypeView, ...] = (),
        key: TypeView | None = None,
        container: str | None = None,
        **flags: object,
    ) -> ModelHint:
        """Return the hint a composite renders to, statically and as an annotation, from its parts' hints."""
        if container is not None:
            flags[container] = True
        parts = tuple(part for part in (base, key, *members) if part is not None)
        variants: list[tuple[str, tuple[Import, ...]]] = []
        for static in (False, True) if any(_differs(part) for part in parts) else (False,):
            spelled = [self.spelled(part, static=static) for part in parts]
            texts = iter(text for text, _ in spelled)
            text, imports = self.compose(
                style,
                base="" if base is None else next(texts),
                key=None if key is None else next(texts),
                members=tuple(texts),
                **flags,
            )
            variants.append((text, (*imports, *(item for _, found in spelled for item in found))))
        annotation, static = variants if len(variants) > 1 else (variants[0], None)
        return self.hint(annotation, None if static == annotation else static)

    def leaf(self, data_type: DataType, enum: SymbolId | None = None) -> ModelHint:
        """Render one DataType without its containers and optionality, as the model's own type hint spells it.

        Its import, runtime-expression imports and enum class are slots. Statically, a constrained scalar is its base
        type, a constrained string is str and bytes decoded from base64 text with lengths are bytes.
        """
        if (python_type := data_type.python_type) is not None:
            return self.hint(self.bound(python_type))
        update: dict[str, object] = {**_ALONE, "data_types": [], "children": []}
        slotted: set[tuple[str | None, str]] = set()
        if (import_ := data_type.import_) is not None and import_.from_ is not None:
            update["alias"] = self.slot(import_)
            slotted.add(_identity(import_))
        if data_type.enum_member_literals or data_type.literals:
            update.update(type=None, alias=None)
            if enum is not None:
                update["enum_member_literals"] = [(self.slot(enum), name) for _, name in data_type.enum_member_literals]
        if (kwargs := data_type.kwargs) and any(
            isinstance(value, PythonRuntimeExpression) for value in kwargs.values()
        ):
            update["kwargs"] = {name: self.expression(value, slotted) for name, value in kwargs.items()}
        copy = data_type.model_copy(update=update)
        if runtime := data_type.runtime_expression_imports:
            slotted.update(map(_identity, runtime))
            copy._set_runtime_expression_imports(tuple(replace(item, alias=self.slot(item)) for item in runtime))  # noqa: SLF001
        annotation = copy.type_hint, [item for item in copy.imports if _identity(item) not in slotted]
        static = None
        if import_ is not None and data_type.is_func and kwargs:
            base = _constraint_base(data_type, encoded=True)
            static = None if base is None else ((self.slot(IMPORT_DECIMAL) if base == "Decimal" else base), ())
        return self.hint(annotation, static)

    def bound(self, binding: BoundPythonType) -> tuple[str, tuple[Import, ...]]:
        """Render a bound Python type with each name it imports, and each module it names, as a slot.

        The model generator's own aliases of those names stay out of the text, so a target module names them itself.
        """
        from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
            PythonTypeBoundName,
            PythonTypeRuntimeSymbol,
            render_python_type_expr,
            rewrite_python_type_expr,
        )

        slots: dict[str, str] = {}

        def placeholder(import_: Import) -> str:
            slot = self.slot(import_)
            name = f"__dcg_slot_{slot.strip(_SLOT)}__"
            slots[name] = slot
            return name

        def leaf(expression: PythonTypeExpr) -> PythonTypeExpr:
            match expression:
                case PythonTypeBoundName():
                    name = placeholder(Import(import_=expression.import_name, from_=expression.import_from))
                    return PythonTypeBoundName(name, expression.import_from, expression.import_name)
                case PythonTypeRuntimeSymbol() if expression.module:
                    return PythonTypeRuntimeSymbol(
                        placeholder(Import(import_=expression.module)), expression.qualname_parts
                    )
                case _:
                    pass
            return expression

        text = render_python_type_expr(rewrite_python_type_expr(binding.expression, leaf))
        for name, slot in slots.items():
            text = text.replace(name, slot)
        return text, ()

    def expression(self, value: object, slotted: set[tuple[str | None, str]]) -> object:
        if not isinstance(value, PythonRuntimeExpression):
            return value
        slotted.add(key := _identity(value.import_))
        return value.with_import_aliases({key: replace(value.import_, alias=self.slot(value.import_))})


class _Projector:
    """Project final DataTypes of one direction into type views, each spelled as the model spells it.

    An inlining projector reads each alias and root model as the type it stands for, as an argument takes it, and
    `constrained` says whether one of those models it read since it was last cleared constrains that type in its field.
    """

    def __init__(self, binder: _Binder, direction: Direction, *, inline: bool = False) -> None:
        self.binder = binder
        self.direction = direction
        self.hints = binder.hints
        self.inline = inline
        self.inlined: set[SymbolId] = set()
        self.projected: dict[int, tuple[DataType, TypeView]] = {}
        self.constrained = False

    def project(self, data_type: DataType) -> TypeProjection:
        try:
            return TypeProjection(self._imports(self._project(data_type)))
        except _UnprojectedError as error:
            return TypeProjection(None, error.reason)

    def declaration(self, reference: Reference, *, nullable: bool = False) -> TypeProjection:
        """Project a schema's own model, which as a nullable value also takes None when an object model stands for it.

        An object model, unlike an alias or a root model, cannot hold the null its schema admits; the null is the one
        a reference to the schema admits (``_Binder.schema_null``).
        """
        try:
            value = self._reference_value(reference, serialize_as_any=False)
            if (
                nullable
                and isinstance(value, GeneratedSymbolType)
                and self.binder.policies[id(self.binder.models[value.symbol])].kind == "model"
                and self.binder.schema_null(reference, self.direction)
            ):
                hint = self.hints.composed(self.hints.hint_type.model_construct(), base=value, is_optional=True)
                value = UnionType((value, NoneType()), preserve_order=False, hint=hint)
            return TypeProjection(self._imports(value))
        except _UnprojectedError as error:
            return TypeProjection(None, error.reason)

    def _project(self, data_type: DataType) -> TypeView:
        if not self.inline and (known := self.projected.get(id(data_type))) is not None:
            return known[1]
        projected, inferred_optional = self._base(data_type)
        projected = self._container(data_type, projected)
        nullable_reference = (
            (reference := data_type.reference) is not None
            and data_type.adds_reference_null
            and self.binder.reference(reference, self.direction) is not None
        )
        if (data_type.is_optional or inferred_optional or nullable_reference) and projected != ImportedType(IMPORT_ANY):
            base = projected
            projected = _ordered_union(
                (projected, NoneType()),
                preserve_order=data_type.preserve_union_member_order,
                hint=lambda _: self.hints.composed(data_type, base=base, is_optional=True),
            )
        if not self.inline:
            self.projected[id(data_type)] = data_type, projected
        return projected

    def _base(self, data_type: DataType) -> tuple[TypeView | None, bool]:
        if data_type.python_type is not None:
            return BoundType(data_type.python_type, self.hints.leaf(data_type)), False
        if data_type.type is not None:
            return self._atomic(data_type), False
        if data_type.data_types or data_type.is_tuple:
            return self._structural(data_type)
        if (reference := data_type.reference) is not None and not (
            data_type.enum_member_literals or data_type.literals
        ):
            return self._reference_value(
                reference, serialize_as_any=data_type.use_serialize_as_any and data_type.alias is None
            ), False
        return self._atomic(data_type), False

    def _reference_value(self, reference: Reference, *, serialize_as_any: bool) -> TypeView:
        binder = self.binder
        if (binding := binder.reference(reference, self.direction)) is None:
            if (root := binder.unwrapped(reference)) is not None:
                return self._project(root)
            raise _NotEmittedError
        symbol = binding.symbol
        if (
            self.inline
            and symbol not in self.inlined
            and binder.policies[id(model := binder.models[symbol])].kind in WRAPPER_KINDS
            and (root := binder.root_field(model)) is not None
        ):
            self.inlined.add(symbol)
            self.constrained = self.constrained or binder.constrains(model)
            try:
                return self._project(root.data_type)
            finally:
                self.inlined.discard(symbol)
        result: TypeView = GeneratedSymbolType(symbol)
        if serialize_as_any and binding.serialize_as_any:
            spelled = subscript_text(self.hints.slot(IMPORT_SERIALIZE_AS_ANY), (self.hints.slot(symbol),)), ()
            result = GenericType(ImportedType(IMPORT_SERIALIZE_AS_ANY), (result,), hint=self.hints.hint(spelled))
        return result

    def _atomic(self, data_type: DataType) -> TypeView | None:
        if data_type.enum_member_literals:
            symbol, members = self._enum_members(data_type)
            return LiteralType(members, self.hints.leaf(data_type, symbol))
        if data_type.literals:
            return LiteralType(tuple(self._literal(value) for value in data_type.literals), self.hints.leaf(data_type))
        if (import_ := data_type.import_) is not None:
            imported = ImportedType(import_)
            if data_type.is_func and data_type.kwargs:
                keywords = tuple((name, _freeze_argument(value)) for name, value in data_type.kwargs.items())
                return ConstructorType(imported, keywords, self.hints.leaf(data_type), _constrained_base(data_type))
            return imported
        if data_type.type is None:
            return None
        return NoneType() if data_type.type == "None" else _BUILTINS[data_type.type]

    @staticmethod
    def _literal(value: object) -> LiteralScalar:
        match value:
            case bool():
                return LiteralScalar("bool", value)
            case int():
                return LiteralScalar("int", value)
            case _:
                return LiteralScalar("str", str(value))

    def _enum_members(self, data_type: DataType) -> tuple[SymbolId, tuple[GeneratedEnumMember, ...]]:
        reference = data_type._enum_member_literal_reference or data_type.reference  # pyright: ignore[reportPrivateUsage] # noqa: SLF001
        model = _source(cast("Reference", self.binder.final(cast("Reference", reference))))
        fields = {field.name: field for field in model.fields}
        symbol = self.binder.symbols[id(model)]
        return symbol, tuple(
            GeneratedEnumMember(symbol, self.binder.identity(fields[name]), name)
            for _, name in data_type.enum_member_literals
        )

    def _structural(self, data_type: DataType) -> tuple[TypeView, bool]:
        hints = self.hints
        if data_type.is_tuple:
            arguments = tuple(self._project(child) for child in data_type.data_types)
            count = data_type.tuple_item_count
            hint = hints.composed(
                data_type,
                base=None,
                members=arguments[:1] if count is not None else arguments,
                is_tuple=True,
                tuple_item_count=count,
            )
            if count is not None:
                arguments = (arguments[0] if arguments else ImportedType(IMPORT_ANY),) * count
            return GenericType(_BUILTINS[SLOT_CLASSES["tuple"].name], arguments, "fixed", hint), False
        if len(data_type.data_types) == 1:
            return self._project(data_type.data_types[0]), False
        preserve_order = data_type.preserve_union_member_order
        projected = tuple(self._project(child) for child in data_type.data_types)
        flattened = tuple(member for value in projected for member in _members(value))
        merged = merge_members(
            WrittenMember(member, member, null=not preserve_order and is_none(member))
            for member in (projected if preserve_order else flattened)
        )
        members, inferred_optional = merged.kept, merged.takes_null
        union = (
            _ordered_union(
                members,
                preserve_order=preserve_order,
                hint=lambda parts: hints.composed(data_type, members=parts, preserve_union_member_order=preserve_order),
                discriminator=self.selector(data_type) if len(dict.fromkeys(flattened)) > 1 else None,
            )
            if members
            else ImportedType(IMPORT_ANY)
        )
        parts = union.members if isinstance(union, UnionType) else (union,)
        if (discriminator := data_type.discriminator) is not None:
            wrapped = hints.composed(
                data_type,
                base=None,
                members=parts,
                preserve_union_member_order=preserve_order,
                discriminator=discriminator,
            )
            annotation = wrapped.annotation
            hint = ModelHint(HintText(annotation.parts, (*annotation.imports, IMPORT_FIELD)), hints.of(union).static)
            hints.fixed[IMPORT_FIELD] = None
            schema = union.discriminator if isinstance(union, UnionType) else None
            return UnionType(parts, preserve_order, schema, hint, discriminator), inferred_optional
        return union, inferred_optional

    def selector(self, data_type: DataType) -> UnionDiscriminator | None:
        """Return the discriminator a union's schema declares.

        Each mapped value names the source of the model its reference resolves to, or the reference's own path when
        no emitted model stands for it, as a model that only read and write variants stand for.
        """
        if (union := self.union(data_type)) is None or (selector := union.selector) is None:
            return None
        references = self.binder.parser.model_resolver.references
        return UnionDiscriminator(
            selector.property_name,
            tuple(
                (
                    value,
                    self.binder.source(final)
                    if (reference := references.get(path)) is not None
                    and (final := self.binder.final(reference)) is not None
                    else path,
                )
                for value, path in selector.mapping
            ),
        )

    def union(self, data_type: DataType) -> _Union | None:
        """Return the oneOf or anyOf a union was parsed from, through the copies the parser makes of it.

        A union that holds only the members it was parsed to is that one. A union in a copy of a field, as inherited
        fields and read or write variants are, is the union at the same place in the field it was copied from, or in
        the field that one was copied from.
        """
        if (found := self.parsed(data_type)) is not None:
            return found
        steps: list[int] = []
        node = data_type
        while isinstance(parent := node.parent, DataType):
            steps.append(
                next((index for index, child in enumerate(parent.data_types) if child is node), len(parent.data_types))
            )
            node = parent
        copies = self.binder.parser.copies
        field = node.parent
        while (copy := copies.get(id(field))) is not None:
            field = copy[1]
            target = field.data_type
            for index in reversed(steps):
                target = next(iter(target.data_types[index : index + 1]), target)
            if (found := self.parsed(target)) is not None:
                return found
        return None

    def parsed(self, data_type: DataType) -> _Union | None:
        """Return the oneOf or anyOf whose parse a union holds only the members of."""
        unions = self.binder.parser.unions
        found = unions.get(id(next(iter(data_type.data_types), None)))
        return (
            found
            if found is not None and all(unions.get(id(member)) is found for member in data_type.data_types)
            else None
        )

    def _container(self, data_type: DataType, value: TypeView | None) -> TypeView:
        standard = data_type.use_standard_collections
        for modifier in WRITTEN_CONTAINERS:
            if not getattr(data_type, flag := CONTAINER_FLAGS[modifier]):
                continue
            name: str | None = None
            form: Literal["not_tuple", "variadic"] = "not_tuple"
            if (found := given_container_of(data_type.given_container, modifier)) is not None:
                written, slot, name = found.written, modifier, data_type.given_container
                form = "variadic" if found.variadic else form
            else:
                written = SLOT_CLASSES[slot := written_slot(modifier, generic=data_type.use_generic_container)]
            base = (
                ImportedType(Import(import_=written.name, from_=written.module_of(standard=standard)))
                if written.abstract
                else _BUILTINS[written.name]
            )
            if modifier in MAPPINGS.slots and (data_type.dict_key is not None or value is not None):
                key = self._project(data_type.dict_key) if data_type.dict_key is not None else None
                value = value if value is not None else ImportedType(IMPORT_ANY)
                hint = self.hints.composed(data_type, base=value, key=key, container=flag, given_container=name)
                return GenericType(
                    base, (BuiltinType("str") if key is None else key, value), hint=hint, slot=slot, given=name
                )
            hint = self.hints.composed(data_type, base=value, container=flag, given_container=name)
            return GenericType(base, (value,) if value is not None else (), form, hint, slot, name)
        if value is None:
            raise _UnsupportedError
        return value

    def _imports(self, value: TypeView) -> TypeView:  # noqa: PLR0911
        if not self.binder.overrides:
            return value
        resolve = self.binder.resolve_import
        match value:
            case ImportedType():
                return ImportedType(resolve(value.import_), value.qualified_suffix)
            case BoundType():
                return replace(value, binding=_bound(value.binding, resolve))
            case GenericType():
                return replace(
                    value,
                    base=self._imports(value.base),
                    arguments=tuple(self._imports(item) for item in value.arguments),
                )
            case UnionType():
                return replace(value, members=tuple(self._imports(item) for item in value.members))
            case ConstructorType():
                return replace(
                    value,
                    callable=self._imports(value.callable),
                    keywords=tuple((name, _argument_import(item, resolve)) for name, item in value.keywords),
                    base=None if value.base is None else self._imports(value.base),
                )
            case _:
                pass
        return value


class _NotEmittedError(_UnprojectedError):
    """A reference whose model the emitted modules do not contain."""

    reason: ClassVar[TypeProjectionReason] = "BND_SYMBOL_NOT_EMITTED"


def _argument_import(value: TypeArgument, resolve: Callable[[Import], Import]) -> TypeArgument:
    return replace(value, import_=resolve(value.import_)) if isinstance(value, ImportedExpression) else value


def _bound(value: BoundPythonType, resolve: Callable[[Import], Import]) -> BoundPythonType:
    from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
        PythonTypeBoundName,
        PythonTypeRuntimeSymbol,
        rewrite_python_type_expr,
    )
    from datamodel_code_generator._python_type_binding import BoundPythonType  # noqa: PLC0415

    resolved = tuple(resolve(import_) for import_ in value.imports)
    names = {
        (original.from_, original.import_, original.binding_name): actual
        for original, actual in zip(value.imports, resolved, strict=True)
    }
    modules = {
        original.import_: actual
        for original, actual in zip(value.imports, resolved, strict=True)
        if original.from_ is None
    }

    def leaf(expression: PythonTypeExpr) -> PythonTypeExpr:
        if isinstance(expression, PythonTypeBoundName) and (
            actual := names.get((expression.import_from, expression.import_name, expression.value))
        ):
            return PythonTypeBoundName(actual.binding_name, actual.from_, actual.import_)
        if (
            isinstance(expression, PythonTypeRuntimeSymbol)
            and (actual := modules.get(expression.module))
            and not (actual.from_ or "").startswith(".")
        ):
            return PythonTypeRuntimeSymbol(_import_path(actual), expression.qualname_parts)
        return expression

    return BoundPythonType(rewrite_python_type_expr(value.expression, leaf), resolved)


def _frozen_or_source(value: object) -> TypeArgument:
    """Return a value as a frozen argument, or as the source text of its repr when it has no frozen form."""
    try:
        return _freeze_argument(value)
    except _UnsupportedError:
        return SourceExpression(stable_repr(value))


def _emitted_default(  # ruff: ignore[too-many-return-statements]
    field: DataModelFieldBase, backend: BackendName
) -> tuple[DefaultKind, FrozenLiteral | SourceExpression | None]:
    """Return the default kind and value that the builtin backend renders for a field."""
    if backend == "typeddict":
        return "absent", None
    if getattr(field, "use_missing_sentinel_default", False):
        return "pydantic_missing", None
    has_default, has_value = field._get_constructor_default_info()  # pyright: ignore[reportPrivateUsage] # ruff: ignore[private-member-access]
    if not has_default:
        return "absent", None
    if not has_value:
        return "factory", None
    if isinstance(field, MsgspecField) and field._get_field_data().get("default") is msgspec.UNSET:  # pyright: ignore[reportPrivateUsage] # ruff: ignore[private-member-access]
        return "msgspec_unset", None
    if field.default is None or field.default is UNDEFINED:
        return "none", LiteralScalar("none", None)
    if isinstance(value := _frozen_or_source(field.default), (SourceExpression, ImportedExpression)):
        return "expression", SourceExpression(stable_repr(field.default))
    return "literal", value


def _constructor_keywords(field: DataModelFieldBase, backend: BackendName) -> tuple[tuple[str, TypeArgument], ...]:
    """Return the keyword arguments of the builtin field call, read from the field's structured render data.

    A runtime expression, such as the ``compile(...)`` of a pattern that needs Python's regex engine, keeps its import.
    """
    keywords: list[tuple[str, TypeArgument]] = []
    match field:
        case PydanticField() if backend in {"pydantic", "pydantic_dataclass"}:
            data, factory = field._get_field_data_and_default_factory()  # pyright: ignore[reportPrivateUsage] # ruff: ignore[private-member-access]
            items = sorted((name, value) for name, value in data.items() if value is not None)
            if factory:
                keywords.append(("default_factory", SourceExpression(str(factory))))
        case DataclassField() | MsgspecField() if backend in {"dataclass", "msgspec"}:
            items = list(field._get_field_data().items())  # pyright: ignore[reportPrivateUsage] # ruff: ignore[private-member-access]
            if len(items) == 1 and items[0][0] == "default":
                return ()
        case _:
            return ()
    for name, value in items:
        if name == "default_factory":
            keywords.append((name, SourceExpression(str(value))))
            continue
        keywords.append((name, _frozen_or_source(value)))
    return tuple(keywords)


def _qualifiers(field: DataModelFieldBase, backend: BackendName) -> tuple[str, ...]:
    if backend != "typeddict":
        return ("ClassVar",) if field.is_class_var else ()
    qualifiers: list[str] = []
    if requiredness := getattr(field, "_requiredness", ""):
        qualifiers.append(requiredness)
    if getattr(field, "_read_only", False):
        qualifiers.append("ReadOnly")
    return tuple(qualifiers)


def _setting(name: str, keywords: tuple[tuple[str, TypeArgument], ...], *, fallback: bool | None) -> BackendValue:
    for key, value in reversed(keywords):
        if key == name:
            return (
                RuntimeBackendValue("expression")
                if isinstance(value, SourceExpression | ImportedExpression)
                else KnownBackendValue(value)
            )
    return _backend_value(fallback) if fallback is not None else OpaqueBackendValue("model_policy_required")


def _model_settings(model: DataModel, backend: BackendName) -> dict[str, BackendValue] | None:
    internal: dict[str, object] = model._internal_template_data  # pyright: ignore[reportPrivateUsage] # noqa: SLF001
    match backend:
        case "dataclass" | "pydantic_dataclass":
            return {name: _backend_value(value) for name, value in model.written_dataclass_arguments.items()}
        case "msgspec":
            raw = model.extra_template_data.get("base_class_kwargs", {})
            adopted = cast("dict[str, str]", internal.get("base_class_kwargs", {}))
            if not isinstance(raw, dict):
                return None
            values: dict[str, BackendValue] = {
                name: _backend_value(value) for name, value in raw.items() if name in _MSGSPEC_PARAMETERS
            }
            values.update(
                (name, _syntax_value(value)) for name, value in adopted.items() if name in _MSGSPEC_PARAMETERS
            )
            return values
        case "typeddict":
            arguments = cast("dict[str, str]", internal.get("typed_dict_kwargs", {}))
            return {name: _syntax_value(value) for name, value in arguments.items()}
        case _:
            pass
    return {}


def _settings(names: tuple[str, ...], values: dict[str, BackendValue] | None) -> tuple[BackendSetting, ...]:
    if values is None:
        return tuple(BackendSetting(name, None, OpaqueBackendValue("custom_origin")) for name in names)
    return tuple(
        BackendSetting(name, name in values, values[name] if name in values else _backend_value(None)) for name in names
    )


def _model_facts(model: DataModel, policy: _Policy) -> BackendModelFacts | None:
    if (backend := policy.backend) is None or policy.kind == "custom" or model.decorators:
        return None
    values = _model_settings(model, backend)
    parameters = _BACKEND_PARAMETERS.get(backend, ())
    configuration: tuple[BackendSetting, ...] = ()
    if backend in {"pydantic", "pydantic_dataclass"}:
        key = "config_items" if backend == "pydantic" else "_safe_config_items"
        items: list[tuple[str, str]] = model._internal_template_data.get(key, [])  # pyright: ignore[reportPrivateUsage] # noqa: SLF001
        configuration = _settings(
            _PYDANTIC_CONFIGURATION,
            {name: _syntax_value(value) for name, value in items if name in _PYDANTIC_CONFIGURATION},
        )
    return BackendModelFacts(backend, _settings(parameters, values), configuration, policy.functional_typeddict)


@dataclass(frozen=True, slots=True)
class _FieldContext:
    """Producer facts of a property field: whether its schema is nullable, and whether its own type accepts None.

    None says the field's own type is unknown.
    """

    explicit_nullable: bool
    preexisting_null: bool | None


def _field_facts(  # noqa: PLR0913
    field: DataModelFieldBase,
    type_value: TypeView,
    backend: BackendName,
    model_facts: BackendModelFacts | None,
    context: _FieldContext | None,
    *,
    explicit_name: bool,
) -> ModelFieldFacts:
    """Read a field's model and builtin backend facts from the final field and its render data."""
    emitted = not (backend == "msgspec" and field.extras.get("is_classvar") is True)
    qualifiers = _qualifiers(field, backend)
    default_kind, default_value = _emitted_default(field, backend)
    keywords = _constructor_keywords(field, backend)
    null_in_annotation = _annotation_null(field, type_value)
    emitted_facts = EmittedFieldFacts(
        emitted=emitted,
        emitted_default_kind=default_kind,
        emitted_default_value=default_value,
        qualifiers=qualifiers,
        constructor_keywords=keywords if backend != "typeddict" else (),
    )
    constructor_init = (
        _backend_value(None)
        if backend == "typeddict"
        else _backend_value(value=False)
        if not emitted or "ClassVar" in qualifiers
        else _setting("init", keywords, fallback=None if model_facts is None else True)
    )
    return ModelFieldFacts(
        field.required,
        field.nullable,
        field.has_default,
        field.type_has_null,
        field.read_only,
        field.write_only,
        type_value,
        BackendFieldFacts(backend, emitted_facts, constructor_init),
        context is not None
        and (
            context.preexisting_null is True
            or (context.preexisting_null is False and context.explicit_nullable and null_in_annotation)
        ),
        null_in_annotation,
        explicit_name,
    )


def _annotation_null(field: DataModelFieldBase, type_value: TypeView) -> bool:
    """Return whether the emitted field annotation accepts None, as the field's type hint decides."""
    if admits_none(type_value):
        return True
    return field._field_null_source(field.data_type) is not None  # pyright: ignore[reportPrivateUsage] # noqa: SLF001


def _members_of(data_type: DataType) -> list[DataType]:
    """Return the types a type holds as items, values or union members, below the wrappers that hold one type."""
    inner = data_type
    while (
        len(inner.data_types) == 1 and inner.reference is None and not inner._is_container  # pyright: ignore[reportPrivateUsage] # noqa: SLF001
    ):
        inner = inner.data_types[0]
    return inner.data_types


def _map_values(data_type: DataType) -> DataType | None:
    """Return the value type of the mapping a type is, or holds directly, if any."""
    mapping = next((child for child in (data_type, *data_type.data_types) if child.is_dict), None)
    return mapping.data_types[-1] if mapping is not None and mapping.data_types else None


def _below(location: SchemaLocation, schema: SchemaLocation) -> bool:
    """Return whether a location is a schema's own or one inside it."""
    return location.document == schema.document and location.tokens[: len(schema.tokens)] == schema.tokens


def _rebased(location: SchemaLocation, origin: SchemaLocation, schema: SchemaLocation) -> SchemaLocation:
    """Return the place below ``schema`` that ``location`` has below ``origin``; a location elsewhere stays."""
    if not _below(location, origin):
        return location
    return SchemaLocation(schema.document, (*schema.tokens, *location.tokens[len(origin.tokens) :]))


def _wire_name(field: DataModelFieldBase) -> str:
    return field_source_name(name=field.name, original_name=field.original_name, alias=field.alias) or ""


class _Reader:
    """Read the loaded API documents by declaration while the walk reads them, recording what the models do not say.

    It reads the site of each location (``ApiOpenAPIParser._site_facts``), and the keywords beside a reference at a
    location with the schema it names where the models do (``siblings``, the parser's ``_reads_ref_siblings_at``).
    """

    def __init__(
        self, site: Callable[[SchemaLocation], SiteFacts], *, siblings: Callable[[SchemaLocation], bool]
    ) -> None:
        self.site = site
        self.siblings = siblings

    def annotations(self, declaration: SchemaLocation) -> dict[str, YamlValue]:
        """Return the documentation keywords and default a schema gives its values, through its reference chain.

        The schema a chain leads to gives its own, and the keywords beside each reference that the models read add
        theirs, the nearer one's first. A chain that loops back leads to no schema: the keywords beside its references
        are all it gives.
        """
        location, site = declaration, self.site(declaration)
        seen = {declaration}
        found: dict[str, YamlValue] = {}
        while (target := site.ref) is not None:
            if self.siblings(location):
                found = {**site.annotations, **found}
            if target in seen:
                return found
            seen.add(target)
            location, site = target, self.site(target)
        return {**site.annotations, **found}

    def record(self, declaration: SchemaLocation, role: TypeUseRole) -> _SchemaRecord:
        """Record what an acquired parameter schema says that its model types do not: its default and keywords."""
        if role != "parameter":
            return _UNRECORDED
        annotations = self.annotations(declaration)
        return _SchemaRecord(_default(annotations.get("default")), _keywords(annotations))


class _Documents:
    """The documents an attempt loaded, by identity: the walked roots first, then the documents they reference.

    The binder holds them for the source lease and reads only the link, callback and security scheme objects in them,
    which the walk does not follow.
    """

    def __init__(self, parser: TargetParser) -> None:
        loaded = parser._api_documents  # pyright: ignore[reportPrivateUsage] # ruff: ignore[private-member-access]
        roots = [document for document in loaded if document in parser._api_roots]  # pyright: ignore[reportPrivateUsage] # ruff: ignore[private-member-access]
        self.documents: dict[str, dict[str, YamlValue]] = {}
        self.ids: dict[str, SourceDocumentId] = {}
        self.unloadable: set[str] = set()
        for document in (*roots, *loaded):
            if document not in self.ids:
                self.ids[document] = SourceDocumentId(len(self.ids))
                self.documents[document] = loaded[document]
        self.walked = tuple(self.documents)

    def borrow(self, declaration: SchemaLocation, missing: YamlValue = None) -> YamlValue:
        """Read a declaration from its loaded document, or `missing` when the document lacks it."""
        return descend(self.documents.get(declaration.document), declaration.tokens, "canonical", missing)

    def location(self, declaration: SchemaLocation, role: Literal["declaration", "use", "schema"]) -> SourceLocation:
        """Return a declaration's plain pointer location in its loaded document."""
        return SourceLocation(self.ids[declaration.document], declaration.pointer, role)


class _Models:
    """Inventory one attempt's emitted models and bind their effective fields."""

    def __init__(
        self,
        parser: TargetParser,
        results: str | dict[tuple[str, ...], Result],
        *,
        attempt: AttemptId,
        output: Path,
        model_package: str,
    ) -> None:
        self.binder = _Binder(parser, results)
        self.documents = _Documents(parser)
        self.parser = parser
        self.attempt = attempt
        self.output = output
        self.model_package = model_package
        self.ignored = [(item.declaration, item.use_site, item) for item in parser.ignored_declarations]
        self.ignored_declarations = {declaration for declaration, _, _ in self.ignored}
        self.members: dict[SymbolId, list[FieldUseBinding]] = {}
        self.uses: dict[TypeUseId, TypeUseBinding] = {}
        self.slots: dict[int, FieldSlot] = {}
        self.facts: dict[FieldSlot, ModelFieldFacts] = {}
        self.locations: dict[SymbolId, SchemaLocation | None] = {}
        self.contexts_by_field: dict[
            int, tuple[FieldKind, SchemaLocation | None, str | None, _FieldContext | None]
        ] = {}
        self.alias_nulls: dict[SymbolId, bool | None] = {}
        self.helpers: dict[SchemaLocation, list[TypeProjection]] = {}
        self.texts: dict[TypeUseId, bool] = {}
        self.excluded: dict[tuple[SymbolId, str], DataModelFieldBase] = {}
        self.binaries: dict[tuple[SymbolId, Direction], SchemaLocation | None] = {}
        self.symbol_table: dict[SymbolId, FinalModelSymbol] = {}
        self.copies = {
            location
            for old, _, merged in cast("RecordingGenerationStore", parser.generation_store).redirects
            if merged and (location := location_of(old)) is not None
        }
        self.variants = any(_rw_variant_of(key) for key in self.parser.model_resolver.references)

    @staticmethod
    def model_location(model: DataModel) -> SchemaLocation | None:
        """Return the location the parser recorded for the schema a model is built from (``reference.location_of``).

        A read or write variant shares its base schema's location and a model that reuse made shares the replaced
        model's.
        """
        return location_of(model.reused_reference or model.reference)

    def address(self, module: ModulePath, result: Result) -> ModelArtifactAddress:
        if isinstance(results := self.binder.results, str):
            return ModelArtifactAddress("single", (self.output.name,), self.model_package, ())
        key = written_path(module, treat_dot_as_module=self.parser.treat_dot_as_module)
        return ModelArtifactAddress(
            key,
            key,
            self.model_package,
            tuple(other for other, value in results.items() if value is result and other != key),
        )

    def lightweight(
        self, declaration: SchemaLocation, direction: Direction = "neutral", *, inline: bool = False
    ) -> TypeProjection:
        """Project a subschema that no model field holds: a referenced model, or the parser's model-free type."""
        projector = self.binder.projector(direction, inline=inline)
        with self.free_type(declaration) as found:
            if isinstance(found, DataType):
                return projector.project(found)
            return TypeProjection(None, _NotEmittedError.reason) if found is None else projector.declaration(found)

    @contextmanager
    def free_type(self, declaration: SchemaLocation) -> Generator[Reference | DataType | None, None, None]:
        """Yield what a subschema no model field holds binds: the model it references, or the parser's model-free type.

        The model-free type is built for the caller and dropped when it is done.
        """
        parser = self.parser
        schema = parser.free_schemas[declaration]
        if (
            schema.ref is not None
            and (target := parser._schema_reference_location(declaration.document, schema.ref)) is not None  # pyright: ignore[reportPrivateUsage] # noqa: SLF001
            and (reference := parser.model_resolver.references.get(_resolver_key(parser.model_resolver, target)))
            is not None
        ):
            yield reference
            return
        with parser.model_resolver.current_root_context(declaration.document.split("/")):
            data_type = parser._build_lightweight_type(schema)  # pyright: ignore[reportPrivateUsage] # noqa: SLF001
        from datamodel_code_generator._graph_links import drop_tree  # noqa: PLC0415

        try:
            yield data_type
        finally:
            drop_tree(data_type)

    def variant_direction(self, model: DataModel) -> Direction:
        key = self.binder.keys.get(id(model.reference), model.reference.path)
        return variant[1] if (variant := _rw_variant_of(key)) else "neutral"

    def symbols(self) -> tuple[tuple[FinalModelSymbol, ...], tuple[ModelArtifactAddress, ...]]:
        binder = self.binder
        symbols: list[FinalModelSymbol] = []
        artifacts: dict[ModelArtifactAddress, None] = {}
        for module, models, result in binder.outputs:
            artifacts[address := self.address(module, result)] = None
            for model in models:
                symbol = binder.symbols[id(model)]
                policy = binder.policies[id(model)]
                slots = tuple(
                    FieldSlot(self.attempt, symbol, binder.identity(field), index, _field_name(model, field.name))
                    for index, field in enumerate(model.fields)
                )
                self.slots.update((id(field), slot) for field, slot in zip(model.fields, slots, strict=True))
                bases = tuple(
                    binder.symbols[id(final.source)]
                    for base in model.base_classes
                    if base.reference is not None and (final := binder.final(base.reference)) is not None
                )
                values = (
                    [get_raw_enum_member_value(item.default) for item in model.fields] if policy.kind == "enum" else []
                )
                symbols.append(
                    FinalModelSymbol(
                        symbol,
                        binder.identity(model),
                        binder.identity(model.reference),
                        policy.backend,
                        policy.kind,
                        model.reference.name.rsplit(".", 1)[-1],
                        address,
                        len(symbols),
                        bases,
                        slots,
                        model.IS_ALIAS,
                        _model_facts(model, policy),
                        tuple(map(_literal_scalar, values)),
                        binder.source(model.reference),
                        self.reused_discriminator(model),
                        tuple(map(json_type, values)),
                    )
                )
        return tuple(symbols), tuple(artifacts)

    def reused_discriminator(self, model: DataModel) -> UnionDiscriminator | None:
        """Return the discriminator the own schema of the union a model that reuse replaced declares."""
        original = self.binder.reused(model)
        return (
            None
            if original is None or not original.fields
            else self.binder.projector("neutral").selector(original.fields[0].data_type)
        )

    def field_facts(self, symbols: tuple[FinalModelSymbol, ...]) -> None:
        self.symbol_table.update((symbol.id, symbol) for symbol in symbols)
        for model, symbol in zip(self.binder.models, symbols, strict=True):
            self.locations[symbol.id] = self.model_location(model)
        projector = self.binder.projector("neutral")
        resolver, model_type = self.parser.model_resolver, self.parser.field_name_model_type
        for model, symbol in zip(self.binder.models, symbols, strict=True):
            if (backend := symbol.backend) is None or symbol.kind == "enum":
                continue
            for field, slot in zip(model.fields, symbol.fields, strict=True):
                if (projected := projector.project(field.data_type).value) is None:
                    continue
                explicit = resolver.explicit_field_alias(field, model.reference, model_type)
                self.facts[slot] = _field_facts(
                    field,
                    projected,
                    backend,
                    symbol.facts,
                    self.context(model, field)[3],
                    explicit_name=explicit is not None and explicit[0] == field.name,
                )

    def context(
        self, model: DataModel, field: DataModelFieldBase
    ) -> tuple[FieldKind, SchemaLocation | None, str | None, _FieldContext | None]:
        """Return a field's member kind, property declaration, wire name and, for a property, its producer facts.

        The property declaration is where the parser built the field (``reference.field_origin_of``); a tag the
        discriminator pass appends is declared where the model's schema declares the property. A field typed as a
        literal is a synthetic tag when the model's own schema does not declare it.
        """
        if (known := self.contexts_by_field.get(id(field))) is not None:
            return known
        location = self.locations.get(self.binder.symbols[id(model)])
        wire_name: str | None = _wire_name(field)
        kind: FieldKind = "property"
        schema: SchemaLocation | None = None
        origin = field_origin_of(field)
        if model.IS_ALIAS or model.IS_ROOT_MODEL:
            kind, wire_name, schema = "root_value", None, location
            if (
                location is not None
                and (values := _map_values(field.data_type)) is not None
                and type_origin_of(values) == (additional := location.child("additionalProperties"))
            ):
                schema = additional
        elif field.name is not None and field.name == model.TYPED_EXTRA_FIELD_NAME:
            kind = "additional_properties"
            schema = None if location is None else location.child("additionalProperties")
        elif location is not None and wire_name is not None and origin is not None:
            schema = self.declaration(model, wire_name) if origin.kind == "tag" else origin.location
            if (field.data_type.literals or field.data_type.enum_member_literals) and (
                schema is None or (model.base_classes and not _below(schema, location))
            ):
                kind = "discriminator_synthetic"
            elif origin.kind == "required-only":
                kind = "required_only"
        context = (
            _FieldContext(self.parser.nullable(field) or field.nullable is True, self.preexisting_null(field.data_type))
            if kind == "property"
            else None
        )
        known = self.contexts_by_field[id(field)] = kind, schema, wire_name, context
        return known

    def declaration(self, model: DataModel, wire_name: str) -> SchemaLocation | None:
        """Return where the schema of a model declares a property, as the field its bases build for it is located.

        A model that reuse made declares its properties as the model it replaced does.
        """
        pending = [model]
        while pending:
            for field in (replaced := self.binder.reused(current := pending.pop()) or current).fields:
                origin = field_origin_of(field)
                if _wire_name(field) == wire_name and origin is not None and origin.kind == "property":
                    return origin.location
            pending.extend(
                source
                for base in reversed(replaced.base_classes)
                if base.reference is not None and isinstance(source := base.reference.source, DataModel)
            )
        return None

    def preexisting_null(self, data_type: DataType) -> bool | None:
        """Return whether a field's own type accepts None before the field adds optionality.

        A reference accepts the null of the schema it names (``DataType.names_nullable_model``), an alias that of its
        value.
        """
        pending = [data_type]
        references: dict[int, tuple[Reference, bool]] = {}
        opaque = False
        python_imports = self.parser._python_imports  # pyright: ignore[reportPrivateUsage] # noqa: SLF001
        while pending:
            current = pending.pop()
            if current.is_optional or current.type == "None":
                return True
            if current._is_container:  # pyright: ignore[reportPrivateUsage] # noqa: SLF001
                continue
            if (reference := current.reference) is not None:
                named = references.get(id(reference), (reference, False))[1]
                references[id(reference)] = reference, named or current.names_nullable_model
            opaque |= (
                current.python_type is not None
                or current.is_custom_type
                or current.type == "Any"
                or (
                    (import_ := current.import_) is not None
                    and python_imports is not None
                    and python_imports[0].get(_import_path(import_)) is import_
                )
            )
            pending.extend(current.data_types)
        unknown = opaque
        for reference, named in references.values():
            binding = self.binder.reference(reference, "neutral")
            nullable = (
                None if binding is None else self.alias_nullable(binding.symbol, set()) if binding.is_alias else named
            )
            if nullable is True:
                return True
            unknown |= nullable is None
        return None if unknown else False

    def alias_nullable(self, symbol: SymbolId, active: set[SymbolId]) -> bool | None:
        """Return whether a type alias's value accepts None, None when its producer is unknown."""
        if symbol in self.alias_nulls:
            return self.alias_nulls[symbol]
        model = self.binder.models[symbol]
        if type(model) is pydantic_v2.RootModelTypeAlias:
            return False
        if symbol in active or len(model.fields) != 1:
            return None
        active.add(symbol)
        field = model.fields[0]
        value = self.binder.projector("neutral").project(field.data_type).value
        direct = field.nullable is True or (field.nullable is None and field.required and bool(field.type_has_null))
        unknown = value is None
        references: list[SymbolId] = []
        pending: list[TypeView] = [] if value is None else [value]
        while pending:
            item = pending.pop()
            direct = direct or is_none(item)
            match item:
                case UnionType():
                    pending.extend(item.members)
                case GeneratedSymbolType() if self.binder.models[item.symbol].IS_ALIAS:
                    references.append(item.symbol)
                case BoundType():
                    unknown = True
                case _:
                    pass
        null_in_annotation = value is not None and _annotation_null(field, value)
        result: bool | None
        if direct and null_in_annotation:
            result = True
        elif unknown or bool(direct) != null_in_annotation:
            result = None
        else:
            states = {self.alias_nullable(reference, active) for reference in references}
            result = True if True in states else None if None in states else False
        active.discard(symbol)
        self.alias_nulls[symbol] = result
        return result

    def parent_first(self, symbol: SymbolId, bases: dict[SymbolId, tuple[SymbolId, ...]]) -> tuple[SymbolId, ...]:
        visited: set[str] = set()
        order: list[SymbolId] = []
        pending = [(symbol, False)]
        while pending:
            current, leaving = pending.pop()
            if leaving:
                order.append(current)
                continue
            if (path := self.binder.models[current].reference.path) in visited:
                continue
            visited.add(path)
            pending.append((current, True))
            pending.extend((parent, False) for parent in reversed(bases[current]))
        return tuple(order)

    def field_bindings(self, symbols: tuple[FinalModelSymbol, ...]) -> tuple[FieldUseBinding, ...]:
        """Bind each model's effective fields, inherited ones included, in the order its class declares them."""
        models = self.binder.models
        bases = {symbol.id: symbol.bases for symbol in symbols}
        unknown = {
            symbol.id
            for model, symbol in zip(models, symbols, strict=True)
            if len(symbol.bases) != sum(base.reference is not None for base in model.base_classes)
        }
        bindings: list[FieldUseBinding] = []
        for model, symbol in zip(models, symbols, strict=True):
            if symbol.kind == "enum":
                continue
            functional = symbol.facts is not None and symbol.facts.functional_typeddict
            order = (
                self.parent_first(symbol.id, bases)
                if functional
                else linearize([symbol.id], lambda item: item, bases.__getitem__, rule="python")
            )
            if order is None or any(owner in unknown for owner in order):
                continue
            fields = collect_members(
                order[::-1] if functional else order,
                lambda owner: models[owner].fields,
                _wire_name if functional else lambda field: self.slots[id(field)].name,
                bases.__getitem__,
                lambda item: item,
                rule="nearest" if functional else model._BASE_FIELDS,  # noqa: SLF001
            )
            members = self.variant_exclusions(
                model, symbol, [self.member(symbol, models[owner], field) for owner, field in fields.values()]
            )
            self.members[symbol.id] = members
            bindings.extend(members)
        return tuple(bindings)

    def member(self, symbol: FinalModelSymbol, owner: DataModel, field: DataModelFieldBase) -> FieldUseBinding:
        slot = self.slots[id(field)]
        facts = self.facts.get(slot)
        kind, schema, wire_name, _ = self.context(owner, field)
        source = None if schema is None else self.documents.location(schema, "schema")
        return FieldUseBinding(
            kind,
            wire_name,
            symbol.id,
            slot,
            facts,
            source,
            "neutral",
            "tag" if facts is not None and not facts.backend.emitted.emitted else None,
        )

    def variant_exclusions(
        self, model: DataModel, symbol: FinalModelSymbol, members: list[FieldUseBinding]
    ) -> list[FieldUseBinding]:
        """Add the properties the split of a request or response variant left out, in the order of its fields."""
        split = self.parser.left_out.get(self.binder.keys.get(id(model.reference), model.reference.path))
        if split is None or not split[1] or (direction := self.variant_direction(model)) == "neutral":
            return members
        location = self.locations.get(symbol.id)
        excluded: list[FieldUseBinding] = []
        for field in split[1]:
            origin = field_origin_of(field)
            schema = None if location is None or origin is None else origin.location
            self.excluded[symbol.id, _wire_name(field)] = field
            excluded.append(
                FieldUseBinding(
                    "property",
                    _wire_name(field),
                    symbol.id,
                    None,
                    None,
                    None if schema is None else self.documents.location(schema, "schema"),
                    direction,
                    _rw_variant_flag(direction, "read_only", "write_only"),
                )
            )
        positions = {name: index for index, name in enumerate(map(_wire_name, split[0]))}
        return sorted((*members, *excluded), key=lambda member: positions.get(member.wire_name or "", len(positions)))


class _SchemaUses(_Models):
    """Bind the schemas the models were generated from, nested ones included."""

    def projection(
        self,
        declaration: SchemaLocation,
        projection: Projection,
        direction: Direction,
        *,
        inline: bool = False,
        nullable: bool = False,
    ) -> TypeProjection:
        """Bind an acquired declaration: its emitted model wins over a direct reference type.

        A declaration the parser emitted nothing for, such as a recursive reference with sibling keywords, binds
        the model-free type of its schema, which is the referenced model. An inlined projection reads each alias and
        root model as the type it stands for, and a nullable one, a value an operation sends or receives in a media
        that can carry null, takes the null its schema admits; a form or multipart body has no null to send.
        """
        projector = self.binder.projector(direction, inline=inline)
        match self.bound_node(declaration, projection, direction):
            case Reference() as reference:
                return projector.declaration(reference, nullable=nullable)
            case DataType() as data_type:
                return projector.project(data_type)
            case _:
                pass
        return self.lightweight(declaration, direction, inline=inline)

    def bound_node(
        self, declaration: SchemaLocation, projection: Projection, direction: Direction
    ) -> Reference | DataType | None:
        """Return what a use of a declaration binds: its emitted model's reference, or the declaration's own type.

        None is a declaration the parser emitted nothing for, which binds its schema's model-free type
        (``free_type``).
        """
        binder = self.binder
        parser = self.parser
        engine = parser.acquisitions.get((declaration, projection))
        if engine is None and projection == "value":
            engine = _resolver_key(parser.model_resolver, declaration)
        reference = parser.model_resolver.references.get(engine) if engine is not None else None
        data_type = binder.declaration_types.get(declaration) if projection == "value" else None
        if reference is not None and (
            data_type is None
            or binder.final(reference, direction) is not None
            or binder.unwrapped(reference) is not None
        ):
            return reference
        return data_type

    @contextmanager
    def bound(
        self, declaration: SchemaLocation, projection: Projection, direction: Direction
    ) -> Generator[Reference | DataType | None, None, None]:
        """Yield what a use of a declaration binds (``bound_node``), its schema's model-free type when nothing else."""
        if (found := self.bound_node(declaration, projection, direction)) is not None or (
            declaration not in self.parser.free_schemas
        ):
            yield found
            return
        with self.free_type(declaration) as free:
            yield free

    def held(self, value: TypeView | None, seen: frozenset[SymbolId] = frozenset()) -> SymbolId | None:
        """Return the model a type is, or holds through root models, aliases and a union with None alone, or None."""
        if isinstance(value, UnionType):
            value = sole_present(value)
        symbol = value.symbol if isinstance(value, GeneratedSymbolType) and value.symbol not in seen else None
        members = () if symbol is None else self.members.get(symbol, ())
        root = next(
            (
                facts.type
                for member in members
                if member.member_kind == "root_value" and (facts := member.model_facts) is not None
            ),
            None,
        )
        return symbol if root is None or symbol is None else self.held(root, seen | {symbol})

    def union_members(self, value: TypeView | None, direction: Direction) -> tuple[FieldUseBinding, ...]:
        """Return the properties of a union of models merged by name, the members a multipart body's parts are read by.

        The value of such a union is one of its objects, whose properties are its parts; an alternative that is a union
        of models itself gives its objects (``union_objects``). A name takes the member of the first object that
        declares it in the body's direction, typed as the union of the types its objects give it and required when each
        object requires it; a name every object leaves out of the direction is left out. An alternative that is no
        model declares none; whether the parts of a name agree is the body's to tell (``_Octets._union_parts``).
        """
        if not isinstance(unwrapped(value, self.symbol_table, self.members), UnionType):
            return ()
        objects = [self.members_at(symbol, None, direction) for symbol in self.union_objects(value, frozenset()) or ()]
        declared = [{name: member for name, _, member in property_members(members)} for members in objects]
        merged: dict[str, FieldUseBinding] = {}
        for name in dict.fromkeys(name for found in declared for name in found):
            alike = sorted(
                (found[name] for found in declared if name in found), key=lambda item: item.exclusion is not None
            )
            facts = [item for member in alike if (item := member.model_facts) is not None]
            merged[name] = (
                alike[0]
                if (first := alike[0].model_facts) is None
                else replace(
                    alike[0],
                    model_facts=replace(
                        first,
                        type=_joined(tuple(item.type for item in facts)) or first.type,
                        required=len(facts) == len(declared) and all(item.required for item in facts),
                    ),
                )
            )
        return tuple(merged.values())

    def union_objects(self, value: TypeView | None, seen: frozenset[TypeView]) -> list[SymbolId] | None:
        """Return the models a type is one of: the object it holds (``held``), or those of each member of its union.

        None is for a type that is, or has a member that is, neither, such as an enum, and for a union that holds
        itself.
        """
        if (symbol := self.held(value)) is not None:
            return None if self.symbol_table[symbol].kind in _SCALAR_SYMBOLS else [symbol]
        if not isinstance(found := unwrapped(value, self.symbol_table, self.members), UnionType) or found in seen:
            return None
        objects: list[SymbolId] = []
        for item in present(found):
            if (held := self.union_objects(item, seen | {found})) is None:
                return None
            objects.extend(symbol for symbol in held if symbol not in objects)
        return objects

    def members_at(
        self, symbol: SymbolId, schema: SchemaLocation | None, direction: Direction
    ) -> tuple[FieldUseBinding, ...]:
        """Return a model's members anchored at the schema a use reads, None where its own schema is read.

        A member is where the model's schema declares it, as the parser built its fields
        (``reference.field_origin_of``); a model that reuse made declares them as the model it replaced. A use whose
        schema the parser read as a schema of its own, while the model stands for an equal one at another place, holds
        each member at the same place below its schema.
        """
        members: list[FieldUseBinding] = []
        model = self.binder.models[symbol]
        location = self.locations.get(symbol)
        copy = location is not None and schema is not None and schema != location and schema in self.copies
        for member in self.members.get(symbol, ()):
            match member.member_kind:
                case "property" if member.wire_name is not None and member.exclusion != "tag":
                    found = self.declaration(model, member.wire_name)
                case "root_value" if member.schema is not None and member.schema.pointer.endswith(
                    "/additionalProperties"
                ):
                    found = None if location is None else location.child("additionalProperties")
                case "root_value":
                    found = location
                case "additional_properties":
                    found = None if location is None else location.child("additionalProperties")
                case _:
                    found = None
            if copy and found is not None and location is not None and schema is not None:
                found = _rebased(found, location, schema)
            if found is not None and (anchored := self.documents.location(found, "schema")) != member.schema:
                member = replace(  # noqa: PLW2901
                    member,
                    schema=anchored,
                )
            members.append(replace(member, direction=direction) if direction != member.direction else member)
        return tuple(members)

    def use_id(  # ruff: ignore[too-many-arguments]
        self,
        owner: OperationId | SourceLocation,
        role: TypeUseRole,
        declaration: SchemaLocation,
        use_site: SchemaLocation,
        schema_use: SchemaLocation,
        *,
        projection: Projection = "value",
        location: str | None = None,
        name: str | None = None,
        status: str | None = None,
        media: str | None = None,
        direction: Direction | None = None,
    ) -> TypeUseId:
        """Return the id of a use, in the direction its role reads or sends unless one is given."""
        direction = direction or (
            "response" if role.startswith("response") else "neutral" if role == "schema" else "request"
        )
        locate = self.documents.location
        return TypeUseId(
            owner,
            role,
            locate(use_site, "use"),
            locate(schema_use, "schema"),
            DeclarationId(locate(declaration, "declaration")),
            direction,
            projection,
            location,
            name,
            status,
            media,
        )

    def own_use(
        self, declaration: SchemaLocation, *, projection: Projection = "value", direction: Direction | None = None
    ) -> TypeUseId:
        """Return the id of a schema's use of itself, the neutral value use `schema_uses` binds first by default."""
        owner = self.documents.location(declaration, "schema")
        return self.use_id(
            owner, "schema", declaration, declaration, declaration, projection=projection, direction=direction
        )

    def use(self, use: TypeUseId, schema: SchemaLocation) -> TypeUseId:
        """Bind a use whose id `use_id` built to the type the models give the schema it reads in its direction."""
        role, media, projection, direction = use.role, use.media, use.projection, use.direction
        locate = self.documents.location
        form = media is not None and _media_kind(media) in _FORMS
        nullable = role != "schema" and not form
        projected = self.projection(schema, projection, direction, nullable=nullable)
        held = (
            self.held(projected.value)
            if form or role == "parameter" or role in _HEADER_ROLES
            else projected.value.symbol
            if isinstance(projected.value, GeneratedSymbolType)
            else None
        )
        members = (
            self.members_at(held, schema, direction)
            if held is not None
            else self.union_members(projected.value, direction)
            if media is not None and _multipart(media)
            else ()
        )
        argument = (
            self.projection(schema, projection, direction, inline=True, nullable=nullable).value
            if role == "parameter" and projected.value is not None
            else None
        )
        self.uses[use] = TypeUseBinding(
            use,
            "bound" if projected.value is not None else "invalid",
            projected.value,
            projected.reason,
            members,
            locate(schema, "schema"),
            argument=None if argument is None else self.binder.hints.of(argument),
        )
        return use

    def schema_uses(self) -> None:
        """Bind every schema declaration the models were generated from, then each declared property."""
        declarations: dict[tuple[SchemaLocation, Projection], bool] = dict.fromkeys(self.parser.acquisitions, True)
        for key in self.parser.model_resolver.references:
            document, separator, _ = key.partition("#")
            if not separator or _is_special_path(key) or document not in self.documents.ids:
                continue
            declarations.setdefault((SchemaLocation(document, _pointer_tokens(key) or ()), "value"), False)
        for (declaration, projection), acquired in declarations.items():
            neutral = self.use(self.own_use(declaration, projection=projection), declaration)
            if (
                acquired
                and isinstance(value := self.uses[neutral].type, GeneratedSymbolType)
                and declaration not in self.binder.declaration_types
            ):
                self.relocate(declaration, value.symbol)
            directional = [
                self.directional(declaration, projection, direction, neutral)
                for direction in (_RW_DIRECTIONS if self.variants else ())
            ]
            if not acquired and not any(directional) and self.uses[neutral].type is None:
                del self.uses[neutral]
        for model in self.binder.models:
            for field in model.fields:
                kind, schema, _, _ = self.context(model, field)
                if schema is None:
                    continue
                match kind:
                    case "additional_properties" | "root_value" if schema.tokens[-1:] == ("additionalProperties",):
                        if (values := _map_values(field.data_type)) is not None:
                            self.nested(schema, values)
                    case "root_value":
                        self.nested(schema, field.data_type, own=False)
                    case _:
                        self.nested(schema, field.data_type)

    def directional(
        self, declaration: SchemaLocation, projection: Projection, direction: Direction, neutral: TypeUseId
    ) -> bool:
        """Bind a schema in one direction, keeping the use only when a variant model gives it another type."""
        use = self.use(self.own_use(declaration, projection=projection, direction=direction), declaration)
        if (kept := self.uses[use].type) is None or kept == self.uses[neutral].type:
            del self.uses[use]
            return False
        return True

    def extras(self) -> None:
        """Bind each model's typed additional properties that no field holds."""
        for symbol, location in self.locations.items():
            if (
                location is None
                or (declaration := location.child("additionalProperties")) not in self.parser.free_schemas
            ):
                continue
            use = self.own_use(declaration)
            if (
                use in self.uses
                or (
                    projected := self.lightweight(declaration, self.variant_direction(self.binder.models[symbol]))
                ).value
                is None
            ):
                continue
            self.uses[use] = self.binding(use, projected)

    def binding(self, use: TypeUseId, projected: TypeProjection) -> TypeUseBinding:
        """Bind a use to its projected type, with the members of the model it names."""
        return TypeUseBinding(
            use,
            "bound" if projected.value is not None else "invalid",
            projected.value,
            projected.reason,
            tuple(self.members.get(projected.value.symbol, ()))
            if isinstance(projected.value, GeneratedSymbolType)
            else (),
            use.schema_site,
        )

    def nested(
        self,
        declaration: SchemaLocation,
        data_type: DataType,
        *,
        own: bool = True,
        moved: tuple[SchemaLocation, SchemaLocation] | None = None,
    ) -> None:
        """Collect the type a field holds at a schema below a model, items, values and branches included.

        Each type below it is held at the subschema the parser built it from (``reference.type_origin_of``), when that
        subschema is below the schema; ``moved`` places the types of an equal schema at another place.
        """
        for child in _members_of(data_type):
            origin = type_origin_of(child)
            if moved is not None and origin is not None:
                origin = _rebased(origin, *moved)
            if origin is not None and origin != declaration and _below(origin, declaration):
                self.nested(origin, child, moved=moved)
        if not own:
            return
        projected = self.binder.projector("neutral").project(data_type)
        candidates = self.helpers.setdefault(declaration, [])
        if projected in candidates:
            return
        candidates.append(projected)
        if isinstance(value := projected.value, GeneratedSymbolType) and declaration in self.copies:
            self.relocate(declaration, value.symbol)

    def relocate(self, declaration: SchemaLocation, symbol: SymbolId) -> None:
        """Collect a model's property types at an equal schema the parser read as its own, at the same places in it.

        A model that reuse made holds no field of its own: the model reuse kept holds the properties.
        """
        model = self.binder.kept(self.binder.models[symbol])
        if (location := self.locations.get(self.binder.symbols[id(model)])) is None or location == declaration:
            return
        for field in model.fields:
            kind, schema, _, _ = self.context(model, field)
            if kind == "property" and schema is not None and _below(schema, location):
                self.nested(_rebased(schema, location, declaration), field.data_type, moved=(location, declaration))

    def helper_uses(self) -> None:
        """Bind each collected schema to its one type, or refuse it when fields hold it as different types."""
        for declaration, candidates in self.helpers.items():
            use = self.own_use(declaration)
            self.uses.setdefault(
                use,
                TypeUseBinding(use, "invalid", None, "BND_AMBIGUOUS_REPLACEMENT", schema=use.schema_site)
                if len(candidates) > 1
                else self.binding(use, candidates[0]),
            )


class _UseFacts(_SchemaUses):
    """Read how a use's value is written as text from the type and members it binds, once every schema use is bound.

    A value is laid out as its type is (``layout``), its kinds are those of its types (``_target_types.lexical_kind``),
    and its other properties are what the parser reads the schema of the model it holds to say (``others``).
    """

    def layout(self, value: TypeView | None) -> Literal["object", "array", "scalar"]:
        """Return how a type's values are laid out, through aliases, root models and a union with None alone.

        A class model or a mapping is an object, an item container an array, a union of members laid out alike is laid
        out as they are, and any other type is a scalar.
        """
        found = unwrapped(value, self.symbol_table, self.members)
        if isinstance(found, UnionType):
            layouts = {self.layout(member) for member in present(found)}
            return layouts.pop() if len(layouts) == 1 else "scalar"
        if (
            isinstance(found, GeneratedSymbolType) and self.symbol_table[found.symbol].kind not in _SCALAR_SYMBOLS
        ) or contained(found, "values", self.symbol_table, self.members) is not None:
            return "object"
        return "array" if self.items(found) is not None else "scalar"

    def kind(self, value: TypeView | None) -> LexicalKind | None:
        """Return the lexical kind of a text leaf's type (``_target_types.lexical_kind``)."""
        return lexical_kind(value, self.symbol_table, self.members)

    def items(self, value: TypeView | None) -> TypeView | None:
        """Return the items type of the item container a type stands for (``_target_types.contained``)."""
        return contained(value, "items", self.symbol_table, self.members)

    def named_models(self, value: TypeView | None) -> list[SymbolId]:
        """Return the models a type names, then those the aliases and root models among them stand for, in order.

        A union with None alone names what its other member names (``_target_types.wrapper_chain``).
        """
        return wrapper_chain(value, self.symbol_table, self.members)[1]

    def holder(self, value: TypeView | None, source: SourceLocation) -> SourceLocation:
        """Return where the schema of a type's values is: the last named model's (``named_models``), else ``source``."""
        location = next(
            (
                found
                for symbol in reversed(self.named_models(value))
                if (found := self.locations.get(symbol)) is not None
            ),
            None,
        )
        return source if location is None else self.documents.location(location, "schema")

    def object_symbols(self, value: TypeView | None) -> list[SymbolId]:
        """Return the models a type names (``named_models``), then the bases of the class it holds.

        The bases are in method resolution order (``_mro.linearize``).
        """
        found = self.named_models(value)
        if found and self.symbol_table[found[-1]].kind not in WRAPPER_KINDS:
            order = linearize([found[-1]], lambda item: item, lambda item: self.symbol_table[item].bases, rule="python")
            found.extend(base for base in (order or [])[1:] if base not in found)
        return found

    def use_others(self, use: TypeUseBinding) -> _Others | None:
        """Return what the model a use's value holds says of its other properties (``others``), or None.

        None is for a value laid out as no object (``layout``).
        """
        if self.layout(value := use.type) != "object":
            return None
        return self.others(value, use.members, self.holder(value, use.schema or use.id.schema_site), use.id.direction)

    def others(
        self, value: TypeView | None, members: tuple[FieldUseBinding, ...], holder: SourceLocation, direction: Direction
    ) -> _Others:
        """Return what the model a type holds says of its other properties, and the type of their values.

        The first of the models it names and their bases (``object_symbols``) whose schema says anything of them says
        it, as the parser reads that schema (``_ObjectFacts``); any of them with pattern properties gives the type
        them. A mapping no model says anything for types them by its values. Typed ones are the values of the field
        the model keeps them in, else of the model-free type their schema has in the use's direction, else of its
        mapping.
        """
        facts = [
            (symbol, found)
            for symbol in self.object_symbols(value)
            if (schema := self.locations.get(symbol)) is not None
            and (found := self.parser.object_facts.get(schema)) is not None
        ]
        patterns = any(found.patterns for _, found in facts)
        owner, said = next(
            ((symbol, found.others) for symbol, found in facts if found.others != "absent"), (None, "absent")
        )
        mapping = contained(value, "values", self.symbol_table, self.members)
        if said == "absent" and mapping is not None:
            said = "schema"
        if said != "schema":
            return _Others(said, patterns)
        if (location := None if owner is None else self.locations.get(owner)) is None:
            return _Others(said, patterns, holder.child("additionalProperties"), mapping)
        declaration = location.child("additionalProperties")
        kept = next(kept_values(members, self.symbol_table, self.members), None)
        free = declaration if kept is None and declaration in self.parser.free_schemas else None
        values = (
            kept[1] if kept is not None else None if free is None else self.lightweight(free, direction).value
        ) or mapping
        source = self.documents.location(declaration, "schema")
        return _Others(said, patterns, source, values, None if kept is None else kept[0], free)

    def member_shape(  # noqa: PLR0913
        self,
        name: str,
        value: TypeView | None,
        source: SourceLocation,
        direction: Direction,
        *,
        form: bool,
        seen: frozenset[SourceLocation] = frozenset(),
    ) -> MemberShape:
        """Return how a member of a type is written as text, its kind reported where its schema is.

        An item container member of a form repeats, in its items' kind, reported where its items' schema is
        (``repeated``). A member of a parameter value whose values have no one kind has their shape (``bracketed``).
        """
        if form and (item := self.items(value)) is not None:
            return MemberShape(name, KindAt(self.repeated(value, source), self.kind(item)), repeated=True)
        if (kind := self.kind(value)) is not None or form:
            return MemberShape(name, KindAt(source, kind))
        return MemberShape(name, KindAt(source, None), value=self.bracketed(value, source, direction, seen))

    def bracketed(
        self, value: TypeView | None, source: SourceLocation, direction: Direction, seen: frozenset[SourceLocation]
    ) -> TextShape | None:
        """Return the shape of values that have no one kind as one parameter value, which the bracket form writes.

        The schemas of the values that hold them are `seen`; values of one of those, a recursive value, have none.
        """
        if (holder := self.holder(value, source)) in seen:
            return None
        members = self.held_members(value, direction)
        return self.shape(value, source, members, direction, form=False, seen=seen | {holder})

    def repeated(self, value: TypeView | None, source: SourceLocation) -> SourceLocation:
        """Return where the schema of the items of a form member's values is, its own where it does not repeat."""
        return source if self.items(value) is None else self.holder(value, source).child("items")

    def shape(  # noqa: PLR0911, PLR0913
        self,
        value: TypeView | None,
        source: SourceLocation,
        members: tuple[FieldUseBinding, ...],
        direction: Direction,
        *,
        form: bool,
        seen: frozenset[SourceLocation] = frozenset(),
    ) -> TextShape:
        """Return how a type's values are written as one parameter value, or as the members of a URL-encoded form.

        `source` is where the use's schema is, and `members` those of the model the type holds. An array has its
        items' kind and a scalar its own, and a union of items and arrays of them is an array of their one kind that
        takes an item alone (``single``); an object has the model's members, then its other properties
        (``others``): none when it allows none, those of its typed values' kind, else any string. A form that is no
        object, and an object with pattern properties, have no builtin encoding. As one parameter value, items, a
        scalar and members without one kind have their shapes for the bracket form (``bracketed``), and a union of
        members laid out unlike each other the shape of each.
        """
        holder = self.holder(value, source)
        layout = self.layout(value)
        if layout == "array" and not form:
            site = holder.child("items")
            if (kind := self.kind(items := self.items(value))) is not None:
                return TextShape("array", KindAt(site, kind))
            return TextShape("array", KindAt(site, None), items=self.bracketed(items, site, direction, seen))
        if not form and (kind := self.single(value)) is not None:
            return TextShape("array", KindAt(holder, kind), single=True)
        found = unwrapped(value, self.symbol_table, self.members)
        if layout != "object":
            if form:
                return TextShape("object", problem=(holder, _NOT_OBJECT))
            if (kind := self.kind(value)) is not None or not isinstance(found, UnionType):
                return TextShape("scalar", KindAt(holder, kind))
            choices = tuple(
                self.shape(item, holder, self.held_members(item, direction), direction, form=False, seen=seen)
                for item in present(found)
            )
            return TextShape("scalar", KindAt(holder, None), choices=choices)
        if isinstance(found, UnionType):
            return self.union_shape(present(found), source, direction, form=form, seen=seen)
        others = self.others(value, members, holder, direction)
        if others.patterns:
            return TextShape("object", problem=(holder, _PATTERNS))
        additional = (
            None
            if others.said == "closed"
            else MemberShape("")
            if others.source is None
            else self.member_shape("", others.values, others.source, direction, form=form, seen=seen)
        )
        return TextShape(
            "object",
            members=tuple(
                self.member_shape(name, facts.type, schema, direction, form=form, seen=seen)
                for name, schema, facts in _typed(members)
            ),
            additional=additional,
        )

    def single(self, value: TypeView | None) -> LexicalKind | None:
        """Return the one kind of a union of items and arrays of them, or None for any other type.

        Its scalar members and the items of its array members are all of that kind; a member that is an object, or a
        member or items of another kind or of none, leaves it without one.
        """
        if not isinstance(found := unwrapped(value, self.symbol_table, self.members), UnionType):
            return None
        alternatives = present(found)
        layouts = [self.layout(member) for member in alternatives]
        if set(layouts) != {"scalar", "array"}:
            return None
        kinds = {
            self.kind(self.items(member) if layout == "array" else member)
            for member, layout in zip(alternatives, layouts, strict=True)
        }
        return kinds.pop() if len(kinds) == 1 else None

    def union_shape(
        self,
        alternatives: tuple[TypeView, ...],
        source: SourceLocation,
        direction: Direction,
        *,
        form: bool,
        seen: frozenset[SourceLocation] = frozenset(),
    ) -> TextShape:
        """Return how the values of a union of objects are written as text: the members of its objects, merged by name.

        A name several objects declare has the kind they agree on, and none (refused, ``_kind``) when they differ; an
        object that does not declare it but allows other properties of one kind takes part in that agreement too. Other
        properties are those of the objects that allow them, in their kind when all agree on one, else any string; an
        object with pattern properties has no members and allows any string.
        """
        shapes = [
            self.shape(item, source, self.held_members(item, direction), direction, form=form, seen=seen)
            for item in alternatives
        ]
        merged: dict[str, MemberShape] = {}
        for shape in shapes:
            for member in shape.members:
                merged[member.name] = _agreed(merged[member.name], member) if member.name in merged else member
        others = [MemberShape("") if shape.problem else shape.additional for shape in shapes]
        for shape, other in zip(shapes, others, strict=True):
            if other is not None and other.kind is not None:
                declared = {member.name for member in shape.members}
                for name in merged.keys() - declared:
                    merged[name] = _agreed(merged[name], other)
        allowed = [other for other in others if other is not None]
        additional = (
            None
            if not allowed
            else allowed[0]
            if all(_same_kind(allowed[0], other) for other in allowed)
            else MemberShape("")
        )
        return TextShape("object", members=tuple(merged.values()), additional=additional)

    def encoding(self, use: TypeUseBinding, *, members: bool) -> EncodingFacts:
        """Return how a use's value is written as text: as one value, as form members, and member by member.

        Only a form's members, which its encodings can give a style, are written member by member, each with the
        members of the model its type holds, and only a form has other properties of its own.
        """
        value, direction = use.type, use.id.direction
        source = use.schema or use.id.schema_site
        extras: tuple[SourceLocation, TypeView] | None = None
        declared = False
        if members and (others := self.use_others(use)) is not None:
            declared = others.said in {"allowed", "schema"}
            if others.source is not None and others.values is not None:
                extras = self.repeated(others.values, others.source), others.values
        return EncodingFacts(
            self.shape(value, source, use.members, direction, form=False),
            self.shape(value, source, use.members, direction, form=True),
            tuple(
                (name, self.shape(facts.type, schema, self.held_members(facts.type, direction), direction, form=False))
                for name, schema, facts in _typed(use.members)
            )
            if members
            else (),
            extras,
            declared=declared,
        )

    def held_members(self, value: TypeView | None, direction: Direction) -> tuple[FieldUseBinding, ...]:
        """Return the members of the model a type holds (``held``), where its own schema declares them."""
        return () if (symbol := self.held(value)) is None else self.members_at(symbol, None, direction)

    def takes_object(self, value: TypeView | None) -> bool:
        """Return whether a multipart body's type is one object, takes any value, or is no type the use binds.

        A union of several objects declares no members of its own (``_union_parts`` merges those of its objects).
        """
        return (
            value is None
            or untyped(value, self.symbol_table, self.members)
            or (
                self.layout(value) == "object"
                and not isinstance(unwrapped(value, self.symbol_table, self.members), UnionType)
            )
        )

    def encodings(self) -> None:
        """Record how each parameter, header and form use's value is written as text, once every use is bound."""
        for use, members in self.texts.items():
            self.uses[use] = replace(self.uses[use], encoding=self.encoding(self.uses[use], members=members))


class _Octets(_UseFacts):
    """Read which values the models hold as raw octets: a multipart body's files, a raw body, and bytes sent as text.

    A value holds them where the parser reads a string as raw octets (``reference.binary_content_of``).
    """

    def field_of(self, member: FieldUseBinding) -> DataModelFieldBase:
        """Return the field a member binds: its slot's, or the one the split of a read or write variant left out."""
        if (slot := member.slot) is not None:
            return self.binder.models[slot.symbol].fields[slot.index]
        return self.excluded[member.consumer, member.wire_name or ""]

    def alternatives(
        self,
        node: Reference | DataType,
        direction: Direction,
        constrained: bool = False,  # noqa: FBT001, FBT002
        active: frozenset[SymbolId] = frozenset(),
    ) -> Iterator[tuple[Reference | DataType, bool]]:
        """Yield the nodes a value is one of, None aside, and whether a model it is read through constrains it.

        A union is each of its members, and an alias or root model the type it stands for, as an inlining projector
        reads them (``_Projector._reference_value``); a container, a scalar and a reference to any other model are one
        node each.
        """
        reference = node if isinstance(node, Reference) else node.reference
        if isinstance(node, DataType):
            if node in CONTAINERS or node.python_type is not None or node.type is not None:
                if node.type != "None":
                    yield node, constrained
                return
            if node.data_types:
                for child in node.data_types:
                    yield from self.alternatives(child, direction, constrained, active)
                return
            if node.literals or node.enum_member_literals:
                reference = None
        binder = self.binder
        if reference is None:
            yield node, constrained
            return
        if (final := binder.final(reference, direction)) is None:
            root = binder.unwrapped(reference)
            yield from () if root is None else self.alternatives(root, direction, constrained, active)
            return
        model = _source(final)
        if (
            (symbol := binder.symbols[id(model)]) not in active
            and binder.policies[id(model)].kind in WRAPPER_KINDS
            and (field := binder.root_field(model)) is not None
        ):
            wrapped = constrained or binder.constrains(model)
            yield from self.alternatives(field.data_type, direction, wrapped, active | {symbol})
            return
        yield node, constrained

    def view(self, leaf: Reference | DataType, direction: Direction, *, inline: bool = True) -> TypeView | None:
        """Return the type the models give one node of a value, each alias and root model by the type it stands for.

        Without `inline`, aliases and root models are the types they name.
        """
        projector = self.binder.projector(direction, inline=inline)
        return (projector.declaration(leaf) if isinstance(leaf, Reference) else projector.project(leaf)).value

    @cached_property
    def binary_type(self) -> TypeView | None:
        """Return the type the models give a raw binary string."""
        binary = self.parser.data_type_manager.get_data_type(Types.binary)
        return self.binder.projector("neutral", inline=True).project(binary).value

    def octet(self, leaf: Reference | DataType, direction: Direction) -> bool:
        """Return whether the models hold a node's values as raw octets.

        They do for a string the parser reads as raw octets (``reference.binary_content_of``) that they type as bytes:
        their raw binary string, or bytes they constrain.
        """
        return (
            isinstance(leaf, DataType)
            and binary_content_of(leaf)
            and (value := self.view(leaf, direction)) is not None
            and any(
                member.base == _BUILTINS["bytes"] if isinstance(member, ConstructorType) else member == self.binary_type
                for member in present(value)
            )
        )

    def builtin_types(self, leaf: DataType, direction: Direction) -> frozenset[str]:
        """Return the builtins a node's type is, constrained or not; empty when any member is another type.

        ``builtins.str`` imported (a ``--type-overrides`` value) is the builtin ``str``.
        """
        value = self.view(leaf, direction)
        names = [
            _builtin_name(member.base if isinstance(member, ConstructorType) else member)
            for member in (() if value is None else present(value))
        ]
        return frozenset() if None in names else frozenset(cast("list[str]", names))

    def part_of(self, node: Reference | DataType | None, value: TypeView | None, direction: Direction) -> PartSchema:
        """Return how the values of a type are sent as parts.

        They are files when every node they, or their items when they repeat, are one of holds raw octets as the
        parser reads them (``reference.binary_content_of``) and the models type it as bytes or as plain ``str``
        (``octet``, ``text_files``), which is sent as UTF-8 text; a binary string the models give another type, such as
        one a ``--type-overrides`` names, is sent as the part of that type. They repeat as an item container, one part
        for each item; and they are structured when one of their alternatives is a model, a mapping or an item
        container.
        """
        leaves = [] if node is None else [leaf for leaf, _ in self.alternatives(node, direction)]
        if (item := self.items(value)) is not None:
            leaves = [
                inner
                for leaf in leaves
                if isinstance(leaf, DataType) and leaf in ITEM_CONTAINERS and leaf.data_types
                for inner, _ in self.alternatives(leaf.data_types[0], direction)
            ]
        found = unwrapped(value if item is None else item, self.symbol_table, self.members)
        binary = [leaf for leaf in leaves if isinstance(leaf, DataType) and binary_content_of(leaf)]
        types = [self.builtin_types(leaf, direction) for leaf in binary]
        file = (
            bool(leaves)
            and len(binary) == len(leaves)
            and all(
                self.octet(leaf, direction) or (bool(names) and names <= _FILE_TYPES)
                for leaf, names in zip(binary, types, strict=True)
            )
        )
        return PartSchema(
            file=file,
            repeated=item is not None,
            structured=found is not None and any(self.layout(member) != "scalar" for member in present(found)),
            value=value,
            text=file and any("str" in names for names in types),
        )

    def part(self, member: FieldUseBinding) -> PartSchema:
        """Return how a member of a multipart body is sent as parts, by the field it binds in the body's direction."""
        field = self.field_of(member)
        direction = member.direction
        return self.part_of(field.data_type, self.view(field.data_type, direction, inline=False), direction)

    def mapped(self, node: Reference | DataType, direction: Direction) -> DataType | None:
        """Return the values node of the mapping a value is (``alternatives``), None for any other value."""
        return next(
            (
                leaf.data_types[-1]
                for leaf, _ in self.alternatives(node, direction)
                if isinstance(leaf, DataType) and leaf in MAPPINGS and leaf.data_types
            ),
            None,
        )

    @contextmanager
    def values_node(
        self, others: _Others, node: Reference | DataType | None, direction: Direction
    ) -> Generator[Reference | DataType | None, None, None]:
        """Yield the node of the typed other properties of a value (``others``).

        It is the values of its model's kept field, its model-free schema's type, or the values of its mapping.
        """
        if others.free is not None:
            with self.free_type(others.free) as found:
                yield found
            return
        holder = node if others.kept is None else self.field_of(others.kept).data_type
        yield None if holder is None else self.mapped(holder, direction)

    def parts(
        self, use: TypeUseBinding, node: Reference | DataType | None, schema: SchemaLocation
    ) -> tuple[PartFacts, SourceLocation | None]:
        """Return how a multipart body's use encodes the parts of its members and of its other properties.

        It is an object as its type is (``takes_object``), each property member's parts are its field's
        (``part``), and the other parts are none when the model allows no other properties, those of their typed
        values when it types them (``others``), and any value otherwise; a union of models whose properties its use
        merges has the parts of its objects (``_union_parts``). Also return where the first raw binary string sits in a
        part holding no files, which carries it as text (``_text_binary``).
        """
        if (union := self._union_alternatives(use)) is not None:
            return self._union_parts(use, union, schema)
        direction = use.id.direction
        members = [(name, member, self.part(member)) for name, _, member in property_members(use.members)]
        refused = self._text_binary(members, schema)
        named = tuple((name, part) for name, _, part in members)
        extra: PartSchema | Literal["closed"] | None = None
        if (others := self.use_others(use)) is not None:
            if others.said == "closed":
                extra = "closed"
            elif others.source is not None:
                with self.values_node(others, node, direction) as values:
                    typed = others.values if values is None else self.view(values, direction, inline=False)
                    extra = self.part_of(values, typed, direction)
                    if refused is None and not extra.file and values is not None:
                        refused = self.first(values, direction, others.free or schema)
        return PartFacts(self.takes_object(use.type), named, extra), refused

    def _text_binary(
        self, members: Iterable[tuple[str, FieldUseBinding, PartSchema]], schema: SchemaLocation
    ) -> SourceLocation | None:
        """Return where the first raw binary string sits in a member's part holding no files (``first``), or None.

        A member the body's direction leaves out is never sent.
        """
        return next(
            (
                found
                for _, member, part in members
                if not part.file
                and member.exclusion is None
                and (
                    found := self.first(
                        (field := self.field_of(member)).data_type, member.direction, self.origin(field, schema)
                    )
                )
                is not None
            ),
            None,
        )

    def _union_parts(
        self, use: TypeUseBinding, union: list[SymbolId], schema: SchemaLocation
    ) -> tuple[PartFacts, SourceLocation | None]:
        """Return the parts of a multipart use whose type is a union of models, by the members it merges.

        The objects that declare a name agree on its parts: each holds files, repeats and is structured alike, its
        values have one lexical kind (``member_kind``), and a part that repeats or is structured has one type, which
        its items or its JSON are read by; the part reads the union of their values. A body whose objects
        disagree on a name has no object its parts could be read by. It allows no other parts only when each of its
        objects allows no other properties, and any value otherwise.
        """
        direction = use.id.direction
        declared = [self.members_at(symbol, None, direction) for symbol in union]
        found = [{name: self.part(member) for name, _, member in property_members(items)} for items in declared]
        refused = self._text_binary(
            (
                (name, member, item[name])
                for items, item in zip(declared, found, strict=True)
                for name, _, member in property_members(items)
            ),
            schema,
        )
        named: list[tuple[str, PartSchema]] = []
        for name, _, _ in property_members(use.members):
            parts = [item[name] for item in found if name in item]
            shapes = {(part.file, part.repeated, part.structured, part.text) for part in parts}
            kinds = {member_kind(value, self.symbol_table, self.members)[0] for part in parts if (value := part.value)}
            whole = {part.value for part in parts if part.repeated or part.structured}
            if len(shapes) > 1 or len(kinds) > 1 or len(whole) > 1:
                return PartFacts(object=False, members=(), extra=None), None
            named.append((name, replace(parts[0], value=_joined(tuple(part.value for part in parts if part.value)))))
        closed = all(
            self.others(
                item := GeneratedSymbolType(symbol),
                members,
                self.holder(item, use.schema or use.id.schema_site),
                direction,
            ).said
            == "closed"
            for symbol, members in zip(union, declared, strict=True)
        )
        return PartFacts(object=True, members=tuple(named), extra="closed" if closed else None), refused

    def _union_alternatives(self, use: TypeUseBinding) -> list[SymbolId] | None:
        """Return the models of a multipart use whose type is a union of models it merges the members of, or None."""
        if self.takes_object(use.type) or not use.members:
            return None
        return self.union_objects(use.type, frozenset())

    @staticmethod
    def origin(field: DataModelFieldBase, fallback: SchemaLocation) -> SchemaLocation:
        """Return where the parser built a field (``reference.field_origin_of``), ``fallback`` where it records none."""
        return fallback if (origin := field_origin_of(field)) is None or origin.location is None else origin.location

    def first(
        self, node: Reference | DataType | None, direction: Direction, fallback: SchemaLocation
    ) -> SourceLocation | None:
        """Return where the first value the models hold as raw octets sits in a value, None without one (``octet``).

        The walk reads the value's nodes, then the members of each model it references and the model-free type of
        the other properties a model keeps no field for; each node reports where the parser built it
        (``reference.type_origin_of``), else where the field it is in was built, else ``fallback``.
        """
        found = None if node is None else self.reach(node, direction, fallback, {})[0]
        return None if found is None else self.documents.location(found, "schema")

    def reach(
        self, node: Reference | DataType, direction: Direction, fallback: SchemaLocation, walked: dict[SymbolId, int]
    ) -> tuple[SchemaLocation | None, int]:
        """Return the first raw octets a value reaches (``first``), and the earliest walked model it leads back to."""
        low = len(walked)
        pending: list[Reference | DataType] = [node]
        binder = self.binder
        while pending:
            current = pending.pop()
            reference = current if isinstance(current, Reference) else current.reference
            if isinstance(current, DataType):
                if self.octet(current, direction):
                    return type_origin_of(current) or fallback, low
                if reference is None or current.literals or current.enum_member_literals:
                    pending.extend(reversed(current.data_types))
                    continue
            if (final := binder.final(cast("Reference", reference), direction)) is None:
                pending.extend(() if (root := binder.unwrapped(cast("Reference", reference))) is None else (root,))
                continue
            model = _source(final)
            if binder.policies[id(model)].kind == "enum":
                continue
            found, reached = self.reach_model(binder.symbols[id(model)], direction, fallback, walked)
            low = min(low, reached)
            if found is not None:
                return found, low
        return None, low

    def reach_model(
        self, symbol: SymbolId, direction: Direction, fallback: SchemaLocation, walked: dict[SymbolId, int]
    ) -> tuple[SchemaLocation | None, int]:
        """Return the first raw octets a model's members reach, then those of its model-free other properties.

        `walked` holds the models of the walk whose answer is pending, in their walk order. A model that finds them,
        or leads back to none walked before it, settles itself and every pending model walked after it, since they
        all reach it; each model is walked once per direction (``binaries``).
        """
        if (key := (symbol, direction)) in self.binaries:
            return self.binaries[key], len(walked)
        if (order := walked.get(symbol)) is not None:
            return None, order
        order = walked[symbol] = len(walked)
        low = order
        found: SchemaLocation | None = None
        location = self.locations.get(symbol)
        members = self.members.get(symbol, ())
        for member in members:
            if member.slot is None:
                continue
            field = self.field_of(member)
            found, reached = self.reach(field.data_type, direction, self.origin(field, location or fallback), walked)
            low = min(low, reached)
            if found is not None:
                break
        free = None if location is None else location.child("additionalProperties")
        if (
            found is None
            and free in self.parser.free_schemas
            and not any(member.member_kind == "additional_properties" for member in members)
        ):
            with self.free_type(cast("SchemaLocation", free)) as values:
                if values is not None:
                    found, reached = self.reach(values, direction, cast("SchemaLocation", free), walked)
                    low = min(low, reached)
        while (found is not None or low == order) and len(walked) > order:
            self.binaries[walked.popitem()[0], direction] = found
        return found, low

    def whole(
        self, node: Reference | DataType | None, direction: Direction, kind: MediaKind | None
    ) -> tuple[bool, bool, bool, bool]:
        """Return how a body travels by the models' types of its whole value (``alternatives``).

        A JSON, text or binary media carries it as the raw body when the models hold every alternative as raw octets
        (``octet``), checked by the model when they constrain those bytes, in a type or in a model it is read through:
        raw binary is a whole body (OpenAPI 3.1.1 "Working with Binary Data"), and a whole body written as a binary
        string is the raw octets of its media type (`contentMediaType` set to it). Any other body that holds them in
        part carries them as text, which is refused. A binary media whose
        alternatives are all enums or literals of strings carries text read by the model.
        """
        leaves = [] if node is None else list(self.alternatives(node, direction))
        raw = [self.octet(leaf, direction) for leaf, _ in leaves]
        octets = kind in _RAW_MEDIA and bool(raw) and all(raw)
        checked = octets and any(
            constrained or isinstance(self.view(leaf, direction), ConstructorType) for leaf, constrained in leaves
        )
        text = (
            kind == "binary"
            and not any(raw)
            and bool(leaves)
            and all(self.strings(self.view(leaf, direction)) for leaf, _ in leaves)
        )
        return octets, not octets and any(raw), checked, text

    def strings(self, value: TypeView | None) -> bool:
        """Return whether a type is an enum or literal of strings alone, None aside."""
        return value is not None and all(
            (isinstance(member, GeneratedSymbolType) and self.string_enum(member.symbol))
            or (
                isinstance(member, LiteralType)
                and all(
                    self.string_enum(item.symbol) if isinstance(item, GeneratedEnumMember) else item.kind == "str"
                    for item in member.values
                )
            )
            for member in present(value)
        )

    def string_enum(self, symbol: SymbolId) -> bool:
        """Return whether a symbol is an enum of strings alone."""
        return (declared := self.symbol_table[symbol]).kind == "enum" and set(declared.value_types) == {"string"}


def _same_kind(first: MemberShape, other: MemberShape) -> bool:
    """Return whether two members of objects are written alike: in one lexical kind, both repeated or both not.

    Members whose values have no one kind (``MemberShape.value``) are not written alike.
    """
    return (
        first.repeated == other.repeated
        and (first.kind and first.kind.kind) == (other.kind and other.kind.kind)
        and first.value is other.value is None
    )


def _agreed(first: MemberShape, other: MemberShape) -> MemberShape:
    """Return the member two objects declare by one name: the first when both write it alike, else with no kind."""
    if _same_kind(first, other):
        return first
    return replace(first, kind=KindAt(cast("KindAt", first.kind).source, None), value=None)


def _typed(members: tuple[FieldUseBinding, ...]) -> Iterator[tuple[str, SourceLocation, ModelFieldFacts]]:
    """Yield each property of a model with a field, its allOf branches' included, with its wire name and schema."""
    for name, schema, member in property_members(members):
        if (facts := member.model_facts) is not None:
            yield name, schema, facts


class _Contracts(_Octets):
    """Bind every type use of the operations the target parser walked."""

    def observed(self, document: str) -> bool:
        """Return whether a document is loaded, loading one that nothing but a link or security scheme references.

        A document that fails to load is tried once.
        """
        if document in self.documents.ids:
            return True
        if (
            document in self.documents.unloadable
            or (raw := cast("TargetApiOpenAPIParser", self.parser).load_document(document)) is None
        ):
            self.documents.unloadable.add(document)
            return False
        self.documents.ids[document] = SourceDocumentId(len(self.documents.ids))
        self.documents.documents[document] = raw
        return True

    def record(self, schema: SchemaLocation) -> _SchemaRecord:
        """Return what the walk recorded of an acquired schema."""
        return self.parser.schema_records.get(schema, _UNRECORDED)

    @staticmethod
    def named(raw: YamlValue, holder: SchemaLocation, kind: str) -> Iterator[tuple[str, YamlValue]]:
        """Yield the members of the object at ``holder`` by the keys that locate them.

        A key that is no string, such as a YAML key ``1`` loaded as a number, names no ``kind``: it is a schema error.
        """

        def error_path(key: object) -> list[str]:
            return [str(holder.child(str(key)))]

        for key, value in as_mapping(raw).items():
            yield string_key(key, kind, error_path), value

    def metadata(
        self,
        kind: Literal["link", "callback", "security_scheme"],
        name: str,
        declaration: SchemaLocation,
        use_site: SchemaLocation,
        raw: YamlValue,
    ) -> WireDeclaration:
        """Freeze link, callback and security scheme metadata with every reference edge it follows."""
        value = as_mapping(raw)
        seen = {declaration}
        references: list[SourceReference] = []
        locate = self.documents.location
        while isinstance(ref := value.get("$ref"), str):
            source = locate(declaration, "declaration")
            document = (
                declaration.document
                if ref.startswith("#")
                else cast("TargetApiOpenAPIParser", self.parser).referenced_document(declaration.document, ref)
            )
            if document is not None and not self.observed(document):
                references.append(SourceReference(source, ref, None, "document_not_observed"))
                break
            if document is None or (tokens := _pointer_tokens(ref, strict=True)) is None:
                references.append(SourceReference(source, ref, None, "invalid_pointer"))
                break
            target = SchemaLocation(document, tokens)
            location = locate(target, "declaration")
            if target in seen:
                references.append(SourceReference(source, ref, location, "cycle"))
                break
            if (borrowed := self.documents.borrow(target, MISSING)) is MISSING:
                references.append(SourceReference(source, ref, location, "pointer_missing"))
                break
            if not isinstance(borrowed, dict):
                references.append(SourceReference(source, ref, location, "invalid_target"))
                break
            references.append(SourceReference(source, ref, location, "resolved"))
            seen.add(target)
            declaration, value = target, borrowed
        return WireDeclaration(
            kind,
            name,
            DeclarationId(locate(declaration, "declaration")),
            locate(use_site, "use"),
            _facts(value, ("$ref", *_METADATA_FACTS.get(kind, ())), declaration),
            references=tuple(references),
        )

    def media_facts(
        self, uses: tuple[TypeUseId, ...], schema: SchemaLocation, role: TypeUseRole, media: str | None
    ) -> tuple[TypeUseId, ...]:
        """Record what the models say of a media's own schema keyword, or a parameter's or header's, on its uses.

        A multipart media's uses get its parts (``parts``) and a parameter's schema or content its default and
        keywords; the uses of a parameter's, a header's or a form's media get the text encoding of their values
        (``encodings``). A body's models decide how it travels (``whole``), and a use that carries its values as text
        is refused where the first raw binary string sits in them (``first``), as a multipart body is in a part
        holding no files. A schema with no media (`media` None) is a parameter's or a header's own.
        """
        multipart = media is not None and _multipart(media)
        record = self.record(schema)
        kind = None if media is None else _media_kind(media)
        text = role == "parameter" or role in _HEADER_ROLES
        item = schema.tokens[-1] == "itemSchema"
        for use in uses:
            binding = self.uses[use]
            if text or kind in _FORMS:
                self.texts[use] = kind in _FORMS
            with self.bound(schema, use.projection, use.direction) as node:
                parts, refused = self.parts(binding, node, schema) if multipart else (None, None)
                octets, partly, checked, read = (
                    self.whole(node, use.direction, kind)
                    if kind is not None and not (multipart or text or item)
                    else (False, False, False, False)
                )
                if text or item or kind in _TEXT_MEDIA:
                    refused = self.first(node, use.direction, schema)
            self.uses[use] = replace(
                binding,
                parts=parts,
                default=record.default if role == "parameter" else None,
                keywords=record.keywords,
                binary=None if octets else refused or (use.schema_site if partly else None),
                octets=octets,
                checked=checked,
                text=read,
            )
        return uses

    def media(  # noqa: PLR0913
        self,
        holder: _WalkedObject,
        use_site: SchemaLocation,
        owner: OperationId,
        role: TypeUseRole,
        *,
        status: str | None = None,
        parameter_name: str | None = None,
        parameter_location: str | None = None,
    ) -> tuple[WireDeclaration, ...]:
        locate = self.documents.location
        declaration = holder.declaration
        values: list[WireDeclaration] = []
        for medium in holder.below("media"):
            name = medium.key
            media_declaration, media_use = medium.declaration, use_site.child("content", name)
            uses: list[TypeUseId] = []
            for schema, projections in medium.schemas.items():
                uses.extend(
                    self.media_facts(
                        tuple(
                            self.use(
                                self.use_id(
                                    owner,
                                    role,
                                    declaration,
                                    use_site,
                                    media_use.child(schema.tokens[-1]),
                                    projection=projection,
                                    name=parameter_name,
                                    location=parameter_location,
                                    status=status,
                                    media=name,
                                ),
                                schema,
                            )
                            for projection in projections
                        ),
                        schema,
                        role,
                        name,
                    )
                )
            encodings: list[WireDeclaration] = []
            encoding_declaration = media_declaration.child("encoding")
            if encoding_declaration not in self.ignored_declarations:
                header_role: TypeUseRole = (
                    "response_encoding_header" if role.startswith("response") else "request_encoding_header"
                )
                walked = {encoding.key: encoding for encoding in medium.below("encoding")}
                for property_name, _ in self.named(
                    medium.value.get("encoding"), encoding_declaration, "encoding property name"
                ):
                    encoding = walked[property_name]
                    declared = encoding.declaration
                    used = media_use.child("encoding", property_name)
                    encodings.append(
                        WireDeclaration(
                            "encoding",
                            property_name,
                            DeclarationId(locate(declared, "declaration")),
                            locate(used, "use"),
                            _facts(encoding.value, ("contentType", "style", "explode", "allowReserved"), declared),
                            children=self.headers(
                                encoding,
                                used.child("headers"),
                                owner,
                                header_role,
                                status=status,
                                media=name,
                            ),
                        )
                    )
            values.append(
                WireDeclaration(
                    "media",
                    name,
                    DeclarationId(locate(media_declaration, "declaration")),
                    locate(media_use, "use"),
                    _facts(medium.value, ("example", "examples"), media_declaration),
                    tuple(uses),
                    tuple(encodings),
                )
            )
        return tuple(values)

    def parameter(  # noqa: PLR0913
        self,
        walked: _WalkedObject,
        use_site: SchemaLocation,
        owner: OperationId,
        role: TypeUseRole,
        *,
        status: str | None = None,
        media: str | None = None,
        placeholders: Container[str] = (),
    ) -> WireDeclaration:
        """Bind one parameter or header; a path parameter naming a placeholder is required, as OpenAPI requires."""
        declared, value, wire_name = walked.declaration, walked.value, walked.key
        header = role != "parameter"
        location = None if header or "in" not in value else str(value["in"])
        schemas: tuple[TypeUseId, ...] = ()
        if (schema := next(iter(walked.schemas), None)) is not None:
            use = self.use_id(
                owner,
                role,
                declared,
                use_site,
                use_site.child("schema"),
                location=location,
                name=wire_name,
                status=status,
                media=media,
            )
            schemas = self.media_facts((self.use(use, schema),), schema, role, None)
        children = self.media(
            walked, use_site, owner, role, status=status, parameter_name=wire_name, parameter_location=location
        )
        read = _read_required(value, declared, HeaderObject if header else ParameterObject)
        locate = self.documents.location
        return WireDeclaration(
            "header" if header else "parameter",
            wire_name,
            DeclarationId(locate(declared, "declaration")),
            locate(use_site, "use"),
            _facts(
                {**read, "required": True} if location == "path" and wire_name in placeholders else read,
                _HEADER_FACTS if header else _PARAMETER_FACTS,
                declared,
            ),
            schemas,
            children,
        )

    def headers(  # noqa: PLR0913
        self,
        holder: _WalkedObject,
        use_site: SchemaLocation,
        owner: OperationId,
        role: TypeUseRole,
        *,
        status: str | None = None,
        media: str | None = None,
    ) -> tuple[WireDeclaration, ...]:
        """Bind the headers the walk visited in ``holder``; every name of its headers is checked, in order."""
        walked = {header.key: header for header in holder.below("header")}
        return tuple(
            self.parameter(header, use_site.child(name), owner, role, status=status, media=media)
            for name, _ in self.named(holder.value.get("headers"), holder.declaration.child("headers"), "header name")
            if (header := walked.get(name)) is not None
        )

    def response(
        self, walked: _WalkedObject, use_site: SchemaLocation, owner: OperationId, status: str
    ) -> WireDeclaration:
        declared, value = walked.declaration, walked.value
        content = self.media(walked, use_site, owner, "response_body", status=status)
        headers = self.headers(walked, use_site.child("headers"), owner, "response_header", status=status)
        links = tuple(
            self.metadata("link", name, declared.child("links", name), use_site.child("links", name), link)
            for name, link in self.named(value.get("links"), declared.child("links"), "link name")
        )
        locate = self.documents.location
        return WireDeclaration(
            "response",
            status,
            DeclarationId(locate(declared, "declaration")),
            locate(use_site, "use"),
            _facts(value, ("description",), declared),
            children=(*content, *headers, *links),
        )

    def operations(self) -> tuple[OperationContract, ...]:
        """Bind the operations in the order the target parser walked them, each before its callbacks' operations."""
        contracts: list[OperationContract] = []
        for walked in cast("TargetApiOpenAPIParser", self.parser).operations:
            parent = None if walked.parent is None else contracts[walked.parent].id
            contracts.append(self.operation(walked, len(contracts), parent))
        return tuple(contracts)

    def operation(self, walked: _WalkedOperation, order: int, parent: OperationId | None) -> OperationContract:
        locate = self.documents.location
        use_site, item = walked.use_site, walked.item
        raw, declaration = walked.value, walked.declaration
        kind: Literal["path", "webhook", "callback"] = (
            "callback" if parent is not None else "webhook" if use_site.tokens[0] == "webhooks" else "path"
        )
        identity = OperationId(
            locate(use_site, "use"),
            kind,
            parent,
            locate(SchemaLocation(use_site.document, use_site.tokens[:-1]), "use") if parent is not None else None,
        )
        placeholders = frozenset(path_placeholders(item.use_site.tokens[-1]))
        parameters = tuple(
            self.parameter(parameter, used, identity, "parameter", placeholders=placeholders)
            for used, parameter in walked.parameters
            if parameter.declaration not in self.ignored_declarations
        )
        body: WireDeclaration | None = None
        if (requested := next(walked.below("request_body"), None)) is not None:
            used = use_site.child("requestBody")
            body = WireDeclaration(
                "request_body",
                None,
                DeclarationId(locate(requested.declaration, "declaration")),
                locate(used, "use"),
                _facts(
                    _read_required(requested.value, requested.declaration, RequestBodyObject),
                    ("required", "description"),
                    requested.declaration,
                ),
                children=self.media(requested, used, identity, "request_body"),
            )
        responses = tuple(
            self.response(response, use_site.child("responses", status), identity, key)
            for status, response in document_statuses(
                {response.key: response for response in walked.below("response")},
                response_key,
                lambda status: [str(declaration.child("responses", status))],
            ).items()
            if (key := response_key(status)) is not None
        )
        callbacks = tuple(
            self.metadata(
                "callback",
                callback.key,
                callback.declaration,
                use_site.child("callbacks", callback.key),
                callback.value,
            )
            for callback in walked.below("callback")
        )
        root = SchemaLocation(use_site.document, ())
        facts = _facts(raw, _OPERATION_FACTS, declaration)
        if (security := inherited_security(raw, walked.security)) is not None:
            facts = (*facts, *_facts({"security": security}, ("security",), root))
        if "servers" not in raw:
            facts = (
                *facts,
                *(
                    _facts(item.raw, ("servers",), item.declaration)
                    if "servers" in item.raw
                    else _facts(self.documents.documents[use_site.document], ("servers",), root)
                ),
            )
        return OperationContract(
            identity,
            DeclarationId(locate(declaration, "declaration")),
            declaration.tokens[-1],
            item.use_site.tokens[-1],
            "operationId" in raw,
            "servers" in raw,
            order,
            facts,
            parameters,
            body,
            responses,
            callbacks,
            tuple(
                IgnoredDeclaration(
                    locate(ignored_declaration, "declaration"),
                    locate(use_site, "use"),
                    ignored.owner,
                    ignored.media,
                    ignored.wire_name,
                    ignored.reason,
                )
                for ignored_declaration, ignored_use, ignored in self.ignored
                if ignored_use == use_site
            ),
        )

    def channels(self) -> tuple[ChannelContract, ...]:
        """Bind the channels of the input documents of an AsyncAPI attempt, in document order."""
        from datamodel_code_generator._asyncapi_connections import read  # noqa: PLC0415

        contracts: list[ChannelContract] = []
        for document in self.documents.walked:
            if document in self.parser._api_roots:  # pyright: ignore[reportPrivateUsage] # noqa: SLF001
                contracts.extend(
                    self.channel(document, channel, len(contracts) + index)
                    for index, channel in enumerate(
                        read(self.documents.documents[document], partial(self.loaded_schema, document))
                    )
                )
        return tuple(contracts)

    def loaded_schema(self, document: str, ref: str) -> YamlValue:
        """Return the schema a `$ref` of a document names in the loaded documents, or None where none holds it."""
        return None if (target := self.loaded_target(document, ref)) is None else self.documents.borrow(target)

    def loaded_target(self, document: str, ref: str) -> SchemaLocation | None:
        """Return where the schema a `$ref` of a document names is, as the parser resolves it, in a loaded document."""
        target = self.parser._schema_reference_location(document, ref)  # pyright: ignore[reportPrivateUsage] # noqa: SLF001
        return None if target is None or target.document not in self.documents.ids else target

    def channel(self, document: str, read: ReadChannel, order: int) -> ChannelContract:
        """Bind one channel: its handshake, a GET of its address on its WebSocket servers, and its messages."""
        locate = self.documents.location
        use_site = SchemaLocation(document, read.use_site)
        declaration = SchemaLocation(document, read.declaration)
        identity = OperationId(locate(use_site, "use"), "channel")
        messages = tuple(
            MessageContract(
                locate(SchemaLocation(document, message.declaration), "declaration"),
                message.action,
                message.codec,
                self.payload(document, message.payload, message.payload_ref),
            )
            for message in read.messages
        )
        skips = tuple(starmap(ChannelSkip, read.skips))
        if read.address is None:
            return ChannelContract(locate(use_site, "use"), read.name, None, messages, skips)
        raw = as_mapping(self.documents.borrow(declaration))
        servers: list[YamlValue] = [
            {"url": server.url, **({} if server.variables is None else {"variables": server.variables})}
            for server in read.servers
        ]
        declared = {parameter.name: parameter for parameter in read.parameters}
        handshake = OperationContract(
            identity,
            DeclarationId(locate(declaration, "declaration")),
            "get",
            read.address,
            False,  # noqa: FBT003
            True,  # noqa: FBT003
            order,
            (
                *_facts(dict(raw), ("summary", "description"), declaration),
                *_facts({"servers": servers}, ("servers",), declaration),
            ),
            tuple(
                self.address_parameter(identity, use_site, declaration, name, declared.get(name))
                for name in dict.fromkeys(path_placeholders(read.address))
            ),
            None,
            (),
            (),
            (),
        )
        return ChannelContract(locate(use_site, "use"), read.name, handshake, messages, skips)

    def payload(self, document: str, tokens: tuple[str, ...], ref: str | None) -> SourceLocation | None:
        """Return the location of the schema a message's payload binds as: that a lone `$ref` names, or its own."""
        if ref is None:
            return self.documents.location(SchemaLocation(document, tokens), "schema")
        target = self.loaded_target(document, ref)
        return None if target is None else self.documents.location(target, "schema")

    def address_parameter(
        self,
        owner: OperationId,
        use_site: SchemaLocation,
        channel: SchemaLocation,
        name: str,
        parameter: ReadParameter | None,
    ) -> WireDeclaration:
        """Bind a parameter of a channel's address, a required path parameter of its handshake.

        An AsyncAPI 2 parameter's schema types it, and any other is a string.
        """
        locate = self.documents.location
        declared = (
            channel.child("parameters", name)
            if parameter is None
            else SchemaLocation(channel.document, parameter.declaration)
        )
        used = use_site.child("parameters", name)
        facts: dict[str, YamlValue] = {"name": name, "in": "path", "required": True}
        if parameter is not None and parameter.description is not None:
            facts["description"] = parameter.description
        if parameter is not None and parameter.schema is not None:
            schema = SchemaLocation(channel.document, parameter.schema)
            use = self.use_id(owner, "parameter", declared, used, schema, location="path", name=name)
            schemas = self.media_facts((self.use(use, schema),), schema, "parameter", None)
        else:
            use = self.use_id(owner, "parameter", declared, used, declared, location="path", name=name)
            self.uses[use] = TypeUseBinding(use, "bound", _BUILTINS["str"], None, schema=locate(declared, "schema"))
            self.texts[use] = False
            schemas = (use,)
        return WireDeclaration(
            "parameter",
            name,
            DeclarationId(locate(declared, "declaration")),
            locate(used, "use"),
            _facts(facts, _PARAMETER_FACTS, declared),
            schemas,
        )

    def security_schemes(self) -> tuple[WireDeclaration, ...]:
        """Bind the security schemes of the documents the walk loaded, loading the documents they reference."""
        declarations: list[WireDeclaration] = []
        for document in self.documents.walked:
            raw = self.documents.documents[document]
            schemes = SchemaLocation(document, ("components", "securitySchemes"))
            for name, scheme in self.named(
                as_mapping(as_mapping(raw).get("components")).get("securitySchemes"), schemes, "component name"
            ):
                use = schemes.child(name)
                declarations.append(self.metadata("security_scheme", name, use, use, scheme))
        return tuple(declarations)


@dataclass(frozen=True, slots=True)
class BoundAttempt:
    """One attempt's contract batch and the documents its locations point into."""

    batch: GeneratedTypeContractBatch
    documents: tuple[tuple[str, dict[str, YamlValue]], ...]


def bind_operations(
    parser: TargetParser,
    results: str | dict[tuple[str, ...], Result],
    *,
    output: Path,
    model_package: str,
    root_selector_document: str,
) -> BoundAttempt:
    """Bind every operation and schema use of a parsed attempt to the models its modules emit."""
    attempt = parser.attempt
    builder = _Contracts(parser, results, attempt=attempt, output=output, model_package=model_package)
    symbols, artifacts = builder.symbols()
    builder.field_facts(symbols)
    fields = builder.field_bindings(symbols)
    asyncapi = isinstance(parser, TargetAsyncAPIParser)
    channels = builder.channels() if asyncapi else None
    operations = (
        builder.operations()
        if channels is None
        else tuple(channel.handshake for channel in channels if channel.handshake is not None)
    )
    builder.schema_uses()
    builder.helper_uses()
    builder.extras()
    builder.encodings()
    security_schemes = () if asyncapi else builder.security_schemes()
    documents = builder.documents
    return BoundAttempt(
        GeneratedTypeContractBatch(
            attempt,
            root_selector_document,
            tuple(SourceDocument(identity, uri) for uri, identity in documents.ids.items()),
            operations,
            tuple(builder.uses.values()),
            symbols,
            artifacts,
            fields,
            security_schemes,
            api_scope=True,
            openapi=(
                version := parser.document_versions.get(root := next(iter(documents.ids), ""), OpenAPIVersion.V31)
            ),
            document_versions=() if asyncapi else _own_versions(parser, documents, version),
            document_facts=parser.document_facts.get(root, ()),
            hint_type=builder.binder.hints.hint_type,
            hint_imports=tuple(builder.binder.hints.fixed),
            names=TargetNames.of(parser.model_resolver),
            channels=channels,
        ),
        _pristine(parser, documents.documents),
    )


def _own_versions(
    parser: TargetParser, documents: _Documents, root: OpenAPIVersion
) -> tuple[tuple[SourceDocumentId, OpenAPIVersion], ...]:
    """Return the loaded documents that --read-schema-version-per-document reads by another version than the root's.

    Each is read as the parser reads it (``_dialect_at``); without the option there are none.
    """
    return tuple(
        (identity, version)
        for uri, identity in documents.ids.items()
        if (dialect := parser._dialect_at(SchemaLocation(uri, ()))) is not None  # pyright: ignore[reportPrivateUsage] # ruff: ignore[private-member-access]
        and (version := cast("OpenAPIVersion", dialect.version)) != root
    )


def _pristine(
    parser: TargetParser, documents: dict[str, dict[str, YamlValue]]
) -> tuple[tuple[str, dict[str, YamlValue]], ...]:
    """Return every document the attempt loaded as it was written, by the location it was read from.

    The walked documents come first, in contract order, then the others the models' references loaded. The parser
    walks a rebased copy of a document with nested `$id` resources and keeps the loaded one beside it, and keeps a
    document as written beside the one --use-single-value-enum-as-const read.
    """
    cache = parser._schema_resource_cache  # pyright: ignore[reportPrivateUsage] # ruff: ignore[private-member-access]
    as_written = parser._document_as_written  # pyright: ignore[reportPrivateUsage] # ruff: ignore[private-member-access]
    located = {id(prepared): (location or _unnamed(parser), raw) for location, (raw, prepared) in cache.items()}
    walked = [located.get(id(document), (uri or _unnamed(parser), document)) for uri, document in documents.items()]
    seen = {id(document) for _, document in walked}
    loaded = (*walked, *((location, raw) for location, (raw, _) in cache.items() if id(raw) not in seen and location))
    return tuple((location, as_written(document)) for location, document in loaded)


def _unnamed(parser: TargetParser) -> str:
    """Return the location of a document read as text, such as the input when its type is detected.

    Its references resolve against the parser's base path, as the models' do.
    """
    return f"{file_uri(Path(parser.base_path).resolve())}/"


if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Container, Generator, Iterable, Iterator, Mapping, Sequence

    from datamodel_code_generator._asyncapi_connections import ReadChannel, ReadParameter
    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator._python_type_annotation import PythonTypeExpr
    from datamodel_code_generator._python_type_binding import BoundPythonType
    from datamodel_code_generator._runtime.model_codecs.media import LexicalKind, MediaKind
    from datamodel_code_generator._site_facts import Projection, SiteFacts, TypeUseRole
    from datamodel_code_generator._source import YamlValue
    from datamodel_code_generator._target_contract import (
        BackendName,
        BackendValue,
        DefaultKind,
        FrozenLiteral,
        TypeArgument,
        TypeProjectionReason,
        TypeView,
    )
    from datamodel_code_generator.config import AsyncAPIParserConfig, OpenAPIParserConfig
    from datamodel_code_generator.imports import Imports
    from datamodel_code_generator.parser.base import ForwarderMap, ModuleContext, ModulePath, ParseConfig, Result
    from datamodel_code_generator.parser.openapi_media import MediaOwner
    from datamodel_code_generator.parser.openapi_scope import (
        ApiIgnoredDeclaration,
        ApiObjectKind,
        ApiParameterDeclaration,
    )
    from datamodel_code_generator.reference import ModelResolver, _RwDirection
