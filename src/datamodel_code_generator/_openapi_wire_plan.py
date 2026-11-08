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
    media_range,
    normalize_media_type,
    normalized,
)
from datamodel_code_generator._runtime.model_codecs.parameters import (
    ParameterLocation,
    ParameterPlan,
    ParameterShapeError,
    ValueShape,
    builtin_content,
    header_text,
    parameter_key,
)
from datamodel_code_generator._target_contract import (
    API_KEY_LOCATIONS,
    BindingReason,
    EncodingFacts,
    GeneratedSymbolType,
    GeneratedTypeContractBatch,
    KindAt,
    LiteralScalar,
    OpenAPIVersions,
    OperationContract,
    OperationId,
    SourceLocation,
    TextShape,
    TypeUseId,
    WireDeclaration,
    api_key_place,
    children_of,
    encoding_headers,
    fact,
    is_required,
    label,
    members_by_consumer,
    operation_schemes,
    parameter_location,
    parameter_place,
    property_members,
    request_media,
    scheme_catalogues,
    security_requirements,
    symbols_by_id,
    type_uses,
    uses_by_id,
)
from datamodel_code_generator._target_types import (
    kept_values,
    lexical_kind,
    member_kind,
    takes_null,
    untyped,
    unwrapped,
)
from datamodel_code_generator.enums import OpenAPIVersion
from datamodel_code_generator.python_literal import stable_repr

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence

    from datamodel_code_generator._openapi_features import ClientFeature
    from datamodel_code_generator._runtime.model_codecs.media import MediaKind
    from datamodel_code_generator._target_contract import (
        FieldSlot,
        FieldUseBinding,
        FinalModelSymbol,
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

_DEFAULT_STYLES: Final = {"path": "simple", "query": "form", "header": "simple", "cookie": "form"}
NO_SCALAR_KIND: Final = "A parameter value needs one unambiguous scalar kind"
STYLED: Final = ("style", "explode", "allowReserved")
_BINARY_TEXT: Final = (
    "carries a binary string as text; declare it with format: byte for base64 text, optionally with "
    "--type-mappings string+byte=binary to keep bytes, or send it as a whole binary body or a file part"
)


@dataclass(frozen=True, slots=True)
class CodecDiagnostic:
    """Report one generation-time wire rule that requires a source fix."""

    code: CodecReason
    source: SourceLocation
    message: str
    operation: OperationId | None = None
    uses: tuple[TypeUseId, ...] = ()
    feature: ClientFeature | None = None


@dataclass(frozen=True, slots=True)
class WirePlan:
    """Keep the parameter, header and form plans of the planned operations, and the bound lexical kinds."""

    parameters: tuple[tuple[OperationId, tuple[ParameterPlan, ...]], ...]
    diagnostics: tuple[CodecDiagnostic, ...]
    kinds: LexicalKinds
    versions: OpenAPIVersions
    headers: tuple[tuple[TypeUseId, ParameterPlan], ...] = ()
    forms: tuple[tuple[TypeUseId, tuple[FieldPlan, ...], FieldPlan | None, tuple[ParameterPlan, ...]], ...] = ()
    styles: tuple[tuple[TypeUseId, tuple[ParameterPlan, ...]], ...] = ()


class _PlanError(Exception):
    """Refuse a parameter, header or form member no builtin encoding writes, as `MC_PARAMETER_ENCODING`."""

    def __init__(self, source: SourceLocation, message: str, feature: ClientFeature | None = None) -> None:
        super().__init__(message)
        self.diagnostic = CodecDiagnostic("MC_PARAMETER_ENCODING", source, message, feature=feature)


class _WirePlanner:
    def __init__(self, batch: GeneratedTypeContractBatch, *, writes: bool = False) -> None:
        self.batch = batch
        self.writes = writes
        self.kinds = LexicalKinds(batch)
        self.diagnostics: list[CodecDiagnostic] = []
        self.versions = batch.versions
        self.uses = uses_by_id(batch)


def declared_media(name: str) -> tuple[str, str | None]:
    """Return a content key's normalized media type and why no header carries it, or None when one does.

    A key that is no media type is kept as it is written. A media type whose text is not ASCII header text, such as one
    with a non-ASCII parameter, labels no body: its Content-Type would fail every call.
    """
    if (media_type := normalized(name)) is None:
        return name, "is not a media type"
    return media_type, None if header_text(media_type) else "is not ASCII header text, which a Content-Type needs"


def body_kind(media_type: str, use: TypeUseBinding | None) -> MediaKind:
    """Return how a body of a normalized media type is carried, as its models decide.

    A JSON or text body the models type as raw bytes is carried as the raw body, and a binary body written as a binary
    string they type as text, such as an enum of strings, as text.
    """
    kind = media_kind(media_type)
    if use is None:
        return kind
    if use.octets:
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


def plan_wire(  # noqa: PLR0913
    batch: GeneratedTypeContractBatch,
    uses: Sequence[TypeUseId] | None = None,
    *,
    operations: Collection[OperationId] | None = None,
    forms: Mapping[TypeUseId, tuple[WireDeclaration, ...]] | None = None,
    styles: Mapping[TypeUseId, tuple[WireDeclaration, ...]] | None = None,
    writes: bool = False,
) -> WirePlan:
    """Build the parameter plans of the requested operations and the plans of their requested encoding headers.

    The uses of URL-encoded bodies named in `forms` get their member plans, and each member their encoding names the
    plan of a query parameter; the form-data uses named in `styles` get the query parameter plan of each member their
    encodings give a style. A requested use that carries a raw binary string as text is refused, since the model
    backends write bytes as different text. An operation parameter that is an item or an array of items is planned
    only when the operations' parameters are written (`writes`), since text read back cannot tell the two apart.
    """
    forms = forms or {}
    styles = styles or {}
    planner = _WirePlanner(batch, writes=writes)
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
    planner.diagnostics.extend(diagnostic for operation in planned for diagnostic in _unnamed(planner, operation))
    return WirePlan(
        parameters,
        tuple(planner.diagnostics),
        planner.kinds,
        planner.versions,
        headers,
        planned_forms,
        planned_styles,
    )


def _binary_text(
    batch: GeneratedTypeContractBatch, operations: Sequence[OperationContract], requested: frozenset[TypeUseId] | None
) -> Iterator[CodecDiagnostic]:
    """Yield a diagnostic for each requested use of the operations that carries a raw binary string as text."""
    labels = {operation.id: label(operation) for operation in operations}
    yield from (
        CodecDiagnostic(
            "MC_CODEC_UNSUPPORTED",
            source,
            f"{_carrier(use.id, labels[owner])} {_BINARY_TEXT}",
            owner,
            (use.id,),
            "binary_in_text_media",
        )
        for use in batch.type_uses
        if (source := use.binary) is not None and use.id.projection == "value"
        if requested is None or use.id in requested
        if isinstance(owner := use.id.owner, OperationId) and owner in labels
    )


def _unnamed(planner: _WirePlanner, operation: OperationContract) -> Iterator[CodecDiagnostic]:
    """Yield a diagnostic for each encoding of an operation's form request body that names no property of its schema.

    The Media Type Object requires every encoding key to name one, a property the request leaves out included.
    """
    for media in request_media(operation):
        media_type = declared_media(media.name or "")[0]
        if media_kind(media_type) not in {"form", "multipart"}:
            continue
        use = next((planner.uses[item] for item in media.schemas if item in planner.uses), None)
        names = (
            set() if use is None else {member.wire_name for member in use.members if member.member_kind == "property"}
        )
        yield from (
            CodecDiagnostic(
                "MC_PARAMETER_ENCODING",
                encoding.use_site,
                f"The {encoding.name} encoding of the {media_type} media of {label(operation)} names no property of "
                "its schema",
                operation.id,
            )
            for encoding in children_of(media, "encoding")
            if (encoding.name or "") not in names
        )


def _carrier(use: TypeUseId, label: str) -> str:
    """Name the parameter, body, response, header or part header a use's value is, in its operation."""
    where = "request body" if use.role.startswith("request") else f"response {use.status}"
    match use.role:
        case "parameter":
            return f"The {use.location} parameter {stable_repr(use.name)} of {label}"
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
        styled = {
            declaration.name or "": declaration
            for declaration in member_encodings(use, encodings)
            if _styled(declaration)
        }
        fields = tuple(_text(member) for member in form_members(planner.kinds, bound) if member.name not in styled)
        additional = _additional(planner, bound)
        members = {name: schema for name, schema, _ in property_members(use.members)}
        encoded = tuple(_encoding(bound, members, declaration) for declaration in styled.values())
        encoded = _distinct(planner, bound, [field.name for field in fields], encoded)
    except _PlanError as error:
        _refused(planner, error, use.id.owner, (use.id,))
        return None
    return use.id, fields, additional, encoded


def _styles(
    planner: _WirePlanner, use: TypeUseBinding, encodings: tuple[WireDeclaration, ...]
) -> tuple[TypeUseId, tuple[ParameterPlan, ...]] | None:
    """Return the query parameter plans of the form-data members whose encodings name a style or its options.

    The other members that count against their names are those the form's model writes, as for a URL-encoded form: a
    property the direction leaves out, such as a readOnly one of a request, writes none.
    """
    try:
        bound = _schema_use(planner, (use.id,), use.id.use_site)
        members = {name: schema for name, schema, _ in property_members(use.members)}
        encoded = tuple(_encoding(bound, members, declaration) for declaration in member_encodings(use, encodings))
        styled = {plan.name for plan in encoded}
        names = [member.name for member in form_members(planner.kinds, bound) if member.name not in styled]
        encoded = _distinct(planner, bound, names, encoded)
    except _PlanError as error:
        _refused(planner, error, use.id.owner, (use.id,))
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
    shaped = {
        shape.name: shape.kind.source
        for shape in (() if encoding is None else encoding.form.members)
        if shape.kind is not None
    }
    for name, schema, member in property_members(use.members):
        if (facts := member.model_facts) is not None:
            yield FormMember(name, facts.type, *kinds.member(facts.type), shaped.get(name, schema), member)


def form_extras(kinds: LexicalKinds, use: TypeUseBinding) -> FormMember | None:
    """Return how the other properties of a form are written, by the type of their values.

    The type is the one the form's model keeps them as, in a field of its own or one it inherits. A model without
    such a field takes the type its use binds for their schema (`EncodingFacts.extras`). A form whose model keeps
    none as a type, and that allows no other properties, has no schema for them, or binds no type for it, has no such
    member: its other properties are text as they come.
    """
    extras = None if (encoding := use.encoding) is None else encoding.extras
    if (kept := kinds.kept(use)) is not None:
        value, declared = kept
        return FormMember("", value, *kinds.member(value), declared if extras is None else extras[0])
    if extras is None:
        return None
    source, value = extras
    return FormMember("", value, *kinds.member(value), source)


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
        raise _PlanError(source, message)
    return typed or (None if shape.additional is None else FieldPlan(""))


def _text(member: FormMember) -> FieldPlan:
    """Return the text plan of a URL-encoded member, refusing one whose type has no one kind."""
    if member.kind is None:
        raise _PlanError(member.source, NO_SCALAR_KIND, "structured_parameter_values")
    return FieldPlan(member.name, member.kind, repeated=member.repeated)


def member_encodings(use: TypeUseBinding | None, encodings: Iterable[WireDeclaration]) -> Iterator[WireDeclaration]:
    """Yield the encodings of a form that name a member its model has in the form's direction.

    The encoding of a property the direction leaves out, such as a readOnly one of a request, applies to no member,
    and `plan_wire` refuses one that names no property of the form's schema.
    """
    names = (
        set()
        if use is None
        else {
            name
            for name, _, member in property_members(use.members)
            if member.exclusion is None and member.model_facts is not None
        }
    )
    return (encoding for encoding in encodings if (encoding.name or "") in names)


def media_encodings(use: TypeUseBinding | None, media: WireDeclaration) -> Iterator[WireDeclaration]:
    """Yield the encodings a form media declares that name a member its model has in the form's direction.

    They are the media's encodings that apply to a member (``member_encodings``).
    """
    return member_encodings(use, children_of(media, "encoding"))


def part_json(
    kinds: LexicalKinds, value: TypeView, encoding: WireDeclaration | None, versions: OpenAPIVersions
) -> bool:
    """Return whether a form-data member holding no files, of a type, is JSON by that type rather than text of its kind.

    A type without one kind is JSON, and so is one that takes any value unless the member's encoding gives it a style
    the document's version applies, which writes it as text, or a media type, which writes it in that media type.
    """
    kind, leaf, _ = kinds.member(value)
    if kind is None:
        return True
    given = encoding is not None and (form_styled(encoding, versions, multipart=True) or bool(content_types(encoding)))
    return kinds.untyped(leaf) and not given


def _refused(planner: _WirePlanner, error: _PlanError, owner: object, uses: tuple[TypeUseId, ...]) -> None:
    operation = owner if isinstance(owner, OperationId) else None
    planner.diagnostics.append(replace(error.diagnostic, operation=operation, uses=uses))


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
        raise _PlanError(location, "Expanded form member names collide")
    absorbing = [plan.name for plan in encoded if plan.additional is not None and _spread(plan)]
    if absorbing and (
        len(absorbing) > 1
        or any(plan.style == "deepObject" for plan in encoded)
        or _encoded(form).declared
        or planner.kinds.configured(form)
    ):
        message = (
            f"The exploded form member {stable_repr(absorbing[0])} allows other properties and takes every name no "
            "other member writes, so the form cannot have another such member, a deepObject member, or other "
            "properties of its own"
        )
        raise _PlanError(location, message)
    plans = list(encoded)
    return tuple(_reserving(plan, plans) if plan.name in absorbing else plan for plan in plans)


def _styled(encoding: WireDeclaration) -> bool:
    """Return whether an encoding gives its member a query parameter's style or content."""
    return any(fact(encoding, key) is not None for key in (*STYLED, "contentType"))


def content_types(encoding: WireDeclaration) -> tuple[str, ...] | None:
    """Return the media types an encoding's contentType lists, none without one, or None when one is no media type."""
    if (declared := fact(encoding, "contentType")) is None:
        return ()
    try:
        return tuple(normalize_media_type(item.strip()) for item in str(declared).split(","))
    except ValueError:
        return None


def single_media(encoding: WireDeclaration) -> str | None:
    """Return the one contentType of an encoding, or None when it names none, several media types or a range."""
    if (found := content_types(encoding)) is None or len(found) != 1 or media_range(found[0]):
        return None
    return found[0]


def _encoding(form: TypeUseBinding, members: Mapping[str, SourceLocation], encoding: WireDeclaration) -> ParameterPlan:
    """Return the query parameter plan of a form member: its style, or its content when only that is named.

    A style, explode, or allowReserved takes precedence over contentType, as the Encoding Object prescribes.
    """
    name, source = encoding.name or "", encoding.use_site
    member = members[name]
    style, explode, reserved, content = (
        fact(encoding, key) for key in ("style", "explode", "allowReserved", "contentType")
    )
    shape: ValueShape | None = None
    try:
        if style is None and explode is None and reserved is None:
            media = normalize_media_type(str(content))
            if not builtin_content("query", media):
                raise _PlanError(source, "The member content has no builtin encoding")
            return ParameterPlan(location="query", name=name, content_media_type=media)
        shape, kind, fields, additional = _shape(_member_shape(form, name, member))
        chosen, exploded = _style_of(encoding, "query")
        return ParameterPlan(
            location="query",
            name=name,
            style=chosen,
            explode=exploded,
            allow_reserved=reserved is True,
            shape=shape,
            kind=kind,
            fields=fields,
            additional=additional,
            reserved_names=tuple(sorted(other for other in members if other != name)),
        )
    except ValueError as error:
        raise _PlanError(source, str(error), _style_feature(style, shape)) from None


def parameter_plans(wire: WirePlan) -> dict[OperationId, dict[tuple[ParameterLocation, str], ParameterPlan]]:
    """Index each operation's parameter plans by location and name."""
    return {operation: {(plan.location, plan.name): plan for plan in planned} for operation, planned in wire.parameters}


def form_styled(encoding: WireDeclaration, versions: OpenAPIVersions, *, multipart: bool) -> bool:
    """Return whether an encoding gives its form member a query parameter's style, explode, or allowReserved.

    OpenAPI 3.0 applies them to application/x-www-form-urlencoded alone and ignores them for multipart/form-data,
    whose member is then written as its contentType says; 3.1 and later apply them to both, and write the parts they
    give without percent-encoding. The version is that of the document that declares the encoding.
    """
    if multipart and versions.declared(encoding) == OpenAPIVersion.V30:
        return False
    return any(fact(encoding, key) is not None for key in STYLED)


def _allow_reserved(version: OpenAPIVersion, location: ParameterLocation, declaration: WireDeclaration) -> bool:
    """Return the effective allowReserved flag for the parameter's OpenAPI version and location."""
    return fact(declaration, "allowReserved") is True and not (
        location == "path" and version in {OpenAPIVersion.V30, OpenAPIVersion.V31}
    )


def _planned(
    planner: _WirePlanner,
    operation: OperationId,
    declaration: WireDeclaration,
    names: list[tuple[object, str]],
    *,
    written: bool = False,
) -> ParameterPlan | None:
    try:
        return _parameter(planner, declaration, names, written=written)
    except _PlanError as error:
        _refused(planner, error, operation, type_uses(declaration))
    return None


def _headers(operation: OperationContract, requested: frozenset[TypeUseId] | None) -> Iterator[WireDeclaration]:
    """Yield the headers of an operation's responses, then the requested ones its request body's encodings declare."""
    for response in operation.responses:
        yield from children_of(response, "header")
    yield from (
        header
        for header in encoding_headers(operation)
        if requested is None or not requested.isdisjoint(type_uses(header))
    )


def _header(
    planner: _WirePlanner, operation: OperationId, declaration: WireDeclaration
) -> tuple[tuple[TypeUseId, ParameterPlan], ...]:
    if not (uses := type_uses(declaration)):
        return ()
    facts = (*declaration.facts, ("in", LiteralScalar("str", "header")), ("style", LiteralScalar("str", "simple")))
    plan = _planned(planner, operation, replace(declaration, facts=facts), [])
    return () if plan is None else ((uses[0], plan),)


def auth_names(batch: GeneratedTypeContractBatch, operation: OperationContract) -> list[tuple[object, str]]:
    """Return the locations and names of the apiKey credentials the operation's security requirements send."""
    schemes = operation_schemes(scheme_catalogues(batch), operation)
    return list(
        dict.fromkeys(
            place
            for requirement in security_requirements(operation) or ()
            for name, _ in requirement
            if (scheme := schemes.get(name)) is not None and (place := api_key_place(scheme)) is not None
        )
    )


def _spread(plan: ParameterPlan) -> bool:
    return plan.shape == "object" and plan.explode and plan.style in {"form", "cookie"}


def _claimed(plans: Sequence[ParameterPlan], location: str) -> list[str]:
    return [
        parameter_key(location, name)[1]
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
    names = [*(parameter_place(item) for item in declarations), *auth]
    plans = [
        plan
        for declaration in declarations
        if (plan := _planned(planner, operation.id, declaration, names, written=planner.writes)) is not None
    ]
    planner.diagnostics.extend(parameter_collisions(operation, plans, auth))
    return tuple(_reserving(plan, plans) for plan in plans)


def parameter_collisions(
    operation: OperationContract, plans: Sequence[ParameterPlan], auth: Sequence[tuple[object, str]]
) -> tuple[CodecDiagnostic, ...]:
    """Report each location whose expanded parameter names or the `auth` apiKey names collide."""
    found: list[CodecDiagnostic] = []
    for location in API_KEY_LOCATIONS.values():
        claimed = [
            *_claimed(plans, location),
            *{parameter_key(location, name)[1] for kind, name in auth if kind == location},
        ]
        absorbing = [
            plan for plan in plans if plan.location == location and plan.additional is not None and _spread(plan)
        ]
        if (
            len(set(claimed)) != len(claimed)
            or len(absorbing) > 1
            or (absorbing and any(plan.location == location and plan.style == "deepObject" for plan in plans))
        ):
            source = next(item.use_site for item in operation.parameters if parameter_location(item) == location)
            found.append(
                CodecDiagnostic(
                    "MC_PARAMETER_ENCODING", source, f"Expanded {location} parameter names collide", operation.id
                )
            )
    return tuple(found)


def _style_of(declaration: WireDeclaration, location: ParameterLocation) -> tuple[str, bool]:
    """Return the style a parameter or a form member's encoding writes in a location, and whether it explodes.

    The style defaults by the location, and a style that is not given explodes by default where it is form or cookie.
    """
    style = str(fact(declaration, "style") or _DEFAULT_STYLES[location])
    explode = fact(declaration, "explode")
    return style, explode if isinstance(explode, bool) else style in {"form", "cookie"}


def _parameter(
    planner: _WirePlanner, declaration: WireDeclaration, names: list[tuple[object, str]], *, written: bool
) -> ParameterPlan:
    source = declaration.use_site
    location, name = parameter_place(declaration)
    required = is_required(declaration)
    _finite(planner, declaration)
    if location == "querystring" or declaration.children:
        return _content_parameter(planner, declaration, location, name, required=required)
    value = _encoded(_schema_use(planner, declaration.schemas, source)).value
    shape, kind, fields, additional = _shape(value, single=written)
    style, explode = _style_of(declaration, location)
    version = planner.versions.declared(declaration)
    if style == "cookie" and version != OpenAPIVersion.V32:
        raise _PlanError(source, "Cookie style requires OpenAPI 3.2", "cookie_style")
    try:
        return ParameterPlan(
            location=location,
            name=name,
            style=style,
            explode=explode,
            required=required,
            allow_reserved=_allow_reserved(version, location, declaration),
            shape=shape,
            kind=kind,
            fields=fields,
            additional=additional,
            reserved_names=tuple(sorted(other for kind_, other in names if kind_ == location and other != name)),
            single=value.single,
        )
    except ParameterShapeError as error:
        raise _PlanError(source, str(error), _style_feature(style, shape, single=value.single)) from None
    except ValueError as error:
        raise _PlanError(source, str(error)) from None


def _style_feature(style: object, shape: ValueShape | None, *, single: bool = False) -> ClientFeature | None:
    """Return the feature a refusal of a style for the shape of its values stems from, or None.

    A value that is an item or an array of them uses structured values, and deepObject on a value that is no object
    uses that style.
    """
    if single:
        return "structured_parameter_values"
    return "deep_object_style" if style == "deepObject" and shape not in {None, "object"} else None


def _content_parameter(
    planner: _WirePlanner, declaration: WireDeclaration, location: ParameterLocation, name: str, *, required: bool
) -> ParameterPlan:
    source = declaration.use_site
    media = normalize_media_type(declaration.children[0].name or "")
    if not builtin_content(location, media):
        raise _PlanError(source, "The parameter content has no builtin encoding")
    fields: tuple[FieldPlan, ...] = ()
    additional: FieldPlan | None = None
    content = declaration.children[0]
    kind = media_kind(media)
    if kind == "form":
        _, _, fields, additional = _shape(_encoded(_schema_use(planner, content.schemas, source)).form)
    elif kind == "text" and content.schemas and not planner.kinds.text(_schema_use(planner, content.schemas, source)):
        raise _PlanError(source, "Text parameter content requires a string schema")
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
        raise _PlanError(source, str(error)) from None


def _finite(planner: _WirePlanner, declaration: WireDeclaration) -> None:
    """Refuse a parameter whose schema declares a non-finite default, which no JSON number or parameter text writes."""
    for use in (planner.uses[item] for item in type_uses(declaration) if item in planner.uses):
        if isinstance(value := getattr(use.default, "value", None), float) and not isfinite(value):
            location = _located(use)
            raise _PlanError(location.child("default"), "A schema value must be a finite JSON number")


def _schema_use(planner: _WirePlanner, uses: tuple[TypeUseId, ...], source: SourceLocation) -> TypeUseBinding:
    """Return the first of a declaration's type uses that has a schema."""
    if (use := next((item for item in planner.batch.type_uses if item.id in uses and item.schema), None)) is None:
        raise _PlanError(source, "A style-based parameter requires a schema")
    return use


def _located(use: TypeUseBinding) -> SourceLocation:
    return use.schema or use.id.use_site


def _encoded(use: TypeUseBinding) -> EncodingFacts:
    """Return how a use's value is written as text, as the binder read it from the type and members the use binds."""
    return cast("EncodingFacts", use.encoding)


def _member_shape(form: TypeUseBinding, name: str, member: SourceLocation) -> TextShape:
    """Return how one member of a form is written as a parameter value; one its model types no field for has no kind."""
    found = dict(_encoded(form).members).get(name)
    return TextShape("scalar", KindAt(member, None)) if found is None else found


def _kind(site: KindAt) -> LexicalKind:
    """Return the kind of a text leaf by the final type bound for it.

    A leaf bound to no type, to a model or a container, or to values of several kinds no text reaches, is refused.
    """
    if (kind := site.kind) is None:
        raise _PlanError(site.source, NO_SCALAR_KIND, "structured_parameter_values")
    return kind


def _shape(
    shape: TextShape, *, single: bool = False
) -> tuple[ValueShape, LexicalKind, tuple[FieldPlan, ...], FieldPlan | None]:
    """Return a value's shape, its kind, and its members' plans.

    The members come first, in their order, then why the value has no builtin encoding, then any other property. An
    array that takes an item alone has no one scalar kind where `single` does not let it be written.
    """
    fields = tuple(_field(member) for member in shape.members)
    if (problem := shape.problem) is not None:
        source, message = problem
        raise _PlanError(source, message)
    if shape.single and not single:
        raise _PlanError(cast("KindAt", shape.kind).source, NO_SCALAR_KIND, "structured_parameter_values")
    if shape.shape == "object":
        return "object", "string", fields, None if shape.additional is None else _field(shape.additional)
    return shape.shape, _kind(cast("KindAt", shape.kind)), (), None


def _field(member: MemberShape) -> FieldPlan:
    """Return a member's plan: any string without a kind of its own, a repeated array member in its items' kind."""
    return FieldPlan(member.name, "string" if member.kind is None else _kind(member.kind), repeated=member.repeated)


class LexicalKinds:
    """Read the lexical kind of a form member (`_target_types.member_kind`) and the other properties its model keeps."""

    def __init__(self, batch: GeneratedTypeContractBatch) -> None:
        """Keep the batch whose symbols and root values the first lookups index."""
        self._batch = batch

    @cached_property
    def _symbols(self) -> dict[SymbolId, FinalModelSymbol]:
        return symbols_by_id(self._batch)

    @cached_property
    def _members(self) -> dict[SymbolId, list[FieldUseBinding]]:
        return members_by_consumer(self._batch)

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
                for member, value in kept_values(use.members, self._symbols, self._members)
                if (schema := member.schema) is not None
            ),
            None,
        )

    def configured(self, use: TypeUseBinding) -> bool:
        """Return whether the model a use's type stands for keeps its other properties by the configuration it takes.

        The type stands for the model through aliases, root models and a union with None alone, and the model takes
        its own setting over the one it inherits.
        """
        return isinstance(
            value := unwrapped(use.type, self._symbols, self._members), GeneratedSymbolType
        ) and keeps_extras(self._symbols, value.symbol)

    def member(self, value: TypeView) -> tuple[LexicalKind | None, TypeView, bool]:
        """Return how a form member of a type is written (``_target_types.member_kind``)."""
        return member_kind(value, self._symbols, self._members)

    def text(self, use: TypeUseBinding) -> bool:
        """Return whether a use's type takes strings read as they come and rejects null, as a text content's must."""
        return lexical_kind(use.type, self._symbols, self._members) == "string" and not takes_null(
            use.type, self._symbols, self._members
        )

    def untyped(self, value: TypeView) -> bool:
        """Return whether a type takes any value, as the type of an untyped schema does (``_target_types.untyped``).

        A form-data part carries such a value as JSON, as it does one without one kind, while its kind is a string's.
        """
        return untyped(value, self._symbols, self._members)
