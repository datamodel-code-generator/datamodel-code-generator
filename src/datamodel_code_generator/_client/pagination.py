"""Plan the cursor pagination helpers of a client target: their page, items, cursor, and the checks they must pass.

A helper's items are read through the page model's fields, by a typed accessor the package generates, and its cursor
from the page's wire value, a response header, or the status; each is checked against the operation it calls.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final, Literal

from datamodel_code_generator._api_types import Diagnostic
from datamodel_code_generator._codec_declarations import OperationRef
from datamodel_code_generator._generation_contract import (
    BindingCaptureError,
    GeneratedSymbolType,
    GenericType,
    NoneType,
    SourceLocation,
    SymbolId,
    UnionType,
)
from datamodel_code_generator._runtime.model_codecs.bindings import ArrayNode, MapNode, ModelNode, UnionNode
from datamodel_code_generator._runtime.protocols.records import canonical_json

if TYPE_CHECKING:
    from collections.abc import Iterator

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_types import DiagnosticStage
    from datamodel_code_generator._client.plan import ClientPlan, OperationSpec, ResponseSpec
    from datamodel_code_generator._client.protocol_plan import Protocols
    from datamodel_code_generator._client.protocols import Helper
    from datamodel_code_generator._codec_declarations import SchemaRef
    from datamodel_code_generator._generation_contract import FieldUseBinding, FinalPythonType, TypeUseBinding
    from datamodel_code_generator._openapi_codec_plan import CodecPlan
    from datamodel_code_generator._openapi_wire_plan import WirePlan
    from datamodel_code_generator._runtime.model_codecs.bindings import ModelBinding, TypeNode, UseBinding

StepKind = Literal["attr", "key", "get", "root"]
_TARGETS: Final = frozenset({"query", "path"})
_INTEGER: Final = re.compile(r"-?[0-9]+")
_MEMBERS: Final = ("anyOf", "oneOf")
_NULL: Final = frozenset({"null"})
_SEQUENCES: Final = frozenset({"list", "tuple"})


@dataclass(frozen=True, slots=True)
class ItemStep:
    """One step of an items accessor: an attribute, a required or optional TypedDict key, or a root model's root.

    `none` says the value it reads may be None, and `unset` that it may be msgspec's UNSET, as for an optional member
    of a Struct.
    """

    kind: StepKind
    name: str
    none: bool = False
    unset: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class PaginationSpec:
    """A cursor pagination helper ready to render: its operation, page use, item type, and items accessor."""

    helper: Helper
    operation: OperationSpec
    page: TypeUseBinding
    item: FinalPythonType
    item_schema: Mapping[str, str]
    steps: tuple[ItemStep, ...]

    @property
    def continuation(self) -> Mapping[str, Any]:
        """Return the helper's normalized cursor continuation."""
        return self.helper.tree["continuation"]


@dataclass(frozen=True, slots=True)
class _Reached:
    """Where a pointer through a page's model ends: the accessor steps, the last property's use, and its node."""

    steps: tuple[ItemStep, ...]
    member: FieldUseBinding | None
    node: TypeNode


def _label(spec: OperationSpec) -> str:
    return f"{spec.contract.method.upper()} {spec.contract.path}"


def _problem(code: str, stage: DiagnosticStage, at: str, message: str, spec: OperationSpec) -> Diagnostic:
    pointer = spec.contract.id.use_site.pointer
    return Diagnostic(
        code=code,
        severity="error",
        stage=stage,
        message=message,
        source_pointer=pointer,
        operation=OperationRef(pointer=pointer),
        option_path=at,
    )


def _tokens(pointer: str) -> list[str]:
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer.split("/")[1:]]


def _json_type(value: object) -> str:
    """Return the JSON type of a JSON value, a number being an integer exactly when its canonical JSON is one.

    Runtime end values compare by canonical JSON, where `2.0` stays a number, so it never ends an integer cursor.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float | Decimal):
        return "integer" if _INTEGER.fullmatch(canonical_json(value).decode()) else "number"
    if isinstance(value, str):
        return "string"
    return "object" if isinstance(value, Mapping) else "array"


def _listed(kinds: list[str]) -> str:
    return kinds[0] if len(kinds) == 1 else f"{', '.join(kinds[:-1])}{',' if len(kinds) > 2 else ''} or {kinds[-1]}"  # noqa: PLR2004


def _fits(kind: str, accepted: frozenset[str] | None) -> bool:
    return accepted is None or kind in accepted or (kind == "integer" and "number" in accepted)


def _unwrapped(node: TypeNode, models: Mapping[str, ModelBinding], steps: list[ItemStep]) -> TypeNode:
    """Return the node a root model or a single-member union stands for, adding each root step taken."""
    while True:
        if isinstance(node, ModelNode) and (root := models[node.symbol].root) is not None:
            steps.append(ItemStep("root", "root"))
            node = root
        elif isinstance(node, UnionNode) and len(node.members) == 1:
            node = node.members[0]
        else:
            return node


class _Pages:
    """Check and plan every enabled cursor pagination helper of a client target."""

    def __init__(self, protocols: Protocols, codecs: CodecPlan, wire: WirePlan, request: TargetRequest) -> None:
        """Index the use bindings, the model fields by symbol and wire name, and the documents by manifest pointer."""
        self.protocols = protocols
        self.wire = wire
        self.request = request
        self.bindings: Mapping[object, UseBinding] = dict(codecs.bindings)
        self.symbols = {name: symbol for symbol, name in codecs.imports}
        members = request.batch.fields
        self.fields = {(member.consumer, member.wire_name): member for member in members}
        self.roots = {member.consumer: member for member in members if member.member_kind == "root_value"}
        self.documents = {pointer: document for document, pointer in request.documents.pointers.items()}

    def walk(self, binding: UseBinding, pointer: str) -> _Reached | Literal["absent", "unsupported"]:
        """Follow a pointer through the fields of a page's models, or say why it names no field.

        A root model and a nullable single model are stepped through; a union of several members or a map is not.
        """
        models = {model.symbol: model for model in binding.models}
        steps: list[ItemStep] = []
        member: FieldUseBinding | None = None
        node = binding.type
        for token in _tokens(pointer):
            node = _unwrapped(node, models, steps)
            if isinstance(node, (UnionNode, MapNode)):
                return "unsupported"
            if (
                not isinstance(node, ModelNode)
                or (field := next((item for item in models[node.symbol].fields if item.wire_name == token), None))
                is None
            ):
                return "absent"
            native = models[node.symbol].native_kind
            nullable = isinstance(field.type, UnionNode) and field.type.nullable
            kind: StepKind = ("key" if field.required else "get") if native == "typed_dict" else "attr"
            struct = native == "struct"
            steps.append(
                ItemStep(
                    kind,
                    field.native_name,
                    none=nullable or (not field.required and not struct),
                    unset=struct and not field.required,
                )
            )
            member = self.fields[SymbolId(self.symbols[node.symbol]), token]
            node = field.type
        node = _unwrapped(node, models, steps)
        return _Reached(tuple(steps), member, node)

    def element(self, value: FinalPythonType | None) -> FinalPythonType:
        """Return the element type of a list or tuple type an array node binds, through None and generated roots."""
        assert value is not None
        while not isinstance(value, GenericType):
            if isinstance(value, UnionType):
                value = next(member for member in value.members if not isinstance(member, NoneType))
            else:
                assert isinstance(value, GeneratedSymbolType)
                facts = self.roots[value.symbol].model_facts
                assert facts is not None
                value = facts.type
        return value.arguments[0]

    def members(self, location: SourceLocation) -> tuple[SourceLocation, Mapping[str, object], list[SourceLocation]]:
        """Return a schema's resolved location, its value, and the locations of its anyOf and oneOf members."""
        resolved, schema = self.wire.schema(location)
        members = [
            _child(resolved, f"{keyword}/{index}")
            for keyword in _MEMBERS
            if isinstance(items := schema.get(keyword), tuple)
            for index in range(len(items))
        ]
        return resolved, schema, members

    def types(self, location: SourceLocation) -> frozenset[str] | None:
        """Return the JSON types a schema admits by its type, enum, const, or anyOf and oneOf members.

        None means any value: a schema that says nothing of its type, or a member that does not.
        """
        _, schema, members = self.members(location)
        kind = schema.get("type")
        if isinstance(kind, str):
            return frozenset({kind})
        if isinstance(kind, tuple):
            return frozenset(item for item in kind if isinstance(item, str))
        if isinstance(values := schema.get("enum"), tuple):
            return frozenset(map(_json_type, values))
        if "const" in schema:
            return frozenset({_json_type(schema["const"])})
        found = [kinds for member in members if (kinds := self.types(member)) is not None]
        return frozenset().union(*found) if members and len(found) == len(members) else None

    def array(self, location: SourceLocation) -> SourceLocation:
        """Return the location of an array schema, through anyOf or oneOf members of which all but it are null."""
        resolved, _, members = self.members(location)
        others = [member for member in members if self.types(member) != _NULL]
        return self.array(others[0]) if len(others) == 1 else resolved

    def item_schema(self, reference: SchemaRef) -> SourceLocation | None:
        """Return the location of a helper's item schema, or None when its document has no such pointer."""
        location = SourceLocation(self.documents[self.protocols.documents[reference]], reference.pointer, "schema")
        try:
            self.request.lease.borrow(location)
        except BindingCaptureError:
            return None
        return self.wire.schema(location)[0]

    def helper(self, helper: Helper, spec: OperationSpec) -> tuple[PaginationSpec | None, list[Diagnostic]]:
        """Check one enabled cursor helper against its operation, and plan it when every check passes."""
        tree = helper.tree
        at, name, label = helper.at, helper.name, _label(spec)
        problems: list[Diagnostic] = []
        continuation = tree["continuation"]
        for index, binding in enumerate(tree["bindings"]):
            message = (
                f"The pagination helper {name!r} with a binding to a {binding['target']['in']} target is not "
                "supported yet"
            )
            problems.append(_problem("E_CLIENT_UNSUPPORTED", "target", f"{at}.bindings[{index}]", message, spec))
        if (location := continuation["write"]["in"]) not in _TARGETS:
            message = f"The pagination helper {name!r} writing its cursor to a {location} target is not supported yet"
            problems.append(_problem("E_CLIENT_UNSUPPORTED", "target", f"{at}.continuation.write", message, spec))
        if spec.body is not None and any(media.kind != "json" for media in spec.body.media):
            message = f"The pagination helper {name!r} sends a request body other than JSON, which is not supported yet"
            problems.append(_problem("E_CLIENT_UNSUPPORTED", "target", at, message, spec))
        successes = [response for response in spec.responses if response.success]
        if (page := _page_use(successes)) is None or (binding := self.bindings.get(page.id)) is None:
            message = f"{label} must declare exactly one JSON success response for the pagination helper {name!r}"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.operation", message, spec))
            return None, problems
        if binding.projection_mode != "native" or binding.converter_strategy == "registered_adapter":
            message = f"The pagination helper {name!r} reads an envelope-projected response, which is not supported yet"
            problems.append(_problem("E_CLIENT_UNSUPPORTED", "target", at, message, spec))
            return None, problems
        planned = self.items(helper, spec, binding, page, problems)
        problems.extend(self.cursor(helper, spec, binding, successes[0].headers, continuation))
        if planned is None or problems:
            return None, problems
        steps, item = planned
        reference = tree["item_schema"]
        schema = {"document": self.protocols.documents[reference], "pointer": reference.pointer}
        return PaginationSpec(
            helper=helper, operation=spec, page=page, item=item, item_schema=schema, steps=steps
        ), problems

    def items(
        self,
        helper: Helper,
        spec: OperationSpec,
        binding: UseBinding,
        page: TypeUseBinding,
        problems: list[Diagnostic],
    ) -> tuple[tuple[ItemStep, ...], FinalPythonType] | None:
        """Check that the items pointer selects an array of the page whose items the item schema describes."""
        at, name, label = helper.at, helper.name, _label(spec)
        pointer = helper.tree["items"]["pointer"]
        reached = self.walk(binding, pointer)
        if reached == "unsupported":
            message = (
                f"The items pointer {pointer!r} of {name!r} reads through a union or map, which is not supported yet"
            )
            problems.append(_problem("E_CLIENT_UNSUPPORTED", "target", f"{at}.items", message, spec))
            return None
        if reached == "absent":
            message = f"The items pointer {pointer!r} of {name!r} names no property of the {label} response"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.items", message, spec))
            return None
        if not isinstance(reached.node, ArrayNode) or reached.node.container not in _SEQUENCES:
            message = f"The items pointer {pointer!r} of {name!r} selects no JSON array of the {label} response"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.items", message, spec))
            return None
        member = reached.member
        item = self.element(page.type if member is None or member.model_facts is None else member.model_facts.type)
        reference = helper.tree["item_schema"]
        if (expected := self.item_schema(reference)) is None:
            message = f"The item_schema {reference.pointer!r} of {name!r} does not exist in its document"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.item_schema", message, spec))
            return None
        array = page.schema if member is None else member.schema
        if array is None or self.wire.schema(_child(self.array(array), "items"))[0] != expected:
            message = (
                f"The item_schema {reference.pointer!r} of {name!r} is not the item schema of the array its items "
                "pointer selects"
            )
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.item_schema", message, spec))
            return None
        return reached.steps, item

    def cursor(
        self,
        helper: Helper,
        spec: OperationSpec,
        binding: UseBinding,
        headers: tuple[Any, ...],
        continuation: Mapping[str, Any],
    ) -> Iterator[Diagnostic]:
        """Check that the cursor reads a declared value whose types its target accepts, and that its ends are typed."""
        at, name, label = f"{helper.at}.continuation", helper.name, _label(spec)
        read = continuation["read"]
        types: frozenset[str] | None = frozenset({"integer"})
        match read["from"]:
            case "body":
                reached = self.walk(binding, pointer := read["pointer"])
                if reached == "unsupported":
                    message = (
                        f"The cursor pointer {pointer!r} of {name!r} reads through a union or map, which is not "
                        "supported yet"
                    )
                    yield _problem("E_CLIENT_UNSUPPORTED", "target", f"{at}.read", message, spec)
                    return
                if isinstance(reached, str) or reached.member is None or reached.member.schema is None:
                    message = f"The cursor pointer {pointer!r} of {name!r} names no property of the {label} response"
                    yield _problem("E_CONFIG_VALUE", "config", f"{at}.read", message, spec)
                    return
                types = self.types(reached.member.schema)
            case "header":
                if read["name"].lower() not in {header.name.lower() for header in headers}:
                    message = (
                        f"The cursor of {name!r} reads the header {read['name']!r}, which {label} does not declare"
                    )
                    yield _problem("E_CONFIG_VALUE", "config", f"{at}.read", message, spec)
                    return
                types = frozenset({"string"})
        write = continuation["write"]
        target = next(
            (item for item in spec.parameters if (item.location, item.wire_name) == (write["in"], write["name"])), None
        )
        accepted = (
            None if target is None or target.use is None or target.use.schema is None else self.types(target.use.schema)
        )
        if refused := sorted(kind for kind in types or () if kind != "null" and not _fits(kind, accepted)):
            message = (
                f"The cursor of {name!r} reads {_listed(refused)} values, which the {write['in']} parameter "
                f"{write['name']!r} of {label} does not accept"
            )
            yield _problem("E_CONFIG_VALUE", "config", f"{at}.write", message, spec)
        ends = continuation["end"]
        if types is not None and "null" in types and {"kind": "null"} not in ends:
            message = f"The cursor of {name!r} can read null, which no end condition covers"
            yield _problem("E_CONFIG_VALUE", "config", f"{at}.end", message, spec)
        for index, end in enumerate(ends):
            if end["kind"] == "value" and types is not None and not _fits(kind := _json_type(end["value"]), types):
                message = f"The end value {end['value']!r} of {name!r} is {kind}, which its cursor never reads"
                yield _problem("E_CONFIG_VALUE", "config", f"{at}.end[{index}].value", message, spec)


def _page_use(successes: list[ResponseSpec]) -> TypeUseBinding | None:
    """Return the use of the one JSON media with a schema of the one success response, which has a body, or None."""
    if len(successes) != 1 or successes[0].bodyless or len(media := successes[0].media) != 1:
        return None
    return media[0].use if media[0].kind == "json" else None


def _child(location: SourceLocation, token: str) -> SourceLocation:
    return SourceLocation(location.document, f"{location.pointer}/{token}", location.role)


def plan_pagination(
    protocols: Protocols | None, plan: ClientPlan, codecs: CodecPlan, wire: WirePlan, request: TargetRequest
) -> tuple[tuple[PaginationSpec, ...], dict[str, list[Diagnostic]]]:
    """Plan every enabled cursor pagination helper, returning the planned ones and each checked helper's problems.

    Helpers of other kinds and continuations are left to the caller, which refuses them.
    """
    if protocols is None:
        return (), {}
    pages = _Pages(protocols, codecs, wire, request)
    operations = {spec.contract.id: spec for spec in plan.operations}
    specs: list[PaginationSpec] = []
    problems: dict[str, list[Diagnostic]] = {}
    for helper in protocols.helpers:
        if not helper.enabled or helper.kind != "pagination" or helper.tree["continuation"]["kind"] != "cursor":
            continue
        spec = operations[protocols.operations[helper.links[0].ref].id]
        planned, problems[helper.name] = pages.helper(helper, spec)
        if planned is not None:
            specs.append(planned)
    return tuple(specs), problems
