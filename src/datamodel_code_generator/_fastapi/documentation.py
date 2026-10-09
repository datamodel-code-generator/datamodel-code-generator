"""Take the documentation FastAPI cannot derive from the routes out of the recorded declarations, at generation time.

Routes pass it to FastAPI as `responses=` and `openapi_extra`, so FastAPI's own document describes adapter
parameters and bodies, every declared response, and callbacks. The generated models own every schema: FastAPI
documents the ones it validates or sends a model with, and these annotations carry only the HTTP metadata the
parser recorded, never a copy of a source schema. The other parameters and headers, and the media types of a
generated type, get an empty schema; the application fills in the schema of each generated type when it builds its
document, from the places recorded here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

from typing_extensions import TypeIs

from datamodel_code_generator._fastapi.callbacks import CallbackIndex
from datamodel_code_generator._fastapi.plan import NotJSONError, fact, is_json_scalar, json_literal, json_value

if TYPE_CHECKING:
    from collections.abc import Iterable

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._fastapi.callbacks import CallbackNode
    from datamodel_code_generator._fastapi.plan import OperationSpec, ServerPlan
    from datamodel_code_generator._runtime.model_codecs.wire import JSONValue, WireValue
    from datamodel_code_generator._target_contract import FinalPythonType, FrozenLiteral, WireDeclaration

JSONObject: TypeAlias = "dict[str, JSONValue]"
Step: TypeAlias = "str | tuple[str, str]"
Mode: TypeAlias = Literal["validation", "serialization"]

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
_RAW_NOTE: Final = "The server passes this body to its handler as the request, without validating it."


@dataclass(frozen=True, slots=True)
class Place:
    """One place of an operation object that a generated type's schema describes, and the name FastAPI titles it by.

    A string token is a key of the object at hand, and a pair the parameter of that location and name in its
    parameters. A place under a response is described as what the server sends, any other as what it validates.
    """

    at: tuple[Step, ...]
    type: FinalPythonType
    name: str
    mode: Mode = "validation"


@dataclass(frozen=True, slots=True)
class OperationDocs:
    """What one route documents: its responses, its `openapi_extra`, and the places generated types describe.

    `models` names the JSON media type of each response whose model FastAPI documents itself, by status.
    """

    responses: dict[str, JSONObject]
    extra: JSONObject
    places: tuple[Place, ...]
    models: dict[str, tuple[str, FinalPythonType]]


class Documentation:
    """Build each operation's `openapi_extra` and `responses` from its recorded declarations."""

    def __init__(self, plan: ServerPlan, request: TargetRequest) -> None:
        """Index the callback operations of the batch the plan was made from."""
        self.plan = plan
        self.request = request
        self.index = CallbackIndex(request.batch)
        self.uses = {use.id: use for use in request.batch.type_uses}
        self.problems: dict[str, None] = {}
        self.found: dict[str, OperationDocs] = {}

    @property
    def documented(self) -> bool:
        """Return whether a generated type describes a place FastAPI does not document, in any operation."""
        return any(self.operation(spec).places for spec in self.plan.operations)

    def operation(self, spec: OperationSpec) -> OperationDocs:
        """Return what an operation documents, built once."""
        if (found := self.found.get(spec.key)) is not None:
            return found
        places: list[Place] = []
        models = _models(spec)
        responses = self.responses(spec, places)
        extra = self.openapi_extra(spec, places)
        native: set[tuple[Step, ...]] = {
            ("responses", status, "content", media) for status, (media, _) in models.items()
        }
        if spec.native_primary and spec.primary is not None:
            native.add(("responses", str(spec.primary.status), "content"))
        kept = tuple(place for place in places if not {place.at[:3], place.at[:4]} & native)
        self.found[spec.key] = found = OperationDocs(responses=responses, extra=extra, places=kept, models=models)
        return found

    def openapi_extra(self, spec: OperationSpec, places: list[Place]) -> JSONObject:
        """Return what the operation adds to FastAPI's operation object: adapter inputs, callbacks, and links."""
        contract = spec.contract
        extra: JSONObject = {}
        declarations = {(fact(item, "in"), item.name): item for item in contract.parameters}
        slots: dict[str, list[str]] = {}
        for slot in spec.route.slots:
            slots.setdefault(slot.wire_name, []).append(slot.slot)
        adapters: list[JSONValue] = []
        for parameter in spec.parameters:
            if parameter.native is None:
                declaration = declarations[parameter.location, parameter.wire_name]
                names = slots.get(parameter.wire_name, ()) if parameter.location == "path" else ()
                adapters.extend(
                    self.parameter(declaration, places, name=name, value=parameter.type)
                    for name in names or (parameter.wire_name,)
                )
        if adapters:
            extra["parameters"] = adapters
        if (body := spec.body) is not None and body.decision.transport != "fastapi_native":
            assert contract.request_body is not None
            raw = body.decision.transport == "raw_request"
            extra["requestBody"] = self.body(contract.request_body, ("requestBody",), places, raw=raw)
        if callbacks := self.callbacks(self.index.nodes(contract, spec.key), (), places):
            extra["callbacks"] = callbacks
        facts = dict(contract.facts)
        if (documentation := facts.get("externalDocs")) is not None:
            self.put(extra, "externalDocs", documentation, contract.id.use_site.pointer)
        if (servers := facts.get("servers")) is not None and self.own_servers(servers):
            self.put(extra, "servers", servers, contract.id.use_site.pointer)
        return extra

    def responses(self, spec: OperationSpec, places: list[Place]) -> dict[str, JSONObject]:
        """Return every declared response object; the route's response model documents a native primary body."""
        responses = {
            response.status: self.response(response.declaration, ("responses", response.status), places)
            for response in spec.responses
        }
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
        return root is None or json_value(servers) != _plain(root)

    def parameter(
        self,
        declaration: WireDeclaration,
        places: list[Place],
        *,
        at: tuple[Step, ...] = (),
        name: str | None = None,
        value: FinalPythonType | None = None,
    ) -> JSONObject:
        """Return a parameter object under its name: its facts, then its examples, then its schema or content.

        An adapter parameter's schema is its adapter's type; any other parameter's is its own type use's.
        """
        parameter = self.facts(declaration, _PARAMETER_KEYS)
        name = str(declaration.name) if name is None else name
        parameter["name"] = name
        place = (*at, (str(parameter.get("in")), name))
        value = self.type_of(declaration) if value is None else value
        self.payload(parameter, declaration, place, places, name=name, value=value)
        return parameter

    def header(self, declaration: WireDeclaration, at: tuple[Step, ...], places: list[Place]) -> JSONObject:
        """Return a header object: a parameter object without a name or location, which the server sends."""
        header = self.facts(declaration, _HEADER_KEYS)
        name = str(declaration.name)
        self.payload(header, declaration, at, places, name=name, value=self.type_of(declaration), mode="serialization")
        return header

    def payload(  # noqa: PLR0913, PLR0917
        self,
        target: JSONObject,
        declaration: WireDeclaration,
        at: tuple[Step, ...],
        places: list[Place],
        name: str,
        value: FinalPythonType | None,
        mode: Mode = "validation",
    ) -> None:
        """Add a parameter's or header's examples, then its media content or its schema."""
        target.update(self.facts(declaration, _EXAMPLE_KEYS))
        if media := [child for child in declaration.children if child.kind == "media"]:
            target["content"] = {
                str(child.name): self.media(child, (*at, "content", str(child.name)), places, mode, name)
                for child in media
            }
        else:
            target["schema"] = {}
            _record(places, at, value, name, mode)

    def media(
        self, declaration: WireDeclaration, at: tuple[Step, ...], places: list[Place], mode: Mode, name: str
    ) -> JSONObject:
        """Return a media type object: the schema of its type, its examples, and its effective encodings."""
        media: JSONObject = {}
        if _record(places, at, self.type_of(declaration), name, mode):
            media["schema"] = {}
        media.update(self.facts(declaration, _EXAMPLE_KEYS))
        if encodings := [child for child in declaration.children if child.kind == "encoding"]:
            media["encoding"] = {str(child.name): self.facts(child, _ENCODING_KEYS) for child in encodings}
        return media

    def body(self, declaration: WireDeclaration, at: tuple[Step, ...], places: list[Place], *, raw: bool) -> JSONObject:
        """Return a request body object; a raw request body says the server does not validate it."""
        body = self.facts(declaration, ("description", "required"))
        if raw:
            description = body.get("description")
            body["description"] = f"{description}\n\n{_RAW_NOTE}" if isinstance(description, str) else _RAW_NOTE
        body["content"] = {
            str(child.name): self.media(child, (*at, "content", str(child.name)), places, "validation", "body")
            for child in declaration.children
            if child.kind == "media"
        }
        return body

    def response(self, declaration: WireDeclaration, at: tuple[Step, ...], places: list[Place]) -> JSONObject:
        """Return a response object: its description, headers, content, and links."""
        response = self.facts(declaration, ("description",))
        response.setdefault("description", "")
        if headers := [child for child in declaration.children if child.kind == "header"]:
            response["headers"] = {
                str(child.name): self.header(child, (*at, "headers", str(child.name)), places) for child in headers
            }
        if media := [child for child in declaration.children if child.kind == "media"]:
            response["content"] = {
                str(child.name): self.media(
                    child, (*at, "content", str(child.name)), places, "serialization", "response"
                )
                for child in media
            }
        if links := [child for child in declaration.children if child.kind == "link"]:
            response["links"] = {str(child.name): self.facts(child, _LINK_KEYS) for child in links}
        return response

    def type_of(self, declaration: WireDeclaration) -> FinalPythonType | None:
        """Return the generated type of a declaration's first type use, or None if the model generator made none."""
        use = next((self.uses[use] for use in declaration.schemas if use in self.uses), None)
        return use.type if use is not None and use.state == "bound" else None

    def callbacks(self, nodes: tuple[CallbackNode, ...], at: tuple[Step, ...], places: list[Place]) -> JSONObject:
        """Return callbacks as documentation: each callback expression's operations by method."""
        callbacks: JSONObject = {}
        for node in nodes:
            target = _object(callbacks.setdefault(node.name, {}))
            for token in node.tokens[:-1]:
                target = _object(target.setdefault(token, {}))
            target[node.tokens[-1]] = self.callback(node, (*at, "callbacks", node.name, *node.tokens), places)
        return callbacks

    def callback(self, node: CallbackNode, at: tuple[Step, ...], places: list[Place]) -> JSONObject:
        """Return a callback operation object from its facts, declarations, and nested callbacks."""
        contract = node.operation
        facts = dict(contract.facts)
        operation: JSONObject = {}
        for name in _CALLBACK_KEYS:
            if (value := facts.get(name)) is not None:
                self.put(operation, name, value, contract.id.use_site.pointer)
        if contract.parameters:
            operation["parameters"] = [
                self.parameter(declaration, places, at=at) for declaration in contract.parameters
            ]
        if contract.request_body is not None:
            operation["requestBody"] = self.body(contract.request_body, (*at, "requestBody"), places, raw=False)
        operation["responses"] = {
            str(declaration.name): self.response(declaration, (*at, "responses", str(declaration.name)), places)
            for declaration in contract.responses
        }
        if callbacks := self.callbacks(node.callbacks, at, places):
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
            target[key] = json_literal(value)
        except NotJSONError:
            self.problems[f"{pointer}: The served document leaves out {key}, which has no JSON form"] = None


def _record(places: list[Place], at: tuple[Step, ...], value: FinalPythonType | None, name: str, mode: Mode) -> bool:
    """Record the place a generated type describes, and return whether the declaration has one."""
    if value is None:
        return False
    places.append(Place(at, value, name, mode))
    return True


def _models(spec: OperationSpec) -> dict[str, tuple[str, FinalPythonType]]:
    """Return the JSON media type and model of each response whose body FastAPI documents from the model itself."""
    return {
        response.status: (str(media.declaration.name), media.use.type)
        for response in spec.responses
        for media in response.media
        if media.media_type == "application/json" and media.use is not None and media.use.type is not None
    }


def _plain(value: WireValue) -> JSONValue:
    if _is_wire_mapping(value):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    assert is_json_scalar(value)
    return value


def _object(value: JSONValue) -> JSONObject:
    assert _is_object(value)
    return value


def _is_object(value: object) -> TypeIs[JSONObject]:
    return isinstance(value, dict)


def _is_wire_mapping(value: object) -> TypeIs[Mapping[str, WireValue]]:
    return isinstance(value, Mapping)
