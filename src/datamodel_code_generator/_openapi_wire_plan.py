"""Plan offline wire schemas, static pattern checks, and parameter codecs from an accepted batch."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from math import isfinite
from typing import TYPE_CHECKING, Final, Literal, TypeAlias
from urllib.parse import quote, unquote, urldefrag, urljoin

from datamodel_code_generator._generation_contract import (
    BindingCaptureError,
    GeneratedTypeContractBatch,
    LiteralMapping,
    LiteralScalar,
    LiteralSequence,
    OperationContract,
    OperationId,
    SourceDocumentId,
    SourceLocation,
    TypeUseId,
    WireDeclaration,
)
from datamodel_code_generator._runtime.model_codecs.media import (
    FieldPlan,
    LexicalKind,
    media_kind,
    normalize_media_type,
)
from datamodel_code_generator._runtime.model_codecs.parameters import (
    ParameterLocation,
    ParameterPlan,
    ValueShape,
    builtin_content,
)
from datamodel_code_generator._runtime.model_codecs.patterns import (
    PatternDialectError,
    PatternResourceError,
    plan_pattern,
)
from datamodel_code_generator._runtime.model_codecs.schema import (
    SCHEMA_ARRAY_KEYWORDS,
    SCHEMA_MAP_KEYWORDS,
    SCHEMA_VALUE_KEYWORDS,
    SchemaResource,
)
from datamodel_code_generator._runtime.model_codecs.wire import JSONValue, escape_pointer_token, freeze_wire

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from datamodel_code_generator._generation_contract import FrozenLiteral
    from datamodel_code_generator._openapi_generation import SourceLease
    from datamodel_code_generator._source import YamlValue

CodecReason: TypeAlias = Literal[
    "BND_UNRESOLVED_REFERENCE",
    "MC_PARAMETER_ENCODING",
    "MC_PATTERN_DIALECT",
    "MC_PATTERN_RESOURCE_LIMIT",
    "MC_SCHEMA_DIALECT",
]

LOGICAL_ROOT: Final = "https://dcg.invalid/inputs/"
_JSON_SCHEMA_2020_12: Final = "https://json-schema.org/draft/2020-12/schema"
_OAS_DIALECT_PREFIXES: Final = (
    "https://spec.openapis.org/oas/3.1/dialect/",
    "https://spec.openapis.org/oas/3.2/dialect/",
)
_FRAGMENT_SAFE: Final = "/?:@!$&'()*+,;=~"
_UNSUPPORTED_KEYWORDS: Final = frozenset({
    "$dynamicAnchor",
    "$dynamicRef",
    "$recursiveAnchor",
    "$recursiveRef",
    "additionalItems",
    "dependencies",
})
_LOCATIONS: Final[dict[object, ParameterLocation]] = {
    "path": "path",
    "query": "query",
    "querystring": "querystring",
    "header": "header",
    "cookie": "cookie",
}
_DEFAULT_STYLES: Final = {"path": "simple", "query": "form", "header": "simple", "cookie": "form"}
_NULL: Final = frozenset({"null"})
_ARRAY: Final = frozenset({"array"})
_OBJECT: Final = frozenset({"object"})
_STRING: Final = frozenset({"string"})
_INTEGER_NUMBER: Final = frozenset({"integer", "number"})
_LEXICAL_KINDS: Final[dict[str, LexicalKind]] = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
}


@dataclass(frozen=True, slots=True)
class CodecDiagnostic:
    """Report one generation-time wire rule that requires an explicit adapter or a source fix."""

    code: CodecReason
    source: SourceLocation
    message: str
    operation: OperationId | None = None


@dataclass(frozen=True, slots=True)
class WirePlan:
    """Keep bundled normalized schema resources, per-use schema IDs, and parameter plans."""

    resources: tuple[SchemaResource, ...]
    schema_ids: tuple[tuple[TypeUseId, str], ...]
    parameters: tuple[tuple[OperationId, tuple[ParameterPlan, ...]], ...]
    diagnostics: tuple[CodecDiagnostic, ...]


class _PlanError(Exception):
    def __init__(self, *, code: CodecReason, source: SourceLocation, message: str) -> None:
        super().__init__(message)
        self.diagnostic = CodecDiagnostic(code, source, message)


class _Omit(Enum):
    OMIT = "omit"


def _at(location: SourceLocation, *tokens: str | int) -> SourceLocation:
    return replace(location, pointer=location.pointer + "".join(f"/{escape_pointer_token(token)}" for token in tokens))


def _tokens(pointer: str) -> list[str]:
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")] if pointer else []


def _enclosing(roots: Mapping[str, object], pointer: str) -> str | None:
    while pointer not in roots:
        if not pointer:
            return None
        pointer = pointer[: pointer.rfind("/")]
    return pointer


def _fact(declaration: WireDeclaration, key: str) -> object:
    return next(
        (value.value for name, value in declaration.facts if name == key and isinstance(value, LiteralScalar)), None
    )


def _supported_dialect(dialect: str) -> bool:
    return dialect == _JSON_SCHEMA_2020_12 or dialect.startswith(_OAS_DIALECT_PREFIXES)


class _WirePlanner:
    def __init__(self, batch: GeneratedTypeContractBatch, lease: SourceLease) -> None:
        self.batch = batch
        self.lease = lease
        root, *others = batch.documents
        self.logical = {root.id: f"{LOGICAL_ROOT}root"} | {
            document.id: f"{LOGICAL_ROOT}documents/{index}"
            for index, document in enumerate(sorted(others, key=lambda document: document.uri))
        }
        self.retrieval = {document.uri: document.id for document in batch.documents}
        self.bases = {document.id: document.uri for document in batch.documents}
        self.diagnostics: list[CodecDiagnostic] = []
        self.roots: dict[SourceDocumentId, dict[str, JSONValue]] = {}
        self.pending: list[SourceLocation] = []
        self.aliases: dict[str, SourceDocumentId] = {}
        self.containers: set[SourceDocumentId] = set()
        for document in batch.documents:
            match lease.borrow(SourceLocation(document.id, "", "schema")):
                case {"openapi": _}:
                    self.containers.add(document.id)
                case {"$id": str() as identifier}:
                    self.aliases[urljoin(document.uri, identifier)] = document.id
                case _:
                    pass
        specification = lease.borrow(SourceLocation(root.id, "", "schema"))
        settings = specification if isinstance(specification, dict) else {}
        self.version = str(settings.get("openapi", ""))
        self.legacy = self.version.startswith("3.0")
        if isinstance(dialect := settings.get("jsonSchemaDialect"), str) and not _supported_dialect(dialect):
            self.report(
                "MC_SCHEMA_DIALECT",
                SourceLocation(root.id, "/jsonSchemaDialect", "schema"),
                "The default schema dialect is not builtin",
            )

    def report(self, code: CodecReason, source: SourceLocation, message: str) -> None:
        if (diagnostic := CodecDiagnostic(code, source, message)) not in self.diagnostics:
            self.diagnostics.append(diagnostic)

    def schema_id(self, location: SourceLocation) -> str:
        return f"{self.logical[location.document]}#{quote(location.pointer, safe=_FRAGMENT_SAFE)}"

    def root(self, location: SourceLocation) -> str:
        self.pending.append(location)
        while self.pending:
            current = self.pending.pop()
            roots = self.roots.setdefault(current.document, {})
            if current.pointer not in roots:
                roots[current.pointer] = self.schema(self.lease.borrow(current), current)
        return self.schema_id(location)

    def resolve(self, reference: str, location: SourceLocation) -> SourceLocation:
        uri, fragment = urldefrag(urljoin(self.bases[location.document], reference))
        if (document := self.aliases.get(uri, self.retrieval.get(uri))) is None:
            raise _PlanError(
                code="BND_UNRESOLVED_REFERENCE",
                source=location,
                message="A schema reference targets an unobserved document",
            )
        target = SourceLocation(document, unquote(fragment), "schema")
        try:
            self.lease.borrow(target)
        except BindingCaptureError:
            raise _PlanError(
                code="BND_UNRESOLVED_REFERENCE", source=location, message="A schema reference pointer does not exist"
            ) from None
        return target

    def reference(self, reference: YamlValue, location: SourceLocation) -> JSONValue:
        if not isinstance(reference, str):
            self.report("MC_SCHEMA_DIALECT", location, "A schema reference must be a string")
            return None
        try:
            target = self.resolve(reference, location)
        except _PlanError as error:
            self.report(error.diagnostic.code, location, error.diagnostic.message)
            return reference
        self.pending.append(target)
        return self.schema_id(target)

    def schema(self, value: YamlValue, location: SourceLocation) -> JSONValue:
        if isinstance(value, bool):
            return value
        if not isinstance(value, dict):
            self.report("MC_SCHEMA_DIALECT", location, "A schema must be an object or a boolean")
            return None
        self.check_identity(value, location)
        if self.legacy and "$ref" in value:
            return {"$ref": self.reference(value["$ref"], _at(location, "$ref"))}
        normalized = {
            str(key): member
            for key, item in value.items()
            if (member := self.member(str(key), item, _at(location, str(key)))) is not _Omit.OMIT
        }
        if self.legacy:
            _legacy_keywords(value, normalized)
        return normalized

    def check_identity(self, value: Mapping[str, YamlValue], location: SourceLocation) -> None:
        if "$id" in value and (location.pointer or location.document in self.containers):
            self.report(
                "MC_SCHEMA_DIALECT",
                _at(location, "$id"),
                "Embedded schema resources require an explicit schema adapter",
            )
        if "$anchor" in value and location.document in self.containers:
            self.report(
                "MC_SCHEMA_DIALECT",
                _at(location, "$anchor"),
                "Schema anchors in OpenAPI documents require an explicit schema adapter",
            )
        if isinstance(dialect := value.get("$schema"), str) and not _supported_dialect(dialect):
            self.report("MC_SCHEMA_DIALECT", _at(location, "$schema"), "The schema dialect is not builtin")

    def member(self, key: str, item: YamlValue, at: SourceLocation) -> JSONValue | _Omit:
        normalized: JSONValue | _Omit = _Omit.OMIT
        match key, item:
            case "$ref", _:
                normalized = self.reference(item, at)
            case "$id" | "$anchor" | "$schema", _:
                pass
            case _, _ if key in _UNSUPPORTED_KEYWORDS:
                self.report("MC_SCHEMA_DIALECT", at, f"Keyword {key} requires an explicit schema adapter")
            case "items", list():
                self.report("MC_SCHEMA_DIALECT", at, "Array-form items requires an explicit schema adapter")
            case "nullable", _ if self.legacy:
                pass
            case "exclusiveMinimum" | "exclusiveMaximum", bool():
                if not self.legacy:
                    self.report("MC_SCHEMA_DIALECT", at, "Boolean exclusive bounds belong to OpenAPI 3.0")
            case "pattern", str():
                self.check_pattern(item, at)
                normalized = item
            case _:
                normalized = self.applicator(key, item, at)
        return normalized

    def applicator(self, key: str, item: YamlValue, at: SourceLocation) -> JSONValue:
        match item:
            case dict() if key in SCHEMA_MAP_KEYWORDS:
                if key == "patternProperties":
                    for pattern in item:
                        self.check_pattern(str(pattern), _at(at, str(pattern)))
                return {str(name): self.schema(child, _at(at, str(name))) for name, child in item.items()}
            case list() if key in SCHEMA_ARRAY_KEYWORDS:
                return [self.schema(child, _at(at, index)) for index, child in enumerate(item)]
            case _ if key in SCHEMA_VALUE_KEYWORDS:
                return self.schema(item, at)
            case _:
                return self.data(item, at)

    def data(self, value: YamlValue, location: SourceLocation) -> JSONValue:
        match value:
            case dict():
                return {str(key): self.data(item, _at(location, str(key))) for key, item in value.items()}
            case list():
                return [self.data(item, _at(location, index)) for index, item in enumerate(value)]
            case float() if not isfinite(value):
                self.report("MC_SCHEMA_DIALECT", location, "A schema value must be a finite JSON number")
                return None
            case _:
                return value

    def check_pattern(self, source: str, location: SourceLocation) -> None:
        try:
            plan_pattern(source)
        except PatternDialectError:
            self.report("MC_PATTERN_DIALECT", location, "The pattern is outside the builtin ECMA-262 Unicode grammar")
        except PatternResourceError:
            self.report("MC_PATTERN_RESOURCE_LIMIT", location, "The pattern exceeds a static resource limit")

    def resources(self) -> tuple[SchemaResource, ...]:
        resources: list[SchemaResource] = []
        for document, roots in sorted(self.roots.items(), key=lambda item: self.logical[item[0]]):
            covered = tuple(
                pointer
                for pointer in sorted(roots)
                if not pointer or _enclosing(roots, pointer[: pointer.rfind("/")]) is None
            )
            if covered == ("",):
                resources.append(SchemaResource(uri=self.logical[document], contents=freeze_wire(roots[""])))
                continue
            container: JSONValue = None
            for pointer in covered:
                container = self.place(
                    container, SourceLocation(document, "", "schema"), _tokens(pointer), roots[pointer]
                )
            resources.append(SchemaResource(uri=self.logical[document], contents=freeze_wire(container), roots=covered))
        return tuple(resources)

    def place(self, container: JSONValue, location: SourceLocation, tokens: list[str], value: JSONValue) -> JSONValue:
        if not tokens:
            return value
        head, *rest = tokens
        child = _at(location, head)
        if isinstance(self.lease.borrow(location), list):
            index = int(head)
            items = container if isinstance(container, list) else []
            items.extend([None] * (index + 1 - len(items)))
            items[index] = self.place(items[index], child, rest, value)
            return items
        members = container if isinstance(container, dict) else {}
        members[head] = self.place(members.get(head), child, rest, value)
        return members

    def resolved(self, location: SourceLocation) -> tuple[Mapping[str, YamlValue], SourceLocation]:
        seen: set[SourceLocation] = set()
        while True:
            try:
                value = self.lease.borrow(location)
            except BindingCaptureError:
                return {}, location
            if not isinstance(value, dict):
                return {}, location
            if not isinstance(reference := value.get("$ref"), str) or location in seen:
                return value, location
            seen.add(location)
            location = self.resolve(reference, _at(location, "$ref"))


def _legacy_keywords(raw: Mapping[str, YamlValue], normalized: dict[str, JSONValue]) -> None:
    if raw.get("nullable") is True and isinstance(kind := raw.get("type"), str):
        normalized["type"] = [kind, "null"]
    for exclusive, inclusive in (("exclusiveMinimum", "minimum"), ("exclusiveMaximum", "maximum")):
        if raw.get(exclusive) is True and inclusive in normalized:
            normalized[exclusive] = normalized.pop(inclusive)


def plan_wire(
    batch: GeneratedTypeContractBatch, lease: SourceLease, uses: Sequence[TypeUseId] | None = None
) -> WirePlan:
    """Build normalized offline schemas and parameter plans for the requested schema-bearing uses."""
    planner = _WirePlanner(batch, lease)
    requested = None if uses is None else frozenset(uses)
    schema_ids = tuple(
        (binding.id, planner.root(binding.schema))
        for binding in batch.type_uses
        if binding.schema is not None and (requested is None or binding.id in requested)
    )
    parameters = tuple((operation.id, _parameters(planner, operation)) for operation in batch.operations)
    return WirePlan(planner.resources(), schema_ids, parameters, tuple(planner.diagnostics))


def _planned(
    planner: _WirePlanner, operation: OperationId, declaration: WireDeclaration, names: list[tuple[object, str]]
) -> ParameterPlan | None:
    try:
        return _parameter(planner, declaration, names)
    except _PlanError as error:
        planner.diagnostics.append(replace(error.diagnostic, operation=operation))
    return None


def _requirement_names(value: FrozenLiteral | None) -> list[str]:
    match value:
        case LiteralSequence(items=items):
            return [
                str(key.value)
                for item in items
                if isinstance(item, LiteralMapping)
                for key, _ in item.entries
                if isinstance(key, LiteralScalar)
            ]
        case _:
            return []


def _auth_names(planner: _WirePlanner, operation: OperationContract) -> list[tuple[object, str]]:
    schemes = {scheme.name: scheme for scheme in planner.batch.security_schemes}
    requirements = next((value for name, value in operation.facts if name == "security"), None)
    return list(
        dict.fromkeys(
            (_fact(scheme, "in"), str(_fact(scheme, "name")))
            for name in _requirement_names(requirements)
            if (scheme := schemes.get(name)) is not None and _fact(scheme, "type") == "apiKey"
        )
    )


def _claimed(plans: list[ParameterPlan], location: str) -> list[str]:
    return [
        name.lower() if location == "header" else name
        for plan in plans
        if plan.location == location
        for name in (
            [field.name for field in plan.fields]
            if plan.shape == "object" and plan.explode and plan.style in {"form", "cookie"}
            else [plan.name]
        )
    ]


def _parameters(planner: _WirePlanner, operation: OperationContract) -> tuple[ParameterPlan, ...]:
    declarations = operation.parameters
    auth = _auth_names(planner, operation)
    names = [*((_fact(item, "in"), item.name or "") for item in declarations), *auth]
    plans = [
        plan
        for declaration in declarations
        if (plan := _planned(planner, operation.id, declaration, names)) is not None
    ]
    for location in ("query", "header", "cookie"):
        claimed = [
            *_claimed(plans, location),
            *{name.lower() if location == "header" else name for kind, name in auth if kind == location},
        ]
        absorbing = [
            plan for plan in plans if plan.location == location and plan.additional is not None and plan.explode
        ]
        if len(set(claimed)) != len(claimed) or len(absorbing) > 1:
            source = next(item.use_site for item in declarations if _fact(item, "in") == location)
            planner.diagnostics.append(
                CodecDiagnostic(
                    "MC_PARAMETER_ENCODING", source, f"Expanded {location} parameter names collide", operation.id
                )
            )
    return tuple(plans)


def _parameter(planner: _WirePlanner, declaration: WireDeclaration, names: list[tuple[object, str]]) -> ParameterPlan:
    source = declaration.use_site
    location = _LOCATIONS[_fact(declaration, "in")]
    name = declaration.name or ""
    required = _fact(declaration, "required") is True
    if location == "querystring" or declaration.children:
        return _content_parameter(planner, declaration, location, name, required=required)
    schema = _schema_location(planner, declaration.schemas, source)
    shape, kind, fields, additional = _shape(planner, schema, form=False)
    style = str(_fact(declaration, "style") or _DEFAULT_STYLES[location])
    if style == "cookie" and not planner.version.startswith("3.2"):
        raise _PlanError(code="MC_PARAMETER_ENCODING", source=source, message="Cookie style requires OpenAPI 3.2")
    explode = _fact(declaration, "explode")
    try:
        return ParameterPlan(
            location=location,
            name=name,
            style=style,
            explode=explode if isinstance(explode, bool) else style in {"form", "cookie"},
            required=required,
            allow_reserved=_fact(declaration, "allowReserved") is True,
            shape=shape,
            kind=kind,
            fields=fields,
            additional=additional,
            reserved_names=tuple(sorted(other for kind_, other in names if kind_ == location and other != name)),
        )
    except ValueError as error:
        raise _PlanError(code="MC_PARAMETER_ENCODING", source=source, message=str(error)) from None


def _content_parameter(
    planner: _WirePlanner, declaration: WireDeclaration, location: ParameterLocation, name: str, *, required: bool
) -> ParameterPlan:
    source = declaration.use_site
    if len(declaration.children) != 1 or declaration.schemas:
        raise _PlanError(
            code="MC_PARAMETER_ENCODING", source=source, message="Parameter content must declare exactly one media type"
        )
    if location == "querystring" and not planner.version.startswith("3.2"):
        raise _PlanError(
            code="MC_PARAMETER_ENCODING", source=source, message="Querystring parameters require OpenAPI 3.2"
        )
    media = normalize_media_type(declaration.children[0].name or "")
    if not builtin_content(location, media):
        raise _PlanError(
            code="MC_PARAMETER_ENCODING",
            source=source,
            message="The parameter content requires an explicit parameter adapter",
        )
    fields: tuple[FieldPlan, ...] = ()
    additional: FieldPlan | None = None
    content = declaration.children[0]
    kind = media_kind(media)
    if kind == "form":
        _, _, fields, additional = _shape(planner, _schema_location(planner, content.schemas, source), form=True)
    elif (
        kind == "text"
        and content.schemas
        and _kinds(planner, _schema_location(planner, content.schemas, source)) != _STRING
    ):
        raise _PlanError(
            code="MC_PARAMETER_ENCODING", source=source, message="Text parameter content requires a string schema"
        )
    return ParameterPlan(
        location=location, name=name, required=required, content_media_type=media, fields=fields, additional=additional
    )


def _schema_location(planner: _WirePlanner, uses: tuple[TypeUseId, ...], source: SourceLocation) -> SourceLocation:
    if (
        schema := next((item.schema for item in planner.batch.type_uses if item.id in uses and item.schema), None)
    ) is None:
        raise _PlanError(
            code="MC_PARAMETER_ENCODING", source=source, message="A style-based parameter requires a schema"
        )
    return schema


def _kinds(planner: _WirePlanner, location: SourceLocation) -> frozenset[str] | None:
    value, location = planner.resolved(location)
    kinds: frozenset[str] | None = None
    match value.get("type"):
        case str() as single:
            kinds = frozenset({single})
        case list() as many:
            kinds = frozenset(str(item) for item in many)
        case _:
            pass
    if kinds is None and isinstance(values := value.get("enum", [value["const"]] if "const" in value else None), list):
        kinds = frozenset(_json_kind(item) for item in values)
    for keyword in ("allOf", "anyOf", "oneOf"):
        branches = value.get(keyword)
        if not isinstance(branches, list) or not branches:
            continue
        found_kinds = [_kinds(planner, _at(location, keyword, index)) for index in range(len(branches))]
        if keyword == "allOf":
            for found in found_kinds:
                kinds = found if kinds is None else kinds if found is None else kinds & found
        elif all(found is not None for found in found_kinds):
            union = frozenset[str]().union(*(found for found in found_kinds if found is not None))
            kinds = union if kinds is None else kinds & union
    return kinds


def _json_kind(value: object) -> str:
    match value:
        case bool():
            return "boolean"
        case int():
            return "integer"
        case float():
            return "number"
        case None:
            return "null"
        case str():
            return "string"
        case _:
            return "object"


def _kind(planner: _WirePlanner, location: SourceLocation) -> LexicalKind:
    kinds = (_kinds(planner, location) or frozenset()) - _NULL
    if kinds == _INTEGER_NUMBER:
        return "number"
    if len(kinds) != 1 or (kind := _LEXICAL_KINDS.get(next(iter(kinds)))) is None:
        raise _PlanError(
            code="MC_PARAMETER_ENCODING", source=location, message="A parameter value needs one unambiguous scalar kind"
        )
    return kind


def _shape(
    planner: _WirePlanner, location: SourceLocation, *, form: bool
) -> tuple[ValueShape, LexicalKind, tuple[FieldPlan, ...], FieldPlan | None]:
    value, location = planner.resolved(location)
    kinds = (_kinds(planner, location) or frozenset()) - _NULL
    if kinds == _ARRAY and not form:
        return "array", _kind(planner, _at(location, "items")), (), None
    if kinds != _OBJECT:
        if form:
            raise _PlanError(
                code="MC_PARAMETER_ENCODING", source=location, message="A URL-encoded value must be an object"
            )
        return "scalar", _kind(planner, location), (), None
    if "patternProperties" in value:
        raise _PlanError(
            code="MC_PARAMETER_ENCODING",
            source=location,
            message="Pattern properties need an explicit parameter adapter",
        )
    properties = value.get("properties")
    fields = tuple(
        _field(planner, _at(location, "properties", name), name, form=form)
        for name in (properties if isinstance(properties, dict) else {})
    )
    return (
        "object",
        "string",
        fields,
        _additional(planner, value.get("additionalProperties", True), location, form=form),
    )


def _additional(planner: _WirePlanner, schema: YamlValue, location: SourceLocation, *, form: bool) -> FieldPlan | None:
    match schema:
        case False:
            return None
        case True:
            return FieldPlan("", "string")
        case dict() if not schema:
            return FieldPlan("", "string")
        case _:
            return _field(planner, _at(location, "additionalProperties"), "", form=form)


def _field(planner: _WirePlanner, location: SourceLocation, name: str, *, form: bool) -> FieldPlan:
    if form and (_kinds(planner, location) or frozenset()) - _NULL == _ARRAY:
        _, resolved = planner.resolved(location)
        return FieldPlan(name, _kind(planner, _at(resolved, "items")), repeated=True)
    return FieldPlan(name, _kind(planner, location))
