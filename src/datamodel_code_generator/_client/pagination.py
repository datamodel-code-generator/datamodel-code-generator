"""Plan the pagination helpers of a client target and the checks they must pass.

A helper's items are read through the page model's fields, by a typed accessor the package generates, and its cursor,
end evidence, next URL, and binding values from the page's wire value, a response header, or the status; each is
checked against the operation it calls and the request target it writes.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Literal, TypeAlias

from datamodel_code_generator._api_types import Diagnostic, OperationRef
from datamodel_code_generator._client.model_facts import ItemStep
from datamodel_code_generator._client.plan import schema_use, schema_uses
from datamodel_code_generator._generation_contract import BindingCaptureError
from datamodel_code_generator._runtime.client.paths import dot_segment, path_segments
from datamodel_code_generator._runtime.client.retry import body_replay_safe
from datamodel_code_generator._runtime.client.security import secret_names
from datamodel_code_generator._runtime.model_codecs.errors import ParameterEncodingError
from datamodel_code_generator._runtime.model_codecs.media import media_kind
from datamodel_code_generator._runtime.model_codecs.parameters import path_text
from datamodel_code_generator._runtime.protocols.records import canonical_json
from datamodel_code_generator._target_contract import (
    BuiltinType,
    GeneratedSymbolType,
    GenericType,
    NoneType,
    SourceLocation,
    UnionType,
)

if TYPE_CHECKING:
    from collections.abc import Container, Iterator

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_types import DiagnosticStage, SchemaRef
    from datamodel_code_generator._client.model_facts import ModelFacts, StepKind
    from datamodel_code_generator._client.plan import ClientPlan, HeaderSpec, OperationSpec, ResponseSpec
    from datamodel_code_generator._client.protocol_plan import Protocols
    from datamodel_code_generator._client.protocols import Helper
    from datamodel_code_generator._runtime.model_codecs.media import JSONValue
    from datamodel_code_generator._target_contract import (
        Direction,
        FieldUseBinding,
        FinalPythonType,
        TypeUseBinding,
        TypeUseId,
    )

_INTEGER: Final = re.compile(r"-?[0-9]+")
_SETS: Final = frozenset({"set", "frozenset"})
_POINTED: Final = frozenset({"body", "querystring"})
_FOLLOWED: Final = frozenset({"next_url", "link"})
_URL_TYPES: Final = frozenset({"string", "null"})
_RELATION: Final = re.compile(r"[A-Za-z][A-Za-z0-9.-]*|[A-Za-z][A-Za-z0-9+.-]*:[!#-\[\]-~]+")
_WRITES: Final = MappingProxyType({"cursor": "cursor", "offset": "offset", "page": "page number"})
_EVIDENCE: Final = MappingProxyType({"has_more": "boolean", "total": "integer"})
_Types: TypeAlias = frozenset[str] | None


@dataclass(frozen=True, slots=True, kw_only=True)
class PaginationSpec:
    """A pagination helper ready to render: its operation, page use, item type, and items accessor."""

    helper: Helper
    operation: OperationSpec
    page: TypeUseBinding
    item: FinalPythonType
    item_schema: Mapping[str, str]
    steps: tuple[ItemStep, ...]

    @property
    def continuation(self) -> Mapping[str, Any]:
        """Return the helper's normalized continuation."""
        return self.helper.tree["continuation"]

    @property
    def evidence(self) -> Literal["has_more", "total"]:
        """Return which end evidence an offset or page-number continuation reads."""
        return evidence(self.continuation)


def evidence(continuation: Mapping[str, Any]) -> Literal["has_more", "total"]:
    """Return which end evidence an offset or page-number continuation reads, `has_more` or `total`."""
    return "has_more" if "has_more" in continuation else "total"


@dataclass(frozen=True, slots=True)
class _Reached:
    """Where a pointer through a page's model ends: the accessor steps, the last property's use, and its type."""

    steps: tuple[ItemStep, ...]
    member: FieldUseBinding | None
    value: FinalPythonType


def _label(spec: OperationSpec) -> str:
    return f"{spec.contract.method.upper()} {spec.contract.path}"


def _dot_literals(spec: OperationSpec, bindings: list[Mapping[str, Any]]) -> frozenset[int]:
    """Return the first literal binding of each path segment the literal bindings make `.` or `..`, by index.

    Only a segment whose every parameter a literal writes is judged, each literal encoded in its parameter's style; a
    literal its parameter cannot encode is left to the type check.
    """
    literals: dict[str, tuple[int, JSONValue]] = {}
    for index, item in enumerate(bindings):
        if item["target"]["in"] == "path" and "literal" in (value := item["value"]):
            literals.setdefault(item["target"]["name"], (index, value["literal"]))
    if not literals:
        return frozenset()
    plans = {item.wire_name: item.plan for item in spec.parameters if item.location == "path"}
    dotted: set[int] = set()
    for segment, names in path_segments(spec.contract.path):
        if not all(name in literals for name in names):
            continue
        try:
            texts = {name: path_text(plans[name], literals[name][1]) for name in names}
        except ParameterEncodingError:
            continue
        if dot_segment(segment, texts):
            dotted.add(min(literals[name][0] for name in names))
    return frozenset(dotted)


def _parameter_key(location: str, name: str) -> tuple[str, str]:
    """Return what a target naming a parameter writes, a header by its case-insensitive name.

    A querystring's targets point into it instead, so its key is no target's.
    """
    return (location, name.lower() if location == "header" else name)


def _target_key(target: Mapping[str, Any]) -> tuple[str, ...]:
    """Return what a request target writes, a header by its case-insensitive name."""
    if (location := target["in"]) == "body":
        return (location, target["pointer"])
    if location == "querystring":
        return (location, target["name"], target["pointer"])
    return _parameter_key(location, target["name"])


def _overlaps(first: tuple[str, ...], second: tuple[str, ...]) -> bool:
    """Return whether two targets write the same value, or one a member of the other's body or querystring value."""
    if first[0] not in _POINTED or first[:-1] != second[:-1]:
        return first == second
    one, other = first[-1], second[-1]
    return one == other or other.startswith(f"{one}/") or one.startswith(f"{other}/")


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
    if isinstance(value, int | float):
        return "integer" if _INTEGER.fullmatch(canonical_json(value).decode()) else "number"
    if isinstance(value, str):
        return "string"
    return "object" if isinstance(value, Mapping) else "array"


def _listed(kinds: list[str]) -> str:
    return kinds[0] if len(kinds) == 1 else f"{', '.join(kinds[:-1])}{',' if len(kinds) > 2 else ''} or {kinds[-1]}"  # noqa: PLR2004


def _fits(kind: str, accepted: frozenset[str] | None) -> bool:
    return accepted is None or kind in accepted or (kind == "integer" and "number" in accepted)


def _branches(value: FinalPythonType) -> bool:
    """Return whether a type is a union of several types or a mapping, which no accessor reads through."""
    return isinstance(value, UnionType) or (
        isinstance(value, GenericType) and value.tuple_form != "fixed" and len(value.arguments) == 2  # noqa: PLR2004
    )


def _sequence(value: FinalPythonType) -> bool:
    """Return whether a type is a list or a tuple of one item type, as a JSON array decodes to."""
    return (
        isinstance(value, GenericType)
        and value.tuple_form != "fixed"
        and len(value.arguments) == 1
        and not (isinstance(value.base, BuiltinType) and value.base.name in _SETS)
    )


class _Pages:
    """Check and plan every enabled pagination helper of a client target."""

    def __init__(
        self,
        protocols: Protocols,
        plan: ClientPlan,
        facts: ModelFacts,
        codecs: Container[TypeUseId],
        request: TargetRequest,
    ) -> None:
        """Keep the model facts and the uses with codecs, and index the documents by manifest pointer.

        The positions of the package's security schemes, with the credential headers, are where no helper writes.
        """
        self.protocols = protocols
        self.secret_headers, self.secret_queries = secret_names(plan.security_schemes)
        self.facts = facts
        self.codecs = codecs
        self.request = request
        self.documents = {pointer: document for document, pointer in request.documents.pointers.items()}
        self.schemas = schema_uses(request.batch.type_uses)

    def walk(self, value: FinalPythonType, pointer: str) -> _Reached | Literal["absent", "unsupported"]:
        """Follow a pointer through the fields of a page's models, or say why it names no field.

        A root model and a nullable single model are stepped through; a union of several members or a map is not.
        """
        facts = self.facts
        steps: list[ItemStep] = []
        member: FieldUseBinding | None = None
        for token in _tokens(pointer):
            if _branches(value := facts.unwrapped(value, steps)):
                return "unsupported"
            symbol = facts.symbol(value)
            fields = () if symbol is None or symbol.kind != "model" else facts.fields(symbol.id)
            if symbol is None or (field := next((item for item in fields if item.wire_name == token), None)) is None:
                return "absent"
            kind: StepKind = ("key" if field.required else "get") if symbol.backend == "typeddict" else "attr"
            struct = symbol.backend == "msgspec"
            steps.append(
                ItemStep(
                    kind,
                    field.name,
                    none=facts.nullable(field.type) or (not field.required and not struct),
                    unset=struct and not field.required,
                )
            )
            member, value = field.member, field.type
        value = facts.unwrapped(value, steps)
        return _Reached(tuple(steps), member, value)

    def element(self, value: FinalPythonType | None) -> FinalPythonType:
        """Return the element type of a list or tuple type an array node binds, through None and generated roots."""
        assert value is not None
        while not isinstance(value, GenericType):
            if isinstance(value, UnionType):
                value = next(member for member in value.members if not isinstance(member, NoneType))
            else:
                assert isinstance(value, GeneratedSymbolType)
                root = self.facts.root(value.symbol)
                assert root is not None
                value = root
        return value.arguments[0]

    def declared(self, value: FinalPythonType | None, pointer: str) -> _Types | Literal["absent"]:
        """Return the JSON types of the property a pointer names through the fields of a value's models.

        An empty pointer names the value itself; a pointer through no field of a model is absent.
        """
        if value is None or isinstance(reached := self.walk(value, pointer), str):
            return "absent" if pointer else None
        member = reached.member
        return (
            self.facts.json_types(value)
            if member is None or member.model_facts is None
            else self.facts.field_types(member.model_facts)
        )

    def item_type(self, reference: SchemaRef, direction: Direction) -> FinalPythonType | Literal["missing"] | None:
        """Return the type a helper's item schema is read as in a direction, or say its document has no such pointer.

        None is a schema no model stands for.
        """
        location = SourceLocation(self.documents[self.protocols.documents[reference]], reference.pointer, "schema")
        try:
            self.request.lease.borrow(location)
        except BindingCaptureError:
            return "missing"
        return None if (use := schema_use(self.schemas, location, direction)) is None else use.type

    def same(self, value: FinalPythonType | None, expected: FinalPythonType | None) -> bool:
        """Return whether a type is the type an item schema is read as, through None and aliases."""
        return value is not None and expected is not None and self.present(value) == self.present(expected)

    def present(self, value: FinalPythonType) -> FinalPythonType:
        """Return a type without None, through its aliases."""
        value = self.facts.plain(value)
        if (
            isinstance(value, UnionType)
            and len(members := [item for item in value.members if not isinstance(item, NoneType)]) == 1
        ):
            return self.facts.plain(members[0])
        return value

    def helper(self, helper: Helper, spec: OperationSpec) -> tuple[PaginationSpec | None, list[Diagnostic]]:
        """Check one enabled helper against its operation, and plan it when every check passes."""
        tree = helper.tree
        at, name, label = helper.at, helper.name, _label(spec)
        problems: list[Diagnostic] = []
        for index, entry in enumerate(tree["bindings"]):
            if entry["value"].get("source") == "input":
                message = f"The binding {index} of {name!r} reads the helper's input, which is not supported yet"
                where = f"{at}.bindings[{index}].value.source"
                problems.append(_problem("E_CLIENT_UNSUPPORTED", "target", where, message, spec))
        problems.extend(_credentials(helper, spec, self.secret_headers, self.secret_queries))
        if spec.body is not None and any(media.kind != "json" for media in spec.body.media):
            message = f"The pagination helper {name!r} sends a request body other than JSON, which is not supported yet"
            problems.append(_problem("E_CLIENT_UNSUPPORTED", "target", at, message, spec))
        successes = [response for response in spec.responses if response.success]
        if (page := _page_use(successes)) is None or page.id not in self.codecs or (body := page.type) is None:
            message = f"{label} must declare exactly one JSON success response for the pagination helper {name!r}"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.operation", message, spec))
            return None, problems
        planned = self.items(helper, spec, body, page, problems)
        headers = successes[0].headers
        check = {"cursor": self.cursor, "next_url": self.next_url, "link": self.link}.get(
            helper.tree["continuation"]["kind"], self.count
        )
        problems.extend(check(helper, spec, body, headers))
        problems.extend(self.values(helper, spec, body, headers))
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
        body: FinalPythonType,
        page: TypeUseBinding,
        problems: list[Diagnostic],
    ) -> tuple[tuple[ItemStep, ...], FinalPythonType] | None:
        """Check that the items pointer selects an array of the page whose items the item schema describes."""
        at, name, label = helper.at, helper.name, _label(spec)
        pointer = helper.tree["items"]["pointer"]
        reached = self.walk(body, pointer)
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
        if not _sequence(reached.value):
            message = f"The items pointer {pointer!r} of {name!r} selects no JSON array of the {label} response"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.items", message, spec))
            return None
        member = reached.member
        item = self.element(page.type if member is None or member.model_facts is None else member.model_facts.type)
        reference = helper.tree["item_schema"]
        if (expected := self.item_type(reference, page.id.direction)) == "missing":
            message = f"The item_schema {reference.pointer!r} of {name!r} does not exist in its document"
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.item_schema", message, spec))
            return None
        if not self.same(item, expected):
            message = (
                f"The item_schema {reference.pointer!r} of {name!r} is not the item schema of the array its items "
                "pointer selects"
            )
            problems.append(_problem("E_CONFIG_VALUE", "config", f"{at}.item_schema", message, spec))
            return None
        return reached.steps, item

    def read(  # noqa: PLR0913, PLR0917
        self,
        helper: Helper,
        spec: OperationSpec,
        body: FinalPythonType,
        headers: tuple[HeaderSpec, ...],
        read: Mapping[str, Any],
        at: str,
        what: str,
    ) -> _Types | Diagnostic:
        """Return the JSON types a selector of the page reads, every occurrence of a header being an array of them.

        A body pointer must name a property of the page's models and a header one the page response declares. A header
        read as end evidence spells a value of the types its declared schema gives.
        """
        name, label = helper.name, _label(spec)
        types: _Types = frozenset({"integer"})
        match read["from"]:
            case "body":
                reached = self.walk(body, pointer := read["pointer"])
                if reached == "unsupported":
                    message = (
                        f"The {what} pointer {pointer!r} of {name!r} reads through a union or map, which is not "
                        "supported yet"
                    )
                    return _problem("E_CLIENT_UNSUPPORTED", "target", at, message, spec)
                if isinstance(reached, str) or (member := reached.member) is None or member.model_facts is None:
                    message = f"The {what} pointer {pointer!r} of {name!r} names no property of the {label} response"
                    return _problem("E_CONFIG_VALUE", "config", at, message, spec)
                types = self.facts.field_types(member.model_facts)
            case "header":
                uses = {header.name.lower(): header.use for header in headers}
                if (key := read["name"].lower()) not in uses:
                    message = (
                        f"The {what} of {name!r} reads the header {read['name']!r}, which {label} does not declare"
                    )
                    return _problem("E_CONFIG_VALUE", "config", at, message, spec)
                types = frozenset({"array" if read["occurrence"] == "all" else "string"})
                if what in _EVIDENCE:
                    types = None if (use := uses[key]) is None or use.type is None else self.facts.json_types(use.type)
        return types

    def accepted(
        self, helper: Helper, spec: OperationSpec, target: Mapping[str, Any], at: str, what: str
    ) -> tuple[list[_Types], str, bool] | Diagnostic:
        """Return the JSON types each schema of a request target accepts, its description, and whether null fits it.

        Only a JSON body or a querystring of JSON content carries null. A querystring or body pointer must name a
        declared property, and every media of a body is checked; an optional body needs a media a call without one
        sends, since a page after a call that gives none still writes it.
        """
        label, pointer, where = _label(spec), target.get("pointer", ""), target["in"]
        carries = False
        if where == "body":
            body = spec.body
            assert body is not None
            place = f"the request body of {label}"
            if not body.required and body.default is None:
                message = (
                    f"The {what} of {helper.name!r} writes {place}, which is optional and has no media type a call "
                    "without one sends"
                )
                return _problem("E_CONFIG_VALUE", "config", at, message, spec)
            uses, carries = [media.use for media in body.media], True
        elif where == "querystring":
            place = f"the querystring {target['name']!r} of {label}"
            found = [item for item in spec.parameters if item.location == "querystring"]
            uses = [item.use for item in found]
            carries = all(media_kind(item.plan.content_media_type or "") == "json" for item in found)
        else:
            key = _target_key(target)
            place = f"the {where} parameter {target['name']!r} of {label}"
            uses = [item.use for item in spec.parameters if _parameter_key(item.location, item.wire_name) == key]
        schemas: list[_Types] = []
        for use in uses:
            if (found := self.declared(None if use is None else use.type, pointer)) == "absent":
                message = f"The {what} of {helper.name!r} writes {pointer!r}, which names no property of {place}"
                return _problem("E_CONFIG_VALUE", "config", at, message, spec)
            schemas.append(found)
        return schemas, f"the property {pointer!r} of {place}" if pointer else place, carries

    def cursor(
        self, helper: Helper, spec: OperationSpec, body: FinalPythonType, headers: tuple[HeaderSpec, ...]
    ) -> Iterator[Diagnostic]:
        """Check that the cursor reads a declared value whose types its target accepts, and that its ends are typed."""
        at = f"{helper.at}.continuation"
        continuation = helper.tree["continuation"]
        types = self.read(helper, spec, body, headers, continuation["read"], f"{at}.read", "cursor")
        if isinstance(types, Diagnostic):
            yield types
            return
        yield from self.fits(helper, spec, continuation["write"], types, f"{at}.write", "cursor", null=False)
        yield from self.ends(helper, spec, types, "cursor")

    @staticmethod
    def ends(helper: Helper, spec: OperationSpec, types: _Types, what: str) -> Iterator[Diagnostic]:
        """Check that a null the continuation reads ends the traversal and that each end value is of a type it reads."""
        at, name = f"{helper.at}.continuation", helper.name
        ends = helper.tree["continuation"]["end"]
        if types is not None and "null" in types and {"kind": "null"} not in ends:
            message = f"The {what} of {name!r} can read null, which no end condition covers"
            yield _problem("E_CONFIG_VALUE", "config", f"{at}.end", message, spec)
        for index, end in enumerate(ends):
            if end["kind"] == "value" and types is not None and not _fits(kind := _json_type(end["value"]), types):
                message = f"The end value {end['value']!r} of {name!r} is {kind}, which its {what} never reads"
                yield _problem("E_CONFIG_VALUE", "config", f"{at}.end[{index}].value", message, spec)

    def next_url(
        self, helper: Helper, spec: OperationSpec, body: FinalPythonType, headers: tuple[HeaderSpec, ...]
    ) -> Iterator[Diagnostic]:
        """Check that the next URL reads strings, that its ends are typed, and that a repeated body may be sent again.

        A body is sent again with its method only where a 307 or 308 redirect would send it again: the operation does
        not refuse retries, and its method is safe or it declares itself idempotent.
        """
        at, name = f"{helper.at}.continuation", helper.name
        continuation = helper.tree["continuation"]
        if continuation["repeat_request_body"]:
            message = None
            if spec.body is None:
                message = f"The next URL of {name!r} repeats the request body, which {_label(spec)} does not take"
            elif not body_replay_safe(spec.contract.method.upper(), spec.retry_safety, None):
                message = (
                    f"The next URL of {name!r} repeats the request body of {_label(spec)}, which is not safe to send "
                    "again; declare the operation's retry_safety idempotent"
                )
            if message is not None:
                yield _problem("E_CONFIG_VALUE", "config", f"{at}.repeat_request_body", message, spec)
        types = self.read(helper, spec, body, headers, continuation["read"], f"{at}.read", "next URL")
        if isinstance(types, Diagnostic):
            yield types
            return
        if types is not None and (others := sorted(types - _URL_TYPES)):
            message = f"The next URL of {name!r} reads {_listed(others)} values, where only string values fit"
            yield _problem("E_CONFIG_VALUE", "config", f"{at}.read", message, spec)
        yield from self.ends(helper, spec, types, "next URL")

    @staticmethod
    def link(
        helper: Helper, spec: OperationSpec, body: FinalPythonType, headers: tuple[HeaderSpec, ...]
    ) -> Iterator[Diagnostic]:
        """Check that the page response declares the Link header and that the relation is one RFC 8288 relation type."""
        del body
        at, name = f"{helper.at}.continuation", helper.name
        continuation = helper.tree["continuation"]
        if (header := continuation["header"]).lower() not in {item.name.lower() for item in headers}:
            message = f"The links of {name!r} come from the header {header!r}, which {_label(spec)} does not declare"
            yield _problem("E_CONFIG_VALUE", "config", f"{at}.header", message, spec)
        if not _RELATION.fullmatch(rel := continuation["rel"]):
            message = (
                f"The relation {rel!r} of {name!r} is no RFC 8288 relation type: a registered name or an absolute URI"
            )
            yield _problem("E_CONFIG_VALUE", "config", f"{at}.rel", message, spec)

    def count(
        self, helper: Helper, spec: OperationSpec, body: FinalPythonType, headers: tuple[HeaderSpec, ...]
    ) -> Iterator[Diagnostic]:
        """Check that an offset or page number is an integer its target accepts and that its end evidence is typed.

        A page number advances by a literal step. `has_more` must read booleans and `total` integers; a header is
        typed by its declared schema, whose text spells the value.
        """
        at, name = f"{helper.at}.continuation", helper.name
        continuation = helper.tree["continuation"]
        kind = continuation["kind"]
        if kind == "page" and "page_items_count" in continuation["step"]:
            message = f"The page number of {name!r} advances by a literal step, never by the page's item count"
            yield _problem("E_CONFIG_VALUE", "config", f"{at}.step", message, spec)
        yield from self.fits(
            helper, spec, continuation["write"], frozenset({"integer"}), f"{at}.write", _WRITES[kind], null=False
        )
        chosen = evidence(continuation)
        expected, read, where = _EVIDENCE[chosen], continuation[chosen], f"{at}.{chosen}"
        if chosen == "total" and read["from"] == "status":
            message = f"The total of {name!r} reads the response status, which counts no items"
            yield _problem("E_CONFIG_VALUE", "config", where, message, spec)
            return
        types = self.read(helper, spec, body, headers, read, where, chosen)
        if isinstance(types, Diagnostic):
            yield types
        elif types is not None and (others := sorted(types - {expected})):
            message = f"The {chosen} of {name!r} reads {_listed(others)} values, where only {expected} values fit"
            yield _problem("E_CONFIG_VALUE", "config", where, message, spec)

    def fits(  # noqa: PLR0913, PLR0917
        self,
        helper: Helper,
        spec: OperationSpec,
        target: Mapping[str, Any],
        types: _Types,
        at: str,
        what: str,
        *,
        null: bool,
    ) -> Iterator[Diagnostic]:
        """Refuse a value whose JSON types, null only when it is written, a request target does not accept.

        A null written where it cannot be carried is refused whatever the target's schema says.
        """
        accepted = self.accepted(helper, spec, target, at, what)
        if isinstance(accepted, Diagnostic):
            yield accepted
            return
        schemas, place, carries = accepted
        if null and types is not None and "null" in types and not carries:
            message = f"The {what} of {helper.name!r} can give null, which cannot be written to {place}"
            yield _problem("E_CONFIG_VALUE", "config", at, message, spec)
            null = False
        if refused := sorted(
            kind for kind in types or () if (null or kind != "null") and not all(_fits(kind, item) for item in schemas)
        ):
            verb = "reads" if what == "cursor" else "gives"
            message = f"The {what} of {helper.name!r} {verb} {_listed(refused)} values, which {place} does not accept"
            yield _problem("E_CONFIG_VALUE", "config", at, message, spec)

    def values(
        self, helper: Helper, spec: OperationSpec, body: FinalPythonType, headers: tuple[HeaderSpec, ...]
    ) -> Iterator[Diagnostic]:
        """Check each binding's value against its target, and that no two writes overlap.

        A binding's null is written, so its target must accept null too, and literals that make a path segment a dot
        segment are no path values. A followed URL replaces the path and query, and carries a body only when it
        repeats the request body.
        """
        tree = helper.tree
        continuation = tree["continuation"]
        kind = continuation["kind"]
        written = [] if kind in _FOLLOWED else [(_target_key(continuation["write"]), f"its {_WRITES[kind]}")]
        dotted = _dot_literals(spec, tree["bindings"])
        for index, item in enumerate(tree["bindings"]):
            at, what, value, target = (
                f"{helper.at}.bindings[{index}]",
                f"binding {index}",
                item["value"],
                item["target"],
            )
            if kind in _FOLLOWED and (unsent := _unsent(target, continuation)) is not None:
                message = f"The {what} of {helper.name!r} writes {unsent}, which no request after the first sends"
                yield _problem("E_CONFIG_VALUE", "config", f"{at}.target", message, spec)
                continue
            key = _target_key(target)
            if (clash := next(((other, owner) for other, owner in written if _overlaps(key, other)), None)) is not None:
                relation = "the same target as" if clash[0] == key else "a target overlapping that of"
                message = f"The {what} of {helper.name!r} writes {relation} {clash[1]}"
                yield _problem("E_CONFIG_CONFLICT", "config", f"{at}.target", message, spec)
                continue
            written.append((key, f"its {what}"))
            if value.get("source") == "input":
                continue
            if "literal" in value:
                if index in dotted:
                    message = (
                        f"The {what} of {helper.name!r} gives {value['literal']!r}, which makes the segment of the "
                        f"path parameter {target['name']!r} of {_label(spec)} a dot segment"
                    )
                    yield _problem("E_CONFIG_VALUE", "config", f"{at}.value.literal", message, spec)
                    continue
                types: _Types | Diagnostic = frozenset({_json_type(value["literal"])})
            else:
                types = self.read(helper, spec, body, headers, value["selector"], f"{at}.value.selector", what)
            if isinstance(types, Diagnostic):
                yield types
                continue
            yield from self.fits(helper, spec, target, types, f"{at}.target", what, null=True)


def _credentials(
    helper: Helper, spec: OperationSpec, headers: frozenset[str], queries: frozenset[str]
) -> Iterator[Diagnostic]:
    """Refuse a cursor, position, or binding written where a request carries credentials.

    Those are cookies, the credential headers, and the header and query positions of the package's security
    schemes, a querystring property among them; a value a server gives or a checkpoint saves is never written there.
    """
    tree = helper.tree
    continuation = tree["continuation"]
    kind = continuation["kind"]
    targets = [] if kind in _FOLLOWED else [(f"{helper.at}.continuation.write", _WRITES[kind], continuation["write"])]
    targets.extend(
        (f"{helper.at}.bindings[{index}].target", f"binding {index}", entry["target"])
        for index, entry in enumerate(tree["bindings"])
    )
    for at, what, target in targets:
        if (place := credential_place(target, headers, queries, spec)) is not None:
            message = f"The {what} of {helper.name!r} writes {place}, which carries credentials no helper writes"
            yield _problem("E_CONFIG_VALUE", "config", at, message, spec)


def credential_place(
    target: Mapping[str, Any], headers: frozenset[str], queries: frozenset[str], operation: OperationSpec
) -> str | None:
    """Return the credential position a request target writes, or None: a cookie or a credential header or query field.

    `headers` are the credential header names, folded to lower case, and `queries` the query names of the package's
    security schemes, which a querystring property may name too, and so may a declared property of an exploded form
    object query parameter, which the request sends as a query field of its own. The names its additional properties
    give are known only once written, which a checkpoint refuses.
    """
    name = target.get("name", "")
    field = next(iter(_tokens(target.get("pointer", ""))), None)
    place = None
    match target["in"]:
        case "cookie":
            place = f"the cookie {name!r}"
        case "header" if name.lower() in headers:
            place = f"the header {name!r}"
        case "query" if name in queries:
            place = f"the query parameter {name!r}"
        case "querystring" if field in queries:
            place = f"the query field {field!r}"
        case "query" if (sent := _sent_field(operation, name, queries)) is not None:
            place = f"the query field {sent!r}"
        case _:
            pass
    return place


def _sent_field(operation: OperationSpec, name: str, queries: frozenset[str]) -> str | None:
    """Return a declared property an exploded form object query parameter sends as a scheme's query field, or None."""
    plan = next(
        (item.plan for item in operation.parameters if item.location == "query" and item.wire_name == name), None
    )
    fields = (
        plan.fields if plan is not None and plan.shape == "object" and plan.explode and plan.style == "form" else ()
    )
    return next((item.name for item in fields if item.name in queries), None)


def _unsent(target: Mapping[str, Any], continuation: Mapping[str, Any]) -> str | None:
    """Return what a binding of a helper that follows URLs writes when the followed requests never send it.

    The URL replaces the path and the query, and the body is sent only when a next URL repeats it.
    """
    unsent = None
    match where := target["in"]:
        case "path" | "query" | "querystring":
            unsent = f"the {where} parameter {target['name']!r}"
        case "body" if not continuation.get("repeat_request_body"):
            unsent = "the request body"
    return unsent


def _page_use(successes: list[ResponseSpec]) -> TypeUseBinding | None:
    """Return the use of the one JSON media with a schema of the one success response, which has a body, or None."""
    if len(successes) != 1 or successes[0].bodyless or len(media := successes[0].media) != 1:
        return None
    return media[0].use if media[0].kind == "json" else None


def plan_pagination(
    protocols: Protocols | None,
    plan: ClientPlan,
    facts: ModelFacts,
    codecs: Container[TypeUseId],
    request: TargetRequest,
) -> tuple[tuple[PaginationSpec, ...], dict[str, list[Diagnostic]]]:
    """Plan every enabled pagination helper, returning the planned ones and each checked helper's problems.

    Helpers of other kinds have planners of their own.
    """
    if protocols is None:
        return (), {}
    pages = _Pages(protocols, plan, facts, codecs, request)
    operations = {spec.contract.id: spec for spec in plan.operations}
    specs: list[PaginationSpec] = []
    problems: dict[str, list[Diagnostic]] = {}
    for helper in protocols.helpers:
        if not helper.enabled or helper.kind != "pagination":
            continue
        spec = operations[protocols.operations[helper.links[0].ref].id]
        planned, problems[helper.name] = pages.helper(helper, spec)
        if planned is not None:
            specs.append(planned)
    return tuple(specs), problems
