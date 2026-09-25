"""Decide at generation time whether FastAPI handles a parameter, a body, or a primary response natively.

Native handling requires that the standard FastAPI declaration and the final model types accept exactly what
the source schema accepts. Every check is conservative: a shape the checks cannot prove equal uses the common
codec adapter, which validates the wire value itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final, Literal, TypeAlias, TypeGuard, get_args

from datamodel_code_generator._generation_contract import (
    BuiltinType,
    ConstructorType,
    GeneratedSymbolType,
    GenericType,
    ImportedType,
    LiteralScalar,
    LiteralType,
    NoneType,
    SourceLocation,
    UnionType,
)
from datamodel_code_generator._runtime.model_codecs.schema import STANDARD_FORMATS
from datamodel_code_generator.model.binding import KnownBackendValue

if TYPE_CHECKING:
    from collections.abc import Sequence

    from datamodel_code_generator._generation_contract import (
        FieldUseBinding,
        FinalModelSymbol,
        FinalPythonType,
        ModelFieldFacts,
        SymbolId,
        TypeArgument,
        TypeUseBinding,
    )
    from datamodel_code_generator._openapi_wire_plan import WirePlan
    from datamodel_code_generator._runtime.model_codecs.context import Direction
    from datamodel_code_generator._runtime.model_codecs.wire import WireValue

Reason: TypeAlias = Literal[
    "explicit_raw",
    "explicit_adapter",
    "unsupported_wire_shape",
    "envelope_required",
    "opaque_native_semantics",
    "native_shape_mismatch",
    "native_required_mismatch",
    "native_nullable_mismatch",
    "native_alias_mismatch",
    "source_assertion_not_projected",
    "response_presence_or_nullable_mismatch",
    "native_supported",
]
Schema: TypeAlias = "Mapping[str, WireValue]"
Constraints: TypeAlias = "dict[str, object]"

PRECEDENCE: Final = {reason: index for index, reason in enumerate(get_args(Reason))}
INTEGER_RANGES: Final = {"int32": (-(2**31), 2**31 - 1), "int64": (-(2**63), 2**63 - 1)}
BOUNDS: Final = {
    "minimum": "ge",
    "exclusiveMinimum": "gt",
    "maximum": "le",
    "exclusiveMaximum": "lt",
    "multipleOf": "multiple_of",
}
STRING_LENGTHS: Final = {"minLength": "min_length", "maxLength": "max_length"}
ARRAY_LENGTHS: Final = {"minItems": "min_length", "maxItems": "max_length"}
ANNOTATIONS: Final = frozenset({
    "$comment",
    "default",
    "deprecated",
    "description",
    "example",
    "examples",
    "externalDocs",
    "format",
    "readOnly",
    "title",
    "writeOnly",
    "xml",
})
SHAPES: Final = frozenset({
    "allOf",
    "anyOf",
    "contains",
    "dependentRequired",
    "dependentSchemas",
    "else",
    "if",
    "maxContains",
    "maxProperties",
    "minContains",
    "minProperties",
    "not",
    "oneOf",
    "patternProperties",
    "prefixItems",
    "propertyNames",
    "then",
    "unevaluatedItems",
    "unevaluatedProperties",
})
CODEC_FORMATS: Final = frozenset({
    *STANDARD_FORMATS,
    "byte",
    "date-time-local",
    "int32",
    "int64",
    "regex",
    "time-local",
})
_IMPORTED_LEAVES: Final = {
    ("datetime", "date"): "date",
    ("datetime", "datetime"): "datetime",
    ("pydantic", "AwareDatetime"): "aware_datetime",
    ("typing", "Any"): "any",
    ("uuid", "UUID"): "uuid",
}
_BUILTIN_LEAVES: Final = {"str": "str", "int": "int", "float": "float", "bool": "bool", "object": "any"}
_STRING: Final = frozenset({"string"})
_LEAF_TYPES: Final = {
    "str": (_STRING,),
    "int": (frozenset({"integer"}),),
    "float": (frozenset({"number"}), frozenset({"number", "integer"})),
    "bool": (frozenset({"boolean"}),),
    "date": (_STRING,),
    "aware_datetime": (_STRING,),
    "uuid": (_STRING,),
}
_FORMAT_LEAVES: Final = {"date": "date", "date-time": "aware_datetime", "uuid": "uuid"}
_FORMATTED: Final = frozenset(_FORMAT_LEAVES.values())
_CONSTRUCTORS: Final = {"conint": "int", "constr": "str", "confloat": "float"}
_SAFE_CONFIGURATION: Final = frozenset({"extra", "frozen", "populate_by_name", "validate_by_name", "validate_by_alias"})
_OPTIONAL_FALLBACK: Final = "optional_fallback"
_SYMBOL_BACKENDS: Final = {"pydantic_v2.BaseModel": "pydantic", "pydantic_v2.dataclass": "pydantic_dataclass"}


@dataclass(frozen=True, slots=True, order=True)
class Finding:
    """One reason a use cannot be native, ordered by the fixed reason precedence and then by discovery."""

    rank: int
    order: int
    reason: Reason
    source: SourceLocation | None


def kinds(schema: Schema) -> frozenset[str] | None:
    """Return the JSON types a schema declares through `type`, or None when it declares none."""
    value = schema.get("type")
    if isinstance(value, str):
        return frozenset({value})
    if isinstance(value, tuple):
        return frozenset(str(item) for item in value)
    return None


def nullable(schema: Schema) -> bool:
    """Return whether a schema accepts null through its type, enum, or const."""
    return (
        "null" in (kinds(schema) or ())
        or (isinstance(values := schema.get("enum"), tuple) and None in values)
        or ("const" in schema and schema["const"] is None)
    )


def number(value: object) -> TypeGuard[int | float | Decimal]:
    """Return whether a wire value is a JSON number, which excludes booleans."""
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def at(location: SourceLocation, *tokens: str | int) -> SourceLocation:
    """Return the location of a child schema."""
    escaped = (str(token).replace("~", "~0").replace("/", "~1") for token in tokens)
    return SourceLocation(location.document, location.pointer + "".join(f"/{token}" for token in escaped), "schema")


def literal(argument: TypeArgument) -> object:
    """Return a finite literal's Python value, or the argument itself when it is source text."""
    return argument.value if isinstance(argument, LiteralScalar) else argument


def assertions_projected(
    schema: Schema, constraints: Constraints, lengths: Mapping[str, str], integer_format: str | None
) -> bool:
    """Return whether the type constraints equal the schema's bound and length assertions exactly."""
    expected: Constraints = {
        keyword: schema[source] for source, keyword in (*BOUNDS.items(), *lengths.items()) if source in schema
    }
    relevant = {name: value for name, value in constraints.items() if name in {*BOUNDS.values(), *lengths.values()}}
    if integer_format is not None:
        low, high = INTEGER_RANGES[integer_format]
        lowest = _bound(relevant, "ge", "gt", 1)
        highest = _bound(relevant, "le", "lt", -1)
        if lowest is None or highest is None or lowest < low or highest > high:
            return False
    return all(_equal(relevant.get(name), value) for name, value in expected.items()) and set(relevant) <= set(expected)


def _bound(constraints: Constraints, inclusive: str, exclusive: str, step: int) -> int | None:
    if number(value := constraints.get(inclusive)):
        return int(value)
    if number(value := constraints.get(exclusive)):
        return int(value) + step
    return None


def _equal(left: object, right: object) -> bool:
    return (number(left) and number(right) and Decimal(str(left)) == Decimal(str(right))) or (
        type(left) is type(right) and left == right
    )


class GraphCheck:
    """Compare one directional body or response use's final types with its schema, collecting findings."""

    def __init__(
        self,
        symbols: Mapping[SymbolId, FinalModelSymbol],
        members: Mapping[SymbolId, Sequence[FieldUseBinding]],
        wire: WirePlan,
        direction: Direction,
        backend: str,
    ) -> None:
        """Keep the batch's indexed symbols and field bindings for one direction and one Pydantic backend."""
        self.wire = wire
        self.direction = direction
        self.backend = _SYMBOL_BACKENDS[backend]
        self.flag = "readOnly" if direction == "request" else "writeOnly"
        self.symbols = symbols
        self.members = members
        self.findings: list[Finding] = []
        self.visited: set[SymbolId] = set()

    def check(self, use: TypeUseBinding) -> tuple[Reason, SourceLocation | None]:
        """Return the highest-precedence reason the use cannot be native, or native support."""
        self.findings.clear()
        self.visited.clear()
        if use.type is None or use.schema is None:
            return "opaque_native_semantics", use.id.use_site
        self.value(use.schema, use.type, {})
        if not self.findings:
            return "native_supported", None
        finding = min(self.findings)
        return finding.reason, finding.source

    def report(self, reason: Reason, source: SourceLocation | None) -> None:
        """Record one finding in discovery order."""
        self.findings.append(Finding(PRECEDENCE[reason], len(self.findings), reason, source))

    def value(self, location: SourceLocation, value: FinalPythonType, constraints: Constraints) -> None:
        """Compare one final type with the schema at a location."""
        resolved, schema = self.wire.schema(location)
        if "$ref" in schema:
            self.report("native_shape_mismatch", resolved)
            return
        match value:
            case GeneratedSymbolType():
                self.symbol(resolved, schema, self.symbols[value.symbol], constraints)
            case UnionType():
                self.union(resolved, value, constraints)
            case GenericType() if isinstance(value.base, BuiltinType) and value.base.name == "list":
                self.array(resolved, schema, value, constraints)
            case GenericType() if isinstance(value.base, BuiltinType) and value.base.name == "dict":
                self.mapping(resolved, schema, value)
            case _:
                self.leaf(resolved, schema, value, constraints)

    def keywords(self, location: SourceLocation, schema: Schema, allowed: frozenset[str]) -> None:
        """Report keywords outside the allowed structure, annotations, and projected assertions."""
        for keyword in schema:
            if keyword in allowed or keyword in ANNOTATIONS or keyword.startswith("x-"):
                continue
            if keyword in SHAPES:
                self.report("native_shape_mismatch", at(location, keyword))
            else:
                self.report("source_assertion_not_projected", at(location, keyword))
            return

    def symbol(
        self, location: SourceLocation, schema: Schema, symbol: FinalModelSymbol, constraints: Constraints
    ) -> None:
        """Compare a generated symbol with its schema: models by field, roots by their root value."""
        members = self.members.get(symbol.id, ())
        match symbol.kind:
            case "enum":
                self.keywords(location, schema, frozenset({"type", "enum", "const"}))
            case "alias" if (facts := _facts(members)) is not None and symbol.id not in self.visited:
                self.visited.add(symbol.id)
                self.value(location, facts.type, constraints)
            case "root" if symbol.backend == "pydantic" and (facts := _facts(members)) is not None:
                if symbol.id not in self.visited:
                    self.visited.add(symbol.id)
                    self.value(location, facts.type, constraints)
            case "model" if symbol.backend == self.backend and self.builtin(symbol, location):
                if symbol.id not in self.visited:
                    self.visited.add(symbol.id)
                    self.model(location, schema, symbol, members)
            case _:
                self.report("opaque_native_semantics", location)

    def builtin(self, symbol: FinalModelSymbol, location: SourceLocation) -> bool:
        """Return whether a model has builtin semantics and only settings whose effect the checks know."""
        facts = symbol.facts
        if facts is None or facts.custom_base:
            self.report("opaque_native_semantics", location)
            return False
        for setting in (*facts.parameters, *facts.configuration):
            if not setting.present:
                continue
            if not isinstance(setting.value, KnownBackendValue) or setting.name not in _SAFE_CONFIGURATION:
                self.report("opaque_native_semantics", location)
                return False
        return True

    def model(
        self, location: SourceLocation, schema: Schema, symbol: FinalModelSymbol, members: Sequence[FieldUseBinding]
    ) -> None:
        """Compare a model's fields with the object schema's properties, requirements, and extras policy."""
        self.keywords(location, schema, frozenset({"type", "properties", "required", "additionalProperties"}))
        if kinds(schema) not in {None, frozenset({"object"})}:
            self.report("native_shape_mismatch", location)
        properties = schema.get("properties")
        declared = properties if isinstance(properties, Mapping) else {}
        required = schema.get("required")
        names = frozenset(str(name) for name in required) if isinstance(required, tuple) else frozenset()
        additional = schema.get("additionalProperties", True)
        if (additional is False) != (_setting(symbol, "extra") == "forbid") or (
            isinstance(additional, Mapping) and additional
        ):
            self.report("native_shape_mismatch", at(location, "additionalProperties"))
        fields = {member.wire_name: member for member in members if member.wire_name is not None}
        by_name = _setting(symbol, "populate_by_name") or _setting(symbol, "validate_by_name")
        for name in declared:
            if (member := fields.get(name)) is None or (facts := member.model_facts) is None or member.slot is None:
                self.report("native_shape_mismatch", at(location, "properties", name))
                continue
            property_location = member.schema or at(location, "properties", name)
            if facts.read_only if self.direction == "request" else facts.write_only:
                self.report(
                    "envelope_required" if name in names else "native_shape_mismatch",
                    at(property_location, self.flag),
                )
            self.field(
                property_location,
                name,
                facts,
                required=name in names,
                renamed=bool(by_name) and member.slot.name != name,
            )
        for member in members:
            if member.wire_name not in declared:
                self.report("native_shape_mismatch", member.schema or location)

    def field(
        self, location: SourceLocation, name: str, facts: ModelFieldFacts, *, required: bool, renamed: bool
    ) -> None:
        """Compare one field's requiredness, nullability, aliases, and type with its property schema."""
        _, schema = self.wire.schema(location)
        optional = not facts.required or facts.has_default or facts.explicit_default_factory
        if required == optional:
            self.report("native_required_mismatch", location)
        provenance = facts.none_default_provenance
        missing = provenance.emitted_default == "missing"
        accepts_null = bool(facts.type_has_null) and not (missing and provenance.annotation_null_origin == "none")
        if provenance.annotation_null_origin == _OPTIONAL_FALLBACK or accepts_null != nullable(schema):
            self.report("native_nullable_mismatch", location)
        if self.backend == "pydantic_dataclass" and self.direction == "response" and optional:
            self.report("response_presence_or_nullable_mismatch", location)
        if not _aliased(facts, name, self.direction) or renamed:
            self.report("native_alias_mismatch", location)
        keywords = {name: literal(value) for name, value in facts.backend.emitted.constructor_keywords}
        if keywords.get("strict") not in {None, False}:
            self.report("opaque_native_semantics", location)
        self.value(location, facts.type, keywords)

    def union(self, location: SourceLocation, value: UnionType, constraints: Constraints) -> None:
        """Accept only a nullable wrapper around one member, matched by the schema's null acceptance."""
        members = [member for member in value.members if not isinstance(member, NoneType)]
        if len(members) != 1:
            self.report("native_shape_mismatch", location)
            return
        self.value(location, members[0], constraints)

    def array(self, location: SourceLocation, schema: Schema, value: GenericType, constraints: Constraints) -> None:
        """Compare a list with an array schema, its item schema, and its item count assertions."""
        self.keywords(location, schema, frozenset({"type", "items", *ARRAY_LENGTHS}))
        if (kinds(schema) or frozenset()) - {"null"} != {"array"} or "items" not in schema:
            self.report("native_shape_mismatch", location)
            return
        if not assertions_projected(schema, constraints, ARRAY_LENGTHS, None):
            self.report("source_assertion_not_projected", location)
        self.value(at(location, "items"), value.arguments[0], {})

    def mapping(self, location: SourceLocation, schema: Schema, value: GenericType) -> None:
        """Compare a string-keyed dict with an object schema that only constrains its additional values."""
        self.keywords(location, schema, frozenset({"type", "additionalProperties"}))
        additional = schema.get("additionalProperties", True)
        if (kinds(schema) or frozenset()) - {"null"} != {"object"} or additional is False:
            self.report("native_shape_mismatch", location)
        elif isinstance(additional, Mapping) and additional:
            self.value(at(location, "additionalProperties"), value.arguments[-1], {})

    def leaf(self, location: SourceLocation, schema: Schema, value: FinalPythonType, constraints: Constraints) -> None:
        """Compare a scalar, enum, literal, or unconstrained type with its schema and assertions."""
        leaf, extra = leaf_kind(value)
        present = (kinds(schema) or frozenset()) - {"null"}
        match leaf:
            case None:
                self.report("opaque_native_semantics", location)
            case "any":
                if present or set(schema) - ANNOTATIONS:
                    self.report("native_shape_mismatch", location)
            case "literal":
                self.keywords(location, schema, frozenset({"type", "enum", "const"}))
            case _:
                self.scalar(location, schema, leaf, present, {**extra, **constraints})

    def scalar(
        self, location: SourceLocation, schema: Schema, leaf: str, present: frozenset[str], constraints: Constraints
    ) -> None:
        """Compare a builtin scalar or formatted string with its schema type, format, and assertions."""
        format_ = schema.get("format")
        self.keywords(location, schema, frozenset({"type", *BOUNDS, *STRING_LENGTHS}))
        if present not in _LEAF_TYPES.get(leaf, ()):
            self.report("native_shape_mismatch", location)
            return
        if (leaf == "str" and format_ in CODEC_FORMATS) or (
            leaf in _FORMATTED and _FORMAT_LEAVES.get(str(format_)) != leaf
        ):
            self.report("source_assertion_not_projected", at(location, "format"))
        integer = str(format_) if leaf == "int" and format_ in INTEGER_RANGES else None
        if not assertions_projected(schema, constraints, STRING_LENGTHS if leaf == "str" else {}, integer):
            self.report("source_assertion_not_projected", location)


def leaf_kind(value: FinalPythonType) -> tuple[str | None, Constraints]:
    """Classify a final scalar type, with the constraints its constructor carries."""
    match value:
        case BuiltinType():
            return _BUILTIN_LEAVES.get(value.name), {}
        case ConstructorType() if value.callable.import_.from_ == "pydantic" and (
            kind := _CONSTRUCTORS.get(value.callable.import_.import_)
        ):
            return kind, {name: literal(argument) for name, argument in value.keywords}
        case ImportedType():
            return _IMPORTED_LEAVES.get((value.import_.from_ or "", value.import_.import_)), {}
        case LiteralType():
            return "literal", {}
    return None, {}


def _setting(symbol: FinalModelSymbol, name: str) -> object:
    for setting in symbol.facts.configuration if symbol.facts is not None else ():
        if setting.name == name and setting.present and isinstance(setting.value, KnownBackendValue):
            return literal(setting.value.value)
    return None


def _facts(members: Sequence[FieldUseBinding]) -> ModelFieldFacts | None:
    return next((member.model_facts for member in members if member.model_facts is not None), None)


def _aliased(facts: ModelFieldFacts, name: str, direction: Direction) -> bool:
    if facts.validation_aliases is not None and facts.validation_aliases != (name,):
        return False
    reads = facts.alias or name
    writes = (facts.serialization_alias if facts.use_serialization_alias else None) or facts.alias or name
    return (reads if direction == "request" else writes) == name and reads == writes
