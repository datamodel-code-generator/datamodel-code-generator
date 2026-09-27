"""Plan a client target: the resource, method, arguments, media, responses, and servers of each operation."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import starmap
from typing import TYPE_CHECKING, Final, Literal, TypeAlias
from urllib.parse import urljoin, urlsplit

from typing_extensions import TypeIs

from datamodel_code_generator._api_types import Diagnostic
from datamodel_code_generator._client.config import absolute
from datamodel_code_generator._client.naming import (
    RESERVED_ARGUMENTS,
    RESERVED_MEMBERS,
    WINDOWS_DEVICES,
    method_name,
    pascal,
    snake,
)
from datamodel_code_generator._codec_declarations import OperationRef
from datamodel_code_generator._generation_contract import LiteralScalar, LiteralSequence, SourceLocation
from datamodel_code_generator._runtime.client.multipart import PartPlan
from datamodel_code_generator._runtime.model_codecs.media import media_kind, normalize_media_type

if TYPE_CHECKING:
    from collections.abc import Iterator

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._client.config import ClientGenerationConfig, ClientOperationConfig
    from datamodel_code_generator._generation_contract import (
        FrozenLiteral,
        OperationContract,
        TypeUseBinding,
        TypeUseId,
        WireDeclaration,
    )
    from datamodel_code_generator._openapi_wire_plan import WirePlan
    from datamodel_code_generator._runtime.model_codecs.media import FieldPlan, MediaKind
    from datamodel_code_generator._runtime.model_codecs.parameters import ParameterLocation, ParameterPlan
    from datamodel_code_generator._runtime.model_codecs.wire import WireValue

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
_OBJECT: Final = frozenset({"object"})
_ARRAY: Final = frozenset({"array"})
_STRING: Final = frozenset({"string"})
_MIN_SUCCESS: Final = 200
_MAX_SUCCESS: Final = 299
_MIN_ERROR: Final = 400
_MAX_ERROR: Final = 599
_CONFIG_CODES: Final = frozenset({"E_CONFIG_VALUE", "E_CONFIG_CONFLICT", "E_OPERATION_REF"})


@dataclass(frozen=True, slots=True, kw_only=True)
class MediaSpec:
    """One declared media type of a body or response, with its type use when its content has a schema."""

    media_type: str
    kind: MediaKind
    use: TypeUseBinding | None
    fields: tuple[FieldPlan, ...] = ()
    additional: FieldPlan | None = None
    parts: tuple[PartPlan, ...] = ()
    additional_part: PartPlan | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ParameterSpec:
    """One effective parameter: its location and names, requiredness, type use, and wire plan."""

    location: ParameterLocation
    wire_name: str
    python_name: str
    required: bool
    use: TypeUseBinding | None
    plan: ParameterPlan


@dataclass(frozen=True, slots=True, kw_only=True)
class BodySpec:
    """A request body: its declared media, the media sent when a call names none, and whether it is required."""

    required: bool
    media: tuple[MediaSpec, ...]
    default: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class HeaderSpec:
    """One effective declared response header, with the plan and type use that decode its value."""

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
class OperationSpec:
    """Everything the renderer needs for one selected operation."""

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

    @property
    def head(self) -> bool:
        """Return whether the operation is a HEAD operation, whose responses have no body."""
        return self.contract.method == "head"


@dataclass(frozen=True, slots=True, kw_only=True)
class ResourceSpec:
    """One resource namespace: its operations in operation order and its direct child namespaces."""

    namespace: str
    operations: tuple[OperationSpec, ...]
    children: tuple[str, ...]

    @property
    def parts(self) -> tuple[str, ...]:
        """Return the namespace's dotted parts."""
        return tuple(self.namespace.split("."))

    @property
    def pascal(self) -> str:
        """Return the PascalCase name of the namespace, which its resource classes start with."""
        return "".join(pascal(part) for part in self.parts)


@dataclass(frozen=True, slots=True, kw_only=True)
class ClientPlan:
    """The planned operations of one client target and its resource namespaces in first-appearance order."""

    operations: tuple[OperationSpec, ...]
    resources: tuple[ResourceSpec, ...]

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

    def __init__(self, request: TargetRequest, config: ClientGenerationConfig, wire: WirePlan) -> None:
        """Index the batch and the wire plan, and resolve the per-operation settings to operation keys."""
        self.request = request
        self.config = config
        self.wire = wire
        self.uses = {use.id: use for use in request.batch.type_uses}
        self.parameter_plans = {
            operation: {(plan.location, plan.name): plan for plan in plans} for operation, plans in wire.parameters
        }
        self.header_plans = dict(wire.headers)
        self.forms = {use: (fields, additional) for use, fields, additional in wire.forms}
        self.documents = {document.id: document.uri for document in request.batch.documents}
        self.resource_names = {item.tag: item.namespace for item in config.resource_names}
        self.problems: list[Diagnostic] = []
        self.settings = self.resolved()
        self.raise_problems()

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
                message = f"The operation setting {reference.pointer!r} selects no root path operation"
                self.problems.append(_problem("E_OPERATION_REF", message, option_path=option))
            elif (key := operation.id.use_site.pointer) in resolved:
                self.problems.append(_problem("E_CONFIG_CONFLICT", f"Two settings name {key!r}", option_path=option))
            else:
                resolved[key] = item
        return resolved

    def plan(self) -> ClientPlan:
        """Plan each selected operation, then its resource namespaces and the names they must keep apart."""
        specs = tuple(starmap(self.operation, enumerate(self.request.operations)))
        self.raise_problems()
        resources = self.resources(specs)
        self.raise_problems()
        return ClientPlan(operations=specs, resources=resources)

    def operation(self, index: int, operation: OperationContract) -> OperationSpec:
        """Plan one operation's names, arguments, media, responses, and servers."""
        setting = self.settings.get(operation.id.use_site.pointer)
        name = self.method(operation, setting)
        parameters = self.parameters(operation, setting)
        body = None if operation.request_body is None else self.body(operation, operation.request_body, setting)
        runtime = None if setting is None else setting.runtime
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
            resource=self.resource(operation, setting),
            name=name,
            pascal=pascal(name),
            operation_id=operation_id if isinstance(operation_id, str) and operation_id else None,
            parameters=parameters,
            body=body,
            responses=responses,
            servers=self.servers(operation),
            success_statuses=success_statuses,
            request_id_header=None if runtime is None else runtime.request_id_header,
            response_media_type=self.response_media(operation, responses, setting),
            description=None if setting is None else setting.description,
        )

    def resource(self, operation: OperationContract, setting: ClientOperationConfig | None) -> str:
        """Return the operation's resource namespace: explicit, its first tag's mapped or snake name, or default."""
        if setting is not None and setting.resource is not None:
            return setting.resource
        if not (tags := _tags(operation)):
            return "default"
        if (mapped := self.resource_names.get(tags[0])) is not None:
            return mapped
        namespace = snake(tags[0])
        if not namespace or namespace in RESERVED_MEMBERS | WINDOWS_DEVICES:
            message = f"The tag {tags[0]!r} of {_label(operation)} needs an explicit resource name"
            self.problems.append(_problem("E_RESERVED_NAME", message, operation.id.use_site))
        return namespace

    def method(self, operation: OperationContract, setting: ClientOperationConfig | None) -> str:
        """Return the operation's method name: explicit, its operationId in snake case, or its method and path."""
        operation_id = next((_plain(item) for key, item in operation.facts if key == "operationId"), None)
        if setting is not None and setting.name is not None:
            name = setting.name
        elif isinstance(operation_id, str) and operation_id:
            name = snake(operation_id)
        else:
            name = method_name(operation.method, operation.path)
        if not name or name in RESERVED_MEMBERS:
            message = f"{_label(operation)} needs an explicit method name instead of {name!r}"
            self.problems.append(_problem("E_RESERVED_NAME", message, operation.id.use_site))
        return name

    def parameters(
        self, operation: OperationContract, setting: ClientOperationConfig | None
    ) -> tuple[ParameterSpec, ...]:
        """Plan the effective parameters in order and name their arguments."""
        names = () if setting is None else setting.parameter_names
        explicit = {(item.in_, item.name): item.python_name for item in names}
        plans = self.parameter_plans.get(operation.id, {})
        specs: list[ParameterSpec] = []
        for declaration in operation.parameters:
            location = _LOCATIONS[fact(declaration, "in")]
            wire_name = declaration.name or ""
            if (plan := plans.get((location, wire_name))) is None:
                continue
            python_name = explicit.pop((location, wire_name), None) or snake(wire_name)
            if not python_name or python_name in RESERVED_ARGUMENTS:
                message = f"The {location} parameter {wire_name!r} of {_label(operation)} needs an explicit python_name"
                self.problems.append(_problem("E_RESERVED_NAME", message, declaration.use_site))
            specs.append(
                ParameterSpec(
                    location=location,
                    wire_name=wire_name,
                    python_name=python_name,
                    required=fact(declaration, "required") is True,
                    use=self.use(_uses(declaration)),
                    plan=plan,
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
        self.problems.extend(
            _problem(
                "E_NAME_COLLISION", f"The arguments of {_label(operation)} take {name!r} twice", operation.id.use_site
            )
            for name, count in sorted(Counter(spec.python_name for spec in specs).items())
            if count > 1
        )
        declared = {spec.wire_name for spec in specs if spec.location == "path"}
        self.problems.extend(
            _problem(
                "E_METADATA_REQUIRED",
                f"The path placeholder {placeholder!r} of {_label(operation)} has no path parameter",
                operation.id.use_site,
            )
            for placeholder in dict.fromkeys(_PLACEHOLDER.findall(operation.path))
            if placeholder not in declared
        )
        return tuple(specs)

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
        if (reason := self.unsupported(kind, essence, use, request=request)) is not None:
            message = f"The {media_type} media of {_label(operation)} {reason}"
            self.problems.append(_problem("E_CLIENT_UNSUPPORTED", message, declaration.use_site))
        if kind in {"form", "multipart"} and (encoded := _encoded(declaration)) is not None:
            message = (
                f"The {encoded.name} encoding of the {media_type} media of {_label(operation)} is not supported yet"
            )
            self.problems.append(_problem("E_CLIENT_UNSUPPORTED", message, encoded.use_site))
        fields, additional = self.forms.get(use.id, ((), None)) if kind == "form" and use is not None else ((), None)
        parts, additional_part = _part_plans(self.wire, use) if kind == "multipart" and not request else ((), None)
        return MediaSpec(
            media_type=media_type,
            kind=kind,
            use=use,
            fields=fields,
            additional=additional,
            parts=parts,
            additional_part=additional_part,
        )

    def unsupported(self, kind: MediaKind, essence: str, use: TypeUseBinding | None, *, request: bool) -> str | None:
        """Return why a media type cannot be sent or read yet, or None when it can.

        Multipart without a schema is sent as form-data parts and read as bytes of any multipart media; with a
        schema, only form-data maps its parts to the schema's members, and file parts are not supported yet.
        """
        if request and "*" in essence:
            return "is not supported yet"
        if kind != "multipart":
            return None
        if use is None or use.schema is None:
            return None if essence == _FORM_DATA or not request else "is not supported yet"
        if essence != _FORM_DATA:
            return "is not supported yet"
        return self.file_parts(use, use.schema)

    def file_parts(self, use: TypeUseBinding, location: SourceLocation) -> str | None:
        """Return why a form-data schema cannot be sent yet: file parts or a shape other than an object; else None.

        The members of the use are its properties as its model declares them, so allOf branches count too.
        """
        _, schema = self.wire.schema(location)
        if _types(schema) - _NULL not in {frozenset(), _OBJECT}:
            return "needs an object schema to be sent as parts"
        files = [
            member.wire_name or "additional properties"
            for member in use.members
            if member.schema is not None and _file(self.wire, member.schema)
        ]
        return None if not files else f"has file parts, which are not supported yet: {', '.join(files)}"

    def body(
        self, operation: OperationContract, declaration: WireDeclaration, setting: ClientOperationConfig | None
    ) -> BodySpec:
        """Plan a request body: its media and the media a call without media_type sends."""
        media = self.media_list(operation, declaration, request=True)
        declared = [item.media_type for item in media]
        default = None if len(declared) != 1 else declared[0]
        if setting is not None and setting.request_media_type is not None:
            default = normalize_media_type(setting.request_media_type)
            if default not in declared:
                message = f"The request media type {default!r} of {_label(operation)} is not declared"
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
        resources = tuple(
            ResourceSpec(namespace=namespace, operations=tuple(operations), children=tuple(children[namespace]))
            for namespace, operations in members.items()
        )
        for resource in resources:
            self.check_names(resource)
        return resources

    def check_names(self, resource: ResourceSpec) -> None:
        """Reject methods that take one name twice, a child resource's name, or one PascalCase type name twice."""
        taken = [spec.name for spec in resource.operations]
        taken.extend(child.rpartition(".")[2] for child in resource.children)
        self.problems.extend(
            _problem("E_NAME_COLLISION", f"The resource {resource.namespace!r} takes the name {name!r} twice")
            for name, count in sorted(Counter(taken).items())
            if count > 1
        )
        self.problems.extend(
            _problem("E_NAME_COLLISION", f"Several methods of the resource {resource.namespace!r} become {name!r}")
            for name, count in sorted(Counter(spec.pascal for spec in resource.operations).items())
            if count > 1
        )


def _success(status: str, success_statuses: tuple[int, ...]) -> bool:
    if status in {"default", "2XX"} or (status.isdigit() and _MIN_SUCCESS <= int(status) <= _MAX_SUCCESS):
        return True
    return any(status in {str(item), f"{item // 100}XX"} for item in success_statuses)


def _error(status: str) -> bool:
    return status in {"default", "4XX", "5XX"} or (status.isdigit() and _MIN_ERROR <= int(status) <= _MAX_ERROR)


def success_media(responses: tuple[ResponseSpec, ...]) -> tuple[str, ...]:
    """Return the concrete media types of every success body, in response and media order without repeats."""
    return tuple(
        dict.fromkeys(
            media.media_type
            for response in responses
            if response.success and not response.bodyless
            for media in response.media
            if "*" not in media.media_type.partition(";")[0]
        )
    )


def _file(wire: WirePlan, location: SourceLocation) -> bool:
    """Return whether a property holds binary files: a binary string, or an array of them."""
    _, schema = wire.schema(location)
    if _types(schema) - _NULL == _ARRAY:
        _, schema = wire.schema(SourceLocation(location.document, f"{location.pointer}/items", "schema"))
    binary = schema.get("format") == "binary" or ("contentMediaType" in schema and "contentEncoding" not in schema)
    return _types(schema) - _NULL == _STRING and binary


def _part_plans(wire: WirePlan, use: TypeUseBinding | None) -> tuple[tuple[PartPlan, ...], PartPlan | None]:
    """Return how the parts of a form-data response are read: each member's kind, then any other part's."""
    if use is None or use.schema is None:
        return (), None
    location, schema = wire.schema(use.schema)
    parts = tuple(
        _part_plan(wire, member.wire_name, member.schema)
        for member in use.members
        if member.member_kind == "property" and member.wire_name is not None and member.schema is not None
    )
    match schema.get("additionalProperties", True):
        case False:
            return parts, None
        case Mapping() as extra if extra:
            extra_location = SourceLocation(location.document, f"{location.pointer}/additionalProperties", "schema")
            return parts, _part_plan(wire, "", extra_location)
        case _:
            return parts, PartPlan("")


def _part_plan(wire: WirePlan, name: str, location: SourceLocation) -> PartPlan:
    """Return how one member's parts are read: a scalar's lexical kind, or JSON, repeated for an array."""
    _, schema = wire.schema(location)
    if repeated := _types(schema) - _NULL == _ARRAY:
        _, schema = wire.schema(SourceLocation(location.document, f"{location.pointer}/items", "schema"))
    match sorted(_types(schema) - _NULL):
        case [("string" | "integer" | "number" | "boolean") as scalar]:
            return PartPlan(name, scalar, repeated=repeated)
        case ["integer", "number"]:
            return PartPlan(name, "number", repeated=repeated)
        case _:
            return PartPlan(name, "json", repeated=repeated)


def _types(schema: Mapping[str, WireValue]) -> frozenset[str]:
    """Return the JSON types a schema declares, none when it declares none."""
    match declared := schema.get("type"):
        case str():
            return frozenset({declared})
        case tuple():
            return frozenset(str(item) for item in declared)
        case _:
            pass
    return frozenset()


def _encoded(media: WireDeclaration) -> WireDeclaration | None:
    """Return the first form encoding the builtin URL-encoded writer does not read."""
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


def plan_uses(plan: ClientPlan) -> Iterator[TypeUseId]:
    """Yield every type use whose codec the client binds: parameters, bodies, responses, and headers."""
    for spec in plan.operations:
        yield from (parameter.use.id for parameter in spec.parameters if parameter.use is not None)
        if spec.body is not None:
            yield from (media.use.id for media in spec.body.media if media.use is not None and media.kind != "binary")
        for response in spec.responses:
            if not response.bodyless:
                yield from (
                    media.use.id for media in response.media if media.use is not None and media.kind != "binary"
                )
            yield from (header.use.id for header in response.headers if header.use is not None)


def form_uses(request: TargetRequest) -> Iterator[TypeUseId]:
    """Yield the uses of the URL-encoded bodies and responses of the selected operations, which need member plans."""
    for operation in request.operations:
        declarations = (*(() if operation.request_body is None else (operation.request_body,)), *operation.responses)
        for declaration in declarations:
            for child in declaration.children:
                if child.kind == "media" and _form(child.name or ""):
                    yield from child.schemas


def _form(media_type: str) -> bool:
    try:
        return media_kind(normalize_media_type(media_type)) == "form"
    except ValueError:
        return False
