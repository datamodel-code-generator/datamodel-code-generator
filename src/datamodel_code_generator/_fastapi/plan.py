"""Plan a FastAPI server target: names, routes, arguments, and the native or adapter handling of each boundary."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
from itertools import starmap
from math import isfinite
from typing import TYPE_CHECKING, Final, Literal, TypeAlias, TypeVar

from typing_extensions import TypeIs

from datamodel_code_generator._api_types import Diagnostic, OperationRef
from datamodel_code_generator._fastapi.naming import file_stem_conflict
from datamodel_code_generator._fastapi.routes import (
    BUILDER_NAMES,
    EXPORTED_NAMES,
    RouteError,
    RoutePath,
    group_basis,
    group_key,
    placeholders,
    route_path,
)
from datamodel_code_generator._openapi_wire_plan import parameter_plans
from datamodel_code_generator._runtime.model_codecs.media import FieldPlan, media_kind, normalize_media_type
from datamodel_code_generator._target_contract import (
    BuiltinType,
    ConstructorType,
    GeneratedSymbolType,
    GenericType,
    ImportedType,
    LiteralMapping,
    LiteralScalar,
    LiteralSequence,
    LiteralType,
    NoneType,
    SourceLocation,
    UnionType,
)
from datamodel_code_generator._target_naming import NameScope, explicit_name, operation_basis
from datamodel_code_generator._url_redaction import redact_reference
from datamodel_code_generator.enums import DataModelType
from datamodel_code_generator.imports import Import

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_types import OperationSelector
    from datamodel_code_generator._fastapi.config import FastAPIConfig, HandlerMode, ResponseChoice
    from datamodel_code_generator._openapi_wire_plan import WirePlan
    from datamodel_code_generator._runtime.model_codecs.media import JSONValue, MediaKind
    from datamodel_code_generator._runtime.model_codecs.parameters import ParameterLocation, ParameterPlan
    from datamodel_code_generator._target_contract import (
        FieldUseBinding,
        FrozenLiteral,
        ModelFieldFacts,
        OperationContract,
        SymbolId,
        TypeArgument,
        TypeUseBinding,
        TypeUseId,
        TypeView,
        WireDeclaration,
    )
    from datamodel_code_generator._target_module import TypeNames

ArgumentLocation: TypeAlias = Literal[
    "path", "query", "querystring", "header", "cookie", "body", "request", "principal", "media_type"
]
Transport: TypeAlias = Literal["fastapi_native", "adapter", "raw_request"]
ArgumentKind: TypeAlias = Literal["request", "principal", "native", "adapter", "body", "media_type"]
SchemeKind: TypeAlias = Literal["api_key", "basic", "bearer", "digest", "oauth2", "openid", "custom"]
Requirement: TypeAlias = tuple[tuple[str, tuple[str, ...]], ...]
NativeApi: TypeAlias = Literal["Path", "Query", "Header", "Cookie"]
ValueKind: TypeAlias = Literal["scalar", "sequence"]
SettingT = TypeVar("SettingT")

RESERVED: Final = frozenset({"self", "request", "principal", "body", "media_type"})
BODYLESS_STATUSES: Final = frozenset({204, 205, 304})
_JSON: Final = "application/json"
_LOCATIONS: Final[dict[object, ParameterLocation]] = {
    "path": "path",
    "query": "query",
    "querystring": "querystring",
    "header": "header",
    "cookie": "cookie",
}
_STYLES: Final[dict[str, frozenset[str]]] = {
    "path": frozenset({"simple"}),
    "query": frozenset({"form"}),
    "header": frozenset({"simple"}),
    "cookie": frozenset({"cookie", "form"}),
}
_APIS: Final[dict[str, NativeApi]] = {"path": "Path", "query": "Query", "header": "Header", "cookie": "Cookie"}
_SCALAR_BUILTINS: Final = frozenset({"str", "int", "float", "bool", "bytes", "object"})
_SCALAR_MODULES: Final = frozenset({"datetime", "decimal", "ipaddress", "pydantic", "pydantic.networks", "uuid"})
_MODEL_IMPORTS: Final = frozenset({"BaseModel", "RootModel"})
_STRICT_TYPES: Final = frozenset({"StrictBool", "StrictFloat", "StrictInt"})
_STRICT_CONSTRUCTORS: Final = frozenset({"confloat", "conint"})
_STRICT_BYTES: Final = ("pydantic", "StrictBytes")
_STRICT_KEYWORD: Final = ("strict", LiteralScalar(kind="bool", value=True))
_DOCUMENTATION: Final = frozenset({"title", "description", "examples", "deprecated"})
_NULL: Final = LiteralScalar("none", None)
_LITERAL_KINDS: Final = frozenset({"bool", "int", "float", "str"})
_CONSTRAINTS: Final = frozenset({
    "allow_inf_nan",
    "decimal_places",
    "ge",
    "gt",
    "le",
    "lt",
    "max_digits",
    "max_length",
    "min_length",
    "multiple_of",
    "pattern",
})
_CONSTRUCTORS: Final[dict[tuple[str | None, str], tuple[str, BuiltinType | None]]] = {
    (None, "bytes"): ("conbytes", None),
    ("decimal", "Decimal"): ("condecimal", None),
    (None, "float"): ("confloat", None),
    (None, "int"): ("conint", None),
    (None, "str"): ("constr", None),
    ("pydantic", "StrictFloat"): ("confloat", BuiltinType("float")),
    ("pydantic", "StrictInt"): ("conint", BuiltinType("int")),
    ("pydantic", "StrictStr"): ("constr", BuiltinType("str")),
}
_PLAIN_KEYWORDS: Final = _CONSTRAINTS | _DOCUMENTATION | {"default_factory"}
_CONSTRAINED: Final = frozenset({"conbytes", "condecimal", "confloat", "conint", "constr"})
_WRAPPERS: Final = frozenset({"root", "alias"})
_HTTP_SCHEMES: Final[dict[str, SchemeKind]] = {"basic": "basic", "bearer": "bearer", "digest": "digest"}
_FLOWS: Final[dict[object, SchemeKind]] = {"oauth2": "oauth2", "openIdConnect": "openid"}
_API_KEY_LOCATIONS: Final = frozenset({"header", "query", "cookie"})
_INFO: Final = (
    ("info", "title", "title", "text"),
    ("info", "summary", "summary", "text"),
    ("info", "description", "description", "text"),
    ("info", "version", "version", "text"),
    ("root", "tags", "openapi_tags", "objects"),
    ("root", "servers", "servers", "objects"),
    ("info", "termsOfService", "terms_of_service", "text"),
    ("info", "contact", "contact", "object"),
    ("info", "license", "license_info", "object"),
)
_MIN_CONTENT_STATUS: Final = 200
_MAX_SUCCESS_STATUS: Final = 299
_DEFAULT_STATUS: Final = 200


class Default(Enum):
    """Whether a request must send a native argument, or the handler receives None for an absent one."""

    REQUIRED = "required"
    ABSENT = "absent"


@dataclass(frozen=True, slots=True)
class RootDefault:
    """The default of a parameter whose type stays a root model: the model of a literal, or the model's own default."""

    type: TypeView
    literal: LiteralScalar | LiteralSequence | None = None


@dataclass(frozen=True, slots=True)
class MemberDefault:
    """The default of an enum parameter: the members of its enum type that a literal, or each item of one, names."""

    type: TypeView
    literal: LiteralScalar | LiteralSequence


ParameterDefault: TypeAlias = Default | LiteralScalar | LiteralSequence | RootDefault | MemberDefault


@dataclass(frozen=True, slots=True, kw_only=True)
class Decision:
    """How one parameter, body, or primary response is handled."""

    transport: Transport


@dataclass(frozen=True, slots=True, kw_only=True)
class NativeField:
    """A native Path, Query, Header, or Cookie declaration: the model's type, the alias, and its documentation."""

    api: NativeApi
    alias: str
    type: TypeView
    keywords: tuple[tuple[str, object], ...] = ()
    default: ParameterDefault = Default.REQUIRED


@dataclass(frozen=True, slots=True, kw_only=True)
class MediaSpec:
    """One declared media type of a body or response, with its type use when the content has a schema."""

    media_type: str
    kind: MediaKind
    use: TypeUseBinding | None
    declaration: WireDeclaration


@dataclass(frozen=True, slots=True, kw_only=True)
class ParameterSpec:
    """One effective parameter, its wire plan, the model's type, and how the server receives it.

    `local` says the document that names its operation declares it, rather than a document it references.
    """

    location: ParameterLocation
    wire_name: str
    required: bool
    use: TypeUseBinding | None
    plan: ParameterPlan | None
    decision: Decision
    type: TypeView | None = None
    native: NativeField | None = None
    default: ParameterDefault = Default.ABSENT
    local: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class BodySpec:
    """A request body: its media, requiredness, decision, and the plans a URL-encoded form adapter reads."""

    required: bool
    media: tuple[MediaSpec, ...]
    decision: Decision
    form_fields: tuple[FieldPlan, ...] = ()

    @property
    def form(self) -> bool:
        """Return whether FastAPI reads the body natively as a form model."""
        return self.decision.transport == "fastapi_native" and self.media[0].kind == "form"


@dataclass(frozen=True, slots=True, kw_only=True)
class ResponseSpec:
    """One declared response with its media."""

    status: str
    declaration: WireDeclaration
    media: tuple[MediaSpec, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class PrimarySpec:
    """The response a bare return value takes, and how FastAPI sends it."""

    status: int
    response: ResponseSpec
    media: MediaSpec | None
    decision: Decision


@dataclass(frozen=True, slots=True, kw_only=True)
class SchemeSpec:
    """One declared security scheme the selected operations use: the FastAPI class that reads it, and its facts."""

    name: str
    kind: SchemeKind
    declaration: WireDeclaration
    location: str | None = None
    parameter: str | None = None
    python_name: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class SecuritySpec:
    """An operation's security alternatives in source order: scheme names with their scopes, or none."""

    requirements: tuple[Requirement, ...]

    @property
    def anonymous(self) -> bool:
        """Return whether an alternative requires no scheme, so the handler may receive no principal."""
        return not all(self.requirements)


@dataclass(frozen=True, slots=True, kw_only=True)
class Argument:
    """One keyword the handler receives, in the fixed argument order."""

    name: str
    kind: ArgumentKind
    location: ArgumentLocation
    wire_name: str | None = None
    required: bool = True
    native: NativeField | None = None
    parameter: ParameterSpec | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationSpec:
    """Everything the renderer needs for one selected operation."""

    contract: OperationContract
    python_name: str
    pascal: str
    group: str
    route: RoutePath
    parameters: tuple[ParameterSpec, ...]
    body: BodySpec | None
    responses: tuple[ResponseSpec, ...]
    primary: PrimarySpec | None
    registration_status: int
    arguments: tuple[Argument, ...]
    mode: HandlerMode
    security: SecuritySpec | None

    @property
    def key(self) -> str:
        """Return the operation reference: the root use-site pointer."""
        return self.contract.id.use_site.pointer

    @property
    def head(self) -> bool:
        """Return whether the operation is a HEAD operation, which sends no body."""
        return self.contract.method == "head"

    @property
    def native_primary(self) -> bool:
        """Return whether FastAPI's response_model sends bare primary values."""
        return self.primary is not None and self.primary.decision.transport == "fastapi_native"


@dataclass(frozen=True, slots=True, kw_only=True)
class GroupSpec:
    """One router group: its key, name, service Protocol name, and operations in declaration order."""

    key: str
    stem: str
    service: str
    operations: tuple[OperationSpec, ...]

    @property
    def secured(self) -> bool:
        """Return whether an operation of the group uses security, so its methods receive a principal."""
        return any(spec.security is not None for spec in self.operations)


@dataclass(frozen=True, slots=True, kw_only=True)
class ServerPlan:
    """The planned operations and groups of one server target, the schemes they use, and the source info."""

    operations: tuple[OperationSpec, ...]
    groups: tuple[GroupSpec, ...]
    schemes: tuple[SchemeSpec, ...]
    info: tuple[tuple[str, JSONValue], ...]


class PlanError(Exception):
    """Report every failure of one planning phase."""

    def __init__(self, diagnostics: list[Diagnostic]) -> None:
        """Keep the ordered diagnostics."""
        super().__init__(diagnostics[0].message)
        self.diagnostics = tuple(diagnostics)


def fact(declaration: WireDeclaration, name: str) -> object:
    """Return one scalar wire fact of a declaration."""
    return next(
        (value.value for key, value in declaration.facts if key == name and isinstance(value, LiteralScalar)), None
    )


def _problem(
    code: str, message: str, source: SourceLocation | None = None, option_path: str | None = None
) -> Diagnostic:
    return Diagnostic(
        code=code,
        severity="error",
        stage="config" if code.startswith("E_") else "target",
        message=message,
        source_pointer=None if source is None else source.pointer,
        option_path=option_path,
    )


def _unresolved(code: str, kind: str, declaration: WireDeclaration) -> Diagnostic | None:
    """Return the problem of a declaration whose reference reaches no object, or whose document does not load."""
    if (item := next((item for item in declaration.references if item.state != "resolved"), None)) is None:
        return None
    reason = (
        "whose document could not be loaded" if item.state == "document_not_observed" else f"which reaches no {kind}"
    )
    message = f"The {kind} {declaration.name!r} references {redact_reference(item.reference)!r}, {reason}"
    return _problem(code, message, declaration.use_site)


def _link_operation_problem(link: WireDeclaration) -> str | None:
    """Return why a link does not name its operation with exactly one string operationRef or operationId."""
    named = [(key, value) for key, value in link.facts if key in {"operationRef", "operationId"}]
    if not named:
        return f"The link {link.name!r} names no operation with operationRef or operationId"
    if len(named) > 1:
        return f"The link {link.name!r} names its operation with both operationRef and operationId"
    key, value = named[0]
    if isinstance(value, LiteralScalar) and isinstance(value.value, str):
        return None
    return f"The link {link.name!r} has an {key} that is not a string"


def invalid_links(operations: Iterable[OperationContract]) -> tuple[Diagnostic, ...]:
    """Return the problems of the operations' response links that reach no link naming an operation."""
    problems: list[Diagnostic] = []
    for operation in operations:
        for link in (child for response in operation.responses for child in response.children if child.kind == "link"):
            if (unresolved := _unresolved("F_LINK_INVALID", "link", link)) is not None:
                problems.append(unresolved)
            elif (message := _link_operation_problem(link)) is not None:
                problems.append(_problem("F_LINK_INVALID", message, link.use_site))
    return tuple(problems)


def _label(operation: OperationContract) -> str:
    return f"{operation.method.upper()} {operation.path}"


def _uses(declaration: WireDeclaration) -> tuple[TypeUseId, ...]:
    return (*declaration.schemas, *(use for child in declaration.children for use in child.schemas))


def _encoded(media: MediaSpec) -> WireDeclaration | None:
    """Return the first form encoding that neither FastAPI's Form nor the form adapter reads."""
    return next(
        (
            child
            for child in media.declaration.children
            if child.kind == "encoding"
            and (
                bool(child.children)
                or fact(child, "contentType") is not None
                or fact(child, "style") not in {None, "form"}
                or fact(child, "explode") is False
                or fact(child, "allowReserved") is True
            )
        ),
        None,
    )


class Planner:  # noqa: PLR0904
    """Plan every selected operation of one server target from the accepted batch and its wire plan."""

    def __init__(
        self,
        request: TargetRequest,
        config: FastAPIConfig,
        wire: WirePlan,
        types: TypeNames,
    ) -> None:
        """Index the batch, and resolve the per-operation settings to operation keys.

        `types` says which types a generated module can import.
        """
        self.request = request
        self.config = config
        self.wire = wire
        self.types = types
        self.unspelled: set[TypeUseId] = set()
        self.uses = {use.id: use for use in request.batch.type_uses}
        self.symbols = {symbol.id: symbol for symbol in request.batch.symbols}
        self.members: dict[SymbolId, list[FieldUseBinding]] = {}
        for member in request.batch.fields:
            self.members.setdefault(member.consumer, []).append(member)
        self.facts: dict[SymbolId, ModelFieldFacts] = {
            member.consumer: member.model_facts
            for member in reversed(request.batch.fields)
            if member.model_facts is not None
        }
        self.parameter_plans = parameter_plans(wire)
        self.scheme_declarations = {
            (declaration.use_site.document, declaration.name): declaration
            for declaration in request.batch.security_schemes
        }
        self.schemes: dict[str, SchemeSpec] = {}
        assert request.batch.names is not None
        self.target_names = request.batch.names
        self.backend = request.model_config.output_model_type.value
        self.problems: list[Diagnostic] = []
        self.names = self.selected("operation_names", config.operation_names)
        self.body_modes = self.selected("body_modes", config.body_modes)
        self.primaries = self.selected("primary_responses", config.primary_responses)
        self.parameter_names = self.selected("parameter_names", config.parameter_names)
        self.modes: dict[str, HandlerMode] = self.selected("handler_modes", config.handler_modes)
        self.raise_problems()

    def raise_problems(self) -> None:
        """Stop the phase when it reported any failure."""
        if self.problems:
            raise PlanError(self.problems)

    def selected(self, option: str, values: Mapping[OperationSelector, SettingT]) -> dict[str, SettingT]:
        """Resolve the operation selectors of one per-operation setting to operation keys."""
        resolved: dict[str, SettingT] = {}
        for selector, value in values.items():
            reference = OperationRef(pointer=selector) if isinstance(selector, str) else selector
            if (operation := self.request.resolve(reference)) is None:
                from datamodel_code_generator._target_documents import named_document  # noqa: PLC0415

                named = named_document(reference.document, reference.document)
                message = f"The {option} entry {reference.pointer!r}{named} {self.request.unresolved}"
                self.problems.append(_problem("E_OPERATION_REF", message, option_path=option))
            elif (key := operation.id.use_site.pointer) in resolved:
                message = f"The {option} setting names {key!r} twice"
                self.problems.append(_problem("E_CONFIG_VALUE", message, option_path=option))
            else:
                resolved[key] = value
        return resolved

    def plan(self) -> ServerPlan:
        """Plan names, then each operation's boundaries, arguments, and route, then the router groups."""
        operations = self.request.operations
        names = self.operation_names(operations)
        self.raise_problems()
        pascals = self.target_names.claim(
            NameScope(),
            [
                (self.target_names.pascal(name), _local(operation), (self.target_names.pascal(group),))
                for operation, name, group in zip(operations, names, map(self.group_name, operations), strict=True)
            ],
            camel=True,
        )
        specs = tuple(starmap(self.operation, zip(operations, names, pascals, strict=True)))
        self.check_routes(specs)
        groups = self.groups(specs)
        self.raise_problems()
        schemes = tuple(self.schemes.values())
        bases = [self.target_names.function(scheme.name) for scheme in schemes]
        python_names = self.target_names.claim(
            NameScope(),
            [
                (base, scheme.declaration.declaration.location.document == self.request.batch.documents[0].id, ())
                for scheme, base in zip(schemes, bases, strict=True)
            ],
        )
        schemes = tuple(replace(scheme, python_name=name) for scheme, name in zip(schemes, python_names, strict=True))
        return ServerPlan(operations=specs, groups=groups, schemes=schemes, info=self.info())

    def group_name(self, operation: OperationContract) -> str:
        """Return the name an operation's group derives, the scope a duplicate name of the operation lives in."""
        return self.target_names.function(group_basis(group_key(operation, single=self.config.layout == "single")))

    def operation_names(self, operations: tuple[OperationContract, ...]) -> list[str]:
        """Name each operation's handler: explicit names first, which must differ, then derived ones suffixed apart."""
        explicit = [name for operation in operations if (name := self.names.get(operation.id.use_site.pointer))]
        self.problems.extend(
            _problem("F_NAME_CONFLICT", f"Several operation names become {name!r}")
            for name, count in sorted(Counter(explicit).items())
            if count > 1
        )
        derived = [operation for operation in operations if operation.id.use_site.pointer not in self.names]
        bases = [
            self.target_names.function(operation_basis(operation.method, operation.path, operation.operation_id))
            for operation in derived
        ]
        claimed = dict(
            zip(
                (operation.id for operation in derived),
                self.target_names.claim(
                    NameScope(explicit),
                    [
                        (base, _local(operation), (self.group_name(operation),))
                        for operation, base in zip(derived, bases, strict=True)
                    ],
                ),
                strict=True,
            )
        )
        return [self.names.get(operation.id.use_site.pointer) or claimed[operation.id] for operation in operations]

    def groups(self, specs: tuple[OperationSpec, ...]) -> tuple[GroupSpec, ...]:
        """Group operations by first tag in first-occurrence order, naming router files and services apart.

        A router name becomes a file stem and the keyword that passes the group's service to a builder: explicit
        names must differ, also by case, from each other and from the names the package defines; derived ones are
        suffixed apart. A Windows device name or `__init__` needs an explicit name.
        """
        members: dict[str, list[OperationSpec]] = {}
        for spec in specs:
            members.setdefault(spec.group, []).append(spec)
        scope = NameScope((*BUILDER_NAMES, *EXPORTED_NAMES), folded=True)
        conflicts = {
            name
            for key in members
            if (name := self.config.router_names.get(key)) is not None
            and (file_stem_conflict(name) or not scope.take(name))
        }
        derived = [key for key in members if key not in self.config.router_names]
        bases = [self.target_names.function(group_basis(key)) for key in derived]
        claimed = self.target_names.claim(
            scope, [(base, _local(members[key][0].contract), ()) for key, base in zip(derived, bases, strict=True)]
        )
        stems = {
            key: self.config.router_names.get(key) or dict(zip(derived, claimed, strict=True))[key] for key in members
        }
        conflicts.update(stem for stem in stems.values() if file_stem_conflict(stem))
        self.problems.extend(
            _problem("F_NAME_CONFLICT", f"Several router groups or reserved names take {stem!r}")
            for stem in sorted(conflicts)
        )
        named = [key for key in members if stems[key] != "service"]
        services = dict(
            zip(
                named,
                self.target_names.claim(
                    NameScope(),
                    [(self.target_names.pascal(stems[key]), _local(members[key][0].contract), ()) for key in named],
                    camel=True,
                ),
                strict=True,
            )
        )
        return tuple(
            GroupSpec(
                key=key,
                stem=stems[key],
                service=f"{services[key]}Service" if key in services else "Service",
                operations=tuple(values),
            )
            for key, values in members.items()
        )

    def check_routes(self, specs: tuple[OperationSpec, ...]) -> None:
        """Reject two operations FastAPI cannot tell apart: the same method and path shape."""
        seen: set[tuple[str, str]] = set()
        for spec in specs:
            if (key := (spec.contract.method, spec.route.shape())) in seen:
                message = f"{_label(spec.contract)} differs from another route only by placeholder names"
                self.problems.append(_problem("F_ROUTE_INVALID", message, spec.contract.id.use_site))
            seen.add(key)

    def operation(self, operation: OperationContract, name: str, pascal: str) -> OperationSpec:
        """Plan one operation's parameters, body, responses, arguments, and route path."""
        try:
            wire_names: tuple[str, ...] | None = placeholders(operation.path)
        except RouteError as error:
            self.problems.append(_problem("F_ROUTE_INVALID", str(error), operation.id.use_site))
            wire_names = None
        names = wire_names or ()
        repeated = {placeholder for placeholder, count in Counter(names).items() if count > 1}
        parameters = tuple(self.parameter(operation, declaration, repeated) for declaration in operation.parameters)
        declared = {parameter.wire_name for parameter in parameters if parameter.location == "path"}
        self.problems.extend(
            _problem(
                "F_ROUTE_INVALID", f"The path placeholder {placeholder!r} has no path parameter", operation.id.use_site
            )
            for placeholder in dict.fromkeys(names)
            if placeholder not in declared
        )
        body = None if operation.request_body is None else self.body(operation, operation.request_body)
        responses = tuple(self.response(operation, response) for response in operation.responses)
        primary = self.primary(operation, responses)
        security = self.security(operation)
        arguments = self.arguments(
            operation, parameters, body, names, secured=security is not None, scopes=(self.group_name(operation), name)
        )
        route = self.route(operation, arguments, wire_names)
        slots = {slot.wire_name: slot.slot for slot in route.slots}
        return OperationSpec(
            contract=operation,
            python_name=name,
            pascal=pascal,
            group=group_key(operation, single=self.config.layout == "single"),
            route=route,
            parameters=parameters,
            body=body,
            responses=responses,
            primary=primary,
            registration_status=_registration(responses, primary),
            arguments=tuple(_slotted(argument, slots) for argument in arguments),
            mode=self.modes.get(operation.id.use_site.pointer, self.config.handler_mode),
            security=security,
        )

    def security(self, operation: OperationContract) -> SecuritySpec | None:
        """Return the operation's effective security alternatives, or None when none of them names a scheme."""
        value = next((value for key, value in operation.facts if key == "security"), None)
        if (requirements := _requirements(value)) is None:
            message = f"The security of {_label(operation)} is not a list of security requirement objects"
            self.problems.append(_problem("F_SECURITY_INVALID", message, operation.id.use_site))
            return None
        if not any(requirements):
            return None
        for name in dict.fromkeys(name for requirement in requirements for name, _ in requirement):
            self.scheme(operation, name)
        return SecuritySpec(requirements=requirements)

    def scheme(self, operation: OperationContract, name: str) -> None:
        """Record a scheme the first time an operation requires it, or report one the document does not declare."""
        if name in self.schemes:
            return
        if (declaration := self.scheme_declarations.get((operation.id.use_site.document, name))) is None:
            message = f"{_label(operation)} requires the undeclared security scheme {name!r}"
            self.problems.append(_problem("F_SECURITY_INVALID", message, operation.id.use_site))
        elif (unresolved := _unresolved("F_SECURITY_INVALID", "security scheme", declaration)) is not None:
            self.problems.append(unresolved)
        elif (scheme := _scheme(name, declaration)) is None:
            message = (
                f"The apiKey security scheme {name!r} needs a name and a location of header, query, or cookie"
                if fact(declaration, "type") == "apiKey"
                else f"The security scheme {name!r} needs a type of apiKey, http with a scheme, mutualTLS, oauth2, "
                "or openIdConnect"
            )
            self.problems.append(_problem("F_SECURITY_INVALID", message, declaration.use_site))
        else:
            self.schemes[name] = scheme

    def info(self) -> tuple[tuple[str, JSONValue], ...]:
        """Return the FastAPI settings the root document's info, tags, and servers supply, in constructor order."""
        root = {
            key: found for key, value in self.request.batch.document_facts if (found := json_value(value)) is not None
        }
        sources = {"root": root, "info": root.get("info")}
        found: list[tuple[str, JSONValue]] = []
        for container, key, option, kind in _INFO:
            match kind, _member(sources[container], key):
                case ("text", str() as value) | ("object", Mapping() as value):
                    found.append((option, value))
                case "objects", list() as value if all(isinstance(item, Mapping) for item in value):
                    found.append((option, value))
                case _:
                    pass
        return tuple(found)

    def route(
        self, operation: OperationContract, arguments: tuple[Argument, ...], wire_names: tuple[str, ...] | None
    ) -> RoutePath:
        """Rename the placeholders an ordinary route cannot match by their wire names."""
        if wire_names is None:
            return RoutePath(path=operation.path, route_path=operation.path, placeholders=(), slots=())
        own = {
            argument.name
            for argument in arguments
            if argument.location == "path" and argument.name == argument.wire_name
        }
        taken = {*RESERVED, *(argument.name for argument in arguments if argument.name not in own)}
        try:
            return route_path(operation.path, taken, repeatable=not self.wire.version.startswith("3.2"))
        except RouteError as error:
            self.problems.append(_problem("F_ROUTE_INVALID", str(error), operation.id.use_site))
            return RoutePath(path=operation.path, route_path=operation.path, placeholders=wire_names, slots=())

    def use(self, uses: tuple[TypeUseId, ...]) -> TypeUseBinding | None:
        """Return the first type use of a declaration, reporting a type no generated module can import."""
        use = next((self.uses[use] for use in uses if use in self.uses), None)
        if (
            use is not None
            and use.type is not None
            and use.id not in self.unspelled
            and self.types.unspellable(use.type)
        ):
            self.unspelled.add(use.id)
            self.problems.append(
                Diagnostic(
                    code="BND_TYPE_EXPRESSION_UNSUPPORTED",
                    severity="error",
                    stage="binding",
                    message="The use's final type has no expression a generated module can import",
                    source_pointer=use.id.use_site.pointer,
                )
            )
        return use

    def bound(self, use: TypeUseBinding | None) -> TypeUseBinding | None:
        """Return a request use, reporting a schema the model generator gave no type."""
        if use is not None and use.schema is not None and (use.state != "bound" or use.type is None):
            self.problems.append(
                Diagnostic(
                    code="BND_MODEL_SCOPE_REQUIRED"
                    if use.state == "not_generated"
                    else use.reason or "MC_BINDING_MISSING",
                    severity="error",
                    stage="binding",
                    message="The use has no generated native type",
                    source_pointer=use.id.use_site.pointer,
                )
            )
        return use

    def parameter(
        self, operation: OperationContract, declaration: WireDeclaration, repeated: set[str]
    ) -> ParameterSpec:
        """Decide how the server receives one effective parameter: natively when FastAPI reads its style and type."""
        location = _LOCATIONS[fact(declaration, "in")]
        name = declaration.name or ""
        use = self.bound(self.use(_uses(declaration)))
        plan = self.parameter_plans.get(operation.id, {}).get((location, name))
        value, default = self.parameter_type(use)
        if plan is not None and plan.content_media_type is None and use is not None and self.strict_bytes(use.type):
            message = (
                f"The {location} parameter {name!r} of {_label(operation)} is strict bytes, which rejects the text "
                "a parameter carries"
            )
            self.problems.append(_problem("F_PARAMETER_UNSUPPORTED", message, declaration.use_site))
        kind = (
            None
            if value is None or (plan is not None and plan.kind != "string" and self.textless(value))
            else self.kind(value)
        )
        spec = ParameterSpec(
            location=location,
            wire_name=name,
            required=fact(declaration, "required") is True,
            use=use,
            plan=plan,
            decision=Decision(transport="adapter"),
            type=value,
            default=default,
            local=declaration.declaration.location.document == operation.id.use_site.document,
        )
        if plan is None or value is None or not _native(plan, location, kind, repeated=name in repeated):
            return spec
        native = NativeField(
            api=_APIS[location],
            alias=plan.name,
            type=value,
            keywords=tuple(self.documentation(declaration, use)),
            default=Default.REQUIRED if plan.required else default,
        )
        return replace(spec, native=native, decision=replace(spec.decision, transport="fastapi_native"))

    def parameter_type(self, use: TypeUseBinding | None) -> tuple[TypeView | None, ParameterDefault]:
        """Return a parameter's type and default through the root models and aliases whose type alone validates.

        The parameter schema's own default comes first when it is a boolean, number, or string, or a list of them,
        since an alias may carry none and a referenced root model's is not the parameter's, and a declared null
        leaves the parameter without one. A declared default does not depend on how the model spells the type: a type
        that stays a root model takes that model, of the schema's default or with its own, an enum type its members,
        and any other type the schema's. A default factory without such a literal stays the model's, so its root model
        or alias is not unwrapped.
        """
        default: ParameterDefault = Default.ABSENT
        if use is None or use.type is None:
            return None, default
        value = use.type
        seen: set[SymbolId] = set()
        factory: TypeView | None = None
        while (
            isinstance(value, GeneratedSymbolType)
            and value.symbol not in seen
            and self.symbols[value.symbol].kind in {"root", "alias"}
            and (facts := self.facts.get(value.symbol)) is not None
            and (plain := self.plain(value.symbol, facts, frozenset(seen))) is not None
        ):
            seen.add(value.symbol)
            if factory is None and facts.backend.emitted.emitted_default_kind == "factory":
                factory = value
            value = plain
            default = _default(facts) if default is Default.ABSENT else default
        declared = None if use.schema is None else dict(use.keywords).get("default")
        literal = _literal(declared)
        null = declared == _NULL
        if null:
            default = Default.ABSENT
        if factory is not None and literal is None and default is Default.ABSENT:
            value = factory
        if isinstance(value, GeneratedSymbolType) and self.symbols[value.symbol].kind == "root":
            wrapped = self.facts.get(value.symbol)
            return value, RootDefault(
                value, literal
            ) if not null and wrapped is not None and wrapped.has_default else default
        default = default if literal is None else literal
        if isinstance(default, LiteralScalar | LiteralSequence) and (member := self.member(value)) is not None:
            return value, MemberDefault(member, default)
        return value, default

    def member(self, value: TypeView) -> GeneratedSymbolType | None:
        """Return the enum type whose members a default names: the type, its one member besides None, or its item."""
        match value:
            case GeneratedSymbolType() if self.symbols[value.symbol].kind == "enum":
                return value
            case UnionType() if len(members := [item for item in value.members if not isinstance(item, NoneType)]) == 1:
                return self.member(members[0])
            case GenericType() if value.base == BuiltinType("list") and len(value.arguments) == 1:
                return self.member(value.arguments[0])
            case _:
                pass
        return None

    def plain(self, symbol: SymbolId, facts: ModelFieldFacts, seen: frozenset[SymbolId]) -> TypeView | None:
        """Return the type a root model or alias validates as: its type, a scalar with constraints as a constrained one.

        A scalar that may be None is constrained the same way, so each spelling of the model gives one type. Its
        documentation keywords are left to the parameter's own and a default factory to the default; any other
        keyword, a constrained container, or a setting returns None.
        """
        settings = () if (model := self.symbols[symbol].facts) is None else model.configuration
        keywords = facts.backend.emitted.constructor_keywords
        constraints = tuple(item for item in keywords if item[0] in _CONSTRAINTS)
        if (
            any(setting.present for setting in settings)
            or any(name not in _PLAIN_KEYWORDS for name, _ in keywords)
            or self.types.unspellable(facts.type)
        ):
            return None
        value = self.nested(facts.type, seen | {symbol})
        return _constrained(value, constraints) if constraints else value

    def nested(self, value: TypeView, seen: frozenset[SymbolId]) -> TypeView:
        """Return a type with each alias among its members and arguments replaced by the type it validates as.

        A tuple keeps its members, as FastAPI reads none natively.
        """
        match value:
            case GeneratedSymbolType() if (
                value.symbol not in seen
                and self.symbols[value.symbol].kind == "alias"
                and (facts := self.facts.get(value.symbol)) is not None
                and (plain := self.plain(value.symbol, facts, seen)) is not None
            ):
                return plain
            case GenericType():
                fixed = value.tuple_form == "fixed"
                arguments = value.arguments if fixed else tuple(self.nested(item, seen) for item in value.arguments)
                return value if arguments == value.arguments else replace(value, arguments=arguments, hint=None)
            case UnionType():
                members = tuple(self.nested(item, seen) for item in value.members)
                return value if members == value.members else replace(value, members=members, hint=None)
            case _:
                pass
        return value

    def kind(self, value: TypeView) -> ValueKind | None:
        """Return whether FastAPI reads a parameter type as a scalar, as a sequence of scalars, or as neither."""
        kind: ValueKind | None = None
        match value:
            case UnionType():
                found = {self.kind(member) for member in value.members if not isinstance(member, NoneType)}
                kind = found.pop() if len(found) == 1 else None
            case GenericType() if value.base == BuiltinType("list") and len(value.arguments) == 1:
                kind = "sequence" if self.kind(value.arguments[0]) == "scalar" else None
            case _ if self.scalar(value):
                kind = "scalar"
            case _:
                pass
        return kind

    def literal(self, value: TypeView) -> bool:
        """Return whether a type accepts only enum members or literals, which FastAPI matches against query text."""
        if isinstance(value, UnionType | GenericType):
            members = value.members if isinstance(value, UnionType) else value.arguments
            return any(self.literal(member) for member in members)
        if isinstance(value, GeneratedSymbolType):
            return self.symbols[value.symbol].kind == "enum"
        return isinstance(value, LiteralType)

    def strict_bytes(self, value: TypeView | None, seen: frozenset[SymbolId] = frozenset()) -> bool:
        """Return whether a type holds pydantic's StrictBytes, also inside its root models and aliases."""
        match value:
            case UnionType():
                return any(self.strict_bytes(item, seen) for item in value.members)
            case GenericType():
                return any(self.strict_bytes(item, seen) for item in value.arguments)
            case ImportedType():
                return (value.import_.from_, value.import_.import_) == _STRICT_BYTES
            case GeneratedSymbolType() if (
                value.symbol not in seen and self.symbols[value.symbol].kind in _WRAPPERS and value.symbol in self.facts
            ):
                return self.strict_bytes(self.facts[value.symbol].type, seen | {value.symbol})
            case _:
                pass
        return False

    def textless(self, value: TypeView) -> bool:
        """Return whether a type takes values FastAPI's text is not: enum members, literals, or strict scalars."""
        return self.literal(value) or _strict(value)

    def scalar(self, value: TypeView) -> bool:
        """Return whether FastAPI reads a type as one scalar value: a builtin, enum, literal, or constrained scalar."""
        match value:
            case BuiltinType():
                return value.name in _SCALAR_BUILTINS
            case ConstructorType():
                return value.callable.import_.import_ in _CONSTRAINED
            case ImportedType():
                return value.import_.from_ in _SCALAR_MODULES and value.import_.import_ not in _MODEL_IMPORTS
            case GeneratedSymbolType():
                return self.symbols[value.symbol].kind == "enum"
            case _:
                pass
        return isinstance(value, LiteralType)

    @staticmethod
    def documentation(declaration: WireDeclaration, use: TypeUseBinding | None) -> Iterator[tuple[str, object]]:
        """Yield a parameter's documentation keywords: its schema's title, description, deprecation, and examples."""
        schema = {
            key: found
            for key, value in (() if use is None or use.schema is None else use.keywords)
            if (found := json_value(value)) is not None
        }
        if isinstance(title := schema.get("title"), str):
            yield "title", title
        if isinstance(description := fact(declaration, "description"), str) or isinstance(
            description := schema.get("description"), str
        ):
            yield "description", description
        if fact(declaration, "deprecated") is True or schema.get("deprecated") is True:
            yield "deprecated", True
        if isinstance(examples := schema.get("examples"), list) and examples:
            yield "examples", examples

    def body(self, operation: OperationContract, declaration: WireDeclaration) -> BodySpec:
        """Decide how the server receives a request body: FastAPI reads one JSON or URL-encoded model natively."""
        media = tuple(self.media(child) for child in declaration.children if child.kind == "media")
        for item in media:
            self.bound(item.use)
        spec = BodySpec(
            required=fact(declaration, "required") is True, media=media, decision=Decision(transport="adapter")
        )
        if self.body_modes.get(operation.id.use_site.pointer, self.config.body_mode) == "request":
            return replace(spec, decision=Decision(transport="raw_request"))
        if len(media) != 1:
            self.unsupported(operation, (item for item in media if item.kind == "multipart"))
            for item in media:
                if item.kind == "form" and (encoded := _encoded(item)) is not None:
                    self.unsupported(operation, (item,), f"the {encoded.name} encoding of ")
            form = next((item.use for item in media if item.kind == "form" and item.use is not None), None)
            return spec if form is None else replace(spec, form_fields=self.form_plans(form.type))
        item = media[0]
        match item.kind:
            case "json":
                return replace(spec, decision=Decision(transport="fastapi_native"))
            case "form" | "multipart":
                return self.form_body(operation, spec, item)
            case _:
                pass
        return spec

    def unsupported(self, operation: OperationContract, media: Iterable[MediaSpec], encoding: str = "") -> None:
        """Report request media that only the request body mode reads."""
        self.problems.extend(
            _problem(
                "F_MEDIA_UNSUPPORTED",
                f"{_label(operation)} needs body_mode='request' for {encoding}{item.media_type}",
                item.declaration.use_site,
            )
            for item in media
        )

    def media(self, declaration: WireDeclaration) -> MediaSpec:
        """Return one media declaration with its normalized type, kind, and type use."""
        media_type = normalize_media_type(declaration.name or "")
        return MediaSpec(
            media_type=media_type,
            kind=media_kind(media_type),
            use=self.use(declaration.schemas),
            declaration=declaration,
        )

    def form_body(self, operation: OperationContract, spec: BodySpec, media: MediaSpec) -> BodySpec:
        """Read a URL-encoded BaseModel natively as a FastAPI form model; adapt other forms to the model."""
        if (use := media.use) is None or (media.kind == "multipart" and use.type is None):
            self.unsupported(operation, (media,))
            return spec
        if (encoded := _encoded(media)) is not None:
            self.unsupported(operation, (media,), f"the {encoded.name} encoding of ")
            return spec
        if media.kind == "form" and spec.required and self.form_model(use.type):
            return replace(spec, decision=Decision(transport="fastapi_native"))
        return replace(spec, form_fields=self.form_plans(use.type))

    def form_plans(self, value: TypeView | None) -> tuple[FieldPlan, ...]:
        """Return the members a form adapter reads as text: each model field by wire name, as the field's type says."""
        members = self.members.get(value.symbol, ()) if isinstance(value, GeneratedSymbolType) else ()
        return tuple(
            self.form_field(member.wire_name, facts.type)
            for member in members
            if member.wire_name is not None and (facts := member.model_facts) is not None
        )

    def form_field(self, name: str, value: TypeView) -> FieldPlan:
        """Return a form member's plan: repeated for a list, in the kind the model's type gives its text or items."""
        repeated = self.kind(value) == "sequence"
        kind = self.wire.kinds.of(value, ("items",) if repeated else ()) or "string"
        return FieldPlan(name, kind, repeated=repeated)

    def form_model(self, value: TypeView | None) -> bool:
        """Return whether FastAPI reads a type as a form model: a BaseModel of fields FastAPI reads from text.

        A field FastAPI reads is a scalar or a list of scalars whose type accepts text, so neither a strict int, float,
        or bool nor an enum or literal of non-string values.
        """
        return (
            self.backend == DataModelType.PydanticV2BaseModel.value
            and isinstance(value, GeneratedSymbolType)
            and self.symbols[value.symbol].kind == "model"
            and all(
                (facts := member.model_facts) is not None
                and self.kind(facts.type) is not None
                and not (self.textless(facts.type) and self.form_field("", facts.type).kind != "string")
                for member in self.members.get(value.symbol, ())
            )
        )

    def response(self, operation: OperationContract, declaration: WireDeclaration) -> ResponseSpec:
        """Return one declared response with its media and effective headers."""
        media = tuple(self.media(child) for child in declaration.children if child.kind == "media")
        self.problems.extend(
            _problem(
                "F_MEDIA_UNSUPPORTED",
                f"No builtin encoder sends the {item.media_type} response of {_label(operation)}",
                item.declaration.use_site,
            )
            for item in media
            if item.kind in {"form", "multipart"}
        )
        status = declaration.name or "default"
        return ResponseSpec(
            status=status if status == "default" else status.upper(), declaration=declaration, media=media
        )

    def primary(self, operation: OperationContract, responses: tuple[ResponseSpec, ...]) -> PrimarySpec | None:
        """Choose the primary response and decide whether FastAPI's response_model sends bare values."""
        exact = {int(response.status): response for response in responses if response.status.isdigit()}
        if (choice := self.primaries.get(operation.id.use_site.pointer)) is not None:
            return self.chosen(operation, exact, choice)
        if not exact:
            return None
        successful = [code for code in exact if _MIN_CONTENT_STATUS <= code <= _MAX_SUCCESS_STATUS]
        status = _DEFAULT_STATUS if _DEFAULT_STATUS in exact else min(successful or exact)
        response = exact[status]
        media = default_media(response)
        decision = _primary_decision(operation, status, media)
        return PrimarySpec(status=status, response=response, media=media, decision=decision)

    def chosen(
        self, operation: OperationContract, exact: dict[int, ResponseSpec], choice: ResponseChoice
    ) -> PrimarySpec | None:
        """Return the explicitly chosen primary response, reporting a choice that names no declared response."""
        response = exact.get(choice.status_code)
        media = None if response is None else default_media(response)
        if response is not None and choice.media_type is not None:
            wanted = _normalized(choice.media_type)
            media = next((item for item in response.media if item.media_type == wanted), None)
        if response is None or (choice.media_type is not None and media is None):
            self.problems.append(
                _problem(
                    "E_CONFIG_VALUE",
                    f"The primary response chosen for {_label(operation)} is not declared",
                    operation.id.use_site,
                    "primary_responses",
                )
            )
            return None
        decision = _primary_decision(operation, choice.status_code, media)
        return PrimarySpec(status=choice.status_code, response=response, media=media, decision=decision)

    def arguments(  # noqa: PLR0913
        self,
        operation: OperationContract,
        parameters: tuple[ParameterSpec, ...],
        body: BodySpec | None,
        wire_names: tuple[str, ...],
        *,
        secured: bool,
        scopes: tuple[str, ...] = (),
    ) -> tuple[Argument, ...]:
        """Name the handler's keywords in the fixed order.

        Explicit names, configured or `--aliases` entries, must differ from each other and from the handler's own
        arguments; the others are named as model fields after their wire names, told apart in argument order by the
        naming strategy, whose enclosing `scopes` are the operation's group and handler.
        """
        names = self.parameter_names.get(operation.id.use_site.pointer, {})
        order = {name: index for index, name in enumerate(dict.fromkeys(wire_names))}
        path = sorted(
            (parameter for parameter in parameters if parameter.location == "path"),
            key=lambda parameter: order.get(parameter.wire_name, len(order)),
        )
        ordered = (*path, *(parameter for parameter in parameters if parameter.location != "path"))
        given = [
            names.get(f"{parameter.location}:{parameter.wire_name}") or self.alias(operation, parameter)
            for parameter in ordered
        ]
        raw = body is not None and body.decision.transport == "raw_request"
        known = {f"{parameter.location}:{parameter.wire_name}" for parameter in parameters}
        self.problems.extend(
            _problem(
                "E_CONFIG_VALUE",
                f"The parameter_names entry {key!r} of {_label(operation)} names no argument",
                operation.id.use_site,
                "parameter_names",
            )
            for key in names
            if key not in known
        )
        support: list[Argument] = []
        if raw or self.config.include_request:
            support.append(Argument(name="request", kind="request", location="request"))
        if secured:
            support.append(Argument(name="principal", kind="principal", location="principal"))
        if body is not None and not raw:
            support.append(Argument(name="body", kind="body", location="body", required=body.required))
            if len(body.media) > 1:
                support.append(
                    Argument(name="media_type", kind="media_type", location="media_type", required=body.required)
                )
        self.problems.extend(
            _problem(
                "F_NAME_CONFLICT", f"The arguments of {_label(operation)} take {name!r} twice", operation.id.use_site
            )
            for name, count in sorted(Counter([*filter(None, given), "self", *(item.name for item in support)]).items())
            if count > 1
        )
        bases = {
            index: self.target_names.argument(parameter.wire_name)
            for index, (parameter, name) in enumerate(zip(ordered, given, strict=True))
            if name is None
        }
        claimed = dict(
            zip(
                bases,
                self.target_names.claim(
                    NameScope((*RESERVED, *filter(None, given))),
                    [(base, ordered[index].local, scopes) for index, base in bases.items()],
                ),
                strict=True,
            )
        )
        arguments = [
            Argument(
                name=name or claimed[index],
                kind="native" if parameter.native is not None else "adapter",
                location=parameter.location,
                wire_name=parameter.wire_name,
                required=parameter.required,
                native=parameter.native,
                parameter=parameter,
            )
            for index, (parameter, name) in enumerate(zip(ordered, given, strict=True))
        ]
        bodies = [item for item in support if item.kind in {"body", "media_type"}]
        return (*(item for item in support if item.kind in {"request", "principal"}), *arguments, *bodies)

    def alias(self, operation: OperationContract, parameter: ParameterSpec) -> str | None:
        """Return the `--aliases` entry naming a parameter's argument, which must be an identifier."""
        if (alias := self.target_names.alias(parameter.wire_name)) is not None and not explicit_name(alias):
            message = (
                f"The --aliases entry {alias!r} of the {parameter.location} parameter {parameter.wire_name!r} of "
                f"{_label(operation)} is not an identifier"
            )
            self.problems.append(_problem("E_CONFIG_VALUE", message, operation.id.use_site))
        return alias


def _local(operation: OperationContract) -> bool:
    """Return whether the document that names the operation's path item declares the operation itself."""
    return operation.declaration.location.document == operation.id.use_site.document


def _requirements(value: FrozenLiteral | None) -> tuple[Requirement, ...] | None:
    if value is None:
        return ()
    if not isinstance(value, LiteralSequence):
        return None
    found = [requirement for item in value.items if (requirement := _requirement(item)) is not None]
    return tuple(found) if len(found) == len(value.items) else None


def _requirement(value: FrozenLiteral) -> Requirement | None:
    if not isinstance(value, LiteralMapping):
        return None
    found = [
        (name, scopes)
        for key, item in value.entries
        if isinstance(key, LiteralScalar)
        and isinstance(name := key.value, str)
        and (scopes := _scopes(item)) is not None
    ]
    return tuple(found) if len(found) == len(value.entries) else None


def _scopes(value: FrozenLiteral) -> tuple[str, ...] | None:
    if not isinstance(value, LiteralSequence):
        return None
    found = tuple(
        scope for item in value.items if isinstance(item, LiteralScalar) and isinstance(scope := item.value, str)
    )
    return found if len(found) == len(value.items) else None


def _scheme(name: str, declaration: WireDeclaration) -> SchemeSpec | None:
    match fact(declaration, "type"), fact(declaration, "in"), fact(declaration, "name"), fact(declaration, "scheme"):
        case "apiKey", str() as location, str() as parameter, _ if parameter and location in _API_KEY_LOCATIONS:
            return SchemeSpec(
                name=name, kind="api_key", declaration=declaration, location=location, parameter=parameter
            )
        case "http", _, _, str() as scheme if scheme:
            return SchemeSpec(name=name, kind=_HTTP_SCHEMES.get(scheme.lower(), "custom"), declaration=declaration)
        case "mutualTLS", _, _, _:
            return SchemeSpec(name=name, kind="custom", declaration=declaration)
        case kind, _, _, _ if kind in _FLOWS:
            return SchemeSpec(name=name, kind=_FLOWS[kind], declaration=declaration)
        case _:
            pass
    return None


class NotJSONError(Exception):
    """A documentation value that has no JSON form."""


def json_literal(value: FrozenLiteral) -> JSONValue:
    """Return a recorded literal as JSON, or raise `NotJSONError` when it has no JSON form."""
    if isinstance(value, LiteralSequence):
        return [json_literal(item) for item in value.items]
    if isinstance(value, LiteralMapping) and (names := _names(value)) is not None:
        return {name: json_literal(item) for name, (_, item) in zip(names, value.entries, strict=True)}
    if isinstance(value, LiteralScalar) and is_json_scalar(scalar := value.value):
        return scalar
    raise NotJSONError


def json_value(value: FrozenLiteral) -> JSONValue | None:
    """Return a recorded literal as JSON, or None when it has no JSON form."""
    try:
        return json_literal(value)
    except NotJSONError:
        return None


def is_json_scalar(value: object) -> TypeIs[str | int | float | bool | None]:
    """Return whether a value is a finite JSON scalar."""
    return value is None or isinstance(value, (bool, int, str)) or (isinstance(value, float) and isfinite(value))


def _names(value: LiteralMapping) -> list[str] | None:
    names = [key.value for key, _ in value.entries if isinstance(key, LiteralScalar) and isinstance(key.value, str)]
    return names if len(names) == len(value.entries) else None


def _member(source: object, key: str) -> JSONValue | None:
    """Return a recorded JSON member of an info source, or None when it has none."""
    return source.get(key) if isinstance(source, dict) else None


def _primary_decision(operation: OperationContract, status: int, media: MediaSpec | None) -> Decision:
    """Decide whether a bare primary value goes to FastAPI's response_model or through the response codecs."""
    bodyless = operation.method == "head" or status in BODYLESS_STATUSES or status < _MIN_CONTENT_STATUS
    native = (
        not bodyless
        and media is not None
        and media.media_type == _JSON
        and (use := media.use) is not None
        and use.type is not None
    )
    return Decision(transport="fastapi_native" if native else "adapter")


def _native(plan: ParameterPlan, location: ParameterLocation, kind: ValueKind | None, *, repeated: bool) -> bool:
    """Return whether FastAPI reads a parameter's style and type natively, so no adapter reads it."""
    return (
        plan.content_media_type is None
        and kind is not None
        and kind == {"scalar": "scalar", "array": "sequence"}.get(plan.shape)
        and plan.style in _STYLES.get(location, ())
        and (kind != "sequence" or (location == "query" and plan.explode))
        and not (location == "path" and repeated)
    )


def _constrained(value: TypeView, constraints: tuple[tuple[str, TypeArgument], ...]) -> TypeView | None:
    """Return a scalar with its constraints as a constrained scalar, a pydantic Strict one as a strict constrained one.

    A union of one scalar and None constrains the scalar; any other type returns None.
    """
    scalar: BuiltinType | ImportedType | None = None
    identity: tuple[str | None, str] = (None, "")
    match value:
        case UnionType() if len(scalars := [item for item in value.members if not isinstance(item, NoneType)]) == 1:
            if (constrained := _constrained(scalars[0], constraints)) is None:
                return None
            members = tuple(constrained if item is scalars[0] else item for item in value.members)
            return replace(value, members=members, hint=None)
        case BuiltinType():
            scalar, identity = value, (None, value.name)
        case ImportedType():
            scalar, identity = value, (value.import_.from_, value.import_.import_)
        case _:
            pass
    if scalar is None or (found := _CONSTRUCTORS.get(identity)) is None:
        return None
    constructor, strict = found
    keywords = constraints if strict is None else (*constraints, _STRICT_KEYWORD)
    return ConstructorType(ImportedType(Import(import_=constructor, from_="pydantic")), keywords, base=strict or scalar)


def _strict(value: TypeView) -> bool:
    """Return whether a type holds a strict int, float, or bool: a pydantic Strict type or strict constrained one."""
    match value:
        case UnionType():
            return any(map(_strict, value.members))
        case GenericType():
            return any(map(_strict, value.arguments))
        case ImportedType():
            return value.import_.from_ == "pydantic" and value.import_.import_ in _STRICT_TYPES
        case ConstructorType():
            return _STRICT_KEYWORD in value.keywords and value.callable.import_.import_ in _STRICT_CONSTRUCTORS
        case _:
            pass
    return False


def _literal(value: FrozenLiteral | None) -> LiteralScalar | LiteralSequence | None:
    """Return a recorded JSON boolean, number, or string, or a list of them, as a literal."""
    if isinstance(value, LiteralSequence):
        items = tuple(item for element in value.items if isinstance(item := _literal(element), LiteralScalar))
        return LiteralSequence("list", items) if len(items) == len(value.items) else None
    return value if isinstance(value, LiteralScalar) and value.kind in _LITERAL_KINDS else None


def _default(facts: ModelFieldFacts) -> Default | LiteralScalar | LiteralSequence:
    """Return the literal default a root model or alias declares, or absence."""
    emitted = facts.backend.emitted
    match emitted.emitted_default_kind, emitted.emitted_default_value:
        case "literal", LiteralScalar() as value:
            return value
        case "literal", LiteralSequence() as value if all(isinstance(item, LiteralScalar) for item in value.items):
            return value
        case _:
            pass
    return Default.ABSENT


def _slotted(argument: Argument, slots: Mapping[str, str]) -> Argument:
    """Declare a native path argument by the slot its placeholder takes in the route path."""
    native = argument.native
    if native is None or argument.location != "path" or (slot := slots.get(native.alias)) is None:
        return argument
    return replace(argument, native=replace(native, alias=slot))


def _registration(responses: tuple[ResponseSpec, ...], primary: PrimarySpec | None) -> int:
    if primary is not None:
        return primary.status
    if any(response.status == "default" for response in responses):
        return _DEFAULT_STATUS
    ranges = (int(response.status[0]) * 100 for response in responses if response.status.endswith("XX"))
    return min(ranges, default=_DEFAULT_STATUS)


def _normalized(media_type: str) -> str:
    try:
        return normalize_media_type(media_type)
    except ValueError:
        return media_type


def default_media(response: ResponseSpec) -> MediaSpec | None:
    """Return the media a response takes without a choice: JSON, then +json, then the first declared."""
    return (
        next((item for item in response.media if item.media_type == _JSON), None)
        or next((item for item in response.media if item.media_type.partition(";")[0].endswith("+json")), None)
        or next(iter(response.media), None)
    )
