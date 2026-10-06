"""Plan offline wire schemas, static pattern checks, and parameter codecs from an accepted batch."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
from math import isfinite
from typing import TYPE_CHECKING, Final, Literal, TypeAlias
from urllib.parse import quote, unquote, urldefrag, urljoin

from datamodel_code_generator._generation_contract import BindingCaptureError
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
    DirectionalView,
    SchemaPatch,
    SchemaResource,
)
from datamodel_code_generator._runtime.model_codecs.wire import JSONValue, WireValue, escape_pointer_token, freeze_wire
from datamodel_code_generator._target_contract import (
    BindingReason,
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

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Iterator, Sequence

    from datamodel_code_generator._openapi_generation import SourceLease
    from datamodel_code_generator._runtime.model_codecs.context import Direction
    from datamodel_code_generator._source import YamlValue
    from datamodel_code_generator._target_contract import FieldUseBinding, FrozenLiteral, TypeUseBinding

CodecReason: TypeAlias = (
    BindingReason
    | Literal[
        "MC_ALIAS_COLLISION",
        "MC_BINDING_MISSING",
        "MC_CODEC_UNSUPPORTED",
        "MC_PARAMETER_ENCODING",
        "MC_PATTERN_DIALECT",
        "MC_PATTERN_RESOURCE_LIMIT",
        "MC_SCHEMA_DIALECT",
    ]
)

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
_FLAGS: Final[dict[Direction, str]] = {"request": "readOnly", "response": "writeOnly"}
_BRANCH_ARRAYS: Final = ("anyOf", "oneOf")
_BRANCH_VALUES: Final = ("if", "then", "else", "not")
_GUARDED: Final = frozenset({"if", "not"})
_CHILD_VALUES: Final = ("items", "additionalProperties", "contains", "unevaluatedItems", "unevaluatedProperties")
_LEXICAL_KINDS: Final[dict[str, LexicalKind]] = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
}


@dataclass(frozen=True, slots=True)
class CodecDiagnostic:
    """Report one generation-time wire rule that requires a source fix."""

    code: CodecReason
    source: SourceLocation
    message: str
    operation: OperationId | None = None
    uses: tuple[TypeUseId, ...] = ()


@dataclass(frozen=True, slots=True)
class WirePlan:
    """Keep bundled normalized schema resources, per-use schema IDs, and parameter plans."""

    resources: tuple[SchemaResource, ...]
    schema_ids: tuple[tuple[TypeUseId, str], ...]
    parameters: tuple[tuple[OperationId, tuple[ParameterPlan, ...]], ...]
    diagnostics: tuple[CodecDiagnostic, ...]
    views: tuple[DirectionalView, ...] = ()
    documents: tuple[tuple[SourceDocumentId, str], ...] = ()
    version: str = ""
    headers: tuple[tuple[TypeUseId, ParameterPlan], ...] = ()
    forms: tuple[tuple[TypeUseId, tuple[FieldPlan, ...], FieldPlan | None, tuple[ParameterPlan, ...]], ...] = ()
    styles: tuple[tuple[TypeUseId, tuple[ParameterPlan, ...]], ...] = ()

    def schema_id(self, location: SourceLocation) -> str:
        """Return the bundled schema identifier of a planned source location."""
        return f"{dict(self.documents)[location.document]}#{quote(location.pointer, safe=_FRAGMENT_SAFE)}"

    def resolved(self, locations: Iterable[SourceLocation]) -> dict[SourceLocation, SourceLocation]:
        """Map each location whose schema the bundle holds to that schema's location, past whole-schema references.

        A location the normalized bundle lacks, such as a legacy reference's dropped sibling, maps to nothing.
        """
        return {
            location: target
            for location in locations
            if isinstance(self._node(target := self.schema(location)[0]), Mapping | bool)
        }

    def schema(self, location: SourceLocation) -> tuple[SourceLocation, Mapping[str, WireValue]]:
        """Return the normalized schema object at a location, following whole-schema references."""
        value = self._node(location)
        schema: Mapping[str, WireValue] = value if isinstance(value, Mapping) else {}
        uri, fragment = urldefrag(str(schema.get("$ref", "")))
        target = next((document for document, logical in self.documents if logical == uri), None)
        if target is None or len(schema) != 1:
            return location, schema
        return self.schema(SourceLocation(target, unquote(fragment), "schema"))

    def _node(self, location: SourceLocation) -> WireValue:
        """Return the bundled value at a location, or None when the bundle holds nothing there."""
        documents = dict(self.documents)
        value: WireValue = next(
            (resource.contents for resource in self.resources if resource.uri == documents.get(location.document)), None
        )
        for token in _tokens(location.pointer):
            value = (
                value.get(token)
                if isinstance(value, Mapping)
                else value[int(token)]
                if isinstance(value, tuple) and token.isdigit() and int(token) < len(value)
                else None
            )
        return value


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


def _logical(
    batch: GeneratedTypeContractBatch, pointers: Mapping[SourceDocumentId, str]
) -> dict[SourceDocumentId, str]:
    logical: dict[SourceDocumentId, str] = {}
    seen: dict[str, int] = {}
    for document in batch.documents:
        base = f"{LOGICAL_ROOT}{pointers[document.id].removeprefix('/inputs/')}"
        count = seen[base] = seen.get(base, -1) + 1
        logical[document.id] = base if not count else f"{base}/{count}"
    return logical


class _WirePlanner:
    def __init__(
        self,
        batch: GeneratedTypeContractBatch,
        lease: SourceLease,
        pointers: Mapping[SourceDocumentId, str],
    ) -> None:
        self.batch = batch
        self.lease = lease
        self.logical = _logical(batch, pointers)
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
        root = batch.documents[0].id
        specification = lease.borrow(SourceLocation(root, "", "schema"))
        settings = specification if isinstance(specification, dict) else {}
        self.version = str(settings.get("openapi", ""))
        self.legacy = self.version.startswith("3.0")
        if isinstance(dialect := settings.get("jsonSchemaDialect"), str) and not _supported_dialect(dialect):
            self.report(
                "MC_SCHEMA_DIALECT",
                SourceLocation(root, "/jsonSchemaDialect", "schema"),
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
                "Embedded schema resources are not supported",
            )
        if "$anchor" in value and location.document in self.containers:
            self.report(
                "MC_SCHEMA_DIALECT",
                _at(location, "$anchor"),
                "Schema anchors in OpenAPI documents are not supported",
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
                self.report("MC_SCHEMA_DIALECT", at, f"Keyword {key} is not supported")
            case "items", list():
                self.report("MC_SCHEMA_DIALECT", at, "Array-form items is not supported")
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


class _DirectionalPlanner:
    """Relax required members that one direction excludes, following references and allOf members."""

    def __init__(self, planner: _WirePlanner, direction: Direction) -> None:
        self.planner = planner
        self.flag = _FLAGS[direction]
        self.documents = {uri: document for document, uri in planner.logical.items()}
        self.decisions: dict[tuple[SourceLocation, str], tuple[JSONValue, JSONValue]] = {}
        self.visited: set[tuple[frozenset[SourceLocation], frozenset[tuple[str, SourceLocation]]]] = set()
        self.guarded: set[SourceLocation] = set()

    def schema(self, location: SourceLocation) -> dict[str, JSONValue]:
        roots = self.planner.roots.get(location.document, {})
        prefix = _enclosing(roots, location.pointer)
        value = None if prefix is None else roots[prefix]
        for token in _tokens(location.pointer[len(prefix or "") :]):
            value = (
                value.get(token)
                if isinstance(value, dict)
                else value[int(token)]
                if isinstance(value, list) and token.isdigit() and int(token) < len(value)
                else None
            )
        return value if isinstance(value, dict) else {}

    def closure(self, locations: Iterable[SourceLocation]) -> tuple[SourceLocation, ...]:
        group: dict[SourceLocation, None] = {}
        pending = list(locations)
        while pending:
            if (location := pending.pop()) in group or not (schema := self.schema(location)):
                continue
            group[location] = None
            if (target := self.target(schema)) is not None:
                pending.append(target)
            if isinstance(members := schema.get("allOf"), list):
                pending.extend(_at(location, "allOf", index) for index in range(len(members)))
        return tuple(group)

    def target(self, schema: Mapping[str, JSONValue]) -> SourceLocation | None:
        if not isinstance(reference := schema.get("$ref"), str):
            return None
        uri, fragment = urldefrag(reference)
        return (
            None
            if (document := self.documents.get(uri)) is None
            else SourceLocation(document, unquote(fragment), "schema")
        )

    def reaches_flag(self, root: SourceLocation) -> bool:
        seen: set[SourceLocation] = set()
        pending = [root]
        while pending:
            if (location := pending.pop()) in seen or not (schema := self.schema(location)):
                continue
            seen.add(location)
            if schema.get(self.flag) is True:
                return True
            if (target := self.target(schema)) is not None:
                pending.append(target)
            for keyword, value in schema.items():
                match value:
                    case dict() if keyword in SCHEMA_MAP_KEYWORDS:
                        pending.extend(_at(location, keyword, name) for name in value)
                    case list() if keyword in SCHEMA_ARRAY_KEYWORDS:
                        pending.extend(_at(location, keyword, index) for index in range(len(value)))
                    case _ if keyword in SCHEMA_VALUE_KEYWORDS:
                        pending.append(_at(location, keyword))
                    case _:
                        continue
        return False

    def branches(self, location: SourceLocation) -> list[SourceLocation]:
        schema = self.schema(location)
        branches = [
            _at(location, keyword, index)
            for keyword in _BRANCH_ARRAYS
            if isinstance(members := schema.get(keyword), list)
            for index in range(len(members))
        ]
        branches.extend(_at(location, keyword) for keyword in _BRANCH_VALUES if keyword in schema)
        if isinstance(dependent := schema.get("dependentSchemas"), dict):
            branches.extend(_at(location, "dependentSchemas", name) for name in dependent)
        return branches

    def children(self, location: SourceLocation) -> list[SourceLocation]:
        schema = self.schema(location)
        children = [_at(location, keyword) for keyword in _CHILD_VALUES if keyword in schema]
        if isinstance(items := schema.get("prefixItems"), list):
            children.extend(_at(location, "prefixItems", index) for index in range(len(items)))
        if isinstance(patterns := schema.get("patternProperties"), dict):
            children.extend(_at(location, "patternProperties", pattern) for pattern in patterns)
        return children

    def flagged(self, group: Iterable[SourceLocation]) -> bool:
        return any(self.schema(location).get(self.flag) is True for location in group)

    def conditional(self, group: tuple[SourceLocation, ...], seen: set[SourceLocation]) -> bool:
        for branch in (branch for location in group for branch in self.branches(location)):
            if branch in seen:
                continue
            seen.add(branch)
            if self.flagged(inner := self.closure((branch,))) or self.conditional(inner, seen):
                return True
        return False

    def excluded(self, declarations: tuple[SourceLocation, ...], source: SourceLocation) -> bool:
        group = self.closure(declarations)
        if all(any(self.schema(at).get(flag) is True for at in group) for flag in _FLAGS.values()):
            self.planner.report(
                "MC_SCHEMA_DIALECT", declarations[0], "A property cannot be both read-only and write-only"
            )
        if self.flagged(group):
            return True
        if self.conditional(group, set()):
            self.planner.report(
                "MC_SCHEMA_DIALECT", source, "A required property's readOnly or writeOnly annotation is conditional"
            )
        return False

    def kept(
        self, names: list[JSONValue], declared: Mapping[str, tuple[SourceLocation, ...]], source: SourceLocation
    ) -> list[JSONValue]:
        return [name for name in names if not self.excluded(declared.get(str(name), ()), source)]

    def decide(self, location: SourceLocation, keyword: str, original: JSONValue, decided: JSONValue) -> None:
        if self.decisions.setdefault((location, keyword), (original, decided))[1] != decided:
            self.planner.report(
                "MC_SCHEMA_DIALECT",
                _at(location, keyword),
                "The directional required members of a shared schema depend on how it is referenced",
            )

    def visit(self, locations: Iterable[SourceLocation], inherited: Mapping[str, tuple[SourceLocation, ...]]) -> None:
        group = self.closure(locations)
        own: dict[str, tuple[SourceLocation, ...]] = {}
        for location in group:
            if isinstance(properties := self.schema(location).get("properties"), dict):
                for name in properties:
                    own[name] = (*own.get(name, ()), _at(location, "properties", name))
        declared = {name: (*inherited.get(name, ()), *own.get(name, ())) for name in {*inherited, *own}}
        key = (frozenset(group), frozenset((name, at) for name, ats in declared.items() for at in ats))
        if key in self.visited:
            return
        self.visited.add(key)
        for location in group:
            schema = self.schema(location)
            if isinstance(required := schema.get("required"), list):
                self.decide(location, "required", required, self.kept(required, declared, _at(location, "required")))
            if isinstance(dependent := schema.get("dependentRequired"), dict):
                source = _at(location, "dependentRequired")
                self.decide(
                    location,
                    "dependentRequired",
                    dependent,
                    {
                        name: self.kept(names, declared, source) if isinstance(names, list) else names
                        for name, names in dependent.items()
                    },
                )
        for name in own:
            self.visit(declared[name], {})
        for location in group:
            self.descend(location, declared)

    def descend(self, location: SourceLocation, declared: Mapping[str, tuple[SourceLocation, ...]]) -> None:
        for child in self.children(location):
            self.visit((child,), {})
        for branch in self.branches(location):
            if branch.pointer.rpartition("/")[2] in _GUARDED:
                self.guard((branch,))
            else:
                self.visit((branch,), declared)

    def guard(self, locations: Iterable[SourceLocation]) -> None:
        for location in self.closure(locations):
            if location in self.guarded:
                continue
            self.guarded.add(location)
            properties = self.schema(location).get("properties")
            self.guard((
                *(_at(location, "properties", name) for name in (properties if isinstance(properties, dict) else {})),
                *self.children(location),
                *self.branches(location),
            ))

    def view(self, direction: Direction, flagged: Iterable[str]) -> DirectionalView:
        for (location, keyword), (original, decided) in self.decisions.items():
            if original != decided and location in self.guarded:
                self.planner.report(
                    "MC_SCHEMA_DIALECT",
                    _at(location, keyword),
                    "The directional required members of a shared schema depend on how it is referenced",
                )
        return DirectionalView(
            direction=direction,
            flagged=tuple(sorted(set(flagged))),
            patches=tuple(
                SchemaPatch(
                    uri=self.planner.logical[location.document],
                    pointer=location.pointer,
                    keyword="required" if keyword == "required" else "dependentRequired",
                    value=freeze_wire(decided),
                )
                for (location, keyword), (original, decided) in self.decisions.items()
                if original != decided
            ),
        )


def _legacy_keywords(raw: Mapping[str, YamlValue], normalized: dict[str, JSONValue]) -> None:
    if raw.get("nullable") is True and isinstance(kind := raw.get("type"), str):
        normalized["type"] = [kind, "null"]
    for exclusive, inclusive in (("exclusiveMinimum", "minimum"), ("exclusiveMaximum", "maximum")):
        if raw.get(exclusive) is True and inclusive in normalized:
            normalized[exclusive] = normalized.pop(inclusive)


def operation_uses(operation: OperationContract) -> tuple[TypeUseId, ...]:
    """Return the type uses of an operation's parameters, responses, and body, without encoding headers."""
    pending = [*operation.parameters, *operation.responses]
    if operation.request_body is not None:
        pending.append(operation.request_body)
    found: list[TypeUseId] = []
    while pending:
        declaration = pending.pop(0)
        found.extend(declaration.schemas)
        pending.extend(child for child in declaration.children if child.kind != "encoding")
    return tuple(found)


def plan_wire(  # noqa: PLR0913
    batch: GeneratedTypeContractBatch,
    lease: SourceLease,
    uses: Sequence[TypeUseId] | None = None,
    *,
    operations: Collection[OperationId] | None = None,
    documents: Mapping[SourceDocumentId, str],
    forms: Mapping[TypeUseId, tuple[WireDeclaration, ...]] | None = None,
    styles: Mapping[TypeUseId, tuple[WireDeclaration, ...]] | None = None,
) -> WirePlan:
    """Build normalized offline schemas and parameter plans for the requested uses and operations.

    The document pointers of the target manifest, such as `/inputs/documents/<index>`, name the bundled resources,
    so schema references match the manifest. The uses of URL-encoded bodies named in `forms`
    get their member plans, and each member their encoding names the plan of a query parameter; the form-data uses
    named in `styles` get the query parameter plan of each member their encodings give a style.
    """
    forms = forms or {}
    styles = styles or {}
    planner = _WirePlanner(batch, lease, documents)
    requested = None if uses is None else frozenset(uses)
    schema_ids = tuple(
        (binding.id, planner.root(binding.schema))
        for binding in batch.type_uses
        if binding.schema is not None and (requested is None or binding.id in requested)
    )
    planned = [operation for operation in batch.operations if operations is None or operation.id in operations]
    parameters = tuple((operation.id, _parameters(planner, operation)) for operation in planned)
    headers = tuple(
        (use, plan)
        for operation in planned
        for header in _headers(operation, requested)
        for use, plan in _header(planner, operation.id, header)
    )
    planned_forms = tuple(
        form for use in batch.type_uses if use.id in forms and (form := _form(planner, use, forms[use.id])) is not None
    )
    planned_styles = tuple(
        style
        for use in batch.type_uses
        if use.id in styles and (style := _styles(planner, use, styles[use.id])) is not None
    )
    views: list[DirectionalView] = []
    for direction in _FLAGS:
        directional = _DirectionalPlanner(planner, direction)
        roots = [
            binding.schema
            for binding in batch.type_uses
            if binding.id.direction == direction
            and binding.schema is not None
            and (requested is None or binding.id in requested)
        ]
        for root in roots:
            directional.visit((root,), {})
        views.append(
            directional.view(direction, (planner.schema_id(root) for root in roots if directional.reaches_flag(root)))
        )
    return WirePlan(
        planner.resources(),
        schema_ids,
        parameters,
        tuple(planner.diagnostics),
        tuple(views),
        tuple(sorted(planner.logical.items())),
        planner.version,
        headers,
        planned_forms,
        planned_styles,
    )


def _form(
    planner: _WirePlanner, use: TypeUseBinding, encodings: tuple[WireDeclaration, ...]
) -> tuple[TypeUseId, tuple[FieldPlan, ...], FieldPlan | None, tuple[ParameterPlan, ...]] | None:
    """Return the member plans of a URL-encoded use: each member's field, or the parameter plan its encoding gives."""
    try:
        location = _schema_location(planner, (use.id,), use.id.use_site)
        styled = {declaration.name or "": declaration for declaration in encodings if _styled(declaration)}
        _, _, fields, additional = _shape(planner, location, form=True, skip=frozenset(styled))
        members = {name: schema for name, schema, _ in property_members(use)}
        encoded = tuple(_encoding(planner, members, declaration) for declaration in styled.values())
        _distinct(location, [field.name for field in fields], encoded)
    except _PlanError as error:
        _refused(planner, use, error)
        return None
    return use.id, fields, additional, encoded


def _styles(
    planner: _WirePlanner, use: TypeUseBinding, encodings: tuple[WireDeclaration, ...]
) -> tuple[TypeUseId, tuple[ParameterPlan, ...]] | None:
    """Return the query parameter plans of the form-data members whose encodings name a style or its options."""
    try:
        location = _schema_location(planner, (use.id,), use.id.use_site)
        members = {name: schema for name, schema, _ in property_members(use)}
        encoded = tuple(_encoding(planner, members, declaration) for declaration in encodings)
        styled = {plan.name for plan in encoded}
        _distinct(location, [name for name in members if name not in styled], encoded)
    except _PlanError as error:
        _refused(planner, use, error)
        return None
    return use.id, encoded


def property_members(use: TypeUseBinding) -> Iterator[tuple[str, SourceLocation, FieldUseBinding]]:
    """Yield each property a use's model declares, its allOf branches' included, with its wire name and schema."""
    for member in use.members:
        if member.member_kind == "property" and member.wire_name is not None and member.schema is not None:
            yield member.wire_name, member.schema, member


def _refused(planner: _WirePlanner, use: TypeUseBinding, error: _PlanError) -> None:
    owner = use.id.owner
    planner.diagnostics.append(
        replace(error.diagnostic, operation=owner if isinstance(owner, OperationId) else None, uses=(use.id,))
    )


def _distinct(location: SourceLocation, names: list[str], encoded: tuple[ParameterPlan, ...]) -> None:
    """Refuse a form whose members, an exploded member's own included, write a name twice."""
    claimed = [
        *(item.name for plan in encoded if _spread(plan) for item in plan.fields),
        *names,
        *(plan.name for plan in encoded if not _spread(plan)),
    ]
    if len(set(claimed)) != len(claimed):
        raise _PlanError(code="MC_PARAMETER_ENCODING", source=location, message="Expanded form member names collide")


def _styled(encoding: WireDeclaration) -> bool:
    """Return whether an encoding gives its member a query parameter's style or content."""
    return any(_fact(encoding, key) is not None for key in ("style", "explode", "allowReserved", "contentType"))


def _encoding(planner: _WirePlanner, members: Mapping[str, SourceLocation], encoding: WireDeclaration) -> ParameterPlan:
    """Return the query parameter plan of a form member: its style, or its content when only that is named.

    A style, explode, or allowReserved takes precedence over contentType, as the Encoding Object prescribes.
    """
    name, source = encoding.name or "", encoding.use_site
    if (member := members.get(name)) is None:
        raise _PlanError(code="MC_PARAMETER_ENCODING", source=source, message="An encoding names no member of its form")
    style, explode, reserved, content = (
        _fact(encoding, key) for key in ("style", "explode", "allowReserved", "contentType")
    )
    try:
        if style is None and explode is None and reserved is None:
            media = normalize_media_type(str(content))
            if not builtin_content("query", media):
                raise _PlanError(
                    code="MC_PARAMETER_ENCODING",
                    source=source,
                    message="The member content has no builtin encoding",
                )
            return ParameterPlan(location="query", name=name, content_media_type=media)
        shape, kind, fields, additional = _shape(planner, member, form=False)
        chosen = str(style or "form")
        return ParameterPlan(
            location="query",
            name=name,
            style=chosen,
            explode=explode if isinstance(explode, bool) else chosen == "form",
            allow_reserved=reserved is True,
            shape=shape,
            kind=kind,
            fields=fields,
            additional=additional,
            reserved_names=tuple(sorted(other for other in members if other != name)),
        )
    except ValueError as error:
        raise _PlanError(code="MC_PARAMETER_ENCODING", source=source, message=str(error)) from None


def parameter_plans(wire: WirePlan) -> dict[OperationId, dict[tuple[ParameterLocation, str], ParameterPlan]]:
    """Index each operation's parameter plans by location and name."""
    return {operation: {(plan.location, plan.name): plan for plan in planned} for operation, planned in wire.parameters}


def _allow_reserved(version: str, location: ParameterLocation, declaration: WireDeclaration) -> bool:
    """Return the effective allowReserved flag for the root OpenAPI version and parameter location."""
    return _fact(declaration, "allowReserved") is True and not (
        location == "path" and (version in ("3.0", "3.1") or version.startswith(("3.0.", "3.1.")))  # noqa: PLR6201
    )


def _planned(
    planner: _WirePlanner, operation: OperationId, declaration: WireDeclaration, names: list[tuple[object, str]]
) -> ParameterPlan | None:
    try:
        return _parameter(planner, declaration, names)
    except _PlanError as error:
        planner.diagnostics.append(
            replace(
                error.diagnostic,
                operation=operation,
                uses=_uses(declaration),
            )
        )
    return None


def _headers(operation: OperationContract, requested: frozenset[TypeUseId] | None) -> Iterator[WireDeclaration]:
    """Yield the headers of an operation's responses, then the requested ones its request body's encodings declare."""
    for response in operation.responses:
        yield from (child for child in response.children if child.kind == "header")
    for media in () if operation.request_body is None else operation.request_body.children:
        for encoding in (child for child in media.children if child.kind == "encoding"):
            yield from (
                child
                for child in encoding.children
                if child.kind == "header" and (requested is None or not requested.isdisjoint(_uses(child)))
            )


def _uses(declaration: WireDeclaration) -> tuple[TypeUseId, ...]:
    return (*declaration.schemas, *(use for child in declaration.children for use in child.schemas))


def _header(
    planner: _WirePlanner, operation: OperationId, declaration: WireDeclaration
) -> tuple[tuple[TypeUseId, ParameterPlan], ...]:
    if not (uses := _uses(declaration)):
        return ()
    facts = (*declaration.facts, ("in", LiteralScalar("str", "header")), ("style", LiteralScalar("str", "simple")))
    plan = _planned(planner, operation, replace(declaration, facts=facts), [])
    return () if plan is None else ((uses[0], plan),)


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


def auth_names(batch: GeneratedTypeContractBatch, operation: OperationContract) -> list[tuple[object, str]]:
    """Return the locations and names of the apiKey credentials the operation's security requirements send."""
    document = (
        operation.declaration.location.document if operation.security_declared else operation.id.use_site.document
    )
    schemes = {scheme.name: scheme for scheme in batch.security_schemes if scheme.use_site.document == document}
    requirements = next((value for name, value in operation.facts if name == "security"), None)
    return list(
        dict.fromkeys(
            (_fact(scheme, "in"), str(_fact(scheme, "name")))
            for name in _requirement_names(requirements)
            if (scheme := schemes.get(name)) is not None and _fact(scheme, "type") == "apiKey"
        )
    )


def _spread(plan: ParameterPlan) -> bool:
    return plan.shape == "object" and plan.explode and plan.style in {"form", "cookie"}


def _claimed(plans: Sequence[ParameterPlan], location: str) -> list[str]:
    return [
        name.lower() if location == "header" else name
        for plan in plans
        if plan.location == location
        for name in ([field.name for field in plan.fields] if _spread(plan) else [plan.name])
    ]


def _reserving(plan: ParameterPlan, plans: list[ParameterPlan]) -> ParameterPlan:
    owned = {
        field.name
        for other in plans
        if other is not plan and other.location == plan.location and _spread(other)
        for field in other.fields
    } - set(_claimed([plan], plan.location))
    return replace(plan, reserved_names=tuple(sorted({*plan.reserved_names, *owned}))) if owned else plan


def _parameters(planner: _WirePlanner, operation: OperationContract) -> tuple[ParameterPlan, ...]:
    declarations = operation.parameters
    auth = auth_names(planner.batch, operation)
    names = [*((_fact(item, "in"), item.name or "") for item in declarations), *auth]
    plans = [
        plan
        for declaration in declarations
        if (plan := _planned(planner, operation.id, declaration, names)) is not None
    ]
    planner.diagnostics.extend(parameter_collisions(operation, plans, auth))
    return tuple(_reserving(plan, plans) for plan in plans)


def parameter_collisions(
    operation: OperationContract, plans: Sequence[ParameterPlan], auth: Sequence[tuple[object, str]]
) -> tuple[CodecDiagnostic, ...]:
    """Report each location whose expanded parameter names or the `auth` apiKey names collide."""
    found: list[CodecDiagnostic] = []
    for location in ("query", "header", "cookie"):
        claimed = [
            *_claimed(plans, location),
            *{name.lower() if location == "header" else name for kind, name in auth if kind == location},
        ]
        absorbing = [
            plan for plan in plans if plan.location == location and plan.additional is not None and _spread(plan)
        ]
        if (
            len(set(claimed)) != len(claimed)
            or len(absorbing) > 1
            or (absorbing and any(plan.location == location and plan.style == "deepObject" for plan in plans))
        ):
            source = next(item.use_site for item in operation.parameters if _fact(item, "in") == location)
            found.append(
                CodecDiagnostic(
                    "MC_PARAMETER_ENCODING", source, f"Expanded {location} parameter names collide", operation.id
                )
            )
    return tuple(found)


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
            allow_reserved=_allow_reserved(planner.version, location, declaration),
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
    if location == "querystring" and not planner.version.startswith("3.2"):
        raise _PlanError(
            code="MC_PARAMETER_ENCODING", source=source, message="Querystring parameters require OpenAPI 3.2"
        )
    media = normalize_media_type(declaration.children[0].name or "")
    if not builtin_content(location, media):
        raise _PlanError(
            code="MC_PARAMETER_ENCODING",
            source=source,
            message="The parameter content has no builtin encoding",
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
    planner: _WirePlanner, location: SourceLocation, *, form: bool, skip: frozenset[str] = frozenset()
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
            message="Pattern properties have no builtin parameter encoding",
        )
    properties = value.get("properties")
    fields = tuple(
        _field(planner, _at(location, "properties", name), name, form=form)
        for name in (properties if isinstance(properties, dict) else {})
        if name not in skip
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
