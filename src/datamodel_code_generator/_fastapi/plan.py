"""Plan a FastAPI server target: names, routes, arguments, and the native or adapter handling of each boundary."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum
from functools import cached_property
from typing import TYPE_CHECKING, Final, Literal, TypeAlias, TypeVar

from datamodel_code_generator._api_types import Diagnostic
from datamodel_code_generator._codec_declarations import OperationRef
from datamodel_code_generator._fastapi.naming import normalize
from datamodel_code_generator._fastapi.native import (
    ANNOTATIONS,
    ARRAY_LENGTHS,
    BOUNDS,
    CODEC_FORMATS,
    INTEGER_RANGES,
    SHAPES,
    STRING_LENGTHS,
    GraphCheck,
    at,
    kinds,
    leaf_kind,
    number,
)
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
from datamodel_code_generator._generation_contract import (
    GeneratedSymbolType,
    GenericType,
    LiteralScalar,
)
from datamodel_code_generator._runtime.model_codecs.media import FieldPlan, media_kind, normalize_media_type
from datamodel_code_generator._runtime.model_codecs.unset import UNSET, Unset

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_types import OperationSelector
    from datamodel_code_generator._fastapi.config import FastAPIConfig, ResponseChoice
    from datamodel_code_generator._fastapi.native import Reason, Schema
    from datamodel_code_generator._generation_contract import (
        FieldUseBinding,
        FinalPythonType,
        OperationContract,
        SourceLocation,
        SymbolId,
        TypeUseBinding,
        TypeUseId,
        WireDeclaration,
    )
    from datamodel_code_generator._openapi_codec_adapters import AdapterSelection
    from datamodel_code_generator._openapi_wire_plan import WirePlan
    from datamodel_code_generator._runtime.model_codecs.media import LexicalKind, MediaKind
    from datamodel_code_generator._runtime.model_codecs.parameters import ParameterLocation, ParameterPlan
    from datamodel_code_generator._runtime.model_codecs.wire import WireValue

Site: TypeAlias = Literal["parameter", "body", "primary_response"]
Transport: TypeAlias = Literal["fastapi_native", "codec_adapter", "raw_request"]
ScalarKind: TypeAlias = Literal["str", "int", "float", "bool", "date", "aware_datetime", "uuid", "literal"]
ArgumentKind: TypeAlias = Literal["request", "native", "adapter", "body", "media_type"]
NativeApi: TypeAlias = Literal["Path", "Query", "Header", "Form", "File"]
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
_STYLES: Final = {"path": "simple", "query": "form", "header": "simple"}
_APIS: Final[dict[str, NativeApi]] = {"path": "Path", "query": "Query", "header": "Header"}
_SCALARS: Final[dict[str, ScalarKind]] = {"string": "str", "integer": "int", "number": "float", "boolean": "bool"}
_FORMAT_SCALARS: Final[dict[str, ScalarKind]] = {"date": "date", "date-time": "aware_datetime", "uuid": "uuid"}
_LEXICAL: Final[dict[str, LexicalKind]] = {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}
_DEFAULT_TYPES: Final[dict[str, tuple[type, ...]]] = {
    "str": (str,),
    "int": (int,),
    "float": (int, float),
    "bool": (bool,),
}
_SCALAR_KEYWORDS: Final = frozenset({"type", "enum", "const", *BOUNDS, *STRING_LENGTHS})
_ARRAY_KEYWORDS: Final = frozenset({"type", "items", *ARRAY_LENGTHS})
_FORM_KEYWORDS: Final = frozenset({"type", "properties", "required"})
_MIN_CONTENT_STATUS: Final = 200
_MAX_SUCCESS_STATUS: Final = 299
_DEFAULT_STATUS: Final = 200


class Default(Enum):
    """How an optional native argument behaves when the request omits it."""

    REQUIRED = "required"
    ABSENT = "absent"


@dataclass(frozen=True, slots=True, kw_only=True)
class Decision:
    """How one parameter, body, or primary response is handled, why, and where the reason comes from."""

    site: Site
    transport: Transport
    reason: Reason
    source: SourceLocation | None = None
    uses: tuple[TypeUseId, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Scalar:
    """The native surface of a scalar: a builtin, date, aware datetime, UUID, or a Literal of values."""

    kind: ScalarKind
    values: tuple[str | int | float | bool, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class NativeField:
    """A native Path, Query, Header, Form, or File declaration and the FieldInfo keywords it projects."""

    api: NativeApi
    alias: str
    scalar: Scalar | None
    array: bool = False
    keywords: tuple[tuple[str, object], ...] = ()
    items: tuple[tuple[str, object], ...] = ()
    default: object = Default.REQUIRED


@dataclass(frozen=True, slots=True, kw_only=True)
class MediaSpec:
    """One declared media type of a body or response, with its type use when the content has a schema."""

    media_type: str
    kind: MediaKind
    use: TypeUseBinding | None
    declaration: WireDeclaration


@dataclass(frozen=True, slots=True, kw_only=True)
class ParameterSpec:
    """One effective parameter, its wire plan, and how the server receives it."""

    location: ParameterLocation
    wire_name: str
    required: bool
    use: TypeUseBinding | None
    plan: ParameterPlan | None
    decision: Decision
    native: NativeField | None = None
    default: WireValue | Unset = UNSET


@dataclass(frozen=True, slots=True, kw_only=True)
class FormField:
    """One property of a native form or multipart body."""

    name: str
    required: bool
    native: NativeField


@dataclass(frozen=True, slots=True, kw_only=True)
class BodySpec:
    """A request body: its media, requiredness, decision, and the properties or plans it is read with."""

    required: bool
    media: tuple[MediaSpec, ...]
    decision: Decision
    fields: tuple[FormField, ...] = ()
    form_fields: tuple[FieldPlan, ...] = ()
    form_additional: FieldPlan | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class HeaderSpec:
    """One effective declared response header, with the plan and type use that validate its value."""

    name: str
    required: bool
    use: TypeUseBinding | None
    plan: ParameterPlan | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ResponseSpec:
    """One declared response with its media and headers."""

    status: str
    declaration: WireDeclaration
    media: tuple[MediaSpec, ...]
    headers: tuple[HeaderSpec, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class PrimarySpec:
    """The response a bare return value takes, and how FastAPI sends it."""

    status: int
    response: ResponseSpec
    media: MediaSpec | None
    decision: Decision


@dataclass(frozen=True, slots=True, kw_only=True)
class Argument:
    """One keyword the handler receives, in the fixed argument order."""

    name: str
    kind: ArgumentKind
    location: str
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

    def decisions(self) -> Iterator[Decision]:
        """Yield the operation's decisions in parameter, body, and primary response order."""
        yield from (parameter.decision for parameter in self.parameters)
        if self.body is not None:
            yield self.body.decision
        if self.primary is not None:
            yield self.primary.decision


@dataclass(frozen=True, slots=True, kw_only=True)
class GroupSpec:
    """One router group: its key, file stem, first tag, and operations in declaration order."""

    key: str
    stem: str
    primary_tag: str | None
    operations: tuple[OperationSpec, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ServerPlan:
    """The planned operations and groups of one server target."""

    operations: tuple[OperationSpec, ...]
    groups: tuple[GroupSpec, ...]


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


def _label(operation: OperationContract) -> str:
    return f"{operation.method.upper()} {operation.path}"


def _uses(declaration: WireDeclaration) -> tuple[TypeUseId, ...]:
    return (*declaration.schemas, *(use for child in declaration.children for use in child.schemas))


class Planner:  # noqa: PLR0904
    """Plan every selected operation of one server target from the accepted batch and its wire plan."""

    def __init__(
        self, request: TargetRequest, config: FastAPIConfig, wire: WirePlan, selection: AdapterSelection
    ) -> None:
        """Index the batch, and resolve the per-operation settings to operation keys."""
        self.request = request
        self.config = config
        self.wire = wire
        self.adapted = frozenset(use for _, use in selection.chosen)
        self.uses = {use.id: use for use in request.batch.type_uses}
        self.symbols = {symbol.id: symbol for symbol in request.batch.symbols}
        self.members: dict[SymbolId, list[FieldUseBinding]] = {}
        for member in request.batch.fields:
            self.members.setdefault(member.consumer, []).append(member)
        self.facts = {
            member.consumer: member.model_facts
            for member in reversed(request.batch.fields)
            if member.model_facts is not None
        }
        self.parameter_plans = {
            operation: {(plan.location, plan.name): plan for plan in plans} for operation, plans in wire.parameters
        }
        self.header_plans = dict(wire.headers)
        self.backend = request.model_config.output_model_type.value
        self.problems: list[Diagnostic] = []
        self.names = self.selected("operation_names", config.operation_names)
        self.body_modes = self.selected("body_modes", config.body_modes)
        self.primaries = self.selected("primary_responses", config.primary_responses)
        self.parameter_names = self.selected("parameter_names", config.parameter_names)
        self.raise_problems()

    @cached_property
    def request_check(self) -> GraphCheck:
        """Return the model-graph check of request bodies, indexed once per target."""
        return GraphCheck(self.symbols, self.members, self.wire, "request", self.backend)

    @cached_property
    def response_check(self) -> GraphCheck:
        """Return the model-graph check of primary responses, indexed once per target."""
        return GraphCheck(self.symbols, self.members, self.wire, "response", self.backend)

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
                message = f"The {option} entry {reference.pointer!r} selects no root path operation"
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
            _problem("F_NAME_CONFLICT", f"Several operations or reserved files take {name!r}")
            for name in sorted(stem_conflicts(names.values()))
        )
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
        return ServerPlan(operations=specs, groups=groups)

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
            _problem("F_NAME_CONFLICT", f"Several router groups or reserved files take {stem!r}")
            for stem in sorted(stem_conflicts(stems.values()))
        )
        return tuple(
            GroupSpec(
                key=key,
                stem=stems[key],
                primary_tag=key.removeprefix("tag:") if key.startswith("tag:") else None,
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
        arguments = self.arguments(operation, parameters, body, names)
        return OperationSpec(
            contract=operation,
            python_name=name,
            pascal=pascal(name),
            group=group_key(operation, single=self.config.layout == "single"),
            route=self.route(operation, arguments, wire_names),
            parameters=parameters,
            body=body,
            responses=responses,
            primary=primary,
            registration_status=_registration(responses, primary),
            arguments=arguments,
        )

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

    def parameter(
        self, operation: OperationContract, declaration: WireDeclaration, repeated: set[str]
    ) -> ParameterSpec:
        """Decide how the server receives one effective parameter."""
        location = _LOCATIONS[fact(declaration, "in")]
        name = declaration.name or ""
        uses = _uses(declaration)
        use = self.use(uses)
        plan = self.parameter_plans.get(operation.id, {}).get((location, name))
        spec = ParameterSpec(
            location=location,
            wire_name=name,
            required=fact(declaration, "required") is True,
            use=use,
            plan=plan,
            decision=Decision(site="parameter", transport="codec_adapter", reason="unsupported_wire_shape", uses=uses),
            default=self.default(use),
        )
        if any(item in self.adapted for item in uses):
            return replace(spec, decision=replace(spec.decision, reason="explicit_adapter"))
        if plan is None or use is None or not _natively_serialized(plan, location, repeated=name in repeated):
            return spec
        native, reason, source = self.native_parameter(declaration, use, plan)
        if native is None:
            return replace(spec, decision=replace(spec.decision, reason=reason, source=source))
        decision = replace(spec.decision, transport="fastapi_native", reason="native_supported")
        return replace(spec, native=native, decision=decision)

    def default(self, use: TypeUseBinding | None) -> WireValue | Unset:
        """Return the schema default an omitted adapter parameter decodes, if the schema declares one."""
        if use is None or use.schema is None:
            return UNSET
        _, schema = self.wire.schema(use.schema)
        return schema.get("default", UNSET)

    def native_parameter(
        self, declaration: WireDeclaration, use: TypeUseBinding, plan: ParameterPlan
    ) -> tuple[NativeField | None, Reason, SourceLocation | None]:
        """Return the native declaration of a builtin-typed parameter, or the reason it needs the adapter."""
        bound = self.bound(use.type)
        if use.schema is None or bound is False:
            return None, "opaque_native_semantics", use.schema
        location, schema = self.wire.schema(use.schema)
        return self.native_value(
            declaration, location, schema, bound, api=_APIS[plan.location], alias=plan.name, required=plan.required
        )

    def native_value(  # noqa: PLR0913
        self,
        declaration: WireDeclaration | None,
        location: SourceLocation,
        schema: Schema,
        bound: str | None,
        *,
        api: NativeApi,
        alias: str,
        required: bool,
    ) -> tuple[NativeField | None, Reason, SourceLocation | None]:
        """Return the native declaration of a scalar or one-level list value, or why the adapter reads it."""
        array = (kinds(schema) or frozenset()) - {"null"} == frozenset({"array"})
        item_location, item = self.wire.schema(at(location, "items")) if array else (location, schema)
        scalar, reason = self.scalar(item, bound)
        if scalar is None:
            return None, reason, item_location
        checks = ((_ARRAY_KEYWORDS, schema, location), (_SCALAR_KEYWORDS, item, item_location)) if array else ()
        for allowed, value, value_location in checks or ((_SCALAR_KEYWORDS, schema, location),):
            if (extra := _extra(value, allowed)) is not None:
                return None, extra[0], at(value_location, extra[1])
        keywords = dict(_documentation(declaration, schema))
        items = _constraints(item, scalar)
        if array:
            keywords.update({name: schema[source] for source, name in ARRAY_LENGTHS.items() if source in schema})
        else:
            keywords.update(items)
            items = {}
            if scalar.kind == "float":
                keywords["allow_inf_nan"] = False
        default: object = Default.REQUIRED if required else Default.ABSENT
        if "default" in schema and not required:
            if not _typed_default(schema["default"], scalar, array=array):
                return None, "source_assertion_not_projected", at(location, "default")
            default = schema["default"]
        field = NativeField(
            api=api,
            alias=alias,
            scalar=scalar,
            array=array,
            keywords=tuple(keywords.items()),
            items=tuple(items.items()),
            default=default,
        )
        return field, "native_supported", None

    def bound(self, value: FinalPythonType | None) -> str | Literal[False] | None:
        """Return the builtin leaf a parameter's final type projects to, False for opaque types, None if unknown."""
        seen: set[int] = set()
        while isinstance(value, GeneratedSymbolType) and value.symbol not in seen:
            seen.add(value.symbol)
            symbol = self.symbols[value.symbol]
            if symbol.kind == "enum":
                return "literal"
            facts = self.facts.get(symbol.id)
            if symbol.kind not in {"root", "alias"} or facts is None:
                return False
            value = facts.type
        if value is None:
            return None
        if isinstance(value, GenericType) and len(value.arguments) == 1:
            return self.bound(value.arguments[0])
        kind, _ = leaf_kind(value)
        return kind if kind is not None and kind != "any" else False

    @staticmethod
    def scalar(schema: Schema, bound: str | None) -> tuple[Scalar | None, Reason]:
        """Return the native scalar surface of a value schema, or why a builtin declaration cannot read it."""
        present = (kinds(schema) or frozenset()) - {"null"}
        values = schema.get("enum", (schema["const"],) if "const" in schema else None)
        if isinstance(values, tuple):
            present_values = [value for value in values if value is not None]
            literals = tuple(value for value in present_values if isinstance(value, (str, int, float, bool)))
            if not literals or len(literals) != len(present_values):
                return None, "native_shape_mismatch"
            return Scalar(kind="literal", values=literals), "native_supported"
        if len(present) != 1 or (kind := _SCALARS.get(next(iter(present)))) is None:
            return None, "native_shape_mismatch"
        format_ = schema.get("format")
        if kind == "str" and isinstance(format_, str) and format_ in _FORMAT_SCALARS:
            kind = _FORMAT_SCALARS[format_]
        elif kind == "str" and format_ in CODEC_FORMATS:
            return None, "source_assertion_not_projected"
        if bound not in {None, "literal", kind} and not (kind == "float" and bound == "int"):
            reason: Reason = (
                "source_assertion_not_projected" if kind in _FORMAT_SCALARS.values() else "native_shape_mismatch"
            )
            return None, reason
        return Scalar(kind=kind), "native_supported"

    def body(self, operation: OperationContract, declaration: WireDeclaration) -> BodySpec:
        """Decide how the server receives a request body."""
        media = tuple(self.media(child) for child in declaration.children if child.kind == "media")
        uses = tuple(item.use.id for item in media if item.use is not None)
        spec = BodySpec(
            required=fact(declaration, "required") is True,
            media=media,
            decision=Decision(site="body", transport="codec_adapter", reason="unsupported_wire_shape", uses=uses),
        )
        if self.body_modes.get(operation.id.use_site.pointer, self.config.body_mode) == "request":
            return replace(spec, decision=replace(spec.decision, transport="raw_request", reason="explicit_raw"))
        if any(use in self.adapted for use in uses):
            return replace(spec, decision=replace(spec.decision, reason="explicit_adapter"))
        if len(media) == 1 and media[0].kind in {"form", "multipart"}:
            return self.form_body(operation, spec, media[0])
        self.problems.extend(
            _problem(
                "F_MEDIA_UNSUPPORTED",
                f"{_label(operation)} needs body_mode='request' for {item.media_type}",
                item.declaration.use_site,
            )
            for item in media
            if item.kind == "multipart"
        )
        if len(media) == 1 and media[0].kind == "json" and media[0].use is not None:
            return replace(spec, decision=self.json_body(media[0], required=spec.required))
        return spec

    def media(self, declaration: WireDeclaration) -> MediaSpec:
        """Return one media declaration with its normalized type, kind, and type use."""
        media_type = normalize_media_type(declaration.name or "")
        return MediaSpec(
            media_type=media_type,
            kind=media_kind(media_type),
            use=self.use(declaration.schemas),
            declaration=declaration,
        )

    def json_body(self, media: MediaSpec, *, required: bool) -> Decision:
        """Decide whether FastAPI's Body reads a JSON body natively."""
        assert media.use is not None
        decision = Decision(
            site="body", transport="codec_adapter", reason="native_required_mismatch", uses=(media.use.id,)
        )
        if not required:
            return replace(decision, source=media.declaration.use_site)
        if media.use.schema is not None and "null" in (kinds(self.wire.schema(media.use.schema)[1]) or ()):
            return replace(decision, reason="native_nullable_mismatch", source=media.use.schema)
        reason, source = self.request_check.check(media.use)
        if reason == "native_supported":
            return replace(decision, transport="fastapi_native", reason=reason)
        return replace(decision, reason=reason, source=source)

    def form_body(self, operation: OperationContract, spec: BodySpec, media: MediaSpec) -> BodySpec:
        """Decide whether FastAPI's Form and File read a form body natively, or which adapter reads it."""
        fields, reason, source = self.form_fields(media)
        if fields is not None and spec.required:
            native = replace(spec.decision, transport="fastapi_native", reason=reason)
            return replace(spec, fields=fields, decision=native)
        if media.kind == "multipart" or media.use is None:
            self.problems.append(
                _problem(
                    "F_MEDIA_UNSUPPORTED",
                    f"{_label(operation)} needs body_mode='request' for {media.media_type}",
                    media.declaration.use_site,
                )
            )
        if fields is not None:
            reason, source = "native_required_mismatch", media.declaration.use_site
        form_fields, additional = self.form_plans(media)
        decision = replace(spec.decision, reason=reason, source=source)
        return replace(spec, decision=decision, form_fields=form_fields, form_additional=additional)

    def form_fields(self, media: MediaSpec) -> tuple[tuple[FormField, ...] | None, Reason, SourceLocation | None]:
        """Return the native fields of a flat form schema, or why FastAPI's Form cannot read it."""
        if media.use is None or media.use.schema is None:
            return None, "unsupported_wire_shape", media.declaration.use_site
        location, schema = self.wire.schema(media.use.schema)
        if kinds(schema) not in {None, frozenset({"object"})} or schema.get("additionalProperties", True) is not True:
            return None, "native_shape_mismatch", location
        if (extra := _extra(schema, _FORM_KEYWORDS)) is not None:
            return None, extra[0], at(location, extra[1])
        if media.kind == "multipart" and any(
            child.kind == "encoding" and (child.children or fact(child, "contentType") is not None)
            for child in media.declaration.children
        ):
            return None, "unsupported_wire_shape", media.declaration.use_site
        properties = schema.get("properties")
        required = schema.get("required")
        names: frozenset[str] = (
            frozenset(str(name) for name in required) if isinstance(required, tuple) else frozenset()
        )
        declared: Mapping[str, WireValue] = properties if isinstance(properties, Mapping) else {}
        fields: list[FormField] = []
        for name in declared:
            field_location, value = self.wire.schema(at(location, "properties", name))
            native, reason, source = self.form_field(
                name, field_location, value, required=name in names, multipart=media.kind == "multipart"
            )
            if native is None:
                return None, reason, source
            fields.append(FormField(name=name, required=name in names, native=native))
        return tuple(fields), "native_supported", None

    def form_field(
        self, name: str, location: SourceLocation, schema: Schema, *, required: bool, multipart: bool
    ) -> tuple[NativeField | None, Reason, SourceLocation | None]:
        """Return one native form property, or the reason it needs an adapter."""
        array = (kinds(schema) or frozenset()) - {"null"} == frozenset({"array"})
        _, item = self.wire.schema(at(location, "items")) if array else (location, schema)
        if (
            multipart
            and item.get("format") == "binary"
            and (kinds(item) or frozenset()) - {"null"} == frozenset({"string"})
        ):
            default = Default.REQUIRED if required else Default.ABSENT
            field = NativeField(api="File", alias=name, scalar=None, array=array, default=default)
            return field, "native_supported", None
        return self.native_value(None, location, schema, None, api="Form", alias=name, required=required)

    def form_plans(self, media: MediaSpec) -> tuple[tuple[FieldPlan, ...], FieldPlan | None]:
        """Return the URL-encoded field plans a form adapter decodes the body with."""
        if media.use is None or media.use.schema is None:
            return (), FieldPlan("", "string")
        location, schema = self.wire.schema(media.use.schema)
        properties = schema.get("properties")
        declared: Mapping[str, WireValue] = properties if isinstance(properties, Mapping) else {}
        fields = tuple(self.form_plan(name, *self.wire.schema(at(location, "properties", name))) for name in declared)
        additional = schema.get("additionalProperties", True)
        if additional is False:
            return fields, None
        if isinstance(additional, Mapping) and additional:
            return fields, self.form_plan("", *self.wire.schema(at(location, "additionalProperties")))
        return fields, FieldPlan("", "string")

    def form_plan(self, name: str, location: SourceLocation, schema: Schema) -> FieldPlan:
        """Return the lexical kind of one URL-encoded member and whether it repeats."""
        if (kinds(schema) or frozenset()) - {"null"} == frozenset({"array"}):
            return FieldPlan(name, _lexical(self.wire.schema(at(location, "items"))[1]), repeated=True)
        return FieldPlan(name, _lexical(schema))

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
        headers = tuple(
            HeaderSpec(
                name=child.name or "",
                required=fact(child, "required") is True,
                use=(use := self.use(_uses(child))),
                plan=None if use is None else self.header_plans.get(use.id),
            )
            for child in declaration.children
            if child.kind == "header"
        )
        status = declaration.name or "default"
        return ResponseSpec(
            status=status if status == "default" else status.upper(),
            declaration=declaration,
            media=media,
            headers=headers,
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
        media = _default_media(response)
        decision = self.primary_decision(operation, status, response, media)
        return PrimarySpec(status=status, response=response, media=media, decision=decision)

    def chosen(
        self, operation: OperationContract, exact: dict[int, ResponseSpec], choice: ResponseChoice
    ) -> PrimarySpec | None:
        """Return the explicitly chosen primary response, reporting a choice that names no declared response."""
        response = exact.get(choice.status_code)
        media = None if response is None else _default_media(response)
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
        decision = self.primary_decision(operation, choice.status_code, response, media)
        return PrimarySpec(status=choice.status_code, response=response, media=media, decision=decision)

    def primary_decision(
        self, operation: OperationContract, status: int, response: ResponseSpec, media: MediaSpec | None
    ) -> Decision:
        """Decide whether a bare primary value goes to FastAPI's response_model or through the codec adapter."""
        bodyless = operation.method == "head" or status in BODYLESS_STATUSES or status < _MIN_CONTENT_STATUS
        use = None if bodyless or media is None else media.use
        decision = Decision(
            site="primary_response",
            transport="codec_adapter",
            reason="unsupported_wire_shape",
            source=response.declaration.use_site,
            uses=() if use is None else (use.id,),
        )
        if use is not None and use.id in self.adapted:
            return replace(decision, reason="explicit_adapter", source=None)
        if (
            use is None
            or media is None
            or media.media_type != _JSON
            or any(header.required for header in response.headers)
        ):
            return decision
        reason, source = self.response_check.check(use)
        if reason == "native_supported":
            return replace(decision, transport="fastapi_native", reason=reason, source=None)
        return replace(decision, reason=reason, source=source)

    def arguments(
        self,
        operation: OperationContract,
        parameters: tuple[ParameterSpec, ...],
        body: BodySpec | None,
        wire_names: tuple[str, ...],
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
            candidates.extend(
                _candidate(
                    names,
                    "file" if field.native.api == "File" else "form",
                    field.name,
                    required=field.required,
                    native=field.native,
                )
                for field in body.fields
            )
            if not body.fields:
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
        arguments = _prefixed(candidates)
        self.problems.extend(
            _problem(
                "F_NAME_CONFLICT", f"The arguments of {_label(operation)} take {name!r} twice", operation.id.use_site
            )
            for name, count in sorted(Counter(argument.name for argument in arguments).items())
            if count > 1
        )
        if raw or self.config.include_request:
            return (Argument(name="request", kind="request", location="request"), *arguments)
        return tuple(arguments)


def _natively_serialized(plan: ParameterPlan, location: ParameterLocation, *, repeated: bool) -> bool:
    match plan.shape:
        case "object":
            return False
        case "array" if location != "query" or not plan.explode:
            return False
        case _:
            pass
    return (
        plan.content_media_type is None
        and _STYLES.get(location) == plan.style
        and not (location == "path" and repeated)
    )


def _candidate(  # noqa: PLR0913
    names: Mapping[str, str],
    location: str,
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


def _default_media(response: ResponseSpec) -> MediaSpec | None:
    return (
        next((item for item in response.media if item.media_type == _JSON), None)
        or next((item for item in response.media if item.media_type.partition(";")[0].endswith("+json")), None)
        or next(iter(response.media), None)
    )


def _extra(schema: Schema, allowed: Iterable[str]) -> tuple[Reason, str] | None:
    permitted = frozenset(allowed)
    return next(
        (
            ("native_shape_mismatch" if keyword in SHAPES else "source_assertion_not_projected", keyword)
            for keyword in schema
            if keyword not in permitted and keyword not in ANNOTATIONS and not keyword.startswith("x-")
        ),
        None,
    )


def _documentation(declaration: WireDeclaration | None, schema: Schema) -> Iterator[tuple[str, object]]:
    if isinstance(title := schema.get("title"), str):
        yield "title", title
    description = None if declaration is None else fact(declaration, "description")
    if isinstance(description, str) or isinstance(description := schema.get("description"), str):
        yield "description", description
    if (declaration is not None and fact(declaration, "deprecated") is True) or schema.get("deprecated") is True:
        yield "deprecated", True
    if isinstance(examples := schema.get("examples"), tuple) and examples:
        yield "examples", examples


def _constraints(schema: Schema, scalar: Scalar) -> dict[str, object]:
    keywords: dict[str, object] = {
        name: value for source, name in BOUNDS.items() if number(value := schema.get(source))
    }
    if scalar.kind == "str":
        keywords.update({name: schema[source] for source, name in STRING_LENGTHS.items() if source in schema})
    if scalar.kind == "int" and (limits := INTEGER_RANGES.get(str(schema.get("format")))) is not None:
        low, high = limits
        lower = next(((name, bound) for name in ("ge", "gt") if number(bound := keywords.pop(name, None))), None)
        upper = next(((name, bound) for name in ("le", "lt") if number(bound := keywords.pop(name, None))), None)
        keywords.update((lower,) if lower is not None and Decimal(str(lower[1])) >= low else (("ge", low),))
        keywords.update((upper,) if upper is not None and Decimal(str(upper[1])) <= high else (("le", high),))
    return keywords


def _lexical(schema: Schema) -> LexicalKind:
    present = (kinds(schema) or frozenset()) - {"null"}
    if len(present) != 1 or (kind := _SCALARS.get(next(iter(present)))) is None:
        return "string"
    return _LEXICAL.get(kind, "string")


def _typed_default(value: WireValue, scalar: Scalar, *, array: bool) -> bool:
    if array:
        return isinstance(value, tuple) and all(_typed_default(item, scalar, array=False) for item in value)
    if scalar.kind == "literal":
        return isinstance(value, (str, int, float, bool)) and value in scalar.values
    types = _DEFAULT_TYPES.get(scalar.kind)
    return types is not None and isinstance(value, types) and (scalar.kind == "bool" or not isinstance(value, bool))
