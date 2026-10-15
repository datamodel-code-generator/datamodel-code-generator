"""Plan the parameter, header and form codecs of an accepted batch from its recorded encoding facts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import cached_property
from math import isfinite
from typing import TYPE_CHECKING, Final, Literal, TypeAlias, cast

from datamodel_code_generator._openapi_codec_plan import keeps_extras
from datamodel_code_generator._runtime.model_codecs.media import (
    FieldPlan,
    LexicalKind,
    media_kind,
    normalize_media_type,
)
from datamodel_code_generator._runtime.model_codecs.parameters import (
    ParameterLocation,
    ParameterPlan,
    ValueShape,
    builtin_content,
    header_text,
)
from datamodel_code_generator._target_contract import (
    BindingReason,
    BuiltinType,
    ConstructorType,
    EncodingFacts,
    GeneratedEnumMember,
    GeneratedSymbolType,
    GeneratedTypeContractBatch,
    GenericType,
    ImportedType,
    KindSite,
    LiteralMapping,
    LiteralScalar,
    LiteralSequence,
    LiteralType,
    NoneType,
    OperationContract,
    OperationId,
    SourceLocation,
    TextShape,
    TypeUseId,
    UnionType,
    WireDeclaration,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator, Mapping, Sequence

    from datamodel_code_generator._runtime.model_codecs.media import MediaKind
    from datamodel_code_generator._target_contract import (
        Direction,
        FieldSlot,
        FieldUseBinding,
        FinalModelSymbol,
        FrozenLiteral,
        LeafStep,
        MemberShape,
        SymbolId,
        TypeUseBinding,
        TypeView,
    )

CodecReason: TypeAlias = (
    BindingReason
    | Literal[
        "MC_ALIAS_COLLISION",
        "MC_BINDING_MISSING",
        "MC_CODEC_UNSUPPORTED",
        "MC_PARAMETER_ENCODING",
    ]
)

_LOCATIONS: Final[dict[object, ParameterLocation]] = {
    "path": "path",
    "query": "query",
    "querystring": "querystring",
    "header": "header",
    "cookie": "cookie",
}
_DEFAULT_STYLES: Final = {"path": "simple", "query": "form", "header": "simple", "cookie": "form"}
_STRING: Final = frozenset({"string"})
_LITERAL_KINDS: Final[dict[str, LexicalKind]] = {"bool": "boolean", "float": "number", "int": "integer"}
_LEXICAL_KINDS: Final[dict[tuple[str | None, str], LexicalKind | None]] = {
    (None, "bool"): "boolean",
    (None, "float"): "number",
    (None, "int"): "integer",
    (None, "dict"): None,
    (None, "frozenset"): None,
    (None, "list"): None,
    (None, "set"): None,
    (None, "tuple"): None,
    ("pydantic", "StrictBool"): "boolean",
    ("pydantic", "NegativeFloat"): "number",
    ("pydantic", "NonNegativeFloat"): "number",
    ("pydantic", "NonPositiveFloat"): "number",
    ("pydantic", "PositiveFloat"): "number",
    ("pydantic", "StrictFloat"): "number",
    ("pydantic", "NegativeInt"): "integer",
    ("pydantic", "NonNegativeInt"): "integer",
    ("pydantic", "NonPositiveInt"): "integer",
    ("pydantic", "PositiveInt"): "integer",
    ("pydantic", "StrictInt"): "integer",
}
_ARGUMENTS: Final[dict[LeafStep, tuple[int, frozenset[tuple[str | None, str]]]]] = {
    "items": (
        1,
        frozenset({
            (None, "frozenset"),
            (None, "list"),
            (None, "set"),
            ("collections.abc", "Sequence"),
            ("typing", "Sequence"),
        }),
    ),
    "values": (2, frozenset({(None, "dict"), ("collections.abc", "Mapping"), ("typing", "Mapping")})),
}
_INTEGER_NUMBER: Final = frozenset({"integer", "number"})
NO_SCALAR_KIND: Final = "A parameter value needs one unambiguous scalar kind"
STYLED: Final = ("style", "explode", "allowReserved")
_BINARY_TEXT: Final = (
    "carries a binary string as text; declare it with format: byte for base64 text, optionally with "
    "--type-mappings string+byte=binary to keep bytes, or send it as a whole binary body or a file part"
)
_WRAPPERS: Final = frozenset({"alias", "root"})
_ANY: Final = frozenset({("typing", "Any"), ("typing_extensions", "Any")})


@dataclass(frozen=True, slots=True)
class CodecDiagnostic:
    """Report one generation-time wire rule that requires a source fix."""

    code: CodecReason
    source: SourceLocation
    message: str
    operation: OperationId | None = None
    uses: tuple[TypeUseId, ...] = ()


@dataclass(frozen=True, slots=True)
class WirePlan:
    """Keep the parameter, header and form plans of the planned operations, and the bound lexical kinds."""

    parameters: tuple[tuple[OperationId, tuple[ParameterPlan, ...]], ...]
    diagnostics: tuple[CodecDiagnostic, ...]
    kinds: LexicalKinds
    version: str = ""
    headers: tuple[tuple[TypeUseId, ParameterPlan], ...] = ()
    forms: tuple[tuple[TypeUseId, tuple[FieldPlan, ...], FieldPlan | None, tuple[ParameterPlan, ...]], ...] = ()
    styles: tuple[tuple[TypeUseId, tuple[ParameterPlan, ...]], ...] = ()


class _PlanError(Exception):
    def __init__(self, *, code: CodecReason, source: SourceLocation, message: str) -> None:
        super().__init__(message)
        self.diagnostic = CodecDiagnostic(code, source, message)


def _fact(declaration: WireDeclaration, key: str) -> object:
    return next(
        (value.value for name, value in declaration.facts if name == key and isinstance(value, LiteralScalar)), None
    )


class _WirePlanner:
    def __init__(self, batch: GeneratedTypeContractBatch) -> None:
        self.batch = batch
        self.kinds = LexicalKinds(batch)
        self.diagnostics: list[CodecDiagnostic] = []
        self.version = batch.openapi
        self.uses = {use.id: use for use in batch.type_uses}


def declared_media(name: str) -> tuple[str, str | None]:
    """Return a content key's normalized media type and why no header carries it, or None when one does.

    A key that is no media type is kept as it is written. A media type whose text is not ASCII header text, such as one
    with a non-ASCII parameter, labels no body: its Content-Type would fail every call.
    """
    try:
        media_type = normalize_media_type(name)
    except ValueError:
        return name, "is not a media type"
    return media_type, None if header_text(media_type) else "is not ASCII header text, which a Content-Type needs"


def body_kind(media_type: str, use: TypeUseBinding | None) -> MediaKind:
    """Return how a body of a normalized media type is carried, as its models decide.

    A text body the models type as raw bytes is carried as the raw body, and a binary body written as a binary string
    they type as text, such as an enum of strings, as text.
    """
    kind = media_kind(media_type)
    if use is None:
        return kind
    if kind == "text" and use.octets:
        return "binary"
    return "text" if use.text else kind


def operation_uses(operation: OperationContract) -> tuple[TypeUseId, ...]:
    """Return the type uses of an operation's parameters, responses, and body, without encoding headers."""
    pending = [*operation.parameters, *operation.responses]
    if operation.request_body is not None:
        pending.append(operation.request_body)
    found: list[TypeUseId] = []
    while pending:
        declaration = pending.pop(0)
        found.extend(declaration.schemas)
        pending.extend(child for child in declaration.children if child.kind != "encoding")
    return tuple(found)


def plan_wire(
    batch: GeneratedTypeContractBatch,
    uses: Sequence[TypeUseId] | None = None,
    *,
    operations: Collection[OperationId] | None = None,
    forms: Mapping[TypeUseId, tuple[WireDeclaration, ...]] | None = None,
    styles: Mapping[TypeUseId, tuple[WireDeclaration, ...]] | None = None,
) -> WirePlan:
    """Build the parameter plans of the requested operations and the plans of their requested encoding headers.

    The uses of URL-encoded bodies named in `forms` get their member plans, and each member their encoding names the
    plan of a query parameter; the form-data uses named in `styles` get the query parameter plan of each member their
    encodings give a style. A requested use that carries a raw binary string as text is refused, since the model
    backends write bytes as different text.
    """
    forms = forms or {}
    styles = styles or {}
    planner = _WirePlanner(batch)
    requested = None if uses is None else frozenset(uses)
    planned = [operation for operation in batch.operations if operations is None or operation.id in operations]
    parameters = tuple((operation.id, _parameters(planner, operation)) for operation in planned)
    headers = tuple(
        (use, plan)
        for operation in planned
        for header in _headers(operation, requested)
        for use, plan in _header(planner, operation.id, header)
    )
    planned_forms = tuple(
        form for use in batch.type_uses if use.id in forms and (form := _form(planner, use, forms[use.id])) is not None
    )
    planned_styles = tuple(
        style
        for use in batch.type_uses
        if use.id in styles and (style := _styles(planner, use, styles[use.id])) is not None
    )
    planner.diagnostics.extend(_binary_text(batch, planned, requested))
    return WirePlan(
        parameters,
        tuple(planner.diagnostics),
        planner.kinds,
        planner.version,
        headers,
        planned_forms,
        planned_styles,
    )


def _binary_text(
    batch: GeneratedTypeContractBatch, operations: Sequence[OperationContract], requested: frozenset[TypeUseId] | None
) -> Iterator[CodecDiagnostic]:
    """Yield a diagnostic for each requested use of the operations that carries a raw binary string as text."""
    labels = {operation.id: f"{operation.method.upper()} {operation.path}" for operation in operations}
    yield from (
        CodecDiagnostic(
            "MC_CODEC_UNSUPPORTED", source, f"{_carrier(use.id, labels[owner])} {_BINARY_TEXT}", owner, (use.id,)
        )
        for use in batch.type_uses
        if (source := use.binary) is not None and use.id.projection == "value"
        if requested is None or use.id in requested
        if isinstance(owner := use.id.owner, OperationId) and owner in labels
    )


def _carrier(use: TypeUseId, label: str) -> str:
    """Name the parameter, body, response, header or part header a use's value is, in its operation."""
    where = "request body" if use.role.startswith("request") else f"response {use.status}"
    match use.role:
        case "parameter":
            return f"The {use.location} parameter {use.name!r} of {label}"
        case "request_body" | "response_body":
            return f"The {use.media} {where} of {label}"
        case "response_header":
            return f"The {use.name} header of {where} of {label}"
        case _:
            pass
    return f"The {use.name} part header of the {use.media} {where} of {label}"


def _form(
    planner: _WirePlanner, use: TypeUseBinding, encodings: tuple[WireDeclaration, ...]
) -> tuple[TypeUseId, tuple[FieldPlan, ...], FieldPlan | None, tuple[ParameterPlan, ...]] | None:
    """Return the member plans of a URL-encoded use: each member's field, or the parameter plan its encoding gives."""
    try:
        bound = _schema_use(planner, (use.id,), use.id.use_site)
        styled = {declaration.name or "": declaration for declaration in encodings if _styled(declaration)}
        fields = tuple(_text(member) for member in form_members(planner.kinds, bound) if member.name not in styled)
        additional = _additional(planner, bound)
        members = {name: schema for name, schema, _ in property_members(use)}
        encoded = tuple(_encoding(planner, bound, members, declaration) for declaration in styled.values())
        encoded = _distinct(planner, bound, [field.name for field in fields], encoded)
    except _PlanError as error:
        _refused(planner, use, error)
        return None
    return use.id, fields, additional, encoded


def _styles(
    planner: _WirePlanner, use: TypeUseBinding, encodings: tuple[WireDeclaration, ...]
) -> tuple[TypeUseId, tuple[ParameterPlan, ...]] | None:
    """Return the query parameter plans of the form-data members whose encodings name a style or its options."""
    try:
        bound = _schema_use(planner, (use.id,), use.id.use_site)
        members = {name: schema for name, schema, _ in property_members(use)}
        encoded = tuple(_encoding(planner, bound, members, declaration) for declaration in encodings)
        styled = {plan.name for plan in encoded}
        encoded = _distinct(planner, bound, [name for name in members if name not in styled], encoded)
    except _PlanError as error:
        _refused(planner, use, error)
        return None
    return use.id, encoded


@dataclass(frozen=True, slots=True)
class FormMember:
    """One member of a form as it is written: its type, its kind of text, each value's type, and whether it repeats.

    `source` is where its schema, or its items' schema when it repeats, declares the values, and `field` the model
    field a declared property is. The member that stands for the form's other properties has no name and no field.
    """

    name: str
    type: TypeView
    kind: LexicalKind | None
    leaf: TypeView
    repeated: bool
    source: SourceLocation
    field: FieldUseBinding | None = None


def form_members(kinds: LexicalKinds, use: TypeUseBinding) -> Iterator[FormMember]:
    """Yield how each property a form's model declares is written, its allOf branches' included, by its field's type."""
    encoding = use.encoding
    recorded = {
        shape.name: shape.kind.source
        for shape in (() if encoding is None else encoding.form.members)
        if shape.kind is not None
    }
    for name, schema, member in property_members(use):
        if (facts := member.model_facts) is not None:
            yield FormMember(name, facts.type, *kinds.member(facts.type), recorded.get(name, schema), member)


def form_extras(kinds: LexicalKinds, use: TypeUseBinding) -> FormMember | None:
    """Return how the other properties of a form are written, by the type of their values.

    The type is the one the form's model keeps them as, in a field of its own or one it inherits. A model without
    such a field takes the type its direction binds for their schema, the form's own or one of a schema it combines
    with allOf, where the schema is or where an owner's mapping holds its values. A form whose model keeps none as a
    type, and that allows no other properties, has no schema for them, or binds no type for it, has no such member:
    its other properties are text as they come.
    """
    site = None if (encoding := use.encoding) is None else encoding.extras
    if (kept := kinds.kept(use)) is not None:
        value, declared = kept
        return FormMember("", value, *kinds.member(value), declared if site is None else site.source)
    value = (
        None
        if site is None
        else kinds.bound(
            use.id.direction,
            *((location, tuple(step for step in steps if step != "items")) for location, steps in site.leaves),
        )
    )
    return None if site is None or value is None else FormMember("", value, *kinds.member(value), site.source)


def _additional(planner: _WirePlanner, use: TypeUseBinding) -> FieldPlan | None:
    """Return the plan of a form's other properties, refusing a form no encoding writes.

    Other properties of a type without one kind are refused first, as the server refuses them. Those without a type
    are any string, and a form that allows none has no plan for them.
    """
    shape = _encoded(use).form
    extras = form_extras(planner.kinds, use)
    typed = None if extras is None else _text(extras)
    if (problem := shape.problem) is not None:
        source, message = problem
        raise _PlanError(code="MC_PARAMETER_ENCODING", source=source, message=message)
    return typed or (None if shape.additional is None else FieldPlan(""))


def _text(member: FormMember) -> FieldPlan:
    """Return the text plan of a URL-encoded member, refusing one whose type has no one kind."""
    if member.kind is None:
        raise _PlanError(code="MC_PARAMETER_ENCODING", source=member.source, message=NO_SCALAR_KIND)
    return FieldPlan(member.name, member.kind, repeated=member.repeated)


def property_members(use: TypeUseBinding) -> Iterator[tuple[str, SourceLocation, FieldUseBinding]]:
    """Yield each property a use's model declares, its allOf branches' included, with its wire name and schema."""
    for member in use.members:
        if member.member_kind == "property" and member.wire_name is not None and member.schema is not None:
            yield member.wire_name, member.schema, member


def _refused(planner: _WirePlanner, use: TypeUseBinding, error: _PlanError) -> None:
    owner = use.id.owner
    planner.diagnostics.append(
        replace(error.diagnostic, operation=owner if isinstance(owner, OperationId) else None, uses=(use.id,))
    )


def _distinct(
    planner: _WirePlanner, form: TypeUseBinding, names: list[str], encoded: tuple[ParameterPlan, ...]
) -> tuple[ParameterPlan, ...]:
    """Refuse a form whose members write a name twice, or whose other names more than one member would take back.

    An exploded member's own names count, and an exploded object that allows other properties takes every name no
    other member writes, so the form may have one such member and no deepObject member then, as an operation's query
    parameters may, nor other properties of its own that its schema allows as `true` or a schema or its model keeps by
    its configuration. Return the plans with the other exploded members' names reserved from the one that takes the
    rest.
    """
    location = _located(form)
    claimed = [
        *(item.name for plan in encoded if _spread(plan) for item in plan.fields),
        *names,
        *(plan.name for plan in encoded if not _spread(plan)),
    ]
    if len(set(claimed)) != len(claimed):
        raise _PlanError(code="MC_PARAMETER_ENCODING", source=location, message="Expanded form member names collide")
    absorbing = [plan.name for plan in encoded if plan.additional is not None and _spread(plan)]
    if absorbing and (
        len(absorbing) > 1
        or any(plan.style == "deepObject" for plan in encoded)
        or _encoded(form).declared
        or planner.kinds.configured(form)
    ):
        message = (
            f"The exploded form member {absorbing[0]!r} allows other properties and takes every name no other member "
            "writes, so the form cannot have another such member, a deepObject member, or other properties of its own"
        )
        raise _PlanError(code="MC_PARAMETER_ENCODING", source=location, message=message)
    plans = list(encoded)
    return tuple(_reserving(plan, plans) if plan.name in absorbing else plan for plan in plans)


def _styled(encoding: WireDeclaration) -> bool:
    """Return whether an encoding gives its member a query parameter's style or content."""
    return any(_fact(encoding, key) is not None for key in (*STYLED, "contentType"))


def content_types(encoding: WireDeclaration) -> tuple[str, ...] | None:
    """Return the media types an encoding's contentType lists, none without one, or None when one is no media type."""
    if (declared := _fact(encoding, "contentType")) is None:
        return ()
    try:
        return tuple(normalize_media_type(item.strip()) for item in str(declared).split(","))
    except ValueError:
        return None


def _encoding(
    planner: _WirePlanner, form: TypeUseBinding, members: Mapping[str, SourceLocation], encoding: WireDeclaration
) -> ParameterPlan:
    """Return the query parameter plan of a form member: its style, or its content when only that is named.

    A style, explode, or allowReserved takes precedence over contentType, as the Encoding Object prescribes.
    """
    name, source = encoding.name or "", encoding.use_site
    if (member := members.get(name)) is None:
        raise _PlanError(code="MC_PARAMETER_ENCODING", source=source, message="An encoding names no member of its form")
    style, explode, reserved, content = (
        _fact(encoding, key) for key in ("style", "explode", "allowReserved", "contentType")
    )
    try:
        if style is None and explode is None and reserved is None:
            media = normalize_media_type(str(content))
            if not builtin_content("query", media):
                raise _PlanError(
                    code="MC_PARAMETER_ENCODING",
                    source=source,
                    message="The member content has no builtin encoding",
                )
            return ParameterPlan(location="query", name=name, content_media_type=media)
        shape, kind, fields, additional = _shape(planner, _member_shape(form, member))
        chosen = str(style or "form")
        return ParameterPlan(
            location="query",
            name=name,
            style=chosen,
            explode=explode if isinstance(explode, bool) else chosen == "form",
            allow_reserved=reserved is True,
            shape=shape,
            kind=kind,
            fields=fields,
            additional=additional,
            reserved_names=tuple(sorted(other for other in members if other != name)),
        )
    except ValueError as error:
        raise _PlanError(code="MC_PARAMETER_ENCODING", source=source, message=str(error)) from None


def parameter_plans(wire: WirePlan) -> dict[OperationId, dict[tuple[ParameterLocation, str], ParameterPlan]]:
    """Index each operation's parameter plans by location and name."""
    return {operation: {(plan.location, plan.name): plan for plan in planned} for operation, planned in wire.parameters}


def form_styled(encoding: WireDeclaration, version: str, *, multipart: bool) -> bool:
    """Return whether an encoding gives its form member a query parameter's style, explode, or allowReserved.

    OpenAPI 3.0 applies them to application/x-www-form-urlencoded alone and ignores them for multipart/form-data,
    whose member is then written as its contentType says; 3.1 and later apply them to both, and write the parts they
    give without percent-encoding.
    """
    if multipart and (version == "3.0" or version.startswith("3.0.")):
        return False
    return any(_fact(encoding, key) is not None for key in STYLED)


def _allow_reserved(version: str, location: ParameterLocation, declaration: WireDeclaration) -> bool:
    """Return the effective allowReserved flag for the root OpenAPI version and parameter location."""
    return _fact(declaration, "allowReserved") is True and not (
        location == "path" and (version in ("3.0", "3.1") or version.startswith(("3.0.", "3.1.")))  # noqa: PLR6201
    )


def _planned(
    planner: _WirePlanner, operation: OperationId, declaration: WireDeclaration, names: list[tuple[object, str]]
) -> ParameterPlan | None:
    try:
        return _parameter(planner, declaration, names)
    except _PlanError as error:
        planner.diagnostics.append(
            replace(
                error.diagnostic,
                operation=operation,
                uses=_uses(declaration),
            )
        )
    return None


def _headers(operation: OperationContract, requested: frozenset[TypeUseId] | None) -> Iterator[WireDeclaration]:
    """Yield the headers of an operation's responses, then the requested ones its request body's encodings declare."""
    for response in operation.responses:
        yield from (child for child in response.children if child.kind == "header")
    for media in () if operation.request_body is None else operation.request_body.children:
        for encoding in (child for child in media.children if child.kind == "encoding"):
            yield from (
                child
                for child in encoding.children
                if child.kind == "header" and (requested is None or not requested.isdisjoint(_uses(child)))
            )


def _uses(declaration: WireDeclaration) -> tuple[TypeUseId, ...]:
    return (*declaration.schemas, *(use for child in declaration.children for use in child.schemas))


def _header(
    planner: _WirePlanner, operation: OperationId, declaration: WireDeclaration
) -> tuple[tuple[TypeUseId, ParameterPlan], ...]:
    if not (uses := _uses(declaration)):
        return ()
    facts = (*declaration.facts, ("in", LiteralScalar("str", "header")), ("style", LiteralScalar("str", "simple")))
    plan = _planned(planner, operation, replace(declaration, facts=facts), [])
    return () if plan is None else ((uses[0], plan),)


def _requirement_names(value: FrozenLiteral | None) -> list[str]:
    match value:
        case LiteralSequence(items=items):
            return [
                str(key.value)
                for item in items
                if isinstance(item, LiteralMapping)
                for key, _ in item.entries
                if isinstance(key, LiteralScalar)
            ]
        case _:
            return []


def auth_names(batch: GeneratedTypeContractBatch, operation: OperationContract) -> list[tuple[object, str]]:
    """Return the locations and names of the apiKey credentials the operation's security requirements send."""
    document = operation.id.use_site.document
    schemes = {scheme.name: scheme for scheme in batch.security_schemes if scheme.use_site.document == document}
    requirements = next((value for name, value in operation.facts if name == "security"), None)
    return list(
        dict.fromkeys(
            (_fact(scheme, "in"), str(_fact(scheme, "name")))
            for name in _requirement_names(requirements)
            if (scheme := schemes.get(name)) is not None and _fact(scheme, "type") == "apiKey"
        )
    )


def _spread(plan: ParameterPlan) -> bool:
    return plan.shape == "object" and plan.explode and plan.style in {"form", "cookie"}


def _claimed(plans: Sequence[ParameterPlan], location: str) -> list[str]:
    return [
        name.lower() if location == "header" else name
        for plan in plans
        if plan.location == location
        for name in ([field.name for field in plan.fields] if _spread(plan) else [plan.name])
    ]


def _reserving(plan: ParameterPlan, plans: list[ParameterPlan]) -> ParameterPlan:
    owned = {
        field.name
        for other in plans
        if other is not plan and other.location == plan.location and _spread(other)
        for field in other.fields
    } - set(_claimed([plan], plan.location))
    return replace(plan, reserved_names=tuple(sorted({*plan.reserved_names, *owned}))) if owned else plan


def _parameters(planner: _WirePlanner, operation: OperationContract) -> tuple[ParameterPlan, ...]:
    declarations = operation.parameters
    auth = auth_names(planner.batch, operation)
    names = [*((_fact(item, "in"), item.name or "") for item in declarations), *auth]
    plans = [
        plan
        for declaration in declarations
        if (plan := _planned(planner, operation.id, declaration, names)) is not None
    ]
    planner.diagnostics.extend(parameter_collisions(operation, plans, auth))
    return tuple(_reserving(plan, plans) for plan in plans)


def parameter_collisions(
    operation: OperationContract, plans: Sequence[ParameterPlan], auth: Sequence[tuple[object, str]]
) -> tuple[CodecDiagnostic, ...]:
    """Report each location whose expanded parameter names or the `auth` apiKey names collide."""
    found: list[CodecDiagnostic] = []
    for location in ("query", "header", "cookie"):
        claimed = [
            *_claimed(plans, location),
            *{name.lower() if location == "header" else name for kind, name in auth if kind == location},
        ]
        absorbing = [
            plan for plan in plans if plan.location == location and plan.additional is not None and _spread(plan)
        ]
        if (
            len(set(claimed)) != len(claimed)
            or len(absorbing) > 1
            or (absorbing and any(plan.location == location and plan.style == "deepObject" for plan in plans))
        ):
            source = next(item.use_site for item in operation.parameters if _fact(item, "in") == location)
            found.append(
                CodecDiagnostic(
                    "MC_PARAMETER_ENCODING", source, f"Expanded {location} parameter names collide", operation.id
                )
            )
    return tuple(found)


def _parameter(planner: _WirePlanner, declaration: WireDeclaration, names: list[tuple[object, str]]) -> ParameterPlan:
    source = declaration.use_site
    location = _LOCATIONS[_fact(declaration, "in")]
    name = declaration.name or ""
    required = _fact(declaration, "required") is True
    _finite(planner, declaration)
    if location == "querystring" or declaration.children:
        return _content_parameter(planner, declaration, location, name, required=required)
    shape, kind, fields, additional = _shape(planner, _encoded(_schema_use(planner, declaration.schemas, source)).value)
    style = str(_fact(declaration, "style") or _DEFAULT_STYLES[location])
    if style == "cookie" and not planner.version.startswith("3.2"):
        raise _PlanError(code="MC_PARAMETER_ENCODING", source=source, message="Cookie style requires OpenAPI 3.2")
    explode = _fact(declaration, "explode")
    try:
        return ParameterPlan(
            location=location,
            name=name,
            style=style,
            explode=explode if isinstance(explode, bool) else style in {"form", "cookie"},
            required=required,
            allow_reserved=_allow_reserved(planner.version, location, declaration),
            shape=shape,
            kind=kind,
            fields=fields,
            additional=additional,
            reserved_names=tuple(sorted(other for kind_, other in names if kind_ == location and other != name)),
        )
    except ValueError as error:
        raise _PlanError(code="MC_PARAMETER_ENCODING", source=source, message=str(error)) from None


def _content_parameter(
    planner: _WirePlanner, declaration: WireDeclaration, location: ParameterLocation, name: str, *, required: bool
) -> ParameterPlan:
    source = declaration.use_site
    if location == "querystring" and not planner.version.startswith("3.2"):
        raise _PlanError(
            code="MC_PARAMETER_ENCODING", source=source, message="Querystring parameters require OpenAPI 3.2"
        )
    media = normalize_media_type(declaration.children[0].name or "")
    if not builtin_content(location, media):
        raise _PlanError(
            code="MC_PARAMETER_ENCODING",
            source=source,
            message="The parameter content has no builtin encoding",
        )
    fields: tuple[FieldPlan, ...] = ()
    additional: FieldPlan | None = None
    content = declaration.children[0]
    kind = media_kind(media)
    if kind == "form":
        _, _, fields, additional = _shape(planner, _encoded(_schema_use(planner, content.schemas, source)).form)
    elif (
        kind == "text" and content.schemas and _encoded(_schema_use(planner, content.schemas, source)).types != _STRING
    ):
        raise _PlanError(
            code="MC_PARAMETER_ENCODING", source=source, message="Text parameter content requires a string schema"
        )
    try:
        return ParameterPlan(
            location=location,
            name=name,
            required=required,
            content_media_type=media,
            fields=fields,
            additional=additional,
        )
    except ValueError as error:
        raise _PlanError(code="MC_PARAMETER_ENCODING", source=source, message=str(error)) from None


def _finite(planner: _WirePlanner, declaration: WireDeclaration) -> None:
    """Refuse a parameter whose schema declares a non-finite default, which no JSON number or parameter text writes."""
    for use in (planner.uses[item] for item in _uses(declaration) if item in planner.uses):
        if isinstance(value := getattr(use.default, "value", None), float) and not isfinite(value):
            location = _located(use)
            raise _PlanError(
                code="MC_PARAMETER_ENCODING",
                source=replace(location, pointer=f"{location.pointer}/default"),
                message="A schema value must be a finite JSON number",
            )


def _schema_use(planner: _WirePlanner, uses: tuple[TypeUseId, ...], source: SourceLocation) -> TypeUseBinding:
    """Return the first of a declaration's type uses that has a schema."""
    if (use := next((item for item in planner.batch.type_uses if item.id in uses and item.schema), None)) is None:
        raise _PlanError(
            code="MC_PARAMETER_ENCODING", source=source, message="A style-based parameter requires a schema"
        )
    return use


def _located(use: TypeUseBinding) -> SourceLocation:
    return use.schema or use.id.use_site


def _encoded(use: TypeUseBinding) -> EncodingFacts:
    """Return how a use's schema is written as text, as the walk recorded it on acquiring the schema."""
    return cast("EncodingFacts", use.encoding)


def _member_shape(form: TypeUseBinding, member: SourceLocation) -> TextShape:
    """Return how one member of a form is written as a parameter value, as the walk recorded it."""
    found = dict(_encoded(form).members).get(member)
    return TextShape("scalar", KindSite(member, ((member, ()),))) if found is None else found


def _kind(planner: _WirePlanner, site: KindSite) -> LexicalKind:
    """Return the kind of a text leaf by the final type bound for it, at one of its places.

    A leaf bound to no type, to a model or a container, or to values of several kinds no text reaches, is refused.
    """
    if (kind := planner.kinds.at(*site.leaves)) is None:
        raise _PlanError(
            code="MC_PARAMETER_ENCODING",
            source=site.source,
            message=NO_SCALAR_KIND,
        )
    return kind


def _shape(
    planner: _WirePlanner, shape: TextShape
) -> tuple[ValueShape, LexicalKind, tuple[FieldPlan, ...], FieldPlan | None]:
    """Return a value's shape, its kind, and its members' plans.

    The members come first, in their order, then why the value has no builtin encoding, then any other property.
    """
    fields = tuple(_field(planner, member) for member in shape.members)
    if (problem := shape.problem) is not None:
        source, message = problem
        raise _PlanError(code="MC_PARAMETER_ENCODING", source=source, message=message)
    if shape.shape == "object":
        return "object", "string", fields, None if shape.additional is None else _field(planner, shape.additional)
    return shape.shape, _kind(planner, cast("KindSite", shape.kind)), (), None


def _field(planner: _WirePlanner, member: MemberShape) -> FieldPlan:
    """Return a member's plan: any string without a kind of its own, a repeated array member in its items' kind."""
    return FieldPlan(
        member.name, "string" if member.kind is None else _kind(planner, member.kind), repeated=member.repeated
    )


class LexicalKinds:
    """Read the lexical kind of a text leaf from the final type the model generator bound at its schema.

    An int, float or bool leaf has its own kind: as the builtin, a constrained or strict form of it, or the member
    type of an enum or literal. The text of any other scalar leaf is the model's to read, and so is that of a union
    with such a leaf. A model, a container, null alone, an enum or literal of several kinds, and a union of several
    kinds none of which reads text as it is, have no kind.
    """

    def __init__(self, batch: GeneratedTypeContractBatch) -> None:
        """Keep the batch whose schema uses, symbols and root values the first lookups index."""
        self._batch = batch

    @cached_property
    def _types(self) -> dict[tuple[int, str, Direction], TypeView | None]:
        return {
            (use.id.use_site.document, use.id.use_site.pointer, use.id.direction): use.type
            for use in self._batch.type_uses
            if use.id.role == "schema" and use.id.projection == "value"
        }

    @cached_property
    def _symbols(self) -> dict[SymbolId, FinalModelSymbol]:
        return {symbol.id: symbol for symbol in self._batch.symbols}

    @cached_property
    def _roots(self) -> dict[int, TypeView]:
        return {
            member.consumer: facts.type
            for member in self._batch.fields
            if member.member_kind == "root_value" and (facts := member.model_facts) is not None
        }

    def at(self, *leaves: tuple[SourceLocation, tuple[LeafStep, ...]]) -> LexicalKind | None:
        """Return the kind of a leaf by the first of its places a type is bound for, if that type has one.

        A place is a schema's location and the steps from the type bound there to the leaf: a list's items, then a
        mapping's values. A leaf no place binds a type for has no kind.
        """
        value = next((found for leaf in leaves if (found := self._reached(*leaf)) is not None), None)
        return _one_kind(set(self._leaves(value, frozenset())), mixed=True)

    @cached_property
    def _extras(self) -> dict[FieldSlot | None, SourceLocation]:
        return {
            member.slot: member.schema
            for member in self._batch.fields
            if member.member_kind == "additional_properties"
            and member.schema is not None
            and member.slot is not None
            and member.consumer == member.slot.symbol
        }

    def kept(self, use: TypeUseBinding) -> tuple[TypeView, SourceLocation] | None:
        """Return the type a use's model keeps the values of its other properties as, and where their schema is.

        The model declares the field that keeps them or inherits it, and the schema is the declaring model's.
        """
        return next(
            (
                (value, self._extras.get(member.slot, schema))
                for member in use.members
                if member.member_kind == "additional_properties"
                and (facts := member.model_facts) is not None
                and (schema := member.schema) is not None
                and (value := self._argument(facts.type, *_ARGUMENTS["values"])) is not None
            ),
            None,
        )

    def configured(self, use: TypeUseBinding) -> bool:
        """Return whether the model a use's type stands for keeps its other properties by the configuration it takes.

        The type stands for the model through aliases, root models and a union with None alone, and the model takes
        its own setting over the one it inherits.
        """
        return isinstance(value := self._unwrapped(use.type), GeneratedSymbolType) and keeps_extras(
            self._symbols, value.symbol
        )

    def bound(self, direction: Direction, *places: tuple[SourceLocation, tuple[LeafStep, ...]]) -> TypeView | None:
        """Return the type bound for a value at the first of its places that binds one.

        The places of a direction's own models come first, then those of the models both directions share.
        """
        return next(
            (
                found
                for side in dict.fromkeys((direction, "neutral"))
                for location, steps in places
                if (found := self._reached(location, steps, side)) is not None
            ),
            None,
        )

    def member(self, value: TypeView) -> tuple[LexicalKind | None, TypeView, bool]:
        """Return how a form member of a type is written: the kind of its text, each value's type, and its repeats.

        A list type repeats, each of its values one of its items. A value type without one kind, such as a model, a
        mapping, or a union of text and an object, has no text of its own: a form-data part carries such a value as
        JSON, and a URL-encoded form cannot carry it.
        """
        item = self._argument(value, *_ARGUMENTS["items"])
        leaf = value if item is None else item
        return _one_kind(set(self._leaves(leaf, frozenset())), mixed=True), leaf, item is not None

    def _reached(
        self, location: SourceLocation, steps: tuple[LeafStep, ...], direction: Direction = "neutral"
    ) -> TypeView | None:
        return self._stepped(self._types.get((location.document, location.pointer, direction)), steps)

    def _stepped(self, value: TypeView | None, steps: tuple[LeafStep, ...]) -> TypeView | None:
        for step in steps:
            value = self._argument(value, *_ARGUMENTS[step])
        return value

    def untyped(self, value: TypeView) -> bool:
        """Return whether a type takes any value, as the type of an untyped schema does.

        A form-data part carries such a value as JSON, as it does one without one kind, while its kind is a string's.
        """
        return (found := self._unwrapped(value)) is not None and _type_name(found) in _ANY

    def _unwrapped(self, value: TypeView | None) -> TypeView | None:
        """Return the type a type stands for, through aliases, root models and a union with None alone."""
        seen: set[int] = set()
        while True:
            if isinstance(value, UnionType) and len(present := _present(value)) == 1:
                value = present[0]
            elif (
                isinstance(value, GeneratedSymbolType)
                and value.symbol not in seen
                and self._symbols[value.symbol].kind in _WRAPPERS
            ):
                seen.add(value.symbol)
                value = self._roots.get(value.symbol)
            else:
                return value

    def _argument(
        self, value: TypeView | None, count: int, containers: frozenset[tuple[str | None, str]]
    ) -> TypeView | None:
        """Return the last argument of a container type, through aliases, root models and a union with None alone."""
        value = self._unwrapped(value)
        return (
            value.arguments[-1]
            if isinstance(value, GenericType) and len(value.arguments) == count and _type_name(value.base) in containers
            else None
        )

    def _leaves(self, value: TypeView | None, seen: frozenset[int]) -> Iterator[LexicalKind | None]:
        match value:
            case NoneType():
                pass
            case ConstructorType():
                yield from self._leaves(value.callable if value.base is None else value.base, seen)
            case UnionType():
                for member in value.members:
                    yield from self._leaves(member, seen)
            case LiteralType():
                yield _one_kind(
                    {
                        kind
                        for item in value.values
                        for kind in (
                            self._leaves(GeneratedSymbolType(item.symbol), seen)
                            if isinstance(item, GeneratedEnumMember)
                            else _literal_kinds(item)
                        )
                    },
                    mixed=False,
                )
            case GeneratedSymbolType():
                yield from self._symbol(self._symbols[value.symbol], seen)
            case BuiltinType() | ImportedType():
                yield _LEXICAL_KINDS.get(_type_name(value), "string")
            case None | GenericType():
                yield None
            case _:
                yield "string"

    def _symbol(self, symbol: FinalModelSymbol, seen: frozenset[int]) -> Iterator[LexicalKind | None]:
        if symbol.kind == "enum":
            yield _one_kind({kind for item in symbol.values for kind in _literal_kinds(item)}, mixed=False)
        elif symbol.kind in _WRAPPERS and symbol.id not in seen:
            yield from self._leaves(self._roots.get(symbol.id), seen | {symbol.id})
        else:
            yield None


def _present(value: UnionType) -> list[TypeView]:
    return [member for member in value.members if not isinstance(member, NoneType)]


def _type_name(value: TypeView) -> tuple[str | None, str]:
    """Return the module and name of a builtin or imported type, or no name for any other type."""
    return (
        (None, value.name)
        if isinstance(value, BuiltinType)
        else (value.import_.from_, value.import_.import_)
        if isinstance(value, ImportedType)
        else (None, "")
    )


def _literal_kinds(value: LiteralScalar | None) -> Iterator[LexicalKind | None]:
    """Yield the kind of one literal or enum member value: none for null, and None for one that is no scalar."""
    if value is None or value.kind != "none":
        yield None if value is None else _LITERAL_KINDS.get(value.kind, "string")


def _one_kind(kinds: set[LexicalKind | None], *, mixed: bool) -> LexicalKind | None:
    """Return the one kind of a leaf's kinds, taking an int beside a float as a number.

    Several other kinds are the string kind when `mixed` admits them and one of them is the string kind, whose leaf
    reads the text as it is; the members of one enum or literal admit none. A leaf of null alone has no kind.
    """
    if kinds == _INTEGER_NUMBER:
        return "number"
    if len(kinds) == 1:
        return kinds.pop()
    return "string" if mixed and "string" in kinds and None not in kinds else None
