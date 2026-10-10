"""Plan a client target: the resource, method, arguments, media, responses, and servers of each operation."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, replace
from functools import cached_property
from itertools import starmap
from typing import TYPE_CHECKING, Final, Literal, TypeAlias
from urllib.parse import urljoin, urlsplit

from typing_extensions import TypeIs

from datamodel_code_generator._api_types import Diagnostic, OperationRef
from datamodel_code_generator._client.config import absolute
from datamodel_code_generator._client.model_facts import ALIASES
from datamodel_code_generator._client.naming import HELPER_ARGUMENTS, RESERVED_ARGUMENTS, RESERVED_MEMBERS
from datamodel_code_generator._client.security import CredentialSpec, SecurityPlanner
from datamodel_code_generator._openapi_wire_plan import parameter_plans, property_members
from datamodel_code_generator._runtime.client.media import most_specific
from datamodel_code_generator._runtime.client.multipart import PartPlan
from datamodel_code_generator._runtime.model_codecs.media import media_kind, normalize_media_type
from datamodel_code_generator._target_contract import (
    BuiltinType,
    ConstructorType,
    LiteralScalar,
    LiteralSequence,
    NoneType,
    PartFacts,
    PartSchema,
    SchemaSite,
    SourceLocation,
    TypeUseBinding,
    UnionType,
)
from datamodel_code_generator._target_naming import WINDOWS_DEVICES, NameScope, explicit_name, operation_basis

if TYPE_CHECKING:
    from collections.abc import Container, Iterable, Iterator, Mapping

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._client.config import (
        BodyArguments,
        BodyFieldName,
        ClientGenerationConfig,
        ClientOperationConfig,
        IdempotencyMetadata,
    )
    from datamodel_code_generator._client.model_facts import ModelFacts
    from datamodel_code_generator._openapi_wire_plan import WirePlan
    from datamodel_code_generator._runtime.client.multipart import PartKind
    from datamodel_code_generator._runtime.client.security import SecurityBinding, SecuritySchemeEntry
    from datamodel_code_generator._runtime.model_codecs.media import FieldPlan, MediaKind
    from datamodel_code_generator._runtime.model_codecs.parameters import ParameterLocation, ParameterPlan
    from datamodel_code_generator._target_contract import (
        Direction,
        FrozenLiteral,
        ModelFieldFacts,
        OperationContract,
        OperationId,
        SourceDocumentId,
        TypeUseId,
        TypeView,
        WireDeclaration,
    )

Role: TypeAlias = Literal["success", "error"]

BODYLESS_STATUSES: Final = frozenset({204, 205, 304})
_LOCATIONS: Final[dict[object, ParameterLocation]] = {
    "path": "path",
    "query": "query",
    "querystring": "querystring",
    "header": "header",
    "cookie": "cookie",
}
_PLACEHOLDER: Final = re.compile(r"\{([^{}]*)\}")
_SCHEMES: Final = frozenset({"http", "https"})
_FORM_DATA: Final = "multipart/form-data"
_NULL: Final = frozenset({"null"})
_UNDECLARED: Final = PartFacts(object=True, members=(), extra=None)
_MIN_SUCCESS: Final = 200
_MAX_SUCCESS: Final = 299
_MIN_ERROR: Final = 400
_MAX_ERROR: Final = 599
_CONFIG_CODES: Final = frozenset({"E_CONFIG_VALUE", "E_CONFIG_CONFLICT", "E_OPERATION_REF"})
_STYLED: Final = ("style", "explode", "allowReserved")
_DEFAULTS: Final = {"bool": ("bool",), "int": ("int",), "float": ("int", "float"), "str": ("str",)}


@dataclass(frozen=True, slots=True, kw_only=True)
class PartSpec:
    """One member of a form-data body with file parts: its plan, its values' type use, and its kind of part.

    A file member has no type use of its values. A received member may be required, or excluded by its direction.
    """

    plan: PartPlan
    use: TypeUseBinding | None = None
    file: bool = False
    required: bool = False
    excluded: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class MediaSpec:
    """One declared media type of a body or response, with its type use when its content has a schema.

    A form-data body or response whose schema has file parts is sent or read as parts: `members` plans each
    declared member, and `extra` any other part, which no plan allows when the schema allows no other properties.
    """

    media_type: str
    kind: MediaKind
    use: TypeUseBinding | None
    fields: tuple[FieldPlan, ...] = ()
    additional: FieldPlan | None = None
    encoded: tuple[ParameterPlan, ...] = ()
    parts: tuple[PartPlan, ...] = ()
    additional_part: PartPlan | None = None
    members: tuple[PartSpec, ...] | None = None
    extra: PartSpec | None = None
    content_types: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class ParameterSpec:
    """One effective parameter: its location and names, requiredness, type use, and wire plan.

    `argument` is the type its argument takes: the model type, with each alias and root model by the type it stands
    for. A call `converts` an argument that stands for a root model into it before it encodes it, and sends any other
    as given. `default` is the schema's default of a builtin scalar argument.
    """

    location: ParameterLocation
    wire_name: str
    python_name: str
    required: bool
    use: TypeUseBinding | None
    plan: ParameterPlan
    argument: TypeView | None = None
    default: LiteralScalar | None = None
    converts: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class BodySpec:
    """A request body: its declared media, the media sent when a call names none, and whether it is required."""

    required: bool
    media: tuple[MediaSpec, ...]
    default: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class HeaderSpec:
    """One effective declared header of a response or a member's parts, with the plan and use that decode its value."""

    name: str
    required: bool
    use: TypeUseBinding | None
    plan: ParameterPlan


@dataclass(frozen=True, slots=True, kw_only=True)
class ResponseSpec:
    """One declared response: its status key, media, headers, and whether it can be a success or an error."""

    status: str
    media: tuple[MediaSpec, ...]
    headers: tuple[HeaderSpec, ...]
    success: bool
    error: bool
    bodyless: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class ServerSpec:
    """One effective server: an absolute URL template and its variables with their defaults and allowed values."""

    url: str
    variables: tuple[tuple[str, str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class FieldArgument:
    """One field of a body that a call may give as its own keyword argument, typed as its final field type."""

    python_name: str
    wire_name: str
    required: bool
    type: TypeView


@dataclass(frozen=True, slots=True, kw_only=True)
class FieldBranch:
    """The fields of one request media type's body that a call may give instead of the body."""

    media_type: str
    fields: tuple[FieldArgument, ...]

    @property
    def required(self) -> bool:
        """Return whether a call giving these fields must give some of them."""
        return any(field.required for field in self.fields)


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationSpec:
    """Everything the renderer needs for one selected operation.

    `body_only` pairs each body media type of a 'both' operation that has no field arguments with the reason, and
    `scopes` names the scopes its arguments live in for the naming strategy: its resource and its derived method name.
    """

    contract: OperationContract
    index: int
    resource: str
    name: str
    pascal: str
    operation_id: str | None
    parameters: tuple[ParameterSpec, ...]
    body: BodySpec | None
    responses: tuple[ResponseSpec, ...]
    servers: tuple[ServerSpec, ...]
    success_statuses: tuple[int, ...]
    request_id_header: str | None
    response_media_type: str | None
    description: str | None
    body_arguments: BodyArguments = "body"
    body_field_names: tuple[BodyFieldName, ...] = ()
    fields: tuple[FieldBranch, ...] = ()
    body_only: tuple[tuple[str, str], ...] = ()
    retry_safety: Literal["method_default", "idempotent", "never"] = "method_default"
    idempotency: IdempotencyMetadata | None = None
    retry_after_ms_header: str | None = None
    should_retry_header: str | None = None
    security: SecurityBinding | None = None
    auth_challenge_less_401: bool = False
    accepted_content_encodings: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()

    @property
    def head(self) -> bool:
        """Return whether the operation is a HEAD operation, whose responses have no body."""
        return self.contract.method == "head"

    @property
    def field_names(self) -> tuple[str, ...]:
        """Return the field arguments of the operation's method: every media's field names, first seen first."""
        return tuple(dict.fromkeys(field.python_name for branch in self.fields for field in branch.fields))


@dataclass(frozen=True, slots=True, kw_only=True)
class ResourceSpec:
    """One resource namespace: its operations in operation order and its direct child namespaces."""

    namespace: str
    operations: tuple[OperationSpec, ...]
    children: tuple[str, ...]
    pascal: str

    @property
    def parts(self) -> tuple[str, ...]:
        """Return the namespace's dotted parts."""
        return tuple(self.namespace.split("."))


@dataclass(frozen=True, slots=True, kw_only=True)
class ClientPlan:
    """The planned operations of one client target and its resource namespaces in first-appearance order."""

    operations: tuple[OperationSpec, ...]
    resources: tuple[ResourceSpec, ...]
    security_schemes: tuple[SecuritySchemeEntry, ...] = ()
    credentials: tuple[CredentialSpec, ...] = ()

    @property
    def roots(self) -> tuple[ResourceSpec, ...]:
        """Return the top-level resources, which the clients expose as attributes."""
        return tuple(resource for resource in self.resources if "." not in resource.namespace)


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
        stage="config" if code in _CONFIG_CODES else "target",
        message=message,
        source_pointer=None if source is None else source.pointer,
        option_path=option_path,
    )


def _label(operation: OperationContract) -> str:
    return f"{operation.method.upper()} {operation.path}"


def _uses(declaration: WireDeclaration) -> tuple[TypeUseId, ...]:
    return (*declaration.schemas, *(use for child in declaration.children for use in child.schemas))


def _plain(value: FrozenLiteral) -> object:
    match value:
        case LiteralScalar():
            return value.value
        case LiteralSequence():
            return tuple(_plain(item) for item in value.items)
        case _:
            pass
    return {_plain(key): _plain(item) for key, item in value.entries}


def _is_tuple(value: object) -> TypeIs[tuple[object, ...]]:
    return isinstance(value, tuple)


def _is_dict(value: object) -> TypeIs[dict[object, object]]:
    return isinstance(value, dict)


def _strings(value: object) -> TypeIs[tuple[str, ...]]:
    return _is_tuple(value) and all(isinstance(item, str) for item in value)


def _tags(operation: OperationContract) -> tuple[str, ...]:
    tags = next((_plain(value) for key, value in operation.facts if key == "tags"), ())
    return tuple(tag for tag in tags if isinstance(tag, str)) if _is_tuple(tags) else ()


class Planner:
    """Plan every selected operation of one client target from the accepted batch and its wire plan."""

    def __init__(
        self,
        request: TargetRequest,
        config: ClientGenerationConfig,
        wire: WirePlan,
        facts: ModelFacts,
        helpers: Container[OperationId] = (),
    ) -> None:
        """Index the batch and the wire plan, and resolve the per-operation settings to operation keys.

        `helpers` are the operations a sending helper calls, whose methods also take the helper's own arguments.
        """
        assert request.batch.names is not None
        self.names = request.batch.names
        self.helpers = helpers
        self.request = request
        self.config = config
        self.wire = wire
        self.facts = facts
        self.uses = {use.id: use for use in request.batch.type_uses}
        self.parameter_plans = parameter_plans(wire)
        self.header_plans = dict(wire.headers)
        self.forms = {use: (fields, additional, encoded) for use, fields, additional, encoded in wire.forms}
        self.styles = {use: {plan.name: plan for plan in plans} for use, plans in wire.styles}
        self.documents = {document.id: document.uri for document in request.batch.documents}
        self.resource_names = {item.tag: item.namespace for item in config.resource_names}
        self.problems: list[Diagnostic] = []
        self.security = SecurityPlanner(request.batch, self.problems)
        self.settings = self.resolved()
        self.raise_problems()
        self.namespaces = self.tag_namespaces()

    @cached_property
    def _schemas(self) -> dict[tuple[SourceDocumentId, str, Direction], TypeUseBinding]:
        """Return the value use of each schema occurrence by location and direction, which a body's part is bound as."""
        return schema_uses(self.request.batch.type_uses)

    def raise_problems(self) -> None:
        """Stop the phase when it reported any failure."""
        if self.problems:
            raise PlanError(self.problems)

    def resolved(self) -> dict[str, ClientOperationConfig]:
        """Resolve each operation setting's reference to its operation key."""
        resolved: dict[str, ClientOperationConfig] = {}
        for index, item in enumerate(self.config.operations):
            reference = OperationRef(pointer=item.ref) if isinstance(item.ref, str) else item.ref
            option = f"operations[{index}].ref"
            if (operation := self.request.resolve(reference)) is None:
                from datamodel_code_generator._target_documents import named_document  # noqa: PLC0415

                named = named_document(reference.document, reference.document)
                message = f"The operation setting {reference.pointer!r}{named} {self.request.unresolved}"
                self.problems.append(_problem("E_OPERATION_REF", message, option_path=option))
            elif (key := operation.id.use_site.pointer) in resolved:
                self.problems.append(_problem("E_CONFIG_CONFLICT", f"Two settings name {key!r}", option_path=option))
            else:
                resolved[key] = item
        return resolved

    def plan(self) -> ClientPlan:
        """Plan each selected operation, then its resource namespaces and the names they must keep apart."""
        specs = self.named(tuple(starmap(self.operation, enumerate(self.request.operations))))
        self.raise_problems()
        resources = self.resources(specs)
        credentials = self.security.credentials(spec.security for spec in specs)
        self.raise_problems()
        return ClientPlan(
            operations=specs, resources=resources, security_schemes=self.security.root, credentials=credentials
        )

    def operation(self, index: int, operation: OperationContract) -> OperationSpec:
        """Plan one operation's names, arguments, media, responses, and servers."""
        setting = self.settings.get(operation.id.use_site.pointer)
        name = self.method(operation, setting)
        resource = self.resource(operation, setting)
        scopes = (*resource.split("."), name)
        parameters = self.parameters(operation, setting, scopes)
        body = None if operation.request_body is None else self.body(operation, operation.request_body, setting)
        runtime = None if setting is None else setting.runtime
        security = self.security.binding(operation)
        if setting is not None and setting.runtime.idempotency is not None:
            self._idempotency_header(operation, setting, parameters, security)
        success_statuses = () if runtime is None else runtime.success_statuses
        responses = tuple(
            self.response(operation, declaration, success_statuses) for declaration in operation.responses
        )
        self.check_statuses(operation, responses, success_statuses)
        operation_id = next(
            (
                value.value
                for key, value in operation.facts
                if key == "operationId" and isinstance(value, LiteralScalar)
            ),
            None,
        )
        return OperationSpec(
            contract=operation,
            index=index,
            resource=resource,
            name=name,
            pascal="",
            operation_id=operation_id if isinstance(operation_id, str) and operation_id else None,
            parameters=parameters,
            body=body,
            responses=responses,
            servers=self.servers(operation),
            success_statuses=success_statuses,
            request_id_header=None if runtime is None else runtime.request_id_header,
            response_media_type=self.response_media(operation, responses, setting),
            description=None if setting is None else setting.description,
            body_arguments=(None if setting is None else setting.body_arguments) or self.config.body_arguments,
            body_field_names=() if setting is None else setting.body_field_names,
            retry_safety="method_default" if runtime is None else runtime.retry_safety,
            idempotency=None if runtime is None else runtime.idempotency,
            retry_after_ms_header=None if runtime is None else runtime.retry_after_ms_header,
            should_retry_header=None if runtime is None else runtime.should_retry_header,
            security=security,
            auth_challenge_less_401=False if runtime is None else runtime.auth_challenge_less_401,
            accepted_content_encodings=() if runtime is None else runtime.accepted_content_encodings,
            scopes=scopes,
        )

    def _idempotency_header(
        self,
        operation: OperationContract,
        setting: ClientOperationConfig,
        parameters: tuple[ParameterSpec, ...],
        security: SecurityBinding | None,
    ) -> None:
        """Reject ownership shared by the key contract and an effective request header or security scheme."""
        metadata = setting.runtime.idempotency
        assert metadata is not None
        owners = {item.wire_name.lower() for item in parameters if item.location == "header"}
        if security is not None:
            owners.update(
                requirement.scheme.wire_name.lower()
                for alternative in security.alternatives
                for requirement in alternative
                if requirement.scheme.location == "header"
            )
        if metadata.header_name.lower() in owners:
            index = self.config.operations.index(setting)
            message = (
                f"The idempotency header {metadata.header_name!r} of {_label(operation)} already has a request owner"
            )
            self.problems.append(
                _problem(
                    "E_CONFIG_CONFLICT",
                    message,
                    operation.id.use_site,
                    f"operations[{index}].runtime.idempotency.header_name",
                )
            )

    def resource(self, operation: OperationContract, setting: ClientOperationConfig | None) -> str:
        """Return the operation's resource namespace: explicit, its first tag's mapped or derived name, or default.

        Each other tag derives a new name beside the clients' members; a Windows device name needs an explicit one.
        """
        if setting is not None and setting.resource is not None:
            return setting.resource
        if not (tags := _tags(operation)):
            return "default"
        if (mapped := self.resource_names.get(tags[0])) is not None:
            return mapped
        return self.namespaces[tags[0]]

    def tag_namespaces(self) -> dict[str, str]:
        """Name the resource of each first tag that no setting names, apart from the clients' members and each other.

        A Windows device name needs an explicit one.
        """
        first: dict[str, OperationContract] = {}
        for operation in self.request.operations:
            setting = self.settings.get(operation.id.use_site.pointer)
            if (
                (setting is None or setting.resource is None)
                and (tags := _tags(operation))
                and tags[0] not in self.resource_names
            ):
                first.setdefault(tags[0], operation)
        bases = [self.names.function(tag) for tag in first]
        names = self.names.claim(
            NameScope(RESERVED_MEMBERS, folded=True),
            [(base, _local(operation), ()) for operation, base in zip(first.values(), bases, strict=True)],
        )
        for (tag, operation), namespace in zip(first.items(), names, strict=True):
            if namespace.casefold() in WINDOWS_DEVICES:
                message = f"The tag {tag!r} of {_label(operation)} needs an explicit resource name"
                self.problems.append(_problem("E_RESERVED_NAME", message, operation.id.use_site))
        return dict(zip(first, names, strict=True))

    def method(self, operation: OperationContract, setting: ClientOperationConfig | None) -> str:
        """Return the operation's method name: explicit, or derived from its operationId or its method and path.

        The resource names derived names apart; an explicit name must not be a member the resource defines.
        """
        if setting is None or (name := setting.name) is None:
            return self.names.function(operation_basis(operation.method, operation.path, operation.operation_id))
        if name in RESERVED_MEMBERS:
            message = f"The method name {name!r} of {_label(operation)} is reserved"
            self.problems.append(_problem("E_RESERVED_NAME", message, operation.id.use_site))
        return name

    def _explicit_method(self, operation: OperationContract) -> bool:
        """Return whether the operation's settings name its method."""
        return (setting := self.settings.get(operation.id.use_site.pointer)) is not None and setting.name is not None

    def named(self, specs: tuple[OperationSpec, ...]) -> tuple[OperationSpec, ...]:
        """Name each resource's methods apart and give each its PascalCase type name.

        A resource holds its members and child namespaces, then explicit method names, which must be new, then
        derived ones, suffixed in operation order; their type names are suffixed by the model's class rule.
        """
        members: dict[str, list[OperationSpec]] = {}
        for spec in specs:
            members.setdefault(spec.resource, []).append(spec)
        namespaces = {".".join(parts[:size]) for parts in map(_parts_of, members) for size in range(1, len(parts) + 1)}
        named = list(specs)
        for namespace, operations in members.items():
            scope = NameScope(child.rpartition(".")[2] for child in namespaces if child.rpartition(".")[0] == namespace)
            explicit = {spec.index for spec in operations if self._explicit_method(spec.contract)}
            for spec in operations:
                if spec.index in explicit and not scope.take(spec.name):
                    message = f"The resource {namespace!r} takes the name {spec.name!r} twice"
                    self.problems.append(_problem("E_NAME_COLLISION", message))
            for name in RESERVED_MEMBERS:
                scope.take(name)
            parts = tuple(namespace.split("."))
            derived = [spec for spec in operations if spec.index not in explicit]
            claimed = dict(
                zip(
                    (spec.index for spec in derived),
                    self.names.claim(scope, [(spec.name, _local(spec.contract), parts) for spec in derived]),
                    strict=True,
                )
            )
            methods = [claimed.get(spec.index, spec.name) for spec in operations]
            prefix = ("".join(map(self.names.pascal, parts)),)
            pascals = self.names.claim(
                NameScope(),
                [
                    (self.names.pascal(name), _local(spec.contract), prefix)
                    for spec, name in zip(operations, methods, strict=True)
                ],
                camel=True,
            )
            for spec, name, pascal in zip(operations, methods, pascals, strict=True):
                named[spec.index] = replace(spec, name=name, pascal=pascal)
        return tuple(named)

    def parameters(
        self, operation: OperationContract, setting: ClientOperationConfig | None, path: tuple[str, ...]
    ) -> tuple[ParameterSpec, ...]:
        """Plan the effective parameters in order and name their arguments.

        Explicit names, configured or `--aliases` entries, are taken first and must be new identifiers; the other
        arguments are named as model fields after their wire names, then suffixed apart from the method's own
        arguments, a helper's when a helper calls the operation, and each other in declaration order.
        """
        names = () if setting is None else setting.parameter_names
        explicit = {(item.in_, item.name): item.python_name for item in names}
        plans = self.parameter_plans.get(operation.id, {})
        declared: list[tuple[WireDeclaration, ParameterLocation, str, ParameterPlan, str | None]] = []
        scope = NameScope()
        for declaration in operation.parameters:
            location = _LOCATIONS[fact(declaration, "in")]
            wire_name = declaration.name or ""
            if (plan := plans.get((location, wire_name))) is None:
                continue
            given = explicit.pop((location, wire_name), None) or self._alias(
                operation, declaration, location, wire_name
            )
            declared.append((declaration, location, wire_name, plan, given))
            if given is None:
                continue
            if given in RESERVED_ARGUMENTS:
                message = (
                    f"The {location} parameter {wire_name!r} of {_label(operation)} cannot take the name {given!r}"
                )
                self.problems.append(_problem("E_RESERVED_NAME", message, declaration.use_site))
            elif not scope.take(given):
                message = f"The arguments of {_label(operation)} take {given!r} twice"
                self.problems.append(_problem("E_NAME_COLLISION", message, operation.id.use_site))
        for name in (*RESERVED_ARGUMENTS, *(HELPER_ARGUMENTS if operation.id in self.helpers else ())):
            scope.take(name)
        bases = {index: self.names.argument(item[2]) for index, item in enumerate(declared) if item[4] is None}
        claimed = dict(
            zip(
                bases,
                self.names.claim(
                    scope, [(base, _declared_by(declared[index][0], operation), path) for index, base in bases.items()]
                ),
                strict=True,
            )
        )
        specs: list[ParameterSpec] = []
        for index, (declaration, location, wire_name, plan, given) in enumerate(declared):
            python_name = given or claimed[index]
            required = fact(declaration, "required") is True
            use = self.use(_uses(declaration))
            argument = None if use is None or use.type is None else self.facts.argument(use.type)
            specs.append(
                ParameterSpec(
                    location=location,
                    wire_name=wire_name,
                    python_name=python_name,
                    required=required,
                    use=use,
                    plan=plan,
                    argument=argument,
                    default=None if required else _default(use, argument),
                    converts=use is not None
                    and use.type is not None
                    and argument != self.facts.argument(use.type, ALIASES),
                )
            )
        self.problems.extend(
            _problem(
                "E_CONFIG_VALUE",
                f"The parameter name of the {location} parameter {name!r} of {_label(operation)} names no parameter",
                option_path="operations",
            )
            for location, name in explicit
        )
        paths = {spec.wire_name for spec in specs if spec.location == "path"}
        self.problems.extend(
            _problem(
                "E_METADATA_REQUIRED",
                f"The path placeholder {placeholder!r} of {_label(operation)} has no path parameter",
                operation.id.use_site,
            )
            for placeholder in dict.fromkeys(_PLACEHOLDER.findall(operation.path))
            if placeholder not in paths
        )
        return tuple(specs)

    def _alias(
        self, operation: OperationContract, declaration: WireDeclaration, location: str, wire_name: str
    ) -> str | None:
        """Return the `--aliases` entry naming a parameter's argument, which must be an identifier."""
        if (alias := self.names.alias(wire_name)) is not None and not explicit_name(alias):
            message = (
                f"The --aliases entry {alias!r} of the {location} parameter {wire_name!r} of {_label(operation)} "
                "is not an identifier"
            )
            self.problems.append(_problem("E_CONFIG_VALUE", message, declaration.use_site))
        return alias

    def use(self, uses: tuple[TypeUseId, ...]) -> TypeUseBinding | None:
        """Return the first type use of a declaration."""
        return next((self.uses[use] for use in uses if use in self.uses), None)

    def media(self, operation: OperationContract, declaration: WireDeclaration, *, request: bool) -> MediaSpec:
        """Return one media declaration with its normalized type, kind, type use, and form member plans."""
        try:
            media_type = normalize_media_type(declaration.name or "")
        except ValueError:
            message = f"The media type {declaration.name!r} of {_label(operation)} is not a media type"
            self.problems.append(_problem("E_METADATA_REQUIRED", message, declaration.use_site))
            media_type = declaration.name or ""
        kind = media_kind(media_type)
        use = self.use(declaration.schemas)
        essence = media_type.partition(";")[0]
        if (reason := _unsupported(kind, essence, use, request=request)) is not None:
            message = f"The {media_type} media of {_label(operation)} {reason}"
            self.problems.append(_problem("E_CLIENT_UNSUPPORTED", message, declaration.use_site))
        if kind == "multipart" and not request and (encoding := _encoded(declaration)) is not None:
            message = (
                f"The {encoding.name} encoding of the {media_type} media of {_label(operation)} is not supported yet"
            )
            self.problems.append(_problem("E_CLIENT_UNSUPPORTED", message, encoding.use_site))
        fields, additional, encoded = (
            self.forms.get(use.id, ((), None, ())) if kind == "form" and use is not None else ((), None, ())
        )
        media_of, headers_of = (
            self.part_media(operation, declaration, media_type, use) if kind == "multipart" and request else ({}, {})
        )
        styles_of = self.styles.get(use.id, {}) if request and use is not None else {}
        members: tuple[PartSpec, ...] | None = None
        extra: PartSpec | None = None
        if reason is None and essence == _FORM_DATA and use is not None and use.schema is not None:
            members, extra = (
                self._sent(use, use.schema, media_of=media_of, styles_of=styles_of)
                if request
                else self._received(use, use.schema)
            )
            if members is None:
                encoded = tuple(styles_of.values())
                self.problems.extend(
                    _problem(
                        "E_CLIENT_UNSUPPORTED",
                        f"The {encoding.name} encoding of the {media_type} media of {_label(operation)} requires a "
                        "header only a body sent as parts carries",
                        encoding.use_site,
                    )
                    for encoding in declaration.children
                    if any(
                        header.required and header.name.lower() != "content-disposition"
                        for header in headers_of.get(encoding.name or "", ())
                    )
                )
        parts, additional_part = (
            self._part_plans(use) if kind == "multipart" and not request and members is None else ((), None)
        )
        return MediaSpec(
            media_type=media_type,
            kind=kind,
            use=use,
            fields=fields,
            additional=additional,
            encoded=encoded,
            parts=parts,
            additional_part=additional_part,
            members=members,
            extra=extra,
            content_types=()
            if members is not None
            else tuple((name, types[0]) for name, types in media_of.items() if types),
        )

    def part_media(
        self, operation: OperationContract, declaration: WireDeclaration, media_type: str, use: TypeUseBinding | None
    ) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[HeaderSpec, ...]]]:
        """Return the media types and headers each form-data member's encoding names, reporting those not sent yet.

        A member holding no files takes one JSON or text media type; a file member takes any media types and ranges.
        A style, explode, or allowReserved leaves the contentType ignored, and only a member holding no files takes it.
        Only a declared header with a schema is returned; a body sent whole cannot carry a required one.
        """
        declared = dict(_parts(use).members)
        members = {} if use is None else {name: declared[name] for name, _, _ in _members(use)}
        media_of: dict[str, tuple[str, ...]] = {}
        headers_of: dict[str, tuple[HeaderSpec, ...]] = {}
        for encoding in (child for child in declaration.children if child.kind == "encoding"):
            name = encoding.name or ""
            label = f"The {name} encoding of the {media_type} media of {_label(operation)}"
            if name in members and (headers := self._part_headers(encoding)):
                headers_of[name] = headers
            styled = any(fact(encoding, key) is not None for key in _STYLED)
            match _content_types(encoding):
                case _ if name not in members:
                    self.problems.append(_problem("E_METADATA_REQUIRED", f"{label} names no member", encoding.use_site))
                case _ if styled and members[name].file:
                    message = f"{label} gives a style to a member holding files"
                    self.problems.append(_problem("E_CLIENT_UNSUPPORTED", message, encoding.use_site))
                case _ if styled:
                    pass
                case None:
                    self.problems.append(
                        _problem("E_METADATA_REQUIRED", f"{label} names no media type", encoding.use_site)
                    )
                case ():
                    pass
                case (single,) if not media_range(single) and (
                    media_kind(single) == "json" or (media_kind(single) == "text" and not members[name].structured)
                ):
                    media_of[name] = (single,)
                case types if members[name].file:
                    media_of[name] = types
                case _:
                    message = f"{label} has no builtin encoding for a member holding no files"
                    self.problems.append(_problem("E_CLIENT_UNSUPPORTED", message, encoding.use_site))
        return media_of, headers_of

    def _part_headers(self, encoding: WireDeclaration) -> tuple[HeaderSpec, ...]:
        """Return the headers with a schema an encoding declares, with the plans that read their values."""
        return tuple(
            HeaderSpec(name=child.name or "", required=fact(child, "required") is True, use=use, plan=plan)
            for child in encoding.children
            if child.kind == "header"
            and (use := self.use(_uses(child))) is not None
            and (plan := self.header_plans.get(use.id)) is not None
        )

    def _extra_use(self, body: TypeUseBinding, extra: SchemaSite) -> TypeUseBinding | None:
        """Return the use the other properties of a body are read or sent by, when their schema is bound."""
        return schema_use(self._schemas, extra.location, body.id.direction, extra.target)

    def _sent(
        self,
        use: TypeUseBinding,
        site: SourceLocation,
        *,
        media_of: Mapping[str, tuple[str, ...]],
        styles_of: Mapping[str, ParameterPlan],
    ) -> tuple[tuple[PartSpec, ...] | None, PartSpec | None]:
        """Return the member plans of a form-data body with file parts, or None when its object is sent whole.

        A member holding no files takes values of its field's type, which the part's own type use validates, and a
        styled member writes the parts its style gives rather than one for each item.
        """
        parts = _parts(use)
        declared_extra = parts.extra
        additional: PartSpec | None = PartSpec(plan=PartPlan(""))
        if declared_extra == "closed":
            additional = None
        elif isinstance(declared_extra, PartSchema) and declared_extra.file:
            additional = PartSpec(plan=PartPlan("", repeated=declared_extra.repeated), file=True)
        elif (
            isinstance(declared_extra, PartSchema)
            and (typed := self._extra_use(use, extra := _site(declared_extra, site))) is not None
        ):
            additional = PartSpec(
                plan=PartPlan("", repeated=declared_extra.repeated), use=_part_use(use, extra.location, None, typed)
            )
        declared = dict(parts.members)
        members = _members(use)
        if not any(declared[name].file for name, _, _ in members) and (additional is None or not additional.file):
            return None, None
        specs: list[PartSpec] = []
        for name, member, facts in members:
            part = declared[name]
            specs.append(
                PartSpec(
                    plan=PartPlan(
                        name,
                        repeated=name not in styles_of and part.repeated,
                        content_types=media_of.get(name, ()),
                        style=styles_of.get(name),
                    ),
                    use=None
                    if part.file
                    else _part_use(use, member, name, TypeUseBinding(use.id, "bound", facts.type, None)),
                    file=part.file,
                )
            )
        return tuple(specs), additional

    def _received(
        self, use: TypeUseBinding, site: SourceLocation
    ) -> tuple[tuple[PartSpec, ...] | None, PartSpec | None]:
        """Return the member plans of a form-data response with file parts, or None when its object is read whole.

        A part holding no file is read by the type use of its schema, or of its items' schema when its member repeats;
        a part of an untyped extra keeps its bytes, as a file part does.
        """
        parts = _parts(use)
        declared = dict(parts.members)
        members = _members(use)
        declared_extra = parts.extra
        if not any(declared[name].file for name, _, _ in members) and not (
            isinstance(declared_extra, PartSchema) and declared_extra.file
        ):
            return None, None
        additional: PartSpec | None = PartSpec(plan=PartPlan("", repeated=True), file=True)
        if declared_extra == "closed":
            additional = None
        elif isinstance(declared_extra, PartSchema):
            additional = self._read(use, "", _site(declared_extra, site).location, declared_extra, required=False)
        return tuple(
            self._read(
                use,
                name,
                member,
                declared[name],
                required=facts.required and not facts.write_only,
                excluded=facts.write_only,
            )
            for name, member, facts in members
        ), additional

    def _read(  # noqa: PLR0913
        self,
        body: TypeUseBinding,
        name: str,
        location: SourceLocation,
        part: PartSchema,
        *,
        required: bool,
        excluded: bool = False,
    ) -> PartSpec:
        """Return how a member's parts are read: a file's as bytes, any other in its kind by its or its items' use."""
        if part.file:
            return PartSpec(
                plan=PartPlan(name, repeated=part.repeated), file=True, required=required, excluded=excluded
            )
        plan = self._part_plan(name, location, part)
        read = _site(part, location, items=plan.repeated)
        bound = schema_use(self._schemas, read.location, body.id.direction, read.target) or TypeUseBinding(
            body.id, "not_generated", None, None
        )
        return PartSpec(
            plan=plan, use=_part_use(body, read.location, name or None, bound), required=required, excluded=excluded
        )

    def _part_plans(self, use: TypeUseBinding | None) -> tuple[tuple[PartPlan, ...], PartPlan | None]:
        """Return how the parts of a multipart response are read: each member's kind, then any other part's."""
        if use is None or use.schema is None:
            return (), None
        parts = _parts(use)
        declared = dict(parts.members)
        plans = tuple(
            self._part_plan(member.wire_name, member.schema, declared[member.wire_name])
            for member in use.members
            if member.member_kind == "property" and member.wire_name is not None and member.schema is not None
        )
        if (declared_extra := parts.extra) == "closed":
            return plans, None
        if isinstance(declared_extra, PartSchema):
            return plans, self._part_plan("", _site(declared_extra, use.schema).location, declared_extra)
        return plans, PartPlan("")

    def _part_plan(self, name: str, location: SourceLocation, part: PartSchema) -> PartPlan:
        """Return how one member's parts are read: a scalar's lexical kind, or JSON, repeated for an array."""
        steps = ("items",) if part.repeated else ()
        kind: PartKind = (self.wire.kinds.at((location, steps)) if part.text else None) or "json"
        return PartPlan(name, kind, repeated=part.repeated)

    def body(
        self, operation: OperationContract, declaration: WireDeclaration, setting: ClientOperationConfig | None
    ) -> BodySpec:
        """Plan a request body: its media and the media a call without media_type sends, never a media range."""
        media = self.media_list(operation, declaration, request=True)
        declared = [item.media_type for item in media]
        default = None if len(declared) != 1 or media_range(declared[0]) else declared[0]
        if setting is not None and setting.request_media_type is not None:
            default = normalize_media_type(setting.request_media_type)
            if default not in declared or media_range(default):
                message = f"The request media type {default!r} of {_label(operation)} is not a declared concrete type"
                self.problems.append(_problem("E_CONFIG_VALUE", message, operation.id.use_site, "operations"))
        return BodySpec(required=fact(declaration, "required") is True, media=media, default=default)

    def media_list(
        self, operation: OperationContract, declaration: WireDeclaration, *, request: bool
    ) -> tuple[MediaSpec, ...]:
        """Return the declared media of a body or response, rejecting media types that normalize alike."""
        media = tuple(
            self.media(operation, child, request=request) for child in declaration.children if child.kind == "media"
        )
        self.problems.extend(
            _problem(
                "E_NAME_COLLISION",
                f"Several media types of {_label(operation)} normalize to {media_type!r}",
                declaration.use_site,
            )
            for media_type, count in Counter(item.media_type for item in media).items()
            if count > 1
        )
        return media

    def response(
        self, operation: OperationContract, declaration: WireDeclaration, success_statuses: tuple[int, ...]
    ) -> ResponseSpec:
        """Plan one declared response and classify it as a possible success or error."""
        status = (declaration.name or "default").upper() if declaration.name != "default" else "default"
        headers = [
            HeaderSpec(name=child.name or "", required=fact(child, "required") is True, use=use, plan=plan)
            for child in declaration.children
            if child.kind == "header"
            and (use := self.use(_uses(child))) is not None
            and (plan := self.header_plans.get(use.id)) is not None
        ]
        self.problems.extend(
            _problem(
                "E_NAME_COLLISION",
                f"Several headers of the {status} response of {_label(operation)} are named {name!r}",
                declaration.use_site,
            )
            for name, count in Counter(header.name.lower() for header in headers).items()
            if count > 1
        )
        exact = int(status) if status.isdigit() else None
        return ResponseSpec(
            status=status,
            media=self.media_list(operation, declaration, request=False),
            headers=tuple(headers),
            success=_success(status, success_statuses),
            error=_error(status),
            bodyless=operation.method == "head" or exact in BODYLESS_STATUSES,
        )

    def check_statuses(
        self, operation: OperationContract, responses: tuple[ResponseSpec, ...], success_statuses: tuple[int, ...]
    ) -> None:
        """Require every configured success status to select a declared response."""
        keys = {response.status for response in responses}
        self.problems.extend(
            _problem(
                "E_CONFIG_VALUE",
                f"The success status {status} of {_label(operation)} selects no declared response",
                operation.id.use_site,
                "operations",
            )
            for status in success_statuses
            if not keys & {str(status), f"{status // 100}XX", "default"}
        )

    def response_media(
        self,
        operation: OperationContract,
        responses: tuple[ResponseSpec, ...],
        setting: ClientOperationConfig | None,
    ) -> str | None:
        """Return the configured success media type, which must be a concrete media of a success body."""
        if setting is None or setting.response_media_type is None:
            return None
        media_type = normalize_media_type(setting.response_media_type)
        if media_type not in success_media(responses):
            message = f"The response media type {media_type!r} of {_label(operation)} is not a declared success media"
            self.problems.append(_problem("E_CONFIG_VALUE", message, operation.id.use_site, "operations"))
        return media_type

    def servers(self, operation: OperationContract) -> tuple[ServerSpec, ...]:
        """Resolve the operation's effective servers to absolute URL templates."""
        entries = _servers(next((value for key, value in operation.facts if key == "servers"), None))
        if entries is None:
            message = f"The servers of {_label(operation)} are not server objects"
            self.problems.append(_problem("E_BASE_URL", message, operation.id.use_site))
            return ()
        if not entries:
            if (default := self.config.default_base_url) is not None:
                return (ServerSpec(url=default),)
            entries = (("/", ()),)
        document = (
            operation.declaration.location.document
            if operation.servers_declared
            else self.request.batch.documents[0].id
        )
        source = self.documents[document]
        base = self.config.server_base_url or (source if urlsplit(source).scheme in _SCHEMES else "")
        resolved: list[ServerSpec] = []
        for url, variables in entries:
            spec = ServerSpec(
                url=url if absolute(_defaulted(url, variables)) else urljoin(base, url), variables=variables
            )
            if not absolute(_defaulted(spec.url, variables)) or not _declared(spec.url, variables):
                message = f"A server of {_label(operation)} does not resolve to an absolute http or https URL"
                self.problems.append(_problem("E_BASE_URL", message, operation.id.use_site))
            resolved.append(spec)
        return tuple(resolved)

    def resources(self, specs: tuple[OperationSpec, ...]) -> tuple[ResourceSpec, ...]:
        """Group operations by namespace, with every parent namespace, and check the names each resource holds."""
        members: dict[str, list[OperationSpec]] = {}
        for spec in specs:
            parts = spec.resource.split(".")
            for size in range(1, len(parts) + 1):
                members.setdefault(".".join(parts[:size]), [])
            members[spec.resource].append(spec)
        children: dict[str, list[str]] = {namespace: [] for namespace in members}
        for namespace in members:
            if "." in namespace:
                children[namespace.rpartition(".")[0]].append(namespace)
        folded: dict[str, list[str]] = {}
        for namespace in members:
            folded.setdefault(namespace.casefold(), []).append(namespace)
        self.problems.extend(
            _problem(
                "E_NAME_COLLISION",
                f"The resource namespaces {', '.join(map(repr, spellings))} differ only by case, which their "
                "directories cannot; name them explicitly",
            )
            for spellings in folded.values()
            if len(spellings) > 1
        )
        return tuple(
            ResourceSpec(
                namespace=namespace,
                operations=tuple(operations),
                children=tuple(children[namespace]),
                pascal="".join(self.names.pascal(part) for part in namespace.split(".")),
            )
            for namespace, operations in members.items()
        )


def _declared_by(declaration: WireDeclaration, operation: OperationContract) -> bool:
    """Return whether the document that names an operation's path item declares one of its declarations."""
    return declaration.declaration.location.document == operation.id.use_site.document


def _local(operation: OperationContract) -> bool:
    """Return whether the document that names the operation's path item declares the operation itself."""
    return operation.declaration.location.document == operation.id.use_site.document


def _parts_of(namespace: str) -> list[str]:
    return namespace.split(".")


def _default(use: TypeUseBinding | None, argument: TypeView | None) -> LiteralScalar | None:
    """Return the default the schema of a builtin scalar argument, or one or None, declares when it is of its type."""
    if argument is None or use is None:
        return None
    present = (
        tuple(member for member in argument.members if not isinstance(member, NoneType))
        if isinstance(argument, UnionType)
        else (argument,)
    )
    if len(present) != 1:
        return None
    scalar = present[0].base if isinstance(present[0], ConstructorType) else present[0]
    if not isinstance(scalar, BuiltinType):
        return None
    literal = use.default
    return literal if literal is not None and literal.kind in _DEFAULTS.get(scalar.name, ()) else None


def _success(status: str, success_statuses: tuple[int, ...]) -> bool:
    if status in {"default", "2XX"} or (status.isdigit() and _MIN_SUCCESS <= int(status) <= _MAX_SUCCESS):
        return True
    return any(status in {str(item), f"{item // 100}XX"} for item in success_statuses)


def _error(status: str) -> bool:
    return status in {"default", "4XX", "5XX"} or (status.isdigit() and _MIN_ERROR <= int(status) <= _MAX_ERROR)


def success_media(responses: tuple[ResponseSpec, ...]) -> tuple[str, ...]:
    """Return the concrete media types of every success body, in response and media order without repeats.

    Media ranges such as image/* are left out, since a call never names one.
    """
    return tuple(
        dict.fromkeys(
            media.media_type
            for response in responses
            if response.success and not response.bodyless
            for media in response.media
            if not media_range(media.media_type)
        )
    )


def reachable(concrete: str, media: tuple[str, ...]) -> tuple[str, ...]:
    """Return the media type of one response a concrete type dispatches to: itself, then type/*, then */*."""
    return tuple(found for found in (most_specific(concrete, media),) if found is not None)


def media_range(media_type: str) -> bool:
    """Return whether a media type is a range such as image/* or */*."""
    return "*" in media_type.partition(";")[0]


def _unsupported(kind: MediaKind, essence: str, use: TypeUseBinding | None, *, request: bool) -> str | None:
    """Return why a media type cannot be sent or read yet, or None when it can.

    Multipart without a schema is sent as form-data parts and read as bytes of any multipart media; with a
    schema, only form-data maps its parts to the schema's members, which must be an object's.
    """
    if kind != "multipart":
        return None
    if use is None or use.schema is None:
        return None if essence == _FORM_DATA or not request else "is not supported yet"
    if essence != _FORM_DATA:
        return "is not supported yet"
    return None if _parts(use).object else "needs an object schema to be sent as parts"


def _parts(use: TypeUseBinding | None) -> PartFacts:
    """Return what the schema of a multipart body or response says of its parts, nothing for one without a schema."""
    return _UNDECLARED if use is None or use.parts is None else use.parts


def _members(use: TypeUseBinding) -> list[tuple[str, SourceLocation, ModelFieldFacts]]:
    """Return the members of a form-data use as its model declares them, allOf branches included."""
    return [
        (name, schema, facts)
        for name, schema, member in property_members(use)
        if member.exclusion is None and (facts := member.model_facts) is not None
    ]


def schema_uses(uses: Iterable[TypeUseBinding]) -> dict[tuple[SourceDocumentId, str, Direction], TypeUseBinding]:
    """Index the value use of each schema occurrence by its location and the direction whose model it projects."""
    return {
        (use.id.use_site.document, use.id.use_site.pointer, use.id.direction): use
        for use in uses
        if use.id.role == "schema" and use.id.projection == "value"
    }


def schema_use(
    schemas: Mapping[tuple[SourceDocumentId, str, Direction], TypeUseBinding],
    location: SourceLocation,
    direction: Direction,
    referenced: SourceLocation | None = None,
) -> TypeUseBinding | None:
    """Return the use a schema is read or sent by in a body's direction.

    The direction's own use comes first, at the schema or else at the `referenced` schema it resolves to, since a split
    model's request and response variants are recorded where it is declared; the schema's neutral use comes last.
    """
    key = (location.document, location.pointer)
    return (
        schemas.get((*key, direction))
        or (None if referenced is None else schemas.get((referenced.document, referenced.pointer, direction)))
        or schemas.get((*key, "neutral"))
    )


def _site(part: PartSchema, location: SourceLocation, *, items: bool = False) -> SchemaSite:
    """Return where a part's values are read: its schema's site, or its items' when it repeats, as recorded."""
    found = part.items if items else part.own
    return SchemaSite(location, location) if found is None else found


def _part_use(
    body: TypeUseBinding, location: SourceLocation, name: str | None, bound: TypeUseBinding
) -> TypeUseBinding:
    """Return the type use of one part of a body: the body's use at the part's schema, bound as `bound` is."""
    return TypeUseBinding(
        id=replace(body.id, use_site=location, schema_site=location, name=name),
        state=bound.state,
        type=bound.type,
        reason=bound.reason,
        schema=location,
    )


def _content_types(encoding: WireDeclaration) -> tuple[str, ...] | None:
    """Return the media types an encoding's contentType lists, none without one, or None when one is no media type."""
    if (declared := fact(encoding, "contentType")) is None:
        return ()
    try:
        return tuple(normalize_media_type(item.strip()) for item in str(declared).split(","))
    except ValueError:
        return None


def _encoded(media: WireDeclaration) -> WireDeclaration | None:
    """Return the first encoding of a multipart response, which no builtin reader applies."""
    return next(
        (
            child
            for child in media.children
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


def _servers(
    value: FrozenLiteral | None,
) -> tuple[tuple[str, tuple[tuple[str, str, tuple[str, ...]], ...]], ...] | None:
    if value is None:
        return ()
    entries = _plain(value)
    if not _is_tuple(entries):
        return None
    servers: list[tuple[str, tuple[tuple[str, str, tuple[str, ...]], ...]]] = []
    for entry in entries:
        if not _is_dict(entry) or not isinstance(url := entry.get("url"), str):
            return None
        if (variables := _variables(entry.get("variables", {}))) is None:
            return None
        servers.append((url, variables))
    return tuple(servers)


def _variables(value: object) -> tuple[tuple[str, str, tuple[str, ...]], ...] | None:
    if not _is_dict(value):
        return None
    variables: list[tuple[str, str, tuple[str, ...]]] = []
    for name, variable in value.items():
        if (
            not isinstance(name, str)
            or not _is_dict(variable)
            or not isinstance(default := variable.get("default"), str)
        ):
            return None
        if not _strings(enum := variable.get("enum", ())):
            return None
        variables.append((name, default, enum))
    return tuple(variables)


def _defaulted(url: str, variables: tuple[tuple[str, str, tuple[str, ...]], ...]) -> str:
    defaults = {name: default for name, default, _ in variables}
    return _PLACEHOLDER.sub(lambda match: defaults.get(match[1], match[0]), url)


def _declared(url: str, variables: tuple[tuple[str, str, tuple[str, ...]], ...]) -> bool:
    return set(_PLACEHOLDER.findall(url)) <= {name for name, _, _ in variables}


def member_parts(media: MediaSpec) -> tuple[PartSpec, ...]:
    """Return the plans of a body or response sent or read as parts: each declared member, then any other part."""
    return (*(media.members or ()), *(() if media.extra is None else (media.extra,)))


def part_uses(plan: ClientPlan) -> Iterator[TypeUseBinding]:
    """Yield the type use of each part holding no files, of every body or response sent or read as parts."""
    for spec in plan.operations:
        media = (
            *(() if spec.body is None else spec.body.media),
            *(item for response in spec.responses if not response.bodyless for item in response.media),
        )
        yield from (part.use for item in media for part in member_parts(item) if part.use is not None)


def plan_uses(plan: ClientPlan) -> Iterator[TypeUseId]:
    """Yield every type use whose codec the client binds: parameters, bodies or their parts, responses, and headers."""
    for spec in plan.operations:
        yield from (parameter.use.id for parameter in spec.parameters if parameter.use is not None)
        if spec.body is not None:
            yield from (
                media.use.id
                for media in spec.body.media
                if media.use is not None and media.kind != "binary" and media.members is None
            )
        for response in spec.responses:
            if not response.bodyless:
                yield from (
                    media.use.id
                    for media in response.media
                    if media.use is not None and media.kind != "binary" and media.members is None
                )
            yield from (header.use.id for header in response.headers if header.use is not None)
    yield from (use.id for use in part_uses(plan))


def encoding_header_uses(request: TargetRequest) -> Iterator[TypeUseId]:
    """Yield the uses of the headers the encodings of the selected operations' request bodies declare."""
    for operation in request.operations:
        for media in () if operation.request_body is None else operation.request_body.children:
            for encoding in (child for child in media.children if child.kind == "encoding"):
                yield from (use for header in encoding.children if header.kind == "header" for use in _uses(header))


def style_uses(request: TargetRequest) -> Iterator[tuple[TypeUseId, tuple[WireDeclaration, ...]]]:
    """Yield the uses of the selected operations' form-data request bodies with the encodings that give a style."""
    for operation in request.operations:
        for media in () if operation.request_body is None else operation.request_body.children:
            if _essence(media.name or "") == _FORM_DATA and (
                encodings := tuple(
                    child
                    for child in media.children
                    if child.kind == "encoding" and any(fact(child, key) is not None for key in _STYLED)
                )
            ):
                yield from ((use, encodings) for use in media.schemas)


def form_uses(request: TargetRequest) -> Iterator[tuple[TypeUseId, tuple[WireDeclaration, ...]]]:
    """Yield the uses of the URL-encoded bodies and responses of the selected operations, which need member plans.

    Each use comes with the encodings of its request body media; a response's encodings do not apply.
    """
    for operation in request.operations:
        declarations = (*(() if operation.request_body is None else (operation.request_body,)), *operation.responses)
        for declaration in declarations:
            for child in declaration.children:
                if child.kind == "media" and _form(child.name or ""):
                    encodings = tuple(
                        item
                        for item in child.children
                        if item.kind == "encoding" and declaration.kind == "request_body"
                    )
                    yield from ((use, encodings) for use in child.schemas)


def _form(media_type: str) -> bool:
    return media_kind(_essence(media_type)) == "form"


def _essence(media_type: str) -> str:
    try:
        return normalize_media_type(media_type).partition(";")[0]
    except ValueError:
        return ""
