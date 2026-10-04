"""Take the documentation FastAPI cannot derive from the routes out of the source document, at generation time.

Routes pass it to FastAPI as `responses=` and `openapi_extra`, so FastAPI's own document describes adapter
parameters and bodies, every declared response, and callbacks. Schemas are copied with references resolved, because
the document FastAPI serves has only the components of the models it validates with.
"""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from typing import TYPE_CHECKING, Final, TypeAlias
from urllib.parse import unquote, urldefrag, urljoin

from typing_extensions import TypeIs

from datamodel_code_generator._api_types import Diagnostic
from datamodel_code_generator._fastapi.callbacks import CallbackIndex
from datamodel_code_generator._runtime.model_codecs.wire import pointer_tokens
from datamodel_code_generator._target_contract import LiteralMapping, LiteralScalar, LiteralSequence

if TYPE_CHECKING:
    from collections.abc import Iterable

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._fastapi.callbacks import CallbackNode
    from datamodel_code_generator._fastapi.plan import OperationSpec, ServerPlan
    from datamodel_code_generator._openapi_wire_plan import WirePlan
    from datamodel_code_generator._runtime.model_codecs.wire import JSONValue, WireValue
    from datamodel_code_generator._target_contract import FrozenLiteral, TypeUseId, WireDeclaration

JSONObject: TypeAlias = "dict[str, JSONValue]"

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
_CALLBACK_KEYS: Final = ("tags", "summary", "description", "externalDocs", "operationId", "deprecated")
_DATA_KEYWORDS: Final = frozenset({"const", "default", "enum", "example", "examples"})
_RAW_NOTE: Final = "The server passes this body to its handler as the request, without validating it."


class _NotJSONError(Exception):
    """A documentation value that has no JSON form."""


class Documentation:
    """Build each operation's `openapi_extra` and `responses` from its declarations and the bundled schemas."""

    def __init__(self, plan: ServerPlan, request: TargetRequest, wires: tuple[WirePlan, ...]) -> None:
        """Index the bundled schema resources and the schema identities of the wire plans' uses."""
        self.plan = plan
        self.request = request
        self.resources: dict[str, list[WireValue]] = {}
        self.ids: dict[TypeUseId, str] = {}
        for wire in wires:
            for resource in wire.resources:
                self.resources.setdefault(resource.uri, []).append(resource.contents)
            self.ids.update(wire.schema_ids)
        self.index = CallbackIndex(request.batch)
        self.problems: dict[Diagnostic, None] = {}

    def openapi_extra(self, spec: OperationSpec) -> JSONObject:
        """Return what the operation adds to FastAPI's operation object: adapter inputs, callbacks, and links."""
        contract = spec.contract
        extra: JSONObject = {}
        declarations = {(_fact(item, "in"), item.name): item for item in contract.parameters}
        adapters: list[JSONValue] = [
            self.parameter(declarations[parameter.location, parameter.wire_name])
            for parameter in spec.parameters
            if parameter.native is None
        ]
        if adapters:
            extra["parameters"] = adapters
        if (body := spec.body) is not None and body.decision.transport != "fastapi_native":
            assert contract.request_body is not None
            extra["requestBody"] = self.body(contract.request_body, raw=body.decision.transport == "raw_request")
        if callbacks := self.callbacks(self.index.nodes(contract, spec.key)):
            extra["callbacks"] = callbacks
        facts = dict(contract.facts)
        if (documentation := facts.get("externalDocs")) is not None:
            self.put(extra, "externalDocs", documentation, contract.id.use_site.pointer)
        if (servers := facts.get("servers")) is not None and self.own_servers(servers):
            self.put(extra, "servers", servers, contract.id.use_site.pointer)
        return extra

    def responses(self, spec: OperationSpec) -> dict[str, JSONObject]:
        """Return every declared response object; the route's response model documents a native primary body."""
        responses = {response.status: self.response(response.declaration) for response in spec.responses}
        if spec.native_primary and spec.primary is not None:
            primary = responses[status := str(spec.primary.status)]
            primary.pop("content", None)
            primary.pop("description", None)
            if not primary:
                del responses[status]
        return responses

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
        if callbacks := self.callbacks(node.callbacks):
            operation["callbacks"] = callbacks
        return operation

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
            self.problems[
                Diagnostic(
                    code="W_DOCUMENTATION_ANNOTATION",
                    severity="warning",
                    stage="target",
                    message=f"The served document leaves out {key}, which has no JSON form",
                    source_pointer=pointer,
                    target_id=self.request.target_id,
                )
            ] = None

    def schema(self, use: TypeUseId) -> JSONValue:
        """Return the normalized schema of a type use with every reference resolved in place."""
        uri, fragment = urldefrag(self.ids[use])
        return self.resolved(self.value(uri, unquote(fragment)), uri, frozenset({self.ids[use]}))

    def value(self, uri: str, pointer: str) -> WireValue:
        """Return the normalized value at a pointer of a bundled resource."""
        tokens = pointer_tokens(pointer)
        return next(found for contents in self.resources[uri] if (found := _at(contents, tokens)) is not None)

    def resolved(self, value: WireValue, uri: str, active: frozenset[str]) -> JSONValue:
        """Copy a schema, replacing each reference by the schema it names; a reference to itself becomes `{}`."""
        if isinstance(value, tuple):
            return [self.resolved(item, uri, active) for item in value]
        if not _is_wire_mapping(value):
            return _plain(value)
        schema: JSONObject = {}
        reference = value.get("$ref")
        if isinstance(reference, str):
            target = urljoin(uri, reference)
            if target in active:
                return {}
            base, fragment = urldefrag(target)
            resolved = self.resolved(self.value(base, unquote(fragment)), base, active | {target})
            schema.update(resolved if _is_object(resolved) else {})
        for key, item in value.items():
            if key == "$ref":
                continue
            schema[key] = _plain(item) if key in _DATA_KEYWORDS else self.resolved(item, uri, active)
        return schema


def _at(value: WireValue, tokens: list[str]) -> WireValue | None:
    for token in tokens:
        if _is_wire_mapping(value) and token in value:
            value = value[token]
        elif isinstance(value, tuple) and token.isdigit() and int(token) < len(value):
            value = value[int(token)]
        else:
            return None
    return value


def _fact(declaration: WireDeclaration, name: str) -> object:
    return next(
        (value.value for key, value in declaration.facts if key == name and isinstance(value, LiteralScalar)), None
    )


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


def documentation(value: FrozenLiteral) -> JSONValue | None:
    """Return a documentation value as JSON, or None when it has no JSON form."""
    try:
        return _json(value)
    except _NotJSONError:
        return None


def _plain(value: WireValue) -> JSONValue:
    if _is_wire_mapping(value):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    assert _is_json_scalar(value)
    return value


def _object(value: JSONValue) -> JSONObject:
    assert _is_object(value)
    return value


def _is_object(value: object) -> TypeIs[JSONObject]:
    return isinstance(value, dict)


def _is_wire_mapping(value: object) -> TypeIs[Mapping[str, WireValue]]:
    return isinstance(value, Mapping)
