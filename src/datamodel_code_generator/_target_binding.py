"""Bind target operations to the emitted models through recorded model replacements.

Only target generation imports this module. A target parser records the two reference redirects of the generation
store, the API schema acquisitions with their engine keys, the operations and reference objects its walk resolves,
and each emitted module's final models. After parsing, `bind_operations` reads the final model graph and these records
once, before disposal, and returns an immutable contract batch; it locates members and nested types through the
parser's origins (``reference.field_origin_of``, ``reference.type_origin_of``) and reads no schema of the loaded
documents.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, is_dataclass, replace
from dataclasses import fields as dataclass_fields
from decimal import Decimal
from functools import cached_property, partial
from math import isfinite
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal, TypeAlias, cast, get_args
from urllib.parse import ParseResult, unquote

from typing_extensions import override

from datamodel_code_generator import Error
from datamodel_code_generator._container_kinds import (
    CONTAINER_FLAGS,
    MAPPINGS,
    SLOT_CLASSES,
    WRITTEN_CONTAINERS,
    abstract_class_module,
    written_slot,
)
from datamodel_code_generator._document_reader import MISSING, as_mapping, descend, document_statuses, string_key
from datamodel_code_generator._generation_contract import AttemptId
from datamodel_code_generator._json_pointer import NONCANONICAL_ESCAPE, NONCANONICAL_PERCENT, fragment_tokens
from datamodel_code_generator._json_schema import (
    COMBINATOR_POSITIONS,
    MODEL_VALUE_POSITIONS,
    MODELED_SCHEMA_MAPS,
    SCHEMA_SHAPES,
    SEQUENCE_SCHEMA_SHAPES,
    SINGLE_SCHEMA_SHAPES,
)
from datamodel_code_generator._module_paths import written_path
from datamodel_code_generator._mro import linearize, resolve_members
from datamodel_code_generator._openapi_paths import inherited_security
from datamodel_code_generator._python_syntax import subscript_text
from datamodel_code_generator._runtime.model_codecs.media import media_kind, normalized
from datamodel_code_generator._target_contract import (
    WRAPPER_KINDS,
    BackendFieldFacts,
    BackendModelFacts,
    BackendSetting,
    BoundType,
    BuiltinName,
    BuiltinType,
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
    KindSite,
    KnownBackendValue,
    LiteralMapping,
    LiteralScalar,
    LiteralSequence,
    LiteralType,
    MemberShape,
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
    SchemaSite,
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
)
from datamodel_code_generator._target_module import TypeComposer
from datamodel_code_generator._target_naming import TargetNames, path_placeholders
from datamodel_code_generator._target_types import admits_none, is_none, present
from datamodel_code_generator.enums import DataclassArguments
from datamodel_code_generator.imports import IMPORT_ANY, IMPORT_DECIMAL, Import, _import_path, _stored_import
from datamodel_code_generator.model import dataclass as dataclass_model
from datamodel_code_generator.model import msgspec, pydantic_v2, typed_dict
from datamodel_code_generator.model._decisions.enum_kind import literal_value_kind
from datamodel_code_generator.model._decisions.nullability import reference_null
from datamodel_code_generator.model._decisions.tag_value import field_source_name
from datamodel_code_generator.model._decisions.union_assembly import WrittenMember, merge_members
from datamodel_code_generator.model.base import (
    _REUSE_PATH_SUFFIX,
    UNDEFINED,
    ConstraintsBase,
    DataModel,
    DataModelFieldBase,
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
from datamodel_code_generator.parser.generation import GenerationStore
from datamodel_code_generator.parser.jsonschema import Discriminator, JsonSchemaObject
from datamodel_code_generator.parser.openapi_scope import ApiOpenAPIParser
from datamodel_code_generator.python_literal import PythonCode, PythonRuntimeExpression
from datamodel_code_generator.reference import (
    _RW_DIRECTIONS,
    SPECIAL_PATH_MARKER,
    SchemaLocation,
    _is_special_path,
    _rw_direction,
    _rw_variant_flag,
    _rw_variant_of,
    _rw_variant_path,
    _source_path,
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
_NULL: Final = frozenset({"null"})
_ARRAY: Final = frozenset({"array"})
_STRING: Final = frozenset({"string"})
_OBJECT: Final = frozenset({"object"})
_NESTED: Final = _ARRAY | _OBJECT
_IDENTIFIERS: Final = frozenset({"$id", "$anchor", "$schema"})
_ITEMS: Final[tuple[LeafStep, ...]] = ("items",)
_FORMS: Final = frozenset({"form", "multipart"})
_TEXT_MEDIA: Final = frozenset({"json", "form", "text"})
_RAW_MEDIA: Final = frozenset({"text", "binary"})
_HEADER_ROLES: Final[frozenset[SchemaRole]] = frozenset({
    "response_header",
    "request_encoding_header",
    "response_encoding_header",
})
_SCHEMA_KEYWORDS: Final = ("title", "description", "deprecated", "examples", "default")
_DOCUMENT_FACTS: Final = ("openapi", "info", "tags", "servers")
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
        self, old_reference: Reference, new_reference: Reference, *, merged: bool = False
    ) -> list[DataType]:
        """Record a global redirect, and whether it merges a model, then redirect every user and return them."""
        self.redirects.append((old_reference, new_reference, merged))
        return super().redirect_reference_users(old_reference, new_reference, merged=merged)  # ty: ignore[deprecated]

    def redirect_model_reference_users(
        self, model: DataModel, models: list[DataModel], new_reference: Reference
    ) -> None:
        """Record a redirect scoped to some models, which merges a model, then redirect their users."""
        self.redirects.append((model.reference, new_reference, True))
        super().redirect_model_reference_users(model, models, new_reference)  # ty: ignore[deprecated]


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
class _WalkedOperation:
    """An operation as the target parser walked it, with the parameters in effect in their order."""

    raw: dict[str, YamlValue]
    declaration: SchemaLocation
    use_site: SchemaLocation
    origin: SchemaLocation
    item: _WalkedPathItem
    parent: int | None
    security: YamlValue
    shared: dict[int, SchemaLocation]
    parameters: list[tuple[SchemaLocation, SchemaLocation, dict[str, YamlValue]]]


class TargetApiOpenAPIParser(ApiOpenAPIParser):
    """Record API schema acquisitions, walked operations and emitted modules for one target generation attempt."""

    _generation_store_factory = staticmethod(RecordingGenerationStore.create_with_results)

    def __init__(
        self,
        source: str | Path | list[Path] | ParseResult | dict[str, YamlValue],
        *,
        config: OpenAPIParserConfig | None = None,
        serves_source: bool,
    ) -> None:
        """Start with no acquisitions, walked operations or emitted modules."""
        self.serves_source = serves_source
        self.attempt = AttemptId(0)
        self.acquisitions: dict[tuple[SchemaLocation, Projection], str] = {}
        self.module_outputs: list[tuple[ModulePath, tuple[DataModel, ...], Result]] = []
        self.model_imports: set[str] = set()
        self.operations: list[_WalkedOperation] = []
        self.resolutions: dict[SchemaLocation, tuple[SchemaLocation, dict[str, YamlValue]]] = {}
        self.unions: dict[int, _Union] = {}
        self.copies: dict[int, tuple[DataModelFieldBase, DataModelFieldBase]] = {}
        self.left_out: dict[str, tuple[tuple[DataModelFieldBase, ...], tuple[DataModelFieldBase, ...]]] = {}
        self.schema_records: dict[SchemaLocation, _SchemaRecord] = {}
        self.free_schemas: dict[SchemaLocation, JsonSchemaObject] = {}
        self.nullable_fields: dict[int, DataModelFieldBase] = {}
        self.document_facts: dict[str, tuple[tuple[str, FrozenLiteral], ...]] = {}
        self.record_documents: dict[str, SourceDocumentId] = {}
        self._walked_items: list[_WalkedPathItem] = []
        self._walked_operations: list[int] = []
        self._callback_origin: SchemaLocation | None = None
        super().__init__(source, config=config)
        self._reader = _Reader(
            self._api_documents,
            versions=[],
            binary=self._binary_formats(encoded=False),
            encoded=self._binary_formats(encoded=True),
            follow=self._schema_reference_location,
        )

    def _binary_formats(self, *, encoded: bool) -> frozenset[str]:
        """Return the string formats the models type as bytes, as `--type-mappings` leaves or makes them.

        They are the formats of raw bytes, or those of bytes the models write as base64 text.
        """
        return frozenset(
            found
            for found in ("binary", *(format_ for type_, format_ in self.type_mappings if type_ == "string"))
            if self._get_type_with_mappings("string", found) == Types.binary
            and self._is_base64_encoded_binary_mapping("string", found) is encoded
        )

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

    def _resolve_api_object(self, value: YamlValue, path: list[str]) -> Any:
        """Record the declaration a reference object resolves to."""
        target = super()._resolve_api_object(value, path)
        if target.value is not value:
            self.resolutions[self._declared(value)] = (target.declaration, target.value)
        return target

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
        self.operations.append(
            _WalkedOperation(
                operation,
                self._declared(operation),
                use_site,
                item.origin.child(*use_site.tokens[len(item.use_site.tokens) :]),
                item,
                self._walked_operations[-1] if self._walked_operations else None,
                self._api_security,
                shared,
                [],
            )
        )
        self._walked_operations.append(len(self.operations) - 1)
        super()._walk_api_operation(operation, path, use_site, prefix, common)
        self._walked_operations.pop()

    def _walk_parameter(self, name: str, target: Any, *, header: bool = False, role: SchemaRole = "parameter") -> None:
        """Record each parameter in effect for the operation under the walk at its place in the document."""
        if self._walked_operations and role == "parameter" and not header:
            operation = self.operations[self._walked_operations[-1]]
            operation.parameters.append((
                operation.shared.get(id(target))
                or operation.origin.child("parameters", str(len(operation.parameters))),
                target.declaration,
                target.value,
            ))
        super()._walk_parameter(name, target, header=header, role=role)

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

    def _record_schema_object(self, reference: Reference, obj: JsonSchemaObject) -> None:
        """Record the schema of the typed additional properties of a model's schema, which binds model-free."""
        super()._record_schema_object(reference, obj)
        if isinstance(additional := obj.additionalProperties, JsonSchemaObject) and obj.schema_location is not None:
            self.free_schemas.setdefault(obj.schema_location.child("additionalProperties"), additional)

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

    def _parse_specification(self, specification: dict[str, YamlValue], path_parts: list[str]) -> None:
        """Record the OpenAPI version, info, tags and servers of each walked document before walking it."""
        self.document_facts["/".join(path_parts)] = _keywords(self._document_as_written(specification), _DOCUMENT_FACTS)
        self._reader.versions.append(str(specification.get("openapi", "")))
        super()._parse_specification(specification, path_parts)

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

    @override
    def _reads_media_type(self, medium: dict[str, YamlValue], path: list[str], owner: MediaOwner) -> bool:
        """Return True: the bindings locate every medium by its media type, so walking one reads it."""
        return True

    def _acquire_schema(
        self, name: str, raw: YamlValue, path: list[str], *, declaration: SchemaLocation, role: SchemaRole
    ) -> None:
        """Record the declaration's engine key, even when it was already generated, and what its schema says.

        Once the schema is generated, its default, keywords, parts and text encoding are recorded as the
        documents spell them, since its model types do not say them.
        """
        self.acquisitions.setdefault((declaration, "value"), self.model_resolver.join_path(tuple(path)))
        super()._acquire_schema(name, raw, path, declaration=declaration, role=role)
        if declaration not in self.schema_records:
            self.schema_records[declaration] = self._reader.record(declaration, role, self._locate)
        if declaration not in self._declaration_types:
            self._free(declaration, "value", raw, path)

    def _free(self, declaration: SchemaLocation, projection: Projection, raw: YamlValue, path: list[str]) -> None:
        """Record the schema of a declaration that binds as the parser's model-free type, when no model holds it."""
        if (
            declaration not in self.free_schemas
            and self.model_resolver.references.get(self.acquisitions[declaration, projection]) is None
        ):
            self.free_schemas[declaration] = self._validate_schema_object(raw, path)

    def _locate(self, declaration: SchemaLocation) -> SourceLocation:
        """Return a declaration's location, its document identified in the order the records first name them."""
        document = self.record_documents.setdefault(declaration.document, SourceDocumentId(len(self.record_documents)))
        return SourceLocation(document, declaration.pointer, "schema")

    def _acquire_item_schema(  # ruff: ignore[too-many-arguments]
        self,
        name: str,
        item: YamlValue,
        path: list[str],
        projected: YamlValue,
        *,
        declaration: SchemaLocation,
        role: SchemaRole,
    ) -> None:
        """Record an item schema and its stream-array projection, even when they were already generated."""
        item_path = [*path, "itemSchema"]
        self.acquisitions.setdefault((declaration, "value"), self.model_resolver.join_path(tuple(item_path)))
        self.acquisitions.setdefault(
            (declaration, "item_stream_array"),
            self.model_resolver.join_path(tuple(self._media_schema_path(path, from_item_schema=True))),
        )
        super()._acquire_item_schema(name, item, path, projected, declaration=declaration, role=role)
        self._free(declaration, "item_stream_array", item, item_path)

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

    def release_records(self) -> None:
        """Drop the recorded graph anchors and borrowed source nodes, and the state of a walk that failed."""
        self.acquisitions.clear()
        self.module_outputs.clear()
        self.model_imports.clear()
        self.operations.clear()
        self.resolutions.clear()
        self.unions.clear()
        self.copies.clear()
        self.schema_records.clear()
        self._reader.nodes.clear()
        self._reader.binaries.clear()
        self._reader.encodings.clear()
        self.free_schemas.clear()
        self.nullable_fields.clear()
        self.document_facts.clear()
        self.record_documents.clear()
        self._walked_items.clear()
        self._walked_operations.clear()
        self._callback_origin = None
        cast("RecordingGenerationStore", self.generation_store).redirects.clear()


def _imported_names(imports: Imports) -> Iterator[str]:
    """Yield every module and module member that the import statements of a collection name."""
    for module, names in imports.items():
        members = (_stored_import(name)[0] for name in names)
        if module is None:
            yield from members
        else:
            yield module
            yield from (f"{module}.{member}" for member in members)


_Locate: TypeAlias = "Callable[[SchemaLocation], SourceLocation]"
_Follow: TypeAlias = "Callable[[str, str], SchemaLocation | None]"


@dataclass(frozen=True, slots=True)
class _PartsRecord:
    """What a multipart body's schema says of its parts: whether it is an object, each property's, and any other's.

    `binary` is where the first raw binary string sits inside a part that holds no files, which carries it as text.
    """

    object: bool
    members: tuple[tuple[SourceLocation, PartSchema], ...]
    extra: PartSchema | Literal["closed"] | None
    binary: SourceLocation | None = None


@dataclass(frozen=True, slots=True)
class _SchemaRecord:
    """What the walk recorded of an acquired schema that its model types do not say.

    `binary` is where the first raw binary string sits that the schema's values carry as text. Of a body's schema,
    `string` says its whole value may be a string, which its models may type as raw bytes, `spelled` that it is
    written as one binary string, and `base64` that it reaches bytes the models write as base64 text. `form` is the
    branch of a form's union with null alone that declares the form's members, which its parts and encoding are read
    from.
    """

    default: LiteralScalar | None
    keywords: tuple[tuple[str, FrozenLiteral], ...]
    parts: _PartsRecord | None
    encoding: EncodingFacts | None
    binary: SourceLocation | None = None
    string: bool = False
    spelled: bool = False
    base64: bool = False
    form: SchemaLocation | None = None


def _media_kind(media: str) -> MediaKind | None:
    return None if (found := normalized(media)) is None else media_kind(found)


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
    raw: dict[str, YamlValue], keys: tuple[str, ...] = _SCHEMA_KEYWORDS
) -> tuple[tuple[str, FrozenLiteral], ...]:
    """Return each of ``keys`` an object declares, with its literal; by default a schema's documentation keywords."""
    found: list[tuple[str, FrozenLiteral]] = []
    for key in keys:
        if key in raw:
            with suppress(_UnsupportedError):
                found.append((key, _freeze_literal(raw[key], set())))
    return tuple(found)


def _relocated(value: object, documents: Mapping[SourceDocumentId, SourceDocumentId]) -> object:
    """Return a record with each location's document identity the one the attempt's documents give it."""
    match value:
        case SourceLocation():
            return replace(value, document=documents[value.document])
        case tuple():
            return tuple(_relocated(item, documents) for item in value)
        case _ if is_dataclass(value) and not isinstance(value, type):
            return replace(
                value,
                **{field.name: _relocated(getattr(value, field.name), documents) for field in dataclass_fields(value)},
            )
        case _:
            pass
    return value


_UNRECORDED: Final = _SchemaRecord(None, (), None, None)
_NOT_OBJECT: Final = "A URL-encoded value must be an object"
_PATTERNS: Final = "Pattern properties have no builtin parameter encoding"
_UNDECLARED_PARTS: Final = _PartsRecord(object=True, members=(), extra=None)
_NO_PART: Final = PartSchema(file=False, repeated=False, text=False, structured=False)


def _pointer_tokens(ref: str) -> tuple[str, ...] | None:
    """Decode a reference's JSON pointer fragment, or return None for a named anchor."""
    return fragment_tokens(unquote(ref.partition("#")[2]))


def _schema_use(location: SourceLocation) -> TypeUseId:
    return TypeUseId(
        location,
        "schema",
        replace(location, role="use"),
        location,
        DeclarationId(replace(location, role="declaration")),
        "neutral",
    )


_JSON_TYPES: Final[dict[LiteralValueKind, str]] = {
    "none": "null",
    "bool": "boolean",
    "int": "integer",
    "float": "number",
    "str": "string",
}
_VALUE_SCHEMA_LISTS = tuple(
    keyword for keyword in MODEL_VALUE_POSITIONS if SCHEMA_SHAPES[keyword] in SEQUENCE_SCHEMA_SHAPES
)
_VALUE_SCHEMAS = tuple(keyword for keyword in MODEL_VALUE_POSITIONS if SCHEMA_SHAPES[keyword] in SINGLE_SCHEMA_SHAPES)


def _json_types(schema: dict[str, YamlValue]) -> frozenset[str]:
    """Return the JSON types a schema declares, none when it declares none."""
    match declared := schema.get("type"):
        case str():
            return frozenset({declared})
        case list():
            return frozenset(str(item) for item in declared)
        case _:
            pass
    return frozenset()


def _valued_types(schema: dict[str, YamlValue]) -> frozenset[str]:
    """Return the JSON types of a schema's enum members and const value."""
    values = schema.get("enum")
    members = [*(values if isinstance(values, list) else ()), *((schema["const"],) if "const" in schema else ())]
    return frozenset(map(_json_type, members))


def _spelled(schema: dict[str, YamlValue], formats: frozenset[str]) -> bool:
    """Return whether a schema is written as a string of one of the formats, its values telling the type it omits."""
    format_ = schema.get("format")
    return (
        isinstance(format_, str) and format_ in formats and "string" in (_json_types(schema) or _valued_types(schema))
    )


def _json_type(value: object, other: str | None = None) -> str:
    """Return the JSON type of an enum or const value by its literal kind.

    Any other value is ``other``, or by default a number when it is a Decimal, an object when it is a dict, and an
    array otherwise.
    """
    if (found := _JSON_TYPES.get(literal_value_kind(value))) is not None:
        return found
    if other is not None:
        return other
    return "number" if isinstance(value, Decimal) else "object" if isinstance(value, dict) else "array"


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

    With ``encoded``, bytes decoded from base64 text with lengths constrain bytes.
    """
    if getattr(data_type, "annotated_string", False):
        return "str"
    if encoded and getattr(data_type, "annotated_bytes", False):
        return "bytes"
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

    def __init__(self, parser: TargetApiOpenAPIParser, results: str | dict[tuple[str, ...], Result]) -> None:
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

    def source(self, reference: Reference) -> str:
        """Return the path of the schema a model is generated from, through read and write variants and reuse.

        A model that reuse makes of another has its schema's path with a reuse suffix that no resolver key holds.
        """
        path = reference.path
        return _source_path(path.removesuffix(_REUSE_PATH_SUFFIX) if id(reference) not in self.keys else path)

    def reused(self, model: DataModel) -> DataModel | None:
        """Return the model that reuse replaced with a model inheriting from an equal one, if reuse made this one."""
        path = model.reference.path
        original = (
            None
            if id(model.reference) in self.keys or not path.endswith(_REUSE_PATH_SUFFIX)
            else self.parser.model_resolver.references.get(path.removesuffix(_REUSE_PATH_SUFFIX))
        )
        return original.source if original is not None and isinstance(original.source, DataModel) else None

    def emitted(self, model: object) -> bool:
        return isinstance(model, DataModel) and id(model) in self.symbols

    def final(self, reference: Reference, direction: Direction = "neutral") -> Reference | None:
        return self._final(reference, direction)[0]

    def schema_null(self, reference: Reference, direction: Direction) -> bool:
        """Return whether a use of a schema's own model admits the null of that schema, as a reference to it does.

        Each merge that pointed the schema's model at an equal one recorded the null the use admitted right before it
        (``GenerationStore.redirect_reference_users``), so a schema that rejects null keeps rejecting it in a merged
        nullable model (``nullability.reference_null``, #5410).
        """
        found, named = self._final(reference, direction)
        return found is not None and reference_null(named=named, names_nullable=reference_names_nullable(found))

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
            null = reference_null(named=named, names_nullable=reference_names_nullable(current))
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
            and isinstance(constraints := field.constraints, ConstraintsBase)
            and constraints.has_constraints
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
        generic = data_type.use_generic_container
        module = abstract_class_module(standard=data_type.use_standard_collections)
        for modifier in WRITTEN_CONTAINERS:
            if not getattr(data_type, flag := CONTAINER_FLAGS[modifier]):
                continue
            written = SLOT_CLASSES[written_slot(modifier, generic=generic)]
            base = (
                ImportedType(Import(import_=written.name, from_=module))
                if written.abstract
                else _BUILTINS[written.name]
            )
            if modifier in MAPPINGS.slots and (data_type.dict_key is not None or value is not None):
                key = self._project(data_type.dict_key) if data_type.dict_key is not None else None
                value = value if value is not None else ImportedType(IMPORT_ANY)
                hint = self.hints.composed(data_type, base=value, key=key, container=flag)
                return GenericType(base, (BuiltinType("str") if key is None else key, value), hint=hint)
            hint = self.hints.composed(data_type, base=value, container=flag)
            return GenericType(base, (value,) if value is not None else (), hint=hint)
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
    try:
        value = _freeze_argument(field.default)
    except _UnsupportedError:
        return "expression", SourceExpression(repr(field.default))
    if isinstance(value, (SourceExpression, ImportedExpression)):
        return "expression", SourceExpression(repr(field.default))
    return "literal", value


def _constructor_keywords(
    field: DataModelFieldBase, backend: BackendName
) -> tuple[tuple[str, FrozenLiteral | SourceExpression], ...]:
    """Return the keyword arguments of the builtin field call, read from the field's structured render data."""
    keywords: list[tuple[str, FrozenLiteral | SourceExpression]] = []
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
        try:
            frozen = _freeze_argument(value)
        except _UnsupportedError:
            frozen = SourceExpression(repr(value))
        keywords.append((name, frozen if not isinstance(frozen, ImportedExpression) else SourceExpression(str(value))))
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


def _setting(
    name: str, keywords: tuple[tuple[str, FrozenLiteral | SourceExpression], ...], *, fallback: bool | None
) -> BackendValue:
    for key, value in reversed(keywords):
        if key == name:
            return (
                RuntimeBackendValue("expression") if isinstance(value, SourceExpression) else KnownBackendValue(value)
            )
    return _backend_value(fallback) if fallback is not None else OpaqueBackendValue("model_policy_required")


def _model_settings(model: DataModel, backend: BackendName) -> dict[str, BackendValue] | None:
    internal: dict[str, object] = model._internal_template_data  # pyright: ignore[reportPrivateUsage] # noqa: SLF001
    match backend:
        case "dataclass" | "pydantic_dataclass":
            return {
                name: _backend_value(value)
                for name, value in model.dataclass_arguments.items()  # pyright: ignore[reportAttributeAccessIssue]
                if value is not False and value is not None
            }
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


def _field_facts(
    field: DataModelFieldBase,
    type_value: TypeView,
    backend: BackendName,
    model_facts: BackendModelFacts | None,
    context: _FieldContext | None,
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


class _ShapeError(Exception):
    """A schema whose values no builtin parameter or form encoding writes, at the location that says so."""

    def __init__(self, declaration: SchemaLocation, message: str) -> None:
        super().__init__(message)
        self.declaration = declaration


_Branch: TypeAlias = Literal["reference", "schema", "other"]


@dataclass(frozen=True, slots=True)
class _SchemaNode:
    """Where a schema declares its members, as the reader reads it: its reference, properties and allOf branches.

    `target` is where its `$ref` leads, as the parser resolves it.
    """

    target: SchemaLocation | None = None
    properties: tuple[str, ...] = ()
    all_of: tuple[_Branch, ...] = ()
    additional: Literal["absent", "false", "schema", "other"] = "absent"

    @classmethod
    def of(cls, value: YamlValue, follow: Callable[[str], SchemaLocation | None]) -> _SchemaNode:
        """Read a loaded schema's member locations, following its `$ref`; any value but a mapping declares none."""
        if not isinstance(value, dict) or not value:
            return _LEAF
        branches = value.get("allOf")
        additional = value.get("additionalProperties", MISSING)
        node = cls(
            follow(ref) if isinstance(ref := value.get("$ref"), str) else None,
            tuple(as_mapping(value.get("properties"))),
            tuple(
                ("reference" if "$ref" in branch else "schema") if isinstance(branch, dict) else "other"
                for branch in (branches if isinstance(branches, list) else ())
            ),
            "absent"
            if additional is MISSING
            else "false"
            if additional is False
            else "schema"
            if isinstance(additional, dict)
            else "other",
        )
        return _LEAF if node == _LEAF else node


_LEAF: Final = _SchemaNode()


class _SchemaLocations:
    """Locate schema members in the loaded documents as they spell them, following references where they lead.

    Only the reader's records read them; the binder locates members and nested types through the parser's origins.
    """

    def __init__(self, documents: dict[str, dict[str, YamlValue]], follow: _Follow) -> None:
        self.loaded = documents
        self.follow = follow
        self.nodes: dict[SchemaLocation, _SchemaNode] = {}

    def borrow(self, declaration: SchemaLocation, missing: YamlValue = None) -> YamlValue:
        """Read a declaration from its loaded document, or `missing` when the document lacks it."""
        return descend(self.loaded.get(declaration.document), declaration.tokens, "legacy-int-tokens", missing)

    def at(self, declaration: SchemaLocation) -> _SchemaNode:
        """Record and return where a loaded schema declares its members, nothing when its document lacks it."""
        if (node := self.nodes.get(declaration)) is None and (value := self.borrow(declaration)) is not None:
            node = self.nodes[declaration] = _SchemaNode.of(value, partial(self.follow, declaration.document))
        return node or _LEAF

    def resolve(self, declaration: SchemaLocation) -> tuple[SchemaLocation, _SchemaNode]:
        """Follow a schema's reference chain, whatever keywords sit beside them, to the schema it leads to."""
        node = self.at(declaration)
        seen = {declaration}
        while (target := node.target) is not None and target not in seen:
            seen.add(target)
            declaration, node = target, self.at(target)
        return declaration, node

    def property_location(
        self, schema: SchemaLocation, name: str, active: set[SchemaLocation]
    ) -> SchemaLocation | None:
        """Find the property declaration of a name, through allOf branches and references."""
        if schema in active:
            return None
        active.add(schema)
        node = self.at(schema)
        if name in node.properties:
            return schema.child("properties", name)
        for index, branch in reversed(list(enumerate(node.all_of))):
            if branch != "other" and (found := self.property_location(schema.child("allOf", str(index)), name, active)):
                return found
        if (
            len(schema.tokens) > 1
            and schema.tokens[-2] in {"anyOf", "oneOf"}
            and name in self.at(parent := SchemaLocation(schema.document, schema.tokens[:-2])).properties
        ):
            return parent.child("properties", name)
        if (target := node.target) is not None:
            return self.property_location(target, name, active)
        return None

    def additional_owner(self, schema: SchemaLocation, active: set[SchemaLocation]) -> SchemaLocation | None:
        """Find the schema that says what a schema's other properties are, through allOf branches and references.

        A schema that says anything of its other properties itself hides what its branches and references say, and
        one that its own branches or references lead back to says nothing more.
        """
        node = _LEAF if schema in active else self.at(schema)
        active.add(schema)
        if node.additional != "absent":
            return schema
        for index, branch in reversed(list(enumerate(node.all_of))):
            if branch != "other" and (found := self.additional_owner(schema.child("allOf", str(index)), active)):
                return found
        if (target := node.target) is not None:
            return self.additional_owner(target, active)
        return None

    def properties(
        self, schema: SchemaLocation, active: set[SchemaLocation] | None = None
    ) -> dict[str, SchemaLocation]:
        """Return a schema's properties in declaration order, allOf branches and references included.

        A schema that its own branches or references lead back to adds its properties once.
        """
        active = set() if active is None else active
        if schema in active:
            return {}
        active.add(schema)
        node = self.at(schema)
        found: dict[str, SchemaLocation] = {}
        if (target := node.target) is not None:
            found.update(self.properties(target, active))
        for index in range(len(node.all_of)):
            found.update(self.properties(schema.child("allOf", str(index)), active))
        found.update((name, schema.child("properties", name)) for name in node.properties)
        return found

    def members(self, schema: SchemaLocation) -> dict[str, SchemaLocation]:
        """Return the property declaration of each name a schema declares, through allOf branches and references."""
        return {
            name: found
            for name in self.properties(schema)
            if (found := self.property_location(schema, name, set())) is not None
        }


class _Reader(_SchemaLocations):
    """Read the loaded API documents by declaration while the walk reads them, recording what the models do not say.

    The target parser reads with it while it walks, to record what the model types do not say.
    """

    def __init__(
        self,
        documents: dict[str, dict[str, YamlValue]],
        *,
        versions: list[str],
        binary: frozenset[str],
        encoded: frozenset[str],
        follow: _Follow,
    ) -> None:
        super().__init__(documents, follow)
        self.versions = versions
        self.formats = binary
        self.encoded = encoded
        self.binaries: dict[SchemaLocation, SchemaLocation | None] = {}
        self.encodings: dict[SchemaLocation, SchemaLocation | None] = {}

    @property
    def legacy(self) -> bool:
        """Return whether the entry document is OpenAPI 3.0, whose schemas ignore the siblings of a reference."""
        return next(iter(self.versions), "").startswith("3.0")

    def whole(self, declaration: SchemaLocation) -> tuple[SchemaLocation, dict[str, YamlValue]]:
        """Follow a schema that is nothing but a reference to the schema it names, through such schemas.

        Identification keywords beside a reference say nothing of its values, and in OpenAPI 3.0 no sibling does.
        """
        raw = as_mapping(self.borrow(declaration))
        seen = {declaration}
        while (
            (self.legacy or raw.keys() - _IDENTIFIERS == {"$ref"})
            and isinstance(ref := raw.get("$ref"), str)
            and (target := self.follow(declaration.document, ref)) is not None
            and target not in seen
        ):
            seen.add(target)
            declaration, raw = target, as_mapping(self.borrow(target))
        return declaration, raw

    def record(self, declaration: SchemaLocation, role: SchemaRole, locate: _Locate) -> _SchemaRecord:
        """Record what an acquired schema says that its model types do not, as the walk acquires it.

        A parameter's schema gives its default and keywords; a multipart schema how its members encode parts;
        and any schema read as parameter or form text how its values are written. A body's schema that is not a
        multipart one says whether its models may type its whole value as raw bytes, which they decide.
        """
        media = declaration.tokens[-2] if declaration.tokens[-3:-2] == ("content",) else None
        kind = None if media is None else _media_kind(media)
        parameter = role == "parameter"
        text = parameter or role in _HEADER_ROLES
        form = found[0] if kind in _FORMS and len(found := self.choices(declaration)) == 1 else None
        declared = form or declaration
        parts = self.parts(declared, locate) if media is not None and _multipart(media) else None
        item = declaration.tokens[-1] == "itemSchema"
        body = kind is not None and parts is None and not (text or item)
        whole = self.whole(declaration)[1] if parameter or body else {}
        if text or item or kind in _TEXT_MEDIA:
            binary = None if (found := self.binary(declaration)) is None else locate(found)
        else:
            binary = None if parts is None else parts.binary
        types = _json_types(whole)
        return _SchemaRecord(
            _default(whole.get("default")) if parameter else None,
            _keywords(whole) if parameter else (),
            parts,
            self.encoding(declared, locate, members=kind in _FORMS)
            if text or media is None or kind in _FORMS
            else None,
            binary,
            body and (not types or "string" in types),
            body and _spelled(whole, self.formats),
            body and self.base64(declaration),
            form,
        )

    def choices(self, declaration: SchemaLocation) -> list[SchemaLocation]:
        """Return the branches beside null of a schema that is a union, by anyOf or oneOf, and none of another schema.

        A schema that declares properties or combines schemas with allOf beside its union is no union of its branches.
        """
        location, raw = self.whole(declaration)
        if "properties" in raw or "allOf" in raw:
            return []
        keyword, branches = next(
            ((key, value) for key in ("anyOf", "oneOf") if isinstance(value := raw.get(key), list)), ("", [])
        )
        found = (location.child(keyword, str(index)) for index in range(len(branches)))
        return [branch for branch in found if self.kinds(branch) != _NULL]

    def part(self, declaration: SchemaLocation, locate: _Locate) -> PartSchema:
        """Return how a multipart member's schema encodes its parts, reading an array's items at its resolved `items`.

        A string is binary by its binary format, or by a content media type without a content encoding. The member's
        values are read where its schema is, or where its items' is when it repeats.
        """
        target, raw = self.whole(declaration)
        own = SchemaSite(locate(declaration), locate(target))
        items: SchemaSite | None = None
        if repeated := _json_types(raw) - _NULL == _ARRAY:
            items = SchemaSite(locate(found := target.child("items")), locate(self.whole(found)[0]))
            raw = self.whole(found)[1]
        types = _json_types(raw) - _NULL
        binary = raw.get("format") == "binary" or ("contentMediaType" in raw and "contentEncoding" not in raw)
        return PartSchema(
            file=types == _STRING and binary,
            repeated=repeated,
            text=bool(types) and not types & _NESTED,
            structured=bool(types & _NESTED),
            own=own,
            items=items,
        )

    def parts(self, schema: SchemaLocation, locate: _Locate) -> _PartsRecord:
        """Return how a multipart body's schema encodes its parts: each property's, then any other property's."""
        location, raw = self.whole(schema)
        declared = raw.get("additionalProperties", True)
        additional = location.child("additionalProperties")
        extra: PartSchema | Literal["closed"] | None = None
        if declared is False:
            extra = "closed"
        elif isinstance(declared, dict) and declared:
            extra = self.part(additional, locate)
        members = tuple((found, self.part(found, locate)) for found in self.members(schema).values())
        others = ((additional, extra),) if isinstance(extra, PartSchema) else ()
        binary = next(
            (hit for found, part in (*members, *others) if not part.file and (hit := self.binary(found)) is not None),
            None,
        )
        return _PartsRecord(
            _json_types(raw) - _NULL <= _OBJECT and not self.choices(schema)[1:],
            tuple((locate(found), part) for found, part in members),
            extra,
            None if binary is None else locate(binary),
        )

    def binary(self, declaration: SchemaLocation) -> SchemaLocation | None:
        """Return where the first string the models type as raw bytes sits in a schema's values, None without one.

        The walk follows references, combined branches, properties, items and other properties.
        """
        return self._reach(declaration, {}, self.formats, self.binaries)[0]

    def base64(self, declaration: SchemaLocation) -> bool:
        """Return whether a schema's values hold a string the models type as bytes yet write as base64 text."""
        return bool(self.encoded) and self._reach(declaration, {}, self.encoded, self.encodings)[0] is not None

    def _reach(
        self,
        declaration: SchemaLocation,
        walked: dict[SchemaLocation, int],
        formats: frozenset[str],
        known: dict[SchemaLocation, SchemaLocation | None],
    ) -> tuple[SchemaLocation | None, int]:
        """Return the first string of the formats a schema reaches, and the earliest walked schema it leads back to.

        `walked` holds the schemas of the walk whose answer is pending, in their walk order. A schema that finds a
        string, or leads back to none walked before it, settles itself and every pending schema walked after it,
        since they all reach it; each schema is walked once, and `known` holds the settled answers.
        """
        if declaration in known:
            return known[declaration], len(walked)
        if (order := walked.get(declaration)) is not None:
            return None, order
        order = walked[declaration] = len(walked)
        low = order
        raw = as_mapping(self.borrow(declaration))
        found = declaration if self.raw_bytes(raw, formats) else None
        for child in self._subschemas(declaration, raw):
            if found is not None:
                break
            found, reached = self._reach(child, walked, formats, known)
            low = min(low, reached)
        while (found is not None or low == order) and len(walked) > order:
            known[walked.popitem()[0]] = found
        return found, low

    @staticmethod
    def raw_bytes(raw: dict[str, YamlValue], formats: frozenset[str]) -> bool:
        """Return whether a schema is a string of one of the formats, as those the models type as raw bytes.

        The models type an enum or a const of such strings as text.
        """
        return _spelled(raw, formats) and "enum" not in raw and "const" not in raw

    def _subschemas(self, declaration: SchemaLocation, raw: dict[str, YamlValue]) -> Iterator[SchemaLocation]:
        """Yield the schemas a schema's values are made of: its references', then its branches', members' and items'."""
        for keyword in ("$ref", "$dynamicRef"):
            if isinstance(ref := raw.get(keyword), str) and (target := self.follow(declaration.document, ref)):
                yield target
        if self.legacy and "$ref" in raw:
            return
        for keyword in _VALUE_SCHEMA_LISTS:
            if isinstance(branches := raw.get(keyword), list):
                yield from (declaration.child(keyword, str(index)) for index in range(len(branches)))
        for keyword in MODELED_SCHEMA_MAPS:
            yield from (declaration.child(keyword, str(name)) for name in as_mapping(raw.get(keyword)))
        for keyword in _VALUE_SCHEMAS:
            if isinstance(raw.get(keyword), dict):
                yield declaration.child(keyword)

    def encoding(self, schema: SchemaLocation, locate: _Locate, *, members: bool) -> EncodingFacts:
        """Return how a schema's values are written as text: as one value, as form members, and member by member.

        Only a form's members, which its encodings can give a style, are written member by member, and only a form
        has other properties of its own.
        """
        extras, declared = self.extras(schema, locate) if members else (None, False)
        return EncodingFacts(
            self.kinds(schema),
            self.shape(schema, locate, form=False),
            self.shape(schema, locate, form=True),
            tuple((locate(found), self.shape(found, locate, form=False)) for found in self.members(schema).values())
            if members
            else (),
            extras,
            declared=declared,
        )

    def extras(self, schema: SchemaLocation, locate: _Locate) -> tuple[KindSite | None, bool]:
        """Return where an object schema types the values of its other properties, and whether it allows them itself.

        Both are its allOf branches' or references' when it says nothing of them. `true` allows them and types none,
        as does a schema of any value.
        """
        _, resolved = self.resolved(schema)
        owner = (
            self.additional_owner(resolved, set()) if (self.kinds(resolved) or frozenset()) - _NULL == _OBJECT else None
        )
        said = "absent" if owner is None else self.at(owner).additional
        declared = said in {"schema", "other"}
        if owner is None or said != "schema" or not self.borrow(found := owner.child("additionalProperties")):
            return None, declared
        return self.field(found, "", locate, (locate(schema), locate(resolved)), form=True).kind, declared

    def resolved(self, declaration: SchemaLocation) -> tuple[dict[str, YamlValue], SchemaLocation]:
        """Follow a schema's references, whatever keywords sit beside them, to the schema they lead to."""
        location = self.resolve(declaration)[0]
        return as_mapping(self.borrow(location)), location

    def kinds(
        self, declaration: SchemaLocation, active: frozenset[SchemaLocation] = frozenset()
    ) -> frozenset[str] | None:
        """Return the JSON types a schema's values have, by its types, enum, const, and combined branches.

        A branch that leads back to a schema being read says nothing of its values.
        """
        value, declaration = self.resolved(declaration)
        if declaration in active:
            return None
        active |= {declaration}
        kinds = _json_types(value) if isinstance(value.get("type"), str | list) else None
        if kinds is None and isinstance(
            values := value.get("enum", [value["const"]] if "const" in value else None), list
        ):
            kinds = frozenset(_json_type(item, "object") for item in values)
        for keyword in COMBINATOR_POSITIONS:
            branches = value.get(keyword)
            if not isinstance(branches, list) or not branches:
                continue
            found_kinds = [self.kinds(declaration.child(keyword, str(index)), active) for index in range(len(branches))]
            if keyword == "allOf":
                for found in found_kinds:
                    kinds = found if kinds is None else kinds if found is None else kinds & found
            elif all(found is not None for found in found_kinds):
                union = frozenset[str]().union(*(found for found in found_kinds if found is not None))
                kinds = union if kinds is None else kinds & union
        return kinds

    def shape(self, declaration: SchemaLocation, locate: _Locate, *, form: bool) -> TextShape:
        """Return how a schema's values are written as one parameter value, or as the members of a URL-encoded form.

        A schema no builtin encoding writes keeps the members found before the problem.
        """
        members: list[MemberShape] = []
        try:
            return self._shape(declaration, locate, members, form=form)
        except _ShapeError as error:
            return TextShape("object", members=tuple(members), problem=(locate(error.declaration), str(error)))

    def _shape(
        self, declaration: SchemaLocation, locate: _Locate, members: list[MemberShape], *, form: bool
    ) -> TextShape:
        source = locate(declaration)
        value, resolved = self.resolved(declaration)
        location = locate(resolved)
        kinds = (self.kinds(resolved) or frozenset()) - _NULL
        if kinds == _ARRAY and not form:
            return TextShape("array", KindSite(locate(resolved.child("items")), ((source, _ITEMS), (location, _ITEMS))))
        if kinds != _OBJECT:
            if form:
                raise _ShapeError(resolved, _NOT_OBJECT)
            return TextShape("scalar", KindSite(location, ((source, ()), (location, ()))))
        if "patternProperties" in value:
            raise _ShapeError(resolved, _PATTERNS)
        properties = value.get("properties")
        members.extend(
            self.field(resolved.child("properties", name), name, locate, (source, location), form=form)
            for name in as_mapping(properties)
        )
        declared = value.get("additionalProperties", True)
        additional = (
            None
            if declared is False
            else MemberShape("")
            if declared is True or declared == {}
            else self.field(resolved.child("additionalProperties"), "", locate, (source, location), form=form)
        )
        return TextShape("object", members=tuple(members), additional=additional)

    def field(
        self,
        declaration: SchemaLocation,
        name: str,
        locate: _Locate,
        owners: tuple[SourceLocation, ...],
        *,
        form: bool,
    ) -> MemberShape:
        """Return a member's shape: its kind by the type bound at its schema, or as a value of a mapping at an owner.

        An array member of a form repeats, in its items' kind.
        """
        _, resolved = self.resolved(declaration)
        steps: tuple[LeafStep, ...] = ()
        location = locate(declaration)
        source = location
        if repeated := form and (self.kinds(declaration) or frozenset()) - _NULL == _ARRAY:
            steps, source = _ITEMS, locate(resolved.child("items"))
        leaves = ((location, steps), (locate(resolved), steps), *((owner, ("values", *steps)) for owner in owners))
        return MemberShape(name, KindSite(source, leaves), repeated=repeated)


class _Documents:
    """The documents an attempt loaded, by identity: the walked roots first, then the documents they reference.

    The binder holds them for the source lease and reads only the link, callback and security scheme objects in them,
    which the walk does not follow.
    """

    def __init__(self, parser: TargetApiOpenAPIParser) -> None:
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
        return descend(self.documents.get(declaration.document), declaration.tokens, "legacy-int-tokens", missing)

    def location(self, declaration: SchemaLocation, role: Literal["declaration", "use", "schema"]) -> SourceLocation:
        """Return a declaration's plain pointer location in its loaded document."""
        return SourceLocation(self.ids[declaration.document], declaration.pointer, role)


class _Models:
    """Inventory one attempt's emitted models and bind their effective fields."""

    def __init__(
        self,
        parser: TargetApiOpenAPIParser,
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
        parser = self.parser
        projector = self.binder.projector(direction, inline=inline)
        schema = parser.free_schemas[declaration]
        if (
            schema.ref is not None
            and (target := parser._schema_reference_location(declaration.document, schema.ref)) is not None  # pyright: ignore[reportPrivateUsage] # noqa: SLF001
            and (reference := parser.model_resolver.references.get(_resolver_key(parser.model_resolver, target)))
            is not None
        ):
            return projector.declaration(reference)
        with parser.model_resolver.current_root_context(declaration.document.split("/")):
            data_type = parser._build_lightweight_type(schema)  # pyright: ignore[reportPrivateUsage] # noqa: SLF001
        projected = (
            projector.project(data_type) if data_type is not None else TypeProjection(None, _NotEmittedError.reason)
        )
        from datamodel_code_generator._graph_links import drop_tree  # noqa: PLC0415

        drop_tree(data_type)
        return projected

    def variant_direction(self, model: DataModel) -> Direction:
        key = self.binder.keys.get(id(model.reference), model.reference.path)
        special = key.rpartition(SPECIAL_PATH_MARKER)[2]
        return next((found for found in _RW_DIRECTIONS if special.startswith(f"read-write-{found}")), "neutral")

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
                        tuple(map(_json_type, values)),
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
        for model, symbol in zip(self.binder.models, symbols, strict=True):
            self.locations[symbol.id] = self.model_location(model)
        projector = self.binder.projector("neutral")
        for model, symbol in zip(self.binder.models, symbols, strict=True):
            if (backend := symbol.backend) is None or symbol.kind == "enum":
                continue
            for field, slot in zip(model.fields, symbol.fields, strict=True):
                if (projected := projector.project(field.data_type).value) is None:
                    continue
                self.facts[slot] = _field_facts(field, projected, backend, symbol.facts, self.context(model, field)[3])

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
            fields = resolve_members(
                order[::-1] if functional else order,
                lambda owner: models[owner].fields,
                _wire_name if functional else lambda field: self.slots[id(field)].name,
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
        binder = self.binder
        parser = self.parser
        engine = parser.acquisitions.get((declaration, projection))
        if engine is None and projection == "value":
            engine = _resolver_key(parser.model_resolver, declaration)
        reference = parser.model_resolver.references.get(engine) if engine is not None else None
        data_type = binder.declaration_types.get(declaration) if projection == "value" else None
        projector = binder.projector(direction, inline=inline)
        if reference is not None and (
            data_type is None
            or binder.final(reference, direction) is not None
            or binder.unwrapped(reference) is not None
        ):
            return projector.declaration(reference, nullable=nullable)
        if data_type is not None:
            return projector.project(data_type)
        return self.lightweight(declaration, direction, inline=inline)

    def held(self, value: TypeView | None, seen: frozenset[SymbolId] = frozenset()) -> SymbolId | None:
        """Return the model a type is, or holds through root models, aliases and a union with None alone, or None."""
        if isinstance(value, UnionType):
            members = present(value)
            value = members[0] if len(members) == 1 else None
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

    def members_at(self, symbol: SymbolId, schema: SchemaLocation, direction: Direction) -> tuple[FieldUseBinding, ...]:
        """Return a model's members anchored at the schema a use reads.

        A member is where the model's schema declares it, as the parser built its fields
        (``reference.field_origin_of``); a model that reuse made declares them as the model it replaced. A use whose
        schema the parser read as a schema of its own, while the model stands for an equal one at another place, holds
        each member at the same place below its schema.
        """
        members: list[FieldUseBinding] = []
        model = self.binder.models[symbol]
        location = self.locations.get(symbol)
        copy = location is not None and schema != location and schema in self.copies
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
            if copy and found is not None:
                found = _rebased(found, location, schema)
            if found is not None and (anchored := self.documents.location(found, "schema")) != member.schema:
                member = replace(  # noqa: PLW2901
                    member,
                    schema=anchored,
                )
            members.append(replace(member, direction=direction) if direction != member.direction else member)
        return tuple(members)

    def use(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
        self,
        owner: OperationId | SourceLocation,
        role: TypeUseRole,
        declaration: SchemaLocation,
        use_site: SchemaLocation,
        schema: SchemaLocation,
        schema_use: SchemaLocation,
        *,
        projection: Projection = "value",
        location: str | None = None,
        name: str | None = None,
        status: str | None = None,
        media: str | None = None,
        direction: Direction | None = None,
    ) -> TypeUseId:
        direction = direction or (
            "response" if role.startswith("response") else "neutral" if role == "schema" else "request"
        )
        locate = self.documents.location
        use = TypeUseId(
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
        nullable = role != "schema" and (media is None or _media_kind(media) not in _FORMS)
        projected = self.projection(schema, projection, direction, nullable=nullable)
        members = (
            self.members_at(projected.value.symbol, schema, direction)
            if isinstance(projected.value, GeneratedSymbolType)
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
            source = self.documents.location(declaration, "schema")
            neutral = self.use(
                source, "schema", declaration, declaration, declaration, declaration, projection=projection
            )
            if (
                acquired
                and isinstance(value := self.uses[neutral].type, GeneratedSymbolType)
                and declaration not in self.binder.declaration_types
            ):
                self.relocate(declaration, value.symbol)
            directional = [
                self.directional(source, declaration, projection, direction, neutral)
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
        self,
        source: SourceLocation,
        declaration: SchemaLocation,
        projection: Projection,
        direction: Direction,
        neutral: TypeUseId,
    ) -> bool:
        """Bind a schema in one direction, keeping the use only when a variant model gives it another type."""
        use = self.use(
            source,
            "schema",
            declaration,
            declaration,
            declaration,
            declaration,
            projection=projection,
            direction=direction,
        )
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
            use = _schema_use(self.documents.location(declaration, "schema"))
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
            if (origin := type_origin_of(child)) is None:
                continue
            if moved is not None:
                origin = _rebased(origin, *moved)
            if origin != declaration and _below(origin, declaration):
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
        """Collect a model's property types at an equal schema the parser read as its own, at the same places in it."""
        if (location := self.locations.get(symbol)) is None or location == declaration:
            return
        model = self.binder.models[symbol]
        for field in model.fields:
            kind, schema, _, _ = self.context(model, field)
            if kind == "property" and schema is not None and _below(schema, location):
                self.nested(_rebased(schema, location, declaration), field.data_type, moved=(location, declaration))

    def helper_uses(self) -> None:
        """Bind each collected schema to its one type, or refuse it when fields hold it as different types."""
        for declaration, candidates in self.helpers.items():
            use = _schema_use(location := self.documents.location(declaration, "schema"))
            self.uses.setdefault(
                use,
                TypeUseBinding(use, "invalid", None, "BND_AMBIGUOUS_REPLACEMENT", schema=location)
                if len(candidates) > 1
                else self.binding(use, candidates[0]),
            )


class _Contracts(_SchemaUses):
    """Bind every type use of the operations the target parser walked."""

    def observed(self, document: str) -> bool:
        """Return whether a document is loaded, loading one that nothing but a link or security scheme references.

        A document that fails to load is tried once.
        """
        if document in self.documents.ids:
            return True
        if document in self.documents.unloadable or (raw := self.parser.load_document(document)) is None:
            self.documents.unloadable.add(document)
            return False
        self.documents.ids[document] = SourceDocumentId(len(self.documents.ids))
        self.documents.documents[document] = raw
        return True

    @cached_property
    def relocations(self) -> dict[SourceDocumentId, SourceDocumentId]:
        """Map the document identities the walk's records use to the attempt's."""
        return {provisional: self.documents.ids[uri] for uri, provisional in self.parser.record_documents.items()}

    def record(self, schema: SchemaLocation) -> _SchemaRecord:
        """Return what the walk recorded of an acquired schema, located in the attempt's documents."""
        return cast("_SchemaRecord", _relocated(self.parser.schema_records.get(schema, _UNRECORDED), self.relocations))

    def parts(self, schema: SchemaLocation, members: tuple[FieldUseBinding, ...]) -> PartFacts:
        """Return how a multipart body's schema encodes the parts of its use's members, as the walk recorded it."""
        record = self.record(schema).parts or _UNDECLARED_PARTS
        declared = dict(record.members)
        return PartFacts(
            record.object,
            tuple(
                (member.wire_name, declared.get(member.schema, _NO_PART))
                for member in members
                if member.member_kind == "property" and member.wire_name is not None and member.schema is not None
            ),
            record.extra,
        )

    def declared(self, declaration: SchemaLocation, raw: YamlValue) -> tuple[SchemaLocation, dict[str, YamlValue]]:
        """Return the object the target parser resolved a declaration to: itself, unless it is a reference."""
        return self.parser.resolutions.get(declaration) or (declaration, as_mapping(raw))

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
                else self.parser.referenced_document(declaration.document, ref)
            )
            if document is not None and not self.observed(document):
                references.append(SourceReference(source, ref, None, "document_not_observed"))
                break
            fragment = ref.partition("#")[2]
            noncanonical = NONCANONICAL_PERCENT.search(fragment) or NONCANONICAL_ESCAPE.search(unquote(fragment))
            if document is None or noncanonical or (tokens := _pointer_tokens(ref)) is None:
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
        self, uses: tuple[TypeUseId, ...], schema: SchemaLocation, role: TypeUseRole, media: str
    ) -> tuple[TypeUseId, ...]:
        """Record what the walk recorded of the media's own schema keyword on its uses.

        A multipart media's uses get its parts, a parameter's content its default and keywords, and every use the
        text encoding of its schema. A body's models decide how it travels: a text or binary media carries it as the
        raw body when they type its whole value as raw binary strings alone, none written as base64, checked by the
        model when they constrain those strings. A binary media whose schema is written as one binary string they type
        otherwise carries text read by the model, and any other body whose whole value they type as raw bytes in part
        carries them as text, which is refused.
        """
        multipart = _multipart(media)
        record = self.record(schema)
        kind = _media_kind(media)
        for use in uses:
            binding = self.uses[use]
            if record.form is not None and (held := self.held(binding.type)) is not None:
                binding = replace(binding, members=self.members_at(held, record.form, use.direction))
            raw, constrained = self.raw(schema, use.direction) if record.string and not record.base64 else ((), False)
            octets = kind in _RAW_MEDIA and bool(raw) and all(raw)
            partly = not octets and any(raw)
            self.uses[use] = replace(
                binding,
                parts=self.parts(schema, binding.members) if multipart else None,
                default=record.default if role == "parameter" else None,
                keywords=record.keywords,
                encoding=record.encoding,
                binary=None if octets else record.binary or (use.schema_site if partly else None),
                octets=octets,
                checked=octets and constrained,
                text=kind == "binary" and record.spelled and not (octets or partly),
            )
        return uses

    @cached_property
    def binary_type(self) -> TypeView | None:
        """Return the type the models give a raw binary string."""
        binary = self.parser.data_type_manager.get_data_type(Types.binary)
        return self.binder.projector("neutral", inline=True).project(binary).value

    def raw(self, schema: SchemaLocation, direction: Direction) -> tuple[tuple[bool, ...], bool]:
        """Return whether each type the models type a schema's whole value as, null aside, is a raw binary string.

        Each alias and root model is read as the type it stands for, as the schema's wrappers leave it, and a string
        may be constrained; a backend that keeps the constraints of a string in its type holds two such strings where
        another holds one. Also return whether the models constrain the value, in a type or in a wrapper's field.
        """
        projector = self.binder.projector(direction, inline=True)
        projector.constrained = False
        value = self.projection(schema, "value", direction, inline=True).value
        members = [member for member in (() if value is None else _members(value)) if not is_none(member)]
        return tuple(
            member.base == _BUILTINS["bytes"] if isinstance(member, ConstructorType) else member == self.binary_type
            for member in members
        ), projector.constrained or any(isinstance(member, ConstructorType) for member in members)

    def media(  # noqa: PLR0913
        self,
        raw: YamlValue,
        declaration: SchemaLocation,
        use_site: SchemaLocation,
        owner: OperationId,
        role: TypeUseRole,
        *,
        status: str | None = None,
        parameter_name: str | None = None,
        parameter_location: str | None = None,
    ) -> tuple[WireDeclaration, ...]:
        locate = self.documents.location
        values: list[WireDeclaration] = []
        for name, value in as_mapping(raw).items():
            medium = as_mapping(value)
            media_declaration, media_use = declaration.child("content", name), use_site.child("content", name)
            uses: list[TypeUseId] = []
            for keyword in ("schema", "itemSchema"):
                schema = media_declaration.child(keyword)
                if keyword not in medium or (schema, "value") not in self.parser.acquisitions:
                    continue
                projections: tuple[Projection, ...] = (
                    ("value", "item_stream_array")
                    if (schema, "item_stream_array") in self.parser.acquisitions
                    else ("value",)
                )
                uses.extend(
                    self.media_facts(
                        tuple(
                            self.use(
                                owner,
                                role,
                                declaration,
                                use_site,
                                schema,
                                media_use.child(keyword),
                                projection=projection,
                                name=parameter_name,
                                location=parameter_location,
                                status=status,
                                media=name,
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
            if "encoding" in medium and encoding_declaration not in self.ignored_declarations:
                header_role: TypeUseRole = (
                    "response_encoding_header" if role.startswith("response") else "request_encoding_header"
                )
                for property_name, encoding_value in self.named(
                    medium.get("encoding"), encoding_declaration, "encoding property name"
                ):
                    encoding = as_mapping(encoding_value)
                    declared = encoding_declaration.child(property_name)
                    used = media_use.child("encoding", property_name)
                    encodings.append(
                        WireDeclaration(
                            "encoding",
                            property_name,
                            DeclarationId(locate(declared, "declaration")),
                            locate(used, "use"),
                            _facts(encoding, ("contentType", "style", "explode", "allowReserved"), declared),
                            children=self.headers(
                                encoding.get("headers"),
                                declared.child("headers"),
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
                    _facts(medium, ("example", "examples"), media_declaration),
                    tuple(uses),
                    tuple(encodings),
                )
            )
        return tuple(values)

    def parameter(  # noqa: PLR0913
        self,
        declared: SchemaLocation,
        value: dict[str, YamlValue],
        use_site: SchemaLocation,
        owner: OperationId,
        role: TypeUseRole,
        *,
        name: str | None = None,
        status: str | None = None,
        media: str | None = None,
        placeholders: Container[str] = (),
    ) -> WireDeclaration:
        """Bind one parameter or header; a path parameter naming a placeholder is required, as OpenAPI requires."""
        wire_name = name if name is not None else str(value.get("name", ""))
        location = str(value["in"]) if "in" in value else None
        schemas: tuple[TypeUseId, ...] = ()
        if "schema" in value:
            schema = declared.child("schema")
            use = self.use(
                owner,
                role,
                declared,
                use_site,
                schema,
                use_site.child("schema"),
                location=location,
                name=wire_name,
                status=status,
                media=media,
            )
            record = self.record(schema)
            self.uses[use] = replace(
                self.uses[use],
                default=record.default if role == "parameter" else None,
                keywords=record.keywords,
                encoding=record.encoding,
                binary=record.binary,
            )
            schemas = (use,)
        children = self.media(
            value.get("content"),
            declared,
            use_site,
            owner,
            role,
            status=status,
            parameter_name=wire_name,
            parameter_location=location,
        )
        locate = self.documents.location
        return WireDeclaration(
            "parameter" if role == "parameter" else "header",
            wire_name,
            DeclarationId(locate(declared, "declaration")),
            locate(use_site, "use"),
            _facts(
                {**value, "required": True} if location == "path" and wire_name in placeholders else value,
                _PARAMETER_FACTS,
                declared,
            ),
            schemas,
            children,
        )

    def headers(  # noqa: PLR0913
        self,
        raw: YamlValue,
        declaration: SchemaLocation,
        use_site: SchemaLocation,
        owner: OperationId,
        role: TypeUseRole,
        *,
        status: str | None = None,
        media: str | None = None,
    ) -> tuple[WireDeclaration, ...]:
        return tuple(
            self.parameter(
                *self.declared(declaration.child(name), value),
                use_site.child(name),
                owner,
                role,
                name=name,
                status=status,
                media=media,
            )
            for name, value in self.named(raw, declaration, "header name")
            if declaration.child(name) not in self.ignored_declarations
        )

    def response(
        self, raw: YamlValue, declaration: SchemaLocation, use_site: SchemaLocation, owner: OperationId, status: str
    ) -> WireDeclaration:
        declared, value = self.declared(declaration, raw)
        content = self.media(value.get("content"), declared, use_site, owner, "response_body", status=status)
        headers = self.headers(
            value.get("headers"),
            declared.child("headers"),
            use_site.child("headers"),
            owner,
            "response_header",
            status=status,
        )
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
        for walked in self.parser.operations:
            parent = None if walked.parent is None else contracts[walked.parent].id
            contracts.append(self.operation(walked, len(contracts), parent))
        return tuple(contracts)

    def operation(  # ruff: ignore[too-many-locals]
        self, walked: _WalkedOperation, order: int, parent: OperationId | None
    ) -> OperationContract:
        locate = self.documents.location
        raw, declaration, use_site, item = walked.raw, walked.declaration, walked.use_site, walked.item
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
            self.parameter(target, value, used, identity, "parameter", placeholders=placeholders)
            for used, target, value in walked.parameters
            if target not in self.ignored_declarations
        )
        body: WireDeclaration | None = None
        if "requestBody" in raw:
            used = use_site.child("requestBody")
            declared, value = self.declared(declaration.child("requestBody"), raw["requestBody"])
            body = WireDeclaration(
                "request_body",
                None,
                DeclarationId(locate(declared, "declaration")),
                locate(used, "use"),
                _facts(value, ("required", "description"), declared),
                children=self.media(value.get("content"), declared, used, identity, "request_body"),
            )
        responses = tuple(
            self.response(
                value,
                declaration.child("responses", status),
                use_site.child("responses", status),
                identity,
                status,
            )
            for status, value in document_statuses(
                as_mapping(raw.get("responses")), lambda status: [str(declaration.child("responses", status))]
            ).items()
        )
        callbacks = tuple(
            self.metadata(
                "callback",
                name,
                declaration.child("callbacks", name),
                use_site.child("callbacks", name),
                callback,
            )
            for name, callback in as_mapping(raw.get("callbacks")).items()
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
    parser: TargetApiOpenAPIParser,
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
    operations = builder.operations()
    builder.schema_uses()
    builder.helper_uses()
    builder.extras()
    security_schemes = builder.security_schemes()
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
            document_facts=parser.document_facts.get(next(iter(documents.ids), ""), ()),
            hint_type=builder.binder.hints.hint_type,
            hint_imports=tuple(builder.binder.hints.fixed),
            names=TargetNames.of(parser.model_resolver),
        ),
        _pristine(parser, documents.documents),
    )


def _pristine(
    parser: TargetApiOpenAPIParser, documents: dict[str, dict[str, YamlValue]]
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


def _unnamed(parser: TargetApiOpenAPIParser) -> str:
    """Return the location of a document read as text, such as the input when its type is detected.

    Its references resolve against the parser's base path, as the models' do.
    """
    return f"{Path(parser.base_path).resolve().as_uri()}/"


if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Container, Iterable, Iterator, Mapping, Sequence

    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator._python_type_annotation import PythonTypeExpr
    from datamodel_code_generator._python_type_binding import BoundPythonType
    from datamodel_code_generator._runtime.model_codecs.media import MediaKind
    from datamodel_code_generator._source import YamlValue
    from datamodel_code_generator._target_contract import (
        BackendName,
        BackendValue,
        DefaultKind,
        FrozenLiteral,
        LeafStep,
        Projection,
        TypeArgument,
        TypeProjectionReason,
        TypeUseRole,
        TypeView,
    )
    from datamodel_code_generator.config import OpenAPIParserConfig
    from datamodel_code_generator.imports import Imports
    from datamodel_code_generator.model._decisions.enum_kind import LiteralValueKind
    from datamodel_code_generator.parser.base import ForwarderMap, ModuleContext, ModulePath, ParseConfig, Result
    from datamodel_code_generator.parser.openapi_media import MediaOwner
    from datamodel_code_generator.parser.openapi_scope import ApiParameterDeclaration, SchemaRole
    from datamodel_code_generator.reference import ModelResolver, Reference, _RwDirection
