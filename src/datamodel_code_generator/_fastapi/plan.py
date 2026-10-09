"""Plan a FastAPI server target: names, routes, arguments, and the native or adapter handling of each boundary."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
from math import isfinite
from typing import TYPE_CHECKING, Final, Literal, TypeAlias, TypeVar

from typing_extensions import TypeIs

from datamodel_code_generator._api_types import Diagnostic, OperationRef
from datamodel_code_generator._codec_type_source import type_reason
from datamodel_code_generator._fastapi.naming import normalize
from datamodel_code_generator._fastapi.routes import (
    RouteError,
    RoutePath,
    group_key,
    group_stem,
    operation_name,
    placeholders,
    route_path,
    stem_conflicts,
)
from datamodel_code_generator._openapi_codec_plan import artifact_module
from datamodel_code_generator._openapi_wire_plan import parameter_plans
from datamodel_code_generator._runtime.model_codecs.media import FieldPlan, media_kind, normalize_media_type
from datamodel_code_generator._runtime.model_codecs.wire import checked_wire
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
from datamodel_code_generator.enums import DataModelType
from datamodel_code_generator.imports import Import

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_types import OperationSelector
    from datamodel_code_generator._fastapi.config import FastAPIConfig, HandlerMode, ResponseChoice
    from datamodel_code_generator._openapi_wire_plan import WirePlan
    from datamodel_code_generator._runtime.model_codecs.media import MediaKind
    from datamodel_code_generator._runtime.model_codecs.parameters import ParameterLocation, ParameterPlan
    from datamodel_code_generator._runtime.model_codecs.wire import JSONValue, WireValue
    from datamodel_code_generator._target_contract import (
        FieldUseBinding,
        FinalPythonType,
        FrozenLiteral,
        GeneratedTypeContractBatch,
        ModelFieldFacts,
        OperationContract,
        SymbolId,
        TypeUseBinding,
        TypeUseId,
        WireDeclaration,
    )

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

RESERVED: Final = frozenset({"request", "principal", "body", "media_type"})
BODYLESS_STATUSES: Final = frozenset({204, 205, 304})
_JSON: Final = "application/json"
_LOCATIONS: Final[dict[object, ParameterLocation]] = {
    "path": "path",
    "query": "query",
    "querystring": "querystring",
    "header": "header",
    "cookie": "cookie",
}
_STYLES: Final = {"path": "simple", "query": "form", "header": "simple", "cookie": "cookie"}
_APIS: Final[dict[str, NativeApi]] = {"path": "Path", "query": "Query", "header": "Header", "cookie": "Cookie"}
_SCALAR_BUILTINS: Final = frozenset({"str", "int", "float", "bool", "bytes", "object"})
_SCALAR_MODULES: Final = frozenset({"datetime", "decimal", "ipaddress", "pydantic", "pydantic.networks", "uuid"})
_MODEL_IMPORTS: Final = frozenset({"BaseModel", "RootModel"})
_DOCUMENTATION: Final = frozenset({"title", "description", "examples", "deprecated"})
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
_CONSTRUCTORS: Final[dict[tuple[str | None, str], str]] = {
    (None, "bytes"): "conbytes",
    ("decimal", "Decimal"): "condecimal",
    (None, "float"): "confloat",
    (None, "int"): "conint",
    (None, "str"): "constr",
}
CONSTRAINED: Final = frozenset({
    ("pydantic", "conbytes"),
    ("pydantic", "condecimal"),
    ("pydantic", "confloat"),
    ("pydantic", "conint"),
    ("pydantic", "constr"),
})
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
    """How an optional native argument behaves when the request omits it."""

    REQUIRED = "required"
    ABSENT = "absent"


@dataclass(frozen=True, slots=True, kw_only=True)
class Decision:
    """How one parameter, body, or primary response is handled."""

    transport: Transport


@dataclass(frozen=True, slots=True, kw_only=True)
class NativeField:
    """A native Path, Query, Header, or Cookie declaration: the model's type, the alias, and its documentation."""

    api: NativeApi
    alias: str
    type: FinalPythonType
    keywords: tuple[tuple[str, object], ...] = ()
    default: Default | LiteralScalar | LiteralSequence = Default.REQUIRED


@dataclass(frozen=True, slots=True, kw_only=True)
class MediaSpec:
    """One declared media type of a body or response, with its type use when the content has a schema."""

    media_type: str
    kind: MediaKind
    use: TypeUseBinding | None
    declaration: WireDeclaration


@dataclass(frozen=True, slots=True, kw_only=True)
class ParameterSpec:
    """One effective parameter, its wire plan, the model's type, and how the server receives it."""

    location: ParameterLocation
    wire_name: str
    required: bool
    use: TypeUseBinding | None
    plan: ParameterPlan | None
    decision: Decision
    type: FinalPythonType | None = None
    native: NativeField | None = None
    default: Default | LiteralScalar | LiteralSequence = Default.ABSENT


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
        """Return the operation key: the root use-site pointer."""
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
    """One router group: its key, name, and operations in declaration order."""

    key: str
    stem: str
    operations: tuple[OperationSpec, ...]

    @property
    def service(self) -> str:
        """Return the name of the group's service Protocol: Service after the group's PascalCase name."""
        return "Service" if self.stem == "service" else f"{pascal(self.stem)}Service"

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
    info: tuple[tuple[str, WireValue], ...]


class PlanError(Exception):
    """Report every failure of one planning phase."""

    def __init__(self, diagnostics: list[Diagnostic]) -> None:
        """Keep the ordered diagnostics."""
        super().__init__(diagnostics[0].message)
        self.diagnostics = tuple(diagnostics)


def pascal(name: str) -> str:
    """Return the PascalCase form of a finalized snake_case name."""
    return "".join(token[0].upper() + token[1:] for token in name.split("_") if token)


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
    message = f"The {kind} {declaration.name!r} references {item.reference!r}, {reason}"
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
    ) -> None:
        """Index the batch, and resolve the per-operation settings to operation keys."""
        self.request = request
        self.config = config
        self.wire = wire
        self.uses = {use.id: use for use in request.batch.type_uses}
        self.symbols = {symbol.id: symbol for symbol in request.batch.symbols}
        self.imports = symbol_imports(request.batch)
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
                from datamodel_code_generator._api_manifest import named_document  # noqa: PLC0415

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
        names = {operation.id.use_site.pointer: self.operation_name(operation) for operation in operations}
        self.problems.extend(
            _problem("F_NAME_CONFLICT", f"Several operation names become {name!r}")
            for name, count in sorted(Counter(pascal(name) for name in names.values()).items())
            if count > 1
        )
        self.raise_problems()
        specs = tuple(self.operation(operation, names[operation.id.use_site.pointer]) for operation in operations)
        self.check_routes(specs)
        groups = self.groups(specs)
        self.raise_problems()
        return ServerPlan(operations=specs, groups=groups, schemes=tuple(self.schemes.values()), info=self.info())

    def operation_name(self, operation: OperationContract) -> str:
        """Return an operation's explicit name, or its normalized operationId or method and path."""
        return self.names.get(operation.id.use_site.pointer) or operation_name(operation)

    def groups(self, specs: tuple[OperationSpec, ...]) -> tuple[GroupSpec, ...]:
        """Group operations by first tag in first-occurrence order, naming router files uniquely."""
        members: dict[str, list[OperationSpec]] = {}
        for spec in specs:
            members.setdefault(spec.group, []).append(spec)
        stems = {key: self.config.router_names.get(key) or group_stem(key) for key in members}
        self.problems.extend(
            _problem("F_NAME_CONFLICT", f"Several router groups or reserved names take {stem!r}")
            for stem in sorted(stem_conflicts(stems.values()))
        )
        groups = tuple(
            GroupSpec(
                key=key,
                stem=stems[key],
                operations=tuple(values),
            )
            for key, values in members.items()
        )
        self.problems.extend(
            _problem("F_NAME_CONFLICT", f"Several router groups take the service name {name!r}")
            for name, count in sorted(Counter(group.service for group in groups).items())
            if count > 1
        )
        return groups

    def check_routes(self, specs: tuple[OperationSpec, ...]) -> None:
        """Reject two operations FastAPI cannot tell apart: the same method and path shape."""
        seen: set[tuple[str, str]] = set()
        for spec in specs:
            if (key := (spec.contract.method, spec.route.shape())) in seen:
                message = f"{_label(spec.contract)} differs from another route only by placeholder names"
                self.problems.append(_problem("F_ROUTE_INVALID", message, spec.contract.id.use_site))
            seen.add(key)

    def operation(self, operation: OperationContract, name: str) -> OperationSpec:
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
        arguments = self.arguments(operation, parameters, body, names, secured=security is not None)
        route = self.route(operation, arguments, wire_names)
        slots = {slot.wire_name: slot.slot for slot in route.slots}
        return OperationSpec(
            contract=operation,
            python_name=name,
            pascal=pascal(name),
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

    def info(self) -> tuple[tuple[str, WireValue], ...]:
        """Return the FastAPI settings the root document's info, tags, and servers supply, in constructor order."""
        root = {
            key: found for key, value in self.request.batch.document_facts if (found := json_value(value)) is not None
        }
        sources = {"root": root, "info": root.get("info")}
        found: list[tuple[str, WireValue]] = []
        for container, key, option, kind in _INFO:
            match kind, _member(sources[container], key):
                case ("text", str() as value) | ("object", Mapping() as value):
                    found.append((option, value))
                case "objects", tuple() as value if all(isinstance(item, Mapping) for item in value):
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
        """Return the first type use of a declaration."""
        return next((self.uses[use] for use in uses if use in self.uses), None)

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
                    source_uri=self.request.documents.root_uri,
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
        kind = (
            None
            if value is None or (plan is not None and plan.kind != "string" and self.literal(value))
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

    def parameter_type(
        self, use: TypeUseBinding | None
    ) -> tuple[FinalPythonType | None, Default | LiteralScalar | LiteralSequence]:
        """Return a parameter's type and default through the root models and aliases whose type alone validates.

        The parameter schema's own boolean, number, or string default comes first, since an alias carries none and a
        referenced root model's is not the parameter's; one of an enum stays the model's.
        """
        default: Default | LiteralScalar | LiteralSequence = Default.ABSENT
        if use is None or use.type is None:
            return None, default
        value = use.type
        seen: set[SymbolId] = set()
        while (
            isinstance(value, GeneratedSymbolType)
            and value.symbol not in seen
            and self.symbols[value.symbol].kind in {"root", "alias"}
            and (facts := self.facts.get(value.symbol)) is not None
            and (plain := self.plain(value.symbol, facts, frozenset(seen))) is not None
        ):
            seen.add(value.symbol)
            value = plain
            default = _default(facts) if default is Default.ABSENT else default
        if (
            seen
            and use.schema is not None
            and (isinstance(value, LiteralType) or not self.literal(value))
            and (literal := use.default) is not None
        ):
            default = literal
        return value, default

    def plain(self, symbol: SymbolId, facts: ModelFieldFacts, seen: frozenset[SymbolId]) -> FinalPythonType | None:
        """Return the type a root model or alias validates as: its type, a scalar with constraints as a constrained one.

        Its documentation keywords are left to the parameter's own; any other keyword, a constrained container, or a
        setting returns None.
        """
        settings = () if (model := self.symbols[symbol].facts) is None else model.configuration
        keywords = facts.backend.emitted.constructor_keywords
        constraints = tuple(item for item in keywords if item[0] in _CONSTRAINTS)
        if (
            any(setting.present for setting in settings)
            or any(name not in _CONSTRAINTS and name not in _DOCUMENTATION for name, _ in keywords)
            or type_reason(facts.type, self.imports) is not None
        ):
            return None
        value = self.nested(facts.type, seen | {symbol})
        if not constraints:
            return value
        base = (
            (None, value.name)
            if isinstance(value, BuiltinType)
            else (value.import_.from_, value.import_.import_)
            if isinstance(value, ImportedType)
            else None
        )
        if (constructor := _CONSTRUCTORS.get(base)) is None:
            return None
        return ConstructorType(ImportedType(Import(import_=constructor, from_="pydantic")), constraints)

    def nested(self, value: FinalPythonType, seen: frozenset[SymbolId]) -> FinalPythonType:
        """Return a type with each alias among its members and arguments replaced by the type it validates as."""
        match value:
            case GeneratedSymbolType() if (
                value.symbol not in seen
                and self.symbols[value.symbol].kind == "alias"
                and (facts := self.facts.get(value.symbol)) is not None
                and (plain := self.plain(value.symbol, facts, seen)) is not None
            ):
                return plain
            case GenericType():
                return replace(value, arguments=tuple(self.nested(item, seen) for item in value.arguments))
            case UnionType():
                return replace(value, members=tuple(self.nested(item, seen) for item in value.members))
            case _:
                pass
        return value

    def kind(self, value: FinalPythonType) -> ValueKind | None:
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

    def literal(self, value: FinalPythonType) -> bool:
        """Return whether a type accepts only enum members or literals, which FastAPI matches against query text."""
        if isinstance(value, UnionType | GenericType):
            members = value.members if isinstance(value, UnionType) else value.arguments
            return any(self.literal(member) for member in members)
        if isinstance(value, GeneratedSymbolType):
            return self.symbols[value.symbol].kind == "enum"
        return isinstance(value, LiteralType)

    def scalar(self, value: FinalPythonType) -> bool:
        """Return whether FastAPI reads a type as one scalar value: a builtin, enum, literal, or constrained scalar."""
        match value:
            case BuiltinType():
                return value.name in _SCALAR_BUILTINS
            case ConstructorType():
                return (value.callable.import_.from_, value.callable.import_.import_) in CONSTRAINED
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
            for key, value in (() if use is None or use.schema is None else use.documentation)
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

    def form_plans(self, value: FinalPythonType | None) -> tuple[FieldPlan, ...]:
        """Return the members a form adapter reads as text: each model field by wire name, repeated for a list."""
        members = self.members.get(value.symbol, ()) if isinstance(value, GeneratedSymbolType) else ()
        return tuple(
            FieldPlan(member.wire_name, repeated=self.kind(facts.type) == "sequence")
            for member in members
            if member.wire_name is not None and (facts := member.model_facts) is not None
        )

    def form_model(self, value: FinalPythonType | None) -> bool:
        """Return whether FastAPI reads a type as a form model: a BaseModel of scalar and repeated scalar fields."""
        return (
            self.backend == DataModelType.PydanticV2BaseModel.value
            and isinstance(value, GeneratedSymbolType)
            and self.symbols[value.symbol].kind == "model"
            and all(
                (facts := member.model_facts) is not None and self.kind(facts.type) is not None
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

    def arguments(
        self,
        operation: OperationContract,
        parameters: tuple[ParameterSpec, ...],
        body: BodySpec | None,
        wire_names: tuple[str, ...],
        *,
        secured: bool,
    ) -> tuple[Argument, ...]:
        """Name the handler's keywords in the fixed order, prefixing colliding names with their location."""
        names = self.parameter_names.get(operation.id.use_site.pointer, {})
        order = {name: index for index, name in enumerate(dict.fromkeys(wire_names))}
        path = sorted(
            (parameter for parameter in parameters if parameter.location == "path"),
            key=lambda parameter: order.get(parameter.wire_name, len(order)),
        )
        candidates = [
            _candidate(
                names,
                parameter.location,
                parameter.wire_name,
                required=parameter.required,
                native=parameter.native,
                parameter=parameter,
            )
            for parameter in (*path, *(parameter for parameter in parameters if parameter.location != "path"))
        ]
        raw = body is not None and body.decision.transport == "raw_request"
        if body is not None and not raw:
            candidates.append((
                "body",
                True,
                Argument(name="body", kind="body", location="body", required=body.required),
            ))
            if len(body.media) > 1:
                argument = Argument(name="media_type", kind="media_type", location="media_type", required=body.required)
                candidates.append(("media_type", True, argument))
        known = {f"{argument.location}:{argument.wire_name}" for _, _, argument in candidates}
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
        request = (Argument(name="request", kind="request", location="request"),)
        principal = (Argument(name="principal", kind="principal", location="principal"),)
        arguments = (
            *(request if raw or self.config.include_request else ()),
            *(principal if secured else ()),
            *_prefixed(candidates),
        )
        self.problems.extend(
            _problem(
                "F_NAME_CONFLICT", f"The arguments of {_label(operation)} take {name!r} twice", operation.id.use_site
            )
            for name, count in sorted(Counter(argument.name for argument in arguments).items())
            if count > 1
        )
        return arguments


def _is_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


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


def _member(source: object, key: str) -> WireValue | None:
    """Return a recorded JSON member of an info source as a wire value, or None when it has none."""
    return checked_wire(source[key]) if _is_mapping(source) and key in source else None


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


def symbol_imports(batch: GeneratedTypeContractBatch) -> dict[int, str]:
    """Return the `module:Name` import location of every emitted model symbol."""
    return {
        symbol.id: f"{artifact_module(symbol.artifact)}:{symbol.name}"
        for symbol in batch.symbols
        if symbol.artifact is not None
    }


def _native(plan: ParameterPlan, location: ParameterLocation, kind: ValueKind | None, *, repeated: bool) -> bool:
    """Return whether FastAPI reads a parameter's style and type natively, so no adapter reads it."""
    return (
        plan.content_media_type is None
        and kind is not None
        and kind == {"scalar": "scalar", "array": "sequence"}.get(plan.shape)
        and _STYLES.get(location) == plan.style
        and (kind != "sequence" or (location == "query" and plan.explode))
        and not (location == "path" and repeated)
    )


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


def _candidate(  # noqa: PLR0913
    names: Mapping[str, str],
    location: ArgumentLocation,
    wire_name: str,
    *,
    required: bool,
    native: NativeField | None,
    parameter: ParameterSpec | None = None,
) -> tuple[str, bool, Argument]:
    key = f"{location}:{wire_name}"
    name = names.get(key) or normalize(wire_name, empty="value", digit="p_")
    argument = Argument(
        name=name,
        kind="native" if native is not None else "adapter",
        location=location,
        wire_name=wire_name,
        required=required,
        native=native,
        parameter=parameter,
    )
    return name, key in names, argument


def _prefixed(candidates: list[tuple[str, bool, Argument]]) -> list[Argument]:
    counts = Counter(name for name, _, _ in candidates)
    return [
        replace(argument, name=f"{argument.location}_{name}")
        if not fixed and (counts[name] > 1 or name in RESERVED)
        else argument
        for name, fixed, argument in candidates
    ]


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
