"""Plan the OpenAPI fragments a FastAPI server adds to its served document, and the components they reference."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from functools import cached_property
from math import isfinite
from typing import TYPE_CHECKING, Final, Literal, TypeAlias
from urllib.parse import unquote, urldefrag

from typing_extensions import TypeIs

from datamodel_code_generator._api_manifest import canonical_bytes, sha256
from datamodel_code_generator._api_types import APIGenerationError, Diagnostic
from datamodel_code_generator._fastapi.callbacks import CallbackNode, flattened
from datamodel_code_generator._fastapi.naming import normalize
from datamodel_code_generator._generation_contract import (
    GeneratedSymbolType,
    LiteralMapping,
    LiteralScalar,
    LiteralSequence,
)
from datamodel_code_generator._runtime.model_codecs.schema import (
    SCHEMA_ARRAY_KEYWORDS,
    SCHEMA_MAP_KEYWORDS,
    SCHEMA_VALUE_KEYWORDS,
)
from datamodel_code_generator._runtime.model_codecs.wire import escape_pointer_token, pointer_tokens

if TYPE_CHECKING:
    from collections.abc import Iterable

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._fastapi.plan import OperationSpec, SecuritySpec, ServerPlan
    from datamodel_code_generator._generation_contract import (
        FrozenLiteral,
        TypeUseId,
        WireDeclaration,
    )
    from datamodel_code_generator._openapi_wire_plan import WirePlan
    from datamodel_code_generator._runtime.model_codecs.wire import JSONValue, WireValue

Version: TypeAlias = Literal["3.1.0", "3.2.1"]
ComponentDirection: TypeAlias = Literal["request", "response", "framework"]
JSONObject: TypeAlias = "dict[str, JSONValue]"

_COMPONENT: Final = re.compile(r"[A-Za-z0-9._-]+")
_SCHEMAS: Final = "#/components/schemas/"
_DEFINITIONS: Final = frozenset({"$defs", "definitions"})
_PARAMETER_KEYS: Final = (
    "name",
    "in",
    "description",
    "required",
    "deprecated",
    "allowEmptyValue",
    "style",
    "explode",
    "allowReserved",
)
_HEADER_KEYS: Final = ("description", "required", "deprecated", "style", "explode")
_EXAMPLE_KEYS: Final = ("example", "examples")
_ENCODING_KEYS: Final = ("contentType", "style", "explode", "allowReserved")
_LINK_KEYS: Final = ("operationRef", "operationId", "parameters", "requestBody", "description", "server")
_SCHEME_KEYS: Final = ("type", "description", "name", "in", "scheme", "bearerFormat", "flows", "openIdConnectUrl")
_CALLBACK_KEYS: Final = ("tags", "summary", "description", "externalDocs", "operationId", "deprecated")
_RAW_NOTE: Final = "The server passes this body to its handler as the request, without validating it."
_EMPTY_NOTE: Final = "The response may also have no content."
_JSON_MEDIA: Final = "application/json"
_HTTP_ERROR: Final[JSONObject] = {"type": "object", "properties": {"detail": {}}, "required": ["detail"]}
_VALIDATION_ERROR: Final[JSONObject] = {
    "type": "object",
    "properties": {
        "detail": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "loc": {"type": "array", "items": {"anyOf": [{"type": "string"}, {"type": "integer"}]}},
                    "msg": {"type": "string"},
                    "type": {"type": "string"},
                },
                "required": ["loc", "msg", "type"],
            },
        }
    },
    "required": ["detail"],
}
_SERVER_ERROR: Final[JSONObject] = {"type": "string"}
_ERRORS: Final[dict[str, tuple[str, str, str | None]]] = {
    "400": ("Invalid request", "http_error", "Invalid request"),
    "401": ("Unauthorized", "http_error", "Unauthorized"),
    "403": ("Forbidden", "http_error", None),
    "415": ("Unsupported media type", "http_error", "Unsupported media type"),
    "422": ("Validation Error", "validation_error", None),
    "500": ("Internal Server Error", "server_error", None),
}
_FRAMEWORK: Final[dict[str, JSONObject]] = {
    "http_error": _HTTP_ERROR,
    "validation_error": _VALIDATION_ERROR,
    "server_error": _SERVER_ERROR,
}


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationDocs:
    """One operation's served-document fragment, with the method, route path, and slots it registers under."""

    key: str
    method: str
    route_path: str
    slots: tuple[tuple[str, str], ...]
    fragment: JSONObject


@dataclass(frozen=True, slots=True, kw_only=True)
class Component:
    """One schema component the fragments reference: its name, source schema and direction, and schema."""

    name: str
    schema_id: str | None
    direction: ComponentDirection
    schema: JSONValue


@dataclass(frozen=True, slots=True, kw_only=True)
class SchemeComponent:
    """One security scheme component: its generated name, the source scheme it documents, and its object."""

    name: str
    scheme_name: str
    scheme: JSONObject


@dataclass(frozen=True, slots=True, kw_only=True)
class ServedDocs:
    """Everything a package adds to its served OpenAPI document, and the annotations it could not keep."""

    package: str
    version: Version
    repeated: bool
    operations: tuple[OperationDocs, ...]
    components: tuple[Component, ...]
    schemes: tuple[SchemeComponent, ...]
    callbacks: tuple[CallbackNode, ...]
    diagnostics: tuple[Diagnostic, ...]

    def bundle(self) -> JSONObject:
        """Return the JSON the package plan carries: every operation's fragment and the components."""
        return {
            "operations": [
                {
                    "key": operation.key,
                    "method": operation.method,
                    "route_path": operation.route_path,
                    "slots": [list(slot) for slot in operation.slots],
                    "fragment": operation.fragment,
                }
                for operation in self.operations
            ],
            "components": self.sections(),
        }

    def document(self, info: JSONValue) -> JSONObject:
        """Return what the manifest fingerprints: the served version, the default info, and the bundle."""
        return {"version": self.version, "info": info, **self.bundle()}

    def sections(self) -> JSONObject:
        """Return the component sections the package adds: its schemas and its security schemes."""
        sections: JSONObject = {}
        if self.components:
            sections["schemas"] = {component.name: component.schema for component in self.components}
        if self.schemes:
            sections["securitySchemes"] = {scheme.name: scheme.scheme for scheme in self.schemes}
        return sections


class _NotJSONError(Exception):
    """A documentation value that has no JSON form."""


class DocsBuilder:  # noqa: PLR0904
    """Build each operation's fragment from its declarations and normalized schemas, collecting components."""

    def __init__(  # noqa: PLR0913
        self,
        plan: ServerPlan,
        request: TargetRequest,
        *,
        package: str,
        wires: tuple[WirePlan, ...],
        callbacks: Mapping[str, tuple[CallbackNode, ...]],
    ) -> None:
        """Index the normalized schema resources, schema identities, and directional patches of the wire plans."""
        self.plan = plan
        self.request = request
        self.package = package
        self.suffix = sha256(package.encode())[:12]
        self.resources: dict[str, list[WireValue]] = {}
        self.ids: dict[TypeUseId, str] = {}
        self.patches: dict[tuple[str, str, str], dict[str, WireValue]] = {}
        for wire in wires:
            for resource in wire.resources:
                self.resources.setdefault(resource.uri, []).append(resource.contents)
            self.ids.update(wire.schema_ids)
            for view in wire.views:
                for patch in view.patches:
                    self.patches.setdefault((view.direction, patch.uri, patch.pointer), {})[patch.keyword] = patch.value
        self.wire = wires[0]
        self.components: dict[str, Component | None] = {}
        self.identities: dict[tuple[str, str], str] = {}
        self.pending: list[tuple[str, str, str]] = []
        self.schemes: dict[str, SchemeComponent] = {}
        self.nodes = callbacks
        self.records: list[CallbackNode] = []
        self.problems: list[Diagnostic] = []

    def build(self) -> ServedDocs:
        """Return the fragments of every planned operation and the components they reference."""
        operations = tuple(self.operation(spec) for spec in self.plan.operations)
        while self.pending:
            schema_id, direction, name = self.pending.pop(0)
            self.components[name] = Component(
                name=name,
                schema_id=schema_id,
                direction="response" if direction == "response" else "request",
                schema=self.rewrite(schema_id, direction),
            )
        return ServedDocs(
            package=self.package,
            version="3.2.1" if self.wire.version.startswith("3.2") else "3.1.0",
            repeated=any(
                len(set(spec.route.placeholders)) != len(spec.route.placeholders) for spec in self.plan.operations
            ),
            operations=operations,
            components=tuple(component for component in self.components.values() if component is not None),
            schemes=tuple(self.schemes.values()),
            callbacks=tuple(self.records),
            diagnostics=tuple(self.problems),
        )

    @cached_property
    def symbols(self) -> dict[str, str]:
        """Return the final model symbol name of each schema identity that a type use binds to one."""
        names = {symbol.id: symbol.name for symbol in self.request.batch.symbols}
        return {
            self.ids[binding.id]: names[bound.symbol]
            for binding in self.request.batch.type_uses
            if binding.id in self.ids and isinstance(bound := binding.type, GeneratedSymbolType)
        }

    def operation(self, spec: OperationSpec) -> OperationDocs:
        """Return one operation's fragment: adapter parameters and bodies, responses, security, and callbacks."""
        contract = spec.contract
        fragment: JSONObject = {}
        declarations = {
            (_fact(declaration, "in"), declaration.name): declaration for declaration in contract.parameters
        }
        adapters: list[JSONValue] = [
            self.parameter(declarations[parameter.location, parameter.wire_name])
            for parameter in spec.parameters
            if parameter.native is None
        ]
        if adapters:
            fragment["parameters"] = adapters
        if (body := spec.body) is not None and body.decision.transport != "fastapi_native":
            assert contract.request_body is not None
            fragment["requestBody"] = self.body(contract.request_body, raw=body.decision.transport == "raw_request")
        fragment["responses"] = self.responses(spec)
        fragment["security"] = self.security(spec.security)
        nodes = self.nodes[spec.key]
        self.records.extend(flattened(nodes))
        if callbacks := self.callbacks(nodes):
            fragment["callbacks"] = callbacks
        facts = dict(contract.facts)
        if (documentation := facts.get("externalDocs")) is not None:
            self.put(fragment, "externalDocs", documentation, contract.id.use_site.pointer)
        if (servers := facts.get("servers")) is not None and self.own_servers(servers):
            self.put(fragment, "servers", servers, contract.id.use_site.pointer)
        return OperationDocs(
            key=spec.key,
            method=contract.method,
            route_path=spec.route.route_path,
            slots=tuple((slot.slot, slot.wire_name) for slot in spec.route.slots),
            fragment=fragment,
        )

    def own_servers(self, servers: FrozenLiteral) -> bool:
        """Return whether an operation's servers differ from the document's, which the application info serves."""
        root = dict(self.plan.info).get("servers")
        return root is None or documentation(servers) != _plain(root)

    def parameter(self, declaration: WireDeclaration) -> JSONObject:
        """Return a parameter object: its facts, then its schema or content, then its examples."""
        parameter = self.facts(declaration, _PARAMETER_KEYS)
        self.payload(parameter, declaration)
        return parameter

    def header(self, declaration: WireDeclaration) -> JSONObject:
        """Return a header object: a parameter object without a name or location."""
        header = self.facts(declaration, _HEADER_KEYS)
        self.payload(header, declaration)
        return header

    def payload(self, target: JSONObject, declaration: WireDeclaration) -> None:
        """Add a declaration's schemas, examples, and media content to its object."""
        uses: dict[str, TypeUseId] = {}
        for use in declaration.schemas:
            uses.setdefault(pointer_tokens(use.schema_site.pointer)[-1], use)
        target.update((keyword, self.schema(use)) for keyword, use in uses.items())
        target.update(self.facts(declaration, _EXAMPLE_KEYS))
        if media := [child for child in declaration.children if child.kind == "media"]:
            target["content"] = {str(child.name): self.media(child) for child in media}

    def media(self, declaration: WireDeclaration) -> JSONObject:
        """Return a media type object: its schemas, examples, and effective encodings."""
        media: JSONObject = {}
        self.payload(media, declaration)
        if encodings := [child for child in declaration.children if child.kind == "encoding"]:
            media["encoding"] = {str(child.name): self.facts(child, _ENCODING_KEYS) for child in encodings}
        return media

    def body(self, declaration: WireDeclaration, *, raw: bool) -> JSONObject:
        """Return a request body object; a raw request body says the server does not validate it."""
        body = self.facts(declaration, ("description", "required"))
        if raw:
            description = body.get("description")
            body["description"] = f"{description}\n\n{_RAW_NOTE}" if isinstance(description, str) else _RAW_NOTE
        body["content"] = {
            str(child.name): self.media(child) for child in declaration.children if child.kind == "media"
        }
        return body

    def response(self, declaration: WireDeclaration) -> JSONObject:
        """Return a response object: its description, headers, content, and links."""
        response = self.facts(declaration, ("description",))
        response.setdefault("description", "")
        if headers := [child for child in declaration.children if child.kind == "header"]:
            response["headers"] = {str(child.name): self.header(child) for child in headers}
        if media := [child for child in declaration.children if child.kind == "media"]:
            response["content"] = {str(child.name): self.media(child) for child in media}
        if links := [child for child in declaration.children if child.kind == "link"]:
            response["links"] = {str(child.name): self.facts(child, _LINK_KEYS) for child in links}
        return response

    def responses(self, spec: OperationSpec) -> JSONObject:
        """Return every declared response, the exact response a rangeless route registers, and generated errors."""
        responses: JSONObject = {response.status: self.response(response.declaration) for response in spec.responses}
        status = str(spec.registration_status)
        if spec.primary is None and status not in responses:
            represented = responses.get("default", responses.get(f"{status[0]}XX"))
            if represented is not None:
                responses[status] = _copy(represented)
        adapted = any(parameter.native is None for parameter in spec.parameters) or (
            spec.body is not None and spec.body.decision.transport == "codec_adapter"
        )
        errors = [
            *(("400", "422") if adapted else ()),
            *(("415",) if spec.body is not None and spec.body.decision.transport == "codec_adapter" else ()),
            *(("401", "403") if spec.security is not None and any(spec.security.requirements) else ()),
            "500",
        ]
        for error in sorted(errors):
            self.error(responses, error)
        return responses

    def error(self, responses: JSONObject, status: str) -> None:
        """Add a generated error to an exact response, or to a copy of the range or default it falls under."""
        description, kind, detail = _ERRORS[status]
        media: JSONObject = {"schema": {"$ref": _SCHEMAS + self.framework(kind)}}
        if detail is not None:
            media["example"] = {"detail": detail}
        error: JSONObject = {
            "description": description,
            "content": {"text/plain" if status == "500" else _JSON_MEDIA: media},
        }
        if status == "401":
            error["headers"] = {
                "WWW-Authenticate": {
                    "description": "The authentication schemes the operation accepts.",
                    "schema": {"type": "string"},
                }
            }
        if _is_object(current := responses.get(status)):
            _merge(current, error)
            return
        base = responses.get(f"{status[0]}XX", responses.get("default"))
        if not _is_object(base):
            responses[status] = error
            return
        combined = _object(_copy(base))
        if "content" not in combined:
            combined["description"] = " ".join(filter(None, (str(combined.get("description", "")), _EMPTY_NOTE)))
        _merge(combined, error)
        responses[status] = combined

    def framework(self, kind: str) -> str:
        """Return the component name of a generated error schema, adding the component on first use."""
        name = f"dcg_{kind}__{self.suffix}"
        if name not in self.components:
            self.components[name] = Component(
                name=name, schema_id=None, direction="framework", schema=_copy(_FRAMEWORK[kind])
            )
        return name

    def security(self, security: SecuritySpec | None) -> JSONValue:
        """Return an operation's security alternatives under the generated scheme components' names."""
        if security is None:
            return []
        return [
            {self.scheme(name): _texts(scopes) for name, scopes in requirement} for requirement in security.requirements
        ]

    def scheme(self, name: str) -> str:
        """Return the component name of a source security scheme, adding the component on first use."""
        component = f"dcg_{normalize(name, empty='scheme', digit='s_')}__{self.suffix}"
        if (known := self.schemes.get(component)) is not None:
            if known.scheme_name != name:
                message = f"The security schemes {known.scheme_name!r} and {name!r} take one component name"
                raise APIGenerationError((
                    Diagnostic(
                        code="F_NAME_CONFLICT",
                        severity="error",
                        stage="target",
                        message=message,
                        target_id=self.request.target_id,
                    ),
                ))
            return component
        declaration = next(item for item in self.request.batch.security_schemes if item.name == name)
        self.schemes[component] = SchemeComponent(
            name=component, scheme_name=name, scheme=self.facts(declaration, _SCHEME_KEYS)
        )
        return component

    def callbacks(self, nodes: tuple[CallbackNode, ...]) -> JSONObject:
        """Return callbacks as documentation: each callback expression's operations by method."""
        callbacks: JSONObject = {}
        for node in nodes:
            target = _object(callbacks.setdefault(node.name, {}))
            for token in node.tokens[:-1]:
                target = _object(target.setdefault(token, {}))
            target[node.tokens[-1]] = self.callback(node)
        return callbacks

    def callback(self, node: CallbackNode) -> JSONObject:
        """Return a callback operation object from its facts, declarations, and nested callbacks."""
        contract = node.operation
        facts = dict(contract.facts)
        operation: JSONObject = {}
        for name in _CALLBACK_KEYS:
            if (value := facts.get(name)) is not None:
                self.put(operation, name, value, contract.id.use_site.pointer)
        if contract.parameters:
            operation["parameters"] = [self.parameter(declaration) for declaration in contract.parameters]
        if contract.request_body is not None:
            operation["requestBody"] = self.body(contract.request_body, raw=False)
        operation["responses"] = {
            str(declaration.name): self.response(declaration) for declaration in contract.responses
        }
        if (security := facts.get("security")) is not None:
            self.callback_security(operation, security, contract.id.use_site.pointer)
        if callbacks := self.callbacks(node.callbacks):
            operation["callbacks"] = callbacks
        return operation

    def callback_security(self, operation: JSONObject, value: FrozenLiteral, pointer: str) -> None:
        """Add a callback's security under the generated scheme components, when every scheme is declared."""
        known = {declaration.name for declaration in self.request.batch.security_schemes}
        requirements = _requirements(value)
        if requirements is None or any(name not in known for requirement in requirements for name in requirement):
            self.warn("The served document leaves out callback security that names no declared scheme", pointer)
            return
        operation["security"] = [
            {self.scheme(name): _texts(scopes) for name, scopes in requirement.items()} for requirement in requirements
        ]

    def facts(self, declaration: WireDeclaration, keys: Iterable[str]) -> JSONObject:
        """Return a declaration's facts of the keys in their order, dropping values that have no JSON form."""
        facts = dict(declaration.facts)
        found: JSONObject = {}
        for key in keys:
            if (value := facts.get(key)) is not None:
                self.put(found, key, value, declaration.use_site.pointer)
        return found

    def put(self, target: JSONObject, key: str, value: FrozenLiteral, pointer: str) -> None:
        """Add one documentation value, or report the annotation the served document cannot keep."""
        try:
            target[key] = _json(value)
        except _NotJSONError:
            self.warn(f"The served document leaves out {key}, which has no JSON form", pointer)

    def warn(self, message: str, pointer: str) -> None:
        """Report, once, a documentation annotation the served document cannot keep."""
        diagnostic = Diagnostic(
            code="W_DOCUMENTATION_ANNOTATION",
            severity="warning",
            stage="target",
            message=message,
            source_pointer=pointer,
            target_id=self.request.target_id,
        )
        if diagnostic not in self.problems:
            self.problems.append(diagnostic)

    def schema(self, use: TypeUseId) -> JSONValue:
        """Return the normalized schema of a type use in its direction, with references to components."""
        return self.rewrite(self.ids[use], use.direction)

    def rewrite(self, schema_id: str, direction: str) -> JSONValue:
        """Return a normalized schema with its direction's patches and every reference renamed to a component."""
        uri, fragment = urldefrag(schema_id)
        pointer = unquote(fragment)
        return self.walk(self.value(uri, pointer), uri, pointer, direction)

    def value(self, uri: str, pointer: str) -> WireValue:
        """Return the normalized value at a pointer of a bundled resource."""
        tokens = pointer_tokens(pointer)
        for contents in self.resources[uri]:
            if (found := _at(contents, tokens)) is not _MISSING:
                return found
        raise AssertionError(uri, pointer)

    def walk(self, value: WireValue, uri: str, pointer: str, direction: str) -> JSONValue:
        """Rewrite one schema object and its subschemas, keeping keyword data as it is."""
        if not _is_wire_mapping(value):
            return _plain(value)
        patches = self.patches.get((direction, uri, pointer), {})
        schema: JSONObject = {}
        for key, member in value.items():
            if key in _DEFINITIONS:
                continue
            item = patches.get(key, member)
            at = f"{pointer}/{escape_pointer_token(key)}"
            if key == "$ref" and isinstance(item, str):
                schema[key] = _SCHEMAS + self.component(item, direction)
            elif key == "discriminator" and _is_wire_mapping(item) and _is_wire_mapping(mapping := item.get("mapping")):
                schema[key] = {
                    **_object(_plain(item)),
                    "mapping": {name: self.mapped(target, uri, direction) for name, target in mapping.items()},
                }
            elif key in SCHEMA_MAP_KEYWORDS and _is_wire_mapping(item):
                schema[key] = {
                    name: self.walk(child, uri, f"{at}/{escape_pointer_token(name)}", direction)
                    for name, child in item.items()
                }
            elif key in SCHEMA_ARRAY_KEYWORDS and isinstance(item, tuple):
                schema[key] = [self.walk(child, uri, f"{at}/{index}", direction) for index, child in enumerate(item)]
            elif key in SCHEMA_VALUE_KEYWORDS:
                schema[key] = self.walk(item, uri, at, direction)
            else:
                schema[key] = _plain(item)
        return schema

    def mapped(self, target: WireValue, uri: str, direction: str) -> JSONValue:
        """Rename a discriminator mapping's schema reference to its component, when the schema is bundled."""
        schema_id = f"{uri}{target}" if str(target).startswith("#") else f"{uri}#/components/schemas/{target}"
        tokens = pointer_tokens(unquote(urldefrag(schema_id)[1]))
        bundled = isinstance(target, str) and any(
            _at(contents, tokens) is not _MISSING for contents in self.resources[uri]
        )
        return _SCHEMAS + self.component(schema_id, direction) if bundled else _plain(target)

    def component(self, schema_id: str, direction: str) -> str:
        """Return the component name of a referenced schema in a direction, queueing the component once."""
        if (name := self.identities.get((schema_id, direction))) is not None:
            return name
        digest = sha256(canonical_bytes([self.package, schema_id, direction]))[:12]
        name = self.identities[schema_id, direction] = f"{self.base(schema_id)}__{digest}"
        self.components[name] = None
        self.pending.append((schema_id, direction, name))
        return name

    def base(self, schema_id: str) -> str:
        """Return the readable part of a component name: the source component name or the model's name."""
        tokens = pointer_tokens(unquote(urldefrag(schema_id)[1]))
        named = tokens[:-1] == ["components", "schemas"] or (len(tokens) > 1 and tokens[-2] in _DEFINITIONS)
        base = tokens[-1] if named else self.symbols.get(schema_id, "Schema")
        return base if _COMPONENT.fullmatch(base) else normalize(base, empty="schema", digit="s_")


class _Missing(Enum):
    MISSING = "missing"


_MISSING: Final = _Missing.MISSING


def _at(value: WireValue, tokens: list[str]) -> WireValue | _Missing:
    for token in tokens:
        if _is_wire_mapping(value) and token in value:
            value = value[token]
        elif isinstance(value, tuple) and token.isdigit() and int(token) < len(value):
            value = value[int(token)]
        else:
            return _MISSING
    return value


def _fact(declaration: WireDeclaration, name: str) -> object:
    return next(
        (value.value for key, value in declaration.facts if key == name and isinstance(value, LiteralScalar)), None
    )


def _texts(values: Iterable[str]) -> list[JSONValue]:
    return [*values]


def _requirements(value: FrozenLiteral) -> list[dict[str, list[str]]] | None:
    requirements = documentation(value)
    if not (isinstance(requirements, list) and all(_is_requirement(requirement) for requirement in requirements)):
        return None
    return [
        {name: [str(scope) for scope in _scopes(names)] for name, names in _object(requirement).items()}
        for requirement in requirements
    ]


def _is_requirement(value: JSONValue) -> bool:
    return _is_object(value) and all(
        isinstance(names, list) and all(isinstance(scope, str) for scope in names) for names in value.values()
    )


def _scopes(value: JSONValue) -> list[JSONValue]:
    assert isinstance(value, list)
    return value


def _json(value: FrozenLiteral) -> JSONValue:
    if isinstance(value, LiteralSequence):
        return [_json(item) for item in value.items]
    if isinstance(value, LiteralMapping) and (names := _names(value)) is not None:
        return {name: _json(item) for name, (_, item) in zip(names, value.entries, strict=True)}
    if isinstance(value, LiteralScalar) and _is_json_scalar(scalar := value.value):
        return scalar
    raise _NotJSONError


def _names(value: LiteralMapping) -> list[str] | None:
    names = [key.value for key, _ in value.entries if isinstance(key, LiteralScalar) and isinstance(key.value, str)]
    return names if len(names) == len(value.entries) else None


def _is_json_scalar(value: object) -> TypeIs[str | int | float | bool | None]:
    return value is None or isinstance(value, (bool, int, str)) or (isinstance(value, float) and isfinite(value))


def _scalar(value: object) -> JSONValue:
    assert _is_json_scalar(value)
    return value


def documentation(value: FrozenLiteral) -> JSONValue | _Missing:
    """Return a documentation value as JSON, or MISSING when it has no JSON form."""
    try:
        return _json(value)
    except _NotJSONError:
        return _MISSING


def references(value: JSONValue) -> tuple[set[str], set[str]]:
    """Return the schema components and the security schemes a served-document value names."""
    schemas: set[str] = set()
    schemes: set[str] = set()
    pending = [value]
    while pending:
        match pending.pop():
            case dict() as mapping:
                for key, item in mapping.items():
                    match key, item:
                        case "$ref", str() as ref if ref.startswith(_SCHEMAS):
                            schemas.add(ref.removeprefix(_SCHEMAS))
                        case "security", list() as requirements:
                            schemes.update(
                                name for requirement in requirements if _is_object(requirement) for name in requirement
                            )
                        case _:
                            pending.append(item)
            case list() as items:
                pending.extend(items)
            case _:
                pass
    return schemas, schemes


def _plain(value: WireValue) -> JSONValue:
    if _is_wire_mapping(value):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return _scalar(value)


def _copy(value: JSONValue) -> JSONValue:
    if _is_object(value):
        return {key: _copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy(item) for item in value]
    return value


def _merge(response: JSONObject, extra: JSONObject) -> None:
    for key, value in extra.items():
        current = response.get(key)
        if key in {"content", "headers"} and _is_object(current) and _is_object(value):
            names = {name.lower() for name in current} if key == "headers" else set(current)
            for name, entry in value.items():
                if (name.lower() if key == "headers" else name) not in names:
                    current[name] = _copy(entry)
                elif key == "content":
                    _schema(_object(current[name]), _object(entry))
        elif current is None:
            response[key] = _copy(value)


def _schema(media: JSONObject, extra: JSONObject) -> None:
    schema = media.get("schema")
    media["schema"] = (
        _copy(extra["schema"]) if schema is None else {"anyOf": [*_branches(schema), _copy(extra["schema"])]}
    )


def _branches(schema: JSONValue) -> list[JSONValue]:
    if _is_object(schema) and len(schema) == 1 and isinstance(branches := schema.get("anyOf"), list):
        return branches
    return [schema]


def _object(value: JSONValue) -> JSONObject:
    assert _is_object(value)
    return value


def _is_object(value: object) -> TypeIs[JSONObject]:
    return isinstance(value, dict)


def _is_wire_mapping(value: object) -> TypeIs[Mapping[str, WireValue]]:
    return isinstance(value, Mapping)
